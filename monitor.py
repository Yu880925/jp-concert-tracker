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
                    timeout=10,
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
                timeout=10,
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
    "kktix.com", "kktix.cc",  # kktix.cc 包含台灣子域名如 kklivetw.kktix.cc
    "ticketplus.com.tw",
    "indievox.com",
    "ibon.com.tw",
    "famiticket.com",
    "ticket.com.tw",
    "cityline.com.tw",
    "accupass.com",
    "ticketmaster.com.tw",
    "tixcraft.com",
    "urbtix.hk",
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
                timeout=10,
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
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning(f"[售票搜尋] AI清理名稱失敗: {e}")
    return None


def _extract_concert_info_with_ai(text: str, artist_name: str, today: str) -> dict:
    """
    從搜尋結果合併文字中，用 AI 萃取演唱會日期和地點。
    只回傳未來的場次，避免抓到舊演唱會。
    """
    prompt = (
        "以下搜尋結果文字中可能包含多個不同歌手的演唱會資訊。\n"
        "今天日期是 " + today + "。\n\n"
        "你的任務：只找出【" + artist_name + "】在台灣/台北的演唱會日期和地點。\n\n"
        "嚴格規則：\n"
        "- 只抓明確屬於【" + artist_name + "】的場次，其他歌手的日期一律忽略\n"
        "- 只抓台灣或台北的場次，忽略首爾、香港、新加坡、東京等其他城市\n"
        "- 只回傳今天之後的未來場次，忽略過去的演唱會\n"
        "- 如果找不到【" + artist_name + "】的台灣明確未來場次，dates 回傳空陣列\n"
        "- 日期格式統一為 YYYY-MM-DD\n"
        "- event_name 填寫 tour/活動的正式名稱（如有），沒有就填 null\n"
        "- 只回傳 JSON，不要其他說明\n\n"
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


class TicketFilter:
    """
    售票連結過濾器：採用權重評分制，減少對 URL 年份字串的依賴。
    """
    def __init__(self, target_years: List[str], artist_name: str):
        self.target_years = target_years
        self.artist_name = artist_name
        # 排除 2010 到目標年份前一年的資料 (防止抓到過期資訊)
        self.exclude_years = [str(y) for y in range(2010, int(target_years[0]))]
        self.essential_keywords = ["台北", "Taipei", "小巨蛋", "演唱會", "Concert", "Ticket", "售票", "KKTIX", "TicketPlus", "遠大"]

    def evaluate_link(self, url: str, title: str, snippet: str, from_query: str) -> bool:
        full_text = f"{title} {snippet} {url}".lower()
        import re as _re
        # 1. 嚴格排除：若內容顯式包含過去年份
        if any(_re.search(rf"\b{yr}\b", full_text) for yr in self.exclude_years):
            return False

        # ★ 新增：排除「總整理／全攻略」類懶人包文章 —— 這類頁面常混雜多位歌手，
        #    導致某一個歌手的連結被誤判成另一個歌手的售票頁
        _aggregator_keywords = ["總整理", "全攻略", "攻略", "特企", "懶人包", "彙整", "整理包"]
        if any(kw in title for kw in _aggregator_keywords):
            return False

        # ★ 新增：歌手名字必須出現在「標題」裡才算數（不看 snippet/body）
        #    因為 body 常常是搜尋引擎自動摘要，會把頁面裡其他歌手的名字也帶進來
        clean_title = title.lower().replace(" ", "")
        clean_name  = self.artist_name.lower().replace(" ", "")
        if clean_name not in clean_title:
            return False

        score = 0
        # 2. 基礎信心分：若標題含有目標年份或來自包含年份的 Query
        if any(yr in full_text for yr in self.target_years):
            score += 50
        if any(yr in from_query for yr in self.target_years):
            score += 20

        # 3. 關鍵字加分（名字已經在上面強制檢查過，這裡分數可以拿掉或保留當輔助分）
        clean_text = full_text.replace(" ", "")
        if clean_name in clean_text:
            score += 40

        # 4. 地域與售票關鍵字
        keyword_hits = sum(1 for kw in self.essential_keywords if kw.lower() in full_text)
        score += min(keyword_hits * 10, 40)

        # 5. 特殊規則：LiSA 15週年巡演語義補償
        if "15" in title and ("smile always" in title.lower() or "lisa" in title.lower()):
            score += 40

        return score >= 60

import random

SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.inetol.net",
    "https://priv.au",
    "https://searx.tiekoetter.com",
    "https://baresearch.org",
]

def _searxng_search(query: str, max_results: int = 10) -> list[dict]:
    """用公開 SearXNG 實例搜尋，回傳格式對齊 ddgs 的 [{'title','href','body'}]"""
    instances = SEARXNG_INSTANCES[:]
    random.shuffle(instances)  # 每次打亂順序，分散流量避免固定實例被鎖
    for base in instances:
        try:
            r = requests.get(
                f"{base}/search",
                params={"q": query, "format": "json"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            results = []
            for item in data.get("results", [])[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "href":  item.get("url", ""),
                    "body":  item.get("content", ""),
                })
            if results:
                log.info(f"[售票搜尋] SearXNG({base}) 取得 {len(results)} 筆")
                return results
        except Exception as e:
            log.warning(f"[售票搜尋] SearXNG {base} 失敗: {e}")
            continue
    return []

def search_ticket_url(artist_name: str, artist_en: str = "", concert_date: str = None, tweet_text: str = "") -> dict:
    """
    整合 TicketFilter 權重機制的售票搜尋。
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        log.error("[售票搜尋] 找不到 ddgs")
        return {"ticket_url": None, "found_date": None, "event_name": None, "venue": None, "sessions": []}

    import datetime as _dt

    _yr    = _dt.date.today().year
    _today = _dt.date.today().isoformat()
    
    # 初始化權重過濾器 (目標今年與明年)
    ticket_filter = TicketFilter(target_years=[str(_yr), str(_yr+1)], artist_name=artist_name)

    # ... (保留你原本的 _find_dates, _find_venue 輔助函數) ...

    _jp_hint = "日本" if artist_en else ""
    queries = [
        f"{_jp_hint}{artist_name} 台灣演唱會 {_yr} 售票",
        f"{_jp_hint}{artist_name} 台灣演唱會 {_yr+1} 售票",
        f"{artist_name} kktix OR ticketplus 台灣",
    ]

    best_url = None
    event_name = None
    winning_results = []

    for query in queries:
        try:
            log.info(f"[售票搜尋] 搜尋: {query}")
            results = _searxng_search(query, max_results=10)
            if not results:
                # SearXNG 全部失效才退回 ddgs 當最後手段
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(query, max_results=10))
                except Exception as e:
                    log.warning(f"[售票搜尋] ddgs 也失敗: {e}")
                    results = []
            
            for r in results:
                if any(domain in r["href"] for domain in TICKET_DOMAINS):
                    is_valid = ticket_filter.evaluate_link(
                        url=r["href"],
                        title=r.get("title", ""),
                        snippet=r.get("body", ""),
                        from_query=query
                    )
                    if is_valid:
                        best_url = r["href"]
                        event_name = r["title"]
                        log.info(f"[售票搜尋] ✅ 評分通過，確定售票連結: {best_url}")
                        break
                    else:
                        log.info(f"[售票搜尋] ⏭️  評分不足，跳過連結: {r['href']}（title: {r.get('title','')[:50]}）")

            if best_url:
                winning_results = results
                break
            time.sleep(1.5)
        except Exception as e:
            log.warning(f"[售票搜尋] 失敗 [{query}]: {e}")

    if not best_url:
        # 找不到售票平台，但嘗試從新聞/資訊網站抓日期地點
        log.info("[售票搜尋] 未找到售票平台，嘗試從搜尋結果抓日期地點...")
        _all_news_results = []
        for query in queries[:2]:  # 只用前兩個 query 的結果
            try:
                with DDGS() as ddgs:
                    _nr = list(ddgs.text(query, max_results=8))
                _all_news_results.extend(_nr)
                time.sleep(1)
            except Exception:
                pass
        if _all_news_results:
            _news_text = " ".join(r.get("title","") + " " + r.get("body","") for r in _all_news_results)
            if tweet_text:
                _news_text = "[原始推文]\n" + tweet_text + "\n\n[搜尋結果]\n" + _news_text
            _news_ai    = _extract_concert_info_with_ai(_news_text, artist_name, _today)
            _news_dates = _news_ai.get("dates") or []
            _news_venue = _news_ai.get("venue")
            _news_event = _news_ai.get("event_name")
            if _news_dates:
                log.info(f"[售票搜尋] 📅 從新聞找到日期: {_news_dates}（無售票連結）")
                _news_sessions = [{"date": d, "venue": _news_venue, "url": None} for d in _news_dates]
                return {
                    "ticket_url": None,
                    "found_date": _news_dates[0],
                    "event_name": _news_event,
                    "venue":      _news_venue,
                    "sessions":   _news_sessions,
                }





        return {"ticket_url": None, "found_date": None, "event_name": None, "venue": None, "sessions": []}

    # ── 合併文字：直接用找到售票平台那批結果（已含日期地點資訊）──
    info_results = winning_results[:]

    # 找出 best_url 對應的那一筆，優先使用
    _primary = next((r for r in info_results if r["href"] == best_url), None)
    if _primary:
        combined_text = _primary.get("title", "") + " " + _primary.get("body", "")
    else:
        combined_text = " ".join(r.get("title", "") + " " + r.get("body", "") for r in info_results)
    # 把原始推文加在最前面，讓 AI 優先參考（資訊最新最準確）
    if tweet_text:
        if tweet_text:
            combined_text = "[原始推文]\n" + tweet_text + "\n\n[搜尋結果]\n" + combined_text



    log.info(f"[售票搜尋] 合併文字({len(combined_text)}字): {combined_text[:500]}")

    # ── 交給 AI 判斷日期和地點 ──────────────────────────────────────
    ai_info = _extract_concert_info_with_ai(combined_text, artist_name, _today)
    all_dates      = ai_info.get("dates") or []
    venue          = ai_info.get("venue")
    ai_event_name  = ai_info.get("event_name")
    log.info(f"[售票搜尋] 📅 AI找到日期: {all_dates}")
    log.info(f"[售票搜尋] 📍 AI找到地點: {venue}")
    if ai_event_name:
        log.info(f"[售票搜尋] 📌 AI找到活動名稱: {ai_event_name}")

    # ── 活動名稱：優先清理 DDG title（最準確），AI 萃取的只做補充 ─
    time.sleep(2)
    clean = _clean_event_name_with_ai(event_name, artist_name)
    if clean:
        log.info(f"[售票搜尋] 📌 AI清理名稱: {clean}")
        event_name = clean
    elif ai_event_name:
        event_name = ai_event_name
        log.info(f"[售票搜尋] 📌 使用AI萃取名稱: {event_name}")

    # ── fetch 頁面（kktix等非JS站才有用）找其他場次連結 ────────────
    session_links = []
    import re as _re
    import urllib.parse as _up
    try:
        rp = requests.get(best_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-TW,zh;q=0.9"
        }, timeout=12)
        if rp.status_code == 200:
            html  = rp.text
            is_js = "JavaScript enabled" in html or len(html.strip()) < 500
            if not is_js:
                _base   = _up.urlparse(best_url)
                _origin = f"{_base.scheme}://{_base.netloc}"
                plain   = _re.sub(r"<[^>]+>", " ", html)
                plain   = _re.sub(r"\s+", " ", plain)
                # 已有日期時不再呼叫 AI（省時間），只在沒有日期或地點時才分析
                if not all_dates or not venue:
                    time.sleep(2)
                    page_ai = _extract_concert_info_with_ai(plain, artist_name, _today)
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
                    log.info(f"[售票搜尋] 📅 已有日期地點，跳過頁面AI分析")
                # 其他場次連結
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

    # ── 組合 sessions ───────────────────────────────────────────────
    # 先用 session_links（從頁面抓的）對應日期
    all_urls = [best_url] + session_links

    sessions = []
    if not all_dates:
        sessions = [{"date": None, "venue": venue, "url": best_url}]
    elif len(all_dates) == 1:
        sessions = [{"date": all_dates[0], "venue": venue, "url": best_url}]
    else:
        for i, d in enumerate(all_dates):
            sessions.append({"date": d, "venue": venue,
                             "url": all_urls[i] if i < len(all_urls) else best_url})

    # ── 多場次時，針對每個日期補搜尋獨立售票連結 ──────────────────
    if len(sessions) > 1:
        log.info(f"[售票搜尋] 多場次，補搜尋各場次獨立售票連結...")
        _base_netloc = _up.urlparse(best_url).netloc
        for i, session in enumerate(sessions):
            d = session["date"]
            if not d:
                continue
            # 已有獨立 URL（不同於 best_url）就跳過
            if session["url"] != best_url:
                continue
            if i == 0:
                continue  # 第一場直接用 best_url，不需要補搜
            try:
                import datetime as _dt_s
                _cur_yr = _dt_s.date.today().year
                _month_day = f"{int(d[5:7])}/{int(d[8:10])}"
                # 用活動名稱 + 日期搜尋，比只用歌手名更精確
                _event_hint = event_name or artist_name
                _search_q   = f"{_event_hint} {_month_day} {_base_netloc.split('.')[0]}"
                log.info(f"[售票搜尋] 補搜場次連結: {_search_q}")
                with DDGS() as ddgs:
                    _sr = list(ddgs.text(_search_q, max_results=8))
                for r in _sr:
                    if _base_netloc in r["href"] and r["href"] != best_url:
                        _rb = (r.get("title","") + " " + r.get("body",""))
                        _old_years = [str(y) for y in range(2020, _cur_yr)]
                        _has_old = any(yr in _rb for yr in _old_years)
                        _has_new = str(_cur_yr) in _rb or str(_cur_yr+1) in _rb
                        if _has_old and not _has_new:
                            log.info(f"[售票搜尋] ⚠️  跳過舊年份連結: {r['href']}")
                            continue
                        sessions[i]["url"] = r["href"]
                        log.info(f"[售票搜尋] 🔗 {d} 找到獨立連結: {r['href']}")
                        break
                time.sleep(1)
            except Exception as e:
                log.warning(f"[售票搜尋] 補搜場次連結失敗: {e}")

    for s in sessions:
        log.info(f"[售票搜尋] 🎫 {s['date']} @ {s['venue']}  {s['url']}")

    return {
        "ticket_url": best_url,
        "found_date": sessions[0]["date"] if sessions else None,
        "event_name": event_name,
        "venue":      venue,
        "sessions":   sessions,
    }


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
            with DDGS() as ddgs:
                results = list(ddgs.text(sq, max_results=8))
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
            time.sleep(1)
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

                # 日期未定且沒有售票連結 → 資訊太不完整，跳過不寫入
                if concert_date is None and not concert_url:
                    log.info(f"[{name}] ⏭️  跳過：日期未定且無售票連結，資訊不足")
                    continue

                # maybe 等級需有售票連結或明確日期才寫入
                if result.get("is_concert") == "maybe" and not concert_url and not concert_date:
                    log.info(f"[{name}] ⏭️  跳過：AI 信心不足且無售票/日期")
                    continue

                # 日期合理性驗證：售票連結有效時，確認日期是台北場次（非其他城市）
                # 無售票連結時（新聞找到的日期），直接信任 AI 已過濾的結果
                if concert_date and final_ticket_url:
                    # 有售票連結：驗證 venue 不是明顯的外國場地
                    _foreign_venues = ["zepp tokyo", "zepp osaka", "zepp fukuoka",
                                       "kspo dome", "asiaworld-expo", "hong kong",
                                       "singapore", "bangkok", "manila", "seoul",
                                       "tokyo dome", "osaka", "fukuoka", "sapporo"]
                    _venue_lower = (concert_venue or "").lower()
                    if any(fv in _venue_lower for fv in _foreign_venues):
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
        ticket_url = ticket_info.get("ticket_url")
        if ticket_url and any(d in ticket_url for d in TICKET_DOMAINS):
            sessions = ticket_info.get("sessions") or []
            if not sessions:
                sessions = [{
                    "date": ticket_info.get("found_date"),
                    "venue": ticket_info.get("venue"),
                    "url": ticket_url,
                }]
            for session in sessions:
                _cd = session.get("date")
                _cu = session.get("url") or ticket_url
                _cv = session.get("venue") or ticket_info.get("venue") or "未定"
                if _is_past_concert_date(_cd):
                    continue
                if not _cd and not _cu:
                    continue
                concert = dict(
                    artist_id=art_id,
                    event_name=ticket_info.get("event_name") or f"{name} 台灣公演",
                    venue=_cv,
                    concert_date=_cd,
                    ticket_url=_cu,
                    ticket_status="on_sale",
                    is_confirmed=1,
                    ai_confidence=0.85,
                    source_url=_cu,
                    source_text="DDG 售票平台搜尋",
                    source_platform="DDG Search",
                    notes="售票平台直搜",
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