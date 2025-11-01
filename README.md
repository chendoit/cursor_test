# Citadel Securities 新聞爬蟲

使用 Python Async Playwright 抓取 Citadel Securities 多個系列文章，並自動翻譯為繁體中文。

## 🌐 支援環境

- ✅ **本地端**：Windows / macOS / Linux
- ✅ **Kaggle Notebook**：完整支援（詳見 `kaggle_setup_guide.md`）
- ✅ 自動檢測環境並使用對應的配置方式

## ✨ 功能特性

- ✅ **多系列支持**: Global Market Intelligence、Macro Thoughts
- ✅ **Async 異步**: 使用 Async Playwright 提升性能
- ✅ **智能抓取**: 自動抓取最新文章
- ✅ **MongoDB 儲存**: 使用 URL 作為唯一鍵，避免重複
- ✅ **OpenAI 翻譯**: 段落對應，英文緊跟繁體中文翻譯
- ✅ **順序保持**: 完整保留原文中圖片與文字的順序
- ✅ **GitHub 圖床**: 自動上傳圖片到 GitHub，永久保存
- ✅ **圖片唯一性**: 使用 hash + timestamp 確保檔案名唯一
- ✅ **Gmail 發送**: 自動發送精美的 HTML 格式郵件
- ✅ **測試模式**: 可重複測試不影響資料庫
- ✅ **詳細日誌**: 每天一個日誌文件
- ✅ **環境適配**: 自動識別本地端或 Kaggle 環境

## 📦 安裝步驟

### 1. 安裝依賴

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置環境變數

創建 `.env` 文件（參考 `env_template.txt`）：

```bash
# MongoDB
MONGODB_URL=mongodb+srv://username:password@cluster.mongodb.net/database

# OpenAI
OPENAI_API_KEY=sk-your-api-key
MODEL=gpt-4o-mini

# Gmail
MAIL_TOKEN=your-email@gmail.com
APP_PASSWORD=your-app-password
RECIPIENTS=recipient@example.com

# GitHub (圖片儲存)
GITHUB_TOKEN=ghp_your-github-token
GITHUB_REPO=chendoit/PicBed
```

### 3. 測試配置

```bash
python test_config.py
```

## 🚀 使用方法

### 🖥️ 本地端使用

#### 方法一：抓取所有系列（推薦）

```bash
python scraper.py
# 或使用 batch 文件
run_scraper.bat
```

#### 方法二：抓取指定系列

```bash
# 只抓取 Global Market Intelligence
python scraper.py --series global-market-intelligence

# 只抓取 Macro Thoughts
python scraper.py --series macro-thoughts

# 同時抓取多個系列
python scraper.py --series global-market-intelligence macro-thoughts
```

#### 方法三：測試模式（不保存到 MongoDB）

```bash
python scraper.py --test --series global-market-intelligence
# 或使用 batch 文件
run_scraper_test.bat
```

---

### ☁️ Kaggle Notebook 使用

詳細說明請參考 **`kaggle_setup_guide.md`**

**快速開始：**

```python
# Cell 1: 安裝依賴
!pip install playwright pymongo python-dotenv openai PyGithub requests Pillow -q
!playwright install chromium
!playwright install-deps chromium

# Cell 2: 上傳 scraper.py 文件到 Kaggle

# Cell 3: 運行爬蟲
import asyncio
import scraper

# 創建爬蟲實例
scraper_instance = scraper.CitadelScraper(
    test_mode=False,
    series_list=['global-market-intelligence', 'macro-thoughts']
)

# 執行
await scraper_instance.scrape_all()
```

**注意**：在 Kaggle Settings → Secrets 中設置所有必需的密鑰（MONGODB_URL、OPENAI_API_KEY 等）

---

### 方法二：命令行

```bash
# 激活虛擬環境
.\venv\Scripts\activate

# 運行爬蟲
python scraper.py         # 正常模式（所有系列）
python scraper.py --test  # 測試模式
```

## 📊 工作流程

1. 訪問目標網站首頁
2. 定位第一篇文章
3. 檢查 MongoDB（URL 是否已存在）
4. 進入文章頁面
5. **按順序抓取內容**（文字段落和圖片，保持原始順序）
6. OpenAI 翻譯文字段落為繁體中文
7. **上傳圖片到 GitHub**（生成唯一檔案名）
8. 保存到 MongoDB
9. **發送郵件**（圖片與文字按原始順序呈現）
10. 完成！

## 📁 輸出文件

### 1. MongoDB 文檔

```json
{
  "url": "https://...",              // Unique Key
  "title": "November",
  "date": "October 24, 2025",
  "content_elements": [              // 按順序的內容元素
    {"type": "text", "content": "...", "order": 0},
    {"type": "image", "content": "https://raw.githubusercontent.com/...", "order": 1},
    {"type": "text", "content": "...", "order": 2}
  ],
  "scraped_at": "2025-10-31T...",
  "translated": true
}
```

### 2. GitHub 圖片

所有圖片上傳到: https://github.com/chendoit/PicBed
- 檔案名格式: `citadel_YYYYMMDDHHMMSS_hash.jpg`
- 永久保存，不會過期
- 郵件中直接引用 GitHub raw URL

### 3. 日誌文件

- `scraper_YYYYMMDD.log`: 當天的日誌文件

### 3. 郵件

- 精美 HTML 格式
- 包含所有圖片
- 英文段落緊跟繁體中文翻譯
- 🇹🇼 台灣旗幟標記中文段落

## 🎨 郵件格式

- **內容順序**: 完全按照原文排列（文字、圖片穿插）
- **英文段落**: 灰色文字，清晰易讀
- **繁體中文**: 淺藍背景 + 藍色左邊框 + 🇹🇼 圖標
- **圖片顯示**: 使用 GitHub raw URL，永久有效
- **圖片位置**: 保持原文中的位置
- **專業排版**: 現代化設計

## 🔧 配置說明

### MongoDB 連接

支持 MongoDB Atlas 或本地 MongoDB:

```bash
# MongoDB Atlas
MONGODB_URL=mongodb+srv://user:pass@cluster.mongodb.net/db

# 本地 MongoDB
MONGODB_URL=mongodb://localhost:27017/citadel_scraper
```

### OpenAI 模型選擇

```bash
MODEL=gpt-4o-mini       # 快速、經濟（推薦）
MODEL=gpt-4o            # 更高質量
MODEL=gpt-4-turbo       # 平衡性能
```

### Gmail 設置

1. 啟用兩步驗證
2. 生成應用專用密碼: https://myaccount.google.com/apppasswords
3. 填入 `.env` 文件

### GitHub Token 設置

1. 訪問: https://github.com/settings/tokens
2. 點擊 "Generate new token" -> "Generate new token (classic)"
3. 選擇權限: `repo` (完整控制)
4. 生成並複製 token
5. 填入 `.env` 文件

## 📝 日誌系統

- **文件名**: `scraper_YYYYMMDD.log`
- **特點**: 一天一個文件，多次運行追加到同一文件
- **級別**: DEBUG（文件）、INFO（控制台）

## 🐛 故障排查

### MongoDB 連接失敗
- 檢查 URL 格式
- 確認 IP 白名單
- 測試網絡連接

### OpenAI API 錯誤
- 確認 API Key 有效
- 檢查帳戶餘額
- 確認模型名稱

### Gmail 發送失敗
- 使用應用專用密碼
- 確認已啟用兩步驗證
- 檢查郵箱地址

## 💡 使用建議

1. **首次運行**: 使用測試模式驗證配置
2. **定期備份**: 導出 MongoDB 數據
3. **監控配額**: 關注 OpenAI API 使用量
4. **日誌管理**: 定期清理舊日誌文件

## 🔐 安全提示

⚠️ **重要**: 
- 不要將 `.env` 文件提交到 Git
- 定期更換 API 密鑰
- 妥善保管應用專用密碼

---

**技術棧**: Python + Playwright + MongoDB + OpenAI + Gmail

