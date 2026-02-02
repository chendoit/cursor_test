#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PicBed Markdown 圖片同步腳本

掃描指定目錄的 .md 檔案，將外部圖片下載並上傳到 GitHub PicBed repo，
同時更新 md 檔案中的圖片連結。

Usage:
    python picbed_sync.py              # 基本執行
    python picbed_sync.py --dry-run    # 預覽模式（不實際修改）
    python picbed_sync.py --force      # 強制重新處理所有檔案
    python picbed_sync.py --status     # 檢查所有 PicBed repo 的容量狀態
"""

import os
import re
import json
import hashlib
import base64
import uuid
import argparse
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Dict, List, Optional, Tuple, Set

import requests
from dotenv import dotenv_values


# ============================================
# 常數設定
# ============================================
ENV_FILE = ".picbed_env"
PROCESSED_FILE = ".picbed_processed.json"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

# 容量警告閾值 (bytes)
WARNING_SIZE = 800 * 1024 * 1024   # 800 MB
CRITICAL_SIZE = 1024 * 1024 * 1024  # 1 GB

# 單檔案大小限制
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

# API 請求間隔 (秒)
API_DELAY = 0.5

# 重試次數
MAX_RETRIES = 3

# 支援的圖片格式
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'}

# Content-Type 到副檔名的映射
CONTENT_TYPE_MAP = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/svg+xml': '.svg',
    'image/bmp': '.bmp',
    'image/x-icon': '.ico',
    'image/vnd.microsoft.icon': '.ico',
}


# ============================================
# 工具函數
# ============================================
def load_config() -> Dict[str, str]:
    """載入設定檔"""
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        print(f"錯誤：找不到設定檔 {ENV_FILE}")
        print(f"請複製 {ENV_FILE}.example 為 {ENV_FILE} 並填入設定")
        exit(1)
    
    config = dotenv_values(env_path)
    
    # 驗證必要設定
    if not config.get('GITHUB_TOKEN'):
        print("錯誤：GITHUB_TOKEN 未設定")
        exit(1)
    
    return config


def get_picbed_repos(config: Dict[str, str]) -> List[str]:
    """取得所有 PicBed repo 列表"""
    repos = []
    for i in range(1000):
        key = f"PICBED_REPO_{i:03d}"
        if key in config and config[key]:
            repos.append(config[key])
    
    if not repos:
        print("錯誤：未設定任何 PICBED_REPO_xxx")
        exit(1)
    
    return repos


def get_folders(config: Dict[str, str]) -> List[str]:
    """取得所有要掃描的目錄"""
    folders = []
    for i in range(1000):
        key = f"FOLDER_{i:03d}"
        if key in config and config[key]:
            folder = config[key]
            if Path(folder).exists():
                folders.append(folder)
            else:
                print(f"警告：目錄不存在，跳過 - {folder}")
    
    if not folders:
        print("錯誤：未設定任何有效的 FOLDER_xxx")
        exit(1)
    
    return folders


def load_processed_data() -> Dict:
    """載入處理記錄"""
    path = Path(PROCESSED_FILE)
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"警告：{PROCESSED_FILE} 格式錯誤，將重新建立")
    
    return {"files": {}, "url_mapping": {}}


def save_processed_data(data: Dict):
    """儲存處理記錄"""
    with open(PROCESSED_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calculate_file_hash(filepath: str) -> str:
    """計算檔案的 SHA256 hash"""
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def generate_unique_filename(original_name: str, extension: str) -> str:
    """生成唯一檔名"""
    # 清理原始檔名
    name = Path(original_name).stem
    # 移除非法字元
    name = re.sub(r'[^\w\-]', '_', name)
    # 限制長度
    name = name[:50] if len(name) > 50 else name
    # 加上 UUID
    short_uuid = uuid.uuid4().hex[:8]
    return f"{name}_{short_uuid}{extension}"


def get_extension_from_url(url: str) -> str:
    """從 URL 取得副檔名"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return ext
    return ""


def get_extension_from_content_type(content_type: str) -> str:
    """從 Content-Type 取得副檔名"""
    # 移除參數部分
    ct = content_type.split(';')[0].strip().lower()
    return CONTENT_TYPE_MAP.get(ct, "")


# ============================================
# GitHub API 函數
# ============================================
class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'PicBed-Sync-Script'
        })
    
    def get_repo_size(self, repo: str) -> Optional[int]:
        """取得 repo 大小（單位：KB）"""
        url = f"{GITHUB_API_BASE}/repos/{repo}"
        try:
            resp = self.session.get(url)
            if resp.status_code == 200:
                return resp.json().get('size', 0)
            else:
                print(f"警告：無法取得 {repo} 的資訊 - {resp.status_code}")
                return None
        except Exception as e:
            print(f"錯誤：取得 repo 資訊失敗 - {e}")
            return None
    
    def upload_file(self, repo: str, branch: str, path: str, 
                    content: bytes, message: str) -> Optional[str]:
        """上傳檔案到 GitHub repo"""
        url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}"
        
        # Base64 編碼內容
        content_b64 = base64.b64encode(content).decode('utf-8')
        
        data = {
            'message': message,
            'content': content_b64,
            'branch': branch
        }
        
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.put(url, json=data)
                
                if resp.status_code == 201:
                    # 成功上傳
                    return f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}"
                elif resp.status_code == 422:
                    # 檔案已存在，取得 SHA 後更新
                    get_resp = self.session.get(url, params={'ref': branch})
                    if get_resp.status_code == 200:
                        sha = get_resp.json().get('sha')
                        data['sha'] = sha
                        resp = self.session.put(url, json=data)
                        if resp.status_code == 200:
                            return f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}"
                
                print(f"警告：上傳失敗 (嘗試 {attempt + 1}/{MAX_RETRIES}) - {resp.status_code}: {resp.text[:200]}")
                
            except Exception as e:
                print(f"錯誤：上傳時發生異常 (嘗試 {attempt + 1}/{MAX_RETRIES}) - {e}")
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(API_DELAY * 2)
        
        return None


# ============================================
# 圖片處理函數
# ============================================
def extract_images_from_markdown(content: str) -> List[Tuple[str, str, str]]:
    """
    從 Markdown 內容中提取圖片連結
    
    Returns:
        List of (full_match, alt_text, url)
    """
    images = []
    
    # Markdown 圖片格式: ![alt](url) 或 ![alt](url "title")
    md_pattern = r'(!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\))'
    for match in re.finditer(md_pattern, content):
        full_match = match.group(1)
        alt_text = match.group(2)
        url = match.group(3)
        images.append((full_match, alt_text, url))
    
    # HTML img 標籤: <img src="url" ... />
    html_pattern = r'(<img[^>]+src=["\']([^"\']+)["\'][^>]*>)'
    for match in re.finditer(html_pattern, content, re.IGNORECASE):
        full_match = match.group(1)
        url = match.group(2)
        images.append((full_match, "", url))
    
    return images


def is_picbed_url(url: str, picbed_repos: List[str]) -> bool:
    """檢查 URL 是否已經是 PicBed repo 的連結"""
    for repo in picbed_repos:
        if f"raw.githubusercontent.com/{repo}" in url:
            return True
        if f"github.com/{repo}" in url:
            return True
    return False


def is_local_path(url: str) -> bool:
    """檢查是否為本地相對路徑"""
    # 不是以 http/https/data: 開頭的都視為本地路徑
    if url.startswith(('http://', 'https://', 'data:')):
        return False
    return True


def download_image(url: str) -> Optional[Tuple[bytes, str]]:
    """
    下載圖片
    
    Returns:
        (content, extension) or None if failed
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            if resp.status_code == 200:
                content = resp.content
                
                # 檢查檔案大小
                if len(content) > MAX_FILE_SIZE:
                    print(f"警告：圖片過大 ({len(content) / 1024 / 1024:.1f} MB)，跳過 - {url}")
                    return None
                
                # 取得副檔名
                ext = get_extension_from_url(url)
                if not ext:
                    content_type = resp.headers.get('Content-Type', '')
                    ext = get_extension_from_content_type(content_type)
                if not ext:
                    ext = '.png'  # 預設
                
                return (content, ext)
            
            print(f"警告：下載失敗 (嘗試 {attempt + 1}/{MAX_RETRIES}) - HTTP {resp.status_code}")
            
        except Exception as e:
            print(f"錯誤：下載時發生異常 (嘗試 {attempt + 1}/{MAX_RETRIES}) - {e}")
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(1)
    
    return None


def read_local_image(base_path: str, relative_path: str) -> Optional[Tuple[bytes, str]]:
    """
    讀取本地圖片
    
    Returns:
        (content, extension) or None if failed
    """
    # 處理相對路徑
    full_path = Path(base_path).parent / relative_path
    
    if not full_path.exists():
        print(f"警告：本地圖片不存在 - {full_path}")
        return None
    
    ext = full_path.suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        print(f"警告：不支援的圖片格式 - {ext}")
        return None
    
    try:
        content = full_path.read_bytes()
        
        if len(content) > MAX_FILE_SIZE:
            print(f"警告：圖片過大 ({len(content) / 1024 / 1024:.1f} MB)，跳過 - {full_path}")
            return None
        
        return (content, ext)
    except Exception as e:
        print(f"錯誤：讀取本地圖片失敗 - {e}")
        return None


# ============================================
# 主要處理邏輯
# ============================================
def check_repo_status(github: GitHubClient, repos: List[str], current_index: int):
    """檢查並顯示所有 repo 的容量狀態"""
    print("=" * 50)
    print("PicBed Repo 容量狀態")
    print("=" * 50)
    
    for i, repo in enumerate(repos):
        size_kb = github.get_repo_size(repo)
        if size_kb is not None:
            size_mb = size_kb / 1024
            percent = (size_kb * 1024) / CRITICAL_SIZE * 100
            
            marker = " ← 目前使用中" if i == current_index else ""
            
            if size_mb >= 1024:
                size_str = f"{size_mb / 1024:.2f} GB"
            else:
                size_str = f"{size_mb:.1f} MB"
            
            status = ""
            if size_kb * 1024 >= CRITICAL_SIZE:
                status = " [已滿!]"
            elif size_kb * 1024 >= WARNING_SIZE:
                status = " [接近滿]"
            
            print(f"[{i}] {repo:30s}: {size_str:>10s} / 1 GB  ({percent:5.1f}%){status}{marker}")
        else:
            print(f"[{i}] {repo:30s}: 無法取得資訊")
        
        time.sleep(API_DELAY)
    
    print("-" * 50)
    
    # 檢查當前 repo 容量
    current_repo = repos[current_index]
    current_size_kb = github.get_repo_size(current_repo)
    if current_size_kb:
        current_size_bytes = current_size_kb * 1024
        if current_size_bytes >= CRITICAL_SIZE:
            print("!! 警告：當前 repo 已超過 1GB，請切換到下一個 repo !!")
            print(f"   修改 {ENV_FILE} 中的 CURRENT_REPO_INDEX={current_index + 1}")
        elif current_size_bytes >= WARNING_SIZE:
            print("提示：當前 repo 接近容量限制，建議準備切換")
    
    print("=" * 50)


def process_markdown_file(
    filepath: str,
    github: GitHubClient,
    repo: str,
    branch: str,
    picbed_repos: List[str],
    processed_data: Dict,
    dry_run: bool = False,
    enable_backup: bool = False
) -> Tuple[int, int, int]:
    """
    處理單一 Markdown 檔案
    
    Returns:
        (processed_count, skipped_count, failed_count)
    """
    processed = 0
    skipped = 0
    failed = 0
    
    # 讀取檔案內容
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"錯誤：無法讀取檔案 - {e}")
        return (0, 0, 1)
    
    original_content = content
    
    # 取得 md 檔案名稱作為上傳目錄
    md_name = Path(filepath).stem
    # 清理目錄名
    upload_dir = re.sub(r'[^\w\-\u4e00-\u9fff]', '_', md_name)
    
    # 提取圖片
    images = extract_images_from_markdown(content)
    
    if not images:
        return (0, 0, 0)
    
    print(f"  找到 {len(images)} 個圖片連結")
    
    for full_match, alt_text, url in images:
        # 檢查是否已經是 PicBed URL
        if is_picbed_url(url, picbed_repos):
            skipped += 1
            continue
        
        # 檢查 URL 映射中是否已處理過
        if url in processed_data.get('url_mapping', {}):
            # 使用已有的映射
            new_url = processed_data['url_mapping'][url]['new_url']
            new_match = full_match.replace(url, new_url)
            content = content.replace(full_match, new_match)
            skipped += 1
            print(f"    [已映射] {url[:60]}...")
            continue
        
        print(f"    處理: {url[:60]}...")
        
        # 下載或讀取圖片
        image_data = None
        if is_local_path(url):
            image_data = read_local_image(filepath, url)
        else:
            image_data = download_image(url)
        
        if not image_data:
            failed += 1
            continue
        
        image_content, ext = image_data
        
        # 生成唯一檔名
        original_name = Path(urlparse(url).path).name or "image"
        new_filename = generate_unique_filename(original_name, ext)
        upload_path = f"{upload_dir}/{new_filename}"
        
        if dry_run:
            print(f"    [預覽] 將上傳到: {repo}/{upload_path}")
            processed += 1
            continue
        
        # 上傳到 GitHub
        new_url = github.upload_file(
            repo=repo,
            branch=branch,
            path=upload_path,
            content=image_content,
            message=f"Upload image: {new_filename}"
        )
        
        if new_url:
            # 更新內容
            new_match = full_match.replace(url, new_url)
            content = content.replace(full_match, new_match)
            
            # 記錄映射
            processed_data.setdefault('url_mapping', {})[url] = {
                'new_url': new_url,
                'repo': repo,
                'uploaded_at': datetime.now().isoformat()
            }
            
            processed += 1
            print(f"    [成功] -> {new_url}")
        else:
            failed += 1
            print(f"    [失敗] 無法上傳")
        
        time.sleep(API_DELAY)
    
    # 如果內容有變更，寫回檔案
    if content != original_content and not dry_run:
        # 建立備份（如果啟用）
        backup_path = None
        if enable_backup:
            backup_path = filepath + '.bak'
            try:
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(original_content)
            except Exception as e:
                print(f"警告：無法建立備份 - {e}")
                backup_path = None
        
        # 寫入新內容
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            if backup_path:
                print(f"  已更新檔案（備份: {backup_path}）")
            else:
                print(f"  已更新檔案")
        except Exception as e:
            print(f"錯誤：無法寫入檔案 - {e}")
            # 嘗試還原
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(original_content)
            except:
                pass
    
    return (processed, skipped, failed)


def main():
    parser = argparse.ArgumentParser(description='PicBed Markdown 圖片同步腳本')
    parser.add_argument('--dry-run', action='store_true', help='預覽模式，不實際修改')
    parser.add_argument('--force', action='store_true', help='強制重新處理所有檔案')
    parser.add_argument('--status', action='store_true', help='檢查所有 PicBed repo 的容量狀態')
    args = parser.parse_args()
    
    print("=" * 50)
    print("PicBed Markdown 圖片同步腳本")
    print("=" * 50)
    
    # 載入設定
    config = load_config()
    picbed_repos = get_picbed_repos(config)
    current_index = int(config.get('CURRENT_REPO_INDEX', '0'))
    branch = config.get('PICBED_BRANCH', 'main')
    enable_backup = config.get('ENABLE_BACKUP', 'false').lower() in ('true', '1', 'yes')
    
    if current_index >= len(picbed_repos):
        print(f"錯誤：CURRENT_REPO_INDEX ({current_index}) 超出範圍")
        exit(1)
    
    current_repo = picbed_repos[current_index]
    
    # 初始化 GitHub 客戶端
    github = GitHubClient(config['GITHUB_TOKEN'])
    
    # 如果是 --status 模式
    if args.status:
        check_repo_status(github, picbed_repos, current_index)
        return
    
    # 顯示當前設定
    print(f"當前 PicBed Repo: {current_repo}")
    print(f"Branch: {branch}")
    print(f"備份功能: {'啟用' if enable_backup else '關閉'}")
    if args.dry_run:
        print("模式: 預覽模式（不實際修改）")
    if args.force:
        print("模式: 強制重新處理")
    print("-" * 50)
    
    # 檢查當前 repo 容量
    size_kb = github.get_repo_size(current_repo)
    if size_kb:
        size_bytes = size_kb * 1024
        size_mb = size_kb / 1024
        print(f"當前 Repo 容量: {size_mb:.1f} MB")
        
        if size_bytes >= CRITICAL_SIZE:
            print("!! 警告：當前 repo 已超過 1GB !!")
            print(f"請修改 {ENV_FILE} 中的 CURRENT_REPO_INDEX 切換到下一個 repo")
            if not args.dry_run:
                response = input("是否繼續執行？(y/N): ")
                if response.lower() != 'y':
                    print("已取消")
                    return
        elif size_bytes >= WARNING_SIZE:
            print("提示：當前 repo 接近容量限制")
    
    print("-" * 50)
    
    # 載入處理記錄
    processed_data = load_processed_data()
    
    # 取得要掃描的目錄
    folders = get_folders(config)
    
    # 統計
    total_files = 0
    total_processed = 0
    total_skipped = 0
    total_failed = 0
    
    # 掃描並處理
    for folder in folders:
        print(f"\n掃描目錄: {folder}")
        
        folder_path = Path(folder)
        md_files = list(folder_path.rglob('*.md'))
        
        print(f"找到 {len(md_files)} 個 .md 檔案")
        
        for md_file in md_files:
            md_path = str(md_file)
            
            # 計算檔案 hash
            file_hash = calculate_file_hash(md_path)
            
            # 檢查是否需要處理
            if not args.force:
                file_record = processed_data.get('files', {}).get(md_path)
                if file_record and file_record.get('hash') == file_hash:
                    print(f"\n[跳過] {md_file.name} (未變更)")
                    continue
            
            print(f"\n[處理] {md_file.name}")
            total_files += 1
            
            processed, skipped, failed = process_markdown_file(
                filepath=md_path,
                github=github,
                repo=current_repo,
                branch=branch,
                picbed_repos=picbed_repos,
                processed_data=processed_data,
                dry_run=args.dry_run,
                enable_backup=enable_backup
            )
            
            total_processed += processed
            total_skipped += skipped
            total_failed += failed
            
            # 更新檔案記錄
            if not args.dry_run and (processed > 0 or skipped > 0):
                processed_data.setdefault('files', {})[md_path] = {
                    'hash': file_hash,
                    'last_processed': datetime.now().isoformat()
                }
                save_processed_data(processed_data)
    
    # 顯示結果
    print("\n" + "=" * 50)
    print("處理完成")
    print("=" * 50)
    print(f"處理檔案數: {total_files}")
    print(f"上傳圖片數: {total_processed}")
    print(f"跳過圖片數: {total_skipped}")
    print(f"失敗圖片數: {total_failed}")
    
    if args.dry_run:
        print("\n（預覽模式，未實際執行）")


if __name__ == '__main__':
    main()
