"""
GitHub API Folder Sync

將多個本地 folder 透過 GitHub API 同步到指定的 GitHub repository。
設定檔：.env_git_sync
"""

import os
import sys
import argparse
import base64
import hashlib
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from github import Github, GithubException
from loguru import logger

# 設定 logger
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def setup_logger(log_level: str = "INFO"):
    """設定 logger，支援 console 和 file 輸出"""
    logger.remove()
    
    # Console 輸出
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
        level=log_level
    )
    
    # File 輸出 - 依日期分檔
    log_file = LOG_DIR / f"git_sync_{datetime.now().strftime('%Y-%m-%d')}.log"
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        level=log_level,
        rotation="00:00",  # 每天午夜輪換
        retention="30 days",  # 保留 30 天
        encoding="utf-8"
    )
    
    logger.info(f"Log 檔案: {log_file}")


# 預設初始化（會在 main 中根據參數重新設定）
setup_logger("INFO")


def load_config(env_file: str = ".env_git_sync") -> dict:
    """載入 .env_git_sync 設定檔"""
    env_path = Path(env_file)
    if not env_path.exists():
        logger.error(f"設定檔 {env_file} 不存在")
        sys.exit(1)
    
    load_dotenv(env_path)
    
    config = {
        "github_token": os.getenv("GITHUB_TOKEN"),
        "github_repo": os.getenv("GITHUB_REPO"),
        "github_branch": os.getenv("GITHUB_BRANCH", "main"),
        "source_folders": [],
        "exclude_folders": [],
    }
    
    # 解析排除的 folder 名稱 (EXCLUDE_FOLDER_000 到 EXCLUDE_FOLDER_999)
    for i in range(1000):
        exclude_key = f"EXCLUDE_FOLDER_{i:03d}"
        exclude_name = os.getenv(exclude_key)
        if exclude_name and exclude_name.strip():
            config["exclude_folders"].append(exclude_name.strip())
    
    if config["exclude_folders"]:
        logger.info(f"排除 folders: {config['exclude_folders']}")
    
    # 解析 source folders (SOURCE_FOLDER_000 到 SOURCE_FOLDER_999)
    for i in range(1000):
        folder_key = f"SOURCE_FOLDER_{i:03d}"
        folder_path = os.getenv(folder_key)
        if folder_path and folder_path.strip():
            config["source_folders"].append(Path(folder_path.strip()))
            logger.debug(f"載入 {folder_key}: {folder_path}")
    
    # 驗證必要設定
    if not config["github_token"]:
        logger.error("GITHUB_TOKEN 未設定")
        sys.exit(1)
    
    if not config["github_repo"]:
        logger.error("GITHUB_REPO 未設定")
        sys.exit(1)
    
    if not config["source_folders"]:
        logger.error("SOURCE_FOLDER_XXX 未設定或為空 (需要至少一個 SOURCE_FOLDER_000)")
        sys.exit(1)
    
    return config


def validate_folders(folders: list[Path]) -> list[Path]:
    """驗證所有 source folder 存在"""
    valid_folders = []
    for folder in folders:
        if folder.exists() and folder.is_dir():
            valid_folders.append(folder)
            logger.info(f"✓ 找到 folder: {folder}")
        else:
            logger.warning(f"✗ Folder 不存在或不是目錄: {folder}")
    
    if not valid_folders:
        logger.error("沒有有效的 source folder")
        sys.exit(1)
    
    return valid_folders


def scan_folder(folder: Path, exclude_folders: list[str] = None) -> list[tuple[Path, str]]:
    """
    遞迴掃描 folder 中的所有檔案
    
    Args:
        folder: 要掃描的 folder
        exclude_folders: 要排除的 folder 名稱列表
    
    Returns:
        list of (本地完整路徑, 相對於 folder 的路徑)
    """
    if exclude_folders is None:
        exclude_folders = []
    
    files = []
    folder_name = folder.name
    
    for file_path in folder.rglob("*"):
        if file_path.is_file():
            # 檢查是否在排除的 folder 中
            relative_path = file_path.relative_to(folder)
            path_parts = relative_path.parts
            
            # 檢查路徑中的每一層是否在排除清單中
            should_exclude = False
            for part in path_parts[:-1]:  # 不檢查檔案名稱本身
                if part in exclude_folders:
                    should_exclude = True
                    break
            
            if should_exclude:
                continue
            
            github_path = f"{folder_name}/{relative_path.as_posix()}"
            files.append((file_path, github_path))
    
    return files


def calculate_git_blob_sha(content: bytes) -> str:
    """
    計算 Git blob SHA (與 GitHub 使用的相同算法)
    
    Git blob SHA = sha1("blob " + file_size + "\0" + content)
    """
    size = len(content)
    header = f"blob {size}\0".encode("utf-8")
    return hashlib.sha1(header + content).hexdigest()


def normalize_line_endings(content: bytes) -> bytes:
    """
    將換行符號標準化為 LF (與 GitHub 一致)
    Windows CRLF (\r\n) -> LF (\n)
    """
    return content.replace(b"\r\n", b"\n")


def get_file_content(file_path: Path) -> tuple[bytes, str, bool]:
    """
    讀取檔案內容
    
    Returns:
        (raw content bytes, git blob sha, is_binary)
    """
    # 一律以二進位模式讀取
    with open(file_path, "rb") as f:
        content = f.read()
    
    # 檢查是否為二進位檔案
    try:
        content.decode("utf-8")
        is_binary = False
        # 文字檔案：標準化換行符號後計算 SHA（與 GitHub 一致）
        normalized_content = normalize_line_endings(content)
        git_sha = calculate_git_blob_sha(normalized_content)
    except UnicodeDecodeError:
        is_binary = True
        # 二進位檔案：直接計算 SHA
        normalized_content = content
        git_sha = calculate_git_blob_sha(content)
    
    return normalized_content, git_sha, is_binary


def get_remote_file_shas(repo, branch: str) -> dict[str, str]:
    """
    一次取得 repo 中所有檔案的 SHA
    
    Returns:
        {檔案路徑: SHA} 的 dict
    """
    logger.info("取得 GitHub 上的檔案清單...")
    try:
        # 取得整個 tree（recursive=True 會取得所有子目錄）
        tree = repo.get_git_tree(branch, recursive=True)
        file_shas = {}
        for item in tree.tree:
            if item.type == "blob":  # 只取檔案，不取目錄
                file_shas[item.path] = item.sha
        logger.info(f"GitHub 上共有 {len(file_shas)} 個檔案")
        return file_shas
    except GithubException as e:
        logger.warning(f"無法取得 tree: {e}")
        return {}


def sync_file_to_github(repo, branch: str, local_path: Path, github_path: str, 
                        commit_message: str, remote_shas: dict[str, str],
                        dry_run: bool = False) -> str:
    """
    將單一檔案同步到 GitHub
    
    Returns:
        "created" - 新建檔案
        "updated" - 更新檔案
        "skipped" - 檔案未變更，跳過
        "failed" - 上傳失敗
    """
    try:
        content, local_sha, is_binary = get_file_content(local_path)
        
        # 檢查檔案是否已存在於 GitHub（從預先取得的 SHA dict 查詢）
        remote_sha = remote_shas.get(github_path)
        
        if remote_sha:
            # 比較 SHA，如果相同則跳過
            logger.debug(f"SHA 比較 {github_path}: local={local_sha}, remote={remote_sha}")
            if local_sha == remote_sha:
                logger.debug(f"⊘ 跳過 (未變更): {github_path}")
                return "skipped"
            
            # SHA 不同時，下載 remote 內容直接比較（處理 SHA 計算差異的問題）
            try:
                existing_file = repo.get_contents(github_path, ref=branch)
                remote_content = existing_file.decoded_content
                # 標準化 remote 內容的換行符號
                if not is_binary:
                    remote_content = normalize_line_endings(remote_content)
                
                if content == remote_content:
                    logger.debug(f"⊘ 跳過 (內容相同): {github_path}")
                    return "skipped"
                
                # 內容確實不同，更新檔案
                if dry_run:
                    file_type = "binary" if is_binary else "text"
                    logger.info(f"[DRY RUN] 會更新: {github_path} ({file_type})")
                    return "updated"
                
                repo.update_file(
                    path=github_path,
                    message=commit_message,
                    content=content,
                    sha=existing_file.sha,
                    branch=branch
                )
                logger.info(f"✓ 更新: {github_path}")
                return "updated"
                
            except GithubException as e:
                raise
        else:
            # 檔案不存在，建立它
            if dry_run:
                file_type = "binary" if is_binary else "text"
                logger.info(f"[DRY RUN] 會建立: {github_path} ({file_type})")
                return "created"
            
            repo.create_file(
                path=github_path,
                message=commit_message,
                content=content,
                branch=branch
            )
            logger.info(f"✓ 建立: {github_path}")
            return "created"
        
    except Exception as e:
        logger.error(f"✗ 上傳失敗 {github_path}: {e}")
        return "failed"


def sync_folder(repo, branch: str, folder: Path, commit_message: str, 
                remote_shas: dict[str, str], dry_run: bool = False, 
                exclude_folders: list[str] = None) -> dict:
    """
    同步單一 folder 到 GitHub
    
    Returns:
        {"created": int, "updated": int, "skipped": int, "failed": int}
    """
    files = scan_folder(folder, exclude_folders)
    logger.info(f"在 {folder.name} 中找到 {len(files)} 個檔案")
    
    stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    
    for local_path, github_path in files:
        result = sync_file_to_github(repo, branch, local_path, github_path, 
                                     commit_message, remote_shas, dry_run)
        stats[result] += 1
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="將多個本地 folder 同步到 GitHub repository"
    )
    parser.add_argument(
        "--env", 
        default=".env_git_sync",
        help="設定檔路徑 (預設: .env_git_sync)"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="只顯示會做什麼，不實際上傳"
    )
    parser.add_argument(
        "--folder",
        help="只同步指定的 folder (可用 folder 名稱或 index)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log 層級 (預設: INFO)"
    )
    
    args = parser.parse_args()
    
    # 根據參數重新設定 logger
    setup_logger(args.log_level)
    
    # 載入設定
    logger.info(f"載入設定檔: {args.env}")
    config = load_config(args.env)
    
    logger.info(f"GitHub Repo: {config['github_repo']}")
    logger.info(f"Branch: {config['github_branch']}")
    
    # 驗證 folders
    valid_folders = validate_folders(config["source_folders"])
    
    # 如果指定了特定 folder，只同步該 folder
    if args.folder:
        target_folders = []
        for i, folder in enumerate(valid_folders):
            if args.folder == folder.name or args.folder == str(i):
                target_folders.append(folder)
                break
        
        if not target_folders:
            logger.error(f"找不到指定的 folder: {args.folder}")
            logger.info("可用的 folders:")
            for i, folder in enumerate(valid_folders):
                logger.info(f"  [{i}] {folder.name}")
            sys.exit(1)
        
        valid_folders = target_folders
    
    # 連接 GitHub
    logger.info("連接 GitHub...")
    try:
        g = Github(config["github_token"])
        repo = g.get_repo(config["github_repo"])
        logger.info(f"✓ 成功連接到 repo: {repo.full_name}")
    except GithubException as e:
        logger.error(f"無法連接 GitHub: {e}")
        sys.exit(1)
    
    # 產生 commit message
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_message = f"Auto sync at {timestamp}"
    
    if args.dry_run:
        logger.info("=== DRY RUN 模式 ===")
    
    # 預先取得 GitHub 上所有檔案的 SHA（減少 API 請求）
    remote_shas = get_remote_file_shas(repo, config["github_branch"])
    
    # 同步所有 folders
    total_stats = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    
    for folder in valid_folders:
        logger.info(f"\n--- 同步 {folder.name} ---")
        stats = sync_folder(
            repo, 
            config["github_branch"], 
            folder, 
            commit_message,
            remote_shas,
            args.dry_run,
            config["exclude_folders"]
        )
        for key in total_stats:
            total_stats[key] += stats[key]
    
    # 顯示結果
    logger.info(f"\n=== 同步完成 ===")
    logger.info(f"新建: {total_stats['created']} 個檔案")
    logger.info(f"更新: {total_stats['updated']} 個檔案")
    logger.info(f"跳過 (未變更): {total_stats['skipped']} 個檔案")
    if total_stats["failed"] > 0:
        logger.warning(f"失敗: {total_stats['failed']} 個檔案")
    
    if args.dry_run:
        logger.info("（DRY RUN 模式，未實際上傳）")


if __name__ == "__main__":
    main()

