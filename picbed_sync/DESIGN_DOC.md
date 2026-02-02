# PicBed Sync 設計文檔

## 概述

PicBed Sync 是一個 Python 腳本，用於自動化管理 Markdown 檔案中的圖片。它會掃描指定目錄的 `.md` 檔案，將外部圖片下載並上傳到 GitHub PicBed repository，確保圖片長期可用。

## 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│                        picbed_sync.py                           │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Config       │  │ GitHub       │  │ Markdown             │  │
│  │ Loader       │  │ Client       │  │ Parser               │  │
│  │              │  │              │  │                      │  │
│  │ .picbed_env  │  │ Upload API   │  │ Image Extractor      │  │
│  │ Parser       │  │ Repo Status  │  │ Link Replacer        │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ File         │  │ Image        │  │ Progress             │  │
│  │ Scanner      │  │ Downloader   │  │ Tracker              │  │
│  │              │  │              │  │                      │  │
│  │ Recursive    │  │ HTTP Client  │  │ .picbed_processed    │  │
│  │ .md Finder   │  │ Local Reader │  │ .json                │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 處理流程

```
                    ┌─────────────────┐
                    │  Start Script   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Load .picbed_env│
                    │ Configuration   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Check Current   │
                    │ Repo Capacity   │
                    └────────┬────────┘
                             │
              ┌──────────────▼──────────────┐
              │  For Each FOLDER_xxx        │
              │  (Recursive Scan)           │
              └──────────────┬──────────────┘
                             │
              ┌──────────────▼──────────────┐
              │  For Each .md File          │
              └──────────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │ Calculate File  │
                    │ Hash (SHA256)   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Hash Changed?   │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │ No                          │ Yes
              ▼                             ▼
        ┌───────────┐              ┌────────────────┐
        │   Skip    │              │ Extract Images │
        │   File    │              │ from Markdown  │
        └───────────┘              └────────┬───────┘
                                            │
                             ┌──────────────▼──────────────┐
                             │  For Each Image URL         │
                             └──────────────┬──────────────┘
                                            │
                                   ┌────────▼────────┐
                                   │ Is PicBed URL?  │
                                   └────────┬────────┘
                                            │
                             ┌──────────────┴──────────────┐
                             │ Yes                         │ No
                             ▼                             ▼
                       ┌───────────┐              ┌────────────────┐
                       │   Skip    │              │ Download Image │
                       │   Image   │              │ (URL or Local) │
                       └───────────┘              └────────┬───────┘
                                                          │
                                                 ┌────────▼────────┐
                                                 │ Upload to       │
                                                 │ GitHub PicBed   │
                                                 └────────┬────────┘
                                                          │
                                                 ┌────────▼────────┐
                                                 │ Update .md File │
                                                 │ with New URL    │
                                                 └────────┬────────┘
                                                          │
                                                 ┌────────▼────────┐
                                                 │ Save to         │
                                                 │ url_mapping     │
                                                 └─────────────────┘
```

## 檔案結構

```
picbed_sync/
├── picbed_sync.py          # 主程式
├── .picbed_env             # 設定檔（包含 token，不上傳 git）
├── .picbed_env.example     # 設定檔範例
├── .picbed_processed.json  # 處理記錄（自動生成）
└── DESIGN_DOC.md           # 本文檔
```

## 設定檔說明 (.picbed_env)

| 變數名稱 | 說明 | 必填 | 預設值 |
|---------|------|------|--------|
| GITHUB_TOKEN | GitHub Personal Access Token | ✓ | - |
| PICBED_REPO_000~999 | PicBed Repository 列表 | ✓ (至少一個) | - |
| CURRENT_REPO_INDEX | 目前使用的 repo 索引 | | 0 |
| PICBED_BRANCH | Git branch 名稱 | | main |
| FOLDER_000~999 | 要掃描的目錄列表 | ✓ (至少一個) | - |
| ENABLE_BACKUP | 是否建立 .bak 備份檔 | | false |

## 目錄掃描邏輯

### 遞迴掃描 (Recursive Scan)

腳本使用 `pathlib.Path.rglob('*.md')` 進行遞迴掃描：

```python
folder_path = Path(folder)
md_files = list(folder_path.rglob('*.md'))
```

這表示：
- ✅ 會掃描 `FOLDER_000` 目錄本身的 `.md` 檔案
- ✅ 會掃描所有子目錄的 `.md` 檔案
- ✅ 會掃描任意深度的巢狀子目錄

### 範例

假設目錄結構：
```
C:/notes/
├── README.md              ← 會處理
├── daily/
│   ├── 2024-01-01.md      ← 會處理
│   └── 2024-01-02.md      ← 會處理
├── projects/
│   ├── project-a/
│   │   └── notes.md       ← 會處理
│   └── project-b/
│       └── design.md      ← 會處理
└── archive/
    └── old/
        └── legacy.md      ← 會處理
```

設定 `FOLDER_000=C:/notes` 後，以上所有 `.md` 檔案都會被掃描處理。

## 圖片連結解析

### 支援的格式

1. **Markdown 標準格式**
   ```markdown
   ![alt text](https://example.com/image.png)
   ![alt text](https://example.com/image.png "title")
   ```

2. **HTML img 標籤**
   ```html
   <img src="https://example.com/image.png" alt="alt text">
   <img src='https://example.com/image.png' />
   ```

3. **本地相對路徑**
   ```markdown
   ![screenshot](./images/screenshot.png)
   ![photo](../assets/photo.jpg)
   ```

### 正則表達式

```python
# Markdown 圖片
md_pattern = r'(!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\))'

# HTML img 標籤
html_pattern = r'(<img[^>]+src=["\']([^"\']+)["\'][^>]*>)'
```

## GitHub 上傳結構

### 目錄命名規則

圖片上傳到 PicBed repo 時，目錄以來源 `.md` 檔案名稱命名：

```
PicBed/
├── README/
│   ├── logo_a1b2c3d4.png
│   └── banner_e5f6g7h8.jpg
├── 2024_01_01/
│   └── screenshot_i9j0k1l2.png
└── design/
    ├── diagram_m3n4o5p6.svg
    └── mockup_q7r8s9t0.png
```

### 唯一檔名生成

```python
def generate_unique_filename(original_name: str, extension: str) -> str:
    name = Path(original_name).stem          # 取得原始檔名
    name = re.sub(r'[^\w\-]', '_', name)     # 移除非法字元
    name = name[:50]                          # 限制長度
    short_uuid = uuid.uuid4().hex[:8]         # 8 位 UUID
    return f"{name}_{short_uuid}{extension}"  # 組合
```

範例：
- `screenshot.png` → `screenshot_a1b2c3d4.png`
- `我的圖片.jpg` → `我的圖片_e5f6g7h8.jpg`

## 處理記錄格式 (.picbed_processed.json)

```json
{
  "files": {
    "C:/notes/README.md": {
      "hash": "sha256_hash_value",
      "last_processed": "2026-02-02T10:00:00"
    }
  },
  "url_mapping": {
    "https://old-host.com/image.png": {
      "new_url": "https://raw.githubusercontent.com/chendoit/PicBed/main/README/image_a1b2c3d4.png",
      "repo": "chendoit/PicBed",
      "uploaded_at": "2026-02-02T10:00:00"
    }
  }
}
```

### 用途

1. **files**: 記錄每個 `.md` 檔案的 hash，避免重複處理未變更的檔案
2. **url_mapping**: 記錄圖片 URL 映射關係
   - 相同 URL 的圖片不會重複上傳
   - 支援跨檔案的 URL 去重
   - 記錄圖片所在的 repo（支援多 repo 場景）

## 多 Repo 支援

### 為什麼需要多 Repo？

GitHub 對 repository 有容量限制：
- 建議大小：< 1 GB
- 警告閾值：> 1 GB
- 硬限制：~5 GB（會開始拒絕 push）

### 切換 Repo 流程

1. 當 `--status` 顯示當前 repo 接近容量限制
2. 建立新的 GitHub repo（如 `chendoit/PicBed2`）
3. 在 `.picbed_env` 新增 `PICBED_REPO_001=chendoit/PicBed2`
4. 修改 `CURRENT_REPO_INDEX=1`
5. 之後新圖片將上傳到新 repo

### 舊連結不受影響

- `url_mapping` 記錄了每個圖片所在的 repo
- 已上傳的圖片連結保持不變
- 只有新圖片會上傳到新 repo

## 錯誤處理

### 重試機制

| 操作 | 重試次數 | 間隔 |
|------|---------|------|
| 圖片下載 | 3 次 | 1 秒 |
| GitHub 上傳 | 3 次 | 1 秒 |

### 容量警告

| 容量 | 處理 |
|------|------|
| < 800 MB | 正常運行 |
| 800 MB ~ 1 GB | 顯示警告，建議切換 |
| > 1 GB | 強制確認才能繼續 |

### 檔案大小限制

- 單一圖片超過 25 MB 會跳過並警告
- GitHub API 限制單檔 100 MB

## 命令列參數

| 參數 | 說明 |
|------|------|
| (無) | 正常執行，處理所有有變更的檔案 |
| `--dry-run` | 預覽模式，顯示將要處理的內容但不實際執行 |
| `--force` | 強制重新處理所有檔案，忽略 hash 記錄 |
| `--status` | 顯示所有 PicBed repo 的容量狀態 |

## 安全性考量

### Token 保護

- `.picbed_env` 包含 GitHub Token，不應上傳到版本控制
- 確保 `.gitignore` 包含 `.picbed_env`

### 備份機制

- `ENABLE_BACKUP=true` 時，修改 `.md` 檔案前會建立 `.bak` 備份
- 預設關閉，避免產生大量備份檔

## 依賴套件

```
requests>=2.28.0
python-dotenv>=1.0.0
```

## 使用範例

```bash
# 首次使用：預覽模式確認
python picbed_sync.py --dry-run

# 檢查 repo 容量
python picbed_sync.py --status

# 正式執行
python picbed_sync.py

# 強制重新處理（例如換了 repo 設定後）
python picbed_sync.py --force
```

## 版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
| 1.0.0 | 2026-02-02 | 初始版本 |
