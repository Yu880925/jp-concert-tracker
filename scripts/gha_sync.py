"""
GitHub Actions 用：與 Render 同步 concerts.db
  python scripts/gha_sync.py download
  python scripts/gha_sync.py upload
"""
import os
import sys
import requests
from pathlib import Path

RENDER_URL = os.getenv("RENDER_URL", "").rstrip("/")
SCAN_API_KEY = os.getenv("SCAN_API_KEY", "")
DB_PATH = Path(os.getenv("DB_PATH", "concerts.db"))


def _headers():
    return {"X-API-Key": SCAN_API_KEY}


def download():
    if not RENDER_URL or not SCAN_API_KEY:
        print("❌ 請設定 RENDER_URL 和 SCAN_API_KEY")
        sys.exit(1)

    url = f"{RENDER_URL}/api/export-db"
    print(f"⬇️  下載資料庫: {url}")
    r = requests.get(url, headers=_headers(), timeout=60)
    if r.status_code == 404:
        print("⚠️  遠端尚無資料庫，將在本機新建")
        return
    r.raise_for_status()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(r.content)
    print(f"✅ 已儲存至 {DB_PATH}（{len(r.content)} bytes）")


def upload():
    if not RENDER_URL or not SCAN_API_KEY:
        print("❌ 請設定 RENDER_URL 和 SCAN_API_KEY")
        sys.exit(1)
    if not DB_PATH.exists():
        print(f"❌ 找不到 {DB_PATH}")
        sys.exit(1)

    url = f"{RENDER_URL}/api/sync-db"
    print(f"⬆️  上傳資料庫: {url}")
    with open(DB_PATH, "rb") as f:
        r = requests.post(
            url,
            headers=_headers(),
            files={"file": ("concerts.db", f, "application/octet-stream")},
            timeout=120,
        )
    r.raise_for_status()
    print(f"✅ 上傳成功: {r.json()}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "download":
        download()
    elif cmd == "upload":
        upload()
    else:
        print("用法: python scripts/gha_sync.py [download|upload]")
        sys.exit(1)
