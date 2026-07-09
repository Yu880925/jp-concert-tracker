"""
monitor.py - 使用 Nitter / X API / Google 監控推文

安裝：
    pip install requests openai

使用：
    python monitor.py                        # 用 Nitter 執行一次（預設）
    python monitor.py --prefer google        # 用 Google Search 執行
    python monitor.py --prefer bearer        # 用 X API Bearer Token 執行
    python monitor.py --daemon               # 背景每 2 小時掃描
    python monitor.py --artist YOASOBI       # 只掃描單一歌手
"""

import os
import re
import json
import time
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests

# ★ 載入 .env 檔案裡的環境變數（一定要在讀取任何 os.getenv 之前執行）
try:
    from dotenv import load_dotenv
    load_dotenv()  # 預設會找執行目錄下的 .env，也可以指定路徑: load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    print("⚠️  未安裝 python-dotenv，.env 檔案不會被讀取。請執行: pip install python-dotenv")

# ──────────────────────────────────────────────────────────────────

# ─── API Credentials（全部從環境變數讀取，勿寫死在程式碼）────────
X_CONSUMER_KEY    = os.getenv("X_CONSUMER_KEY", "")
X_CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET", "")
X_BEARER_TOKEN    = os.getenv("X_BEARER_TOKEN", "")
X_CLIENT_SECRET_1 = os.getenv("X_CLIENT_SECRET_1", "")
X_CLIENT_SECRET_2 = os.getenv("X_CLIENT_SECRET_2", "")

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_KEY   = os.getenv("GROQ_API_KEY", "")

# ─── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ─── 台灣關鍵字 ───────────────────────────────────────────────
TW_KEYWORDS = [
    "台灣", "台北", "台中", "高雄", "taiwan", "taipei", "taichung",
    "kaohsiung", "asia tour", "world tour", "アジアツアー",
    "海外公演", "海外ライブ", "アジア", "台湾",
]
NEG_KEYWORDS = [
    "観光", "旅行", "食べ", "グルメ", "sightseeing",
    "vacation", "holiday", "travelling",
]

# ─── AI Prompt ────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一個日本演唱會資訊分析助手。
使用者會給你一段 X (Twitter) 貼文。
請判斷這是否在宣布「台灣演唱會/表演」。
只回傳 JSON，不要有其他說明文字。

重要規則：
1. 以現在的時間為基準，年份判斷請以貼文發布時間為準，不要預設為過去年份
2. 只回傳「台灣/台北（Taipei）」場次的日期，嚴格忽略首爾（ソウル/Seoul）、香港、新加坡、東京等其他城市的場次
3. 例如貼文寫「6/6台北、6/20ソウル」，只回傳 6/6，不回傳 6/20
4. 如果貼文是巡演日程表，每個日期後面都有城市名，只取台灣/台北對應的日期
   例如：「3/11 愛知 Zepp Nagoya / 3/13 大阪 / 5/23 台湾・台北」→ 只回傳 5/23
5. 如果台灣演唱會有多個日期（例如 6/6 和 6/7 都是台北場），請用 dates 陣列回傳所有台灣場次日期
6. date 格式 YYYY-MM-DD，只要貼文中有明確的月份和日期（例如 8/22、8月22日），就必須填寫日期
7. 年份判斷：貼文中沒寫年份時，以貼文發布年份為基準，若該月日已過則推算為明年
8. 完全沒有月日資訊才填 null，不要因為沒有年份就放棄填日期
9. 日期必須是完整的 YYYY-MM-DD 格式，不可以填 YYYY-MM-xx 或類似不完整的格式，不確定日期就填 null
9. 忽略「申込期間」「受付期間」「先行期間」「締切」等報名/截止日期，只填演出日期

確認是台灣演唱會（單日）：
{"is_concert": true, "confidence": 0.95, "date": "2026-06-19", "dates": ["2026-06-19"], "venue": "台北小巨蛋", "event_name": "TOUR名稱", "ticket_url": null, "notes": "說明"}

確認是台灣演唱會（多日）：
{"is_concert": true, "confidence": 0.95, "date": "2026-06-19", "dates": ["2026-06-19", "2026-06-20"], "venue": "台北小巨蛋", "event_name": "TOUR名稱", "ticket_url": null, "notes": "兩天演出"}

只是旅遊或非演唱會：
{"is_concert": false, "confidence": 0.9}

模糊/可能是演出：
{"is_concert": "maybe", "confidence": 0.5, "notes": "說明原因"}

venue 不清楚填 null。
ticket_url：如果貼文中有提到任何連結（https://...），請直接填入原始連結，否則填 null。

範例（含連結）：
{"is_concert": true, "confidence": 0.95, "date": "2026-08-22", "dates": ["2026-08-22", "2026-08-23"], "venue": "Taipei Arena", "event_name": "Grateful Yesterdays Tour 2026", "ticket_url": "https://t.co/xxxxx", "notes": "台北場"}"""


def fetch_tweets_bearer(twitter_handle: str, limit: int = 20) -> list[dict]:
    """使用 Bearer Token 呼叫 X API v2 抓取推文。"""
    headers = {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "User-Agent": "JPConcertTracker/1.0"
    }

    # Step 1: 取得 user_id
    try:
        r = requests.get(
            f"https://api.twitter.com/2/users/by/username/{twitter_handle}",
            headers=headers, timeout=10
        )
        if r.status_code == 401:
            log.error("❌ Bearer Token 無效或已過期，請重新產生")
            return []
        if r.status_code == 429:
            log.warning("⏳ X API Rate limit 觸發，稍後再試")
            return []
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            log.warning(f"找不到 @{twitter_handle}: {data['errors']}")
            return []
        user_id = data["data"]["id"]
    except requests.exceptions.RequestException as e:
        log.warning(f"取得 user_id 失敗 @{twitter_handle}: {e}")
        return []

    # Step 2: 抓取推文
    try:
        r = requests.get(
            f"https://api.twitter.com/2/users/{user_id}/tweets",
            headers=headers,
            params={
                "max_results":    min(limit, 100),
                "tweet.fields":   "created_at,text,entities",
                "expansions":     "attachments.media_keys",
                "media.fields":   "url",
                "exclude":        "retweets",
            },
            timeout=10
        )
        if r.status_code == 429:
            log.warning("⏳ X API Rate limit，跳過本次")
            return []
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as e:
        log.warning(f"抓取推文失敗 @{twitter_handle}: {e}")
        return []

    tweets = []
    for t in data.get("data", []):
        tweets.append({
            "id":         t["id"],
            "text":       t["text"],
            "url":        f"https://twitter.com/{twitter_handle}/status/{t['id']}",
            "created_at": t.get("created_at", ""),
            "has_media":  bool(t.get("attachments")),
        })

    log.info(f"[Bearer] @{twitter_handle}: 取得 {len(tweets)} 則")
    return tweets


NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.poast.org",
    "https://nitter.privacyredirect.com",
    "https://lightbrd.com",
    "https://nitter.space",
    "https://nitter.net",
]

def _fetch_fxtwitter_user_tweets(twitter_handle: str, limit: int) -> list[dict]:
    """
    用 Nitter RSS 抓取推文 ID，再用 fxtwitter API 取得完整內容。
    """
    import re as _re2

    tweet_ids = []

    # 嘗試各個 Nitter 實例取得 RSS，失敗時重試最多 3 輪
    MAX_ROUNDS = 3
    RETRY_WAIT = [5, 15, 30]  # 每輪失敗後等待秒數

    for round_i in range(MAX_ROUNDS):
        for instance in NITTER_INSTANCES:
            try:
                r = requests.get(
                    f"{instance}/{twitter_handle}/rss",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    timeout=15,
                )
                if r.status_code != 200:
                    continue

                # 從 RSS XML 抓推文 ID
                ids = _re2.findall(r"/status/(\d+)", r.text)
                seen = set()
                for tid in ids:
                    if tid not in seen and len(tweet_ids) < limit:
                        seen.add(tid)
                        tweet_ids.append(tid)

                if tweet_ids:
                    log.info(f"[nitter] @{twitter_handle}: 從 {instance} 找到 {len(tweet_ids)} 則推文 ID")
                    break

            except Exception as e:
                log.warning(f"[nitter] {instance} 失敗: {e}")
                continue

        if tweet_ids:
            break

        # 這輪所有實例都失敗，等一下再試
        if round_i < MAX_ROUNDS - 1:
            wait = RETRY_WAIT[round_i]
            log.warning(f"[nitter] 所有實例失敗，{wait} 秒後重試（第 {round_i+1}/{MAX_ROUNDS} 輪）@{twitter_handle}")
            time.sleep(wait)

    if not tweet_ids:
        log.warning(f"[nitter] 所有實例都失敗（已重試 {MAX_ROUNDS} 輪）@{twitter_handle}")
        return []

    # 用 fxtwitter API 取得每則推文內容
    tweets = []
    for tid in tweet_ids[:limit]:
        try:
            r = requests.get(
                f"https://api.fxtwitter.com/{twitter_handle}/status/{tid}",
                headers={"User-Agent": "JPConcertTracker/1.0"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            d = r.json()
            tw = d.get("tweet", {})
            if not tw:
                continue
            tweets.append({
                "id":         tid,
                "text":       tw.get("text", ""),
                "url":        tw.get("url", f"https://x.com/{twitter_handle}/status/{tid}"),
                "created_at": tw.get("created_at", ""),
                "has_media":  bool(tw.get("media", {}).get("photos") or tw.get("media", {}).get("videos")),
            })
            time.sleep(0.3)
        except Exception as e:
            log.warning(f"[nitter] 取得推文失敗 {tid}: {e}")
            continue

    log.info(f"[nitter] @{twitter_handle}: 取得 {len(tweets)} 則")
    return tweets


def fetch_tweets_nitter(twitter_handle: str, limit: int = 20) -> list[dict]:
    """透過 Nitter RSS 取得推文 ID，再用 fxtwitter API 取得完整內容。"""
    return _fetch_fxtwitter_user_tweets(twitter_handle, limit)


# ════════════════════════════════════════════════════════════════
# 方案 C：Google Search（不需要任何 API，最穩定）
# ════════════════════════════════════════════════════════════════

def fetch_google_search(artist_name: str, artist_en: str = "") -> list[dict]:
    import re as _re  # 補上這一行
    """用 Google 搜尋抓取演唱會相關資訊。"""
    import urllib.parse, html, datetime as _dt2

    _yr = _dt2.date.today().year
    queries = [
        f"{artist_name} 台灣演唱會 {_yr}",
        f"{artist_name} 台灣演唱會 {_yr+1}",
        f"{artist_en or artist_name} Taiwan concert {_yr}",
        f"{artist_name} taipei live {_yr+1}",
    ]

    # 歌手名稱的比對用小寫版本
    _name_lower = artist_name.lower()
    _en_lower   = (artist_en or "").lower()

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }

    for query in queries:
        try:
            url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num=5&hl=zh-TW"
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                continue

            titles   = _re.findall(r"<h3[^>]*>([^<]+)</h3>", r.text)
            snippets = _re.findall(r'<div[^>]*class="[^"]*VwiC3b[^"]*"[^>]*>(.*?)</div>', r.text, _re.DOTALL)

            for i, title in enumerate(titles[:5]):
                title   = html.unescape(_re.sub(r"<[^>]+>", "", title)).strip()
                snippet = html.unescape(_re.sub(r"<[^>]+>", "", snippets[i])).strip() if i < len(snippets) else ""
                if not title:
                    continue
                combined = (title + " " + snippet).lower()
                # 只保留有提到這位歌手名稱的結果
                if not (_name_lower in combined or
                        (_en_lower and _en_lower in combined) or
                        _name_lower.replace(" ", "") in combined.replace(" ", "")):
                    continue
                results.append({
                    "id":         f"{query}_{i}",
                    "text":       f"{title} {snippet}",
                    "url":        url,
                    "created_at": "",
                    "has_media":  False,
                    "source":     "Google Search",
                })

            time.sleep(1.5)  # 避免被 Google 擋
        except Exception as e:
            log.warning(f"Google Search 失敗 [{query}]: {e}")

    log.info(f"[Google] {artist_name}: 找到 {len(results)} 筆結果")
    return results


# ════════════════════════════════════════════════════════════════
# 售票網址搜尋（AI 確認為演唱會後呼叫）
# ════════════════════════════════════════════════════════════════

# 已知售票平台 domain，優先排序
TICKET_DOMAINS = [
    # KKTIX
    "kktix.com",
    "kktix.cc",

    # Ticket Plus
    "ticketplus.com.tw",

    # tixCraft
    "tixcraft.com",

    # 寬宏售票
    "kham.com.tw",
    "khamticket.com",
    "khart.com.tw",

    # 年代售票
    "ticket.com.tw",

    # ibon
    "ibon.com.tw",
    "ticket.ibon.com.tw",

    # 全家
    "famiticket.com",
    "famiticket.com.tw",

    # Ticketmaster Taiwan
    "ticketmaster.com.tw",

    # UDN 售票網
    "udnfunlife.com",
    "tickets.udnfunlife.com",

    # UITicket
    "uiticket.com.tw",
]

def _clean_event_name_with_ai(raw_title: str, artist_name: str) -> str | None:
    """
    把售票頁面標題丟給 AI，只萃取出純粹的活動/tour 名稱。
    例："Yuika 2nd Asia Tour in Taipei 登記抽選 - Ticket Plus遠大售票系統"
        → "Yuika 2nd Asia Tour in Taipei"
    """
    prompt = (
        "以下是一個演唱會售票頁面的標題，請只回傳活動/tour的名稱，"
        "去掉「登記抽選」「售票」「購票」「-」後面的平台名稱等多餘文字。"
        "保留原本所有空格和大小寫，不要合併或移除任何單字之間的空格。"
        "例如：'ONE OK ROCK DETOX Asia Tour 2026 in Taipei' 就直接回傳，不要改動空格。"
        "只回傳名稱本身，不要任何說明或標點。\n\n"
        f"歌手：{artist_name}\n標題：{raw_title}"
    )
    try:
        if GROQ_KEY:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.0,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        if GEMINI_KEY:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
            resp = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 60, "temperature": 0.0}
            }, timeout=10)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if OPENAI_KEY:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.0,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning(f"[售票搜尋] AI清理名稱失敗: {e}")
    return None


def _extract_explicit_dates_from_text(text: str) -> dict:
    """
    用 regex 從原文抓出「YYYY年M月D日」「YYYY-MM-DD」「YYYY/MM/DD」等明確年份寫法。
    回傳 {(month, day): year} —— 這是原文「白紙黑字」寫的年份，比 AI 判斷可靠。
    """
    explicit = {}
    patterns = [
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            explicit[(mo, d)] = y
    return explicit


def _correct_ai_dates_with_source_text(dates: list, text: str) -> list:
    """
    ★ 核心防線：AI（尤其是 8B 小模型）在被要求「只回傳未來場次」時，
      遇到原文明寫的過去日期，常常不會老實回傳空陣列，而是直接把年份
      「捏」成未來年份濫竽充數（例如原文寫 2025年8月30日，AI 卻回 2026-08-30）。
      這裡用原文裡明確寫出的年份蓋掉 AI 自己改過的年份，年份以原文為準，
      月/日不變。修正後如果變成過去日期，交給呼叫端既有的
      _is_past_concert_date() 去過濾，而不是靠 AI 自己判斷。
    """
    explicit = _extract_explicit_dates_from_text(text)
    if not explicit:
        return dates
    corrected = []
    for d in dates:
        try:
            y, mo, day = d.split("-")
            key = (int(mo), int(day))
        except Exception:
            corrected.append(d)
            continue
        if key in explicit and explicit[key] != y:
            fixed = f"{explicit[key]}-{mo}-{day}"
            log.info(f"[售票搜尋] ⚠️  AI年份與原文不符，已修正: {d} → {fixed}")
            corrected.append(fixed)
        else:
            corrected.append(d)
    return corrected


def _extract_concert_info_with_ai(text: str, artist_name: str, today: str) -> dict:
    """
    從搜尋結果合併文字中，用 AI 萃取演唱會日期和地點。
    年份正確性不交給 AI 判斷 —— AI 只負責抓出文字裡寫了什麼日期，
    過去/未來的篩選跟年份校正都在這裡用規則做，因為小模型在這種
    「必須符合某個條件」的任務上，容易用竄改資料的方式來迎合指令。
    """
    ai_info = _extract_concert_info_with_ai_call(text, artist_name, today)
    ai_info["dates"] = _correct_ai_dates_with_source_text(ai_info.get("dates") or [], text)
    return ai_info


def _extract_concert_info_with_ai_call(text: str, artist_name: str, today: str) -> dict:
    """
    從搜尋結果合併文字中，用 AI 萃取演唱會日期和地點。
    """
    prompt = (
        "以下搜尋結果文字中可能包含多個不同歌手的演唱會資訊。\n"
        "今天日期是 " + today + "。（這只是給你參考現在幾號，絕對不可以把這個日期當成演唱會日期回傳）\n\n"
        "你的任務：只找出【" + artist_name + "】在台灣/台北的演唱會日期和地點。\n\n"
        "你只能根據文字中明確描述的資訊回答。\n"
        "禁止推論。\n"
        "嚴格規則：\n"
        "- 只抓明確屬於【" + artist_name + "】的場次，其他歌手的日期一律忽略\n"
        "- 只抓台灣或台北的場次，忽略首爾、香港、新加坡、東京、大阪等所有外國城市\n"
        "- 忠實抄錄文字中明確寫出的年月日，絕對不可以自己修改、推算或「校正」年份\n"
        "  （例如文字寫 2025年8月30日，就必須回傳 2025-08-30，不可以改成其他年份）\n"
        "- 如果找不到【" + artist_name + "】的台灣明確場次，dates 回傳空陣列\n"
        "- 日期格式統一為 YYYY-MM-DD\n"
        "- 確認同個歌手同個活動不只一天，dates 回傳多個日期\n"
        "- event_name 填寫 tour/活動的正式名稱（如有），沒有就填 null\n"
        "- 只回傳 JSON，不要其他說明\n\n"
        "如果你不確定，寧可回傳空陣列。"
        'JSON格式：{"dates": ["2026-05-23"], "venue": "台北國際會議中心", "event_name": "Tour名稱"}\n'
        '找不到時：{"dates": [], "venue": null, "event_name": null}\n\n'
        "搜尋文字：\n" + text[:3000]
        
    )
    if GROQ_KEY:
        for _attempt in range(3):
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.1-8b-instant",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 150,
                        "temperature": 0.0,
                    },
                    timeout=15,
                )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 15))
                    log.warning(f"[售票搜尋] Groq 429，等待 {wait} 秒（第{_attempt+1}次）")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                raw = re.sub(r"```(?:json)?", "", raw).strip()
                return json.loads(raw)
            except requests.exceptions.HTTPError as e:
                if "429" in str(e):
                    log.warning(f"[售票搜尋] Groq 429，等待15秒（第{_attempt+1}次）")
                    time.sleep(15)
                    continue
                log.warning(f"[售票搜尋] Groq失敗，改用備用AI: {e}")
                break
            except Exception as e:
                log.warning(f"[售票搜尋] Groq失敗，改用備用AI: {e}")
                break
    if GEMINI_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
            resp = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 150, "temperature": 0.0,
                                     "responseMimeType": "application/json"}
            }, timeout=15)
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            return json.loads(raw)
        except Exception as e:
            log.warning(f"[售票搜尋] Gemini失敗，改用備用AI: {e}")
    if OPENAI_KEY:
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.0,
                },
                timeout=15,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"```(?:json)?", "", raw).strip()
            return json.loads(raw)
        except Exception as e:
            log.warning(f"[售票搜尋] OpenAI失敗: {e}")
    log.warning("[售票搜尋] 所有AI均無法萃取日期地點")
    return {"dates": [], "venue": None}



# ==============================
# Search cache / rate control
# ==============================
SEARCH_CACHE_FILE = "search_cache.json"
SEARCH_CACHE_TTL = 1800  # 30 minutes

def load_search_cache() -> dict:
    try:
        with open(SEARCH_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_search_cache(cache: dict) -> None:
    try:
        with open(SEARCH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def cached_wait() -> None:
    time.sleep(random.uniform(2.5, 5.5))


class TicketFilter:
    """
    售票連結過濾器：阶段式过滤 + 評分制
    第一關：硬性排除條件（排除明顯不符的連結）
    第二關：評分制（在通過硬性條件後才評分）
    """
    def __init__(self, target_years: List[str], artist_name: str):
        self.target_years = target_years
        self.artist_name = artist_name.lower()
        # 排除 2010 到目標年份前一年的資料 (防止抓到過期資訊)
        self.exclude_years = [str(y) for y in range(2010, int(target_years[0]))]
        
        # ★ 台灣關鍵詞：包含這些 = 台灣場次
        self.tw_indicators = ["台灣", "台湾", "taipei", "台北", "小巨蛋", "arena"]
        
        # ★ 外國城市黑名單：包含這些 = 直接排除（不是台灣場次）
        self.foreign_cities = [
            "hongkong", "hk", "hong kong",
            "bangkok", "thailand", "ไทย",
            "manila", "philippines",
            "seoul", "korea", "서울",
            "tokyo", "osaka", "fukuoka", "nagoya", "japan", "kobe", "kyoto", "yokohama", "sapporo",
            "asiaworld", "marine messe", "zepp", "makuhari", "saitama",
        ]
        
        # 售票平台關鍵詞
        self.ticket_keywords = ["票", "售票", "購票", "ticket", "kktix", "ticketplus", "tixcraft", "indievox"]

    def evaluate_link(self, url: str, title: str, snippet: str, from_query: str) -> bool:
        full_text = f"{title} {snippet} {url}".lower()
        import re as _re

        # ════════════════════════════════════════════════════════
        # ★ 第一關：硬性排除條件（符合任一個就 return False）
        # ════════════════════════════════════════════════════════

        # 1. 排除「過時年份」內容
        #    ★ 原本用 \b{yr}\b，但中文「年」被視為單詞字元，
        #      導致「2025年8月30日」這種寫法完全比對不到 \b 邊界，過期連結永遠排除不掉。
        #      改用「前後不是數字」而非「單詞邊界」，中英文年份格式都能正確排除。
        if any(_re.search(rf"(?<!\d){yr}(?!\d)", full_text) for yr in self.exclude_years):
            log.info("[TicketFilter] reject: old year")
            return False

        # 2. 排除「活動列表頁」（沒有具體活動 ID）
        if _re.search(r"/(activity|event|ticket)/?$", url.lower()):
            log.info("[TicketFilter] reject: list page")
            return False

        # 3. 排除「總整理/全攻略」類懶人包
        aggregator_keywords = ["總整理", "总整理", "全攻略", "特企", "懶人包", "攻略", "彙整", "情報整理", "速報"]
        if any(kw in title.lower() for kw in aggregator_keywords):
            return False

        # 4. ★ 排除「明確的外國城市」—— 關鍵！
        #    如果 URL 或 title 明確說是外國城市，不管其他條件都排除
        if any(city in full_text for city in self.foreign_cities):
            log.info("[TicketFilter] reject: foreign city")
            return False

        # 5. ★ 歌手名字必須在「標題」中出現（不看 snippet）
        #    因為 snippet 是搜尋引擎摘要，常混雜其他歌手
        search_text = f"{title} {snippet} {url}".lower()
        clean_name = self.artist_name.replace(" ", "")

        if not _artist_name_in_text(
            f"{title} {snippet} {url}",
            self.artist_name,
        ):
            log.info(
                "[TicketFilter] reject: artist mismatch\nartist=%s\ntitle=%s",
                clean_name,
                search_text,
            )
            return False

        # ════════════════════════════════════════════════════════
        # ★ 第二關：才開始計分（通過了上面所有硬性條件）
        # ════════════════════════════════════════════════════════

        score = 0
        detail = []

        tw_hits = sum(1 for tw in self.tw_indicators if tw in full_text)
        score += tw_hits * 20
        if tw_hits:
            detail.append(f"tw+{tw_hits*20}")

        if any(yr in full_text for yr in self.target_years):
            score += 30
            detail.append("year+30")

        # C. 售票平台關鍵詞
        ticket_hits = sum(1 for kw in self.ticket_keywords if kw.lower() in full_text)
        score += min(ticket_hits * 10, 30)  # 最高 +30

        # D. Tour/演唱會名稱指標
        if any(kw in title.lower() for kw in ["tour", "演唱會", "concert", "live"]):
            score += 15

        # E. 特殊規則：LiSA 15週年巡演
        if "15" in title and ("smile always" in title.lower() or "lisa" in title.lower()):
            score += 20

        log.info(
            "[TicketFilter]\n"
            "title=%s\n"
            "snippet=%s\n"
            "url=%s",
            title,
            snippet,
            url,
        )

        log.info(f"[售票搜尋] 評分: {score}")   
        # 判定門檻：改成 50 分（之前 60 分太高，容易漏掉正確的）
        return score >= 40


# ════════════════════════════════════════════════════════════════
# ★ SearXNG 多實例輪詢搜尋 —— 免費開源搜尋服務，穩定性比直接打 Google/Brave 更好
# ════════════════════════════════════════════════════════════════


# ★ 自架實例：優先使用，不會被公開流量限流，永遠排第一個嘗試
LOCAL_SEARXNG_INSTANCE = os.getenv("LOCAL_SEARXNG_URL", "http://localhost:8080")

SEARXNG_INSTANCES = []  # disable public instances  # 停用公開實例，只使用 localhost

# Search order: localhost SearXNG -> DDGS fallback

def _searxng_search(query: str, max_results: int = 10) -> list[dict]:
    """
    用公開 SearXNG 實例搜尋，回傳格式對齊 ddgs 的 [{'title','href','body'}]

    ★ 診斷強化：不論成功/失敗/空結果，每個實例都會留下一行 log，
      方便判斷到底是「連不上」「被擋（非 200）」「回傳空結果」還是「JSON 解析失敗」。
    ★ 優先順序：自架實例（不會被限流）先試，公開實例只當備援，且不打亂順序，
      確保自架實例永遠是第一個被嘗試的。
    """
    import random as _rand
    instances = [LOCAL_SEARXNG_INSTANCE]

    attempted = 0
    for base in instances:
        name = base.split("/")[-1]
        attempted += 1
        if attempted > 1:
            # 每個實例之間加一點隨機延遲，避免短時間內連環打多站被當成爬蟲流量
            time.sleep(_rand.uniform(0.8, 1.8))
        try:
            r = requests.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=15,
            )
            if r.status_code != 200:
                log.info(f"[售票搜尋] SearXNG({name}) 回傳非 200 狀態碼: {r.status_code}，跳過")
                continue

            try:
                data = r.json()
            except ValueError:
                # 常見情況：實例把 JSON API 關掉了，改回傳 HTML/空白頁
                snippet = r.text[:80].replace("\n", " ")
                log.warning(f"[售票搜尋] SearXNG({name}) 回傳非 JSON 內容（可能已停用 JSON API），內容開頭: {snippet!r}")
                continue

            results = []
            for item in data.get("results", [])[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "href":  item.get("url", ""),
                    "body":  item.get("content", ""),
                })

            if results:
                log.info(f"[售票搜尋] SearXNG({name}) 取得 {len(results)} 筆")
                return results
            else:
                log.info(f"[售票搜尋] SearXNG({name}) 回應 200 但無搜尋結果，跳過")

        except requests.exceptions.Timeout:
            log.warning(f"[售票搜尋] SearXNG({name}) 連線逾時，跳過")
            continue
        except requests.exceptions.RequestException as e:
            log.warning(f"[售票搜尋] SearXNG({name}) 連線失敗: {e}")
            continue
        except Exception as e:
            log.warning(f"[售票搜尋] SearXNG({name}) 未預期錯誤: {e}")
            continue

    log.warning(f"[售票搜尋] 所有 {attempted} 個 SearXNG 實例皆無有效結果，改用 ddgs fallback")
    return []


# ════════════════════════════════════════════════════════════════
# ★ search_ticket_url 狀態機定義
#   每個階段只能回傳下列四種狀態之一，呼叫端用狀態分流，
#   不再靠「欄位是不是 None」去反推發生了什麼事。
# ════════════════════════════════════════════════════════════════
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional as _Optional, List as _List


class TicketSearchStatus(Enum):
    TICKET_FOUND = "ticket_found"   # 找到售票平台連結（日期/地點可能仍不完整，但連結是真的）
    DATE_ONLY    = "date_only"      # 沒找到售票連結，但從新聞/搜尋結果找到確切日期
    NOT_FOUND    = "not_found"      # 連日期都沒找到，完全沒有可用資訊
    SEARCH_ERROR = "search_error"   # 搜尋機制本身出錯（缺套件、例外等），跟「找不到」是不同狀態


@dataclass
class TicketSearchResult:
    status: TicketSearchStatus
    ticket_url: _Optional[str] = None
    event_name: _Optional[str] = None
    venue: _Optional[str] = None
    sessions: _List[dict] = field(default_factory=list)
    error: _Optional[str] = None

    @property
    def found_date(self) -> _Optional[str]:
        return self.sessions[0]["date"] if self.sessions else None

    def to_dict(self) -> dict:
        """轉成舊呼叫端原本期待的 dict 格式，額外附上 status 讓呼叫端可以明確分流。"""
        return {
            "status":     self.status.value,
            "ticket_url": self.ticket_url,
            "found_date": self.found_date,
            "event_name": self.event_name,
            "venue":      self.venue,
            "sessions":   self.sessions,
        }

from datetime import datetime

def _validate_news_result(ai_info, today):
    dates = ai_info.get("dates", [])

    if not dates:
        return False

    today_dt = datetime.strptime(today, "%Y-%m-%d")

    for d in dates:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")

            # 不接受今天
            if dt <= today_dt:
                return False

            # 超過兩年也不要
            if (dt - today_dt).days > 730:
                return False

        except Exception:
            return False

    return True

def _stage1_search_ticket_platform(artist_name: str, queries: list, ticket_filter: "TicketFilter"):
    """
    階段 1：在售票平台網域中尋找符合評分門檻的連結。
    成功 → (best_url, raw_event_name, winning_results)
    失敗 → (None, None, [])
    """
    from ddgs import DDGS

    best_url = None
    event_name = None
    winning_results = []

    for query in queries:
        try:
            log.info(f"[售票搜尋] 搜尋: {query}")
            results = _searxng_search(query, max_results=10)
            if not results:
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(query, max_results=10))
                    if not results:
                        log.info(f"[售票搜尋] ddgs 也回傳 0 筆結果: {query}")
                except Exception as e:
                    log.warning(f"[售票搜尋] ddgs 也失敗: {e}")
                    results = []
            from pprint import pformat
            log.info("[DDGS]\n%s", pformat(results[:3]))

            for r in results:
                if any(domain in r["href"] for domain in TICKET_DOMAINS):
                    import pprint
                    log.info("result=\n%s", pprint.pformat(r))
                    is_valid = ticket_filter.evaluate_link(
                        url=r["href"], title=r.get("title", ""),
                        snippet=r.get("body", ""), from_query=query,
                    )
                    if is_valid:
                        best_url = r["href"]
                        event_name = r["title"]
                        log.info(f"[售票搜尋] ✅ 評分通過，確定售票連結: {best_url}")
                        break
                    else:
                        log.info(f"[售票搜尋] ⏭️  評分不足，跳過連結: {r['href']}")

            if best_url:
                winning_results = results
                break
            time.sleep(4)
        except Exception as e:
            log.warning(f"[售票搜尋] 失敗 [{query}]: {e}")

    return best_url, event_name, winning_results


import datetime as _dt_validate

_CN_DATE_RE = re.compile(r"(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_ISO_DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")


def _extract_explicit_dates_from_text(text: str) -> list[tuple]:
    """從原始搜尋/新聞文字中，抓出文字裡『明確寫出來』的日期 (year_or_None, month, day)。"""
    out = []
    for m in _CN_DATE_RE.finditer(text):
        yr = int(m.group(1)) if m.group(1) else None
        out.append((yr, int(m.group(2)), int(m.group(3))))
    for m in _ISO_DATE_RE.finditer(text):
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return out


def _validate_and_fix_ai_dates(ai_dates: list[str], raw_text: str, today: str) -> list[str]:
    """
    交叉比對 AI 抽取出的日期跟原文明確寫出的日期。
    小模型常見的失敗模式：原文明明寫「2025年8月30日」，但因為 prompt 要求
    「只回傳未來場次」，模型不會老實排除，而是直接把年份改成 2026 讓它「看起來」是未來。
    這裡的規則：
      1. 等於 today 的日期：高度可疑（很可能是把 prompt 裡的『今天日期』誤抄回來），丟棄。
      2. 原文對同一個月/日有明確標示年份，且該年份跟 AI 給的不同 → 以原文年份為準（修正回去）。
      3. AI 給的月/日在原文完全找不到任何蹤跡 → 視為幻覺，丟棄。
    """
    explicit = _extract_explicit_dates_from_text(raw_text)
    year_by_md: dict[tuple, int] = {}
    md_seen: set[tuple] = set()
    for yr, mo, da in explicit:
        md_seen.add((mo, da))
        if yr is not None:
            # 同一個月/日在原文出現多個不同年份時，保留先出現的那個
            year_by_md.setdefault((mo, da), yr)

    fixed = []
    for d in ai_dates:
        if d == today:
            log.info(f"[售票搜尋] ⏭️  日期 {d} 等於今天，疑似AI誤把參考日期當內容，丟棄")
            continue
        try:
            dt = _dt_validate.date.fromisoformat(d)
        except ValueError:
            continue
        mo, da = dt.month, dt.day
        real_year = year_by_md.get((mo, da))
        if real_year is not None and real_year != dt.year:
            corrected = f"{real_year:04d}-{mo:02d}-{da:02d}"
            log.info(f"[售票搜尋] 🛠️  原文明確寫年份為 {real_year}，修正 AI 給的 {d} → {corrected}")
            fixed.append(corrected)
            continue
        if (mo, da) not in md_seen:
            log.info(f"[售票搜尋] ⏭️  日期 {d} 的月/日在原文找不到任何依據，疑似AI幻覺，丟棄")
            continue
        fixed.append(d)
    return fixed


def _stage2_search_news_dates(queries: list, artist_name: str, tweet_text: str, today: str) -> TicketSearchResult:
    """
    階段 2（只在階段 1 失敗時執行）：退而求其次，從新聞/搜尋結果裡找日期。
    找到日期 → status=DATE_ONLY
    完全沒有 → status=NOT_FOUND
    """
    from ddgs import DDGS

    log.info("[售票搜尋] 未找到售票平台，嘗試從搜尋結果抓日期地點...")
    _all_news_results = []
    for query in queries[:2]:
        try:
            _nr = _searxng_search(query, max_results=8)
            if not _nr:
                try:
                    with DDGS() as ddgs:
                        _nr = list(ddgs.text(query, max_results=8))
                except Exception:
                    _nr = []
            _all_news_results.extend(_nr)
            time.sleep(3)
        except Exception:
            pass

    if not _all_news_results:
        return TicketSearchResult(status=TicketSearchStatus.NOT_FOUND)

    _news_text = " ".join(r.get("title", "") + " " + r.get("body", "") for r in _all_news_results)
    if tweet_text:
        _news_text = "[原始推文]\n" + tweet_text + "\n\n[搜尋結果]\n" + _news_text

    _news_ai    = _extract_concert_info_with_ai(_news_text, artist_name, today)
    _news_dates = _news_ai.get("dates") or []
    _news_venue = _news_ai.get("venue")
    _news_event = _news_ai.get("event_name")

    if not _news_dates:
        return TicketSearchResult(status=TicketSearchStatus.NOT_FOUND)

    # ★ 場地驗證：新聞摘要常會混雜同一波亞洲巡演的其他國家場次，
    #    AI 有時會誤抓成日本/韓國等地的場地。這裡沒有 ticket_url 可佐證，
    #    信心本來就比較低，一旦場地是外國場地就直接視為無效，不要寫入。
    if _is_foreign_venue(_news_venue):
        log.info(f"[售票搜尋] ⏭️  從新聞找到的場地「{_news_venue}」疑似外國場地，捨棄")
        return TicketSearchResult(status=TicketSearchStatus.NOT_FOUND)

    # ★ 日期交叉校驗：用原文明確寫出的年份修正/剔除 AI 幻覺出的日期
    #   （例如原文寫 2025年8月30日，AI 卻回傳 2026-08-30）
    _news_dates = _validate_and_fix_ai_dates(_news_dates, _news_text, today)
    if not _news_dates:
        return TicketSearchResult(status=TicketSearchStatus.NOT_FOUND)

    log.info(f"[售票搜尋] 📅 從新聞找到日期: {_news_dates}（無售票連結）")
    return TicketSearchResult(
        status=TicketSearchStatus.DATE_ONLY,
        event_name=_news_event,
        venue=_news_venue,
        sessions=[{"date": d, "venue": _news_venue, "url": None} for d in _news_dates],
    )


def _stage3_fetch_ticket_page(best_url: str, artist_name: str, today: str, all_dates: list, venue):
    """
    階段 4：嘗試 fetch 售票頁面本身（非 JS 渲染時才有用），補充日期/地點/其他場次連結。
    無論成不成功，都回傳 (all_dates, venue, session_links) —— 不會拋例外中斷主流程。
    """
    import re as _re
    import urllib.parse as _up

    session_links = []
    try:
        rp = requests.get(best_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9"
        }, timeout=12)
        if rp.status_code == 200:
            html = rp.text
            is_js = "JavaScript enabled" in html or len(html.strip()) < 500
            if not is_js:
                _base = _up.urlparse(best_url)
                _origin = f"{_base.scheme}://{_base.netloc}"
                plain = _re.sub(r"<[^>]+>", " ", html)
                plain = _re.sub(r"\s+", " ", plain)
                if not all_dates or not venue:
                    time.sleep(2)
                    page_ai = _extract_concert_info_with_ai(plain, artist_name, today)
                    page_dates = page_ai.get("dates") or []
                    if page_dates and len(page_dates) > len(all_dates):
                        all_dates = page_dates
                        log.info(f"[售票搜尋] 📅 頁面AI日期（補充）: {all_dates}")
                    elif not all_dates and page_dates:
                        all_dates = page_dates
                        log.info(f"[售票搜尋] 📅 頁面AI日期（fallback）: {all_dates}")
                    if not venue and page_ai.get("venue"):
                        venue = page_ai["venue"]
                        log.info(f"[售票搜尋] 📍 頁面AI地點: {venue}")
                else:
                    log.info("[售票搜尋] 📅 已有日期地點，跳過頁面AI分析")

                for href in (_re.findall(r"href='([^']+)'", html) + _re.findall(r'href="([^"]+)"', html)):
                    if not href.startswith("http"):
                        href = _origin + href
                    if (href != best_url and _base.netloc in href
                            and any(kw in href for kw in ["/activity/", "/event/", "/ticket/"])
                            and href not in session_links):
                        session_links.append(href)
                if session_links:
                    log.info(f"[售票搜尋] 🔗 其他場次: {session_links[:5]}")
            else:
                log.info("[售票搜尋] JS渲染頁面，使用搜尋結果資料")
    except Exception as e:
        log.warning(f"[售票搜尋] fetch 頁面失敗: {e}")

    return all_dates, venue, session_links


def _stage4_build_sessions(all_dates: list, venue, best_url: str, session_links: list) -> list:
    """階段 5：組合出 sessions 清單（不做網路請求，純資料組裝）"""
    all_urls = [best_url] + session_links
    if not all_dates:
        return [{"date": None, "venue": venue, "url": best_url}]
    if len(all_dates) == 1:
        return [{"date": all_dates[0], "venue": venue, "url": best_url}]
    return [
        {"date": d, "venue": venue, "url": all_urls[i] if i < len(all_urls) else best_url}
        for i, d in enumerate(all_dates)
    ]


def _stage5_supplement_session_links(sessions: list, best_url: str, event_name, artist_name: str) -> list:
    """階段 6（僅多場次時執行）：針對每個日期補搜獨立售票連結"""
    from ddgs import DDGS
    import urllib.parse as _up
    import datetime as _dt_s

    log.info("[售票搜尋] 多場次，補搜尋各場次獨立售票連結...")
    _base_netloc = _up.urlparse(best_url).netloc
    _cur_yr = _dt_s.date.today().year

    for i, session in enumerate(sessions):
        d = session["date"]
        if not d or session["url"] != best_url or i == 0:
            continue
        try:
            _month_day = f"{int(d[5:7])}/{int(d[8:10])}"
            _event_hint = event_name or artist_name
            _search_q = f"{_event_hint} {_month_day} {_base_netloc.split('.')[0]}"
            log.info(f"[售票搜尋] 補搜場次連結: {_search_q}")
            _sr = _searxng_search(_search_q, max_results=8)
            if not _sr:
                try:
                    with DDGS() as ddgs:
                        _sr = list(ddgs.text(_search_q, max_results=8))
                except Exception as e:
                    log.warning(f"[售票搜尋] 補搜 ddgs 失敗: {e}")
                    _sr = []
            for r in _sr:
                if _base_netloc in r["href"] and r["href"] != best_url:
                    _rb = (r.get("title", "") + " " + r.get("body", ""))
                    _old_years = [str(y) for y in range(2020, _cur_yr)]
                    _has_old = any(yr in _rb for yr in _old_years)
                    _has_new = str(_cur_yr) in _rb or str(_cur_yr + 1) in _rb
                    if _has_old and not _has_new:
                        log.info(f"[售票搜尋] ⚠️  跳過舊年份連結: {r['href']}")
                        continue
                    session["url"] = r["href"]
                    log.info(f"[售票搜尋] 🔗 {d} 找到獨立連結: {r['href']}")
                    break
            time.sleep(1)
        except Exception as e:
            log.warning(f"[售票搜尋] 補搜場次連結失敗: {e}")

    return sessions

def _count_artist_hits(results, artist_name):
    artist = artist_name.lower()

    count = 0

    for r in results:
        text = (
            r.get("title", "")
            + " "
            + r.get("body", "")
        ).lower()

        if artist in text:
            count += 1

    return count

def _validate_ai_result(full_text, artist, dates, venue):
    if not dates:
        return False
    pos = full_text.lower().find(artist.lower())
    if pos == -1:
        return False
    window = full_text[max(0, pos-500):pos+500]
    if venue and venue not in window:
        return False
    if not any(d in window for d in dates):
        return False
    return True

def search_ticket_url(artist_name: str, artist_en: str = "", concert_date: str = None, tweet_text: str = "") -> dict:
    """
    整合 TicketFilter 權重機制的售票搜尋。

    狀態機版本：內部拆成 6 個階段，每個階段回傳明確的成功/失敗結果，
    不再讓「AI 沒抓到日期」這種失敗訊號被後面的程式碼默默忽略。
    回傳 dict 裡多了一個 "status" 欄位（見 TicketSearchStatus），
    呼叫端應該優先看 status，而不是只檢查 ticket_url 是否為 None。
    """
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        log.error("[售票搜尋] 找不到 ddgs 套件")
        return TicketSearchResult(
            status=TicketSearchStatus.SEARCH_ERROR, error="找不到 ddgs 套件"
        ).to_dict()

    import datetime as _dt
    _yr = _dt.date.today().year
    _today = _dt.date.today().isoformat()

    ticket_filter = TicketFilter(target_years=[str(_yr), str(_yr + 1)], artist_name=artist_name)

    _jp_hint = "日本" if artist_en else ""
    queries = [
        f"{_jp_hint}{artist_name} 台灣演唱會 {_yr} 售票",
        f"{_jp_hint}{artist_name} 台灣演唱會 {_yr+1} 售票",
        f"{artist_name} kktix OR ticketplus 台灣",
    ]

    best_url, raw_event_name, winning_results = _stage1_search_ticket_platform(
        artist_name, queries, ticket_filter
    )

    if not best_url:
        return _stage2_search_news_dates(queries, artist_name, tweet_text, _today).to_dict()

    combined_text = " ".join(
        r.get("title", "") + " " + r.get("body", "") for r in winning_results
    )
    if tweet_text:
        combined_text = "[原始推文]\n" + tweet_text + "\n\n[搜尋結果]\n" + combined_text
    log.info(f"[售票搜尋] 合併文字({len(combined_text)}字): {combined_text[:500]}")

    ai_info = _extract_concert_info_with_ai(combined_text, artist_name, _today)
    all_dates = ai_info.get("dates") or []
    venue = ai_info.get("venue")
    ai_event_name = ai_info.get("event_name")
    # ----------------------------------------------------------
    # 新聞 fallback 安全機制
    # ----------------------------------------------------------

    # 沒有售票網址時，不允許 AI 單獨建立演唱會
    if not best_url:
        if not all_dates:
            log.info("[售票搜尋] 新聞未找到有效日期，放棄")
            return None

        if not venue:
            log.info("[售票搜尋] 新聞未找到場地，放棄")
            return None

        if not event_name:
            log.info("[售票搜尋] 新聞未找到活動名稱，放棄")
            return None

    if not best_url:
        required = {
            "date": bool(all_dates),
            "venue": bool(venue),
            "event": bool(event_name),
        }

        if not all(required.values()):
            log.info(
                "[售票搜尋] 新聞資訊不完整 %s，放棄",
                required,
            )
            return None

    if not best_url:
        if not _validate_ai_result(
            tweet_text,
            artist_name,
            all_dates,
            venue,
        ):
            log.info("[售票搜尋] AI 驗證失敗")
            return 
    if not best_url:
        if not _validate_news_result(ai_info, today):
            log.info("[售票搜尋] AI 日期驗證失敗")
            return None

    log.info(f"[售票搜尋] 📅 AI找到日期: {all_dates}")
    log.info(f"[售票搜尋] 📍 AI找到地點: {venue}")
    if ai_event_name:
        log.info(f"[售票搜尋] 📌 AI找到活動名稱: {ai_event_name}")

    time.sleep(2)
    event_name = raw_event_name
    clean = _clean_event_name_with_ai(raw_event_name, artist_name)
    if clean:
        log.info(f"[售票搜尋] 📌 AI清理名稱: {clean}")
        event_name = clean
    elif ai_event_name:
        event_name = ai_event_name
        log.info(f"[售票搜尋] 📌 使用AI萃取名稱: {event_name}")

    all_dates, venue, session_links = _stage3_fetch_ticket_page(
        best_url, artist_name, _today, all_dates, venue
    )

    sessions = _stage4_build_sessions(all_dates, venue, best_url, session_links)

    if len(sessions) > 1:
        sessions = _stage5_supplement_session_links(sessions, best_url, event_name, artist_name)

    for s in sessions:
        log.info(f"[售票搜尋] 🎫 {s['date']} @ {s['venue']}  {s['url']}")

    return TicketSearchResult(
        status=TicketSearchStatus.TICKET_FOUND,
        ticket_url=best_url,
        event_name=event_name,
        venue=venue,
        sessions=sessions,
    ).to_dict()


# ════════════════════════════════════════════════════════════════
# 統一入口
# ════════════════════════════════════════════════════════════════

def fetch_tweets(twitter_handle: str, limit: int = 20, prefer: str = "nitter", artist_name: str = "", artist_en: str = "") -> list[dict]:
    """
    prefer="nitter"  → Nitter RSS + fxtwitter API（預設）
    prefer="bearer"  → X API Bearer Token
    prefer="google"  → Google Search（不需任何 API）
    """
    if prefer == "nitter":
        tweets = fetch_tweets_nitter(twitter_handle, limit)
        if tweets:
            has_tw_content = any(passes_keyword_filter(t.get("text", "")) for t in tweets)
            if not has_tw_content:
                log.info(f"Nitter 無台灣相關推文，啟動 DDG 售票平台補搜: @{twitter_handle}")
                ddg_hints = _fetch_ddg_ticket_hints(artist_name or twitter_handle, artist_en)
                existing_urls = {t["url"] for t in tweets}
                for h in ddg_hints:
                    if h["url"] not in existing_urls:
                        tweets.append(h)
                        existing_urls.add(h["url"])
            return tweets
        log.info("Nitter 無結果，改用 Google Search...")

    if prefer == "bearer":
        tweets = fetch_tweets_bearer(twitter_handle, limit)
        if tweets:
            return tweets
        log.info("Bearer Token 無效，改用 Google Search...")

    # Google Search 作為最終 fallback（或直接使用）
    return fetch_google_search(artist_name or twitter_handle, artist_en)


# ════════════════════════════════════════════════════════════════
# 關鍵字過濾
# ════════════════════════════════════════════════════════════════

def passes_keyword_filter(text: str) -> bool:
    low = text.lower()
    return (
        any(kw.lower() in low for kw in TW_KEYWORDS) and
        not any(kw.lower() in low for kw in NEG_KEYWORDS)
    )


# 不可靠來源（個人貼文、論壇等容易誤判）
UNTRUSTED_SOURCE_DOMAINS = [
    "facebook.com", "instagram.com", "threads.net",
    "tiktok.com", "youtube.com", "reddit.com",
    "ptt.cc", "dcard.tw", "mobile01.com",
    "bilibili.com", "weibo.com",
]


def _artist_name_in_text(text: str, artist_name: str, artist_en: str = "") -> bool:
    """嚴格比對歌手名稱，避免 'aimer' 等短名誤判。"""
    combined = text.lower()
    compact_text = combined.replace(" ", "").replace(".", "")
    for raw in [artist_name, artist_en]:
        if not raw:
            continue
        name = raw.lower().strip()
        compact_name = name.replace(" ", "").replace(".", "")
        if len(name) <= 6:
            if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", combined):
                return True
            if compact_name and len(compact_name) >= 4 and compact_name in compact_text:
                return True
        elif name in combined or compact_name in compact_text:
            return True
    return False


def _is_untrusted_source(url: str) -> bool:
    u = (url or "").lower()
    return any(d in u for d in UNTRUSTED_SOURCE_DOMAINS)


# ★ 外國場地關鍵字（英文拼音 + 中文/日文譯名都要涵蓋，避免只比對羅馬拼音漏放行）
_FOREIGN_VENUE_KEYWORDS = [
    # 英文/拼音
    "zepp tokyo", "zepp osaka", "zepp fukuoka", "zepp nagoya",
    "kspo dome", "asiaworld-expo", "asiaworld",
    "hong kong", "hongkong",
    "singapore", "bangkok", "manila", "seoul",
    "tokyo dome", "tokyo", "osaka", "fukuoka", "nagoya",
    "kobe", "kyoto", "yokohama", "sapporo", "makuhari", "saitama",
    "marine messe", "thailand", "philippines", "korea",
    # 中文/日文譯名（新聞、搜尋摘要常用這種寫法，純英文關鍵字比對不到）
    "大阪", "東京", "名古屋", "福岡", "神戶", "京都", "橫濱", "横浜",
    "札幌", "埼玉", "幕張", "香港", "首爾", "首尔", "曼谷", "馬尼拉",
    "新加坡", "釜山",
]


def _is_foreign_venue(venue: str | None) -> bool:
    """判斷場地名稱是否屬於外國場地（同時比對英文拼音與中文/日文譯名）。"""
    if not venue:
        return False
    v = venue.lower()
    return any(kw.lower() in v for kw in _FOREIGN_VENUE_KEYWORDS)


def _is_past_concert_date(date_str: str | None) -> bool:
    if not date_str:
        return False
    import datetime as _dt
    try:
        return _dt.date.fromisoformat(date_str) < _dt.date.today()
    except ValueError:
        return True


def _fetch_ddg_ticket_hints(artist_name: str, artist_en: str = "") -> list[dict]:
    """DDG 補搜：只接受售票平台連結，並經 TicketFilter 評分。"""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    import datetime as _dt
    _yr = _dt.date.today().year
    ticket_filter = TicketFilter(target_years=[str(_yr), str(_yr + 1)], artist_name=artist_name)
    queries = [
        f"{artist_name} 台灣演唱會 kktix OR ticketplus {_yr}",
        f"{artist_en or artist_name} Taiwan concert Taipei kktix {_yr}",
        f"{artist_name} 台灣演唱會 tixcraft OR indievox {_yr}",
    ]
    hints: list[dict] = []
    seen: set[str] = set()

    for sq in queries:
        try:
            log.info(f"[DDG] 售票平台補搜: {sq}")
            # ★ 先試 SearXNG，失敗才退回 ddgs
            results = _searxng_search(sq, max_results=8)
            if not results:
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(sq, max_results=8))
                except Exception as e:
                    log.warning(f"[DDG] ddgs 補搜失敗: {e}")
                    results = []
            for sr in results:
                url = sr.get("href", "")
                title = sr.get("title", "")
                body = sr.get("body", "")
                text = f"{title} {body}"
                if not url or url in seen:
                    continue
                if _is_untrusted_source(url):
                    continue
                if not any(d in url for d in TICKET_DOMAINS):
                    continue
                if not _artist_name_in_text(text, artist_name, artist_en):
                    continue
                if not ticket_filter.evaluate_link(url, title, body, sq):
                    log.info(f"[DDG] 評分不足，跳過: {url}")
                    continue
                seen.add(url)
                hints.append({
                    "id": f"ddg_{url}",
                    "text": text,
                    "url": url,
                    "created_at": "",
                    "has_media": False,
                    "source": "ddg_supplement",
                    "ticket_url": url,
                })
            time.sleep(3)
        except Exception as e:
            log.warning(f"[DDG] 補搜失敗: {e}")

    log.info(f"[DDG] {artist_name}: 找到 {len(hints)} 筆售票平台結果")
    return hints


# ════════════════════════════════════════════════════════════════
# AI 語意分析
# ════════════════════════════════════════════════════════════════

def analyze_with_ai(text: str, artist_name: str, created_at: str = "") -> dict | None:
    """優先用 Groq（免費快速），備用 Gemini，再備用 OpenAI。"""
    if GROQ_KEY:
        return _analyze_groq(text, artist_name, created_at)
    if GEMINI_KEY:
        return _analyze_gemini(text, artist_name, created_at)
    if OPENAI_KEY:
        return _analyze_openai(text, artist_name, created_at)
    return None


def _analyze_groq(text: str, artist_name: str, created_at: str = "") -> dict | None:
    _date_hint = f"\n貼文發布時間: {created_at}" if created_at else ""
    for _attempt in range(3):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"歌手: {artist_name}{_date_hint}\n\n推文:\n{text[:2000]}"},
                    ],
                    "max_tokens": 300,
                    "temperature": 0.1,
                },
                timeout=20,
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                log.warning(f"Groq 429 rate limit，等待 {wait} 秒後重試（第{_attempt+1}次）")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"```(?:json)?", "", raw).strip()
            return json.loads(raw)
        except requests.exceptions.HTTPError as e:
            if "429" in str(e):
                log.warning(f"Groq 429，等待10秒後重試（第{_attempt+1}次）")
                time.sleep(10)
                continue
            log.warning(f"Groq 分析失敗: {e}")
            return None
        except Exception as e:
            log.warning(f"Groq 分析失敗: {e}")
            return None
    log.warning("Groq 重試3次仍失敗，改用備用 AI")
    return None


def _analyze_gemini(text: str, artist_name: str, created_at: str = "") -> Optional[Dict[str, Any]]:
    """
    使用 Google Gemini API 分析推文內容。
    針對 400 錯誤優化了 Payload 結構與認證方式。
    """
    try:
        # 1. 準備 Payload
        # 將任務目標與資料內容拆分，提高模型遵循指令的準確度
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [{
                "parts": [{"text": f"歌手名稱: {artist_name}" + (f"\n貼文發布時間: {created_at}" if created_at else "") + f"\n待分析內容:\n{text[:2000]}"}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "topP": 0.8,
                "topK": 40,
                "maxOutputTokens": 1024,
                "responseMimeType": "application/json"  # 強制 API 輸出 JSON 格式
            }
        }

        # 2. 建立請求 (將 Key 放在 URL 參數中可避免大部分 400/401 問題)
        model_id = "gemini-2.0-flash" # 確保使用正確的模型標籤
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}"
        
        headers = {"Content-Type": "application/json"}

        # 3. 發送請求並處理錯誤
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            # 當出現 400 錯誤時，印出詳細原因 (Google 會說明是哪個欄位格式不對)
            log.error(f"Gemini API 請求失敗 [Status {response.status_code}]: {response.text}")
            return None

        result = response.json()

        # 4. 解析回傳內容
        if "candidates" in result and result["candidates"]:
            # 取得生成的文字
            content_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            
            # 移除 Markdown 標籤 (避免 AI 雖然回傳 JSON 但還是套了 ```json ... ```)
            clean_json = re.sub(r"```(?:json)?", "", content_text).strip()
            return json.loads(clean_json)
        
        log.warning("Gemini 未能產生任何候選回應 (可能觸發安全過濾)")
        return None

    except json.JSONDecodeError:
        log.error(f"JSON 解析失敗，原始文字: {content_text if 'content_text' in locals() else 'None'}")
        return None
    except Exception as e:
        log.warning(f"Gemini 執行異常: {str(e)}")
        return None


def _analyze_openai(text: str, artist_name: str, created_at: str = "") -> dict | None:
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       "gpt-4o-mini",
                "messages":    [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"歌手: {artist_name}" + (f"\n貼文發布時間: {created_at}" if created_at else "") + f"\n\n推文:\n{text[:2000]}"},
                ],
                "max_tokens":  300,
                "temperature": 0.1,
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"```(?:json)?", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        log.warning(f"OpenAI 分析失敗: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# 主監控流程
# ════════════════════════════════════════════════════════════════

def monitor_artist(artist: dict, prefer: str = "nitter") -> list[dict]:
    from database import get_connection, upsert_concert

    name   = artist["name"]
    handle = artist.get("twitter_handle")
    art_id = artist["id"]
    found  = []

    if not handle:
        log.info(f"[{name}] 無 Twitter handle，跳過")
        return []

    # 檢查資料庫：若已有未來場次且全部都有售票連結，跳過抓取
    import datetime as _dt_skip
    _today_str = _dt_skip.date.today().isoformat()
    try:
        _conn_skip = get_connection()
        _cur_skip  = _conn_skip.cursor()
        _cur_skip.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN ticket_url IS NOT NULL AND ticket_url != '' THEN 1 ELSE 0 END) as with_ticket
            FROM concerts
            WHERE artist_id = ? AND concert_date >= ?
        """, (art_id, _today_str))
        _row = _cur_skip.fetchone()
        _conn_skip.close()
        if _row and _row["total"] > 0 and _row["total"] == _row["with_ticket"]:
            log.info(f"[{name}] ✅ 已有 {_row['total']} 場完整資訊（日期+售票），跳過本次抓取")
            return []
    except Exception:
        pass

    tweets = fetch_tweets(
        handle, limit=40, prefer=prefer,
        artist_name=name, artist_en=artist.get("name_en", "")
    )

    _ticket_cache  = None   # 售票搜尋快取，同一歌手只跑一次
    _written_dates = set()  # 本次已寫入的場次，避免重複寫入

    for tw in tweets:
        text = tw.get("text", "")
        tw_url = tw.get("url", "")

        # RT 跳過（轉推不是官方公告）
        if text.strip().startswith("RT "):
            continue

        is_ddg = tw.get("source") == "ddg_supplement"

        if not is_ddg:
            if _is_untrusted_source(tw_url):
                log.info(f"[{name}] ⏭️  不可靠來源，跳過: {tw_url}")
                continue

            is_official_tweet = bool(handle and tw_url and handle.lower() in tw_url.lower())
            if not is_official_tweet:
                log.info(f"[{name}] ⏭️  非官方帳號推文，跳過: {tw_url}")
                continue

            if not passes_keyword_filter(text):
                continue
            # 官方推文（常為日文）不要求羅馬字歌手名出現在內文
        else:
            if not passes_keyword_filter(text):
                continue
            if not _artist_name_in_text(text, name, artist.get("name_en", "")):
                log.info(f"[{name}] ⏭️  DDG 結果未明確提及歌手，跳過: {tw_url}")
                continue

        log.info(f"[{name}] 🔍 關鍵字命中！{tw_url}")

        # 已掃描過且 AI 判定非演唱會的推文，直接跳過
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            SELECT ai_result, matched FROM monitor_log
            WHERE source_url = ? ORDER BY id DESC LIMIT 1
        """, (tw["url"],))
        _prev = cur.fetchone()
        conn.close()
        if _prev and _prev["matched"] == 0:
            log.info(f"[{name}] ⏭️  已掃過且非演唱會，跳過: {tw['url']}")
            continue

        result = analyze_with_ai(text, name, tw.get("created_at", ""))

        # 寫入 monitor_log
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO monitor_log
                (artist_id, platform, source_url, raw_content, ai_result, matched)
            VALUES (?, 'X (Twitter)', ?, ?, ?, ?)
        """, (
            art_id, tw["url"], text[:2000],
            json.dumps(result, ensure_ascii=False) if result else None,
            1 if result and result.get("is_concert") else 0,
        ))
        conn.commit()
        conn.close()

        if not result:
            log.warning(f"[{name}] ⚠️  AI 回傳 None（API 失敗或無可用 Key），跳過")
            continue

        log.info(f"[{name}] 🤖 AI結果: is_concert={result.get('is_concert')} date={result.get('date')} dates={result.get('dates')} ticket_url={result.get('ticket_url')!r}")

        if result.get("is_concert") in (True, "maybe"):
            # 支援多日期：dates 陣列優先，否則用單一 date
            dates = result.get("dates") or []
            if not dates and result.get("date"):
                dates = [result.get("date")]
            if not dates:
                dates = [None]  # 日期未定（後續若無售票連結則不寫入）

            # 過濾不完整的日期格式（如 2026-06-xx）
            import re as _re_date
            dates = [
                d if (d and _re_date.match(r"^\d{4}-\d{2}-\d{2}$", d)) else None
                for d in dates
            ]

            log.info(f"[{name}] 📅 AI日期列表: {dates}")

            # ── 無論如何都 Google 搜尋售票網址 ──────────────────────
            ai_venue      = result.get("venue")
            ai_event_name = result.get("event_name")
            ai_ticket_url = result.get("ticket_url")

            if _ticket_cache is not None:
                log.info(f"[{name}] 🎟️  使用快取售票搜尋結果")
                ticket_info = _ticket_cache
            else:
                log.info(f"[{name}] 🎟️  開始 Google 搜尋售票網址...")
                ticket_info = search_ticket_url(
                    artist_name=name,
                    artist_en=artist.get("name_en", ""),
                    concert_date=dates[0] if dates else None,
                    tweet_text=text,
                )
                if ticket_info.get("ticket_url"):
                    _ticket_cache = ticket_info
            final_ticket_url = ticket_info.get("ticket_url")
            google_event     = ticket_info.get("event_name")
            google_venue     = ticket_info.get("venue")
            sessions         = ticket_info.get("sessions") or []

            if final_ticket_url:
                log.info(f"[{name}] 🎟️  售票連結: {final_ticket_url}")
            else:
                log.info(f"[{name}] 🎟️  未找到售票連結")

            # ticket_url：Google 找到的優先 → 推文 AI 直接提供的（排除 t.co 短網址）
            if not final_ticket_url and ai_ticket_url:
                if "t.co" in ai_ticket_url or "bit.ly" in ai_ticket_url:
                    # 嘗試展開短網址
                    try:
                        _r = requests.head(ai_ticket_url, allow_redirects=True, timeout=5)
                        _expanded = _r.url
                        if "t.co" not in _expanded and "bit.ly" not in _expanded:
                            ai_ticket_url = _expanded
                            log.info(f"[{name}] 🔗 短網址展開: {ai_ticket_url}")
                        else:
                            log.info(f"[{name}] ⚠️  短網址無法展開，跳過: {ai_ticket_url}")
                            ai_ticket_url = None
                    except Exception:
                        log.info(f"[{name}] ⚠️  短網址展開失敗，跳過: {ai_ticket_url}")
                        ai_ticket_url = None
                if ai_ticket_url:
                    _news_domains = ["billboard", "natalie.mu", "barks.jp", "oricon",
                                     "realsound", "prtimes", "nikkei", "asahi", "yahoo",
                                     "livedoor", "ameblo", "note.com", "wikipedia",
                                     "musicman", "cdjournal",
                                     # 日本售票平台（只賣日本場次）
                                     "t.pia.jp", "pia.jp", "eplus.jp", "l-tike.com",
                                     "wess.jp", "smash-jpn.com", "creativeman.co.jp",
                                     # 香港售票平台（非台灣）
                                     "asiaworld-expo.kktix.cc", "urbtix.hk",
                                     # 二手票/非官方
                                     "stubhub", "viagogo", "seatgeek",
                                     # livehouse kktix（非主辦方票務）
                                     "emergelivehouse"]
                    _non_ticket_paths = ["/entry", "/news", "/blog",
                                         "/profile", "/about", "/top", "/index",
                                         "/article", "/post", "/column", "/interview"]
                    # /feature 只過濾純首頁（結尾是 /feature 或 /feature/）
                    import re as _re2
                    _is_feature_only = bool(_re2.search(r"/feature/?$", ai_ticket_url))
                    if any(d in ai_ticket_url for d in _news_domains) or                        any(p in ai_ticket_url for p in _non_ticket_paths) or                        _is_feature_only:
                        log.info(f"[{name}] ⚠️  新聞/非售票頁面，跳過: {ai_ticket_url}")
                        ai_ticket_url = None
                    else:
                        final_ticket_url = ai_ticket_url
                        log.info(f"[{name}] 🎟️  使用推文中的售票連結: {final_ticket_url}")

            # 活動名稱：Google 搜到的優先 → 推文 AI 的（排除 hashtag 和純日文長句）→ 預設
            if ai_event_name and ai_event_name.startswith("#"):
                ai_event_name = None
            # 純日文（無英文字母）視為摘要非 tour 名稱，一律跳過
            if ai_event_name:
                if not any(c.isascii() and c.isalpha() for c in ai_event_name):
                    log.info(f"[{name}] ⚠️  活動名稱無英文字，疑似日文摘要，跳過: {ai_event_name}")
                    ai_event_name = None
            final_event_name = google_event or ai_event_name or f"{name} 台灣公演"
            # 修正活動名稱中已知的空格問題（AI 有時會把藝人名的空格移除）
            for _fix_from, _fix_to in [
                ("ONEOKROCK", "ONE OK ROCK"),
                ("ONEOKTOCK", "ONE OK ROCK"),  # typo guard
            ]:
                if _fix_from in final_event_name:
                    final_event_name = final_event_name.replace(_fix_from, _fix_to)
            log.info(f"[{name}] 📌 最終活動名稱: {final_event_name}")

            # 場次組合邏輯：
            # 1. 推文 AI 有日期 → 優先用推文 AI 的日期（最可靠）
            # 2. 推文 AI 無日期 + 有售票連結 → 用 Google sessions 的日期
            # 3. 推文 AI 無日期 + 無售票連結 → 日期未定
            _tweet_ai_dates = [d for d in dates if d is not None]

            if _tweet_ai_dates:
                _google_sessions = ticket_info.get("sessions", [])
                _google_dates_with_url = [(s.get("date"), s.get("url")) for s in _google_sessions
                                          if s.get("date") and s.get("url")]
                # 如果 Google sessions 有找到更多台灣場次（包含推文 AI 的日期），用 Google 的
                _google_dates_only = [d for d, u in _google_dates_with_url]
                _tweet_in_google = any(d in _google_dates_only for d in _tweet_ai_dates)
                if final_ticket_url and _google_dates_with_url and _tweet_in_google:
                    log.info(f"[{name}] 📅 Google場次含推文日期，使用Google sessions: {_google_dates_only}")
                    sessions = _google_sessions
                else:
                    log.info(f"[{name}] 📅 使用推文AI日期: {_tweet_ai_dates}")
                    _google_urls = [s.get("url") for s in _google_sessions if s.get("url")]
                    sessions = []
                    for i, d in enumerate(_tweet_ai_dates):
                        sessions.append({"date": d, "venue": google_venue or ai_venue,
                                         "url": _google_urls[i] if i < len(_google_urls) else final_ticket_url})
            elif sessions:
                # 有 Google sessions 就用（不管有沒有售票連結）
                log.info(f"[{name}] 📅 使用 Google 場次資料（{len(sessions)} 場，售票: {bool(final_ticket_url)}）")
                for _s in sessions:
                    log.info(f"[{name}]     session: date={_s.get('date')} venue={_s.get('venue')} url={_s.get('url')}")
            else:
                log.info(f"[{name}] 📅 Google 無結果，使用推文 AI 場次資料（日期未定）")
                sessions = [{"date": d, "venue": google_venue or ai_venue, "url": final_ticket_url} for d in dates]

            for session in sessions:
                concert_date  = session.get("date")
                concert_url   = session.get("url") or final_ticket_url
                concert_venue = session.get("venue") or google_venue or ai_venue or "未定"

                if _is_past_concert_date(concert_date):
                    log.info(f"[{name}] ⏭️  跳過：演出日期已過 ({concert_date})")
                    continue

                # ★ 新增：日期和場地都未定 → 資訊沒有實質內容，不管有沒有 URL 都跳過
                if concert_date is None and concert_venue in (None, "未定"):
                    log.info(f"[{name}] ⏭️  跳過：日期和場地都未定，資訊不足以顯示")
                    continue

                # 日期未定且沒有售票連結 → 資訊太不完整，跳過不寫入
                if concert_date is None and not concert_url:
                    log.info(f"[{name}] ⏭️  跳過：日期未定且無售票連結，資訊不足")
                    continue

                # maybe 等級需有售票連結或明確日期才寫入
                if result.get("is_concert") == "maybe" and not concert_url and not concert_date:
                    log.info(f"[{name}] ⏭️  跳過：AI 信心不足且無售票/日期")
                    continue

                # ★ 改進：日期合理性驗證 —— 不管有沒有售票連結都要檢查場地
                #    （原本只在「有售票連結」時檢查，導致外國場地漏放行）
                if concert_date:  # 只要有日期，就要驗證場地不是外國
                    if _is_foreign_venue(concert_venue):
                        log.info(f"[{name}] ⏭️  跳過：場地 {concert_venue} 非台灣場次")
                        continue

                # 同場次已寫入過，跳過
                _date_key = f"{concert_date}_{concert_venue}"
                if _date_key in _written_dates:
                    log.info(f"[{name}] ⏭️  本次已寫入，跳過: {concert_date}")
                    continue
                _written_dates.add(_date_key)
                log.info(f"[{name}] 🎫 寫入場次: {concert_date} @ {concert_venue}  {concert_url}")
                concert = dict(
                    artist_id=art_id,
                    event_name=final_event_name,
                    venue=concert_venue,
                    concert_date=concert_date,
                    ticket_url=concert_url,
                    ticket_status="on_sale" if concert_url else ("announced" if result.get("is_concert") is True else "rumor"),
                    is_confirmed=1 if result.get("is_concert") is True else 0,
                    ai_confidence=result.get("confidence", 0.5),
                    source_url=tw["url"], source_text=text[:500],
                    source_platform="X (Twitter)",
                    notes=result.get("notes", ""),
                )
                upsert_concert(**concert)
                found.append(concert)
                log.info(f"[{name}] ✅ 發現演唱會！{concert_date} @ {concert_venue}  售票:{concert_url}")

    # Nitter + DDG 推文都無結果時，最後嘗試 DDG 售票平台直搜
    if not found:
        log.info(f"[{name}] 🔎 嘗試 DDG 售票平台直搜 fallback...")
        ticket_info = search_ticket_url(
            artist_name=name,
            artist_en=artist.get("name_en", ""),
        )
        status = ticket_info.get("status")
        ticket_url = ticket_info.get("ticket_url")
        sessions = ticket_info.get("sessions") or []

        # ★ 用 status 明確分流，不再靠 ticket_url 是否為 None 反推發生了什麼事
        if status == TicketSearchStatus.SEARCH_ERROR.value:
            log.warning(f"[{name}] ⚠️  售票搜尋機制出錯（{ticket_info.get('error')}），跳過本次 fallback")
        elif status not in (TicketSearchStatus.TICKET_FOUND.value, TicketSearchStatus.DATE_ONLY.value):
            log.info(f"[{name}] ⏭️  DDG 直搜無結果，跳過")
        else:
            has_ticket = status == TicketSearchStatus.TICKET_FOUND.value
            for session in sessions:
                _cd = session.get("date")
                _cu = session.get("url") or (ticket_url if has_ticket else None)
                _cv = session.get("venue") or ticket_info.get("venue") or "未定"
                if _is_past_concert_date(_cd):
                    continue
                if not _cd and not _cu:
                    continue
                # ★ 這條 fallback 路徑之前完全沒做外國場地檢查，
                #   導致「大阪城展演廳」這類日本場地被當成台灣場次寫入
                if _is_foreign_venue(_cv):
                    log.info(f"[{name}] ⏭️  跳過：場地 {_cv} 非台灣場次（DDG fallback）")
                    continue
                concert = dict(
                    artist_id=art_id,
                    event_name=ticket_info.get("event_name") or f"{name} 台灣公演",
                    venue=_cv,
                    concert_date=_cd,
                    ticket_url=_cu,
                    ticket_status="on_sale" if _cu else "announced",
                    is_confirmed=1,
                    ai_confidence=0.85 if _cu else 0.6,
                    source_url=_cu or "",
                    source_text="DDG 售票平台搜尋" if _cu else "新聞搜尋（尚無售票連結）",
                    source_platform="DDG Search" if _cu else "新聞搜尋",
                    notes="售票平台直搜" if _cu else "從新聞找到日期，尚無售票連結",
                )
                upsert_concert(**concert)
                found.append(concert)
                log.info(f"[{name}] ✅ DDG 直搜寫入！{_cd} @ {_cv}  {_cu}")

    return found


def run_all_monitors(prefer: str = "nitter"):
    from database import get_all_artists
    import random
    artists = get_all_artists()
    log.info(f"▶️  掃描 {len(artists)} 位歌手（方式: {prefer}）...")
    total = 0
    for i, artist in enumerate(artists):
        try:
            found = monitor_artist(artist, prefer=prefer)
            total += len(found)
            time.sleep(2)
        except Exception as e:
            log.error(f"[{artist['name']}] 錯誤: {e}")
    log.info(f"✅ 完成，共發現 {total} 筆新資訊")
    return total


# ════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from database import init_db

    parser = argparse.ArgumentParser(
        description="JP Concert Tracker",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--daemon",     action="store_true", help="背景每隔 interval 分鐘掃描")
    parser.add_argument("--interval",   type=int, default=120, help="背景掃描間隔（分鐘）")
    parser.add_argument("--prefer",     choices=["nitter", "bearer", "google"], default="nitter")
    parser.add_argument("--artist",     type=str, help="只掃描指定歌手（模糊比對名稱）")
    parser.add_argument("--add-artist", type=str, dest="add_artist",
                        help="新增歌手，格式: 名稱|handle|name_en|url")

    parser.add_argument("--list-artists", action="store_true", dest="list_artists",
                        help="列出所有已追蹤歌手")
    args = parser.parse_args()

    init_db()

    # ── 列出歌手 ──────────────────────────────────────────────────
    if args.list_artists:
        from database import get_artists_with_concert_status
        artists = get_artists_with_concert_status()
        print(f"\n{'ID':>4}  {'名稱':<20}  {'Twitter Handle':<25}  {'演唱會筆數':>8}")
        print("-" * 70)
        for a in artists:
            print(f"{a['id']:>4}  {a['name']:<20}  {(a['twitter_handle'] or '-'):<25}  {a['concert_count']:>8}")
        print(f"\n共 {len(artists)} 位歌手")
        sys.exit(0)

    # ── 新增歌手 ──────────────────────────────────────────────────
    if args.add_artist:
        from database import add_artist, get_artist_by_name
        parts = [p.strip() for p in args.add_artist.split("|")]
        if len(parts) < 1:
            print("❌ 格式錯誤，至少需要歌手名稱")
            sys.exit(1)
        a_name    = parts[0]
        a_handle  = parts[1] if len(parts) > 1 else None
        a_en      = parts[2] if len(parts) > 2 else None
        a_url     = parts[3] if len(parts) > 3 else None
        a_genre   = parts[4] if len(parts) > 4 else None

        added = add_artist(
            name=a_name, name_jp=a_name, name_en=a_en,
            twitter_handle=a_handle, official_url=a_url, genre=a_genre
        )
        if added:
            print(f"✅ 新增歌手：{a_name}（Twitter: {a_handle or '-'}）")
            # 立即掃描這位歌手
            if a_handle:
                ans = input("是否立即掃描此歌手？(y/N) ").strip().lower()
                if ans == "y":
                    artist = get_artist_by_name(a_name)
                    if artist:
                        monitor_artist(artist, prefer=args.prefer)
        else:
            print(f"⚠️  歌手已存在：{a_name}")
        sys.exit(0)

    # ── 掃描指定歌手 ──────────────────────────────────────────────
    if args.artist:
        from database import get_all_artists
        targets = [a for a in get_all_artists() if args.artist.lower() in a["name"].lower()]
        if not targets:
            print(f"❌ 找不到歌手：{args.artist}，可用 --list-artists 查看所有歌手")
        else:
            for a in targets:
                monitor_artist(a, prefer=args.prefer)

    elif args.daemon:
        log.info(f"🚀 背景模式啟動（每 {args.interval} 分鐘，方式: {args.prefer}）")
        while True:
            run_all_monitors(prefer=args.prefer)
            time.sleep(args.interval * 60)

    else:
        run_all_monitors(prefer=args.prefer)