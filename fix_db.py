import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path(__file__).parent / "concerts.db")
cur = conn.cursor()
cur.execute("DELETE FROM monitor_log WHERE platform = 'google_supplement'")
print(f"刪除 {cur.rowcount} 筆冷卻記錄")
conn.commit()
conn.close()