#!/usr/bin/env python3
"""
Download prebuilt hardware manual knowledge base index package.

Downloads a zip from GitHub Release and extracts it to backend/data/.
The zip is expected to contain: chroma_db/ + bm25/ + uploads/ + builtin_kb/.

Usage:
    python scripts/download_builtin_kb.py

The prebuilt index has not been published yet. Pass its release URL when available:
    python scripts/download_builtin_kb.py https://example.com/builtin_kb.zip
"""

import sys
import zipfile
import urllib.request
from pathlib import Path

TARGET_DIR = Path(__file__).resolve().parent.parent / "backend" / "data"

EXISTS_MSG = "内置知识库已存在，跳过下载。如需重新下载请先删除 backend/data/builtin_kb/ 目录。"
SUCCESS_MSG = (
    "内置知识库下载完成！已解压到 backend/data/。"
    "包含 chroma_db（向量索引）+ bm25（关键词索引）"
    "+ uploads（源文件）+ builtin_kb（内置KB元数据）。"
)


def builtin_kb_exists(target: Path) -> bool:
    """Check if builtin_kb directory already exists."""
    return (target / "builtin_kb").exists()


def report_progress(block_num: int, block_size: int, total_size: int) -> None:
    """Print download progress to stdout (reporthook for urlretrieve)."""
    downloaded = block_num * block_size
    if total_size > 0:
        percent = min(downloaded / total_size * 100, 100)
        print(f"\r下载进度: {percent:.1f}%", end="", flush=True)


def download_zip(url: str, dest: Path) -> None:
    """Download zip file with progress display; exit on network failure."""
    try:
        urllib.request.urlretrieve(url, dest, reporthook=report_progress)
        print()  # newline after progress line
    except Exception as e:
        print(f"\n下载失败: {e}")
        sys.exit(1)


def extract_zip(zip_path: Path, target: Path) -> None:
    """Extract zip archive to target directory; exit on extraction failure."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target)
    except Exception as e:
        print(f"解压失败: {e}")
        sys.exit(1)


def cleanup_temp(zip_path: Path) -> None:
    """Remove the temporary zip file after extraction."""
    if zip_path.exists():
        zip_path.unlink()


def run_steps(temp_zip: Path, download_url: str) -> None:
    """Run download, extract, cleanup steps in sequence."""
    print(f"开始下载内置知识库: {download_url}")
    download_zip(download_url, temp_zip)
    extract_zip(temp_zip, TARGET_DIR)
    cleanup_temp(temp_zip)


def main() -> None:
    """Entry point: download and extract builtin knowledge base."""
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print("预构建知识库尚未发布。发布后请传入可信的 HTTPS 下载地址；目前可在界面上传自己的 PDF。")
        sys.exit(1)
    if builtin_kb_exists(TARGET_DIR):
        print(EXISTS_MSG)
        return
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    run_steps(TARGET_DIR / "builtin_kb.zip", sys.argv[1])
    print(SUCCESS_MSG)


if __name__ == "__main__":
    main()
