"""清理資料庫：刪除過期、低品質與舊版誤抓記錄"""
from database import cleanup_old_concerts, init_db, get_connection

init_db()
cleanup_old_concerts()

conn = get_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) as n FROM concerts")
remaining = cur.fetchone()["n"]
conn.close()
print(f"清理完成，剩餘 {remaining} 筆演唱會記錄")
