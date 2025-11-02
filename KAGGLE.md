# Kaggle 使用指南

在 Kaggle Notebook 中運行 Citadel Securities 新聞爬蟲

---

## 🚀 快速開始

### Step 1: 設置 Secrets

在 Kaggle Notebook 中：
1. 點擊右側 **Add-ons** → **Secrets**
2. 添加以下密鑰：

| 密鑰名稱 | 說明 | 示例 |
|---------|------|------|
| `MONGODB_URL` | MongoDB 連接字符串 | `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `OPENAI_API_KEY` | OpenAI API 密鑰 | `sk-proj-...` |
| `MODEL` | OpenAI 模型 | `gpt-4o-mini` |
| `MAIL_TOKEN` | Gmail 完整地址 | `your@gmail.com` |
| `APP_PASSWORD` | Gmail 應用專用密碼 | `abcdefghijklmnop` |
| `RECIPIENTS` | 收件人（逗號分隔） | `email1@gmail.com,email2@gmail.com` |
| `GITHUB_TOKEN` | GitHub Token | `ghp_...` |
| `GITHUB_REPO` | GitHub 倉庫 | `username/PicBed` |

3. 確保啟用 **Internet** 選項（Settings → Internet）

---

### Step 2: 上傳 scraper.py 到 Dataset

1. 創建新的 Dataset：
   - 點擊 **Add data** → **New Dataset**
   - 上傳 `scraper.py` 文件
   - 設置 Dataset 名稱（如 `citadel-scraper-code`）
   - 設為 **Public** 或 **Private**

2. 在 Notebook 中添加 Dataset：
   - 點擊右側 **Add data**
   - 搜索並添加你剛創建的 Dataset

---

### Step 3: 安裝依賴

在第一個 Cell 中運行：

```python
# Cell 1: 安裝依賴
!pip install playwright pymongo python-dotenv openai PyGithub requests Pillow -q
!playwright install chromium
!playwright install-deps chromium
```

---

### Step 4: 導入代碼

在第二個 Cell 中：

```python
# Cell 2: 導入 scraper.py
import sys
sys.path.append('/kaggle/input/citadel-scraper-code')  # 替換為你的 Dataset 名稱

# 導入所有內容
from scraper import *
```

---

### Step 5: 運行爬蟲

#### 方法 1：使用命令行參數（推薦）

```python
# Cell 3: 測試模式 - 抓取 GMI
import sys
sys.argv = ['scraper.py', '--test', '--series', 'global-market-intelligence']

# ⚠️ 重要：在 Kaggle 中使用 await，不要用 asyncio.run()
await main_async()
```

#### 方法 2：直接使用類（更簡潔）

```python
# Cell 3: 直接創建實例（無需 sys.argv）
scraper_instance = CitadelScraper(
    test_mode=True,  # 測試模式
    series_list=['global-market-intelligence']
)
await scraper_instance.scrape_all()
```

---

## 📋 常用場景

### 場景 1：測試單個系列

```python
scraper_instance = CitadelScraper(
    test_mode=True,
    series_list=['global-market-intelligence']
)
await scraper_instance.scrape_all()
```

### 場景 2：正式抓取所有系列

```python
scraper_instance = CitadelScraper(
    test_mode=False,
    series_list=['global-market-intelligence', 'macro-thoughts']
)
await scraper_instance.scrape_all()
```

### 場景 3：只抓 Macro Thoughts

```python
scraper_instance = CitadelScraper(
    test_mode=False,
    series_list=['macro-thoughts']
)
await scraper_instance.scrape_all()
```

### 場景 4：使用命令行參數（備選）

如果想使用命令行參數風格：

```python
import sys
sys.argv = ['scraper.py', '--test', '--series', 'global-market-intelligence']
await main_async()
```

---

## 🔍 查看結果

### 檢查 MongoDB 數據

```python
# Cell: 查看數據庫
from pymongo import MongoClient

mongodb_url = get_secret('MONGODB_URL')
client = MongoClient(mongodb_url)
db = client['citadel_scraper']
articles = db['articles']

# 統計
print(f"📊 總文章數: {articles.count_documents({})}")

# 按系列分類
from collections import Counter
series_count = Counter(a['series_name'] for a in articles.find())
print("\n📈 各系列文章數:")
for series, count in series_count.items():
    print(f"  {series}: {count}")

# 最近文章
print("\n📝 最近 5 篇文章:")
for article in articles.find().sort('scraped_at', -1).limit(5):
    print(f"  [{article['series_emoji']}] {article['title']}")
    print(f"    {article['url']}")
    print(f"    抓取: {article['scraped_at']}\n")
```

### 查看日誌

```python
# Cell: 查看日誌
import glob

log_files = glob.glob('/kaggle/working/scraper_*.log')
if log_files:
    latest_log = max(log_files, key=os.path.getctime)
    print(f"📄 最新日誌: {latest_log}\n")
    
    # 顯示最後 50 行
    with open(latest_log, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        print(''.join(lines[-50:]))
else:
    print("❌ 未找到日誌文件")
```

---

## ⚠️ 常見問題

### 問題 1: `RuntimeError: asyncio.run() cannot be called from a running event loop`

**原因**：Kaggle Notebook 已經在運行 event loop。

**解決**：
```python
# ❌ 錯誤
asyncio.run(main())

# ✅ 正確
await main_async()
```

---

### 問題 2: `Client.__init__() got an unexpected keyword argument 'proxies'`

**原因**：OpenAI SDK 版本不匹配。

**解決**：
```python
# 安裝特定版本
!pip install openai==1.3.0 -q
```

---

### 問題 3: Playwright 安裝失敗

**解決**：
```python
# 確保運行完整命令
!playwright install chromium
!playwright install-deps chromium
```

---

### 問題 4: Secrets 無法讀取

**檢查**：
1. Notebook Settings → Secrets 是否已添加
2. Secrets 名稱是否完全一致（區分大小寫）
3. 是否啟用了 Internet

---

### 問題 5: MongoDB 連接超時

**解決**：
1. MongoDB Atlas → Network Access
2. 添加 IP: `0.0.0.0/0`（允許所有 IP）
3. 或添加 Kaggle 的 IP 範圍

---

## 📊 完整 Notebook 範例

### Notebook 結構

```
╔════════════════════════════════════════════╗
║ Cell 1: 安裝依賴                            ║
╚════════════════════════════════════════════╝
!pip install playwright pymongo python-dotenv openai PyGithub requests Pillow -q
!playwright install chromium
!playwright install-deps chromium

╔════════════════════════════════════════════╗
║ Cell 2: 導入 scraper.py                    ║
╚════════════════════════════════════════════╝
import sys
sys.path.append('/kaggle/input/citadel-scraper-code')
from scraper import *

╔════════════════════════════════════════════╗
║ Cell 3: 測試運行（GMI）                     ║
╚════════════════════════════════════════════╝
scraper_instance = CitadelScraper(
    test_mode=True,
    series_list=['global-market-intelligence']
)
await scraper_instance.scrape_all()

╔════════════════════════════════════════════╗
║ Cell 4: 正式運行（所有系列）                ║
╚════════════════════════════════════════════╝
scraper_instance = CitadelScraper(
    test_mode=False,
    series_list=['global-market-intelligence', 'macro-thoughts']
)
await scraper_instance.scrape_all()

╔════════════════════════════════════════════╗
║ Cell 5: 查看結果                           ║
╚════════════════════════════════════════════╝
from pymongo import MongoClient
mongodb_url = get_secret('MONGODB_URL')
client = MongoClient(mongodb_url)
db = client['citadel_scraper']
articles = db['articles']
print(f"總文章數: {articles.count_documents({})}")

╔════════════════════════════════════════════╗
║ Cell 6: 查看日誌                           ║
╚════════════════════════════════════════════╝
!cat scraper_*.log | tail -50
```

---

## 🎯 測試配置

在正式運行前，測試各項配置：

```python
# Cell: 測試配置
print("🔍 測試配置...")

# 1. MongoDB
try:
    from pymongo import MongoClient
    client = MongoClient(get_secret('MONGODB_URL'), serverSelectionTimeoutMS=5000)
    client.server_info()
    print("✅ MongoDB 連接成功")
except Exception as e:
    print(f"❌ MongoDB 失敗: {e}")

# 2. OpenAI
try:
    from openai import OpenAI
    client = OpenAI(api_key=get_secret('OPENAI_API_KEY'))
    print("✅ OpenAI 配置成功")
except Exception as e:
    print(f"❌ OpenAI 失敗: {e}")

# 3. GitHub
try:
    from github import Github
    g = Github(get_secret('GITHUB_TOKEN'))
    user = g.get_user()
    print(f"✅ GitHub 連接成功: {user.login}")
except Exception as e:
    print(f"❌ GitHub 失敗: {e}")

# 4. Gmail
try:
    mail = get_secret('MAIL_TOKEN')
    pwd = get_secret('APP_PASSWORD')
    if mail and pwd and len(pwd) == 16:
        print("✅ Gmail 配置正確")
    else:
        print("❌ Gmail 配置錯誤")
except Exception as e:
    print(f"❌ Gmail 失敗: {e}")

print("\n✨ 配置測試完成")
```

---

## 💡 小技巧

### 1. 查看執行時間

```python
%%time
await main_async()
```

### 2. 靜默安裝（減少輸出）

```python
%%capture
!pip install ... -q
```

### 3. 查看文件大小

```python
!ls -lh /kaggle/working/
```

### 4. 清理日誌

```python
!rm -f scraper_*.log
```

---

## 🔄 自動化執行

Kaggle 不直接支持定時任務，但可以通過：

### 方法 1: GitHub Actions + Kaggle API

```yaml
# .github/workflows/run-scraper.yml
name: Run Scraper
on:
  schedule:
    - cron: '0 9 * * *'  # 每天 9:00 UTC
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Trigger Kaggle Notebook
        run: |
          kaggle kernels push -p .
```

### 方法 2: 本地定時觸發

```python
# local_trigger.py
import kaggle
kaggle.api.kernels_push('username/citadel-scraper')
```

---

## 📝 檢查清單

運行前確保：

- [ ] ✅ 已上傳 scraper.py 到 Dataset
- [ ] ✅ 已在 Notebook 中添加該 Dataset
- [ ] ✅ 已添加所有 Secrets
- [ ] ✅ 已啟用 Internet
- [ ] ✅ 已安裝依賴（playwright, pymongo 等）
- [ ] ✅ 已安裝 Playwright Chromium
- [ ] ✅ MongoDB IP 白名單已設置
- [ ] ✅ Gmail 使用應用專用密碼
- [ ] ✅ GitHub Token 有 repo 權限
- [ ] ✅ 正確導入 scraper 模組

---

## 🎓 學習資源

- [Kaggle Secrets 文檔](https://www.kaggle.com/docs/notebooks#secrets)
- [Playwright 文檔](https://playwright.dev/python/)
- [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
- [OpenAI API](https://platform.openai.com/docs)

---

## 📞 支援

遇到問題？
1. 查看本文「常見問題」章節
2. 檢查 Kaggle Notebook 日誌
3. 運行「測試配置」Cell

---

**版本**: v3.0  
**最後更新**: 2025-11-01  
**環境**: Kaggle Notebook

🚀 祝在 Kaggle 上運行順利！

