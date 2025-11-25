#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patreon Video Downloader
Simple script to download video from a Patreon post using Google account authentication
"""

import asyncio
import os
import time
import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
import httpx
from tqdm import tqdm

# ============================================================================
# Main Script
# ============================================================================

class PatreonVideoDownloader:
    """Download videos from Patreon posts"""
    
    def __init__(self, env_file: str = ".env_patreon"):
        """Initialize downloader with configuration from .env file"""
        # Load environment variables
        env_path = Path(env_file)
        if not env_path.exists():
            raise FileNotFoundError(f"找不到環境變數檔案: {env_file}")
        
        load_dotenv(env_path)
        
        # Read configuration from .env
        self.post_url = os.getenv("PATREON_POST_URL")
        self.download_path = Path(os.getenv("DOWNLOAD_PATH", "downloads"))
        self.google_email = os.getenv("GOOGLE_EMAIL")
        self.google_password = os.getenv("GOOGLE_PASSWORD")
        self.headless = os.getenv("HEADLESS", "false").lower() == "true"
        self.cookie_file = Path(os.getenv("COOKIE_FILE", "patreon_cookies.json"))
        self.use_saved_cookies = os.getenv("USE_SAVED_COOKIES", "true").lower() == "true"
        
        # Validate required fields
        if not self.post_url:
            raise ValueError("PATREON_POST_URL is required in .env_patreon")
        if not self.google_email:
            raise ValueError("GOOGLE_EMAIL is required in .env_patreon")
        if not self.google_password:
            raise ValueError("GOOGLE_PASSWORD is required in .env_patreon")
        
        self.download_path.mkdir(parents=True, exist_ok=True)
        
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.captured_requests: List[Dict] = []
        
    async def setup_browser(self, playwright):
        """Setup Playwright browser with network interception"""
        print("🔧 Setting up browser...")
        
        self.browser = await playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox'
            ]
        )
        
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        self.page = await self.context.new_page()
        
        # Set up network request listener
        self.page.on("response", self._handle_response)
        
        print("✅ Browser ready")
    
    async def save_cookies(self):
        """Save cookies to file for future use"""
        try:
            cookies = await self.context.cookies()
            cookie_data = {
                'cookies': cookies,
                'saved_at': datetime.now().isoformat(),
                'email': self.google_email
            }
            
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookie_data, f, indent=2, ensure_ascii=False)
            
            print(f"💾 Cookies saved to: {self.cookie_file}")
            return True
        except Exception as e:
            print(f"⚠️  Failed to save cookies: {e}")
            return False
    
    async def load_cookies(self):
        """Load cookies from file"""
        if not self.cookie_file.exists():
            print(f"ℹ️  No saved cookies found at: {self.cookie_file}")
            return False
        
        try:
            with open(self.cookie_file, 'r', encoding='utf-8') as f:
                cookie_data = json.load(f)
            
            saved_email = cookie_data.get('email')
            saved_at = cookie_data.get('saved_at')
            cookies = cookie_data.get('cookies', [])
            
            if not cookies:
                print("⚠️  Cookie file is empty")
                return False
            
            if saved_email != self.google_email:
                print(f"⚠️  Saved cookies are for different account: {saved_email}")
                print(f"    Current account: {self.google_email}")
                print("    Will login with current account instead")
                return False
            
            print(f"📂 Loading cookies from: {self.cookie_file}")
            print(f"    Saved at: {saved_at}")
            print(f"    Account: {saved_email}")
            
            # Add cookies to context
            await self.context.add_cookies(cookies)
            print(f"✅ Loaded {len(cookies)} cookies")
            
            return True
            
        except Exception as e:
            print(f"⚠️  Failed to load cookies: {e}")
            return False
    
    async def verify_login(self):
        """Verify if we're logged in to Patreon"""
        try:
            await self.page.goto("https://www.patreon.com", wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)
            
            # Check if we're logged in by looking for common logged-in elements
            # Try to find user menu or profile icon
            logged_in_selectors = [
                "[data-tag='user-menu']",
                "[aria-label='Account']",
                "a[href*='/settings']",
                "button[aria-label*='profile' i]"
            ]
            
            for selector in logged_in_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        print("✅ Already logged in to Patreon!")
                        return True
                except:
                    continue
            
            # Check if we see login/signup buttons (means not logged in)
            login_selectors = ["a[href*='/login']", "button:has-text('Log in')", "a:has-text('Log in')"]
            for selector in login_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element and await element.is_visible():
                        print("ℹ️  Not logged in yet")
                        return False
                except:
                    continue
            
            # If we can't determine, assume not logged in
            print("ℹ️  Could not verify login status")
            return False
            
        except Exception as e:
            print(f"⚠️  Error verifying login: {e}")
            return False
        
    async def _handle_response(self, response):
        """Handle network responses to capture video URLs"""
        try:
            url = response.url
            content_type = response.headers.get('content-type', '')
            content_length = response.headers.get('content-length', '0')
            
            # Check if it's a video URL or video content type
            is_video_url = any(ext in url.lower() for ext in ['.mp4', '.m4v', '.m3u8', '.ts', '.webm'])
            is_video_content = 'video' in content_type.lower()
            
            if is_video_url or is_video_content:
                # Skip very small files (likely not real videos)
                try:
                    size_bytes = int(content_length)
                    if size_bytes > 0 and size_bytes < 100000:  # Skip files smaller than 100KB
                        print(f"  ⚠️  Skipped small file ({size_bytes} bytes): {url[:80]}...")
                        return
                except:
                    pass
                
                video_info = {
                    'url': url,
                    'content_type': content_type,
                    'status': response.status,
                    'size': content_length
                }
                
                # Avoid duplicates
                if not any(v['url'] == url for v in self.captured_requests):
                    self.captured_requests.append(video_info)
                    size_mb = f"{int(content_length) / (1024*1024):.2f} MB" if content_length and content_length.isdigit() else "unknown size"
                    print(f"  🎬 Found video: {url[:80]}... ({size_mb})")
        except Exception as e:
            pass
    
    async def login_with_google(self):
        """Login to Patreon using Google account"""
        print("\n🔐 Logging in to Patreon with Google account...")
        
        try:
            # Navigate to Patreon login
            await self.page.goto("https://www.patreon.com/login", wait_until="networkidle")
            await asyncio.sleep(2)
            
            # Click "Continue with Google" button
            google_btn = await self.page.wait_for_selector("button:has-text('Google'), a:has-text('Google')", timeout=10000)
            await google_btn.click()
            print("👆 Clicked 'Continue with Google' button")
            await asyncio.sleep(3)
            
            # Check if a popup opened
            popup = None
            try:
                popup = await self.context.wait_for_event('page', timeout=5000)
                page_to_use = popup
                print("🔄 Switched to Google login popup")
            except:
                page_to_use = self.page
                print("ℹ️  Using same page for Google login")
            
            # Enter email
            email_input = await page_to_use.wait_for_selector("input[type='email']", timeout=10000)
            await email_input.fill(self.google_email)
            print(f"📧 Entered email: {self.google_email}")
            
            # Click Next
            await page_to_use.click("#identifierNext")
            await asyncio.sleep(3)
            
            # Enter password
            password_input = await page_to_use.wait_for_selector("input[type='password']", timeout=10000)
            await password_input.fill(self.google_password)
            print("🔑 Entered password")
            
            # Click Next
            await page_to_use.click("#passwordNext")
            print("⏳ Waiting for login to complete...")
            
            # Wait for redirect back to Patreon
            await asyncio.sleep(5)
            
            # If popup was used, close it and return to main page
            if popup:
                await popup.close()
            
            # Wait for Patreon to load (check URL doesn't contain 'login')
            await self.page.wait_for_function("window.location.href.includes('patreon.com') && !window.location.href.includes('login')", timeout=30000)
            
            print("✅ Successfully logged in to Patreon")
            
            # Save cookies for future use
            await self.save_cookies()
            
            return True
            
        except Exception as e:
            print(f"⚠️  Login process encountered an issue: {e}")
            print("\n💡 If you see the login page, please complete it manually in the browser")
            print("   The script will wait 30 seconds for you to login...")
            await asyncio.sleep(30)
            return True
    
    async def navigate_to_post(self):
        """Navigate to the Patreon post"""
        print(f"\n📄 Navigating to post: {self.post_url}")
        await self.page.goto(self.post_url, wait_until="networkidle")
        await asyncio.sleep(3)
        print("✅ Post loaded")
    
    async def wait_for_video_to_load(self):
        """Wait for video element to appear and try to play it"""
        print("\n⏳ Waiting for video to load...")
        
        try:
            # Wait for video element
            video = await self.page.wait_for_selector("video", timeout=20000)
            print("✅ Video element found")
            
            # Get video source info
            video_src = await video.get_attribute('src')
            print(f"ℹ️  Video src: {video_src if video_src else 'blob or dynamic source'}")
            
            # Try multiple selectors for play button
            play_button_selectors = [
                "button[aria-label*='play' i]",
                "button[class*='play' i]",
                "button:has-text('Play')",
                "[data-tag*='Play' i]",
                "button svg[data-tag*='Play' i]",
            ]
            
            play_clicked = False
            for selector in play_button_selectors:
                try:
                    play_btn = await self.page.query_selector(selector)
                    if play_btn:
                        # Check if button is visible
                        is_visible = await play_btn.is_visible()
                        if is_visible:
                            await play_btn.click()
                            print("▶️  Clicked play button")
                            play_clicked = True
                            await asyncio.sleep(3)
                            break
                except:
                    continue
            
            if not play_clicked:
                print("ℹ️  No play button found, trying to play video directly via JavaScript")
                # Try to play video directly
                try:
                    await self.page.evaluate("document.querySelector('video').play()")
                    print("▶️  Started video playback via JavaScript")
                    await asyncio.sleep(3)
                except Exception as e:
                    print(f"ℹ️  Could not play video directly: {e}")
            
            # Wait longer for video to buffer and generate network requests
            print("⏳ Waiting for video to buffer (initial 5 seconds)...")
            await asyncio.sleep(5)
            
            # Check if we captured any requests yet
            if not self.captured_requests:
                print("⏳ No video URLs yet, waiting another 10 seconds...")
                await asyncio.sleep(10)
            
            # If still nothing, wait even more
            if not self.captured_requests:
                print("⏳ Still waiting for video URLs... (another 10 seconds)")
                await asyncio.sleep(10)
            
            return True
            
        except Exception as e:
            print(f"⚠️  Could not find video element: {e}")
            return False
    
    async def download_video(self, video_url, filename=None):
        """Download video from URL"""
        if not filename:
            # Generate filename from post URL
            post_id = self.post_url.split('-')[-1]
            # Try to get file extension from URL
            url_lower = video_url.lower()
            if '.mp4' in url_lower:
                ext = '.mp4'
            elif '.webm' in url_lower:
                ext = '.webm'
            elif '.m4v' in url_lower:
                ext = '.m4v'
            else:
                ext = '.mp4'
            filename = f"patreon_video_{post_id}{ext}"
        
        output_path = self.download_path / filename
        
        print(f"\n⬇️  Downloading video to: {output_path}")
        print(f"📍 Video URL: {video_url}")
        
        try:
            # Get cookies from Playwright for authenticated download
            cookies = await self.context.cookies()
            cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}
            
            # Get user agent
            user_agent = await self.page.evaluate("navigator.userAgent")
            
            headers = {
                'User-Agent': user_agent,
                'Referer': self.post_url,
                'Accept': '*/*',
                'Accept-Encoding': 'identity',
                'Range': 'bytes=0-',
            }
            
            print(f"🔑 Using {len(cookie_dict)} cookies for authentication")
            
            # First, try HEAD request to check file size
            async with httpx.AsyncClient(cookies=cookie_dict, headers=headers, timeout=60.0, follow_redirects=True) as client:
                try:
                    head_response = await client.head(video_url)
                    content_length = head_response.headers.get('content-length', '0')
                    content_type = head_response.headers.get('content-type', 'unknown')
                    print(f"📊 File info - Size: {content_length} bytes, Type: {content_type}")
                    
                    # Warn if file seems too small
                    if content_length.isdigit() and int(content_length) < 100000:
                        print(f"⚠️  Warning: File size is very small ({int(content_length)} bytes)")
                        print("    This might not be the actual video file")
                except:
                    print("ℹ️  Could not get HEAD info, proceeding with download...")
            
            # Download with progress bar using httpx
            async with httpx.AsyncClient(cookies=cookie_dict, headers=headers, timeout=300.0, follow_redirects=True) as client:
                async with client.stream('GET', video_url) as response:
                    print(f"📥 Response status: {response.status_code}")
                    print(f"📋 Content-Type: {response.headers.get('content-type', 'unknown')}")
                    
                    response.raise_for_status()
                    
                    total_size = int(response.headers.get('content-length', 0))
                    
                    # Warn again if size is too small
                    if total_size > 0 and total_size < 100000:
                        print(f"⚠️  Warning: Downloading small file ({total_size} bytes)")
                    
                    with open(output_path, 'wb') as f, tqdm(
                        total=total_size,
                        unit='B',
                        unit_scale=True,
                        desc=filename
                    ) as pbar:
                        downloaded = 0
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                            downloaded += len(chunk)
                            pbar.update(len(chunk))
            
            # Verify downloaded file size
            actual_size = output_path.stat().st_size
            print(f"✅ Video downloaded: {output_path}")
            print(f"📦 File size: {actual_size:,} bytes ({actual_size / (1024*1024):.2f} MB)")
            
            # Warn if file is suspiciously small
            if actual_size < 100000:
                print(f"\n⚠️  WARNING: Downloaded file is very small ({actual_size} bytes)")
                print("    This is likely NOT the actual video file!")
                print("    The URL might be protected or the video uses streaming (HLS/DASH)")
                return False
            
            return True
            
        except Exception as e:
            print(f"❌ Download failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def find_best_video_url(self, video_urls):
        """Select the best video URL from captured requests"""
        if not video_urls:
            return None
        
        # Prefer direct .mp4 URLs
        mp4_urls = [v for v in video_urls if '.mp4' in v['url'].lower()]
        if mp4_urls:
            return mp4_urls[0]['url']
        
        # Otherwise return the first video URL found
        return video_urls[0]['url']
    
    async def run(self):
        """Main execution flow"""
        playwright = None
        try:
            playwright = await async_playwright().start()
            await self.setup_browser(playwright)
            
            # Try to use saved cookies first
            cookies_loaded = False
            if self.use_saved_cookies:
                cookies_loaded = await self.load_cookies()
                
                if cookies_loaded:
                    # Verify if cookies are still valid
                    if await self.verify_login():
                        print("🎉 Using saved login session!")
                    else:
                        print("⚠️  Saved cookies are expired or invalid")
                        cookies_loaded = False
            
            # If cookies didn't work, do normal login
            if not cookies_loaded:
                await self.login_with_google()
            
            await self.navigate_to_post()
            await self.wait_for_video_to_load()
            
            # Check captured requests
            if not self.captured_requests:
                print("\n⚠️  No video URLs found in network traffic.")
                print("    This might be because:")
                print("    1. The video is DRM protected")
                print("    2. The video uses a streaming protocol we can't easily capture")
                print("    3. The post doesn't contain a video")
                
                if not self.headless:
                    print("\n💡 Browser will stay open for 30 seconds for manual inspection...")
                    print("    Check the Network tab in DevTools (F12) for video requests")
                    await asyncio.sleep(30)
                
                return False
            
            print(f"\n✅ Found {len(self.captured_requests)} potential video URL(s)")
            
            # Show all found URLs
            for i, v in enumerate(self.captured_requests, 1):
                print(f"   {i}. {v['url'][:80]}...")
            
            # Try to download the best one
            best_url = self.find_best_video_url(self.captured_requests)
            if best_url:
                print(f"\n🎯 Selected URL: {best_url[:100]}...")
                await self.download_video(best_url)
            
            if not self.headless:
                print("\n✅ Done! Browser will close in 5 seconds...")
                await asyncio.sleep(5)
            
            return True
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            return False
            
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            return False
            
        finally:
            if self.browser:
                print("🔚 Closing browser...")
                await self.browser.close()
            if playwright:
                await playwright.stop()


async def main():
    """Main entry point"""
    print("=" * 70)
    print("🎬 Patreon Video Downloader")
    print("=" * 70)
    
    try:
        downloader = PatreonVideoDownloader(env_file=".env_patreon")
        success = await downloader.run()
        
        if success:
            print("\n✅ All done!")
        else:
            print("\n⚠️  Process completed with issues")
    
    except FileNotFoundError as e:
        print(f"\n❌ {e}")
        print("Please create .env_patreon file with required configuration.")
        print("See .env_patreon.example for reference.")
    except ValueError as e:
        print(f"\n❌ Configuration error: {e}")
        print("Please check your .env_patreon file.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

