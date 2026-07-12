"""
文件解析器模块。

支持格式：
  - PDF → Markdown（通过 PyMuPDF/Docling）
  - DOCX/DOC → Markdown（通过 python-docx，保留标题/表格/列表/代码块）
  - XLSX/XLS → Markdown 表格
  - CSV → Markdown 表格
  - JSON → 格式化输出
  - HTML/HTM → Markdown（通过 BeautifulSoup + html2text，保留标题/表格/代码块/链接）
"""

import csv
import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """文件解析器基类，兼容 LangChain DocumentLoader 接口。"""

    @abstractmethod
    def parse(self, file_path: Path) -> str:
        """解析文件，返回纯文本内容。"""
        ...

    def parse_from_bytes(self, data: bytes) -> str:
        """从字节流解析文件（默认实现：写入临时文件后调用 parse）。

        子类可覆盖此方法以提供更高效的内存解析。
        """
        suffix = getattr(self, "_default_suffix", ".bin")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(data)
            tmp.close()
            return self.parse(Path(tmp.name))
        finally:
            try:
                os.unlink(tmp.name)
            except OSError as e:
                logger.debug("临时文件清理失败: %s", e)

    def parse_from_string(self, raw: str) -> str:
        """从字符串解析（默认实现：转字节后调用 parse_from_bytes）。"""
        return self.parse_from_bytes(raw.encode("utf-8"))


class XlsxParser(BaseParser):
    """使用 openpyxl 解析 XLSX/XLS 文件，转为 Markdown 表格。"""

    _default_suffix = ".xlsx"

    def parse(self, file_path: Path) -> str:
        from openpyxl import load_workbook

        wb = load_workbook(str(file_path), read_only=True, data_only=True)
        parts: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"## {sheet_name}\n")

            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            # 第一行作为表头
            header = [str(c) if c is not None else "" for c in rows[0]]
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")

            for row in rows[1:]:
                cells = [str(c).replace("\n", " ") if c is not None else "" for c in row]
                # 补齐列数
                while len(cells) < len(header):
                    cells.append("")
                parts.append("| " + " | ".join(cells[:len(header)]) + " |")

            parts.append("")  # 空行分隔

        wb.close()
        return "\n".join(parts)

    def parse_from_bytes(self, data: bytes) -> str:
        """直接从字节解析 XLSX，避免临时文件。"""
        from openpyxl import load_workbook
        import io

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"## {sheet_name}\n")

            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            header = [str(c) if c is not None else "" for c in rows[0]]
            parts.append("| " + " | ".join(header) + " |")
            parts.append("| " + " | ".join("---" for _ in header) + " |")

            for row in rows[1:]:
                cells = [str(c).replace("\n", " ") if c is not None else "" for c in row]
                while len(cells) < len(header):
                    cells.append("")
                parts.append("| " + " | ".join(cells[:len(header)]) + " |")

            parts.append("")

        wb.close()
        return "\n".join(parts)


# ExcelParser 作为 XlsxParser 的别名（兼容 common.py/kb_routes.py 调用）
ExcelParser = XlsxParser


class DocxParser(BaseParser):
    """使用 python-docx 解析 DOCX 文件，保留结构转为 Markdown。

    保留的元素：
    - 标题层级（Heading 1-6 → # ~ ######）
    - 段落正文（Normal → 纯文本）
    - 表格（→ Markdown 表格 | a | b |）
    - 列表（→ - item 或 1. item，通过样式名识别）
    - 代码块（→ ```wrapped```，通过等宽字体识别）
    - 图片（记录占位符 [图片]，不提取图像内容）

    对于 .doc（旧二进制格式），python-docx 不支持，回退到 docling。
    """

    _default_suffix = ".docx"

    def parse(self, file_path: Path) -> str:
        try:
            from docx import Document as DocxDocument
        except ImportError:
            logger.warning("python-docx 未安装，DOCX 解析不可用")
            return ""

        try:
            doc = DocxDocument(str(file_path))
        except Exception as e:
            logger.warning(f"DOCX 解析失败 {file_path.name}: {e}")
            # 旧 .doc 格式 python-docx 不支持，回退到 docling
            return self._fallback_docling(file_path)

        return self._docx_to_markdown(doc)

    def parse_from_bytes(self, data: bytes) -> str:
        """直接从字节解析 DOCX，避免临时文件。

        Falls back to docling via a temp file when python-docx fails (e.g.
        legacy OLE2 .doc format). Without this fallback, .doc uploads
        silently return empty text.
        """
        try:
            from docx import Document as DocxDocument
        except ImportError:
            logger.warning("python-docx 未安装，DOCX 解析不可用")
            return ""

        import io as _io
        try:
            doc = DocxDocument(_io.BytesIO(data))
            return self._docx_to_markdown(doc)
        except Exception as e:
            logger.warning(
                f"DOCX 字节流解析失败（可能是 .doc 旧格式）: {e}；回退到 docling"
            )
            # _fallback_docling only accepts a file_path, so write a temp file.
            import tempfile
            tmp = Path(tempfile.mktemp(suffix=".doc"))
            try:
                tmp.write_bytes(data)
                return self._fallback_docling(tmp)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"temp file cleanup failed in docx parse fallback: {e}")

    def _docx_to_markdown(self, doc) -> str:
        """将 python-docx Document 转为 Markdown 字符串。

        按 body 元素顺序遍历（段落和表格交错出现），保留文档原始结构。
        OCR text from embedded images is appended at the document end with
        ``[OCR from image N]`` markers when OCR_ENABLED=True.
        """
        from docx.oxml.ns import qn

        parts: list[str] = []

        # 遍历 body 的所有子元素（段落 w:p 和表格 w:tbl 按文档顺序）
        body = doc.element.body
        for child in body.iterchildren():
            tag = child.tag
            if tag == qn("w:p"):
                # 段落
                para = None
                for p in doc.paragraphs:
                    if p._element is child:
                        para = p
                        break
                if para is None:
                    continue
                md_line = self._para_to_md(para)
                if md_line:
                    parts.append(md_line)
            elif tag == qn("w:tbl"):
                # 表格
                table = None
                for t in doc.tables:
                    if t._element is child:
                        table = t
                        break
                if table is None:
                    continue
                md_table = self._table_to_md(table)
                if md_table:
                    parts.append(md_table)
                    parts.append("")  # 表格后空行

        text = self._cleanup_list_spacing("\n\n".join(parts))
        # OCR embedded images and append to document end
        ocr_text = self._extract_docx_images_ocr(doc)
        if ocr_text:
            text += "\n\n" + ocr_text
        return text

    def _extract_docx_images_ocr(self, doc) -> str:
        """OCR all images embedded in a DOCX, return joined text with markers.

        Iterates over the document's image relationships (covers inline and
        floating shapes). Each image with non-empty OCR text is appended as
        ``[OCR from image N]\\n<text>`` so downstream consumers can locate
        the source. Returns empty string when OCR is disabled.
        """
        from src.config.settings import settings
        if not getattr(settings, "ocr_enabled", False):
            return ""
        try:
            ocr_parts: list[str] = []
            img_idx = 0
            for rel in doc.part.rels.values():
                if "image" not in rel.reltype:
                    continue
                try:
                    image_bytes = rel.target_part.blob
                    ocr_text = PaddleOcrParser.extract_text_from_image(image_bytes)
                    if ocr_text.strip():
                        img_idx += 1
                        ocr_parts.append(f"[OCR from image {img_idx}]\n{ocr_text}")
                except Exception:
                    logger.exception("DOCX image OCR failed")
            return "\n\n".join(ocr_parts)
        except Exception:
            logger.exception("DOCX image extraction failed")
            return ""

    def _cleanup_list_spacing(self, text: str) -> str:
        """Remove blank lines between consecutive list items.

        python-docx renders each list item as a separate paragraph, so
        _docx_to_markdown joins them with \\n\\n. This collapses consecutive
        list items (lines starting with - or 1.) into a single block
        separated by single \\n, matching markdown convention.
        """
        lines = text.split("\n")
        result: list[str] = []
        prev_was_list = False
        for line in lines:
            stripped = line.strip()
            is_list = stripped.startswith("- ") or stripped.startswith("1. ")
            if is_list and prev_was_list and stripped == "":
                # Skip blank line between list items
                continue
            result.append(line)
            prev_was_list = is_list
        return "\n".join(result)

    def _para_to_md(self, para) -> str:
        """将段落转为 Markdown 行。"""
        text = para.text.strip()
        if not text:
            return ""

        style_name = (para.style.name or "").lower() if para.style else ""

        # 标题层级
        if style_name.startswith("heading"):
            level = 1
            try:
                level = int(style_name.replace("heading", "").strip())
            except (ValueError, TypeError):
                level = 1
            level = max(1, min(6, level))
            return f"{'#' * level} {text}"

        # 列表（通过样式名识别）
        if "list" in style_name or style_name.startswith("list"):
            # 判断有序/无序：Word 的 List Paragraph 样式不区分，
            # 需要通过 numPr 判断。简化处理：检查是否有 numbering。
            from docx.oxml.ns import qn as _qn
            pPr = para._element.find(_qn("w:pPr"))
            if pPr is not None:
                numPr = pPr.find(_qn("w:numPr"))
                if numPr is not None:
                    ilvl = numPr.find(_qn("w:ilvl"))
                    indent = int(ilvl.get(_qn("w:val"))) if ilvl is not None else 0
                    return f"{'  ' * indent}1. {text}"
            return f"- {text}"

        # 代码块：通过等宽字体识别（Consolas/Courier New/Source Code Pro 等）
        mono_fonts = {"consolas", "courier new", "courier", "monospace",
                      "source code pro", "menlo", "monaco", "jetbrains mono"}
        is_code = False
        for run in para.runs:
            font_name = (run.font.name or "").lower()
            if font_name in mono_fonts:
                is_code = True
                break
        if is_code:
            return f"```\n{text}\n```"

        # 普通段落
        return text

    def _table_to_md(self, table) -> str:
        """将 Word 表格转为 Markdown 表格。"""
        rows = []
        for row in table.rows:
            cells = []
            for cell in row.cells:
                cell_text = cell.text.strip().replace("\n", " ").replace("|", "\\|")
                cells.append(cell_text)
            rows.append(cells)

        if not rows:
            return ""

        # 第一行作为表头
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |"]
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in rows[1:]:
            # 补齐列数
            while len(row) < len(header):
                row.append("")
            lines.append("| " + " | ".join(row[:len(header)]) + " |")

        return "\n".join(lines)

    def _fallback_docling(self, file_path: Path) -> str:
        """旧 .doc 格式回退到 docling 解析器。"""
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(str(file_path))
            return result.document.export_to_markdown()
        except Exception as e:
            logger.warning(f"docling 回退解析 .doc 失败: {e}")
            return ""


class CsvParser(BaseParser):
    """使用 csv 标准库解析 CSV 文件，转为 Markdown 表格。"""

    _default_suffix = ".csv"

    def parse(self, file_path: Path) -> str:
        import io

        try:
            import chardet
            raw = file_path.read_bytes()
            detected = chardet.detect(raw)
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            text = raw.decode(encoding, errors="replace")
        except Exception:
            logger.warning("编码检测失败，使用 UTF-8 回退: %s", file_path.name)
            text = file_path.read_text(encoding="utf-8", errors="replace")

        return self._parse_text(text)

    def parse_from_bytes(self, data: bytes) -> str:
        """从字节解析 CSV，自动检测编码。"""
        try:
            import chardet
            detected = chardet.detect(data)
            encoding = detected.get("encoding", "utf-8") or "utf-8"
            text = data.decode(encoding, errors="replace")
        except Exception:
            text = data.decode("utf-8", errors="replace")
        return self._parse_text(text)

    def parse_from_string(self, raw: str) -> str:
        """从字符串解析 CSV。"""
        return self._parse_text(raw)

    def _parse_text(self, text: str) -> str:
        import io

        # 自动检测分隔符
        sniffer = csv.Sniffer()
        try:
            dialect = sniffer.sniff(text[:4096])
        except csv.Error:
            dialect = csv.excel

        reader = csv.reader(io.StringIO(text), dialect)
        rows = list(reader)

        if not rows:
            return ""

        parts: list[str] = []
        header = rows[0]
        parts.append("| " + " | ".join(header) + " |")
        parts.append("| " + " | ".join("---" for _ in header) + " |")

        for row in rows[1:]:
            cells = [c.replace("\n", " ") for c in row]
            while len(cells) < len(header):
                cells.append("")
            parts.append("| " + " | ".join(cells[:len(header)]) + " |")

        return "\n".join(parts)


class JsonParser(BaseParser):
    """解析 JSON 文件，格式化输出。"""

    _default_suffix = ".json"

    def parse(self, file_path: Path) -> str:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        return self._format_json(raw)

    def parse_from_string(self, raw: str) -> str:
        """从字符串解析 JSON（用于 chat 附件）。"""
        return self._format_json(raw)

    def parse_from_bytes(self, data: bytes) -> str:
        """从字节解析 JSON。"""
        return self._format_json(data.decode("utf-8", errors="replace"))

    def _format_json(self, raw: str) -> str:
        """格式化 JSON 字符串为可读文本。"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return f"JSON 解析失败: {e}"

        # 如果是列表，逐项格式化
        if isinstance(data, list):
            parts: list[str] = []
            for i, item in enumerate(data):
                parts.append(f"### 条目 {i + 1}\n")
                parts.append(self._format_value(item))
                parts.append("")
            return "\n".join(parts)

        # 如果是字典，按键值格式化
        return self._format_value(data)

    def _format_value(self, data, indent: int = 0) -> str:
        """递归格式化 JSON 值。"""
        prefix = "  " * indent
        if isinstance(data, dict):
            if not data:
                return f"{prefix}(空对象)"
            lines = []
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{prefix}- **{key}**:")
                    lines.append(self._format_value(value, indent + 1))
                else:
                    lines.append(f"{prefix}- **{key}**: {value}")
            return "\n".join(lines)
        elif isinstance(data, list):
            if not data:
                return f"{prefix}(空列表)"
            lines = []
            for item in data:
                if isinstance(item, (dict, list)):
                    lines.append(self._format_value(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {item}")
            return "\n".join(lines)
        else:
            return f"{prefix}{data}"


class HtmlParser(BaseParser):
    """HTML 文档解析器。转 Markdown 保留结构（标题/表格/代码块/链接）。

    解析流程：
    1. BeautifulSoup（lxml 解析器）解析 HTML，自动处理编码检测
    2. 过滤 script/style/nav/footer/header/aside 等非正文标签（decompose）
    3. 将 <pre> 块转换为 ``` 围栏代码块（在 html2text 之前处理，
       因为 html2text 默认会把 <pre> 内容缩进 4 空格，下游 markdown
       分块器不会识别为代码块）
    4. html2text 把剩余 HTML 转 markdown（body_width=0 不自动换行，
       保留链接/图片/强调/表格）
    """

    _default_suffix = ".html"

    # Boilerplate tags stripped entirely (content + tag removed).
    _DROP_TAGS = ("script", "style", "nav", "footer", "header", "aside")

    def parse(self, file_path: Path) -> str:
        # Pass raw bytes to BeautifulSoup so it can detect encoding from
        # <meta charset> tags; this handles GBK/Big5/UTF-8 etc. transparently.
        return self._parse_html(file_path.read_bytes(), is_bytes=True)

    def parse_from_bytes(self, data: bytes) -> str:
        return self._parse_html(data, is_bytes=True)

    def parse_from_string(self, raw: str) -> str:
        return self._parse_html(raw, is_bytes=False)

    def _parse_html(self, content, is_bytes: bool) -> str:
        try:
            from bs4 import BeautifulSoup, NavigableString
        except ImportError:
            logger.warning("beautifulsoup4 未安装，HTML 解析不可用")
            return ""

        # BeautifulSoup auto-detects encoding from <meta> when given bytes;
        # for string input the caller has already decoded.
        soup = BeautifulSoup(content, "lxml")

        # Strip boilerplate navigation/scripts (decompose removes content too)
        for tag_name in self._DROP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Convert <pre> blocks to fenced code blocks BEFORE html2text runs,
        # so the ``` markers survive the markdown conversion intact. Without
        # this, html2text indents <pre> content 4 spaces, which downstream
        # markdown chunkers do not treat as a code block.
        for pre in soup.find_all("pre"):
            code_text = pre.get_text()
            pre.replace_with(NavigableString(f"\n```\n{code_text}\n```\n"))

        try:
            import html2text
        except ImportError:
            logger.warning("html2text 未安装，HTML 解析不可用")
            return ""

        converter = html2text.HTML2Text()
        converter.body_width = 0           # Don't wrap lines (preserve table structure)
        converter.ignore_links = False     # Keep [text](url)
        converter.ignore_images = False
        converter.ignore_emphasis = False  # Keep **bold** / *italic*
        converter.protect_links = True     # Don't wrap link URLs across lines

        markdown = converter.handle(str(soup))
        return markdown.strip()


class PaddleOcrParser:
    """PaddleOCR wrapper for extracting text from images embedded in PDF/DOCX.

    Lazy-loads paddleocr to avoid ~10s startup cost when OCR_ENABLED=False.
    Single PaddleOCR instance shared across calls (model load is expensive).

    All public methods are no-ops returning ``""`` when ``settings.ocr_enabled``
    is False, so callers can invoke them unconditionally without gating on the
    flag — this keeps PdfParser/DocxParser code paths uniform.
    """

    _ocr_instance = None  # singleton PaddleOCR instance, lazy-initialized

    @classmethod
    def _get_ocr(cls):
        """Lazy-init PaddleOCR instance. Only called when OCR_ENABLED=True."""
        if cls._ocr_instance is None:
            from paddleocr import PaddleOCR
            from src.config.settings import settings
            lang = getattr(settings, "ocr_lang", "ch") or "ch"
            cls._ocr_instance = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        return cls._ocr_instance

    @classmethod
    def extract_text_from_image(cls, image_bytes: bytes) -> str:
        """OCR a single image (bytes), return extracted text joined by newlines.

        Returns empty string if OCR disabled or any error occurs (the error
        is logged via ``logger.exception`` for diagnosability).
        """
        from src.config.settings import settings
        if not getattr(settings, "ocr_enabled", False):
            return ""
        try:
            import io
            from PIL import Image
            ocr = cls._get_ocr()
            img = Image.open(io.BytesIO(image_bytes))
            result = ocr.ocr(img, cls=True)
            lines: list[str] = []
            if result and result[0]:
                for line in result[0]:
                    if line and len(line) >= 2:
                        # PaddleOCR returns [bbox, (text, confidence)]
                        text = line[1][0] if line[1] else ""
                        if text:
                            lines.append(text)
            return "\n".join(lines)
        except Exception:
            logger.exception("OCR extract failed")
            return ""

    @classmethod
    def extract_text_from_pdf_page(cls, page) -> str:
        """Extract text from a PyMuPDF page by rendering it to image then OCR.

        Used for scanned PDFs where ``page.get_text()`` returns empty. Renders
        at 300 DPI for accurate OCR. Returns empty string when OCR disabled.
        """
        from src.config.settings import settings
        if not getattr(settings, "ocr_enabled", False):
            return ""
        try:
            import fitz  # PyMuPDF
            # Render page to image at 300 DPI for accurate OCR
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat)
            image_bytes = pix.tobytes("png")
            return cls.extract_text_from_image(image_bytes)
        except Exception:
            logger.exception("PDF page OCR failed")
            return ""
