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

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False


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
        
        # 圖片下載相關
        self.images_dir = None
        self.downloaded_images = {}  # URL -> 本地路徑映射
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
        
        print(f"✅ 已載入設定:")
        print(f"   - 帳號: {self.account}")
        print(f"   - 驗證碼模式: {'自動辨識 (Gemini)' if self.captcha_mode == 'auto' else '手動輸入'}")
        if self.captcha_mode == "auto":
            print(f"   - Gemini 模型: {self.model_name}")
        print(f"   - 目標書籍 ID: {self.book_id}")
        print(f"   - 爬蟲模式: {'啟用' if self.enable_scraping else '停用'}")
        if self.enable_scraping:
            print(f"   - 最大爬取頁數: {self.max_pages}")
            print(f"   - 下載圖片: {'是' if self.download_images else '否'}")
    
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
            print("📸 驗證碼圖片已顯示在瀏覽器中")
            print("👀 請查看瀏覽器視窗中的驗證碼")
            print("="*60)
            
            # 等待一下讓使用者看清楚驗證碼
            await asyncio.sleep(1)
            
            # 從命令列讀取使用者輸入
            captcha_text = input("⌨️  請輸入驗證碼: ").strip()
            
            if not captcha_text:
                raise ValueError("驗證碼不能為空")
            
            print(f"✅ 您輸入的驗證碼: {captcha_text}")
            return captcha_text
            
        else:
            # 自動模式：使用 Gemini API 辨識
            print("📸 正在截取驗證碼圖片...")
            
            # 截取驗證碼圖片
            captcha_screenshot = await captcha_img.screenshot()
            
            print("🤖 正在呼叫 Google Gemini API 辨識驗證碼...")
            
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
                print(f"✅ 驗證碼辨識結果: {captcha_text}")
                return captcha_text
                
            except Exception as e:
                print(f"❌ Gemini API 呼叫失敗: {e}")
                raise
    
    async def login(self, page: Page) -> bool:
        """
        執行自動登入
        
        Args:
            page: Playwright 頁面物件
            
        Returns:
            登入是否成功
        """
        print("\n" + "="*60)
        print("🚀 開始自動登入流程")
        print("="*60)
        
        # 前往登入頁面
        print(f"📄 正在前往登入頁面: {self.login_url}")
        await page.goto(self.login_url)
        await asyncio.sleep(2)
        
        # 填寫帳號
        print(f"✍️  填寫帳號: {self.account}")
        account_input = page.locator('input[name="account2"]')
        await account_input.wait_for(state="visible", timeout=10000)
        await account_input.fill(self.account)
        await asyncio.sleep(0.5)
        
        # 填寫密碼
        print("🔒 填寫密碼...")
        password_input = page.locator('input[name="passwd2"]')
        await password_input.fill(self.password)
        await asyncio.sleep(0.5)
        
        # 辨識並填寫驗證碼
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            print(f"\n🔍 驗證碼辨識嘗試 {attempt}/{max_retries}")
            
            try:
                captcha_text = await self.solve_captcha(page)
                
                # 填寫驗證碼
                print(f"✍️  填寫驗證碼: {captcha_text}")
                valicode_input = page.locator('input[name="valicode"]')
                await valicode_input.fill("")  # 先清空
                await valicode_input.fill(captcha_text)
                await asyncio.sleep(0.5)
                
                # 點擊登入按鈕
                print("🖱️  點擊登入按鈕...")
                login_button = page.locator('a[href="javascript:docheck();"] .login-btn')
                await login_button.click()
                
                # 等待頁面導航
                await asyncio.sleep(3)
                
                # 檢查是否登入成功
                current_url = page.url
                print(f"📍 當前 URL: {current_url}")
                
                if "ebook.hyread.com.tw" in current_url and "index.jsp" in current_url:
                    print("\n" + "="*60)
                    print("✅ 登入成功！")
                    print("="*60)
                    return True
                
                elif current_url == self.login_url:
                    print(f"⚠️  驗證碼可能錯誤，準備重試...")
                    
                    if attempt < max_retries:
                        await valicode_input.fill("")
                        await asyncio.sleep(1)
                        continue
                    else:
                        print(f"\n❌ 已達到最大重試次數 ({max_retries})，登入失敗")
                        return False
                
            except Exception as e:
                print(f"❌ 驗證碼辨識失敗: {e}")
                if attempt < max_retries:
                    print("⏳ 等待後重試...")
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
        print("\n" + "="*60)
        print("📚 開始檢查書籍")
        print("="*60)
        
        # 前往書籍詳情頁面
        book_url = f"{self.base_url}/bookDetail.jsp?id={book_id}"
        print(f"📄 正在前往書籍頁面: {book_url}")
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
                        print(f"📖 書名: {short_title}")
                    else:
                        self.book_title = full_title.strip()
                        print(f"📖 書名: {self.book_title}")
        except Exception as e:
            print(f"⚠️  無法提取書名: {e}")
            self.book_title = f"book_{book_id}"
        
        # 檢查線上閱讀按鈕
        try:
            # 定位線上閱讀按鈕
            read_button = page.locator('button.btn-collect:has-text("線上閱讀")')
            
            # 檢查按鈕是否存在
            if await read_button.count() == 0:
                print("❌ 找不到線上閱讀按鈕")
                return False
            
            # 獲取按鈕的 title 屬性
            button_title = await read_button.get_attribute('title')
            print(f"📊 按鈕狀態: {button_title}")
            
            # 使用正則表達式提取可用數量
            match = re.search(r'線上閱讀人數.*?尚有(\d+)本', button_title, re.DOTALL)
            
            if match:
                available_count = int(match.group(1))
                print(f"📊 可借閱數量: {available_count} 本")
                
                if available_count > 0:
                    print("✅ 書籍可借閱，準備點擊線上閱讀按鈕...")
                    
                    # 點擊線上閱讀按鈕
                    await read_button.click()
                    await asyncio.sleep(3)
                    
                    # 檢查是否成功開啟閱讀頁面
                    # 可能會開啟新分頁或彈出視窗
                    current_url = page.url
                    print(f"📍 當前 URL: {current_url}")
                    
                    # 檢查所有頁面
                    all_pages = page.context.pages
                    print(f"📄 目前開啟的頁面數: {len(all_pages)}")
                    
                    reading_page = None
                    
                    if len(all_pages) > 1:
                        print("✅ 已開啟新的閱讀視窗")
                        # 切換到新頁面
                        reading_page = all_pages[-1]
                        await asyncio.sleep(2)
                        print(f"📍 閱讀頁面 URL: {reading_page.url}")
                    else:
                        # 如果沒有開啟新頁面，可能在當前頁面中打開
                        print("⚠️  未偵測到新視窗，檢查當前頁面...")
                        
                        # 等待頁面可能的變化
                        await asyncio.sleep(2)
                        
                        # 檢查當前頁面 URL 是否改變
                        if page.url != current_url or "reader" in page.url.lower():
                            print("✅ 閱讀器在當前頁面中打開")
                            reading_page = page
                        else:
                            # 再等待並重新檢查
                            await asyncio.sleep(3)
                            all_pages = page.context.pages
                            if len(all_pages) > 1:
                                reading_page = all_pages[-1]
                                print(f"✅ 延遲偵測到新視窗: {reading_page.url}")
                            else:
                                print("⚠️  仍未偵測到閱讀視窗，使用當前頁面")
                                reading_page = page
                    
                    print("\n" + "="*60)
                    print("✅ 借閱成功！")
                    print("="*60)
                    
                    # 如果啟用爬蟲，返回閱讀頁面用於後續爬取
                    if self.enable_scraping:
                        if reading_page:
                            print(f"📖 將使用頁面進行爬取: {reading_page.url}")
                            return reading_page
                        else:
                            print("❌ 無法獲取閱讀頁面")
                            return False
                    else:
                        return True
                else:
                    print("⚠️  目前沒有可借閱的副本")
                    return False
            else:
                print("⚠️  無法解析可借閱數量")
                # 嘗試直接點擊看看
                print("🔍 嘗試直接點擊按鈕...")
                await read_button.click()
                await asyncio.sleep(3)
                return True
                
        except Exception as e:
            print(f"❌ 檢查或借閱書籍時發生錯誤: {e}")
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
            print("\n🔍 尋找「我知道了」按鈕...")
            
            # 等待按鈕出現
            accept_button = page.locator('button:has-text("我知道了")')
            
            # 等待最多 10 秒
            await accept_button.wait_for(state="visible", timeout=10000)
            
            print("🖱️  點擊「我知道了」按鈕...")
            await accept_button.click()
            await asyncio.sleep(2)
            
            print("✅ 已點擊「我知道了」按鈕")
            return True
            
        except Exception as e:
            print(f"⚠️  未找到或無法點擊「我知道了」按鈕: {e}")
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
            
            print(f"   🔍 找到 {iframe_count} 個 iframe")
            
            # 遍歷所有 iframe
            for i in range(iframe_count):
                iframe_element = iframes.nth(i)
                
                # 檢查 iframe 是否可見
                is_visible = await iframe_element.is_visible()
                
                if is_visible:
                    frame_locator = page.frame_locator('iframe').nth(i)
                    visible_iframes.append(frame_locator)
                    print(f"      ✓ iframe[{i}] 可見")
                else:
                    print(f"      ✗ iframe[{i}] 不可見")
            
            if not visible_iframes:
                print("   ⚠️  沒有找到可見的 iframe，使用第一個")
                visible_iframes.append(page.frame_locator('iframe').first)
            
            return visible_iframes
            
        except Exception as e:
            print(f"   ⚠️  獲取 iframe 時發生錯誤: {e}")
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
                print(f"      📄 正在抓取 iframe[{iframe_index}] 的內容...")
                iframe_content = await self._scrape_from_single_iframe(iframe)
                
                # 合併內容
                content['headings'].extend(iframe_content['headings'])
                content['paragraphs'].extend(iframe_content['paragraphs'])
                content['images'].extend(iframe_content['images'])
                
                print(f"         找到: 標題={len(iframe_content['headings'])}, 段落={len(iframe_content['paragraphs'])}, 圖片={len(iframe_content['images'])}")
            
            return content
            
        except Exception as e:
            print(f"⚠️  抓取頁面內容時發生錯誤: {e}")
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
            print(f"         ⚠️  提取 figure 內容失敗: {e}")
            return None
    
    async def extract_chapter_name(self, iframe: FrameLocator) -> str:
        """
        從 iframe 中提取章節名稱
        
        Args:
            iframe: iframe locator
            
        Returns:
            章節名稱（如果沒有則返回空字串）
        """
        try:
            # 找到 h1 標籤
            h1_elements = iframe.locator('h1')
            h1_count = await h1_elements.count()
            
            for i in range(h1_count):
                h1 = h1_elements.nth(i)
                # 在 h1 中找 span.num2
                span_num2 = h1.locator('span.num2')
                if await span_num2.count() > 0:
                    # 獲取整個 h1 的文字作為章節名
                    chapter_name = await self.extract_html_with_formatting(h1)
                    return chapter_name.strip()
            
            # 如果沒有找到，嘗試只找第一個 h1
            if h1_count > 0:
                first_h1 = await self.extract_html_with_formatting(h1_elements.first)
                return first_h1.strip()
            
            return ""
            
        except Exception as e:
            print(f"         ⚠️  提取章節名稱失敗: {e}")
            return ""
    
    async def scrape_chapter_from_iframe(self, iframe: FrameLocator, base_url: str = None) -> Dict[str, any]:
        """
        從單個 iframe 抓取完整章節內容（保持元素順序）
        
        Args:
            iframe: iframe locator
            base_url: 基礎 URL（用於解析圖片相對路徑）
            
        Returns:
            章節資料字典，包含章節名和有序內容列表
        """
        try:
            # 提取章節名稱
            chapter_name = await self.extract_chapter_name(iframe)
            
            if not chapter_name:
                # 如果沒有章節名，使用特殊標記（可能是封面或前言）
                chapter_name = "__no_chapter__"
            
            # 按順序抓取所有內容元素（保持 DOM 順序）
            content_items = []
            
            # 抓取 body 內的所有元素
            body = iframe.locator('body')
            
            # 一次性抓取所有內容元素（h1, h2, h3, h4, h5, h6, p, figure）並保持順序
            # 使用 CSS 選擇器來選擇多個元素並保持順序
            all_elements = body.locator('h1, h2, h3, h4, h5, h6, p, figure')
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
                else:
                    # 獲取元素的文字內容（保留格式）
                    text_content = await self.extract_html_with_formatting(element)
                    
                    if text_content.strip():
                        content_items.append({
                            'type': tag_name,
                            'content': text_content.strip()
                        })
            
            # 抓取不在 figure 內的獨立圖片
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
                'content_items': content_items,
                'images': images,
                'figure_images': figure_images,  # figure 中的圖片
                'footnotes': footnotes
            }
            
        except Exception as e:
            print(f"         ⚠️  從 iframe 抓取章節時發生錯誤: {e}")
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
            print(f"         ⚠️  從 iframe 抓取內容時發生錯誤: {e}")
            return {'headings': [], 'paragraphs': [], 'images': []}
    
    async def download_image(self, url: str, page_number: int, base_url: str = None) -> str:
        """
        下載圖片到本地
        
        Args:
            url: 圖片 URL（可能是相對路徑）
            page_number: 頁碼
            base_url: 基礎 URL（用於解析相對路徑）
            
        Returns:
            本地圖片路徑（相對於 Markdown 檔案）
        """
        # 檢查是否已下載
        if url in self.downloaded_images:
            return self.downloaded_images[url]
        
        try:
            # 處理相對路徑
            download_url = url
            if not url.startswith(('http://', 'https://')):
                if base_url:
                    # 使用 urljoin 轉換相對路徑為絕對路徑
                    download_url = urljoin(base_url, url)
                    print(f"      🔗 轉換 URL: {url} -> {download_url}")
                else:
                    print(f"      ⚠️  無法下載相對路徑圖片（缺少 base_url）: {url}")
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
            
            print(f"      📥 已下載圖片: {filename}")
            return relative_path
            
        except Exception as e:
            print(f"      ⚠️  下載圖片失敗 ({url}): {e}")
            # 下載失敗時返回原 URL
            return url
    
    def extract_chapter_number(self, chapter_name: str) -> tuple:
        """
        從章節名稱中提取章節編號
        
        Args:
            chapter_name: 章節名稱
            
        Returns:
            (章節類型, 章節編號) 
            - 章節類型: 'front' (前置), 'main' (正文), 'back' (後置)
            - 章節編號: 數字或 None
        """
        import re
        
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
    
    def sort_chapters(self, chapter_order: list) -> list:
        """
        對章節進行智能排序
        
        Args:
            chapter_order: 原始章節順序列表
            
        Returns:
            排序後的章節列表
        """
        # 為每個章節提取排序資訊
        chapter_info = []
        for chapter_name in chapter_order:
            chapter_type, chapter_num = self.extract_chapter_number(chapter_name)
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
        為章節下載所有圖片（包含 figure 中的圖片）
        
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
    
    async def convert_chapter_to_markdown(self, chapter_data: Dict[str, any]) -> str:
        """
        將章節資料轉換為 Markdown 格式
        
        Args:
            chapter_data: 章節資料字典
            
        Returns:
            Markdown 格式的文字
        """
        markdown_lines = []
        
        # 處理有序內容（包含 figure）
        for item in chapter_data['content_items']:
            item_type = item['type']
            content = item['content']
            
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
        
        # 處理獨立圖片（不在 figure 內的）
        if chapter_data['images']:
            markdown_lines.append("\n")
            for image in chapter_data['images']:
                # 優先使用本地路徑
                img_path = image.get('local_path', image['src'])
                alt_text = image.get('alt', '圖片')
                markdown_lines.append(f"![{alt_text}]({img_path})\n")
        
        # 處理註釋
        if chapter_data['footnotes']:
            markdown_lines.append("\n---\n\n**註釋：**\n\n")
            for footnote in chapter_data['footnotes']:
                markdown_lines.append(f"{footnote}\n\n")
        
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
            print(f"      ⚠️  無法獲取閱讀進度: {e}")
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
        翻到下一頁（模擬鍵盤右鍵）
        
        Args:
            page: Playwright 頁面物件
            
        Returns:
            是否成功翻頁
        """
        try:
            # 按下鍵盤右鍵
            await page.keyboard.press('ArrowRight')
            
            # 等待頁面載入
            await asyncio.sleep(2)
            
            return True
            
        except Exception as e:
            print(f"⚠️  翻頁時發生錯誤: {e}")
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

    async def scrape_entire_book(self, reading_page: Page) -> str:
        """
        爬取整本書的內容（以章節為單位）
        
        Args:
            reading_page: 閱讀頁面的 Page 物件
            
        Returns:
            完整的 Markdown 內容
        """
        print("\n" + "=" * 60)
        print("📚 開始爬取書籍內容（以章節為單位）")
        print("=" * 60)

        # 如果需要下載圖片，建立圖片目錄
        if self.download_images:
            self.images_dir = Path("downloads") / "images" / f"book_{self.book_id}"
            self.images_dir.mkdir(parents=True, exist_ok=True)
            print(f"📁 圖片將保存到: {self.images_dir}")

        # 點擊「我知道了」按鈕
        await self.click_accept_button(reading_page)

        # 等待頁面完全載入
        await asyncio.sleep(3)

        # 儲存章節，key = 章節名，value = 章節資料
        chapters = {}
        chapter_order = []  # 記錄章節出現順序
        
        page_number = 0
        full_progress_count = 0  # 記錄連續出現全文 100% 的次數

        # 獲取 base URL（用於圖片下載）
        base_url = await self.get_base_url_from_iframe(reading_page)
        if base_url:
            print(f"📍 Base URL: {base_url}")

        while page_number < self.max_pages:
            page_number += 1

            # 獲取閱讀進度
            progress = await self.get_reading_progress(reading_page)
            print(f"\n📖 正在掃描第 {page_number} 頁... [{progress['text']}] (進度: {progress['total_percent']}%)")

            # 獲取所有可見的 iframe
            visible_iframes = await self.get_all_visible_iframes(reading_page)
            
            found_new_chapter = False
            
            # 從每個 iframe 抓取章節
            for iframe_index, iframe in enumerate(visible_iframes):
                print(f"      📄 正在抓取 iframe[{iframe_index}] 的章節...")
                
                # 抓取章節資料
                chapter_data = await self.scrape_chapter_from_iframe(iframe, base_url)
                
                if not chapter_data:
                    print(f"         ⚠️  iframe[{iframe_index}] 沒有內容")
                    continue
                
                chapter_name = chapter_data['name']
                
                # 檢查是否為新章節
                if chapter_name not in chapters:
                    chapters[chapter_name] = chapter_data
                    chapter_order.append(chapter_name)
                    found_new_chapter = True
                    
                    # 顯示章節預覽
                    display_name = chapter_name if chapter_name != "__no_chapter__" else "【無章節名稱（可能是封面或前言）】"
                    print(f"         ✅ 新章節: {display_name}")
                    
                    # DEBUG: 顯示內容預覽
                    if chapter_data['content_items']:
                        first_item = chapter_data['content_items'][0]
                        last_item = chapter_data['content_items'][-1]
                        print(f"         🔍 第一項 ({first_item['type']}): {first_item['content'][:80]}...")
                        print(f"         🔍 最後項 ({last_item['type']}): {last_item['content'][:80]}...")
                    
                    total_images = len(chapter_data['images']) + len(chapter_data.get('figure_images', []))
                    print(f"         📊 統計: {len(chapter_data['content_items'])} 個元素, {total_images} 張圖片")
                    
                    # 下載圖片（包括 figure 中的圖片）
                    if self.download_images and total_images > 0:
                        await self.download_images_for_chapter(chapter_data, page_number, base_url)
                else:
                    print(f"         ⚠️  重複章節: {chapter_name}")
            
            # 如果沒有找到新章節，只是提示，不作為終止條件
            if not found_new_chapter:
                print(f"   ℹ️  本頁沒有新章節（可能還在同一章節中）")

            # 檢查是否為最後一頁（主要終止條件）
            if await self.is_last_page(reading_page):
                print("✅ 已到達最後一頁（全文 100% 且本章最後一頁）")
                break
            
            # 安全機制：檢測全文 100% 的情況
            if progress['total_percent'] >= 100:
                full_progress_count += 1
                
                if not found_new_chapter:
                    # 如果全文 100% 且沒有新章節
                    print(f"   ⚠️  已達全文 100% 且無新章節（第 {full_progress_count} 次）")
                    
                    if full_progress_count >= 5:
                        # 連續 5 次 100% 且無新章節，提前終止
                        print("   🛑 連續 5 次偵測到全文 100% 且無新章節，停止爬取")
                        print("   💡 提示：這可能是網站進度顯示錯誤（例如：全文 100%．本章第 1 頁 / 2 頁）")
                        break
                else:
                    # 有新章節，說明還沒結束，只是顯示 100%
                    print(f"   ℹ️  已達全文 100% 但發現新章節，繼續爬取...")
                    full_progress_count = 0
                
                if full_progress_count >= 10:
                    # 保險機制：無論如何，連續 10 次 100% 就停止
                    print("   🛑 連續 10 次偵測到全文 100%，強制停止爬取")
                    break
            else:
                # 重置計數器
                full_progress_count = 0

            # 智能翻頁：根據本章剩餘頁數決定翻多少頁
            remaining_pages = progress['chapter_total'] - progress['chapter_current']
            
            if remaining_pages <= 0:
                # 章節結束，翻 1 頁到下一章
                pages_to_turn = 1
                print(f"   ⏭️  章節已結束，翻 1 頁到下一章...")
            elif remaining_pages <= 2:
                # 接近章節尾部，翻 1 頁
                pages_to_turn = 1
                print(f"   ⏭️  本章剩餘 {remaining_pages} 頁，謹慎翻 1 頁...")
            elif remaining_pages <= 5:
                # 章節中後段，翻 2 頁
                pages_to_turn = 2
                print(f"   ⏭️  本章剩餘 {remaining_pages} 頁，翻 2 頁...")
            elif remaining_pages > 10:
                # 章節前段，直接跳到倒數第 3 頁
                pages_to_turn = remaining_pages - 3
                print(f"   🚀 本章剩餘 {remaining_pages} 頁，直接跳到倒數第 3 頁（翻 {pages_to_turn} 頁）...")
            else:
                # 章節中段（6-10頁），翻 remaining - 3 或 3 頁
                pages_to_turn = max(3, remaining_pages - 3)
                print(f"   ⏭️  本章剩餘 {remaining_pages} 頁，翻 {pages_to_turn} 頁...")
            
            for i in range(pages_to_turn):
                if page_number + i >= self.max_pages:
                    break
                
                success = await self.turn_page(reading_page)
                if not success:
                    print(f"   ⚠️  第 {i+1} 次翻頁失敗")
                    break
                
                # 短暫等待（翻頁多時減少等待）
                if pages_to_turn > 5:
                    await asyncio.sleep(0.3)  # 快速翻頁時縮短等待
                else:
                    await asyncio.sleep(0.5)
            
            page_number += (pages_to_turn - 1)  # 循環會再 +1

        print("\n" + "=" * 60)
        print(f"✅ 爬取完成！共找到 {len(chapters)} 個不重複的章節 (掃描 {page_number} 頁)")
        print("=" * 60)

        # 對章節進行智能排序
        sorted_chapter_order = self.sort_chapters(chapter_order)
        
        print("\n" + "=" * 60)
        print("📖 章節排序結果：")
        print("=" * 60)

        # 按照排序後的順序轉換章節為 Markdown
        all_markdown = []
        
        for idx, chapter_name in enumerate(sorted_chapter_order, 1):
            chapter_data = chapters[chapter_name]
            
            display_name = chapter_name if chapter_name != "__no_chapter__" else "前言/封面"
            print(f"📝 第 {idx} 章: {display_name}")
            
            chapter_markdown = await self.convert_chapter_to_markdown(chapter_data)
            all_markdown.append(chapter_markdown)
        
        return '\n\n'.join(all_markdown)
    
    async def run(self, headless: bool = False, slow_mo: int = 100, wait_time: int = 30) -> bool:
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
            print(f"🌐 正在啟動瀏覽器 (headless={headless})...")
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
                    print("\n❌ 登入失敗，無法繼續")
                    return False
                
                # 步驟 2: 檢查並借閱書籍
                borrow_result = await self.check_and_borrow_book(page, self.book_id)
                
                if not borrow_result:
                    print("\n❌ 借閱失敗")
                    return False
                
                # 步驟 3: 如果啟用爬蟲且成功借閱，開始爬取內容
                if self.enable_scraping and isinstance(borrow_result, Page):
                    reading_page = borrow_result
                    
                    # 爬取整本書
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
                    header = f"# {self.book_title if self.book_title else '書籍內容'}\n\n"
                    if self.book_title:
                        header += f"- 書名: {self.book_title}\n"
                    header += f"- 書籍 ID: {self.book_id}\n"
                    header += f"- 爬取時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    header += "---\n\n"
                    
                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(header + markdown_content)
                    
                    print(f"\n💾 已儲存至: {output_file}")
                    print(f"📊 檔案大小: {output_file.stat().st_size / 1024:.2f} KB")
                    
                    # 等待一段時間讓使用者看到結果
                    if not headless:
                        print(f"\n⏳ 將在 {wait_time} 秒後關閉瀏覽器...")
                        await asyncio.sleep(wait_time)
                    
                    return True
                
                elif not self.enable_scraping:
                    # 只借閱，不爬蟲
                    if not headless:
                        print(f"\n⏳ 將在 {wait_time} 秒後關閉瀏覽器...")
                        await asyncio.sleep(wait_time)
                    return True
                
                return False
                
            except Exception as e:
                print(f"\n❌ 執行過程發生錯誤: {e}")
                import traceback
                traceback.print_exc()
                return False
                
            finally:
                # 關閉瀏覽器
                await browser.close()
                print("\n🔚 瀏覽器已關閉")


async def main():
    """主程式"""
    print("""
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
            wait_time=30
        )
        
        if success:
            print("\n✨ 借閱流程完成！")
            sys.exit(0)
        else:
            print("\n⚠️  借閱流程未成功完成")
            sys.exit(1)
            
    except FileNotFoundError as e:
        print(f"\n❌ 錯誤: {e}")
        print("\n請確保以下檔案存在並包含必要的設定:")
        print("   .env_hyread")
        sys.exit(1)
        
    except ImportError as e:
        print(f"\n❌ 套件錯誤: {e}")
        sys.exit(1)
        
    except ValueError as e:
        print(f"\n❌ 設定錯誤: {e}")
        sys.exit(1)
        
    except Exception as e:
        print(f"\n❌ 發生未預期的錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # 執行主程式
    asyncio.run(main())

