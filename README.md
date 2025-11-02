# Citadel Securities 新聞爬蟲

自動抓取 Citadel Securities 新聞文章，翻譯為繁體中文，並透過郵件發送。

---

## ✨ 功能特性

### 核心功能
- 🌐 **多系列支援**：Global Market Intelligence、Macro Thoughts
- ⚡ **異步爬蟲**：使用 Async Playwright 提升效能
- 🗄️ **MongoDB 儲存**：自動去重，避免重複抓取
- 🤖 **AI 翻譯**：OpenAI GPT 段落對應翻譯為繁體中文
- 📧 **郵件通知**：自動發送精美的 HTML 郵件
- 🖼️ **圖片管理**：上傳至 GitHub 永久保存
- 📝 **詳細日誌**：每日日誌文件，方便追蹤

### 環境支援
- ✅ **本地端**：Windows / macOS / Linux
- ✅ **Kaggle Notebook**：完整支援雲端運行
- ✅ **自動適配**：智能識別環境並載入對應配置

---

## 📦 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置環境變數

創建 `.env` 文件（參考 `env_template.txt`）：

```env
# MongoDB
MONGODB_URL=mongodb+srv://username:password@cluster.mongodb.net/

# OpenAI
OPENAI_API_KEY=sk-your-api-key
MODEL=gpt-4o-mini

# Gmail
MAIL_TOKEN=your-email@gmail.com
APP_PASSWORD=your-16-digit-app-password
RECIPIENTS=recipient1@gmail.com,recipient2@gmail.com

# GitHub（圖片儲存）
GITHUB_TOKEN=ghp_your-token
GITHUB_REPO=username/PicBed
```

### 3. 測試配置

```bash
python test_config.py
```

### 4. 運行爬蟲

```bash
# 方法 1：使用 batch 文件（Windows）
run_scraper.bat              # 抓取所有系列
run_scraper_test.bat         # 測試模式

# 方法 2：命令行
python scraper.py                                    # 抓取所有系列
python scraper.py --series global-market-intelligence # 指定系列
python scraper.py --test --series all                # 測試模式
```

---

## 🎯 使用場景

### 命令行參數

```bash
# 抓取所有系列（默認）
python scraper.py

# 抓取單一系列
python scraper.py --series global-market-intelligence
python scraper.py --series macro-thoughts

# 同時抓取多個系列
python scraper.py --series global-market-intelligence macro-thoughts

# 測試模式（不保存到 MongoDB）
python scraper.py --test --series all
```

### 系列說明

| 系列 ID | 名稱 | 說明 |
|---------|------|------|
| `global-market-intelligence` | Global Market Intelligence 📊 | 全球市場情報 |
| `macro-thoughts` | Macro Thoughts 🌍 | 宏觀經濟思考 |
| `all` | 所有系列 | 抓取所有配置的系列 |

---

## 📊 工作流程

```
1. 訪問系列首頁
   ↓
2. 抓取最新文章 URL
   ↓
3. 檢查 MongoDB（避免重複）
   ↓
4. 進入文章頁面
   ↓
5. 抓取內容（文字 + 圖片）
   ↓
6. OpenAI 翻譯為繁體中文
   ↓
7. 上傳圖片到 GitHub
   ↓
8. 保存到 MongoDB
   ↓
9. 發送郵件通知
   ↓
10. 完成！
```

---

## 🔧 配置說明

### MongoDB
- 用於儲存文章數據
- URL 作為唯一鍵，自動去重
- 建議使用 MongoDB Atlas（免費方案）

### OpenAI
- 用於翻譯文章為繁體中文
- 推薦模型：`gpt-4o-mini`（便宜且效果好）
- 段落對應翻譯，保持原文結構

### Gmail
- **MAIL_TOKEN**：完整的 Gmail 地址（如 `your@gmail.com`）
- **APP_PASSWORD**：Gmail 應用專用密碼（16 位數字，非普通密碼）
  - 獲取方式：Google 帳戶 → 安全性 → 兩步驟驗證 → 應用程式密碼
- **RECIPIENTS**：收件人郵箱，多個用逗號分隔

### GitHub
- 用於永久保存文章圖片
- 需要 Personal Access Token（具有 `repo` 權限）
- 建議創建專門的 PicBed 倉庫

---

## 📁 項目結構

```
citadel-scraper/
├── scraper.py              # 主程式
├── requirements.txt        # Python 依賴
├── .env                    # 環境變數（不提交）
├── env_template.txt        # 配置模板
│
├── run_scraper.bat         # 抓取所有系列
├── run_scraper_test.bat    # 測試模式
│
├── test_config.py          # 配置測試
├── test_config.bat         # 配置測試（Windows）
│
├── README.md               # 功能說明（本文件）
├── KAGGLE.md               # Kaggle 使用指南
└── scraper_YYYYMMDD.log    # 日誌文件（自動生成）
```

---

## 📧 郵件示例

### 主題
```
📊 Citadel Securities - 全球市場情報 - November
🌍 Citadel Securities - 宏觀思考 - Supply Constraints + Cyclical Acceleration
```

### 內容
- 系列標籤（雙語）
- 文章標題、日期、原文連結
- 英文段落 + 繁體中文翻譯（交替呈現）
- 圖片（GitHub 永久連結）
- 保持原文順序

---

## 💾 數據結構

### MongoDB 文檔
```json
{
  "url": "https://...",
  "title": "November",
  "date": "October 24, 2025",
  "series": "global-market-intelligence",
  "series_name": "Global Market Intelligence",
  "series_name_zh": "全球市場情報",
  "series_emoji": "📊",
  "content_elements": [
    {"type": "text", "content": "...", "order": 0},
    {"type": "image", "content": "https://raw.githubusercontent.com/...", "order": 1}
  ],
  "scraped_at": "2025-11-01T12:34:56",
  "translated": true
}
```

---

## 🔒 安全性

- `.env` 文件已加入 `.gitignore`，不會提交到版本控制
- 所有密鑰使用環境變數管理
- GitHub Token 建議使用最小權限（僅 `repo`）
- MongoDB 建議設置 IP 白名單

---

## ⚠️ 常見問題

### Q: Gmail 發送失敗？
A: 確保使用 Gmail 應用專用密碼，而非普通密碼。

### Q: MongoDB 連接失敗？
A: 檢查 IP 白名單，或設置為 `0.0.0.0/0` 允許所有 IP。

### Q: OpenAI 翻譯失敗？
A: 確認 API Key 有效且有餘額。

### Q: 圖片上傳失敗？
A: 確認 GitHub Token 有 `repo` 權限。

### Q: 繁體中文顯示亂碼？
A: 確保 batch 文件開頭有 `chcp 65001 >nul`。

---

## 📚 進階使用

### 定時執行（Windows）
使用 Windows 任務排程器：
1. 打開「工作排程器」
2. 創建基本工作
3. 選擇觸發時間（如每天 9:00）
4. 操作：啟動程式 → 選擇 `run_scraper.bat`

### 擴展新系列
編輯 `scraper.py` 中的 `SERIES_CONFIG`：
```python
SERIES_CONFIG = {
    'your-series': {
        'name': 'Your Series Name',
        'name_zh': '你的系列名稱',
        'url': 'https://...',
        'emoji': '🎯'
    }
}
```

---

## 🎓 技術棧

- **爬蟲**：Playwright (Async)
- **數據庫**：MongoDB
- **AI**：OpenAI GPT
- **郵件**：Gmail SMTP
- **圖床**：GitHub
- **語言**：Python 3.11+

---

## 📞 支援

遇到問題？
1. 查看日誌：`scraper_YYYYMMDD.log`
2. 運行測試：`python test_config.py`
3. 參考 Kaggle 指南：`KAGGLE.md`

---

**版本**: v3.0  
**最後更新**: 2025-11-01  
**作者**: Your Name

🎉 祝使用愉快！

