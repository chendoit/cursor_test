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
    
    async def get_current_iframe(self, page: Page) -> FrameLocator:
        """
        獲取當前顯示的 iframe
        
        Args:
            page: Playwright 頁面物件
            
        Returns:
            當前的 iframe locator
        """
        try:
            # 直接找到所有 iframe 元素
            iframes = page.locator('iframe')
            iframe_count = await iframes.count()
            
            # 遍歷所有 iframe，找到第一個可見的
            for i in range(iframe_count):
                iframe_element = iframes.nth(i)
                
                # 檢查 iframe 是否可見
                is_visible = await iframe_element.is_visible()
                
                if is_visible:
                    # 返回可見的 iframe 的 frame_locator
                    # 使用 nth(i) 來精確定位
                    return page.frame_locator('iframe').nth(i)
            
            # 如果沒有找到可見的，返回第一個
            print("⚠️  未找到可見的 iframe，使用第一個")
            return page.frame_locator('iframe').first
            
        except Exception as e:
            print(f"⚠️  獲取 iframe 時發生錯誤: {e}")
            # 降級方案：直接返回第一個 iframe
            return page.frame_locator('iframe').first
    
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
        抓取當前頁面的內容
        
        Args:
            page: Playwright 頁面物件
            
        Returns:
            包含標題、段落和圖片的字典
        """
        try:
            # 獲取當前的 iframe
            iframe = await self.get_current_iframe(page)
            
            content = {
                'headings': [],
                'paragraphs': [],
                'images': []
            }
            
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
            print(f"⚠️  抓取頁面內容時發生錯誤: {e}")
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
        爬取整本書的內容
        
        Args:
            reading_page: 閱讀頁面的 Page 物件
            
        Returns:
            完整的 Markdown 內容
        """
        print("\n" + "="*60)
        print("📚 開始爬取書籍內容")
        print("="*60)
        
        # 如果需要下載圖片，建立圖片目錄
        if self.download_images:
            self.images_dir = Path("downloads") / "images" / f"book_{self.book_id}"
            self.images_dir.mkdir(parents=True, exist_ok=True)
            print(f"📁 圖片將保存到: {self.images_dir}")
        
        # 點擊「我知道了」按鈕
        await self.click_accept_button(reading_page)
        
        # 等待頁面完全載入
        await asyncio.sleep(3)
        
        all_content = []
        previous_markdown = ""  # 用於檢測重複
        page_number = 0
        duplicate_count = 0  # 連續空白頁計數
        
        # 獲取 base URL（用於圖片下載）
        base_url = await self.get_base_url_from_iframe(reading_page)
        if base_url:
            print(f"📍 Base URL: {base_url}")
        
        while page_number < self.max_pages:
            page_number += 1
            
            # 獲取閱讀進度
            progress = await self.get_reading_progress(reading_page)
            print(f"\n📖 正在爬取第 {page_number} 頁... [{progress['text']}]")
            
            # 抓取當前頁面內容
            content = await self.scrape_page_content(reading_page)
            
            # 檢查是否有內容
            has_content = bool(content['headings'] or content['paragraphs'] or content['images'])
            
            if not has_content:
                print("⚠️  當前頁面沒有內容")
                duplicate_count += 1
                
                if duplicate_count >= 3:
                    print("⚠️  連續 3 頁沒有內容，可能已到達結尾")
                    break
            else:
                duplicate_count = 0
                print(f"   ✓ 有內容: 標題={len(content['headings'])}, 段落={len(content['paragraphs'])}, 圖片={len(content['images'])}")
            
            # 下載圖片（如果啟用）
            if self.download_images and content['images']:
                await self.download_images_for_content(content, page_number, base_url)
            
            # 轉換為 Markdown
            markdown = self.convert_to_markdown(content, page_number)
            
            # 檢查是否與上一頁完全相同（避免重複保存）
            if markdown.strip() and markdown != previous_markdown:
                all_content.append(markdown)
                previous_markdown = markdown
                print(f"   💾 已保存內容")
            elif markdown == previous_markdown:
                print(f"   ⚠️  內容與上一頁相同，跳過保存")
            else:
                print(f"   ⚠️  內容為空，跳過保存")
            
            # 檢查是否為最後一頁（使用閱讀進度）
            if await self.is_last_page(reading_page):
                print("✅ 已到達最後一頁（全文 100% 且本章最後一頁）")
                break
            
            # 備用檢查：偵測結束標誌
            combined_text = ' '.join(content['paragraphs'])
            if any(keyword in combined_text for keyword in ['版權頁', '版權所有', 'Copyright', 'The End', '全書完']):
                print("✅ 偵測到結束標誌")
                break
            
            # 顯示統計（不重複顯示，前面已經顯示過）
            # print(f"   - 標題: {len(content['headings'])} 個")
            # print(f"   - 段落: {len(content['paragraphs'])} 段")
            # print(f"   - 圖片: {len(content['images'])} 張")
            
            # 翻到下一頁
            if page_number < self.max_pages:
                success = await self.turn_page(reading_page)
                if not success:
                    print("⚠️  翻頁失敗，停止爬取")
                    break
        
        print("\n" + "="*60)
        print(f"✅ 爬取完成！共 {page_number} 頁")
        print("="*60)
        
        # 生成完整的 Markdown 文件（不包含分頁標記）
        # 將所有內容合併，用單個換行分隔
        return '\n'.join(all_content)
    
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
                    output_file = output_dir / f"book_{self.book_id}_{timestamp}.md"
                    
                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(markdown_content)
                    
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

