# 台灣日系演唱會雷達 🎵

自動監控日本歌手台灣演出資訊，結合 Nitter 推文抓取、AI 語意分析與網頁展示。

---

## 架構（方案 A）

```
GitHub Actions（每 2 小時）          Render（24/7 網頁）
        │                                    │
        ├─ 下載 concerts.db ────────────────►│
        ├─ python monitor.py（Nitter 掃描）   │
        └─ 上傳 concerts.db ────────────────►│ Flask 網頁
                                             └─ 訪客瀏覽 demo URL
```

---

## 部署教學（方案 A 完整流程）

### 前置準備

1. [GitHub](https://github.com) 帳號
2. [Render](https://render.com) 帳號（免費方案即可）
3. 至少一個 AI API Key（推薦 [Groq](https://console.groq.com) 免費額度）

---

### 步驟 1：整理程式碼並推上 GitHub

```bash
# 在專案目錄
git init
git add .
git commit -m "JP Concert Tracker - ready for deploy"

# 在 GitHub 建立新 repo，然後：
git remote add origin https://github.com/你的帳號/jp-concert-tracker.git
git branch -M main
git push -u origin main
```

> **重要：** `concerts.db`、`.env` 已在 `.gitignore`，不會被上傳。

---

### 步驟 2：產生 SCAN_API_KEY

在 PowerShell 執行：

```powershell
# 產生隨機金鑰（記下來，Render 和 GitHub 要用同一個）
-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object {[char]$_})
```

或自己設一組夠長的隨機字串，例如：`myDemoSecretKey2026XyZ789`

---

### 步驟 3：部署 Render 網頁

1. 登入 [Render Dashboard](https://dashboard.render.com)
2. 點 **New → Blueprint**
3. 連接你的 GitHub repo，選擇 `render.yaml`
4. 在環境變數畫面填入：

| 變數 | 值 |
|------|-----|
| `SCAN_API_KEY` | 步驟 2 產生的金鑰 |
| `GROQ_API_KEY` | 你的 Groq API Key |

5. 點 **Apply**，等待部署完成（約 3–5 分鐘）
6. 記下網址，例如：`https://jp-concert-tracker.onrender.com`
7. 開啟網址確認首頁能正常顯示

> **免費方案說明：** Render 免費版不支援 Persistent Disk，資料庫存在容器內（重啟/重新部署會清空）。演唱會資料由 **GitHub Actions 每次掃描後上傳** 到 Render，所以部署完成後務必執行步驟 5 觸發第一次掃描。

> Render 免費方案會在 15 分鐘無人訪問後休眠，第一次開啟需等約 30 秒喚醒，demo 時先開一次預熱即可。

---

### 步驟 4：設定 GitHub Secrets

進入 repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 名稱 | 值 |
|-------------|-----|
| `RENDER_URL` | `https://jp-concert-tracker.onrender.com`（你的 Render 網址，**不要**結尾斜線） |
| `SCAN_API_KEY` | 與 Render 相同的金鑰（**貼上時不要換行**） |
| `GROQ_API_KEY` | 你的 Groq API Key |

---

### 步驟 5：手動觸發第一次掃描

1. 進入 repo → **Actions** 分頁
2. 左側選 **Concert Monitor Scan**
3. 點 **Run workflow → Run workflow**
4. 等待約 10–20 分鐘（18 位歌手掃描）
5. 完成後重新整理 Render 網頁，應能看到演唱會資料

---

### 步驟 6：確認自動排程

Workflow 已設定每 2 小時自動掃描（UTC 時間）。

台灣時間對照：
- UTC `0 */2 * * *` = 台灣時間 08:00、10:00、12:00 …（每 2 小時）

---

## 本機開發

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 GROQ_API_KEY 等

python app.py          # 網頁 http://localhost:5000
python monitor.py      # 手動掃描（預設 Nitter）
```

---

## 檔案說明

| 檔案 | 說明 |
|------|------|
| `app.py` | Flask 網頁伺服器 |
| `monitor.py` | Nitter 監控 + AI 分析引擎 |
| `database.py` | SQLite 資料庫管理 |
| `render.yaml` | Render 部署設定 |
| `.github/workflows/monitor.yml` | GitHub Actions 定時掃描 |
| `scripts/gha_sync.py` | GHA 與 Render 同步資料庫 |

---

## 安全性說明

- 所有 API Key 只存在 Render / GitHub Secrets，不寫在程式碼
- `/api/trigger-scan`、`/api/sync-db` 需 `X-API-Key` 驗證
- 生產環境預設隱藏網頁上的「立即掃描」按鈕
- 若舊 Key 曾出現在程式碼，請到各平台**撤銷並重新產生**

---

## Demo 時怎麼展示

1. 開啟 Render 網址給面試官看前端
2. 到 GitHub Actions 點 **Run workflow** 展示自動化
3. 說明架構：「網頁 24/7 在 Render，掃描由 GitHub Actions 排程，資料透過 API 同步」

---

## 常見問題

**Q: Render 網頁打不開？**  
免費方案休眠中，等 30 秒或先訪問 `/api/stats` 喚醒。

**Q: Actions 掃描失敗？**  
檢查 Secrets 是否正確，尤其是 `RENDER_URL` 不要有多餘斜線。

**Q: 掃描結果沒更新到網頁？**  
確認 GitHub Actions 的 upload 步驟成功。免費版無 Persistent Disk，若剛重新部署 Render，需再跑一次 Actions 掃描上傳資料庫。

**Q: Nitter 全部失敗？**  
Nitter 實例常不穩定，可改用 `python monitor.py --prefer google` 測試，或等下次排程重試。

---

*Built with Flask, SQLite, Nitter, Groq AI, Render, GitHub Actions*
