import sqlite3, os, json

AUTH_TOKEN = "620d135c8123d32bea354d81ae6ff940649f7392"
CT0        = "054308918381495f27c6f546d8a9a650b7671cf7a62ba508ae585790230188ccf5580f9e20c61b37f9342600a793dd3bace122ac45546ec81710801750f9e875532c814cb6bdea8d88df3e2efba95bdb"
USERNAME   = "HiYu1213774"
EMAIL      = "nick30902@gmail.com"
PASSWORD   = "Aa10712236"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

script_dir = os.path.dirname(os.path.abspath(__file__))
db_path    = os.path.join(script_dir, "accounts.db")

cookies_str  = f"auth_token={AUTH_TOKEN}; ct0={CT0}"
cookies_json = json.dumps({"auth_token": AUTH_TOKEN, "ct0": CT0})
headers_json = json.dumps({
    "authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
    "x-csrf-token": CT0,
    "cookie": cookies_str,
})

conn = sqlite3.connect(db_path)
cur  = conn.cursor()

cur.execute("DELETE FROM accounts WHERE username=?", (USERNAME,))

cur.execute("""
    INSERT INTO accounts
        (username, password, email, email_password,
         user_agent, active, cookies, headers)
    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
""", (USERNAME, PASSWORD, EMAIL, PASSWORD,
      USER_AGENT, cookies_json, headers_json))

conn.commit()

cur.execute("SELECT username, active, cookies FROM accounts WHERE username=?", (USERNAME,))
row = cur.fetchone()
cookies_preview = row[2][:40] + "..." if row[2] else None
print(f"✅ 成功！username={row[0]}, active={row[1]}, cookies={cookies_preview}")
conn.close()
print("\n現在執行：python monitor.py --prefer nitter")