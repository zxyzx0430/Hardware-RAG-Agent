"""附件文本提取 — 从 chat 附件的 data URL 中解析文本内容。

支持 PDF/XLSX/CSV/JSON/HTML/文本类文件，统一使用与 KB 上传一致的解析器
（UnifiedPdfParser）以确保行为一致性。
"""

import asyncio
import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def extract_attachment_text(name: str, mime_type: str, data_url: str) -> str:
    """从 chat 附件的 data URL 中提取文本内容。"""
    ext = Path(name).suffix.lower()
    is_base64 = data_url.startswith("data:")
    payload = data_url.split(",", 1)[-1] if is_base64 else data_url

    # PDF — use UnifiedPdfParser for consistent behavior with KB upload
    if ext == ".pdf":
        try:
            raw = base64.b64decode(payload) if is_base64 else data_url.encode("utf-8")
            from src.rag.document_processor import UnifiedPdfParser
            parser = UnifiedPdfParser()
            return await asyncio.to_thread(parser.parse_from_bytes, raw)
        except Exception as e:
            logger.warning(f"PDF 附件解析失败: {e}")
            return ""

    # XLSX / XLS
    if ext in (".xlsx", ".xls"):
        try:
            raw = base64.b64decode(payload) if is_base64 else data_url.encode("utf-8")
            from src.rag.file_parsers import ExcelParser
            return ExcelParser().parse_from_bytes(raw)
        except Exception as e:
            logger.warning(f"Excel 附件解析失败: {e}")
            return ""

    # CSV
    if ext == ".csv":
        try:
            raw = base64.b64decode(payload).decode("utf-8") if is_base64 else payload
            from src.rag.file_parsers import CsvParser
            return CsvParser().parse_from_string(raw)
        except Exception as e:
            logger.warning(f"CSV 附件解析失败: {e}")
            return ""

    # JSON
    if ext == ".json":
        try:
            raw = base64.b64decode(payload).decode("utf-8") if is_base64 else payload
            from src.rag.file_parsers import JsonParser
            return JsonParser().parse_from_string(raw)
        except Exception as e:
            logger.warning(f"JSON 附件解析失败: {e}")
            return ""

    # HTML / HTM — use HtmlParser to convert to markdown (not plain text)
    if ext in (".html", ".htm"):
        try:
            raw = base64.b64decode(payload) if is_base64 else data_url.encode("utf-8")
            from src.rag.file_parsers import HtmlParser
            return HtmlParser().parse_from_bytes(raw)
        except Exception as e:
            logger.warning(f"HTML 附件解析失败: {e}")
            return ""

    # 文本类
    if ext in (".md", ".txt", ".py", ".c", ".h", ".ino") or mime_type.startswith("text/"):
        try:
            return base64.b64decode(payload).decode("utf-8", errors="replace") if is_base64 else payload
        except Exception:
            return ""

    return ""
