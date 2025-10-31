"""
Citadel Securities 新聞爬蟲
- 支持多個系列：Global Market Intelligence、Macro Thoughts
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
from dotenv import load_dotenv
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


# 配置 logging
def setup_logging():
    """配置日誌系統 - 一天一個日誌文件"""
    log_filename = f'scraper_{datetime.now().strftime("%Y%m%d")}.log'
    
    # 創建 logger
    logger = logging.getLogger('CitadelScraper')
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
    'global-market-intelligence': {
        'name': 'Global Market Intelligence',
        'name_zh': '全球市場情報',
        'url': 'https://www.citadelsecurities.com/news-and-insights/series/global-market-intelligence/',
        'emoji': '📊'
    },
    'macro-thoughts': {
        'name': 'Macro Thoughts',
        'name_zh': '宏觀思考',
        'url': 'https://www.citadelsecurities.com/news-and-insights/series/macro-thoughts/',
        'emoji': '🌍'
    }
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
        
        return f"citadel_{url_hash}.{ext}"
    
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
            
            # 檢查是否已存在
            if self.check_image_exists(filename):
                github_url = self.get_github_raw_url(filename)
                logger.info(f"✓ 圖片已存在，跳過上傳: {filename}")
                self.uploaded_cache[image_url] = github_url
                return github_url
            
            # 下載圖片
            logger.debug(f"下載圖片: {image_url}")
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            # 上傳到 GitHub
            logger.debug(f"上傳到 GitHub: {filename}")
            self.repo.create_file(
                path=filename,
                message=f"Add image from Citadel Securities",
                content=response.content
            )
            
            # 添加到已存在列表和快取
            self.existing_files.add(filename)
            github_url = self.get_github_raw_url(filename)
            self.uploaded_cache[image_url] = github_url
            
            logger.info(f"✓ 圖片已上傳: {filename}")
            
            return github_url
            
        except Exception as e:
            logger.error(f"上傳圖片失敗 {image_url}: {e}")
            logger.debug(traceback.format_exc())
            return image_url  # 失敗時返回原始 URL


class ContentElement:
    """內容元素（文字或圖片）"""
    def __init__(self, element_type, content, order):
        self.type = element_type  # 'text' or 'image'
        self.content = content
        self.order = order


class CitadelScraper:
    def __init__(self, test_mode=False, series_list=None):
        # 加載環境變量
        load_dotenv()
        logger.info("=" * 70)
        logger.info("初始化 Citadel Scraper")
        logger.info("=" * 70)
        
        self.test_mode = test_mode
        self.series_list = series_list or ['global-market-intelligence']  # 默認抓取 GMI
        
        if test_mode:
            logger.warning("測試模式已啟用 - 不會保存到 MongoDB")
        
        logger.info(f"將抓取以下系列: {', '.join([SERIES_CONFIG[s]['name'] for s in self.series_list])}")
        
        # MongoDB 配置
        logger.debug("配置 MongoDB 連接...")
        self.mongodb_url = os.getenv('MONGODB_URL')
        if not self.mongodb_url:
            logger.error("MONGODB_URL 未在 .env 文件中設置")
            raise ValueError("MONGODB_URL 未在 .env 文件中設置")
        
        self.mongo_client = MongoClient(self.mongodb_url)
        self.db = self.mongo_client['citadel_scraper']
        self.articles_collection = self.db['articles']
        
        # 確保 href 字段的唯一索引
        self.articles_collection.create_index('url', unique=True)
        logger.info("✓ MongoDB 已連接")
        
        # OpenAI 配置
        logger.debug("配置 OpenAI API...")
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        self.model = os.getenv('MODEL', 'gpt-4o-mini')
        if not self.openai_api_key:
            logger.error("OPENAI_API_KEY 未在 .env 文件中設置")
            raise ValueError("OPENAI_API_KEY 未在 .env 文件中設置")
        
        self.openai_client = OpenAI(api_key=self.openai_api_key)
        logger.info(f"✓ OpenAI 配置完成 (模型: {self.model})")
        
        # Gmail 配置
        logger.debug("配置 Gmail SMTP...")
        self.mail_token = os.getenv('MAIL_TOKEN')
        self.app_password = os.getenv('APP_PASSWORD')
        self.recipients = os.getenv('RECIPIENTS', '').split(',')
        
        if not self.mail_token or not self.app_password:
            logger.error("MAIL_TOKEN 或 APP_PASSWORD 未在 .env 文件中設置")
            raise ValueError("MAIL_TOKEN 或 APP_PASSWORD 未在 .env 文件中設置")
        
        logger.info(f"✓ Gmail 配置完成 (發件人: {self.mail_token})")
        logger.info(f"  收件人: {', '.join(self.recipients)}")
        
        # GitHub 配置
        logger.debug("配置 GitHub 圖片上傳...")
        github_token = os.getenv('GITHUB_TOKEN')
        github_repo = os.getenv('GITHUB_REPO', 'chendoit/PicBed')
        
        if not github_token:
            logger.error("GITHUB_TOKEN 未在 .env 文件中設置")
            raise ValueError("GITHUB_TOKEN 未在 .env 文件中設置")
        
        self.github_uploader = GitHubImageUploader(github_token, github_repo)
    
    def is_already_scraped(self, url):
        """檢查文章是否已經抓過（通過 URL）"""
        if self.test_mode:
            logger.debug(f"測試模式 - 跳過重複檢查")
            return False
        
        exists = self.articles_collection.find_one({'url': url}) is not None
        logger.debug(f"URL 重複檢查: {url} - {'已存在' if exists else '新文章'}")
        return exists
    
    async def scrape_content_with_order(self, page):
        """按順序抓取內容（文字和圖片）- Async 版本"""
        logger.info("按順序抓取文章內容...")
        
        try:
            content_elements = []
            order = 0
            
            # 獲取主要內容區域
            content_section = page.locator('div.section-intro.is-top-padding.is-bottom-padding').first
            
            # 使用 JavaScript 獲取所有子元素（文字段落和圖片）
            elements = await content_section.evaluate('''
                (element) => {
                    const result = [];
                    const walker = document.createTreeWalker(
                        element,
                        NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );
                    
                    let node;
                    let currentParagraph = '';
                    
                    while (node = walker.nextNode()) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            const text = node.textContent.trim();
                            if (text) {
                                currentParagraph += text + ' ';
                            }
                        } else if (node.nodeName === 'IMG') {
                            // 先保存當前段落
                            if (currentParagraph.trim()) {
                                result.push({type: 'text', content: currentParagraph.trim()});
                                currentParagraph = '';
                            }
                            // 添加圖片
                            result.push({type: 'image', content: node.src});
                        } else if (node.nodeName === 'P' || node.nodeName === 'DIV') {
                            // 段落結束
                            if (currentParagraph.trim()) {
                                result.push({type: 'text', content: currentParagraph.trim()});
                                currentParagraph = '';
                            }
                        }
                    }
                    
                    // 保存最後的段落
                    if (currentParagraph.trim()) {
                        result.push({type: 'text', content: currentParagraph.trim()});
                    }
                    
                    return result;
                }
            ''')
            
            # 轉換為 ContentElement 對象
            for element in elements:
                content_elements.append(
                    ContentElement(
                        element_type=element['type'],
                        content=element['content'],
                        order=order
                    )
                )
                order += 1
            
            logger.info(f"✓ 找到 {len(content_elements)} 個內容元素")
            
            # 統計
            text_count = sum(1 for e in content_elements if e.type == 'text')
            image_count = sum(1 for e in content_elements if e.type == 'image')
            logger.debug(f"  文字段落: {text_count}, 圖片: {image_count}")
            
            return content_elements
            
        except Exception as e:
            logger.error(f"抓取內容失敗: {e}")
            logger.debug(traceback.format_exc())
            return []
    
    def translate_paragraphs(self, text_paragraphs, title):
        """翻譯文字段落為繁體中文"""
        logger.info("開始翻譯文字段落...")
        logger.debug(f"共 {len(text_paragraphs)} 個段落")
        
        try:
            # 準備 JSON list
            paragraphs_json = json.dumps(text_paragraphs, ensure_ascii=False, indent=2)
            
            prompt = f"""請將以下 JSON 數組中的英文段落翻譯成繁體中文。

要求：
1. 必須返回一個純 JSON 數組格式: ["中文1", "中文2", ...]
2. 不要包裝在對象中，直接返回數組
3. 每個英文段落對應一個繁體中文翻譯
4. 保持數組順序和長度一致
5. 保持專業術語的準確性（特別是金融術語）
6. 翻譯流暢自然，使用繁體中文

文章標題: {title}

英文段落數組:
{paragraphs_json}

請返回對應的繁體中文翻譯數組（格式示例: ["段落1翻譯", "段落2翻譯", ...]）：
"""
            
            logger.debug("調用 OpenAI API...")
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一位專業的金融領域翻譯專家，擅長將英文金融文章翻譯成準確流暢的繁體中文。請嚴格返回 JSON 數組格式，不要包裝在對象中。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3
            )
            
            response_text = response.choices[0].message.content.strip()
            logger.debug(f"API 響應長度: {len(response_text)}")
            
            # 解析 JSON
            chinese_paragraphs = json.loads(response_text)
            
            # 如果返回的是對象，嘗試提取數組
            if isinstance(chinese_paragraphs, dict):
                possible_keys = [
                    'translations', 'paragraphs', 'chinese', 'result', 'data',
                    '翻譯結果', '翻译结果', '翻譯', '中文', '段落', '結果'
                ]
                for key in possible_keys:
                    if key in chinese_paragraphs:
                        chinese_paragraphs = chinese_paragraphs[key]
                        logger.debug(f"從鍵 '{key}' 提取數組")
                        break
            
            # 確保是列表
            if not isinstance(chinese_paragraphs, list):
                logger.error(f"返回類型錯誤: {type(chinese_paragraphs)}")
                return None
            
            logger.info(f"✓ 翻譯完成 (Token: {response.usage.total_tokens})")
            
            return chinese_paragraphs
            
        except Exception as e:
            logger.error(f"翻譯失敗: {e}")
            logger.debug(traceback.format_exc())
            return None
    
    def process_content_elements(self, content_elements, title):
        """處理內容元素：翻譯文字、上傳圖片"""
        logger.info("\n" + "-" * 70)
        logger.info("處理內容元素...")
        
        # 分離文字和圖片
        text_paragraphs = []
        text_indices = []
        
        for i, element in enumerate(content_elements):
            if element.type == 'text':
                text_paragraphs.append(element.content)
                text_indices.append(i)
        
        # 翻譯文字
        chinese_paragraphs = self.translate_paragraphs(text_paragraphs, title)
        if not chinese_paragraphs:
            logger.error("翻譯失敗")
            return None
        
        # 創建翻譯映射
        translation_map = dict(zip(text_indices, chinese_paragraphs))
        
        # 上傳圖片到 GitHub
        logger.info("\n上傳圖片到 GitHub...")
        for element in content_elements:
            if element.type == 'image':
                original_url = element.content
                github_url = self.github_uploader.upload_image(original_url)
                element.content = github_url  # 替換為 GitHub URL
        
        logger.info("-" * 70 + "\n")
        
        return translation_map
    
    def save_to_mongodb(self, article_data):
        """保存文章到 MongoDB"""
        try:
            if self.test_mode:
                logger.warning("[測試模式] 跳過保存到 MongoDB")
                return True
            
            logger.debug(f"保存文章到 MongoDB: {article_data['url']}")
            result = self.articles_collection.update_one(
                {'url': article_data['url']},
                {'$set': article_data},
                upsert=True
            )
            
            if result.upserted_id:
                logger.info(f"✓ 新文章已保存到 MongoDB (ID: {result.upserted_id})")
            else:
                logger.info(f"✓ 文章已更新到 MongoDB")
            
            return True
        except Exception as e:
            logger.error(f"保存到 MongoDB 失敗: {e}")
            logger.debug(traceback.format_exc())
            return False
    
    def send_email(self, article_data, content_elements, translation_map):
        """發送郵件"""
        logger.info("準備發送郵件...")
        
        try:
            # 創建郵件
            msg = MIMEMultipart('alternative')
            msg['From'] = self.mail_token
            msg['To'] = ', '.join(self.recipients)
            
            # 郵件主題包含系列名稱
            series_emoji = article_data.get('series_emoji', '📰')
            series_name_zh = article_data.get('series_name_zh', '')
            msg['Subject'] = f"{series_emoji} Citadel Securities - {series_name_zh} - {article_data['title']}"
            
            # 生成 HTML 內容
            html_content = self._generate_html_email(article_data, content_elements, translation_map)
            
            # 添加 HTML 部分
            part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part)
            
            # 發送郵件
            logger.debug("連接到 Gmail SMTP 服務器...")
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.mail_token, self.app_password)
                server.send_message(msg)
            
            logger.info(f"✓ 郵件已發送")
            return True
            
        except Exception as e:
            logger.error(f"發送郵件失敗: {e}")
            logger.debug(traceback.format_exc())
            return False
    
    def _generate_html_email(self, article_data, content_elements, translation_map):
        """生成 HTML 郵件內容（使用 GitHub 圖片連結）"""
        html_parts = []
        
        series_emoji = article_data.get('series_emoji', '📰')
        series_name = article_data.get('series_name', 'News')
        series_name_zh = article_data.get('series_name_zh', '新聞')
        
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
            font-size: 0.9em;
            margin-bottom: 15px;
            font-weight: 600;
        }}
        h1 {{
            color: #1a1a1a;
            border-bottom: 3px solid #0066cc;
            padding-bottom: 15px;
            margin-bottom: 25px;
        }}
        .meta {{
            color: #666;
            font-size: 0.95em;
            margin-bottom: 30px;
            padding: 15px;
            background-color: #f8f9fa;
            border-left: 4px solid #0066cc;
        }}
        .content-element {{
            margin-bottom: 25px;
        }}
        .text-paragraph {{
            margin-bottom: 10px;
        }}
        .english {{
            color: #2c3e50;
            line-height: 1.7;
            margin-bottom: 10px;
        }}
        .chinese {{
            color: #34495e;
            background-color: #f0f7ff;
            padding: 12px;
            border-radius: 5px;
            border-left: 4px solid #0066cc;
            line-height: 1.7;
            margin-bottom: 15px;
        }}
        .chinese::before {{
            content: "🇹🇼 ";
            font-weight: bold;
        }}
        .image-container {{
            text-align: center;
            margin: 30px 0;
        }}
        .article-image {{
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .image-caption {{
            color: #999;
            font-size: 0.85em;
            margin-top: 8px;
            font-style: italic;
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
            font-size: 0.9em;
            text-align: center;
        }}
        a {{
            color: #0066cc;
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
        <h1>{article_data['title']}</h1>
        
        <div class="meta">
            <strong>發布日期 / Date:</strong> {article_data['date']}<br>
            <strong>原文連結 / Source:</strong> <a href="{article_data['url']}" target="_blank">{article_data['url']}</a><br>
            <strong>抓取時間 / Scraped:</strong> {article_data['scraped_at']}
        </div>
""")
        
        # 按順序添加內容元素（確保使用 GitHub 連結）
        text_index = 0
        for element in content_elements:
            if element.type == 'text':
                # 文字段落（英文 + 繁體中文）
                english_text = element.content
                chinese_text = translation_map.get(text_index, "")
                
                html_parts.append(f"""
        <div class="content-element text-paragraph">
            <div class="english">{english_text}</div>
            <div class="chinese">{chinese_text}</div>
        </div>
        <div class="divider"></div>
""")
                text_index += 1
                
            elif element.type == 'image':
                # 圖片（確保使用 GitHub raw URL）
                github_url = element.content
                
                # 驗證是否為 GitHub URL
                if 'raw.githubusercontent.com' in github_url or 'github.com' in github_url:
                    caption = "圖片來自 GitHub（永久保存）"
                else:
                    caption = "原始圖片連結"
                
                html_parts.append(f"""
        <div class="content-element image-container">
            <img src="{github_url}" alt="Article Image" class="article-image">
            <div class="image-caption">{caption}</div>
        </div>
""")
        
        html_parts.append("""
        <div class="footer">
            此郵件由 Citadel Securities 新聞爬蟲自動發送<br>
            圖片永久保存於 GitHub | Powered by Async Playwright + OpenAI
        </div>
    </div>
</body>
</html>
""")
        
        return ''.join(html_parts)
    
    async def scrape_series(self, series_key):
        """抓取單個系列的最新文章 - Async 版本"""
        series_config = SERIES_CONFIG[series_key]
        base_url = series_config['url']
        series_name = series_config['name']
        series_name_zh = series_config['name_zh']
        series_emoji = series_config['emoji']
        
        logger.info("\n" + "=" * 70)
        logger.info(f"開始抓取系列: {series_emoji} {series_name} ({series_name_zh})")
        logger.info("=" * 70)
        
        async with async_playwright() as p:
            logger.debug("啟動瀏覽器 (Chromium headless)")
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                logger.info(f"訪問目標網站: {base_url}")
                await page.goto(base_url, timeout=60000)
                
                # 等待頁面加載
                await page.wait_for_selector('.post-listing__list', timeout=30000)
                
                # 找到第一個文章
                first_card = page.locator('.post-listing__list .post-listing__box-card').first
                link = first_card.locator('.post-listing__box-card__link a').first
                aria_label = await link.get_attribute('aria-label')
                href = await link.get_attribute('href')
                
                logger.info(f"找到文章: {aria_label}")
                logger.info(f"鏈接: {href}")
                
                # 檢查是否已經抓過
                if self.is_already_scraped(href):
                    if self.test_mode:
                        logger.warning("[測試模式] 文章已抓取過，但繼續執行...")
                    else:
                        logger.info("✓ 文章已存在於 MongoDB 中，跳過")
                        await browser.close()
                        return
                
                # 訪問文章頁面
                logger.info("訪問文章頁面...")
                await page.goto(href, timeout=60000)
                await page.wait_for_timeout(2000)  # 等待 2 秒
                
                # 抓取標題
                try:
                    heading = page.locator('span.heading-inner').first
                    title = await heading.inner_text()
                    logger.info(f"標題: {title}")
                except:
                    title = aria_label
                
                # 抓取日期
                try:
                    date_element = page.locator('p.page-section__article-header__date').first
                    date = await date_element.inner_text()
                    logger.info(f"日期: {date}")
                except:
                    date = ""
                
                # 按順序抓取內容（文字和圖片）
                content_elements = await self.scrape_content_with_order(page)
                
                # ✅ 抓取完成，立即關閉瀏覽器
                logger.debug("✓ 內容抓取完成，關閉瀏覽器")
                await browser.close()
                
                if not content_elements:
                    logger.error("內容抓取失敗")
                    return
                
                # 處理內容：翻譯文字、上傳圖片到 GitHub
                translation_map = self.process_content_elements(content_elements, title)
                if translation_map is None:
                    logger.error("內容處理失敗")
                    return
                
                # 準備文章數據
                article_data = {
                    'url': href,
                    'aria_label': aria_label,
                    'title': title,
                    'date': date,
                    'series': series_key,
                    'series_name': series_name,
                    'series_name_zh': series_name_zh,
                    'series_emoji': series_emoji,
                    'content_elements': [
                        {'type': e.type, 'content': e.content, 'order': e.order}
                        for e in content_elements
                    ],
                    'scraped_at': datetime.now().isoformat(),
                    'translated': True
                }
                
                # 保存到 MongoDB
                logger.info("\n" + "-" * 70)
                self.save_to_mongodb(article_data)
                logger.info("-" * 70 + "\n")
                
                # 發送郵件
                logger.info("-" * 70)
                self.send_email(article_data, content_elements, translation_map)
                logger.info("-" * 70 + "\n")
                
                logger.info("=" * 70)
                logger.info(f"✓ {series_name} 抓取完成！")
                logger.info("=" * 70)
                
            except Exception as e:
                logger.error(f"發生錯誤: {e}")
                logger.debug(traceback.format_exc())
                # 如果瀏覽器還開著，關閉它
                try:
                    if browser:
                        await browser.close()
                except:
                    pass
    
    async def scrape_all(self):
        """抓取所有配置的系列 - Async 版本"""
        logger.info("\n" + "=" * 70)
        logger.info("開始執行爬蟲任務")
        logger.info(f"系列數量: {len(self.series_list)}")
        logger.info("=" * 70)
        
        for series_key in self.series_list:
            await self.scrape_series(series_key)
        
        logger.debug("清理資源...")
        self.mongo_client.close()
        logger.info("\n✓ 所有系列抓取完成！")


def main():
    # 設置 Windows 控制台編碼
    import sys
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    
    parser = argparse.ArgumentParser(description='Citadel Securities 新聞爬蟲')
    parser.add_argument('--test', action='store_true', 
                       help='測試模式：強制重新抓取，不更新 MongoDB 記錄')
    parser.add_argument('--series', nargs='+', 
                       choices=['global-market-intelligence', 'macro-thoughts', 'all'],
                       default=['all'],
                       help='要抓取的系列（可多選）：global-market-intelligence, macro-thoughts, all')
    args = parser.parse_args()
    
    # 處理系列選擇
    if 'all' in args.series:
        series_list = list(SERIES_CONFIG.keys())
    else:
        series_list = args.series
    
    logger.info("=" * 70)
    logger.info("  Citadel Securities 新聞爬蟲")
    logger.info("  Async Playwright + MongoDB + OpenAI + Gmail + GitHub")
    logger.info("=" * 70)
    logger.info(f"日誌文件: {log_file}")
    
    if args.test:
        logger.warning("\n[測試模式] 測試模式已啟用")
        logger.warning("   - 將重新抓取已抓取過的文章")
        logger.warning("   - 不會更新 MongoDB 記錄\n")
    
    try:
        scraper = CitadelScraper(test_mode=args.test, series_list=series_list)
        asyncio.run(scraper.scrape_all())
    except ValueError as e:
        logger.error(f"配置錯誤: {e}")
        logger.info("請檢查 .env 文件配置，參考 env_template.txt")
        return
    except Exception as e:
        logger.error(f"程序錯誤: {e}")
        logger.debug(traceback.format_exc())
    
    logger.info("\n" + "=" * 70)
    logger.info("  任務結束")
    logger.info("=" * 70)
    logger.info(f"詳細日誌已保存到: {log_file}")


if __name__ == "__main__":
    main()
