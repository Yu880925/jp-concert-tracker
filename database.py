"""
database.py - SQLite database management for JP Concert Tracker
"""
import os
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).parent / "concerts.db")))


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database schema."""
    conn = get_connection()
    cur = conn.cursor()

    # Artists table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS artists (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            name_jp     TEXT,
            name_en     TEXT,
            twitter_handle TEXT,
            instagram_handle TEXT,
            official_url TEXT,
            image_url   TEXT,
            genre       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    # Concerts / events table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS concerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id       INTEGER NOT NULL REFERENCES artists(id),
            event_name      TEXT,
            venue           TEXT,
            city            TEXT DEFAULT '台北',
            country         TEXT DEFAULT '台灣',
            concert_date    TEXT,
            concert_date_raw TEXT,
            ticket_url      TEXT,
            ticket_status   TEXT DEFAULT 'unknown',
            source_url      TEXT,
            source_text     TEXT,
            source_platform TEXT,
            ai_confidence   REAL DEFAULT 0.0,
            notes           TEXT,
            is_confirmed    INTEGER DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(artist_id, concert_date)
        )
    """)

    # Monitor log table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id   INTEGER,
            platform    TEXT,
            source_url  TEXT,
            raw_content TEXT,
            ai_result   TEXT,
            matched     INTEGER DEFAULT 0,
            scanned_at  TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    _seed_artists()
    _seed_demo_concerts()
    _migrate_db()
    cleanup_old_concerts()


def _seed_artists():
    """Seed famous Japanese artists."""
    artists = [
        {
            "name": "YOASOBI",
            "name_jp": "YOASOBI",
            "name_en": "YOASOBI",
            "twitter_handle": "YOASOBI_staff",
            "instagram_handle": "yoasobi_ayase_ikura",
            "official_url": "https://yoasobi-music.jp",
            "genre": "J-Pop / Indie Pop",
            "image_url": "https://i.imgur.com/placeholder.jpg"
        },
        {
            "name": "Ado",
            "name_jp": "Ado",
            "name_en": "Ado",
            "twitter_handle": "Ado1024imokenp",
            "instagram_handle": "ado1024",
            "official_url": "https://ado-official.com",
            "genre": "J-Pop / Anime",
        },
        {
            "name": "米津玄師",
            "name_jp": "米津玄師",
            "name_en": "Kenshi Yonezu",
            "twitter_handle": "hachi_08",
            "official_url": "https://reissuerecords.net",
            "genre": "J-Pop / Art Pop",
        },
        {
            "name": "Official髭男dism",
            "name_jp": "Official髭男dism",
            "name_en": "Official HIGE DANdism",
            "twitter_handle": "officialhige",
            "official_url": "https://higedan.com",
            "genre": "J-Pop / Piano Rock",
        },
        {
            "name": "King Gnu",
            "name_jp": "King Gnu",
            "name_en": "King Gnu",
            "twitter_handle": "KingGnu_JP",
            "official_url": "https://kinggnu.jp",
            "genre": "J-Pop / Art Rock",
        },
        {
            "name": "星野源",
            "name_jp": "星野源",
            "name_en": "Gen Hoshino",
            "twitter_handle": "gen_senden",
            "official_url": "https://www.hoshinogen.com",
            "genre": "J-Pop / Soul / Comedy",
        },
        {
            "name": "back number",
            "name_jp": "back number",
            "name_en": "back number",
            "twitter_handle": "backnumberstaff",
            "official_url": "https://backnumber.info",
            "genre": "J-Pop / Rock",
        },
        {
            "name": "ONE OK ROCK",
            "name_jp": "ONE OK ROCK",
            "name_en": "ONE OK ROCK",
            "twitter_handle": "ONEOKROCK_japan",
            "official_url": "https://www.oneokrock.com",
            "genre": "J-Rock / Pop Punk",
        },
        {
            "name": "LiSA",
            "name_jp": "LiSA",
            "name_en": "LiSA",
            "twitter_handle": "LiSA_OLiVE",
            "official_url": "https://www.lxixsxa.com",
            "genre": "J-Pop / Anime",
        },
        {
            "name": "Aimer",
            "name_jp": "Aimer",
            "name_en": "Aimer",
            "twitter_handle": "Aimer_and_staff",
            "official_url": "https://www.sonymusic.co.jp/artist/Aimer",
            "genre": "J-Pop / Anime / Alternative",
        },
        {
            "name": "ずっと真夜中でいいのに。",
            "name_jp": "ずっと真夜中でいいのに。",
            "name_en": "ZUTOMAYO",
            "twitter_handle": "zutomayo",
            "official_url": "https://zutomayo.net",
            "genre": "J-Pop / Art Rock",
        },
        {
            "name": "マカロニえんぴつ",
            "name_jp": "マカロニえんぴつ",
            "name_en": "Macaroni Enpitsu",
            "twitter_handle": "macarock0616",
            "official_url": "https://www.macaronienpitsu.com",
            "genre": "J-Pop / Rock",
        },
        {
            "name": "Mrs. GREEN APPLE",
            "name_jp": "Mrs. GREEN APPLE",
            "name_en": "Mrs. GREEN APPLE",
            "twitter_handle": "AORINGOHUZIN",
            "official_url": "https://www.mrs-greenapple.com",
            "genre": "J-Pop / Pop Rock",
        },
        {
            "name": "RADWIMPS",
            "name_jp": "RADWIMPS",
            "name_en": "RADWIMPS",
            "twitter_handle": "RADWIMPS",
            "official_url": "https://www.radwimps.jp",
            "genre": "J-Rock / J-Pop",
        },
        {
            "name": "imase",
            "name_jp": "imase",
            "name_en": "imase",
            "twitter_handle": "imase_1109",
            "official_url": "https://www.imase.com",
            "genre": "J-Pop / R&B",
        },
        {
            "name": "ユイカ",
            "name_jp": "ユイカ",
            "name_en": "yuika",
            "twitter_handle": "yuika_staff",
            "official_url": "https://www.yuika.com",
            "genre": "J-Pop / R&B",
        },
        {
            "name": "Vaundy",
            "name_jp": "Vaundy",
            "name_en": "Vaundy",
            "twitter_handle": "Vaundy_AWS",
            "official_url": "https://www.Vaundy.com",
            "genre": "J-Pop / R&B",
        },
        {
            "name": "ロクデナシ",
            "name_jp": "ロクデナシ",
            "name_en": "Rokudenashi",
            "twitter_handle": "Rokudenashi_nzn",
            "official_url": "https://www.Rokudenashi.com",
            "genre": "J-Pop / R&B",
        },
    ]

    conn = get_connection()
    cur = conn.cursor()
    for a in artists:
        cur.execute("""
            INSERT INTO artists
                (name, name_jp, name_en, twitter_handle, instagram_handle, official_url, genre, image_url)
            VALUES (:name, :name_jp, :name_en, :twitter_handle,
                    :instagram_handle, :official_url, :genre, :image_url)
            ON CONFLICT(name) DO UPDATE SET
                name_jp          = excluded.name_jp,
                name_en          = excluded.name_en,
                twitter_handle   = COALESCE(excluded.twitter_handle, twitter_handle),
                instagram_handle = COALESCE(excluded.instagram_handle, instagram_handle),
                official_url     = COALESCE(excluded.official_url, official_url),
                genre            = COALESCE(excluded.genre, genre),
                image_url        = COALESCE(excluded.image_url, image_url)
        """, {
            "name": a["name"],
            "name_jp": a.get("name_jp"),
            "name_en": a.get("name_en"),
            "twitter_handle": a.get("twitter_handle"),
            "instagram_handle": a.get("instagram_handle"),
            "official_url": a.get("official_url"),
            "genre": a.get("genre"),
            "image_url": a.get("image_url"),
        })
    conn.commit()
    conn.close()


def _seed_demo_concerts():
    """Demo data removed - using real monitored data only."""
    pass



def _migrate_db():
    """執行資料庫 migration，修正 UNIQUE 約束。"""
    conn = get_connection()
    cur = conn.cursor()

    # 檢查 concerts 的 unique index 是否還包含 venue
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='concerts'")
    row = cur.fetchone()
    if row and "UNIQUE(artist_id, concert_date, venue)" in row["sql"]:
        # 需要重建 table：移除 venue 從 unique key
        cur.executescript("""
            BEGIN;
            CREATE TABLE concerts_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_id       INTEGER NOT NULL REFERENCES artists(id),
                event_name      TEXT,
                venue           TEXT,
                city            TEXT DEFAULT '台北',
                country         TEXT DEFAULT '台灣',
                concert_date    TEXT,
                concert_date_raw TEXT,
                ticket_url      TEXT,
                ticket_status   TEXT DEFAULT 'unknown',
                source_url      TEXT,
                source_text     TEXT,
                source_platform TEXT,
                ai_confidence   REAL DEFAULT 0.0,
                notes           TEXT,
                is_confirmed    INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(artist_id, concert_date)
            );
            INSERT OR IGNORE INTO concerts_new
                SELECT * FROM concerts;
            DROP TABLE concerts;
            ALTER TABLE concerts_new RENAME TO concerts;
            COMMIT;
        """)
        print("[DB Migration] concerts UNIQUE 約束已更新為 (artist_id, concert_date)")
    conn.close()


def cleanup_old_concerts():
    """刪除過期、低品質與 DDG 誤抓的演唱會記錄。"""
    import datetime
    today = datetime.date.today().isoformat()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM concerts WHERE concert_date IS NOT NULL AND concert_date < ?", (today,))
    past_deleted = cur.rowcount
    cur.execute("DELETE FROM concerts WHERE source_url = 'ddg_supplement'")
    ddg_deleted = cur.rowcount
    cur.execute("""
        DELETE FROM concerts WHERE
            (ticket_status = 'rumor' AND (ticket_url IS NULL OR ticket_url = ''))
            OR (concert_date IS NULL AND (ticket_url IS NULL OR ticket_url = '') AND is_confirmed = 0)
            OR event_name LIKE '%疑似%'
            OR notes LIKE '%AI 分析失敗%'
            OR notes LIKE '%關鍵字命中%'
    """)
    rumor_deleted = cur.rowcount
    conn.commit()
    conn.close()
    total = past_deleted + ddg_deleted + rumor_deleted
    if total:
        print(f"[DB] 清除 {past_deleted} 筆過期、{ddg_deleted} 筆 DDG 誤抓、{rumor_deleted} 筆低品質記錄")


def get_all_artists():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM artists ORDER BY name")
    artists = [dict(r) for r in cur.fetchall()]
    conn.close()
    return artists


def get_concerts_by_artists(artist_ids: list):
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("?" * len(artist_ids))
    cur.execute(f"""
        SELECT c.*, a.name as artist_name, a.name_en, a.genre, a.twitter_handle, a.official_url
        FROM concerts c
        JOIN artists a ON a.id = c.artist_id
        WHERE c.artist_id IN ({placeholders})
          AND (
            (c.concert_date IS NOT NULL AND c.concert_date >= date('now'))
            OR (c.ticket_url IS NOT NULL AND c.ticket_url != '')
          )
        ORDER BY c.concert_date ASC
    """, artist_ids)
    concerts = [dict(r) for r in cur.fetchall()]
    conn.close()
    return concerts


def get_artists_with_concert_status():
    """Return all artists with a flag indicating if they have any Taiwan data."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.*,
               COUNT(c.id) as concert_count,
               MAX(c.updated_at) as last_updated
        FROM artists a
        LEFT JOIN concerts c ON c.artist_id = a.id
            AND (
                (c.concert_date IS NOT NULL AND c.concert_date >= date('now'))
                OR (c.ticket_url IS NOT NULL AND c.ticket_url != '')
            )
        GROUP BY a.id
        ORDER BY a.name
    """)
    artists = [dict(r) for r in cur.fetchall()]
    conn.close()
    return artists


def upsert_concert(artist_id, event_name, venue, concert_date, **kwargs):
    import datetime as _dt
    if concert_date:
        try:
            if _dt.date.fromisoformat(concert_date) < _dt.date.today():
                return
        except ValueError:
            return

    conn = get_connection()
    cur = conn.cursor()

    # 如果現在有明確日期，刪掉同歌手的「日期未定（NULL）」舊記錄
    if concert_date is not None:
        cur.execute("""
            DELETE FROM concerts
            WHERE artist_id = ? AND concert_date IS NULL
        """, (artist_id,))
    else:
        # concert_date 是 NULL：先刪掉同歌手所有舊 NULL 記錄再重新寫入
        # （SQLite UNIQUE 約束對 NULL 無效，必須手動去重）
        cur.execute("""
            DELETE FROM concerts
            WHERE artist_id = ? AND concert_date IS NULL
        """, (artist_id,))

    cur.execute("""
        INSERT INTO concerts (artist_id, event_name, venue, concert_date,
            ticket_url, ticket_status, source_url, source_text, source_platform,
            ai_confidence, is_confirmed, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(artist_id, concert_date) DO UPDATE SET
            event_name = COALESCE(excluded.event_name, event_name),
            venue = excluded.venue,
            ticket_url = COALESCE(excluded.ticket_url, ticket_url),
            ticket_status = excluded.ticket_status,
            ai_confidence = MAX(excluded.ai_confidence, ai_confidence),
            is_confirmed = MAX(excluded.is_confirmed, is_confirmed),
            notes = excluded.notes,
            updated_at = datetime('now')
    """, (
        artist_id, event_name, venue, concert_date,
        kwargs.get("ticket_url"), kwargs.get("ticket_status", "unknown"),
        kwargs.get("source_url"), kwargs.get("source_text"), kwargs.get("source_platform"),
        kwargs.get("ai_confidence", 0.0), kwargs.get("is_confirmed", 0),
        kwargs.get("notes")
    ))
    conn.commit()
    conn.close()