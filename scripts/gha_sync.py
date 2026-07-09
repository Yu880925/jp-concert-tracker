"""
GitHub Actions 用：與 Render 同步 concerts.db
  python scripts/gha_sync.py wake
  python scripts/gha_sync.py download
  python scripts/gha_sync.py upload
"""
import os
import sys
import time
import requests
from pathlib import Path

RENDER_URL = os.getenv("RENDER_URL", "").strip().rstrip("/")
SCAN_API_KEY = os.getenv("SCAN_API_KEY", "").strip()
DB_PATH = Path(os.getenv("DB_PATH", "concerts.db"))

MAX_RETRIES = 5
RETRY_WAIT = 15  # 秒（Render 免費版喚醒約需 30–60 秒）


def _headers():
    return {"X-API-Key": SCAN_API_KEY}


def _check_config():
    if not RENDER_URL:
        print("❌ RENDER_URL 未設定（GitHub Secrets）")
        sys.exit(1)
    if not SCAN_API_KEY:
        print("❌ SCAN_API_KEY 未設定（GitHub Secrets，需與 Render 相同）")
        sys.exit(1)
    print(f"✓ RENDER_URL = {RENDER_URL}")


def wake():
    """喚醒 Render 免費版服務（休眠時第一次請求很慢）。"""
    _check_config()
    url = f"{RENDER_URL}/api/stats"
    print(f"⏰ 喚醒 Render: {url}")
    for i in range(MAX_RETRIES):
        try:
            r = requests.get(url, timeout=90)
            if r.status_code == 200:
                print(f"✅ Render 已就緒（第 {i + 1} 次嘗試）")
                return
            print(f"⚠️  HTTP {r.status_code}，{RETRY_WAIT}s 後重試...")
        except requests.RequestException as e:
            print(f"⚠️  連線失敗: {e}，{RETRY_WAIT}s 後重試...")
        if i < MAX_RETRIES - 1:
            time.sleep(RETRY_WAIT)
    print("❌ 無法喚醒 Render，請確認網址正確且服務已部署")
    sys.exit(1)


def download():
    _check_config()
    wake()

    url = f"{RENDER_URL}/api/export-db"
    print(f"⬇️  下載資料庫: {url}")

    for i in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=_headers(), timeout=90)
            if r.status_code == 401:
                print("❌ 401 Unauthorized：SCAN_API_KEY 與 Render 不一致")
                sys.exit(1)
            if r.status_code == 404:
                print("⚠️  遠端尚無資料庫，將在本機新建")
                return
            r.raise_for_status()
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            DB_PATH.write_bytes(r.content)
            print(f"✅ 已儲存至 {DB_PATH}（{len(r.content)} bytes）")
            return
        except requests.RequestException as e:
            print(f"⚠️  下載失敗（{i + 1}/{MAX_RETRIES}）: {e}")
            if i < MAX_RETRIES - 1:
                time.sleep(RETRY_WAIT)

    print("❌ 下載失敗（非「尚無資料庫」的404，是真正的連線/伺服器錯誤）")
    print("   → 為了避免用空白資料庫覆蓋 Render 現有資料，中止本次流程")
    print("   → 不會繼續執行 monitor 掃描與上傳")
    sys.exit(1)


def upload():
    _check_config()
    if not DB_PATH.exists():
        print(f"❌ 找不到 {DB_PATH}")
        sys.exit(1)

    wake()

    url = f"{RENDER_URL}/api/sync-db"
    size = DB_PATH.stat().st_size
    print(f"⬆️  上傳資料庫: {url}（{size} bytes）")

    for i in range(MAX_RETRIES):
        try:
            with open(DB_PATH, "rb") as f:
                r = requests.post(
                    url,
                    headers=_headers(),
                    files={"file": ("concerts.db", f, "application/octet-stream")},
                    timeout=120,
                )
            if r.status_code == 401:
                print("❌ 401 Unauthorized：SCAN_API_KEY 與 Render 不一致")
                print("   → 請確認 GitHub Secrets 與 Render 環境變數的 SCAN_API_KEY 完全相同")
                sys.exit(1)
            if r.status_code >= 400:
                print(f"❌ HTTP {r.status_code}: {r.text[:300]}")
                raise requests.HTTPError(r.text)
            print(f"✅ 上傳成功: {r.json()}")
            return
        except requests.RequestException as e:
            print(f"⚠️  上傳失敗（{i + 1}/{MAX_RETRIES}）: {e}")
            if i < MAX_RETRIES - 1:
                time.sleep(RETRY_WAIT)

    print("❌ 上傳失敗，請檢查 Render 日誌與 SCAN_API_KEY")
    sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "wake":
        wake()
    elif cmd == "download":
        download()
    elif cmd == "upload":
        upload()
    else:
        print("用法: python scripts/gha_sync.py [wake|download|upload]")
        sys.exit(1)
