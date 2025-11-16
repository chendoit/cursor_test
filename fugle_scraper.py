#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fugle 部落格文章監控爬蟲
監控指定文章的標題和內容變化，發現更新時發送郵件通知
"""

import asyncio
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from pymongo import MongoClient
from loguru import logger
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# 配置 loguru
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True
)
logger.add(
    "logs/fugle_scraper_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
    rotation="00:00",
    retention="30 days",
    compression="zip"
)


class FugleScraper:
    """Fugle 部落格文章監控爬蟲"""

    def __init__(self, env_file: str = ".env_fugle"):
        """
        初始化爬蟲

        Args:
            env_file: 環境變數檔案路徑
        """
        # 載入環境變數
        env_path = Path(env_file)
        if not env_path.exists():
            raise FileNotFoundError(f"找不到環境變數檔案: {env_file}")

        load_dotenv(env_path)

        # 讀取設定
        self.target_url = os.getenv("TARGET_URL", "https://blog.fugle.tw/captains-newsletter-2024/")
        self.mongodb_url = os.getenv("MONGODB_URL")
        self.mail_token = os.getenv("MAIL_TOKEN")
        self.app_password = os.getenv("APP_PASSWORD")
        self.recipients = os.getenv("RECIPIENTS", "").split(",")
        self.recipients = [r.strip() for r in self.recipients if r.strip()]
        self.test_mode = os.getenv("TEST_MODE", "false").lower() == "true"

        # 驗證必要參數
        if not all([self.mongodb_url, self.mail_token, self.app_password, self.recipients]):
            raise ValueError("請確保 .env_fugle 中包含所有必要參數")

        # MongoDB 連接
        self.mongo_client = MongoClient(self.mongodb_url)
        self.db = self.mongo_client["fugle_scraper"]
        self.collection = self.db["articles"]

        logger.info("✅ Fugle 爬蟲初始化成功")
        if self.test_mode:
            logger.warning("🧪 測試模式已啟用 - 每次執行都會發送郵件")

    def calculate_hash(self, text: str) -> str:
        """計算文字的 MD5 hash"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    async def scrape_article(self) -> Optional[Dict[str, str]]:
        """
        抓取文章標題和內容

        Returns:
            包含 title, content, title_hash, content_hash 的字典，失敗則返回 None
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            try:
                logger.info(f"🌐 開始訪問: {self.target_url}")
                await page.goto(self.target_url, wait_until="networkidle", timeout=30000)

                # 提取標題
                title_element = await page.query_selector("h1.post-title")
                if not title_element:
                    logger.error("❌ 找不到標題元素")
                    return None
                title = await title_element.inner_text()
                title = title.strip()

                # 提取內容
                content_element = await page.query_selector("article.the-post")
                if not content_element:
                    logger.error("❌ 找不到內容元素")
                    return None
                content = await content_element.inner_text()
                content = content.strip()
                
                # 截斷到"立即註冊會員閱讀全文"之前
                cutoff_text = "立即註冊會員閱讀全文"
                if cutoff_text in content:
                    content = content.split(cutoff_text)[0].strip()
                    logger.info("✂️  已截斷內容到註冊提示之前")

                # 計算 hash
                title_hash = self.calculate_hash(title)
                content_hash = self.calculate_hash(content)

                logger.info(f"✅ 成功抓取文章")
                logger.info(f"📝 標題: {title[:300]}...")
                logger.info(f"📄 內容長度: {len(content)} 字元")

                return {
                    "title": title,
                    "content": content,
                    "title_hash": title_hash,
                    "content_hash": content_hash,
                    "url": self.target_url,
                    "scraped_at": datetime.now()
                }

            except Exception as e:
                logger.error(f"❌ 抓取失敗: {e}")
                return None

            finally:
                await browser.close()

    def get_last_record(self) -> Optional[Dict]:
        """從 MongoDB 獲取上次記錄"""
        return self.collection.find_one(
            {"url": self.target_url},
            sort=[("scraped_at", -1)]
        )

    def save_record(self, article: Dict) -> None:
        """保存記錄到 MongoDB，只保留最新 60 筆"""
        self.collection.insert_one(article)
        logger.info("💾 已保存記錄到 MongoDB")
        
        # 檢查記錄數量，只保留最新 60 筆
        total_records = self.collection.count_documents({"url": self.target_url})
        if total_records > 60:
            # 找出最舊的記錄並刪除
            records_to_delete = total_records - 60
            oldest_records = self.collection.find(
                {"url": self.target_url}
            ).sort("scraped_at", 1).limit(records_to_delete)
            
            delete_ids = [record["_id"] for record in oldest_records]
            if delete_ids:
                result = self.collection.delete_many({"_id": {"$in": delete_ids}})
                logger.info(f"🗑️  已刪除 {result.deleted_count} 筆舊記錄（保留最新 60 筆）")

    def send_email(self, article: Dict, changes: Dict[str, bool]) -> bool:
        """
        發送郵件通知

        Args:
            article: 文章資料
            changes: 變更標記 {"title": bool, "content": bool}

        Returns:
            是否成功發送
        """
        try:
            # 建立郵件
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"📢 Fugle 文章更新通知 - {article['title'][:30]}..."
            msg["From"] = self.mail_token
            msg["To"] = ", ".join(self.recipients)

            # 生成變更標記
            change_tags = []
            if changes.get("title"):
                change_tags.append("標題已更新")
            if changes.get("content"):
                change_tags.append("內容已更新")
            change_text = " / ".join(change_tags)

            # HTML 郵件內容
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{
                        font-family: "Microsoft JhengHei", Arial, sans-serif;
                        line-height: 1.6;
                        color: #333;
                        max-width: 800px;
                        margin: 0 auto;
                        padding: 20px;
                        background-color: #f5f5f5;
                    }}
                    .container {{
                        background-color: white;
                        border-radius: 8px;
                        padding: 30px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    }}
                    .header {{
                        border-bottom: 3px solid #0066cc;
                        padding-bottom: 15px;
                        margin-bottom: 25px;
                    }}
                    h1 {{
                        color: #0066cc;
                        margin: 0;
                        font-size: 24px;
                    }}
                    .badge {{
                        display: inline-block;
                        background-color: #ff6b6b;
                        color: white;
                        padding: 5px 12px;
                        border-radius: 15px;
                        font-size: 12px;
                        margin-top: 10px;
                    }}
                    .url-info {{
                        background-color: #e3f2fd;
                        padding: 10px 15px;
                        border-radius: 5px;
                        margin: 15px 0;
                        font-size: 14px;
                        word-break: break-all;
                    }}
                    .url-info a {{
                        color: #0066cc;
                        text-decoration: none;
                        font-weight: bold;
                    }}
                    .section {{
                        margin: 20px 0;
                        padding: 15px;
                        background-color: #f9f9f9;
                        border-left: 4px solid #0066cc;
                    }}
                    .section h2 {{
                        margin-top: 0;
                        color: #0066cc;
                        font-size: 18px;
                    }}
                    .content {{
                        white-space: pre-wrap;
                        word-wrap: break-word;
                        padding: 10px;
                        background-color: white;
                        border-radius: 4px;
                        line-height: 1.8;
                    }}
                    .footer {{
                        margin-top: 30px;
                        padding-top: 20px;
                        border-top: 1px solid #ddd;
                        text-align: center;
                        color: #666;
                        font-size: 12px;
                    }}
                    .button {{
                        display: inline-block;
                        background-color: #0066cc;
                        color: white;
                        padding: 12px 30px;
                        text-decoration: none;
                        border-radius: 5px;
                        margin-top: 15px;
                        font-weight: bold;
                    }}
                    .button:hover {{
                        background-color: #0052a3;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>📢 Fugle 文章更新通知</h1>
                        <span class="badge">{change_text}</span>
                    </div>
                    
                    <div class="url-info">
                        🔗 監控網址: <a href="{article['url']}">{article['url']}</a>
                    </div>
                    
                    <div class="section">
                        <h2>📝 文章標題</h2>
                        <div class="content">{article['title']}</div>
                    </div>
                    
                    <div class="section">
                        <h2>📄 文章完整內容</h2>
                        <div class="content">{article['content']}</div>
                    </div>
                    
                    <div style="text-align: center;">
                        <a href="{article['url']}" class="button">前往原文閱讀</a>
                    </div>
                    
                    <div class="footer">
                        <p>⏰ 檢測時間: {article['scraped_at'].strftime('%Y-%m-%d %H:%M:%S')}</p>
                        <p>🤖 此郵件由 Fugle 監控爬蟲自動發送</p>
                    </div>
                </div>
            </body>
            </html>
            """

            # 純文字版本
            text = f"""
Fugle 文章更新通知
{'='*50}

變更: {change_text}

監控網址: {article['url']}

標題:
{article['title']}

文章完整內容:
{article['content']}

{'='*50}
檢測時間: {article['scraped_at'].strftime('%Y-%m-%d %H:%M:%S')}
此郵件由 Fugle 監控爬蟲自動發送
            """

            # 附加內容
            part1 = MIMEText(text, "plain", "utf-8")
            part2 = MIMEText(html, "html", "utf-8")
            msg.attach(part1)
            msg.attach(part2)

            # 發送郵件
            logger.info("📧 開始發送郵件...")
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self.mail_token, self.app_password)
                server.send_message(msg)

            logger.info(f"✅ 郵件已發送至: {', '.join(self.recipients)}")
            return True

        except Exception as e:
            logger.error(f"❌ 郵件發送失敗: {e}")
            return False

    async def run(self) -> None:
        """執行監控流程"""
        logger.info("🚀 開始執行 Fugle 文章監控")

        # 抓取文章
        article = await self.scrape_article()
        if not article:
            logger.error("❌ 無法抓取文章，結束執行")
            return

        # 獲取上次記錄
        last_record = self.get_last_record()

        if not last_record:
            # 首次執行，保存記錄但不發送郵件
            logger.info("🆕 首次執行，保存初始記錄")
            self.save_record(article)
            logger.info("✅ 初始記錄已保存，下次執行時會比對變更")
            return

        # 比對 hash
        changes = {
            "title": article["title_hash"] != last_record["title_hash"],
            "content": article["content_hash"] != last_record["content_hash"]
        }

        has_changes = changes["title"] or changes["content"]

        if self.test_mode:
            # 測試模式：總是發送郵件
            logger.info("🧪 測試模式 - 強制發送郵件")
            if has_changes:
                logger.info("🔔 （實際上文章有變更）")
                if changes["title"]:
                    logger.info("  ✏️  標題已變更")
                if changes["content"]:
                    logger.info("  📝 內容已變更")
            else:
                logger.info("📌 （實際上文章無變更，但仍發送測試郵件）")
                # 在測試模式下，即使沒變更也標記為有變更以發送郵件
                changes["content"] = True
            
            # 保存新記錄
            self.save_record(article)
            
            # 發送郵件
            self.send_email(article, changes)
            
        elif has_changes:
            # 正常模式：檢測到變更才發送
            logger.info("🔔 檢測到文章更新！")
            if changes["title"]:
                logger.info("  ✏️  標題已變更")
            if changes["content"]:
                logger.info("  📝 內容已變更")

            # 保存新記錄
            self.save_record(article)

            # 發送郵件
            self.send_email(article, changes)
        else:
            logger.info("✅ 文章無變更")

    def close(self):
        """關閉資源"""
        if hasattr(self, 'mongo_client'):
            self.mongo_client.close()
            logger.info("🔌 MongoDB 連接已關閉")


async def main():
    """主函數"""
    scraper = None
    try:
        scraper = FugleScraper()
        await scraper.run()
    except Exception as e:
        logger.error(f"❌ 執行失敗: {e}")
        sys.exit(1)
    finally:
        if scraper:
            scraper.close()


if __name__ == "__main__":
    asyncio.run(main())

