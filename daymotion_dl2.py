#!/usr/bin/env python3
"""
批量 Dailymotion 視頻下載器
專為 Evan 系列視頻設計
修改版：順序下載，每次下載後休息指定時間
"""

import os
import sys
import requests
import re
import json
import time
from urllib.parse import urljoin
from pathlib import Path

# 視頻列表數據
VIDEO_LIST = [
    # Evan 指標示範系列
    # {
    #     "date": "20250302",
    #     "title": "Evan指標示範",
    #     "url": "https://dai.ly/k2SbEZbmP4zlcCDttky",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250309",
    #     "title": "Evan指標示範",
    #     "url": "https://dai.ly/k12MpdqZOuexYADtt26",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250316",
    #     "title": "Evan指標示範",
    #     "url": "https://dai.ly/k6phxwIGeNn8ksDrRoY",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250323",
    #     "title": "Evan指標示範",
    #     "url": "https://dai.ly/k5nJbTwmwFlBHkDrRig",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250330",
    #     "title": "Evan指標示範",
    #     "url": "https://dai.ly/k5ohjU4x4sZuySDrRaQ",
    #     "category": "Evan"
    # },
    # # Evan 四月系列
    # {
    #     "date": "20250406",
    #     "title": "Evan",
    #     "url": "https://dai.ly/k3uJNpXP1P1YHeDrNWk",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250413",
    #     "title": "Evan",
    #     "url": "https://dai.ly/k5S6IWSwnyLDUADdKqi",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250420",
    #     "title": "Evan",
    #     "url": "https://dai.ly/k4BhkhDqoV1XK9DdJQG",
    #     "category": "Evan"
    # },
    # {
    #     "date": "20250427",
    #     "title": "Evan",
    #     "url": "https://dai.ly/k1gilUnlBXffc9DdJOw",
    #     "category": "Evan"
    # },
    # # 六月錄影系列
    # {
    #     "date": "20250601",
    #     "title": "六月錄影",
    #     "url": "https://dai.ly/k3cAldu0Abe2t4Dce1C",
    #     "category": "六月錄影"
    # },
    # {
    #     "date": "20250608",
    #     "title": "六月錄影",
    #     "url": "https://dai.ly/k13OAErmzmbSBCDdup4",
    #     "category": "六月錄影"
    # },
    # {
    #     "date": "20250615",
    #     "title": "六月錄影",
    #     "url": "https://dai.ly/k3vtkVI6imtIYbDgisS",
    #     "category": "六月錄影"
    # },
    # {
    #     "date": "20250622",
    #     "title": "六月錄影",
    #     "url": "https://dai.ly/k4rPETjJJ6JVSMDivzY",
    #     "category": "六月錄影"
    # },
    # {
    #     "date": "20250629",
    #     "title": "六月錄影",
    #     "url": "https://dai.ly/k7IkBjMpBsfIT9DkXT0",
    #     "category": "六月錄影"
    # },
    # # Feng 系列
    # {
    #     "date": "20250616",
    #     "title": "Feng Part1",
    #     "url": "https://dai.ly/ks8AfDqYosbWOpDgJVM",
    #     "category": "Feng"
    # },
    # {
    #     "date": "20250616",
    #     "title": "Feng Part2",
    #     "url": "https://dai.ly/kryB7XTzDuBvbsDgJVK",
    #     "category": "Feng"
    # },
    # {
    #     "date": "20250616",
    #     "title": "Feng Part3",
    #     "url": "https://dai.ly/k5O2vhyEr4i9myDgKg8",
    #     "category": "Feng"
    # },
    # {
    #     "date": "20250630",
    #     "title": "Feng Part1",
    #     "url": "https://dai.ly/k1jqw7wpSs67zgDlebW",
    #     "category": "Feng"
    # },
    # {
    #     "date": "20250630",
    #     "title": "Feng Part2",
    #     "url": "https://dai.ly/k4cJwzh0549HznDlekG",
    #     "category": "Feng"
    # },
    # # FOMC 系列
    # {
    #     "date": "20250618",
    #     "title": "FOMC Part1",
    #     "url": "https://dai.ly/k5WjJ9aYyteKLgDhpfM",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250618",
    #     "title": "FOMC Part2",
    #     "url": "https://dai.ly/k3IHCzl8GENCzMDhph8",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250618",
    #     "title": "FOMC Part3",
    #     "url": "https://dai.ly/k3EryXrBZhjOBbDhpmI",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250618",
    #     "title": "FOMC Part4",
    #     "url": "https://dai.ly/k6qLyg1nNH8ucEDhpmK",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250706",
    #     "title": "FOMC",
    #     "url": "https://dai.ly/k2p1kJzlJLQOvsDns1Y",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250713",
    #     "title": "FOMC",
    #     "url": "https://dai.ly/k14iU8Eyti07a8DqlhW",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250720",
    #     "title": "FOMC (無聲)",
    #     "url": "https://dai.ly/k1p9A7iupdf1LZDtmOi",
    #     "category": "FOMC"
    # },
    # {
    #     "date": "20250727",
    #     "title": "FOMC",
    #     "url": "https://dai.ly/k1HMgyh29Vy5H2Dw6De",
    #     "category": "FOMC"
    # },

    # {
    #     "date": "20250803",
    #     "title": "",
    #     "url": "https://dai.ly/k3TorJITDenrebDzdZO",
    #     "category": ""
    # },
    # {
    #     "date": "20250810",
    #     "title": "",
    #     "url": "https://dai.ly/k4VWX633An9C6NDC5gU",
    #     "category": ""
    # },
    # {
    #     "date": "20250817",
    #     "title": "",
    #     "url": "https://dai.ly/knuvFmC19J9AwaDF7Wc",
    #     "category": ""
    # },
    # {
    #     "date": "20250824",
    #     "title": "",
    #     "url": "https://dai.ly/k34VcMVuhQ5Ve8DIa20",
    #     "category": ""
    # },
    # {
    #     "date": "20250831",
    #     "title": "VIX與指數創高",
    #     "url": "https://dai.ly/k4YfFXCbCJ8jmzDKVNQ",
    #     "category": "VIX與指數創高"
    # },
    # {
    #     "date": "20250907",
    #     "title": "avoid lunch hours",
    #     "url": "https://dai.ly/k72B2LGkqBoLsODNFd4",
    #     "category": "avoid lunch hours"
    # },
    # {
    #     "date": "20250914",
    #     "title": "",
    #     "url": "https://dai.ly/k4bNknOFZ8zqOvDQlze",
    #     "category": ""
    # },
    # {
    #     "date": "20250921",
    #     "title": "收在collar之上 就buy the dip",
    #     "url": "https://dai.ly/k15hdZ3HS9xOuYDTdBi",
    #     "category": "收在collar之上 就buy the dip"
    # },
    # {
    #     "date": "20250928",
    #     "title": "這週可能是 Bear 的最後窗口",
    #     "url": "https://dai.ly/k10oapvDVn2muMDW2Lk",
    #     "category": "這週可能是 Bear 的最後窗口"
    # }
    # {
    #     "date": "20251012",
    #     "title": "小心 VIX 到 25 之上 ",
    #     "url": "https://dai.ly/k4N5xqFXdhgjOhE0Vg2",
    #     "category": "Generate"
    # },
    # {
    #     "date": "2025-12-15",
    #     "title": "trendline & volume profile ",
    #     "url": " https://dai.ly/k3B15npViEfsIdEqLvo",
    #     "category": "trendline & volume profile"
    # },
    # {
    #     "date": "2025-12-21",
    #     "title": "2026 jan new high",
    #     "url": "https://dai.ly/k1R4FmICvtm96DEtZnK",
    #     "category": "2026 jan new high"
    # },
    # {
    #     "date": "2025-12-28",
    #     "title": " fp fp ce spread  ",
    #     "url": "https://dai.ly/kcUQOHjMgtBy8REwXsC",
    #     "category": "fp fp ce spread"
    # },
    # {
    #     "date": "2026-01-04",
    #     "title": "Captain Condor",
    #     "url": "https://dai.ly/k5IMvJho26WpACEAkRQ",
    #     "category": "Captain Condor"
    # },
    # {
    #     "date": "2026-01-11",
    #     "title": "2026 New guidance",
    #     "url": "https://dai.ly/k5VFJTe1MP6ZpqEDSvC",
    #     "category": "2026 New guidance"
    # },
    # {
    #     "date": "2026-01-18",
    #     "title": "Kevin Warsh and superboy",
    #     "url": "https://dai.ly/kC6GE06533vsCLEH8JW",
    #     "category": "Kevin Warsh and superboy"
    # },
    {
        "date": "2026-01-18",
        "title": "feng 經驗精華",
        "url": "https://dai.ly/k1qGUheiUO85IUEKpPq",
        "category": "feng best sharing"
    },
]


class SequentialDailymotionDownloader:
    def __init__(self, rest_interval=120):  # 預設休息 2 分鐘 (120 秒)
        """順序下載器初始化"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.rest_interval = rest_interval  # 休息間隔（秒）
        self.download_stats = {
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'total': 0
        }

    def set_rest_interval(self, seconds):
        """設定休息間隔時間（秒）"""
        self.rest_interval = seconds
        print(f"休息間隔已設定為 {seconds} 秒 ({seconds / 60:.1f} 分鐘)")

    def sanitize_filename(self, filename):
        """清理文件名"""
        invalid_chars = r'<>:"/\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename.strip()

    # def download_with_yt_dlp_single(self, video_info, output_dir):
    #     """使用 yt-dlp 下載單個視頻"""
    #     try:
    #         import yt_dlp
    #
    #         # 創建分類文件夾
    #         category_dir = os.path.join(output_dir, video_info['category'])
    #         Path(category_dir).mkdir(parents=True, exist_ok=True)
    #
    #         # 自定義文件名模板
    #         filename_template = f"{video_info['date']}_{video_info['title']}_%(id)s.%(ext)s"
    #         safe_filename = self.sanitize_filename(filename_template)
    #
    #         ydl_opts = {
    #             'outtmpl': os.path.join(category_dir, safe_filename),
    #             # 'format': 'best[height<=1080]',
    #             'format': 'best[height<=720]',
    #             'concurrent_fragment_downloads': 2,  # 降低併發數
    #             'http_chunk_size': 5242880,  # 5MB 塊大小
    #             'retries': 3,
    #             'fragment_retries': 3,
    #             'writesubtitles': False,
    #             'writeautomaticsub': False,
    #             'ignoreerrors': True,
    #             'quiet': False,  # 顯示下載進度
    #             'no_warnings': False,
    #         }
    #
    #         with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    #             ydl.download([video_info['url']])
    #
    #         return True, f"成功下載: {video_info['date']} {video_info['title']}"
    #
    #     except ImportError:
    #         return False, "yt-dlp 未安裝"
    #     except Exception as e:
    #         return False, f"下載失敗: {str(e)}"

    def download_with_yt_dlp_single(self, video_info, output_dir):
        """使用 yt-dlp 下載單個視頻"""
        try:
            import yt_dlp

            category_dir = os.path.join(output_dir, video_info['category'])
            Path(category_dir).mkdir(parents=True, exist_ok=True)

            filename_template = f"{video_info['date']}_{video_info['title']}_%(id)s.%(ext)s"
            safe_filename = self.sanitize_filename(filename_template)

            # 完整輸出路徑
            outtmpl_path = os.path.join(category_dir, safe_filename)

            ydl_opts = {
                'outtmpl': outtmpl_path,
                'format': 'best[height<=1024]',
                'concurrent_fragment_downloads': 5,
                'retries': 5,
                'fragment_retries': 5,
                # --- 關鍵修正 ---
                'ignoreerrors': False,  # 改為 False，這樣失敗時會拋出異常
                'no_warnings': False,
                'quiet': False,
                # 'cookiesfrombrowser': ('chrome',), # 如果持續 403，請取消此行註釋
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # 執行下載
                result = ydl.download([video_info['url']])
                # ydl.download 返回的是錯誤計數，0 表示成功
                if result != 0:
                    return False, "yt-dlp 下載回傳錯誤碼"

            return True, f"成功下載: {video_info['date']} {video_info['title']}"

        except Exception as e:
            return False, f"yt-dlp 執行異常: {str(e)}"
    def check_existing_file(self, video_info, output_dir):
        """檢查文件是否已存在"""
        category_dir = os.path.join(output_dir, video_info['category'])
        if not os.path.exists(category_dir):
            return False

        # 檢查可能的文件名變體
        patterns = [
            f"{video_info['date']}_{video_info['title']}_*.mp4",
            f"{video_info['date']}_{video_info['title']}_*.mkv",
            f"{video_info['date']}_{video_info['title']}_*.webm"
        ]

        import glob
        for pattern in patterns:
            if glob.glob(os.path.join(category_dir, pattern)):
                return True
        return False

    def download_single_video(self, video_info, output_dir, skip_existing=True):
        """下載單個視頻"""
        try:
            print(f"\n處理: [{video_info['category']}] {video_info['date']} - {video_info['title']}")
            print(f"URL: {video_info['url']}")

            # 檢查是否已存在
            if skip_existing and self.check_existing_file(video_info, output_dir):
                print("✓ 檔案已存在，跳過下載")
                self.download_stats['skipped'] += 1
                return True, "檔案已存在"

            # 顯示下載開始時間
            start_time = time.time()
            print(f"⏰ 開始下載時間: {time.strftime('%Y-%m-%d %H:%M:%S')}")

            # 嘗試使用 yt-dlp 下載
            success, message = self.download_with_yt_dlp_single(video_info, output_dir)

            # 計算下載耗時
            elapsed_time = time.time() - start_time

            if success:
                print(f"✓ 下載完成 (耗時: {elapsed_time:.1f}秒)")
                self.download_stats['success'] += 1
            else:
                print(f"✗ 下載失敗: {message} (耗時: {elapsed_time:.1f}秒)")
                self.download_stats['failed'] += 1

            return success, message

        except Exception as e:
            error_msg = f"處理視頻時出錯: {str(e)}"
            print(f"✗ {error_msg}")
            self.download_stats['failed'] += 1
            return False, error_msg

    def countdown_timer(self, seconds):
        """倒數計時器"""
        print(f"\n⏳ 休息 {seconds} 秒 ({seconds / 60:.1f} 分鐘)...")

        # 顯示倒數計時
        for remaining in range(seconds, 0, -1):
            mins, secs = divmod(remaining, 60)
            timer = f"{mins:02d}:{secs:02d}"
            print(f"\r⏱️  剩餘時間: {timer}", end='', flush=True)
            time.sleep(1)

        print(f"\r✅ 休息完畢，繼續下載...     ")

    def sequential_download(self, video_list=None, output_dir="lieta_downloads",
                            categories=None, skip_existing=True, rest_interval=None):
        """順序下載視頻（一次下載一個，每次下載後休息）"""
        if video_list is None:
            video_list = VIDEO_LIST

        # 設定休息間隔
        if rest_interval is not None:
            self.rest_interval = rest_interval

        # 過濾分類
        if categories:
            video_list = [v for v in video_list if v['category'] in categories]

        self.download_stats['total'] = len(video_list)

        print(f"順序下載開始...")
        print(f"總共 {len(video_list)} 個視頻")
        print(f"輸出目錄: {output_dir}")
        print(f"每次下載後休息: {self.rest_interval} 秒 ({self.rest_interval / 60:.1f} 分鐘)")
        print(f"跳過已存在檔案: {'是' if skip_existing else '否'}")
        print("=" * 60)

        # 創建輸出目錄
        Path(output_dir).mkdir(exist_ok=True)

        # 順序處理每個視頻
        for i, video in enumerate(video_list, 1):
            print(f"\n📹 進度: {i}/{len(video_list)}")

            try:
                # 下載視頻
                success, message = self.download_single_video(video, output_dir, skip_existing)

                # 如果不是最後一個視頻，則休息指定時間
                if i < len(video_list):
                    if self.rest_interval > 0:
                        self.countdown_timer(self.rest_interval)
                    else:
                        print("⏭️  立即繼續下一個視頻...")

            except KeyboardInterrupt:
                print(f"\n\n⚠️  用戶中斷下載")
                print(f"已處理 {i - 1}/{len(video_list)} 個視頻")
                break
            except Exception as e:
                print(f"處理視頻時發生未預期錯誤: {str(e)}")
                self.download_stats['failed'] += 1
                continue

        # 顯示統計結果
        self.print_summary()

    def print_summary(self):
        """顯示下載統計"""
        print("\n" + "=" * 60)
        print("📊 下載完成統計:")
        print(f"總數: {self.download_stats['total']}")
        print(f"✅ 成功: {self.download_stats['success']}")
        print(f"❌ 失敗: {self.download_stats['failed']}")
        print(f"⏭️  跳過: {self.download_stats['skipped']}")

        if self.download_stats['total'] > 0:
            success_rate = (self.download_stats['success'] / self.download_stats['total'] * 100)
            print(f"成功率: {success_rate:.1f}%")

        print(f"⏰ 完成時間: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def list_categories(self):
        """列出所有分類"""
        categories = set(video['category'] for video in VIDEO_LIST)
        return sorted(categories)

    def list_videos_by_category(self, category):
        """按分類列出視頻"""
        videos = [v for v in VIDEO_LIST if v['category'] == category]
        return videos


def main():
    print("順序 Dailymotion 視頻下載器")
    print("=" * 40)

    # 檢查 yt-dlp
    try:
        import yt_dlp
        print("✓ yt-dlp 可用")
    except ImportError:
        print("✗ yt-dlp 未安裝，請執行: pip install yt-dlp")
        return

    rest_interval_seconds = 600  # 休息間隔（秒）- 可以方便修改這裡
    # 創建下載器，設定休息間隔為 2 分鐘 (120 秒)
    downloader = SequentialDailymotionDownloader(rest_interval=rest_interval_seconds)

    # 顯示可用分類
    categories = downloader.list_categories()
    print(f"\n可用分類: {', '.join(categories)}")

    # === 設定區域 - 可以方便修改的參數 ===
    output_directory = "downloads"  # 下載目錄
    skip_existing_files = True  # 跳過已存在的檔案
    selected_categories = None  # 選擇的分類，None = 全部下載

    # 其他休息間隔選項:
    # rest_interval_seconds = 60    # 1分鐘
    # rest_interval_seconds = 180   # 3分鐘
    # rest_interval_seconds = 300   # 5分鐘
    # rest_interval_seconds = 0     # 不休息，連續下載

    # 分類選擇範例:
    # selected_categories = ['Evan']              # 只下載 Evan 系列
    # selected_categories = ['Evan', 'FOMC']      # 下載 Evan 和 FOMC 系列
    # selected_categories = ['六月錄影']           # 只下載六月錄影系列
    # ========================================

    print(f"\n開始順序下載...")
    print(f"選擇的分類: {'全部' if selected_categories is None else ', '.join(selected_categories)}")
    print(f"休息間隔: {rest_interval_seconds} 秒 ({rest_interval_seconds / 60:.1f} 分鐘)")

    # 開始順序下載
    downloader.sequential_download(
        output_dir=output_directory,
        categories=selected_categories,
        skip_existing=skip_existing_files,
        rest_interval=rest_interval_seconds
    )


def download_specific_category_with_custom_interval():
    """下載特定分類並自定義休息間隔的範例"""
    downloader = SequentialDailymotionDownloader()

    # 設定 3 分鐘休息間隔，只下載 Evan 系列
    downloader.sequential_download(
        output_dir="downloads",
        categories=['Evan'],
        skip_existing=True,
        rest_interval=180  # 3 分鐘
    )


def download_without_rest():
    """連續下載不休息的範例"""
    downloader = SequentialDailymotionDownloader()

    # 設定 0 秒休息間隔（連續下載）
    downloader.sequential_download(
        output_dir="downloads",
        categories=None,  # 全部分類
        skip_existing=True,
        rest_interval=0  # 不休息
    )


if __name__ == "__main__":
    main()

    # 其他使用方式的範例:
    # download_specific_category_with_custom_interval()  # 自定義間隔下載特定分類
    # download_without_rest()                           # 連續下載不休息