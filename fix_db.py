"""一次性清理：刪除過期演唱會與 DDG 誤抓資料"""
from database import cleanup_old_concerts, init_db

init_db()
cleanup_old_concerts()
print("清理完成")
