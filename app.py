"""
app.py - Flask Web Server for JP Concert Tracker
Run: python app.py
Prod: gunicorn app:app --bind 0.0.0.0:$PORT
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, request, send_file

import sys
sys.path.insert(0, str(Path(__file__).parent))

from database import (
    DB_PATH,
    init_db,
    get_all_artists,
    get_concerts_by_artists,
    get_artists_with_concert_status,
)

# ─── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SCAN_API_KEY = os.getenv("SCAN_API_KEY", "").strip()
ENABLE_PUBLIC_SCAN = os.getenv("ENABLE_PUBLIC_SCAN", "false").lower() == "true"

HTML_PATH = Path(__file__).parent / "templates" / "index.html"


def _check_api_key() -> bool:
    if not SCAN_API_KEY:
        return True
    key = (request.headers.get("X-API-Key") or request.args.get("api_key") or "").strip()
    return key == SCAN_API_KEY


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _check_api_key():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# 啟動時初始化資料庫（Render 首次部署會建立歌手清單）
init_db()
log.info(f"Database path: {DB_PATH}")


# ─── API Routes ────────────────────────────────────────────────────────────────
@app.route("/api/artists")
def api_artists():
    artists = get_artists_with_concert_status()
    return jsonify(artists)


@app.route("/api/concerts")
def api_concerts():
    ids_param = request.args.get("ids", "")
    if not ids_param:
        return jsonify([])
    try:
        artist_ids = [int(x) for x in ids_param.split(",") if x.strip()]
    except ValueError:
        return jsonify({"error": "Invalid ids parameter"}), 400

    concerts = get_concerts_by_artists(artist_ids)
    for c in concerts:
        c["status_label"] = _status_label(c.get("ticket_status"))
        c["status_class"] = _status_class(c.get("ticket_status"))
        c["is_confirmed_label"] = "已確認" if c.get("is_confirmed") else "未確認"
        if c.get("concert_date"):
            try:
                dt = datetime.strptime(c["concert_date"], "%Y-%m-%d")
                c["date_display"] = dt.strftime("%Y年%m月%d日")
                c["date_weekday"] = ["週一","週二","週三","週四","週五","週六","週日"][dt.weekday()]
            except Exception:
                c["date_display"] = c["concert_date"]
                c["date_weekday"] = ""
        else:
            c["date_display"] = "日期未定"
            c["date_weekday"] = ""
    return jsonify(concerts)


@app.route("/api/config")
def api_config():
    return jsonify({
        "enable_scan_button": ENABLE_PUBLIC_SCAN,
    })


@app.route("/api/trigger-scan", methods=["POST"])
@require_api_key
def api_trigger_scan():
    if not ENABLE_PUBLIC_SCAN:
        return jsonify({"status": "error", "message": "公開掃描已關閉，請使用 GitHub Actions 觸發"}), 403
    try:
        from monitor import run_all_monitors
        import threading
        t = threading.Thread(target=run_all_monitors, daemon=True)
        t.start()
        return jsonify({"status": "scan_started", "message": "監控掃描已啟動，結果將自動更新"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/export-db", methods=["GET"])
@require_api_key
def api_export_db():
    """GitHub Actions 下載目前資料庫。"""
    if not DB_PATH.exists():
        init_db()
    return send_file(DB_PATH, mimetype="application/octet-stream", download_name="concerts.db")


@app.route("/api/sync-db", methods=["POST"])
@require_api_key
def api_sync_db():
    """GitHub Actions 掃描完成後上傳更新後的資料庫。"""
    if "file" not in request.files:
        return jsonify({"error": "Missing file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    f.save(DB_PATH)
    log.info(f"Database synced from GitHub Actions → {DB_PATH}")
    return jsonify({"status": "ok", "message": "Database updated"})


@app.route("/api/stats")
def api_stats():
    artists = get_all_artists()
    from database import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total FROM concerts")
    total_concerts = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) as c FROM concerts WHERE ticket_status = 'on_sale'")
    on_sale = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) as c FROM concerts")
    announced = cur.fetchone()["c"]
    cur.execute("SELECT MAX(updated_at) as last FROM concerts")
    last_update = cur.fetchone()["last"]
    conn.close()
    return jsonify({
        "total_artists":   len(artists),
        "total_concerts":  total_concerts,
        "on_sale":         on_sale,
        "announced":       announced,
        "rumors":          0,
        "last_updated":    last_update,
    })


@app.route("/")
def index():
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


def _status_label(status: str) -> str:
    return {
        "on_sale":   "🎟️ 售票中",
        "pre_sale":  "🎫 預售中",
        "announced": "📢 已宣布",
        "rumor":     "🔍 疑似消息",
        "unknown":   "❓ 未知",
        None:        "❓ 未知",
    }.get(status, "❓ 未知")


def _status_class(status: str) -> str:
    return {
        "on_sale":   "status-on-sale",
        "pre_sale":  "status-pre-sale",
        "announced": "status-announced",
        "rumor":     "status-rumor",
        "unknown":   "status-unknown",
        None:        "status-unknown",
    }.get(status, "status-unknown")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"🎵 JP Concert Tracker starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
