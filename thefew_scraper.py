#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TheFew CB Scraper - 台灣可轉債資料爬蟲
抓取 thefew.tw/cb 的可轉債資料，支援 Google 登入和 Cookie 持久化
"""

import os
import sys
import csv
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext, TimeoutError as PlaywrightTimeout
from loguru import logger

# 配置 loguru
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True
)
logger.add(
    "logs/thefew_scraper_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
    rotation="00:00",
    retention="30 days",
    compression="zip"
)


class TheFewScraper:
    """TheFew 可轉債資料爬蟲"""

    def __init__(self, env_file: str = ".env_thefew"):
        """
        初始化爬蟲
        
        Args:
            env_file: 環境變數檔案路徑
        """
        # 載入環境變數
        load_dotenv(env_file)
        
        self.login_url = os.getenv("THEFEW_LOGIN_URL", "https://thefew.tw/login")
        self.cb_url = os.getenv("THEFEW_CB_URL", "https://thefew.tw/cb")
        self.download_dir = Path(os.getenv("DOWNLOAD_DIR", "downloads/thefew"))
        self.headless = os.getenv("HEADLESS", "false").lower() == "true"
        self.cookie_file = os.getenv("COOKIE_FILE", ".thefew_cookies.json")
        self.login_timeout = int(os.getenv("LOGIN_TIMEOUT", "60")) * 1000  # 轉換為毫秒
        self.page_timeout = int(os.getenv("PAGE_TIMEOUT", "30")) * 1000
        self.max_retries = int(os.getenv("MAX_RETRIES", "3"))
        self.fetch_detail = os.getenv("FETCH_DETAIL", "false").lower() == "true"
        
        # 確保下載目錄和日誌目錄存在
        self.download_dir.mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        logger.info("=" * 60)
        logger.info("TheFew 可轉債資料爬蟲")
        logger.info("=" * 60)
        logger.info(f"目標 URL: {self.cb_url}")
        logger.info(f"下載目錄: {self.download_dir.absolute()}")
        logger.info(f"無頭模式: {self.headless}")
        logger.info(f"Cookie 檔案: {self.cookie_file}")
        logger.info(f"抓取詳細資料: {self.fetch_detail}")
        logger.info("=" * 60)

    def __enter__(self):
        """Context manager 入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager 出口"""
        self.close()

    def init_browser(self) -> None:
        """初始化瀏覽器"""
        if self.browser:
            return
        
        logger.info("正在初始化瀏覽器...")
        self.playwright = sync_playwright().start()
        
        # 啟動瀏覽器
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        
        # 檢查是否有儲存的 cookies
        cookie_path = Path(self.cookie_file)
        if cookie_path.exists():
            logger.info(f"找到 Cookie 檔案，嘗試載入: {self.cookie_file}")
            try:
                self.context = self.browser.new_context(
                    storage_state=self.cookie_file,
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                )
                self.page = self.context.new_page()
                logger.info("✅ Cookie 載入成功")
            except Exception as e:
                logger.warning(f"Cookie 載入失敗: {e}")
                cookie_path.unlink(missing_ok=True)
                self._create_new_context()
        else:
            logger.info("未找到 Cookie 檔案，建立新的瀏覽器上下文")
            self._create_new_context()
        
        logger.info("✅ 瀏覽器初始化完成")

    def _create_new_context(self) -> None:
        """建立新的瀏覽器上下文"""
        self.context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        self.page = self.context.new_page()

    def is_logged_in(self) -> bool:
        """
        檢查是否已登入
        
        Returns:
            是否已登入
        """
        try:
            logger.info("檢查登入狀態...")
            self.page.goto(self.cb_url, wait_until="networkidle", timeout=self.page_timeout)
            
            # 檢查是否有登出按鈕（如果有，表示已登入）
            logout_button = self.page.query_selector('a[href="/logout"]')
            if logout_button:
                logger.info("✅ 已登入（找到登出按鈕）")
                return True
            
            # 檢查是否有登入按鈕（如果有，表示未登入）
            login_button = self.page.query_selector('a[href="/login"]')
            if login_button:
                logger.info("❌ 未登入（找到登入按鈕）")
                return False
            
            logger.info("❌ 未登入（找不到登出按鈕）")
            return False
        except Exception as e:
            logger.error(f"檢查登入狀態時發生錯誤: {e}")
            return False

    def login(self) -> bool:
        """
        執行 Google 登入流程
        
        Returns:
            是否登入成功
        """
        for attempt in range(1, self.max_retries + 1):
            logger.info(f"開始登入流程（第 {attempt}/{self.max_retries} 次）...")
            
            try:
                # 前往登入頁面
                logger.info(f"前往登入頁面: {self.login_url}")
                self.page.goto(self.login_url, wait_until="networkidle", timeout=self.page_timeout)
                
                # 等待一下讓頁面完全載入
                time.sleep(2)
                
                # 尋找 Google 登入按鈕
                logger.info("尋找 Google 登入按鈕...")
                
                # 可能的選擇器
                google_selectors = [
                    'a:has-text("Google")',
                    'button:has-text("Google")',
                    'a:has-text("google")',
                    'button:has-text("google")',
                    '[data-provider="google"]',
                    '.google-login',
                ]
                
                google_button = None
                for selector in google_selectors:
                    google_button = self.page.query_selector(selector)
                    if google_button:
                        logger.info(f"找到 Google 登入按鈕: {selector}")
                        break
                
                if not google_button:
                    logger.error("找不到 Google 登入按鈕")
                    logger.info("請手動完成登入...")
                else:
                    # 點擊 Google 登入按鈕
                    logger.info("點擊 Google 登入按鈕...")
                    google_button.click()
                    
                    # 短暫等待頁面跳轉
                    time.sleep(2)
                
                # 等待 Google 登入流程完成
                logger.info("=" * 60)
                logger.warning(f"⏰ 請在瀏覽器中完成 Google 登入")
                logger.warning(f"⏰ 剩餘時間: {self.login_timeout // 1000} 秒")
                logger.info("=" * 60)
                
                login_success = self._wait_for_login_with_countdown()
                
                if login_success:
                    logger.info("✅ 登入成功（已驗證可以訪問 CB 資料）")
                    
                    # 儲存 cookies
                    logger.info(f"儲存 Cookie 到: {self.cookie_file}")
                    self.context.storage_state(path=self.cookie_file)
                    logger.info("✅ Cookie 已儲存")
                    
                    return True
                else:
                    logger.error("❌ 登入失敗")
                    return False
                
            except PlaywrightTimeout:
                logger.error(f"登入超時（第 {attempt}/{self.max_retries} 次）")
                if attempt < self.max_retries:
                    logger.info("等待 5 秒後重試...")
                    time.sleep(5)
            except Exception as e:
                logger.error(f"登入過程發生錯誤: {e}")
                if attempt < self.max_retries:
                    logger.info("等待 5 秒後重試...")
                    time.sleep(5)
        
        logger.error("❌ 登入失敗")
        return False

    def _wait_for_login_with_countdown(self) -> bool:
        """
        等待登入完成，並顯示倒計時
        
        Returns:
            是否登入成功
        """
        start_time = time.time()
        timeout_seconds = self.login_timeout // 1000
        check_interval = 3  # 每3秒檢查一次
        last_display = -1
        
        logger.info("等待登入完成中...")
        logger.info("（登入成功後，程式會自動偵測並繼續）")
        
        while True:
            elapsed = time.time() - start_time
            remaining = max(0, timeout_seconds - int(elapsed))
            
            # 每10秒顯示一次剩餘時間
            current_display = remaining // 10 * 10
            if current_display != last_display and remaining > 0:
                logger.info(f"⏰ 剩餘時間: {remaining} 秒")
                last_display = current_display
            
            # 檢查是否超時
            if elapsed >= timeout_seconds:
                logger.warning("⏰ 等待時間已到")
                # 超時後，先嘗試儲存 cookie（即使不確定是否登入成功）
                logger.info("嘗試儲存當前的 Cookie...")
                try:
                    self.context.storage_state(path=self.cookie_file)
                    logger.info(f"✅ Cookie 已儲存到: {self.cookie_file}")
                except Exception as e:
                    logger.error(f"儲存 Cookie 失敗: {e}")
                
                # 再檢查一次是否實際上已經登入
                logger.info("最終檢查登入狀態...")
                try:
                    self.page.goto(self.cb_url, wait_until="networkidle", timeout=self.page_timeout)
                    time.sleep(2)
                    
                    # 檢查是否有登出按鈕（表示已登入）
                    logout_button = self.page.query_selector('a[href="/logout"]')
                    if logout_button:
                        logger.info("✅ 實際上已經登入成功！（找到登出按鈕）")
                        return True
                    
                    cb_table = self.page.query_selector('table#cb-table')
                    if cb_table:
                        logger.info("✅ 實際上已經登入成功！（找到 CB 表格）")
                        return True
                except:
                    pass
                
                logger.error("❌ 登入超時")
                return False
            
            # 檢查當前頁面狀態
            try:
                current_url = self.page.url
                
                # 如果還在登入相關頁面，繼續等待
                if '/login' in current_url or 'accounts.google.com' in current_url:
                    time.sleep(check_interval)
                    continue
                
                # 如果離開登入頁面，檢查是否真的登入成功
                if 'thefew.tw' in current_url:
                    logger.info("偵測到頁面跳轉，正在驗證登入狀態...")
                    
                    # 等待頁面載入
                    time.sleep(2)
                    
                    # 檢查是否有登出按鈕（表示已登入）
                    logout_button = self.page.query_selector('a[href="/logout"]')
                    if logout_button:
                        logger.info("✅ 驗證成功：找到登出按鈕")
                        return True
                    
                    # 嘗試找到 CB 表格
                    cb_table = self.page.query_selector('table#cb-table')
                    if cb_table:
                        logger.info("✅ 驗證成功：找到 CB 資料表格")
                        return True
                    
                    # 檢查是否還有登入按鈕
                    login_button = self.page.query_selector('a[href="/login"]')
                    if login_button:
                        logger.warning("⚠️  頁面上還有登入按鈕，繼續等待...")
                        time.sleep(check_interval)
                        continue
                
            except Exception as e:
                logger.debug(f"檢查過程發生錯誤: {e}")
            
            time.sleep(check_interval)

    def ensure_logged_in(self) -> bool:
        """
        確保已登入
        
        Returns:
            是否已登入
        """
        if self.is_logged_in():
            return True
        
        logger.info("需要登入")
        return self.login()

    def extract_text(self, element, selector: str, default: str = "") -> str:
        """
        提取元素中的文字
        
        Args:
            element: 父元素
            selector: CSS 選擇器
            default: 預設值
            
        Returns:
            提取的文字
        """
        try:
            sub_element = element.query_selector(selector)
            if sub_element:
                return sub_element.inner_text().strip()
        except:
            pass
        return default

    def extract_href(self, element, selector: str, default: str = "") -> str:
        """
        提取元素中的連結
        
        Args:
            element: 父元素
            selector: CSS 選擇器
            default: 預設值
            
        Returns:
            提取的連結
        """
        try:
            sub_element = element.query_selector(selector)
            if sub_element:
                return sub_element.get_attribute('href') or default
        except:
            pass
        return default

    def parse_price_change(self, text: str) -> tuple[str, str]:
        """
        解析價格和漲跌幅
        
        Args:
            text: 包含價格和漲跌幅的文字（例如: "96.4(-0.1%)"）
            
        Returns:
            (價格, 漲跌幅) 元組
        """
        import re
        # 使用正則表達式分離價格和漲跌幅
        match = re.match(r'([\d.]+)\s*\(([-+]?[\d.]+%?)\)', text.replace('\n', '').replace(' ', ''))
        if match:
            return match.group(1), match.group(2)
        return text.strip(), ""

    def scrape_cb_data(self) -> List[Dict[str, Any]]:
        """
        抓取可轉債資料
        
        Returns:
            可轉債資料列表
        """
        logger.info("開始抓取可轉債資料...")
        
        # 前往 CB 頁面
        logger.info(f"前往 CB 頁面: {self.cb_url}")
        self.page.goto(self.cb_url, wait_until="networkidle", timeout=self.page_timeout)
        
        # 等待表格載入
        logger.info("等待表格載入...")
        self.page.wait_for_selector('table#cb-table', timeout=self.page_timeout)
        
        # 找到所有主要行（只找有 data-action 的 tr，這些是可點擊的可轉債行）
        main_rows = self.page.query_selector_all('table#cb-table tbody tr[data-action*="toggleExpand"]')
        logger.info(f"找到 {len(main_rows)} 個可轉債")
        
        all_data = []
        row_count = 0
            
        for row_index, row in enumerate(main_rows):
            try:
                row_count += 1
                logger.info(f"處理第 {row_count}/{len(main_rows)} 個可轉債...")
                
                # 提取主要表格資料
                cells = row.query_selector_all('td')
                if len(cells) < 8:
                    logger.warning(f"行 {row_count} 的欄位數量不足，跳過")
                    continue
                
                # 第一欄：代碼/名稱
                code_name_cell = cells[0]
                code_divs = code_name_cell.query_selector_all('div.inline-block')
                code = code_divs[0].inner_text().strip() if len(code_divs) > 0 else ""
                name = code_divs[1].inner_text().strip() if len(code_divs) > 1 else ""
                
                # 第二欄：CB收盤價
                cb_price_text = cells[1].inner_text().strip()
                cb_price, cb_change = self.parse_price_change(cb_price_text)
                
                # 第三欄：轉換價值
                conversion_value = cells[2].inner_text().strip()
                
                # 第四欄：轉換溢價率
                premium_rate = cells[3].inner_text().strip()
                
                # 第五欄：股票收盤價
                stock_price_text = cells[4].inner_text().strip()
                stock_price, stock_change = self.parse_price_change(stock_price_text)
                
                # 第六欄：轉換價
                conversion_price = cells[5].inner_text().strip()
                
                # 第七欄：已轉換(%)
                converted_pct = cells[6].inner_text().strip()
                
                # 第八欄：到期/提前賣回日
                maturity_date = cells[7].inner_text().strip()
                
                data = {
                    '代碼': code,
                    '名稱': name,
                    'CB收盤價': cb_price,
                    'CB漲跌幅': cb_change,
                    '轉換價值': conversion_value,
                    '轉換溢價率': premium_rate,
                    '股票收盤價': stock_price,
                    '股票漲跌幅': stock_change,
                    '轉換價': conversion_price,
                    '已轉換(%)': converted_pct,
                    '到期賣回日': maturity_date,
                }
                
                # 根據設定決定是否抓取詳細資料
                if self.fetch_detail:
                    logger.info(f"  點擊展開 {code} {name} 的詳細資料...")
                    
                    try:
                        # 點擊行
                        row.click()
                        
                        # 等待展開的行出現
                        time.sleep(0.5)  # 短暫等待動畫完成
                        
                        # 找到展開的行
                        expandable_row = self.page.query_selector('tr[data-target="table.expandable"]:not(.hidden)')
                        
                        if expandable_row:
                            # 提取詳細資料
                            detail_tables = expandable_row.query_selector_all('table')
                            
                            if len(detail_tables) >= 2:
                                # 左側表格
                                left_table = detail_tables[0]
                                data['可轉債名稱'] = self.extract_text(left_table, 'tr:has-text("可轉債名稱") td:nth-child(2)')
                                data['轉換標的名稱'] = self.extract_text(left_table, 'tr:has-text("轉換標的名稱") td:nth-child(2)')
                                data['上市櫃別'] = self.extract_text(left_table, 'tr:has-text("上市櫃別") td:nth-child(2)')
                                data['擔保銀行TCRI'] = self.extract_text(left_table, 'tr:has-text("擔保銀行") td:nth-child(2)')
                                
                                # 最新 CB 收盤價（詳細）
                                cb_detail_text = self.extract_text(left_table, 'tr:has-text("最新 CB 收盤價") td:nth-child(2)')
                                # 這裡可能和主表格的資料重複，不需要額外處理
                                
                                data['CBAS權利金'] = self.extract_text(left_table, 'tr:has-text("CBAS 權利金") td:nth-child(2)')
                                data['CBAS折現率'] = self.extract_text(left_table, 'tr:has-text("CBAS 折現率") td:nth-child(2)')
                                data['發行價格'] = self.extract_text(left_table, 'tr:has-text("發行價格") td:nth-child(2)')
                                
                                # 右側表格
                                right_table = detail_tables[1]
                                data['發行總額百萬'] = self.extract_text(right_table, 'tr:has-text("發行總額") td:nth-child(2)')
                                data['最新餘額百萬'] = self.extract_text(right_table, 'tr:has-text("最新餘額") td:nth-child(2)')
                                data['轉換比例'] = self.extract_text(right_table, 'tr:has-text("轉換比例") td:nth-child(2)')
                                data['發行日'] = self.extract_text(right_table, 'tr:has-text("發行日") td:nth-child(2)')
                                data['到期日'] = self.extract_text(right_table, 'tr:has-text("到期日") td:nth-child(2)')
                                data['到期賣回價格'] = self.extract_text(right_table, 'tr:has-text("到期賣回價格") td:nth-child(2)')
                                data['提前賣回日'] = self.extract_text(right_table, 'tr:has-text("下次提前賣回日") td:nth-child(2)')
                                data['提前賣回價格'] = self.extract_text(right_table, 'tr:has-text("下次提前賣回價格") td:nth-child(2)')
                                
                                # 連結
                                data['發行辦法連結'] = self.extract_href(right_table, 'tr:has-text("詳細發行辦法") a')
                                data['公開說明書連結'] = self.extract_href(right_table, 'tr:has-text("公開說明書") a')
                                
                                # 財務數據有多個連結
                                finance_row = right_table.query_selector('tr:has-text("財務數據")')
                                if finance_row:
                                    finance_links = finance_row.query_selector_all('a')
                                    if len(finance_links) >= 1:
                                        data['財報狗連結'] = finance_links[0].get_attribute('href') or ""
                                    if len(finance_links) >= 2:
                                        data['goodinfo連結'] = finance_links[1].get_attribute('href') or ""
                                
                                logger.info(f"  ✅ {code} {name} 詳細資料提取完成")
                            else:
                                logger.warning(f"  找不到足夠的詳細表格")
                        else:
                            logger.warning(f"  找不到展開的行")
                        
                        # 再次點擊以收起
                        row.click()
                        time.sleep(0.3)
                        
                    except Exception as e:
                        logger.error(f"  提取詳細資料時發生錯誤: {e}")
                
                all_data.append(data)
                
                # 隨機延遲，避免過快
                if self.fetch_detail:
                    time.sleep(random.uniform(0.2, 0.5))
                
            except Exception as e:
                logger.error(f"處理第 {row_count} 行時發生錯誤: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        logger.info(f"✅ 共抓取 {len(all_data)} 筆可轉債資料")
        return all_data

    def save_to_csv(self, data: List[Dict[str, Any]]) -> str:
        """
        儲存資料到 CSV
        
        Args:
            data: 可轉債資料列表
            
        Returns:
            CSV 檔案路徑
        """
        if not data:
            logger.warning("沒有資料可儲存")
            return ""
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_file = self.download_dir / f"thefew_cb_data_{timestamp}.csv"
        
        logger.info(f"儲存資料到 CSV: {csv_file}")
        
        # 定義欄位順序（根據是否抓取詳細資料）
        # 主要表格欄位
        fieldnames = [
            '代碼', '名稱', 'CB收盤價', 'CB漲跌幅', '轉換價值', '轉換溢價率',
            '股票收盤價', '股票漲跌幅', '轉換價', '已轉換(%)', '到期賣回日',
        ]
        
        # 如果有抓取詳細資料，加入詳細欄位
        if self.fetch_detail:
            detail_fields = [
                '可轉債名稱', '轉換標的名稱', '上市櫃別', '擔保銀行TCRI',
                'CBAS權利金', 'CBAS折現率', '發行價格', '發行總額百萬', '最新餘額百萬',
                '轉換比例', '發行日', '到期日', '到期賣回價格', '提前賣回日', '提前賣回價格',
                '發行辦法連結', '公開說明書連結', '財報狗連結', 'goodinfo連結'
            ]
            fieldnames.extend(detail_fields)
        
        with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            
            for row in data:
                # 確保所有欄位都存在
                complete_row = {field: row.get(field, '') for field in fieldnames}
                writer.writerow(complete_row)
        
        logger.info(f"✅ CSV 檔案已儲存: {csv_file}")
        logger.info(f"   共 {len(data)} 筆資料")
        
        return str(csv_file)

    def run(self) -> Optional[str]:
        """
        執行爬蟲
        
        Returns:
            CSV 檔案路徑，失敗則返回 None
        """
        try:
            # 初始化瀏覽器
            self.init_browser()
            
            # 確保已登入
            if not self.ensure_logged_in():
                logger.error("❌ 無法登入，中止執行")
                return None
            
            # 抓取資料
            data = self.scrape_cb_data()
            
            if not data:
                logger.error("❌ 未抓取到任何資料")
                return None
            
            # 儲存到 CSV
            csv_file = self.save_to_csv(data)
            
            logger.info("=" * 60)
            logger.info("✅ 爬蟲執行完成")
            logger.info(f"   CSV 檔案: {csv_file}")
            logger.info("=" * 60)
            
            return csv_file
            
        except Exception as e:
            logger.error(f"❌ 執行過程發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            return None

    def close(self) -> None:
        """關閉瀏覽器"""
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            logger.info("🔌 瀏覽器已關閉")
        except Exception as e:
            logger.error(f"關閉瀏覽器時發生錯誤: {e}")


def main():
    """主函數"""
    scraper = None
    try:
        with TheFewScraper() as scraper:
            scraper.run()
    except KeyboardInterrupt:
        logger.info("⚠️  使用者中斷執行")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ 執行失敗: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

