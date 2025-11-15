#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桃園市立圖書館 HyRead 電子書自動借閱工具
使用 Playwright 和 Google Gemini API 進行驗證碼辨識和自動借閱
"""

import asyncio
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict
from urllib.parse import urljoin
import hashlib

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, Browser, FrameLocator
import httpx
from loguru import logger

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# 配置 loguru
logger.remove()  # 移除默認 handler
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True
)
logger.add(
    "logs/hyread_scraper_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
    rotation="00:00",
    retention="7 days",
    compression="zip"
)


class HyReadScraper:
    """桃園市立圖書館 HyRead 電子書自動借閱類別"""

    def __init__(self, env_file: str = ".env_hyread"):
        """
        初始化借閱器

        Args:
            env_file: 環境變數檔案路徑
        """
        # 載入環境變數
        env_path = Path(env_file)
        if not env_path.exists():
            raise FileNotFoundError(f"找不到環境變數檔案: {env_file}")

        load_dotenv(env_path)

        # 讀取設定
        self.account = os.getenv("HYREAD_ACCOUNT")
        self.password = os.getenv("HYREAD_PASSWORD")
        self.google_api_key = os.getenv("OPENAI_API_KEY")
        self.model_name = os.getenv("OPENAI_MODEL", "gemini-2.0-flash-exp")
        self.book_id = os.getenv("HYREAD_BOOK_ID", "279235")  # 預設書籍 ID
        self.captcha_mode = os.getenv("CAPTCHA_MODE", "manual").lower()  # 驗證碼模式
        self.enable_scraping = os.getenv("ENABLE_SCRAPING", "true").lower() == "true"  # 是否啟用爬蟲
        self.max_pages = int(os.getenv("MAX_PAGES", "999"))  # 最大爬取頁數
        self.download_images = os.getenv("DOWNLOAD_IMAGES", "true").lower() == "true"  # 是否下載圖片
        self.image_only_mode = os.getenv("IMAGE_ONLY_MODE", "false").lower() == "true"  # 純圖片書籍模式
        
        # 翻頁策略相關
        self.smart_page_turn = os.getenv("SMART_PAGE_TURN", "true").lower() == "true"  # 是否啟用智能翻頁
        self.pages_per_turn = int(os.getenv("PAGES_PER_TURN", "3"))  # 固定翻頁數量（當智能翻頁關閉時）
        
        # 翻頁按鍵設定
        page_turn_key = os.getenv("PAGE_TURN_KEY", "ArrowRight")
        # 驗證按鍵值是否有效
        valid_keys = ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"]
        if page_turn_key not in valid_keys:
            logger.warning(f"⚠️  無效的翻頁按鍵: {page_turn_key}，使用預設值 ArrowRight")
            page_turn_key = "ArrowRight"
        self.page_turn_key = page_turn_key

        # 圖片下載相關
        self.images_dir = None
        self.downloaded_images = {}  # URL -> 本地路徑映射
        self.canvas_hashes = set()  # 用於 Canvas 去重的 MD5 hash 集合
        self.book_title = None  # 書名

        # 驗證必要參數
        if not all([self.account, self.password]):
            raise ValueError("請確保 .env_hyread 中包含 HYREAD_ACCOUNT 和 HYREAD_PASSWORD")

        # 如果使用自動模式，需要檢查 API Key 和 Gemini SDK
        if self.captcha_mode == "auto":
            if not self.google_api_key:
                raise ValueError("自動模式需要 OPENAI_API_KEY，或將 CAPTCHA_MODE 設為 manual")

            if not HAS_GEMINI:
                raise ImportError(
                    "請安裝 Google Gemini SDK:\n"
                    "pip install google-generativeai Pillow"
                )

            # 設定 Gemini API
            genai.configure(api_key=self.google_api_key)

            # 初始化模型
            self.model = genai.GenerativeModel(self.model_name)

        # URL 設定
        self.login_url = "https://tycccgov.ebook.hyread.com.tw/Template/RWD3.0/liblogin.jsp"
        self.base_url = "https://tycccgov.ebook.hyread.com.tw"

        logger.success(f"✅ 已載入設定:")
        logger.info(f"   - 帳號: {self.account}")
        logger.info(f"   - 驗證碼模式: {'自動辨識 (Gemini)' if self.captcha_mode == 'auto' else '手動輸入'}")
        if self.captcha_mode == "auto":
            logger.info(f"   - Gemini 模型: {self.model_name}")
        logger.info(f"   - 目標書籍 ID: {self.book_id}")
        logger.info(f"   - 爬蟲模式: {'啟用' if self.enable_scraping else '停用'}")
        if self.enable_scraping:
            logger.info(f"   - 最大爬取頁數: {self.max_pages}")
            logger.info(f"   - 下載圖片: {'是' if self.download_images else '否'}")
            logger.info(f"   - 純圖片書籍模式: {'是 (Canvas Only)' if self.image_only_mode else '否 (HTML + Canvas)'}")
            logger.info(f"   - 翻頁策略: {'智能翻頁' if self.smart_page_turn else f'固定翻頁（每次 {self.pages_per_turn} 頁）'}")
            
            # 顯示翻頁按鍵（加上友善的中文說明）
            key_names = {
                "ArrowRight": "右鍵 (→)",
                "ArrowLeft": "左鍵 (←)",
                "ArrowUp": "上鍵 (↑)",
                "ArrowDown": "下鍵 (↓)"
            }
            key_display = key_names.get(self.page_turn_key, self.page_turn_key)
            logger.info(f"   - 翻頁按鍵: {key_display}")

    async def solve_captcha(self, page: Page) -> str:
        """
        解決驗證碼

        Args:
            page: Playwright 頁面物件

        Returns:
            辨識出的驗證碼文字
        """
        # 定位驗證碼圖片
        captcha_img = page.locator("#conImg")
        await captcha_img.wait_for(state="visible", timeout=10000)

        if self.captcha_mode == "manual":
            # 手動模式：顯示驗證碼並等待使用者輸入
            logger.info("📸 驗證碼圖片已顯示在瀏覽器中")
            logger.info("👀 請查看瀏覽器視窗中的驗證碼")
            logger.info("="*60)

            # 等待一下讓使用者看清楚驗證碼
            await asyncio.sleep(1)

            # 從命令列讀取使用者輸入
            captcha_text = input("⌨️  請輸入驗證碼: ").strip()

            if not captcha_text:
                raise ValueError("驗證碼不能為空")

            logger.success(f"✅ 您輸入的驗證碼: {captcha_text}")
            return captcha_text

        else:
            # 自動模式：使用 Gemini API 辨識
            logger.info("📸 正在截取驗證碼圖片...")

            # 截取驗證碼圖片
            captcha_screenshot = await captcha_img.screenshot()

            logger.info("🤖 正在呼叫 Google Gemini API 辨識驗證碼...")

            try:
                # 準備圖片
                import PIL.Image
                import io
                image = PIL.Image.open(io.BytesIO(captcha_screenshot))

                # 呼叫 Gemini Vision API
                prompt = (
                    "Please identify the text or numbers in this CAPTCHA image. "
                    "Return ONLY the CAPTCHA text without any explanation, punctuation, or formatting. "
                    "If you see letters and numbers, return them exactly as shown."
                )

                response = self.model.generate_content([prompt, image])

                # 檢查回應
                if not response.text:
                    raise ValueError("Gemini API 回應內容為空")

                captcha_text = response.text.strip()
                logger.success(f"✅ 驗證碼辨識結果: {captcha_text}")
                return captcha_text

            except Exception as e:
                logger.error(f"❌ Gemini API 呼叫失敗: {e}")
                raise

    async def login(self, page: Page) -> bool:
        """
        執行自動登入

        Args:
            page: Playwright 頁面物件

        Returns:
            登入是否成功
        """
        logger.info("\n" + "="*60)
        logger.info("🚀 開始自動登入流程")
        logger.info("="*60)

        # 前往登入頁面
        logger.info(f"📄 正在前往登入頁面: {self.login_url}")
        await page.goto(self.login_url)
        await asyncio.sleep(2)

        # 填寫帳號
        logger.info(f"✍️  填寫帳號: {self.account}")
        account_input = page.locator('input[name="account2"]')
        await account_input.wait_for(state="visible", timeout=10000)
        await account_input.fill(self.account)
        await asyncio.sleep(0.5)

        # 填寫密碼
        logger.info("🔒 填寫密碼...")
        password_input = page.locator('input[name="passwd2"]')
        await password_input.fill(self.password)
        await asyncio.sleep(0.5)

        # 辨識並填寫驗證碼
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            logger.info(f"\n🔍 驗證碼辨識嘗試 {attempt}/{max_retries}")

            try:
                captcha_text = await self.solve_captcha(page)

                # 填寫驗證碼
                logger.info(f"✍️  填寫驗證碼: {captcha_text}")
                valicode_input = page.locator('input[name="valicode"]')
                await valicode_input.fill("")  # 先清空
                await valicode_input.fill(captcha_text)
                await asyncio.sleep(0.5)

                # 點擊登入按鈕
                logger.info("🖱️  點擊登入按鈕...")
                login_button = page.locator('a[href="javascript:docheck();"] .login-btn')
                await login_button.click()

                # 等待頁面導航
                await asyncio.sleep(3)

                # 檢查是否登入成功
                current_url = page.url
                logger.info(f"📍 當前 URL: {current_url}")

                if "ebook.hyread.com.tw" in current_url and "index.jsp" in current_url:
                    logger.info("\n" + "="*60)
                    logger.success("✅ 登入成功！")
                    logger.info("="*60)
                    return True

                elif current_url == self.login_url:
                    logger.warning(f"⚠️  驗證碼可能錯誤，準備重試...")

                    if attempt < max_retries:
                        await valicode_input.fill("")
                        await asyncio.sleep(1)
                        continue
                    else:
                        logger.info(f"\n❌ 已達到最大重試次數 ({max_retries})，登入失敗")
                        return False

            except Exception as e:
                logger.error(f"❌ 驗證碼辨識失敗: {e}")
                if attempt < max_retries:
                    logger.info("⏳ 等待後重試...")
                    await asyncio.sleep(2)
                    continue
                else:
                    raise

        return False

    async def check_and_borrow_book(self, page: Page, book_id: str) -> bool:
        """
        檢查並借閱書籍

        Args:
            page: Playwright 頁面物件
            book_id: 書籍 ID

        Returns:
            借閱是否成功
        """
        logger.info("\n" + "="*60)
        logger.info("📚 開始檢查書籍")
        logger.info("="*60)

        # 前往書籍詳情頁面
        book_url = f"{self.base_url}/bookDetail.jsp?id={book_id}"
        logger.info(f"📄 正在前往書籍頁面: {book_url}")
        await page.goto(book_url)
        await asyncio.sleep(2)

        # 提取書名（取到第一個標點符號）
        try:
            book_title_element = page.locator('.book-detail h3')
            if await book_title_element.count() > 0:
                full_title = await book_title_element.text_content()

                if full_title:
                    # 取到第一個標點符號（：:、。！？）
                    import re
                    match = re.search(r'^([^：:、。！？]+)', full_title.strip())
                    if match:
                        short_title = match.group(1).strip()
                        self.book_title = short_title
                        logger.info(f"📖 書名: {short_title}")
                    else:
                        self.book_title = full_title.strip()
                        logger.info(f"📖 書名: {self.book_title}")
        except Exception as e:
            logger.warning(f"⚠️  無法提取書名: {e}")
            self.book_title = f"book_{book_id}"

        # 檢查線上閱讀按鈕
        try:
            # 方案1: 定位線上閱讀按鈕（未借閱的情況）
            read_button = page.locator('button.btn-collect:has-text("線上閱讀")')
            button_to_click = None
            is_already_borrowed = False

            # 檢查按鈕是否存在
            if await read_button.count() > 0:
                # 獲取按鈕的 title 屬性
                button_title = await read_button.get_attribute('title')
                logger.info(f"📊 按鈕狀態: {button_title}")

                # 使用正則表達式提取可用數量
                match = re.search(r'線上閱讀人數.*?尚有(\d+)本', button_title, re.DOTALL)

                if match:
                    available_count = int(match.group(1))
                    logger.info(f"📊 可借閱數量: {available_count} 本")

                    if available_count > 0:
                        logger.success("✅ 書籍可借閱，準備點擊線上閱讀按鈕...")
                        button_to_click = read_button
                    else:
                        logger.warning("⚠️  目前沒有可借閱的副本")
                        return False
                else:
                    logger.warning("⚠️  無法解析可借閱數量，嘗試直接點擊...")
                    button_to_click = read_button
            else:
                # 方案2: 檢查是否已借閱（"開啟"按鈕）
                logger.info("📖 未找到「線上閱讀」按鈕，檢查是否已借閱...")
                open_button = page.locator('input[value="開啟"]')
                
                if await open_button.count() > 0:
                    logger.success("✅ 書籍已借閱，找到「開啟」按鈕")
                    button_to_click = open_button
                    is_already_borrowed = True
                else:
                    logger.error("❌ 找不到「線上閱讀」或「開啟」按鈕")
                    return False

            # 點擊按鈕（線上閱讀 或 開啟）
            if button_to_click:
                if is_already_borrowed:
                    logger.info("🖱️  點擊「開啟」按鈕...")
                else:
                    logger.info("🖱️  點擊「線上閱讀」按鈕...")
                
                await button_to_click.click()
                await asyncio.sleep(3)

                # 檢查是否成功開啟閱讀頁面
                # 可能會開啟新分頁或彈出視窗
                current_url = page.url
                logger.info(f"📍 當前 URL: {current_url}")

                # 檢查所有頁面
                all_pages = page.context.pages
                logger.info(f"📄 目前開啟的頁面數: {len(all_pages)}")

                reading_page = None

                if len(all_pages) > 1:
                    logger.success("✅ 已開啟新的閱讀視窗")
                    # 切換到新頁面
                    reading_page = all_pages[-1]
                    await asyncio.sleep(2)
                    logger.info(f"📍 閱讀頁面 URL: {reading_page.url}")
                else:
                    # 如果沒有開啟新頁面，可能在當前頁面中打開
                    logger.warning("⚠️  未偵測到新視窗，檢查當前頁面...")

                    # 等待頁面可能的變化
                    await asyncio.sleep(2)

                    # 檢查當前頁面 URL 是否改變
                    if page.url != current_url or "reader" in page.url.lower():
                        logger.success("✅ 閱讀器在當前頁面中打開")
                        reading_page = page
                    else:
                        # 再等待並重新檢查
                        await asyncio.sleep(3)
                        all_pages = page.context.pages
                        if len(all_pages) > 1:
                            reading_page = all_pages[-1]
                            logger.success(f"✅ 延遲偵測到新視窗: {reading_page.url}")
                        else:
                            logger.warning("⚠️  仍未偵測到閱讀視窗，使用當前頁面")
                            reading_page = page

                logger.info("\n" + "="*60)
                logger.success("✅ 開啟成功！")
                logger.info("="*60)

                # 如果啟用爬蟲，返回閱讀頁面用於後續爬取
                if self.enable_scraping:
                    if reading_page:
                        logger.info(f"📖 將使用頁面進行爬取: {reading_page.url}")
                        return reading_page
                    else:
                        logger.error("❌ 無法獲取閱讀頁面")
                        return False
                else:
                    return True

        except Exception as e:
            logger.error(f"❌ 檢查或借閱書籍時發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def click_accept_button(self, page: Page) -> bool:
        """
        點擊「我知道了」按鈕

        Args:
            page: Playwright 頁面物件

        Returns:
            是否成功點擊
        """
        try:
            logger.info("\n🔍 尋找「我知道了」按鈕...")

            # 等待按鈕出現
            accept_button = page.locator('button:has-text("我知道了")')

            # 等待最多 10 秒
            await accept_button.wait_for(state="visible", timeout=10000)

            logger.info("🖱️  點擊「我知道了」按鈕...")
            await accept_button.click()
            await asyncio.sleep(2)

            logger.success("✅ 已點擊「我知道了」按鈕")
            return True

        except Exception as e:
            logger.warning(f"⚠️  未找到或無法點擊「我知道了」按鈕: {e}")
            return False

    async def handle_reading_progress_popup(self, page: Page) -> bool:
        """
        處理閱讀進度彈窗（如果存在）

        Args:
            page: Playwright 頁面物件

        Returns:
            是否處理了彈窗
        """
        try:
            logger.info("\n🔍 檢查是否有閱讀進度彈窗...")

            # 更精確的選擇器：同時檢查 class 和文字內容
            progress_popup = page.locator('div.reader-popover[aria-label*="閱讀進度"]')
            
            # 如果沒找到，嘗試第二種方式
            if await progress_popup.count() == 0:
                progress_popup = page.locator('div[class*="reader-popover"]:has-text("請問是否前往")')

            # 等待最多 2 秒，給彈窗足夠時間出現
            try:
                await progress_popup.wait_for(state="visible", timeout=2000)
                
                # 確認彈窗真的可見
                if not await progress_popup.is_visible():
                    logger.info("ℹ️  沒有閱讀進度彈窗，繼續執行")
                    return False
                
                # 找到了彈窗，提取進度信息
                popup_text = await progress_popup.text_content()
                logger.info(f"📍 發現閱讀進度彈窗: {popup_text[:60].replace(chr(10), ' ')}...")
                
                # 在彈窗內部查找「略過」按鈕（更精確）
                skip_button = progress_popup.locator('button:has-text("略過")').first
                
                # 確保按鈕存在且可點擊
                if await skip_button.count() > 0:
                    # 等待按鈕可點擊
                    await skip_button.wait_for(state="visible", timeout=1000)
                    
                    logger.info("🖱️  點擊「略過」按鈕...")
                    await skip_button.click()
                    
                    # 等待彈窗消失（重要！）
                    try:
                        await progress_popup.wait_for(state="hidden", timeout=3000)
                        logger.success("✅ 已略過閱讀進度提示，彈窗已關閉")
                    except:
                        logger.warning("⚠️  彈窗可能未完全關閉，繼續執行")
                    
                    # 額外等待，確保頁面穩定
                    await asyncio.sleep(1.5)
                    return True
                else:
                    logger.warning("⚠️  找不到「略過」按鈕")
                    return False
                    
            except Exception as timeout_err:
                # 沒有彈窗或超時，這是正常情況
                logger.info("ℹ️  沒有閱讀進度彈窗，繼續執行")
                return False

        except Exception as e:
            logger.debug(f"檢查閱讀進度彈窗時發生錯誤: {e}")
            return False

    async def get_all_visible_iframes(self, page: Page) -> list:
        """
        獲取所有可見的 iframe

        Args:
            page: Playwright 頁面物件

        Returns:
            所有可見 iframe 的 FrameLocator 列表
        """
        try:
            visible_iframes = []

            # 直接找到所有 iframe 元素
            iframes = page.locator('iframe')
            iframe_count = await iframes.count()

            logger.info(f"   🔍 找到 {iframe_count} 個 iframe")

            # 遍歷所有 iframe
            for i in range(iframe_count):
                iframe_element = iframes.nth(i)

                # 檢查 iframe 是否可見
                is_visible = await iframe_element.is_visible()

                if is_visible:
                    frame_locator = page.frame_locator('iframe').nth(i)
                    visible_iframes.append(frame_locator)
                    logger.info(f"      ✓ iframe[{i}] 可見")
                else:
                    logger.info(f"      ✗ iframe[{i}] 不可見")

            if not visible_iframes:
                logger.info("   ⚠️  沒有找到可見的 iframe，使用第一個")
                visible_iframes.append(page.frame_locator('iframe').first)

            return visible_iframes

        except Exception as e:
            logger.info(f"   ⚠️  獲取 iframe 時發生錯誤: {e}")
            # 降級方案：返回第一個 iframe
            return [page.frame_locator('iframe').first]

    async def get_current_iframe(self, page: Page) -> FrameLocator:
        """
        獲取當前顯示的 iframe（向後兼容的方法）

        Args:
            page: Playwright 頁面物件

        Returns:
            當前的 iframe locator
        """
        visible_iframes = await self.get_all_visible_iframes(page)
        return visible_iframes[0] if visible_iframes else page.frame_locator('iframe').first

    async def extract_html_with_formatting(self, element) -> str:
        """
        提取元素的 HTML 並保留格式標籤

        Args:
            element: Playwright 元素

        Returns:
            包含格式的文字
        """
        try:
            # 獲取元素的 innerHTML
            html = await element.inner_html()

            # 轉換 HTML 格式為 Markdown 格式
            # 粗體：<strong>, <b> -> **text**
            html = re.sub(r'<strong>(.*?)</strong>', r'**\1**', html)
            html = re.sub(r'<b>(.*?)</b>', r'**\1**', html)

            # 斜體：<em>, <i> -> *text*
            html = re.sub(r'<em>(.*?)</em>', r'*\1*', html)
            html = re.sub(r'<i>(.*?)</i>', r'*\1*', html)

            # 特殊 span 類：gfontorange -> 粗體
            html = re.sub(r'<span[^>]*class="[^"]*gfontorange[^"]*"[^>]*>(.*?)</span>', r'**\1**', html)
            
            # Footnote 引用：<a class="ref" ...>1</a> -> [^1]
            # 提取 footnote 編號並轉換為 Markdown 引用格式
            html = re.sub(r'<a[^>]*class="[^"]*ref[^"]*"[^>]*>(\d+)</a>', r'[^\1]', html)
            
            # 移除其他 HTML 標籤但保留內容
            html = re.sub(r'<span[^>]*>(.*?)</span>', r'\1', html)
            html = re.sub(r'<div[^>]*>(.*?)</div>', r'\1', html)
            html = re.sub(r'<br\s*/?>', '\n', html)

            # 移除所有剩餘的 HTML 標籤
            html = re.sub(r'<[^>]+>', '', html)

            return html.strip()

        except Exception as e:
            # 如果出錯，返回純文字
            return await element.text_content()

    async def get_base_url_from_iframe(self, page: Page) -> str:
        """
        從 iframe 獲取 base URL

        Args:
            page: Playwright 頁面物件

        Returns:
            base URL 或空字串
        """
        try:
            iframe = await self.get_current_iframe(page)
            base_element = iframe.locator('base').first
            base_href = await base_element.get_attribute('href')
            return base_href or ''
        except:
            return ''

    async def scrape_page_content(self, page: Page) -> Dict[str, any]:
        """
        抓取當前頁面的內容（從所有可見的 iframe）

        Args:
            page: Playwright 頁面物件

        Returns:
            包含標題、段落和圖片的字典
        """
        try:
            # 獲取所有可見的 iframe
            visible_iframes = await self.get_all_visible_iframes(page)

            content = {
                'headings': [],
                'paragraphs': [],
                'images': []
            }

            # 從所有可見的 iframe 中抓取內容
            for iframe_index, iframe in enumerate(visible_iframes):
                logger.info(f"      📄 正在抓取 iframe[{iframe_index}] 的內容...")
                iframe_content = await self._scrape_from_single_iframe(iframe)

                # 合併內容
                content['headings'].extend(iframe_content['headings'])
                content['paragraphs'].extend(iframe_content['paragraphs'])
                content['images'].extend(iframe_content['images'])

                logger.info(f"         找到: 標題={len(iframe_content['headings'])}, 段落={len(iframe_content['paragraphs'])}, 圖片={len(iframe_content['images'])}")

            return content

        except Exception as e:
            logger.warning(f"⚠️  抓取頁面內容時發生錯誤: {e}")
            return {'headings': [], 'paragraphs': [], 'images': []}

    async def _extract_figure_content(self, figure_element) -> dict:
        """
        從 figure 元素中提取圖片和說明文字

        Args:
            figure_element: figure 元素

        Returns:
            包含 caption 和 image_src 的字典
        """
        try:
            caption_parts = []
            image_src = None

            # 提取 figcaption
            figcaption = figure_element.locator('figcaption')
            if await figcaption.count() > 0:
                figcaption_text = await self.extract_html_with_formatting(figcaption.first)
                if figcaption_text.strip():
                    caption_parts.append(figcaption_text.strip())

            # 提取 p.bold（圖片標題）
            bold_p = figure_element.locator('p.bold')
            if await bold_p.count() > 0:
                bold_text = await self.extract_html_with_formatting(bold_p.first)
                if bold_text.strip():
                    caption_parts.append(bold_text.strip())

            # 提取圖片 src
            img = figure_element.locator('img')
            if await img.count() > 0:
                image_src = await img.first.get_attribute('src')

            if image_src:
                # 合併所有說明文字
                full_caption = ' - '.join(caption_parts) if caption_parts else '圖片'

                return {
                    'caption': full_caption,
                    'image_src': image_src,
                    'image_alt': full_caption
                }

            return None

        except Exception as e:
            logger.info(f"         ⚠️  提取 figure 內容失敗: {e}")
            return None

    async def _extract_container_content(self, container_element) -> list:
        """
        從 div[class^="container"] 元素中按順序提取圖片和說明文字
        
        支持多種格式變體：
        - <div class="container">、<div class="container2">、<div class="container3"> 等
        - <p class="caption">、<p class="caption2"> 等（任何包含 "caption" 的 class）
        
        處理格式如：
        <div class="container2">
            <div id="_idContainer019">
                <img class="fit" src="image/p0018a.jpg" alt="" draggable="false">
                <p class="caption ...">精美的日本繪畫屏風...</p>
            </div>
        </div>
        
        Args:
            container_element: div[class^="container"] 元素
            
        Returns:
            內容項目列表（按 DOM 順序）
        """
        try:
            result_items = []
            
            # 查找所有子元素（img 和 p，按 DOM 順序）
            children = container_element.locator('img, p')
            child_count = await children.count()
            
            for i in range(child_count):
                child = children.nth(i)
                tag_name = await child.evaluate('el => el.tagName.toLowerCase()')
                
                if tag_name == 'img':
                    # 處理圖片
                    src = await child.get_attribute('src')
                    alt = await child.get_attribute('alt') or '圖片'
                    element_class = await child.get_attribute('class') or ''
                    
                    if src:
                        result_items.append({
                            'type': 'image',
                            'image_src': src,
                            'image_alt': alt,
                            'image_class': element_class
                        })
                        
                elif tag_name == 'p':
                    # 處理說明文字（caption, caption2, caption3 等）
                    element_class = await child.get_attribute('class') or ''
                    text_content = await self.extract_html_with_formatting(child)
                    
                    if text_content.strip():
                        # 如果 class 包含 "caption"，作為圖片說明
                        # 支持: caption, caption2, caption3 等所有變體
                        if 'caption' in element_class:
                            result_items.append({
                                'type': 'caption',
                                'content': text_content.strip()
                            })
                        else:
                            # 一般段落
                            result_items.append({
                                'type': 'p',
                                'content': text_content.strip()
                            })
            
            return result_items if result_items else None
            
        except Exception as e:
            logger.info(f"         ⚠️  提取 container 內容失敗: {e}")
            return None

    async def extract_chapter_name(self, iframe: FrameLocator) -> tuple:
        """
        從 iframe 中提取章節名稱和排序號（支持多種規則）

        Args:
            iframe: iframe locator

        Returns:
            (章節名稱, 排序號, 文件名, 錨點ID) 的元組
        """
        try:
            body = iframe.locator('body')
            import re

            # 中文數字映射表
            chinese_nums = {
                '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
                '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
                '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20
            }

            # 提取當前頁面的文件名和錨點（用於與 TOC 匹配）
            current_file_name = None
            current_anchor_id = None
            
            try:
                base_element = iframe.locator('base').first
                base_href = await base_element.get_attribute('href')
                if base_href:
                    # 從 base URL 提取文件名
                    # 例如：.../Text/ch-01.xhtml -> ch-01
                    match = re.search(r'([^/]+)\.xhtml', base_href)
                    if match:
                        current_file_name = match.group(1)
            except:
                pass

            # 規則 0: 優先檢查 h1-h5 的 title 屬性（最完整的章節名）
            for level in range(1, 6):  # h1 到 h5
                elements = body.locator(f'h{level}[title]')
                count = await elements.count()

                if count > 0:
                    element = elements.first
                    title_attr = await element.get_attribute('title')
                    
                    if title_attr and title_attr.strip():
                        # 同時嘗試提取 ID（可能有 sigil_toc_id）
                        element_id = await element.get_attribute('id')
                        if element_id:
                            current_anchor_id = element_id
                            # 從 ID 提取數字
                            match = re.search(r'sigil_toc_id_(\d+)', element_id)
                            if match:
                                order_num = int(match.group(1))
                                return (title_attr.strip(), order_num, current_file_name, current_anchor_id)
                        
                        # 嘗試從 title 文本中提取數字
                        # 匹配 "CHAPTER 1", "第一章", "1.1" 等
                        chapter_match = re.search(r'CHAPTER\s+(\d+)', title_attr, re.IGNORECASE)
                        if chapter_match:
                            order_num = int(chapter_match.group(1))
                            return (title_attr.strip(), order_num, current_file_name, current_anchor_id)
                        
                        num_match = re.match(r'^(\d+(?:\.\d+)?)', title_attr.strip())
                        if num_match:
                            num_str = num_match.group(1)
                            try:
                                float_num = float(num_str)
                                order_num = int(float_num * 10)
                                return (title_attr.strip(), order_num, current_file_name, current_anchor_id)
                            except:
                                pass
                        
                        return (title_attr.strip(), None, current_file_name, current_anchor_id)

            # 規則 1: 檢查 h1-h5 的 sigil_toc_id（優先級最高）
            for level in range(1, 6):  # h1 到 h5
                elements = body.locator(f'h{level}[id^="sigil_toc_id_"]')
                count = await elements.count()

                if count > 0:
                    element = elements.first
                    element_id = await element.get_attribute('id')
                    element_text = await self.extract_html_with_formatting(element)
                    
                    if element_id:
                        current_anchor_id = element_id

                    # 從 id 中提取數字
                    match = re.search(r'sigil_toc_id_(\d+)', element_id)
                    if match:
                        order_num = int(match.group(1))
                        return (element_text.strip(), order_num, current_file_name, current_anchor_id)

                    return (element_text.strip(), None, current_file_name, current_anchor_id)

            # 規則 2: 檢查 h1-h5 中的 span.num2 (Chapter X)
            for level in range(1, 6):  # h1 到 h5
                elements = body.locator(f'h{level}')
                count = await elements.count()

                for i in range(count):
                    element = elements.nth(i)
                    span_num2 = element.locator('span.num2')

                    if await span_num2.count() > 0:
                        # 獲取整個標題的文字作為章節名
                        chapter_name = await self.extract_html_with_formatting(element)
                        
                        # 嘗試提取 ID
                        element_id = await element.get_attribute('id')
                        if element_id:
                            current_anchor_id = element_id

                        # 嘗試從 span.num2 中提取章節號
                        span_text = await span_num2.text_content()
                        match = re.search(r'Chapter\s+(\d+)', span_text, re.IGNORECASE)
                        if match:
                            order_num = int(match.group(1))
                            return (chapter_name.strip(), order_num, current_file_name, current_anchor_id)

                        return (chapter_name.strip(), None, current_file_name, current_anchor_id)

            # 規則 3: 檢查 h1-h5 中的 span.num (第X章)
            for level in range(1, 6):  # h1 到 h5
                elements = body.locator(f'h{level}')
                count = await elements.count()

                for i in range(count):
                    element = elements.nth(i)
                    span_num = element.locator('span.num')

                    if await span_num.count() > 0:
                        # 獲取整個標題的文字作為章節名
                        chapter_name = await self.extract_html_with_formatting(element)
                        
                        # 嘗試提取 ID
                        element_id = await element.get_attribute('id')
                        if element_id:
                            current_anchor_id = element_id

                        # 嘗試從 span.num 中提取章節號
                        span_text = await span_num.text_content()

                        # 嘗試匹配「第X章」
                        match = re.search(r'第([一二三四五六七八九十百\d]+)章', span_text)
                        if match:
                            num_str = match.group(1)
                            if num_str in chinese_nums:
                                order_num = chinese_nums[num_str]
                                return (chapter_name.strip(), order_num, current_file_name, current_anchor_id)
                            elif num_str.isdigit():
                                order_num = int(num_str)
                                return (chapter_name.strip(), order_num, current_file_name, current_anchor_id)

                        return (chapter_name.strip(), None, current_file_name, current_anchor_id)

            # 規則 4: 檢查 h1-h5 class="__reader-paragraph-spacing__"（如 "1.1 合作的演進"）
            for level in range(1, 6):  # h1 到 h5
                elements = body.locator(f'h{level}.__reader-paragraph-spacing__')
                count = await elements.count()

                if count > 0:
                    element = elements.first
                    chapter_name = await self.extract_html_with_formatting(element)
                    
                    # 嘗試提取 ID
                    element_id = await element.get_attribute('id')
                    if element_id:
                        current_anchor_id = element_id
                    
                    # 嘗試從章節名稱中提取數字編號（如 "1.1", "2.3", "10.5"）
                    match = re.match(r'^(\d+(?:\.\d+)?)', chapter_name.strip())
                    if match:
                        num_str = match.group(1)
                        # 將 "1.1" 轉換為 1.1（浮點數）然後乘以 10 得到整數排序
                        # 例如：1.1 -> 11, 2.3 -> 23, 10.5 -> 105
                        try:
                            float_num = float(num_str)
                            order_num = int(float_num * 10)
                            return (chapter_name.strip(), order_num, current_file_name, current_anchor_id)
                        except:
                            pass
                    
                    # 嘗試匹配單純的數字開頭（如 "1 前言"）
                    match = re.match(r'^(\d+)\s+', chapter_name.strip())
                    if match:
                        order_num = int(match.group(1))
                        return (chapter_name.strip(), order_num, current_file_name, current_anchor_id)
                    
                    # 沒有找到數字，但有章節名
                    return (chapter_name.strip(), None, current_file_name, current_anchor_id)

            # 規則 5: 檢查 p.titlebig 作為章節名
            p_titlebig = body.locator('p.titlebig')
            if await p_titlebig.count() > 0:
                chapter_name = await self.extract_html_with_formatting(p_titlebig.first)
                
                # 嘗試從文字中提取數字
                match = re.match(r'^(\d+(?:\.\d+)?)', chapter_name.strip())
                if match:
                    num_str = match.group(1)
                    try:
                        float_num = float(num_str)
                        order_num = int(float_num * 10)
                        return (chapter_name.strip(), order_num, current_file_name, current_anchor_id)
                    except:
                        pass
                
                return (chapter_name.strip(), None, current_file_name, current_anchor_id)

            # 備用方案：嘗試找第一個 h1-h5
            for level in range(1, 6):  # h1 到 h5
                elements = body.locator(f'h{level}')
                if await elements.count() > 0:
                    first_heading = await self.extract_html_with_formatting(elements.first)
                    element_id = await elements.first.get_attribute('id')
                    if element_id:
                        current_anchor_id = element_id
                    return (first_heading.strip(), None, current_file_name, current_anchor_id)

            return ("", None, None, None)

        except Exception as e:
            logger.info(f"         ⚠️  提取章節名稱失敗: {e}")
            return ("", None, None, None)

    async def is_toc_page(self, iframe: FrameLocator) -> bool:
        """
        判斷是否為目錄頁（支持多種格式）

        Args:
            iframe: iframe locator

        Returns:
            是否為目錄頁
        """
        try:
            body = iframe.locator('body')

            # 檢查 1: 是否有 nav[epub:type="toc"]
            toc_nav = body.locator('nav[epub\\:type="toc"]')
            if await toc_nav.count() > 0:
                return True

            # 檢查 2: body 是否有 class="p-toc" 或類似的目錄標記
            body_class = await body.get_attribute('class')
            if body_class and ('toc' in body_class.lower() or 'contents' in body_class.lower()):
                return True

            # 檢查 3: h1 是否包含「目錄」
            h1_elements = body.locator('h1')
            if await h1_elements.count() > 0:
                h1_text = await h1_elements.first.text_content()
                if h1_text and '目錄' in h1_text:
                    return True

            # 檢查 4: div 是否包含「目錄」文字（新格式）
            div_elements = body.locator('div:has-text("目錄")')
            if await div_elements.count() > 0:
                # 檢查是否有足夠的鏈接（至少 3 個）
                links = body.locator('a[href*=".xhtml"]')
                if await links.count() >= 3:
                    return True

            return False
        except:
            return False

    async def extract_toc_links(self, iframe: FrameLocator) -> list:
        """
        從目錄頁提取所有章節鏈接（帶索引號，支持多種格式）

        Args:
            iframe: iframe locator

        Returns:
            章節鏈接列表 [{'title': '章節標題', 'href': '鏈接', 'toc_index': 索引號, 'level': 層級}]
        """
        try:
            toc_items = []
            body = iframe.locator('body')
            import re

            # 方法 1: 標準 EPUB 格式（nav[epub:type="toc"]）
            nav_links = body.locator('nav[epub\\:type="toc"] a, ol a, ul a')
            nav_count = await nav_links.count()

            if nav_count > 0:
                logger.info(f"         📚 使用標準 EPUB TOC 格式")
                for i in range(nav_count):
                    link = nav_links.nth(i)
                    title = await link.text_content()
                    href = await link.get_attribute('href')

                    if title and href:
                        # 提取文件名（不包含錨點）
                        match = re.search(r'([^/]+)\.xhtml', href)
                        file_name = match.group(1) if match else None
                        
                        # 提取錨點 ID
                        anchor_match = re.search(r'#(.+)$', href)
                        anchor_id = anchor_match.group(1) if anchor_match else None
                        
                        toc_items.append({
                            'title': title.strip(),
                            'href': href,
                            'file_name': file_name,
                            'anchor_id': anchor_id,
                            'toc_index': i,
                            'level': 0  # 標準格式不區分層級
                        })

            # 方法 2: 簡化格式（body.p-toc 或包含"目錄"的 div）
            else:
                logger.info(f"         📖 使用簡化 TOC 格式")
                
                # 找到所有包含 .xhtml 鏈接的 <a> 標籤
                all_links = body.locator('a[href*=".xhtml"]')
                link_count = await all_links.count()

                for i in range(link_count):
                    link = all_links.nth(i)
                    title = await link.text_content()
                    href = await link.get_attribute('href')

                    if not title or not href:
                        continue

                    # 提取文件名
                    match = re.search(r'([^/]+)\.xhtml', href)
                    file_name = match.group(1) if match else None
                    
                    # 提取錨點 ID
                    anchor_match = re.search(r'#(.+)$', href)
                    anchor_id = anchor_match.group(1) if anchor_match else None
                    
                    # 判斷層級（通過父元素的 class）
                    level = 0
                    try:
                        # 檢查父元素是否有縮進 class（如 start-4em50）
                        parent_p = link.locator('xpath=ancestor::p[1]')
                        if await parent_p.count() > 0:
                            parent_div = parent_p.locator('xpath=parent::div[1]')
                            if await parent_div.count() > 0:
                                parent_class = await parent_div.first.get_attribute('class')
                                if parent_class:
                                    # 識別縮進 class（start-4em50, start-2em 等）
                                    if 'start-4em' in parent_class or 'start-3em' in parent_class:
                                        level = 2  # 子章節
                                    elif 'start-2em' in parent_class:
                                        level = 1  # 次級章節
                    except:
                        pass
                    
                    # 清理標題（移除多餘空格和換行）
                    clean_title = re.sub(r'\s+', ' ', title.strip())
                    
                    toc_items.append({
                        'title': clean_title,
                        'href': href,
                        'file_name': file_name,
                        'anchor_id': anchor_id,
                        'toc_index': i,
                        'level': level  # 0=主章節, 1=次級, 2=子章節
                    })

            logger.info(f"         📑 提取到 {len(toc_items)} 個目錄項")
            
            # Debug: 顯示前 5 個項目
            if toc_items:
                logger.info(f"         📋 目錄預覽（前5項）：")
                for item in toc_items[:5]:
                    indent = "  " * item.get('level', 0)
                    logger.info(f"            {indent}[{item['toc_index']}] {item['title']}")
            
            return toc_items

        except Exception as e:
            logger.info(f"         ⚠️  提取目錄鏈接失敗: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def scrape_chapter_from_iframe(self, iframe: FrameLocator, base_url: str = None, toc_links: list = None) -> Dict[str, any]:
        """
        從單個 iframe 抓取完整章節內容（保持元素順序，支持 TOC 智能匹配）

        Args:
            iframe: iframe locator
            base_url: 基礎 URL（用於解析圖片相對路徑）
            toc_links: TOC 目錄鏈接列表（用於智能排序）

        Returns:
            章節資料字典，包含章節名和有序內容列表
        """
        try:
            # 檢查是否為目錄頁
            is_toc = await self.is_toc_page(iframe)

            # 提取章節名稱、排序號、文件名、錨點ID
            chapter_name, order_num, file_name, anchor_id = await self.extract_chapter_name(iframe)
            
            # 🔍 智能 TOC 匹配：使用 TOC 提供更準確的排序
            toc_index = None
            toc_title = None
            
            if toc_links and (file_name or anchor_id or chapter_name):
                # 策略1: 精確匹配（文件名 + 錨點ID）
                if file_name and anchor_id:
                    for toc_item in toc_links:
                        if toc_item['file_name'] == file_name and toc_item.get('anchor_id') == anchor_id:
                            toc_index = toc_item['toc_index']
                            toc_title = toc_item['title']
                            logger.info(f"         🎯 TOC 精確匹配: [{toc_index}] {toc_title}")
                            break
                
                # 策略2: 文件名匹配（無錨點）
                if toc_index is None and file_name:
                    for toc_item in toc_links:
                        if toc_item['file_name'] == file_name and not toc_item.get('anchor_id'):
                            toc_index = toc_item['toc_index']
                            toc_title = toc_item['title']
                            logger.info(f"         📍 TOC 文件名匹配: [{toc_index}] {toc_title}")
                            break
                
                # 策略3: 章節名模糊匹配（文字相似度）
                if toc_index is None and chapter_name:
                    best_match_score = 0
                    best_match_item = None
                    
                    for toc_item in toc_links:
                        toc_item_title = toc_item['title']
                        
                        # 計算相似度（簡單的包含關係）
                        if chapter_name in toc_item_title or toc_item_title in chapter_name:
                            # 精確包含
                            score = 0.9
                        elif chapter_name.replace(' ', '') in toc_item_title.replace(' ', ''):
                            # 去空格後包含
                            score = 0.8
                        else:
                            # 計算共同字符數
                            common_chars = sum(1 for c in chapter_name if c in toc_item_title)
                            score = common_chars / max(len(chapter_name), len(toc_item_title))
                        
                        if score > best_match_score and score > 0.6:  # 至少 60% 相似度
                            best_match_score = score
                            best_match_item = toc_item
                    
                    if best_match_item:
                        toc_index = best_match_item['toc_index']
                        toc_title = best_match_item['title']
                        logger.info(f"         💡 TOC 模糊匹配: [{toc_index}] {toc_title} (相似度: {best_match_score:.1%})")
            
            # 優先使用 TOC 索引，否則使用 extract_chapter_name 的 order_num
            if toc_index is not None:
                order_num = toc_index  # TOC 索引優先
                if toc_title and not chapter_name:
                    chapter_name = toc_title  # 如果沒有章節名，使用 TOC 標題

            if not chapter_name:
                # 如果沒有章節名，使用特殊標記（可能是封面或前言）
                chapter_name = "__no_chapter__"
                order_num = None

            # 如果是目錄頁，提取目錄鏈接
            toc_links = []
            if is_toc or '目錄' in chapter_name:
                toc_links = await self.extract_toc_links(iframe)
                if toc_links:
                    chapter_name = "目錄"  # 統一命名為「目錄」
                    order_num = None  # 目錄不參與排序

            # 按順序抓取所有內容元素（保持 DOM 順序）
            content_items = []

            # 抓取 body 內的所有元素
            body = iframe.locator('body')

            # 一次性抓取所有內容元素並保持順序
            # 重要：排除 div[class^="container"] 和 figure 內部的 p, img，避免重複處理
            # 這些元素會由專門的 _extract_container_content 和 _extract_figure_content 處理
            all_elements = body.locator(
                'h1:not(div[class^="container"] *, figure *), '
                'h2:not(div[class^="container"] *, figure *), '
                'h3:not(div[class^="container"] *, figure *), '
                'h4:not(div[class^="container"] *, figure *), '
                'h5:not(div[class^="container"] *, figure *), '
                'h6:not(div[class^="container"] *, figure *), '
                'p:not(div[class^="container"] *, figure *), '
                'figure, '
                'div[class^="container"]'
            )
            element_count = await all_elements.count()

            for i in range(element_count):
                element = all_elements.nth(i)

                # 獲取元素的標籤名
                tag_name = await element.evaluate('el => el.tagName.toLowerCase()')

                if tag_name == 'figure':
                    # 處理 figure 元素（圖片 + 說明文字）
                    figure_data = await self._extract_figure_content(element)
                    if figure_data:
                        # 將 figure 作為特殊的內容項目
                        content_items.append({
                            'type': 'figure',
                            'content': figure_data['caption'],
                            'image_src': figure_data['image_src'],
                            'image_alt': figure_data['image_alt']
                        })
                elif tag_name == 'div':
                    # 處理 div[class^="container"] 內的圖片和說明文字（按順序）
                    # 支持 container, container2, container3 等所有變體
                    container_data = await self._extract_container_content(element)
                    if container_data:
                        for item in container_data:
                            content_items.append(item)
                else:
                    # 獲取元素的文字內容（保留格式）
                    text_content = await self.extract_html_with_formatting(element)

                    if text_content.strip():
                        # 檢查是否有特殊 class 需要處理
                        element_class = await element.get_attribute('class') or ''
                        epub_type = await element.get_attribute('epub:type') or ''
                        
                        # 處理特殊樣式類
                        final_content = text_content.strip()
                        
                        # footnote 類：腳註，標記為 footnote
                        if 'footnote' in element_class or epub_type == 'footnote':
                            # 提取腳註編號（從 <a> 標籤內容）
                            footnote_num = await element.locator('a').first.text_content() if await element.locator('a').count() > 0 else ''
                            if footnote_num.strip():
                                final_content = f"[^{footnote_num.strip()}]: {final_content}"
                            else:
                                final_content = f"**[註]** {final_content}"
                        # titlebig 類：大標題，加粗體
                        elif 'titlebig' in element_class:
                            final_content = f"**{final_content}**"
                        # titlemid 類：中等標題，加粗體
                        elif 'titlemid' in element_class:
                            final_content = f"**{final_content}**"
                        
                        content_items.append({
                            'type': tag_name,
                            'content': final_content
                        })

            # 抓取不在 figure 內的獨立圖片
            # 注意：這裡包括 container 內的圖片，用於下載，但在 Markdown 輸出時會去重
            images = []

            # 一般圖片（排除 figure 內的）
            img_elements = body.locator('img:not(figure img)')
            img_count = await img_elements.count()

            for i in range(img_count):
                img = img_elements.nth(i)
                src = await img.get_attribute('src')
                alt = await img.get_attribute('alt') or '圖片'

                if src:
                    images.append({
                        'src': src,
                        'alt': alt
                    })

            # SVG 圖片（排除 figure 內的）
            svg_images = body.locator('svg:not(figure svg) image')
            svg_count = await svg_images.count()

            for i in range(svg_count):
                svg_img = svg_images.nth(i)

                # 優先嘗試 xlink:href
                src = await svg_img.get_attribute('xlink:href')
                if not src:
                    src = await svg_img.get_attribute('href')

                if src:
                    images.append({
                        'src': src,
                        'alt': 'SVG 圖片'
                    })

            # Canvas 圖片（排除 figure 內的）
            canvas_elements = body.locator('canvas:not(figure canvas)')
            canvas_count = await canvas_elements.count()

            if canvas_count > 0:
                logger.info(f"         🎨 找到 {canvas_count} 個 Canvas 元素")

            for i in range(canvas_count):
                canvas = canvas_elements.nth(i)
                
                try:
                    # 等待 Canvas 渲染完成（檢查是否有內容）
                    # 最多等待 3 秒，每 0.5 秒檢查一次
                    canvas_ready = False
                    for attempt in range(6):
                        has_content = await canvas.evaluate('''
                            canvas => {
                                try {
                                    const ctx = canvas.getContext('2d');
                                    if (!ctx) return false;
                                    
                                    // 檢查 canvas 是否有內容（不是完全空白）
                                    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                                    const data = imageData.data;
                                    
                                    // 檢查是否有非透明的像素
                                    for (let i = 3; i < data.length; i += 4) {
                                        if (data[i] > 0) {
                                            return true;  // 找到非透明像素
                                        }
                                    }
                                    return false;
                                } catch (e) {
                                    return false;
                                }
                            }
                        ''')
                        
                        if has_content:
                            canvas_ready = True
                            logger.info(f"         ✓ Canvas[{i}] 已渲染完成（嘗試 {attempt + 1} 次）")
                            break
                        
                        if attempt < 5:
                            await asyncio.sleep(0.5)
                    
                    if not canvas_ready:
                        logger.info(f"         ⚠️  Canvas[{i}] 可能為空或未渲染完成")
                        # 仍然嘗試抓取，可能有內容只是檢測失敗
                    
                    # 將 canvas 轉換為 data URL（PNG 格式）
                    data_url = await canvas.evaluate('''
                        canvas => {
                            try {
                                return canvas.toDataURL('image/png');
                            } catch (e) {
                                console.error('Canvas toDataURL error:', e);
                                return null;
                            }
                        }
                    ''')
                    
                    if data_url and data_url.startswith('data:image'):
                        # 檢查 data URL 的大小（排除過小的空白圖片）
                        data_size = len(data_url)
                        
                        # 空白的 PNG 通常很小（< 1KB），實際內容通常 > 5KB
                        if data_size > 5000:
                            images.append({
                                'src': data_url,
                                'alt': f'Canvas 圖片 {i+1}',
                                'is_canvas': True  # 標記為 canvas 圖片
                            })
                            logger.info(f"         ✅ Canvas[{i}] 已轉換為圖片 ({data_size / 1024:.1f} KB)")
                        else:
                            logger.info(f"         ⚠️  Canvas[{i}] 圖片過小 ({data_size} bytes)，可能為空白")
                    else:
                        logger.info(f"         ⚠️  Canvas[{i}] 轉換失敗或為空")
                        
                except Exception as e:
                    logger.info(f"         ⚠️  Canvas[{i}] 抓取失敗: {e}")

            # 抓取註釋
            footnotes = []
            footnote_elements = body.locator('div.footnote[role="doc-endnote"]')
            footnote_count = await footnote_elements.count()

            if footnote_count > 0:
                for i in range(footnote_count):
                    footnote = footnote_elements.nth(i)
                    footnote_ps = footnote.locator('p')
                    p_count = await footnote_ps.count()

                    for j in range(p_count):
                        p_text = await self.extract_html_with_formatting(footnote_ps.nth(j))
                        if p_text.strip():
                            footnotes.append(p_text.strip())

            # 收集 figure 中的圖片
            figure_images = []
            for item in content_items:
                if item['type'] == 'figure' and 'image_src' in item:
                    figure_images.append({
                        'src': item['image_src'],
                        'alt': item['image_alt']
                    })

            return {
                'name': chapter_name,
                'order_num': order_num,  # 章節排序號
                'content_items': content_items,
                'images': images,
                'figure_images': figure_images,  # figure 中的圖片
                'footnotes': footnotes,
                'is_toc': is_toc or '目錄' in chapter_name,  # 是否為目錄頁
                'toc_links': toc_links  # 目錄鏈接列表
            }

        except Exception as e:
            logger.info(f"         ⚠️  從 iframe 抓取章節時發生錯誤: {e}")
            return None

    async def _scrape_from_single_iframe(self, iframe: FrameLocator) -> Dict[str, any]:
        """
        從單個 iframe 抓取內容（舊版本，保留向後兼容）

        Args:
            iframe: iframe locator

        Returns:
            包含標題、段落和圖片的字典
        """
        content = {
            'headings': [],
            'paragraphs': [],
            'images': []
        }

        try:

            # 抓取標題 (h1, h2, h3, h4, h5, h6)
            for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                elements = iframe.locator(tag)
                count = await elements.count()

                for i in range(count):
                    # 使用新方法提取包含格式的文字
                    text = await self.extract_html_with_formatting(elements.nth(i))
                    if text and text.strip():
                        content['headings'].append({
                            'level': tag,
                            'text': text.strip()
                        })

            # 抓取段落（包含一般段落和腳註）
            paragraphs = iframe.locator('p')
            p_count = await paragraphs.count()

            for i in range(p_count):
                # 使用新方法提取包含格式的文字
                text = await self.extract_html_with_formatting(paragraphs.nth(i))
                if text and text.strip():
                    content['paragraphs'].append(text.strip())

            # 額外抓取 footnote（腳註）
            footnotes = iframe.locator('.footnote[role="doc-endnote"]')
            footnote_count = await footnotes.count()

            if footnote_count > 0:
                content['paragraphs'].append('\n---\n\n**註釋：**\n')

                for i in range(footnote_count):
                    footnote = footnotes.nth(i)
                    # 獲取 footnote 內的所有段落
                    fn_paragraphs = footnote.locator('p')
                    fn_p_count = await fn_paragraphs.count()

                    for j in range(fn_p_count):
                        text = await self.extract_html_with_formatting(fn_paragraphs.nth(j))
                        if text and text.strip():
                            content['paragraphs'].append(text.strip())

            # 抓取圖片 (HTML img 標籤)
            images = iframe.locator('img')
            img_count = await images.count()

            for i in range(img_count):
                src = await images.nth(i).get_attribute('src')
                alt = await images.nth(i).get_attribute('alt')
                if src:
                    content['images'].append({
                        'src': src,
                        'alt': alt or ''
                    })

            # 抓取圖片 (SVG image 標籤)
            svg_images = iframe.locator('image')
            svg_img_count = await svg_images.count()

            for i in range(svg_img_count):
                # SVG 使用 xlink:href 或 href 屬性
                src = await svg_images.nth(i).get_attribute('xlink:href')
                if not src:
                    src = await svg_images.nth(i).get_attribute('href')

                if src:
                    # 處理相對路徑，轉換為絕對 URL
                    # 獲取 iframe 的 base URL
                    try:
                        # 嘗試從 iframe 獲取完整 URL
                        base_element = iframe.locator('base').first
                        base_href = await base_element.get_attribute('href')

                        if base_href and src.startswith('../'):
                            # 處理相對路徑
                            # ../Images/cover.jpg -> 從 base_href 計算完整路徑
                            full_url = urljoin(base_href, src)
                            src = full_url
                    except:
                        # 如果失敗，保持原樣
                        pass

                    content['images'].append({
                        'src': src,
                        'alt': 'SVG 圖片'
                    })

            return content

        except Exception as e:
            logger.info(f"         ⚠️  從 iframe 抓取內容時發生錯誤: {e}")
            return {'headings': [], 'paragraphs': [], 'images': []}

    async def download_image(self, url: str, page_number: int, base_url: str = None) -> str:
        """
        下載圖片到本地

        Args:
            url: 圖片 URL（可能是相對路徑或 data URL）
            page_number: 頁碼
            base_url: 基礎 URL（用於解析相對路徑）

        Returns:
            本地圖片路徑（相對於 Markdown 檔案）
        """
        # 檢查是否已下載
        if url in self.downloaded_images:
            return self.downloaded_images[url]

        try:
            # 處理 data URL（例如 Canvas 生成的圖片）
            if url.startswith('data:image'):
                import base64
                
                # 解析 data URL
                # 格式: data:image/png;base64,iVBORw0KGgoAAAANS...
                match = re.match(r'data:image/(\w+);base64,(.+)', url)
                if match:
                    img_format = match.group(1)
                    img_data = match.group(2)
                    
                    # 生成檔案名稱
                    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                    filename = f"page_{page_number:04d}_canvas_{url_hash}.{img_format}"
                    
                    local_path = self.images_dir / filename
                    
                    # 解碼並保存圖片
                    with open(local_path, 'wb') as f:
                        f.write(base64.b64decode(img_data))
                    
                    # 記錄下載
                    relative_path = f"images/book_{self.book_id}/{filename}"
                    self.downloaded_images[url] = relative_path
                    
                    logger.info(f"      🎨 已保存 Canvas 圖片: {filename}")
                    return relative_path
                else:
                    logger.info(f"      ⚠️  無法解析 data URL")
                    return url
            
            # 處理相對路徑
            download_url = url
            if not url.startswith(('http://', 'https://')):
                if base_url:
                    # 使用 urljoin 轉換相對路徑為絕對路徑
                    download_url = urljoin(base_url, url)
                    logger.info(f"      🔗 轉換 URL: {url} -> {download_url}")
                else:
                    logger.info(f"      ⚠️  無法下載相對路徑圖片（缺少 base_url）: {url}")
                    return url

            # 生成檔案名稱（使用 URL hash + 頁碼）
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            ext = Path(url).suffix or '.jpg'
            filename = f"page_{page_number:04d}_{url_hash}{ext}"

            local_path = self.images_dir / filename

            # 下載圖片
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(download_url)
                response.raise_for_status()

                # 保存圖片
                with open(local_path, 'wb') as f:
                    f.write(response.content)

            # 記錄下載（相對於 downloads 目錄的路徑）
            relative_path = f"images/book_{self.book_id}/{filename}"
            self.downloaded_images[url] = relative_path

            logger.info(f"      📥 已下載圖片: {filename}")
            return relative_path

        except Exception as e:
            logger.info(f"      ⚠️  下載圖片失敗 ({url[:100]}...): {e}")
            # 下載失敗時返回原 URL
            return url

    def extract_chapter_number(self, chapter_name: str, order_num: int = None) -> tuple:
        """
        從章節名稱中提取章節編號

        Args:
            chapter_name: 章節名稱
            order_num: 已提取的排序號（優先使用）

        Returns:
            (章節類型, 章節編號)
            - 章節類型: 'front' (前置), 'main' (正文), 'back' (後置)
            - 章節編號: 數字或 None
        """
        import re

        # 如果已經有排序號，直接使用
        if order_num is not None:
            return ('main', order_num)

        # 前置內容的關鍵字及其優先順序
        front_keywords = {
            '__no_chapter__': 0,  # 封面
            '封面': 0,
            'cover': 0,
            '推薦序': 1,
            '推薦': 1,
            'recommendation': 1,
            '序': 2,
            'preface': 2,
            '前言': 3,
            'foreword': 3,
            'introduction': 3,
            '導讀': 4,
            '目錄': 5,
            'contents': 5,
            'table of contents': 5,
            '目次': 5,
        }

        # 後置內容的關鍵字
        back_keywords = [
            '附錄', 'appendix', '參考文獻', 'references',
            '版權', 'copyright', '致謝', 'acknowledgment',
            '作者', 'author', '關於作者', 'about the author',
            '後記', 'epilogue', 'afterword'
        ]

        chapter_lower = chapter_name.lower().strip()

        # 檢查是否為前置內容
        for keyword, priority in front_keywords.items():
            if keyword in chapter_lower:
                return ('front', priority)

        # 檢查是否為後置內容
        for keyword in back_keywords:
            if keyword in chapter_lower:
                return ('back', 0)

        # 嘗試提取章節編號（正文）
        # 模式 1: Chapter 1, Chapter 2, CHAPTER 1, etc.
        match = re.search(r'chapter\s+(\d+)', chapter_lower)
        if match:
            return ('main', int(match.group(1)))

        # 模式 2: 第一章, 第二章, 第1章, 第2章
        match = re.search(r'第\s*([一二三四五六七八九十百\d]+)\s*章', chapter_name)
        if match:
            num_str = match.group(1)
            # 轉換中文數字為阿拉伯數字
            chinese_nums = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
                            '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
                            '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20}
            if num_str in chinese_nums:
                return ('main', chinese_nums[num_str])
            elif num_str.isdigit():
                return ('main', int(num_str))

        # 模式 3: 1. 標題, 2. 標題
        match = re.search(r'^(\d+)[\.、]\s*', chapter_name)
        if match:
            return ('main', int(match.group(1)))

        # 模式 4: Chapter I, Chapter II (羅馬數字)
        match = re.search(r'chapter\s+([ivxlcdm]+)', chapter_lower)
        if match:
            roman = match.group(1).upper()
            # 簡單的羅馬數字轉換
            roman_values = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5,
                            'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10}
            if roman in roman_values:
                return ('main', roman_values[roman])

        # 如果無法識別，視為前置內容，放在最後
        return ('front', 999)

    def sort_chapters(self, chapter_order: list, chapters: dict) -> list:
        """
        對章節進行智能排序

        Args:
            chapter_order: 原始章節順序列表
            chapters: 章節資料字典

        Returns:
            排序後的章節列表
        """
        # 為每個章節提取排序資訊
        chapter_info = []
        for chapter_name in chapter_order:
            # 從 chapters 字典中獲取章節的 order_num
            chapter_data = chapters.get(chapter_name, {})
            order_num = chapter_data.get('order_num')

            chapter_type, chapter_num = self.extract_chapter_number(chapter_name, order_num)
            chapter_info.append((chapter_name, chapter_type, chapter_num))

        # 排序規則：
        # 1. 先按類型排序：front < main < back
        # 2. 同類型內按編號排序
        type_order = {'front': 0, 'main': 1, 'back': 2}

        def sort_key(item):
            name, ch_type, ch_num = item
            type_priority = type_order[ch_type]
            num_priority = ch_num if ch_num is not None else 999
            return (type_priority, num_priority)

        sorted_info = sorted(chapter_info, key=sort_key)

        # 返回排序後的章節名稱列表
        return [name for name, _, _ in sorted_info]

    async def download_images_for_chapter(self, chapter_data: Dict[str, any], page_number: int, base_url: str = None):
        """
        為章節下載所有圖片（包含 figure, container 中的圖片）

        Args:
            chapter_data: 章節資料字典
            page_number: 頁碼（用於生成檔案名）
            base_url: 基礎 URL
        """
        # 下載獨立圖片
        for image in chapter_data['images']:
            url = image['src']
            local_path = await self.download_image(url, page_number, base_url)
            image['local_path'] = local_path

        # 下載 figure 中的圖片
        for image in chapter_data.get('figure_images', []):
            url = image['src']
            local_path = await self.download_image(url, page_number, base_url)
            image['local_path'] = local_path
        
        # 下載 content_items 中的圖片（來自 div.container）
        for item in chapter_data.get('content_items', []):
            if item.get('type') in ['image', 'figure']:
                img_src = item.get('image_src')
                if img_src:
                    # 檢查是否已在 images 或 figure_images 中
                    already_downloaded = False
                    for img in chapter_data['images']:
                        if img['src'] == img_src:
                            already_downloaded = True
                            break
                    if not already_downloaded:
                        for img in chapter_data.get('figure_images', []):
                            if img['src'] == img_src:
                                already_downloaded = True
                                break
                    
                    # 如果還沒下載，添加到 images 列表並下載
                    if not already_downloaded:
                        local_path = await self.download_image(img_src, page_number, base_url)
                        chapter_data['images'].append({
                            'src': img_src,
                            'alt': item.get('image_alt', '圖片'),
                            'local_path': local_path
                        })

    def _generate_anchor_id(self, chapter_name: str) -> str:
        """
        從章節名稱生成 Markdown 錨點 ID

        Args:
            chapter_name: 章節名稱

        Returns:
            錨點 ID
        """
        import re
        # 移除特殊字符，保留中英文數字
        anchor = re.sub(r'[^\w\s\-]', '', chapter_name)
        # 替換空格為連字符
        anchor = re.sub(r'\s+', '-', anchor)
        return anchor.lower()

    async def convert_chapter_to_markdown(self, chapter_data: Dict[str, any], chapter_map: dict = None, toc_anchor: str = None, is_toc_chapter: bool = False) -> str:
        """
        將章節資料轉換為 Markdown 格式

        Args:
            chapter_data: 章節資料字典
            chapter_map: 章節名稱到錨點 ID 的映射字典（用於目錄交叉引用）
            toc_anchor: 目錄的錨點 ID（用於"回到目錄"鏈接）
            is_toc_chapter: 是否為目錄章節

        Returns:
            Markdown 格式的文字
        """
        markdown_lines = []

        # 如果是目錄頁，特殊處理
        if chapter_data.get('is_toc') and chapter_data.get('toc_links'):
            markdown_lines.append("\n## 目錄\n\n")

            for toc_item in chapter_data['toc_links']:
                title = toc_item['title']

                # 查找對應的章節錨點
                if chapter_map:
                    # 嘗試在章節映射中找到匹配的章節
                    anchor = None
                    for ch_name, ch_anchor in chapter_map.items():
                        # 簡單的標題匹配
                        if title in ch_name or ch_name in title:
                            anchor = ch_anchor
                            break

                    if anchor:
                        # 生成內部鏈接
                        markdown_lines.append(f"- [{title}](#{anchor})\n")
                    else:
                        # 沒有找到對應章節，只顯示文本
                        markdown_lines.append(f"- {title}\n")
                else:
                    markdown_lines.append(f"- {title}\n")

            markdown_lines.append("\n")
            return ''.join(markdown_lines)

        # 處理有序內容（包含 figure, image, caption, footnote）
        for item in chapter_data['content_items']:
            item_type = item['type']
            content = item.get('content', '')

            if item_type == 'h1':
                markdown_lines.append(f"\n## {content}\n")
            elif item_type == 'h2':
                markdown_lines.append(f"\n### {content}\n")
            elif item_type == 'h3':
                markdown_lines.append(f"\n#### {content}\n")
            elif item_type == 'h4':
                markdown_lines.append(f"\n##### {content}\n")
            elif item_type == 'h5':
                markdown_lines.append(f"\n###### {content}\n")
            elif item_type == 'h6':
                markdown_lines.append(f"\n###### {content}\n")
            elif item_type == 'p':
                markdown_lines.append(f"{content}\n")
            elif item_type == 'image':
                # 處理獨立圖片（來自 div.container）
                img_src = item.get('image_src', '')
                img_alt = item.get('image_alt', '圖片')

                # 使用本地路徑（如果已下載）
                img_path = img_src
                for img in chapter_data.get('images', []):
                    if img['src'] == img_src:
                        img_path = img.get('local_path', img_src)
                        break

                markdown_lines.append(f"\n![{img_alt}]({img_path})\n")
            elif item_type == 'caption':
                # 處理圖片說明文字（來自 div.container）
                markdown_lines.append(f"\n*{content}*\n\n")
            elif item_type == 'figure':
                # 處理 figure（圖片 + 說明）
                img_src = item.get('image_src', '')
                img_alt = item.get('image_alt', '圖片')

                # 使用本地路徑（如果已下載）
                # 注意：這裡需要從 images 列表中查找對應的本地路徑
                img_path = img_src
                for img in chapter_data.get('figure_images', []):
                    if img['src'] == img_src:
                        img_path = img.get('local_path', img_src)
                        break

                markdown_lines.append(f"\n![{img_alt}]({img_path})\n\n")

        # 處理獨立圖片（不在 figure 和 container 內的）
        # 收集 content_items 中已經輸出的圖片 URL，避免重複
        output_image_srcs = set()
        for item in chapter_data['content_items']:
            if item.get('type') in ['image', 'figure']:
                img_src = item.get('image_src')
                if img_src:
                    output_image_srcs.add(img_src)
        
        # 只輸出未在 content_items 中出現的圖片
        remaining_images = [img for img in chapter_data['images'] if img['src'] not in output_image_srcs]
        
        if remaining_images:
            markdown_lines.append("\n")
            for image in remaining_images:
                # 優先使用本地路徑
                img_path = image.get('local_path', image['src'])
                alt_text = image.get('alt', '圖片')
                markdown_lines.append(f"![{alt_text}]({img_path})\n")

        # 處理註釋
        if chapter_data['footnotes']:
            markdown_lines.append("\n---\n\n**註釋：**\n\n")
            for footnote in chapter_data['footnotes']:
                markdown_lines.append(f"{footnote}\n\n")

        # 在章節末尾添加"回到目錄"鏈接（除了目錄頁本身）
        # if not is_toc_chapter and toc_anchor:
        #     markdown_lines.append("\n---\n\n")
        #     markdown_lines.append(f"[📚 回到目錄](#{toc_anchor})\n")

        return ''.join(markdown_lines)

    def convert_to_markdown(self, content: Dict[str, any], page_number: int = 0) -> str:
        """
        將內容轉換為 Markdown 格式

        Args:
            content: 包含標題、段落和圖片的字典
            page_number: 頁碼（用於圖片路徑）

        Returns:
            Markdown 格式的文字
        """
        markdown = []

        # 轉換標題（h1 -> ##, h2 -> ###, h3 -> ####, 以此類推）
        for heading in content['headings']:
            level = int(heading['level'][1])  # h1 -> 1, h2 -> 2, h3 -> 3
            # h1 對應到 ##（2個#），h2 對應到 ###（3個#）
            prefix = '#' * (level + 1)
            markdown.append(f"{prefix} {heading['text']}\n")

        # 轉換段落（已包含粗體和斜體）
        for paragraph in content['paragraphs']:
            markdown.append(f"{paragraph}\n")

        # 轉換圖片（使用本地路徑或 URL）
        for image in content['images']:
            alt_text = image['alt'] or '圖片'
            img_path = image.get('local_path', image['src'])  # 優先使用本地路徑
            markdown.append(f"![{alt_text}]({img_path})\n")

        return '\n'.join(markdown)

    async def get_reading_progress(self, page: Page) -> dict:
        """
        獲取閱讀進度信息

        Args:
            page: Playwright 頁面物件

        Returns:
            包含進度信息的字典 {'total_percent': 100, 'chapter_current': 4, 'chapter_total': 4}
        """
        try:
            # 定位進度容器
            progress_container = page.locator('#page-info-container')

            # 等待元素出現
            await progress_container.wait_for(state="visible", timeout=5000)

            # 獲取文字內容
            progress_text = await progress_container.text_content()

            # 解析進度文字
            # 格式：全文 10%．本章第 1 頁 / 4 頁
            progress_info = {
                'total_percent': 0,
                'chapter_current': 0,
                'chapter_total': 0,
                'text': progress_text.strip()
            }

            # 提取全文百分比
            total_match = re.search(r'全文\s*(\d+)%', progress_text)
            if total_match:
                progress_info['total_percent'] = int(total_match.group(1))

            # 提取本章頁數
            chapter_match = re.search(r'本章第?\s*(\d+)\s*頁\s*/\s*(\d+)\s*頁', progress_text)
            if chapter_match:
                progress_info['chapter_current'] = int(chapter_match.group(1))
                progress_info['chapter_total'] = int(chapter_match.group(2))

            return progress_info

        except Exception as e:
            logger.info(f"      ⚠️  無法獲取閱讀進度: {e}")
            return {
                'total_percent': 0,
                'chapter_current': 0,
                'chapter_total': 0,
                'text': ''
            }

    async def is_last_page(self, page: Page) -> bool:
        """
        檢查是否為最後一頁

        Args:
            page: Playwright 頁面物件

        Returns:
            是否為最後一頁
        """
        progress = await self.get_reading_progress(page)

        # 判斷條件：全文 100% 且本章到最後一頁
        is_last = (
                progress['total_percent'] == 100 and
                progress['chapter_current'] > 0 and
                progress['chapter_current'] == progress['chapter_total']
        )

        return is_last

    async def turn_page(self, page: Page) -> bool:
        """
        翻到下一頁（使用配置的按鍵）

        Args:
            page: Playwright 頁面物件

        Returns:
            是否成功翻頁
        """
        try:
            # 按下配置的翻頁按鍵
            await page.keyboard.press(self.page_turn_key)

            # 等待頁面載入
            await asyncio.sleep(0.1)

            return True

        except Exception as e:
            logger.warning(f"⚠️  翻頁時發生錯誤: {e}")
            return False

    async def download_images_for_content(self, content: Dict[str, any], page_number: int, base_url: str = None):
        """
        下載內容中的所有圖片

        Args:
            content: 包含圖片列表的內容字典
            page_number: 頁碼
            base_url: 基礎 URL（用於解析相對路徑）
        """
        if not self.download_images or not content['images']:
            return

        for image in content['images']:
            url = image['src']

            # 下載圖片並更新為本地路徑
            local_path = await self.download_image(url, page_number, base_url)
            image['local_path'] = local_path

    async def scrape_canvas_from_iframe(self, iframe: FrameLocator, page_number: int) -> list:
        """
        從單個 iframe 中抓取所有 Canvas 圖片（帶 MD5 去重）

        Args:
            iframe: iframe locator
            page_number: 頁碼

        Returns:
            Canvas 圖片資訊列表
        """
        canvas_images = []
        
        try:
            body = iframe.locator('body')
            
            # 找到所有 Canvas 元素
            canvas_elements = body.locator('canvas')
            canvas_count = await canvas_elements.count()
            
            if canvas_count == 0:
                return canvas_images
            
            logger.info(f"         🎨 找到 {canvas_count} 個 Canvas 元素")
            
            for i in range(canvas_count):
                canvas = canvas_elements.nth(i)
                
                try:
                    # 等待 Canvas 渲染完成
                    canvas_ready = False
                    for attempt in range(6):
                        has_content = await canvas.evaluate('''
                            canvas => {
                                try {
                                    const ctx = canvas.getContext('2d');
                                    if (!ctx) return false;
                                    
                                    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                                    const data = imageData.data;
                                    
                                    for (let i = 3; i < data.length; i += 4) {
                                        if (data[i] > 0) return true;
                                    }
                                    return false;
                                } catch (e) {
                                    return false;
                                }
                            }
                        ''')
                        
                        if has_content:
                            canvas_ready = True
                            if attempt > 0:
                                logger.info(f"         ✓ Canvas[{i}] 已渲染完成（嘗試 {attempt + 1} 次）")
                            break
                        
                        if attempt < 5:
                            await asyncio.sleep(0.2)
                    
                    if not canvas_ready:
                        logger.info(f"         ⚠️  Canvas[{i}] 可能為空或未渲染完成，跳過")
                        continue
                    
                    # 轉換為 data URL
                    data_url = await canvas.evaluate('''
                        canvas => {
                            try {
                                return canvas.toDataURL('image/png');
                            } catch (e) {
                                return null;
                            }
                        }
                    ''')
                    
                    if not data_url or not data_url.startswith('data:image'):
                        logger.info(f"         ⚠️  Canvas[{i}] 轉換失敗")
                        continue
                    
                    # 檢查大小
                    data_size = len(data_url)
                    if data_size <= 5000:
                        logger.info(f"         ⚠️  Canvas[{i}] 圖片過小 ({data_size} bytes)，跳過")
                        continue
                    
                    # 計算 MD5 hash 用於去重
                    canvas_hash = hashlib.md5(data_url.encode()).hexdigest()
                    
                    # 檢查是否重複
                    if canvas_hash in self.canvas_hashes:
                        logger.info(f"         🔄 Canvas[{i}] 重複（MD5: {canvas_hash[:8]}...），已跳過")
                        continue
                    
                    # 記錄 hash
                    self.canvas_hashes.add(canvas_hash)
                    
                    # 保存圖片
                    import base64
                    match = re.match(r'data:image/(\w+);base64,(.+)', data_url)
                    if match:
                        img_format = match.group(1)
                        img_data = match.group(2)
                        
                        # 使用 MD5 hash 作為檔案名的一部分（保證唯一性）
                        filename = f"page_{page_number:04d}_canvas_{canvas_hash[:12]}.{img_format}"
                        local_path_full = self.images_dir / filename
                        
                        # 解碼並保存
                        with open(local_path_full, 'wb') as f:
                            f.write(base64.b64decode(img_data))
                        
                        relative_path = f"images/book_{self.book_id}/{filename}"
                        
                        canvas_images.append({
                            'page': page_number,
                            'canvas_index': i,
                            'path': relative_path,
                            'size': data_size,
                            'hash': canvas_hash
                        })
                        
                        logger.info(f"         ✅ Canvas[{i}] 已保存: {filename} ({data_size / 1024:.1f} KB, MD5: {canvas_hash[:8]}...)")
                    
                except Exception as e:
                    logger.info(f"         ⚠️  Canvas[{i}] 處理失敗: {e}")
                    continue
        
        except Exception as e:
            logger.info(f"         ⚠️  掃描 iframe Canvas 失敗: {e}")
        
        return canvas_images

    async def scrape_image_only_book(self, reading_page: Page) -> str:
        """
        爬取純圖片書籍（所有頁面都是 Canvas）

        Args:
            reading_page: 閱讀頁面的 Page 物件

        Returns:
            完整的 Markdown 內容
        """
        logger.info("\n" + "=" * 60)
        logger.info("📚 開始爬取純圖片書籍（Canvas Only 模式）")
        logger.info("=" * 60)
        
        # 建立圖片目錄
        self.images_dir = Path("downloads") / "images" / f"book_{self.book_id}"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"📁 圖片將保存到: {self.images_dir}")

        await asyncio.sleep(0.5)

        # 處理閱讀進度彈窗（如果有）
        await self.handle_reading_progress_popup(reading_page)

        await asyncio.sleep(0.5)

        # 點擊「我知道了」按鈕
        await self.click_accept_button(reading_page)

        # 儲存所有 Canvas 圖片
        all_canvas_images = []
        page_number = 0
        consecutive_no_content = 0
        max_no_content = 10  # 連續 10 頁無內容就停止
        
        while page_number < self.max_pages and consecutive_no_content < max_no_content:
            page_number += 1
            
            # 獲取閱讀進度
            progress = await self.get_reading_progress(reading_page)
            logger.info(f"\n📖 正在掃描第 {page_number} 頁... [{progress['text']}] (進度: {progress['total_percent']}%)")
            
            # 獲取所有可見的 iframe
            visible_iframes = await self.get_all_visible_iframes(reading_page)
            
            found_canvas = False
            
            # 從每個 iframe 抓取 Canvas
            for iframe_index, iframe in enumerate(visible_iframes):
                logger.info(f"      📄 正在掃描 iframe[{iframe_index}]...")
                
                canvas_images = await self.scrape_canvas_from_iframe(iframe, page_number)
                
                if canvas_images:
                    all_canvas_images.extend(canvas_images)
                    found_canvas = True
                    logger.info(f"      ✅ iframe[{iframe_index}] 找到 {len(canvas_images)} 張新圖片")
                else:
                    logger.info(f"      ℹ️  iframe[{iframe_index}] 無新 Canvas 圖片")
            
            # 更新連續無內容計數
            if found_canvas:
                consecutive_no_content = 0
            else:
                consecutive_no_content += 1
                logger.info(f"   ⚠️  本頁無新內容（連續 {consecutive_no_content}/{max_no_content}）")
            
            # 檢查終止條件
            # 1. 檢測「閱讀結束」標記
            try:
                reading_end = reading_page.locator('div.sc-1wqquil-3:has-text("閱讀結束")')
                if await reading_end.count() > 0:
                    logger.success("✅ 檢測到「閱讀結束」標記，停止爬取")
                    break
            except:
                pass
            
            # 2. 檢查是否為最後一頁
            if await self.is_last_page(reading_page):
                logger.success("✅ 已到達最後一頁（全文 100% 且本章最後一頁）")
                break
            
            # 3. 連續無新內容
            if consecutive_no_content >= max_no_content:
                logger.warning(f"⚠️  連續 {max_no_content} 頁無新內容，停止爬取")
                break
            
            # 翻頁
            logger.info(f"   ⏭️  翻到下一頁...")
            success = await self.turn_page(reading_page)
            if not success:
                logger.info(f"   ⚠️  翻頁失敗")
                break
            
            await asyncio.sleep(0.1)
        
        logger.info("\n" + "=" * 60)
        logger.success(f"✅ 爬取完成！")
        logger.info(f"   - 共掃描: {page_number} 頁")
        logger.info(f"   - 找到圖片: {len(all_canvas_images)} 張")
        logger.info(f"   - 去重後: {len(self.canvas_hashes)} 張唯一圖片")
        logger.info("=" * 60)
        
        # 生成 Markdown 內容
        markdown_lines = []
        
        for idx, img in enumerate(all_canvas_images, 1):
            markdown_lines.append(f"![第 {img['page']} 頁]({img['path']})\n")
        
        return '\n'.join(markdown_lines)

    def _get_item_preview(self, item: dict) -> str:
        """
        獲取 content_item 的預覽文字（處理不同類型）
        
        Args:
            item: content_item 字典
            
        Returns:
            預覽文字（最多 60 字符）
        """
        item_type = item.get('type', 'unknown')
        
        if item_type == 'image':
            # image 類型：顯示圖片來源
            img_src = item.get('image_src', '')
            img_alt = item.get('image_alt', '圖片')
            return f"[圖片] {img_alt} ({img_src[:40]}...)" if len(img_src) > 40 else f"[圖片] {img_alt} ({img_src})"
        elif item_type == 'figure':
            # figure 類型：顯示說明文字和圖片來源
            content = item.get('content', '')
            img_src = item.get('image_src', '')
            preview = content[:30] if len(content) > 30 else content
            return f"[圖表] {preview}... ({img_src[:20]}...)" if len(content) > 30 else f"[圖表] {preview} ({img_src[:20]}...)"
        elif item_type == 'caption':
            # caption 類型：顯示說明文字
            content = item.get('content', '')
            return f"[說明] {content[:50]}..." if len(content) > 50 else f"[說明] {content}"
        else:
            # 其他類型（h1-h6, p）：顯示文字內容
            content = item.get('content', '')
            return f"{content[:60]}..." if len(content) > 60 else content
    
    def _renumber_footnotes(self, chapters_list: list, starting_number: int = 1) -> int:
        """
        為所有章節的 footnote 重新編號（避免跨章節編號衝突）
        
        Args:
            chapters_list: 章節列表 [(chapter_data, content_hash), ...]
            starting_number: 起始編號
            
        Returns:
            下一個可用的 footnote 編號
        """
        current_number = starting_number
        
        for chapter_data, _ in chapters_list:
            # 建立該章節的 footnote 編號映射表 (原編號 -> 新編號)
            footnote_map = {}
            
            # 第一步：先收集所有 footnote 定義，建立映射表
            # 只掃描定義，不掃描引用，避免重複計數
            for item in chapter_data.get('content_items', []):
                if item.get('type') == 'p':
                    content = item.get('content', '')
                    # 檢查是否為 footnote 定義（以 [^數字]: 開頭）
                    footnote_def_match = re.match(r'\[\^(\d+)\]:', content)
                    if footnote_def_match:
                        old_num = footnote_def_match.group(1)
                        if old_num not in footnote_map:
                            footnote_map[old_num] = str(current_number)
                            current_number += 1
            
            # 第二步：替換所有 content_items 中的 footnote 引用和定義編號
            for item in chapter_data.get('content_items', []):
                if item.get('type') in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'caption']:
                    content = item.get('content', '')
                    
                    # 替換所有 footnote 引用和定義
                    # 注意：必須按照從大到小的順序替換，避免子串替換問題
                    # 例如：先替換 [^10] 再替換 [^1]，否則 [^10] 會變成 [^新1]0
                    sorted_old_nums = sorted(footnote_map.keys(), key=lambda x: int(x), reverse=True)
                    
                    for old_num in sorted_old_nums:
                        new_num = footnote_map[old_num]
                        # 替換引用：[^1] -> [^新編號]
                        content = re.sub(rf'\[\^{old_num}\](?!:)', f'[^{new_num}]', content)
                        # 替換定義：[^1]: -> [^新編號]:
                        content = re.sub(rf'\[\^{old_num}\]:', f'[^{new_num}]:', content)
                    
                    item['content'] = content
        
        return current_number

    def _generate_chapter_hash(self, chapter_data: Dict[str, any]) -> str:
        """
        為章節內容生成唯一的哈希值（基於文字內容和圖片）

        Args:
            chapter_data: 章節資料字典

        Returns:
            MD5 哈希值
        """
        # 收集所有文字內容和圖片信息
        content_parts = []
        
        for item in chapter_data.get('content_items', []):
            item_type = item.get('type', '')
            
            if item_type == 'image':
                # image 類型：使用圖片來源
                content_parts.append(f"[IMAGE:{item.get('image_src', '')}]")
            elif item_type == 'figure':
                # figure 類型：使用說明文字 + 圖片來源
                content_parts.append(f"[FIGURE:{item.get('content', '')}:{item.get('image_src', '')}]")
            else:
                # 其他類型：使用文字內容
                content_parts.append(item.get('content', ''))
        
        # 收集所有獨立圖片 URL
        for img in chapter_data.get('images', []):
            content_parts.append(f"[IMG:{img.get('src', '')}]")
        
        # 收集所有 figure 圖片 URL
        for img in chapter_data.get('figure_images', []):
            content_parts.append(f"[FIG:{img.get('src', '')}]")
        
        # 組合成唯一字符串
        unique_string = '|||'.join(content_parts)
        
        # 生成 MD5 哈希
        return hashlib.md5(unique_string.encode('utf-8')).hexdigest()

    async def scrape_entire_book(self, reading_page: Page) -> str:
        """
        爬取整本書的內容（按 iframe 出現順序，使用內容哈希去重）

        Args:
            reading_page: 閱讀頁面的 Page 物件

        Returns:
            完整的 Markdown 內容
        """
        logger.info("\n" + "=" * 60)
        logger.info("📚 開始爬取書籍內容（按 iframe 順序）")
        logger.info("=" * 60)

        # 如果需要下載圖片，建立圖片目錄
        if self.download_images:
            self.images_dir = Path("downloads") / "images" / f"book_{self.book_id}"
            self.images_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"📁 圖片將保存到: {self.images_dir}")

        # 等待頁面完全載入
        await asyncio.sleep(0.5)

        # 處理閱讀進度彈窗（如果有）
        await self.handle_reading_progress_popup(reading_page)

        # 等待頁面完全載入
        await asyncio.sleep(0.5)

        # 點擊「我知道了」按鈕
        await self.click_accept_button(reading_page)

        # 使用列表按順序存儲章節（保持 iframe 出現順序）
        chapters_list = []  # [(chapter_data, chapter_hash), ...]
        processed_hashes = set()  # 已處理的內容哈希
        toc_links = []  # TOC 目錄鏈接（用於智能排序）

        page_number = 0
        full_progress_count = 0  # 記錄連續出現全文 100% 的次數

        # 獲取 base URL（用於圖片下載）
        base_url = await self.get_base_url_from_iframe(reading_page)
        if base_url:
            logger.info(f"📍 Base URL: {base_url}")

        # 🔍 嘗試從第一頁提取 TOC（目錄）信息
        try:
            first_iframes = await self.get_all_visible_iframes(reading_page)
            for iframe in first_iframes:
                if await self.is_toc_page(iframe):
                    toc_links = await self.extract_toc_links(iframe)
                    if toc_links:
                        logger.success(f"✅ 已提取 TOC 目錄（共 {len(toc_links)} 項）")
                        break
        except Exception as e:
            logger.warning(f"⚠️  提取 TOC 失敗: {e}")

        while page_number < self.max_pages:
            page_number += 1

            # 獲取閱讀進度
            progress = await self.get_reading_progress(reading_page)
            logger.info(f"\n📖 正在掃描第 {page_number} 頁... [{progress['text']}] (進度: {progress['total_percent']}%)")

            # 獲取所有可見的 iframe（按順序）
            visible_iframes = await self.get_all_visible_iframes(reading_page)

            found_new_content = False

            # 按 iframe[0], iframe[1], iframe[2]... 的順序處理
            for iframe_index, iframe in enumerate(visible_iframes):
                logger.info(f"      📄 正在抓取 iframe[{iframe_index}]...")

                # 抓取章節資料（傳遞 TOC 用於智能排序）
                chapter_data = await self.scrape_chapter_from_iframe(iframe, base_url, toc_links)

                if not chapter_data:
                    logger.info(f"         ⚠️  iframe[{iframe_index}] 沒有內容")
                    continue

                # 生成內容哈希（基於文字+圖片）
                content_hash = self._generate_chapter_hash(chapter_data)

                # 檢查是否為新內容（用哈希判斷，不用章節名）
                if content_hash not in processed_hashes:
                    # 新內容，加入列表
                    chapters_list.append((chapter_data, content_hash))
                    processed_hashes.add(content_hash)
                    found_new_content = True

                    chapter_name = chapter_data['name']
                    display_name = chapter_name if chapter_name != "__no_chapter__" else "【無章節名稱】"
                    logger.info(f"         ✅ 新內容 (#{len(chapters_list)}): {display_name}")
                    logger.info(f"            哈希: {content_hash[:12]}...")

                    # DEBUG: 顯示內容預覽
                    if chapter_data['content_items']:
                        first_item = chapter_data['content_items'][0]
                        last_item = chapter_data['content_items'][-1]
                        
                        # 獲取第一項預覽（處理不同類型）
                        first_preview = self._get_item_preview(first_item)
                        logger.debug(f"         🔍 第一項 ({first_item['type']}): {first_preview}")
                        
                        # 獲取最後項預覽（處理不同類型）
                        last_preview = self._get_item_preview(last_item)
                        logger.debug(f"         🔍 最後項 ({last_item['type']}): {last_preview}")

                    total_images = len(chapter_data['images']) + len(chapter_data.get('figure_images', []))
                    logger.info(f"         📊 統計: {len(chapter_data['content_items'])} 個元素, {total_images} 張圖片")

                    # 下載圖片（包括 figure 中的圖片）
                    if self.download_images and total_images > 0:
                        await self.download_images_for_chapter(chapter_data, page_number, base_url)
                else:
                    logger.debug(f"         🔄 iframe[{iframe_index}] 內容重複（哈希: {content_hash[:12]}...）")

            # 如果沒有找到新內容，只是提示，不作為終止條件
            if not found_new_content:
                logger.info(f"   ℹ️  本頁所有 iframe 都是已處理過的內容")

            # 檢查是否顯示"閱讀結束"（優先終止條件）
            try:
                reading_end = reading_page.locator('div.sc-1wqquil-3:has-text("閱讀結束")')
                if await reading_end.count() > 0:
                    logger.success("✅ 檢測到「閱讀結束」標記，停止爬取")
                    break
            except Exception as e:
                pass  # 忽略錯誤，繼續檢查其他條件

            # 檢查是否為最後一頁（主要終止條件）
            if await self.is_last_page(reading_page):
                logger.success("✅ 已到達最後一頁（全文 100% 且本章最後一頁）")
                break

            # 安全機制：檢測全文 100% 的情況
            if progress['total_percent'] >= 100:
                full_progress_count += 1

                if not found_new_content:
                    # 如果全文 100% 且沒有新內容
                    logger.info(f"   ⚠️  已達全文 100% 且無新內容（第 {full_progress_count} 次）")

                    if full_progress_count >= 5:
                        # 連續 5 次 100% 且無新內容，提前終止
                        logger.info("   🛑 連續 5 次偵測到全文 100% 且無新內容，停止爬取")
                        logger.info("   💡 提示：這可能是網站進度顯示錯誤（例如：全文 100%．本章第 1 頁 / 2 頁）")
                        break
                else:
                    # 有新內容，說明還沒結束，只是顯示 100%
                    logger.info(f"   ℹ️  已達全文 100% 但發現新內容，繼續爬取...")
                    full_progress_count = 0

                if full_progress_count >= 10:
                    # 保險機制：無論如何，連續 10 次 100% 就停止
                    logger.info("   🛑 連續 10 次偵測到全文 100%，強制停止爬取")
                    break
            else:
                # 重置計數器
                full_progress_count = 0

            # 根據設定選擇翻頁策略
            if self.smart_page_turn:
                # 智能翻頁：根據本章剩餘頁數決定翻多少次（考慮 turn_page 可能一次翻2頁）
                remaining_pages = progress['chapter_total'] - progress['chapter_current']
                current_chapter_page = progress['chapter_current']

                if remaining_pages <= 0:
                    # 章節結束，只翻 1 次到下一章
                    turn_count = 1
                    logger.info(f"   ⏭️  章節已結束，翻 1 次到下一章...")
                elif remaining_pages <= 5:
                    # 接近章節尾部，只翻 1 次（避免跳過內容）
                    turn_count = 1
                    logger.info(f"   ⏭️  本章剩餘 {remaining_pages} 頁，謹慎翻 1 次（當前第 {current_chapter_page}/{progress['chapter_total']} 頁）...")
                elif remaining_pages <= 10:
                    # 章節中後段，翻 2 次
                    turn_count = 2
                    logger.info(f"   ⏭️  本章剩餘 {remaining_pages} 頁，翻 2 次...")
                elif remaining_pages > 15:
                    # 章節前段，快速翻到接近末尾（保留最後 5 頁慢慢翻）
                    # 計算需要翻幾次才能到剩餘 5 頁（假設每次翻 2 頁）
                    target_remaining = 5
                    pages_to_skip = remaining_pages - target_remaining
                    # 保守估計：每次翻頁可能移動 1-2 頁，我們按 1.5 頁計算
                    calculated_turns = max(1, int(pages_to_skip / 1.5))
                    # 限制每次最多翻 10 次（避免一次跳太多）
                    turn_count = min(calculated_turns, 10)
                    logger.info(f"   🚀 本章剩餘 {remaining_pages} 頁，快速翻 {turn_count} 次（上限: 10 次）...")
                else:
                    # 章節中段（11-15頁），翻 3 次
                    turn_count = 3
                    logger.info(f"   ⏭️  本章剩餘 {remaining_pages} 頁，翻 {turn_count} 次...")
            else:
                # 固定翻頁：每次翻固定次數
                turn_count = self.pages_per_turn
                logger.info(f"   ⏭️  使用固定翻頁策略，翻 {turn_count} 次...")

            # 執行翻頁
            for i in range(turn_count):
                if page_number + i >= self.max_pages:
                    break

                success = await self.turn_page(reading_page)
                if not success:
                    logger.warning(f"   ⚠️  第 {i+1} 次翻頁失敗")
                    break

                # 等待頁面加載
                await asyncio.sleep(0.3)
                
                # 在關鍵位置（剩餘5頁以內）檢查實際進度
                if self.smart_page_turn and i == 0 and remaining_pages <= 5:
                    new_progress = await self.get_reading_progress(reading_page)
                    actual_moved = new_progress['chapter_current'] - current_chapter_page
                    if actual_moved > 1:
                        logger.debug(f"      💡 檢測到翻頁實際移動了 {actual_moved} 頁（從 {current_chapter_page} → {new_progress['chapter_current']}）")
                        # 如果一次翻了多頁，就不再繼續翻了
                        break

            page_number += (turn_count - 1)  # 循環會再 +1

        logger.info("\n" + "=" * 60)
        logger.success(f"✅ 爬取完成！共找到 {len(chapters_list)} 個不重複的內容區塊 (掃描 {page_number} 頁)")
        logger.info("=" * 60)

        # 內容已經按 iframe 順序存儲，無需排序
        logger.info("\n" + "=" * 60)
        logger.info("📖 內容已按 iframe 出現順序排列（無需重新排序）")
        logger.info("=" * 60)

        # 建立章節名稱到錨點 ID 的映射
        chapter_map = {}
        toc_anchor = None  # 目錄的錨點 ID

        # 先掃描一遍，建立錨點映射
        for idx, (chapter_data, _) in enumerate(chapters_list):
            chapter_name = chapter_data['name']
            if chapter_name == "目錄":
                toc_anchor = "toc"
                chapter_map[chapter_name] = toc_anchor
            elif chapter_name != "__no_chapter__":
                # 為每個章節生成唯一錨點（加上索引避免重複）
                anchor_id = f"{self._generate_anchor_id(chapter_name)}-{idx}"
                chapter_map[chapter_name] = anchor_id

        # 重新編號所有章節的 footnote（避免跨章節編號衝突）
        logger.info("\n🔢 重新編號 footnote...")
        footnote_count = self._renumber_footnotes(chapters_list)
        if footnote_count > 1:
            logger.info(f"   ✅ 已重新編號 {footnote_count - 1} 個 footnote")

        # 按順序轉換為 Markdown
        all_markdown = []

        for idx, (chapter_data, content_hash) in enumerate(chapters_list, 1):
            chapter_name = chapter_data['name']
            display_name = chapter_name if chapter_name != "__no_chapter__" else "【無章節名稱】"
            logger.info(f"📝 第 {idx} 個區塊: {display_name} (哈希: {content_hash[:12]}...)")

            # 為非目錄章節添加錨點
            chapter_markdown_parts = []

            if chapter_name in chapter_map:
                # 添加錨點
                anchor_id = chapter_map[chapter_name]
                chapter_markdown_parts.append(f'<a name="{anchor_id}"></a>\n\n')

            # 轉換章節內容（傳入 chapter_map 和 toc_anchor 用於交叉引用）
            chapter_content = await self.convert_chapter_to_markdown(
                chapter_data,
                chapter_map,
                toc_anchor=toc_anchor,
                is_toc_chapter=(chapter_name == "目錄")
            )
            chapter_markdown_parts.append(chapter_content)

            all_markdown.append(''.join(chapter_markdown_parts))

        return '\n\n'.join(all_markdown)

    async def run(self, headless: bool = False, slow_mo: int = 100, wait_time: int = 10) -> bool:
        """
        執行完整的借閱流程（包含爬蟲）

        Args:
            headless: 是否使用無頭模式（不顯示瀏覽器視窗）
            slow_mo: 減慢操作速度（毫秒），便於觀察
            wait_time: 成功後等待時間（秒），讓使用者看到結果

        Returns:
            執行是否成功
        """
        async with async_playwright() as p:
            # 啟動瀏覽器
            logger.info(f"🌐 正在啟動瀏覽器 (headless={headless})...")
            browser: Browser = await p.chromium.launch(
                headless=headless,
                slow_mo=slow_mo
            )

            try:
                # 建立新頁面
                page: Page = await browser.new_page()

                # 步驟 1: 登入
                login_success = await self.login(page)
                if not login_success:
                    logger.info("\n❌ 登入失敗，無法繼續")
                    return False

                # 步驟 2: 檢查並借閱書籍
                borrow_result = await self.check_and_borrow_book(page, self.book_id)

                if not borrow_result:
                    logger.info("\n❌ 借閱失敗")
                    return False

                # 步驟 3: 如果啟用爬蟲且成功借閱，開始爬取內容
                if self.enable_scraping and isinstance(borrow_result, Page):
                    reading_page = borrow_result

                    # 根據模式選擇不同的爬取方法
                    if self.image_only_mode:
                        # 純圖片書籍模式（Canvas Only）
                        markdown_content = await self.scrape_image_only_book(reading_page)
                    else:
                        # 標準 HTML + Canvas 爬取模式
                        markdown_content = await self.scrape_entire_book(reading_page)

                    # 儲存為檔案
                    output_dir = Path("downloads")
                    output_dir.mkdir(exist_ok=True)

                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

                    # 使用書名作為檔案名（如果有的話）
                    if self.book_title:
                        # 移除檔案名中不允許的字元
                        safe_title = re.sub(r'[<>:"/\\|?*]', '_', self.book_title)
                        output_file = output_dir / f"{safe_title}_{timestamp}.md"
                    else:
                        output_file = output_dir / f"book_{self.book_id}_{timestamp}.md"

                    # 生成 Markdown 標題
                    # header = f"# {self.book_title if self.book_title else '書籍內容'}\n\n"
                    # if self.book_title:
                    #     header += f"- 書名: {self.book_title}\n"
                    # header += f"- 書籍 ID: {self.book_id}\n"
                    # header += f"- 爬取時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    # header += "---\n\n"

                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(markdown_content)

                    logger.info(f"\n💾 已儲存至: {output_file}")
                    logger.info(f"📊 檔案大小: {output_file.stat().st_size / 1024:.2f} KB")

                    # 等待一段時間讓使用者看到結果
                    if not headless:
                        logger.info(f"\n⏳ 將在 {wait_time} 秒後關閉瀏覽器...")
                        await asyncio.sleep(wait_time)

                    return True

                elif not self.enable_scraping:
                    # 只借閱，不爬蟲
                    if not headless:
                        logger.info(f"\n⏳ 將在 {wait_time} 秒後關閉瀏覽器...")
                        await asyncio.sleep(wait_time)
                    return True

                return False

            except Exception as e:
                logger.info(f"\n❌ 執行過程發生錯誤: {e}")
                import traceback
                traceback.print_exc()
                return False

            finally:
                # 關閉瀏覽器
                await browser.close()
                logger.info("\n🔚 瀏覽器已關閉")


async def main():
    """主程式"""
    logger.info("""
╔══════════════════════════════════════════════════════════════╗
║     桃園市立圖書館 HyRead 電子書自動借閱工具                ║
║                                                              ║
║  使用 Playwright + Google Gemini API 自動辨識驗證碼         ║
║  自動登入 → 檢查可借數量 → 借閱電子書                      ║
╚══════════════════════════════════════════════════════════════╝
    """)

    try:
        # 初始化借閱器
        scraper = HyReadScraper(env_file=".env_hyread")

        # 執行借閱流程
        # headless=False: 顯示瀏覽器視窗（方便觀察）
        # headless=True: 無頭模式（適合自動化執行）
        # wait_time: 成功後等待時間（秒）
        success = await scraper.run(
            headless=False,
            slow_mo=100,
            wait_time=5
        )

        if success:
            logger.info("\n✨ 借閱流程完成！")
            sys.exit(0)
        else:
            logger.info("\n⚠️  借閱流程未成功完成")
            sys.exit(1)

    except FileNotFoundError as e:
        logger.info(f"\n❌ 錯誤: {e}")
        logger.info("\n請確保以下檔案存在並包含必要的設定:")
        logger.info("   .env_hyread")
        sys.exit(1)

    except ImportError as e:
        logger.info(f"\n❌ 套件錯誤: {e}")
        sys.exit(1)

    except ValueError as e:
        logger.info(f"\n❌ 設定錯誤: {e}")
        sys.exit(1)

    except Exception as e:
        logger.info(f"\n❌ 發生未預期的錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # 執行主程式
    asyncio.run(main())

