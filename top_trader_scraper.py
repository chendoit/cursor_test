"""
Top Traders Unplugged 播客爬蟲
- 抓取最新的 5 集播客
- 篩選特定系列（GM, UGO）或講者（Cem Karsan）
- 使用 Async Playwright 提升性能
- MongoDB 儲存、OpenAI 翻譯、Gmail 郵件、GitHub 圖床
"""

import os
import json
import logging
import hashlib
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
import time
import argparse
from pymongo import MongoClient
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import traceback
import requests
from github import Github
from io import BytesIO
import base64
import re


# ===== Kaggle & Local 環境兼容 =====
def get_secret(key: str) -> str:
    """
    取得環境變數或 Kaggle Secret。
    在本地端使用 .env，在 Kaggle 使用 UserSecretsClient。
    """
    try:
        # 嘗試在 Kaggle 環境載入
        from kaggle_secrets import UserSecretsClient
        user_secrets = UserSecretsClient()
        secret = user_secrets.get_secret(key)
        logger.debug(f"✓ 從 Kaggle Secrets 載入: {key}")
        return secret
    except Exception:
        # 非 Kaggle 或未設定 kaggle_secrets
        from dotenv import load_dotenv
        load_dotenv()
        value = os.getenv(key)
        logger.debug(f"✓ 從 .env 載入: {key}")
        return value


def is_kaggle_environment():
    """檢測是否在 Kaggle 環境"""
    return os.path.exists('/kaggle/working')


async def setup_playwright_in_kaggle():
    """在 Kaggle 環境中設置 Playwright"""
    if is_kaggle_environment():
        logger.info("檢測到 Kaggle 環境，安裝 Playwright 瀏覽器...")
        try:
            import subprocess
            result = subprocess.run(
                ['playwright', 'install', 'chromium'],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info("✓ Playwright Chromium 已安裝")
            
            # 安裝系統依賴
            result = subprocess.run(
                ['playwright', 'install-deps', 'chromium'],
                capture_output=True,
                text=True,
                check=True
            )
            logger.info("✓ 系統依賴已安裝")
        except Exception as e:
            logger.warning(f"Playwright 安裝警告: {e}")
# ===== 結束環境兼容區域 =====


# 配置 logging
def setup_logging():
    """配置日誌系統 - 一天一個日誌文件"""
    log_filename = f'top_trader_scraper_{datetime.now().strftime("%Y%m%d")}.log'
    
    # 創建 logger
    logger = logging.getLogger('TopTraderScraper')
    logger.setLevel(logging.DEBUG)
    
    # 清除已有的 handlers
    logger.handlers.clear()
    
    # 文件 handler（詳細日誌）- 使用 append 模式
    file_handler = logging.FileHandler(log_filename, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # 控制台 handler（簡化日誌）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    
    # 添加 handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger, log_filename


# 創建全局 logger
logger, log_file = setup_logging()


# 系列配置
SERIES_CONFIG = {
    'gm': {
        'name': 'Global Macro',
        'name_zh': '全球宏觀',
        'emoji': '🌍',
        'prefix': 'gm_'
    },
    # 'ugo': {
    #     'name': 'U Got Options',
    #     'name_zh': '期權解析',
    #     'emoji': '📈',
    #     'prefix': 'ugo_'
    # }
}

# 關注的講者（支持全名或姓氏匹配）
FEATURED_SPEAKERS = {
    # 'Cem Karsan': ['cem karsan', 'karsan', 'cem'],  # 匹配全名、姓氏或名字
    # 'Cem Karsan': ['cem karsan'],  # 匹配全名、姓氏或名字
    'Alan Dunne': ['alan dunne'],
    # 可以在這裡添加更多講者
}


class GitHubImageUploader:
    """GitHub 圖片上傳器"""
    
    def __init__(self, token, repo_name):
        self.github = Github(token)
        self.repo = self.github.get_repo(repo_name)
        self.uploaded_cache = {}  # 快取已上傳的圖片
        logger.info(f"✓ GitHub 倉庫已連接: {repo_name}")
        
        # 載入已存在的檔案列表
        self._load_existing_files()
    
    def _load_existing_files(self):
        """載入 GitHub 倉庫中已存在的檔案"""
        try:
            logger.debug("載入 GitHub 倉庫現有檔案...")
            contents = self.repo.get_contents("")
            self.existing_files = {content.name for content in contents if content.type == "file"}
            logger.debug(f"  找到 {len(self.existing_files)} 個現有檔案")
        except Exception as e:
            logger.warning(f"無法載入現有檔案列表: {e}")
            self.existing_files = set()
    
    def generate_filename_from_url(self, original_url):
        """根據 URL 生成穩定的文件名（用於檢查重複）"""
        # 使用完整 URL hash 確保相同 URL 生成相同檔名
        url_hash = hashlib.md5(original_url.encode()).hexdigest()
        
        # 獲取原始文件擴展名
        ext = original_url.split('.')[-1].split('?')[0].lower()
        if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            ext = 'jpg'
        
        return f"toptrader_{url_hash}.{ext}"
    
    def check_image_exists(self, filename):
        """檢查圖片是否已存在於 GitHub"""
        return filename in self.existing_files
    
    def get_github_raw_url(self, filename):
        """獲取 GitHub raw URL"""
        return f"https://raw.githubusercontent.com/{self.repo.full_name}/main/{filename}"
    
    def upload_image(self, image_url):
        """上傳圖片到 GitHub 並返回 raw URL（避免重複上傳）"""
        try:
            # 檢查快取
            if image_url in self.uploaded_cache:
                logger.debug(f"使用快取: {image_url}")
                return self.uploaded_cache[image_url]
            
            # 生成文件名
            filename = self.generate_filename_from_url(image_url)
            github_url = self.get_github_raw_url(filename)
            
            # 檢查是否已存在（通過 API 再次確認）
            try:
                self.repo.get_contents(filename)
                logger.info(f"✓ 圖片已存在於 GitHub，跳過上傳: {filename}")
                self.uploaded_cache[image_url] = github_url
                return github_url
            except Exception:
                # 檔案不存在，繼續上傳
                pass
            
            # 下載圖片
            logger.debug(f"下載圖片: {image_url}")
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            # 上傳到 GitHub
            logger.debug(f"上傳到 GitHub: {filename}")
            try:
                self.repo.create_file(
                    path=filename,
                    message=f"Add image from Top Traders Unplugged",
                    content=response.content
                )
                logger.info(f"✓ 圖片已上傳: {filename}")
            except Exception as e:
                # 如果創建失敗（可能是並發導致的重複），嘗試獲取現有文件
                if "already exists" in str(e) or "sha" in str(e).lower():
                    logger.info(f"✓ 圖片已存在（並發檢測），使用現有文件: {filename}")
                else:
                    raise
            
            # 添加到快取
            self.uploaded_cache[image_url] = github_url
            
            return github_url
            
        except Exception as e:
            logger.error(f"上傳圖片失敗 {image_url}: {e}")
            logger.debug(traceback.format_exc())
            return image_url  # 失敗時返回原始 URL


class TopTraderScraper:
    def __init__(self, test_mode=False, enable_translation=True):
        logger.info("=" * 70)
        logger.info("初始化 Top Traders Unplugged Scraper")
        
        # 檢測環境
        if is_kaggle_environment():
            logger.info("🔍 運行環境: Kaggle Notebook")
        else:
            logger.info("🔍 運行環境: 本地端")
        
        logger.info("=" * 70)
        
        self.test_mode = test_mode
        self.enable_translation = enable_translation  # 翻譯開關
        
        if test_mode:
            logger.warning("測試模式已啟用 - 不會保存到 MongoDB")
        
        if not enable_translation:
            logger.warning("⚠️  翻譯功能已禁用 - 只會保存英文原文")
        
        # 顯示篩選配置
        series_names = ', '.join([f"{c['name']} ({c['name_zh']})" for c in SERIES_CONFIG.values()])
        speaker_names = ', '.join(FEATURED_SPEAKERS.keys())
        logger.info(f"系列篩選: {series_names}")
        logger.info(f"講者篩選: {speaker_names}")
        
        # MongoDB 配置
        logger.debug("配置 MongoDB 連接...")
        self.mongodb_url = get_secret('MONGODB_URL')
        if not self.mongodb_url:
            logger.error("MONGODB_URL 未設置")
            raise ValueError("MONGODB_URL 未設置")
        
        self.mongo_client = MongoClient(self.mongodb_url)
        self.db = self.mongo_client['top_trader_scraper']
        self.episodes_collection = self.db['episodes']
        
        # 確保 url 字段的唯一索引
        self.episodes_collection.create_index('url', unique=True)
        logger.info("✓ MongoDB 已連接")
        
        # OpenAI 配置
        logger.debug("配置 OpenAI API...")
        self.openai_api_key = get_secret('OPENAI_API_KEY')
        self.model = get_secret('MODEL') or 'gpt-4o-mini'
        if not self.openai_api_key:
            logger.error("OPENAI_API_KEY 未設置")
            raise ValueError("OPENAI_API_KEY 未設置")
        
        self.openai_client = OpenAI(api_key=self.openai_api_key)
        logger.info(f"✓ OpenAI 配置完成 (模型: {self.model})")
        
        # Gmail 配置
        logger.debug("配置 Gmail SMTP...")
        self.mail_token = get_secret('MAIL_TOKEN')
        self.app_password = get_secret('APP_PASSWORD')
        recipients_str = get_secret('RECIPIENTS') or ''
        self.recipients = [r.strip() for r in recipients_str.split(',') if r.strip()]
        
        if not self.mail_token or not self.app_password:
            logger.error("MAIL_TOKEN 或 APP_PASSWORD 未設置")
            raise ValueError("MAIL_TOKEN 或 APP_PASSWORD 未設置")
        
        # 驗證郵件地址格式
        if '@' not in self.mail_token:
            logger.error(f"MAIL_TOKEN 格式錯誤，應為完整郵箱地址: {self.mail_token}")
            raise ValueError("MAIL_TOKEN 應為完整的 Gmail 地址（例如：your_email@gmail.com）")
        
        logger.info(f"✓ Gmail 配置完成 (發件人: {self.mail_token})")
        logger.info(f"  收件人: {', '.join(self.recipients)}")
        logger.debug(f"  APP_PASSWORD 長度: {len(self.app_password) if self.app_password else 0}")
        
        # GitHub 配置
        logger.debug("配置 GitHub 圖片上傳...")
        github_token = get_secret('GITHUB_TOKEN')
        github_repo = get_secret('GITHUB_REPO') or 'chendoit/PicBed'
        
        if not github_token:
            logger.error("GITHUB_TOKEN 未設置")
            raise ValueError("GITHUB_TOKEN 未設置")
        
        self.github_uploader = GitHubImageUploader(github_token, github_repo)
    
    def is_already_scraped(self, url):
        """檢查集數是否已經抓過（通過 URL）"""
        if self.test_mode:
            logger.debug(f"測試模式 - 跳過重複檢查")
            return False
        
        exists = self.episodes_collection.find_one({'url': url}) is not None
        logger.debug(f"URL 重複檢查: {url} - {'已存在' if exists else '新集數'}")
        return exists
    
    def detect_series(self, img_src):
        """從圖片 URL 檢測系列"""
        if not img_src:
            return None
        
        # 提取檔名
        filename = img_src.split('/')[-1].lower()
        
        for series_key, config in SERIES_CONFIG.items():
            if filename.startswith(config['prefix']):
                return series_key
        
        return None
    
    def check_featured_speaker(self, title):
        """檢查標題中是否包含關注的講者"""
        title_lower = title.lower()
        for speaker_name, patterns in FEATURED_SPEAKERS.items():
            for pattern in patterns:
                if pattern in title_lower:
                    logger.debug(f"匹配到講者 '{speaker_name}' (模式: '{pattern}')")
                    return speaker_name
        return None
    
    def should_process_episode(self, series, speaker_found):
        """判斷是否應該處理這個集數"""
        # 只處理在 SERIES_CONFIG 中配置的系列 或 在 FEATURED_SPEAKERS 中配置的講者
        if series or speaker_found:
            return True
        
        return False
    
    async def scrape_latest_episodes(self):
        """抓取最新的 5 集播客 - Async 版本"""
        base_url = 'https://www.toptradersunplugged.com/'
        
        logger.info("\n" + "=" * 70)
        logger.info(f"開始抓取: Top Traders Unplugged")
        logger.info("=" * 70)
        
        async with async_playwright() as p:
            logger.debug("啟動瀏覽器 (Chromium headless)")
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                logger.info(f"訪問目標網站: {base_url}")
                await page.goto(base_url, timeout=60000)
                
                # 等待頁面加載
                await page.wait_for_selector('.latest-episodes-slider__slider__item', timeout=30000)
                
                # 找到前 5 個集數
                items = page.locator('.latest-episodes-slider__slider__item').all()
                items_list = await items
                
                logger.info(f"找到 {len(items_list)} 個集數，將處理前 5 個")
                
                episodes_to_process = []
                
                for i, item in enumerate(items_list[:5]):
                    logger.info(f"\n--- 檢查集數 {i+1}/5 ---")
                    
                    try:
                        # 提取標題和鏈接
                        title_link = item.locator('.latest-episodes-slider__slider__item__title')
                        href = await title_link.get_attribute('href')
                        title = await title_link.inner_text()
                        
                        # 提取圖片
                        img = item.locator('img')
                        img_src = await img.get_attribute('src')
                        
                        logger.info(f"標題: {title}")
                        logger.info(f"鏈接: {href}")
                        logger.info(f"圖片: {img_src}")
                        
                        # 檢測系列
                        series = self.detect_series(img_src)
                        series_info = SERIES_CONFIG.get(series, {}) if series else {}
                        
                        if series:
                            logger.info(f"系列: {series_info.get('emoji', '')} {series_info.get('name', '')} ({series_info.get('name_zh', '')})")
                        else:
                            logger.info(f"系列: 未識別")
                        
                        # 檢查講者
                        speaker_found = self.check_featured_speaker(title)
                        if speaker_found:
                            logger.info(f"特色講者: ⭐ {speaker_found}")
                        
                        # 判斷是否應該處理
                        if not self.should_process_episode(series, speaker_found):
                            logger.info("✗ 不符合篩選條件，跳過")
                            continue
                        
                        # 檢查是否已抓取
                        if self.is_already_scraped(href):
                            if self.test_mode:
                                logger.warning("[測試模式] 集數已抓取過，但繼續執行...")
                            else:
                                logger.info("✓ 集數已存在於 MongoDB 中，跳過")
                                continue
                        
                        logger.info("✓ 符合條件，加入處理列表")
                        
                        episodes_to_process.append({
                            'title': title,
                            'url': href,
                            'img_src': img_src,
                            'series': series,
                            'series_info': series_info,
                            'speaker': speaker_found
                        })
                        
                    except Exception as e:
                        logger.error(f"提取集數 {i+1} 信息失敗: {e}")
                        logger.debug(traceback.format_exc())
                        continue
                
                logger.info(f"\n共有 {len(episodes_to_process)} 個集數需要處理")
                
                # 處理每個集數
                for episode_info in episodes_to_process:
                    await self.scrape_episode(page, episode_info)
                
                await browser.close()
                
            except Exception as e:
                logger.error(f"發生錯誤: {e}")
                logger.debug(traceback.format_exc())
                try:
                    if browser:
                        await browser.close()
                except:
                    pass
    
    async def scrape_episode(self, page, episode_info):
        """抓取單個集數的完整內容"""
        logger.info("\n" + "=" * 70)
        logger.info(f"開始處理: {episode_info['title']}")
        logger.info("=" * 70)
        
        try:
            # 訪問集數頁面
            logger.info(f"訪問集數頁面: {episode_info['url']}")
            await page.goto(episode_info['url'], timeout=60000)
            await page.wait_for_timeout(2000)  # 等待 2 秒
            
            # 抓取 transcript
            logger.info("抓取 transcript...")
            try:
                transcript_section = page.locator('.single-podcast-content__transcript__preview')
                transcript_text = await transcript_section.inner_text()
                
                # 清理文字
                transcript_text = transcript_text.strip()
                
                logger.info(f"✓ Transcript 長度: {len(transcript_text)} 字符")
                
                if len(transcript_text) < 50:
                    logger.warning("Transcript 內容太少，可能抓取失敗")
                    return
                
            except Exception as e:
                logger.error(f"抓取 transcript 失敗: {e}")
                return
            
            # 上傳圖片到 GitHub
            logger.info("上傳封面圖片到 GitHub...")
            github_img_url = self.github_uploader.upload_image(episode_info['img_src'])
            
            # 翻譯 transcript（根據設定決定是否翻譯）
            if self.enable_translation:
                logger.info("翻譯 transcript...")
                translated_paragraphs = self.translate_transcript(transcript_text, episode_info['title'])
                
                if not translated_paragraphs:
                    logger.error("翻譯失敗")
                    return
            else:
                logger.info("⚠️  跳過翻譯 - 只保存英文原文")
                # 不翻譯，創建只有英文的段落
                paragraphs = transcript_text.split('\n')
                paragraphs = [p.strip() for p in paragraphs if p.strip()]
                
                translated_paragraphs = []
                for i, para in enumerate(paragraphs):
                    translated_paragraphs.append({
                        'index': i,
                        'english': para,
                        'chinese': '',  # 空的中文
                        'timestamp': None,
                        'speaker': None
                    })
            
            # 準備集數數據
            episode_data = {
                'url': episode_info['url'],
                'title': episode_info['title'],
                'img_src': github_img_url,
                'series': episode_info['series'],
                'series_name': episode_info['series_info'].get('name', ''),
                'series_name_zh': episode_info['series_info'].get('name_zh', ''),
                'series_emoji': episode_info['series_info'].get('emoji', '🎙️'),
                'featured_speaker': episode_info['speaker'],
                'transcript_en': transcript_text,
                'transcript_zh': translated_paragraphs,
                'scraped_at': datetime.now().isoformat()
            }
            
            # 保存到 MongoDB
            logger.info("\n" + "-" * 70)
            self.save_to_mongodb(episode_data)
            logger.info("-" * 70 + "\n")
            
            # 發送郵件
            logger.info("-" * 70)
            self.send_email(episode_data)
            logger.info("-" * 70 + "\n")
            
            logger.info("=" * 70)
            logger.info(f"✓ {episode_info['title']} 處理完成！")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.error(f"處理集數失敗: {e}")
            logger.debug(traceback.format_exc())

    # ==============================================================================
    # =====▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼ 核心修改區域 ▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼▼=====
    # ==============================================================================
    def translate_transcript(self, transcript_text, title, batch_size=50):
        """翻譯 transcript 為繁體中文（段落形式，分批翻譯）- (已修改，增加講者上下文追蹤)"""
        logger.info("開始翻譯 transcript (使用上下文感知邏輯)...")
        logger.debug(f"文字長度: {len(transcript_text)} 字符")

        try:
            # 保持原始分段
            paragraphs = transcript_text.split('\n')
            paragraphs = [p.strip() for p in paragraphs if p.strip()]

            logger.info(f"原始分段: {len(paragraphs)} 個段落")

            # 用於提取時間戳的函數
            def extract_timestamp(text):
                import re
                timestamp_pattern = r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*'
                timestamp_match = re.match(timestamp_pattern, text)
                if timestamp_match:
                    timestamp = timestamp_match.group(1)
                    clean_text = text[timestamp_match.end():].strip()
                    return timestamp, clean_text
                return None, text

            processed_paragraphs = []
            current_speaker = None  # 狀態變數：追蹤當前講者

            # 用於識別一行文字是否可能為講者名稱的正規表達式
            # 條件：1-3個單詞，首字母大寫，不以標點符號結尾
            speaker_name_pattern = re.compile(r'^[A-Z][a-zA-Z\s]+$')

            for para in paragraphs:
                timestamp, clean_text = extract_timestamp(para)

                # 判斷此行是否為講者名稱
                # 條件: 1. 符合正規表達式 2. 單詞數較少 (<= 3) 3. 不是一句完整的長句
                if speaker_name_pattern.match(clean_text) and len(clean_text.split()) <= 3:
                    # 檢查是否為常見的非講者短語，避免誤判
                    if clean_text.lower() not in ['outro', 'intro', 'introduction', 'conclusion']:
                        current_speaker = clean_text  # 更新當前講者
                        logger.debug(f"✓ 識別到新講者: {current_speaker}")
                        continue # 這是講者標籤，不是對話內容，跳過此行

                # 如果不是講者標籤，則視為對話內容
                # 將其與當前的講者關聯起來
                if clean_text:
                    processed_paragraphs.append({
                        'original': para,
                        'clean': clean_text,
                        'timestamp': timestamp,
                        'speaker': current_speaker  # ★ 關鍵：關聯當前講者
                    })

            logger.info(f"處理後，共有 {len(processed_paragraphs)} 段有效對話需要翻譯")

            if not processed_paragraphs:
                logger.warning("沒有找到可翻譯的對話內容。")
                return []

            # 分批翻譯（只翻譯清理後的文本）
            all_chinese_paragraphs = []
            total_batches = (len(processed_paragraphs) + batch_size - 1) // batch_size

            logger.info(f"將分成 {total_batches} 批進行翻譯 (每批 {batch_size} 段)")

            for batch_num in range(total_batches):
                start_idx = batch_num * batch_size
                end_idx = min((batch_num + 1) * batch_size, len(processed_paragraphs))
                batch_items = processed_paragraphs[start_idx:end_idx]
                batch_texts = [item['clean'] for item in batch_items]

                logger.info(f"\n--- 翻譯批次 {batch_num + 1}/{total_batches} (段落 {start_idx + 1}-{end_idx}) ---")

                paragraphs_json = json.dumps(batch_texts, ensure_ascii=False, indent=2)

                prompt = f"""請將以下 JSON 數組中的英文段落翻譯成繁體中文。

要求：
1. 必須返回一個純 JSON 數組格式: ["中文1", "中文2", ...]
2. 不要包裝在對象中，直接返回數組
3. 每個英文段落對應一個繁體中文翻譯
4. 保持數組順序和長度一致
5. 保持專業術語的準確性（特別是金融、交易術語）
6. 翻譯流暢自然，使用繁體中文

播客標題: {title}

英文段落數組:
{paragraphs_json}

請返回對應的繁體中文翻譯數組（格式示例: ["段落1翻譯", "段落2翻譯", ...]）：
"""

                logger.debug(f"調用 OpenAI API (批次 {batch_num + 1})...")
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一位專業的金融交易領域翻譯專家，擅長將英文播客內容翻譯成準確流暢的繁體中文。請嚴格返回 JSON 數組格式，不要包裝在對象中。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3
                )

                response_text = response.choices[0].message.content.strip()
                logger.debug(f"API 響應長度: {len(response_text)}")

                chinese_paragraphs = json.loads(response_text)

                if isinstance(chinese_paragraphs, dict):
                    possible_keys = ['translations', 'paragraphs', 'chinese', 'result', 'data', '翻譯結果', '翻译结果', '翻譯', '中文', '段落', '結果']
                    for key in possible_keys:
                        if key in chinese_paragraphs:
                            chinese_paragraphs = chinese_paragraphs[key]
                            logger.debug(f"從鍵 '{key}' 提取數組")
                            break

                if not isinstance(chinese_paragraphs, list):
                    logger.error(f"返回類型錯誤: {type(chinese_paragraphs)}")
                    return None

                if len(chinese_paragraphs) != len(batch_texts):
                    logger.warning(f"翻譯數量不匹配: 預期 {len(batch_texts)}，實際 {len(chinese_paragraphs)}")

                all_chinese_paragraphs.extend(chinese_paragraphs)

                logger.info(f"✓ 批次 {batch_num + 1} 翻譯完成 (Token: {response.usage.total_tokens})")

            combined_translations = []
            for i, (item, zh) in enumerate(zip(processed_paragraphs, all_chinese_paragraphs)):
                combined_translations.append({
                    'index': i,
                    'english': item['clean'],
                    'chinese': zh,
                    'timestamp': item['timestamp'],
                    'speaker': item['speaker']
                })

            logger.info(f"✓ 所有翻譯完成，共 {len(combined_translations)} 個段落")

            return combined_translations

        except Exception as e:
            logger.error(f"翻譯失敗: {e}")
            logger.debug(traceback.format_exc())
            return None
    # ==============================================================================
    # =====▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ 核心修改區域 ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲=====
    # ==============================================================================

    def save_to_mongodb(self, episode_data):
        """保存集數到 MongoDB"""
        try:
            if self.test_mode:
                logger.warning("[測試模式] 跳過保存到 MongoDB")
                return True

            logger.debug(f"保存集數到 MongoDB: {episode_data['url']}")
            result = self.episodes_collection.update_one(
                {'url': episode_data['url']},
                {'$set': episode_data},
                upsert=True
            )

            if result.upserted_id:
                logger.info(f"✓ 新集數已保存到 MongoDB (ID: {result.upserted_id})")
            else:
                logger.info(f"✓ 集數已更新到 MongoDB")

            return True
        except Exception as e:
            logger.error(f"保存到 MongoDB 失敗: {e}")
            logger.debug(traceback.format_exc())
            return False

    def send_email(self, episode_data):
        """發送郵件（不保留備份）"""
        logger.info("準備發送郵件...")

        try:
            # 創建郵件
            msg = MIMEMultipart('alternative')
            msg['From'] = self.mail_token
            msg['To'] = ', '.join(self.recipients)
            
            # 多種方法嘗試不保存備份
            msg['X-Gm-No-Archive'] = '1'  # Gmail 專用：不保存備份
            msg['Disposition-Notification-To'] = ''  # 不要求送達通知
            
            # 注意：Gmail 的「已發送」保存行為可能受帳號設定影響
            # 如果以上方法無效，請前往 Gmail 設定 → 一般設定 → 取消勾選「將副本保存在已發送郵件中」

            # 郵件主題
            series_emoji = episode_data.get('series_emoji', '🎙️')
            series_name_zh = episode_data.get('series_name_zh', '')
            subject_parts = [f"{series_emoji} Top Traders Unplugged"]

            if series_name_zh:
                subject_parts.append(f"- {series_name_zh}")

            if episode_data.get('featured_speaker'):
                subject_parts.append(f"- {episode_data['featured_speaker']}")

            subject_parts.append(f"- {episode_data['title']}")

            msg['Subject'] = ' '.join(subject_parts)

            # 生成 HTML 內容
            html_content = self._generate_html_email(episode_data)

            # 添加 HTML 部分
            part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part)

            # 發送郵件
            logger.debug("連接到 Gmail SMTP 服務器...")
            logger.debug(f"  使用帳號: {self.mail_token}")
            logger.debug(f"  密碼長度: {len(self.app_password)}")
            logger.info("  已設定不保存郵件備份到「已發送」文件夾")

            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                logger.debug("  SMTP 連接已建立")
                server.set_debuglevel(0)  # 設為 1 可看到更多調試信息
                server.login(self.mail_token, self.app_password)
                logger.debug("  登入成功")
                server.send_message(msg)
                logger.debug("  郵件已發送")

            logger.info(f"✓ 郵件已發送")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Gmail 認證失敗: {e}")
            logger.error("請檢查:")
            logger.error("  1. MAIL_TOKEN 是否為完整的 Gmail 地址")
            logger.error("  2. APP_PASSWORD 是否正確（應使用 Google App Password，而非帳號密碼）")
            logger.error("  3. 是否已啟用 Google 兩步驟驗證並生成應用程式密碼")
            logger.debug(traceback.format_exc())
            return False
        except Exception as e:
            logger.error(f"發送郵件失敗: {e}")
            logger.debug(traceback.format_exc())
            return False

    def _generate_html_email(self, episode_data):
        """生成 HTML 郵件內容"""
        html_parts = []

        series_emoji = episode_data.get('series_emoji', '🎙️')
        series_name = episode_data.get('series_name', 'Podcast')
        series_name_zh = episode_data.get('series_name_zh', '播客')
        featured_speaker = episode_data.get('featured_speaker', '')

        html_parts.append(f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.8;
            color: #333;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
            font-size: 16px;  /* 基础字体：16px（原14px） */
        }}
        .container {{
            background-color: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .series-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 1em;  /* 16px */
            margin-bottom: 15px;
            font-weight: 600;
        }}
        .speaker-badge {{
            display: inline-block;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 1em;  /* 16px */
            margin-bottom: 15px;
            margin-left: 10px;
            font-weight: 600;
        }}
        h1 {{
            color: #1a1a1a;
            border-bottom: 3px solid #667eea;
            padding-bottom: 15px;
            margin-bottom: 25px;
            font-size: 2em;  /* 32px（原28px） */
        }}
        h2 {{
            font-size: 1.5em;  /* 24px（原20px） */
        }}
        .cover-image {{
            text-align: center;
            margin: 30px 0;
        }}
        .cover-image img {{
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .meta {{
            color: #666;
            font-size: 1em;  /* 16px（原14px） */
            margin-bottom: 30px;
            padding: 15px;
            background-color: #f8f9fa;
            border-left: 4px solid #667eea;
        }}
         .transcript-section {{
             margin-top: 30px;
         }}
         .paragraph-block {{
             margin-bottom: 25px;
             padding: 0;
             background-color: transparent;
             position: relative;
         }}
         .speaker-header {{
             display: flex;
             align-items: center;
             gap: 10px;
             margin-bottom: 12px;
         }}
         .speaker-name-badge {{ /* Renamed for clarity */
             display: inline-flex;
             align-items: center;
             padding: 6px 12px;
             border-radius: 20px;
             font-weight: 600;
             font-size: 1em;  /* 16px（原14px） */
             color: white;
             box-shadow: 0 2px 4px rgba(0,0,0,0.1);
         }}
         .speaker-name-badge.speaker-1 {{
             background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
         }}
         .speaker-name-badge.speaker-2 {{
             background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
         }}
         .speaker-name-badge.speaker-3 {{
             background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
         }}
         .speaker-name-badge.speaker-4 {{
             background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
         }}
         .speaker-name-badge.speaker-5 {{
             background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
         }}
         .speaker-name-badge.speaker-default {{
             background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
         }}
         .timestamp {{
             font-size: 0.875em;  /* 14px（原12px） */
             color: #999;
             font-family: 'Courier New', monospace;
             padding: 4px 8px;
             background-color: #f5f5f5;
             border-radius: 4px;
         }}
         .content-card {{
             background-color: white;
             border-radius: 12px;
             padding: 20px;
             box-shadow: 0 2px 8px rgba(0,0,0,0.08);
             border-left: 4px solid #e0e0e0;
         }}
         .content-card.has-speaker-1 {{
             border-left-color: #667eea;
         }}
         .content-card.has-speaker-2 {{
             border-left-color: #f5576c;
         }}
         .content-card.has-speaker-3 {{
             border-left-color: #00f2fe;
         }}
         .content-card.has-speaker-4 {{
             border-left-color: #38f9d7;
         }}
         .content-card.has-speaker-5 {{
             border-left-color: #fa709a;
         }}
         .english {{
            color: #2c3e50;
            line-height: 1.7;
            margin-bottom: 15px;
            padding: 0;
            background-color: transparent;
            border-radius: 0;
            border-left: none;
            font-size: 1.0625em;  /* 17px（原15px） */
         }}
         .english::before {{
             content: "🇬🇧 ";
             font-weight: bold;
             opacity: 0.6;
         }}
         .chinese {{
             color: #34495e;
             background-color: #f8f9fa;
             padding: 15px;
             border-radius: 8px;
             line-height: 1.7;
             font-size: 1.0625em;  /* 17px（原15px） */
         }}
         .chinese::before {{
             content: "🇹🇼 ";
             font-weight: bold;
         }}
        .divider {{
            border-top: 1px solid #e0e0e0;
            margin: 20px 0;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 2px solid #e0e0e0;
            color: #999;
            font-size: 0.9em;  /* 14.4px（原12.6px） */
            text-align: center;
        }}
        a {{
            color: #667eea;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="series-badge">{series_emoji} {series_name} / {series_name_zh}</div>
        {f'<div class="speaker-badge">⭐ {featured_speaker}</div>' if featured_speaker else ''}
        <h1>{episode_data['title']}</h1>
        
        <div class="cover-image">
            <img src="{episode_data['img_src']}" alt="Episode Cover">
        </div>
        
        <div class="meta">
            <strong>原文連結 / Source:</strong> <a href="{episode_data['url']}" target="_blank">{episode_data['url']}</a><br>
            <strong>抓取時間 / Scraped:</strong> {episode_data['scraped_at']}
        </div>
        
        <div class="transcript-section">
            <h2>📝 Transcript / 文字稿</h2>
""")

        # 為不同講者分配顏色編號
        speaker_colors = {}
        color_index = 1
        previous_speaker = None  # 追蹤上一個講者

        for para in episode_data['transcript_zh']:
            # 獲取講者並分配顏色
            speaker = para.get('speaker', '')
            timestamp = para.get('timestamp', '')

            if speaker and speaker not in speaker_colors:
                speaker_colors[speaker] = color_index
                color_index = (color_index % 5) + 1  # 循環使用 1-5

            speaker_class_num = speaker_colors.get(speaker, 'default')
            speaker_class = f"speaker-{speaker_class_num}"

            # 生成頭部（講者標籤 + 時間戳）
            # ★ 只在講者改變時顯示講者標籤
            header_html = ""
            if speaker or timestamp:
                header_parts = []
                
                # 只有當講者改變時才顯示講者標籤
                if speaker and speaker != previous_speaker:
                    header_parts.append(f'<span class="speaker-name-badge {speaker_class}">{speaker}</span>')
                    previous_speaker = speaker  # 更新上一個講者
                
                # 時間戳始終顯示（如果有）
                if timestamp:
                    header_parts.append(f'<span class="timestamp">🕐 {timestamp}</span>')
                
                if header_parts:  # 只有當有內容時才創建 header
                    header_html = f'<div class="speaker-header">{"".join(header_parts)}</div>'

            card_class = f"has-speaker-{speaker_class_num}" if speaker else ""

            html_parts.append(f"""
            <div class="paragraph-block">
                {header_html}
                <div class="content-card {card_class}">
                    <div class="english">{para['english']}</div>
                    {'<div class="chinese">' + para['chinese'] + '</div>' if para.get('chinese') else ''}
                </div>
            </div>
""")

        html_parts.append("""
        </div>
        
        <div class="footer">
            此郵件由 Top Traders Unplugged 爬蟲自動發送<br>
            圖片永久保存於 GitHub | Powered by Async Playwright + OpenAI
        </div>
    </div>
</body>
</html>
""")

        return ''.join(html_parts)

    async def scrape_all(self):
        """執行爬蟲任務 - Async 版本"""
        logger.info("\n" + "=" * 70)
        logger.info("開始執行爬蟲任務")
        logger.info("=" * 70)

        # 在 Kaggle 環境中設置 Playwright
        await setup_playwright_in_kaggle()

        await self.scrape_latest_episodes()

        logger.debug("清理資源...")
        self.mongo_client.close()
        logger.info("\n✓ 所有任務完成！")


async def main_async():
    """Async main function for both local and Kaggle environments"""
    # 設置 Windows 控制台編碼
    import sys
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description='Top Traders Unplugged 播客爬蟲')
    parser.add_argument('--test', action='store_true',
                       help='測試模式：強制重新抓取，不更新 MongoDB 記錄')
    parser.add_argument('--no-translation', action='store_true',
                       help='禁用翻譯功能：只保存英文原文，不呼叫 OpenAI API')
    args = parser.parse_args()
    
    enable_translation = not args.no_translation  # 默認啟用翻譯

    logger.info("=" * 70)
    logger.info("  Top Traders Unplugged 播客爬蟲")
    logger.info("  Async Playwright + MongoDB + OpenAI + Gmail + GitHub")
    logger.info("=" * 70)
    logger.info(f"日誌文件: {log_file}")

    if args.test:
        logger.warning("\n[測試模式] 測試模式已啟用")
        logger.warning("   - 將重新抓取已抓取過的集數")
        logger.warning("   - 不會更新 MongoDB 記錄\n")
    
    if not enable_translation:
        logger.warning("\n[翻譯已禁用] 翻譯功能已關閉")
        logger.warning("   - 只會保存英文原文")
        logger.warning("   - 不會呼叫 OpenAI API")
        logger.warning("   - 郵件中不會顯示中文翻譯\n")
    
    try:
        scraper = TopTraderScraper(test_mode=args.test, enable_translation=enable_translation)
        await scraper.scrape_all()
    except ValueError as e:
        logger.error(f"配置錯誤: {e}")
        logger.info("請檢查 .env 文件配置")
        return
    except Exception as e:
        logger.error(f"程序錯誤: {e}")
        logger.debug(traceback.format_exc())

    logger.info("\n" + "=" * 70)
    logger.info("  任務結束")
    logger.info("=" * 70)
    logger.info(f"詳細日誌已保存到: {log_file}")


def main():
    """Synchronous wrapper for command-line usage"""
    try:
        # 檢測是否在已有的 event loop 中（如 Kaggle）
        loop = asyncio.get_running_loop()
        logger.error("檢測到已運行的 event loop。")
        logger.error("在 Kaggle/Jupyter 中，請直接使用: await main_async()")
        return
    except RuntimeError:
        # 沒有運行中的 loop，正常執行
        asyncio.run(main_async())


if __name__ == "__main__":
    main()