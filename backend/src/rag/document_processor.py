"""
硬件文档处理器。

两步流程：
  1. Docling 解析 PDF → 保留表格/标题/代码块结构的 Markdown
  2. LLM 翻译为中文 Markdown（统一格式：概述/引脚/电气特性/通信接口/踩坑）
"""

import logging
import re
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from docling.document_converter import DocumentConverter

from src.rag.document_loader import DocumentSource, MARKDOWN_DIR
from src.llm.client import LLMClient, LLMResponse


# ─── 翻译提示词模板 ───
TRANSLATION_SYSTEM_PROMPT = """你是一个硬件文档翻译专家。你的任务：

1. 将英文硬件 Datasheet 翻译为自然流畅的中文
2. **保留技术术语原文**（GPIO、I2C、SPI、UART、PWM、ADC、DAC、MCU 等）
3. **保持 Markdown 表格格式完整**，行列对齐
4. **保留代码块**及其语言标注
5. 输出格式必须严格遵循以下 Markdown 模板：

```markdown
---
title: "芯片/模块中文名"
category: dev-boards | sensors | protocols | peripherals
source_url: "原文 URL"
last_updated: "YYYY-MM-DD"
tags: [tag1, tag2]
---

## 概述
（简短介绍芯片/模块是什么，主要用途）

## 引脚定义
（引脚表格，包含引脚号/名称/功能/备注）

## 电气特性
（工作电压/电流/功耗/IO 电平）

## 通信接口
（支持的通信协议和时序说明）

## 踩坑记录
（常见问题和注意事项）
```

翻译原则：
- 专业术语首次出现时，在中文后加英文括号标注，如"通用输入输出（GPIO）"
- 数值和单位之间加空格，如"3.3 V"、"40 mA"
- 表格内容保持对齐，不要用合并单元格
- 不要遗漏任何技术参数
- 不要捏造不存在的信息"""


@dataclass
class ProcessedDocument:
    """一篇处理完成的文档。"""

    doc_id: str
    source: DocumentSource
    pdf_path: Path
    raw_markdown: str  # Docling 原始解析 Markdown
    translated_markdown: str  # LLM 翻译后中文 Markdown
    metadata: dict = field(default_factory=dict)


logger = logging.getLogger(__name__)


class DoclingParser:
    """使用 Docling 解析 PDF 为 Markdown。"""

    def __init__(self):
        self.converter = DocumentConverter()

    def parse(self, pdf_path: Path) -> str:
        """
        解析 PDF 为 Markdown，保留表格/标题/代码块。
        返回 Markdown 字符串。

        解析失败时抛出 RuntimeError，避免静默返回空字符串。
        """
        logger.info("Docling 解析: %s", pdf_path.name)
        try:
            result = self.converter.convert(str(pdf_path))
            md_content = result.document.export_to_markdown()
        except Exception as e:
            logger.warning("Docling 解析失败 %s: %s", pdf_path.name, e)
            raise RuntimeError(f"PDF 解析失败: {e}") from e

        if not md_content or not md_content.strip():
            logger.warning("Docling 解析结果为空: %s", pdf_path.name)
            raise RuntimeError("PDF 解析失败：解析结果为空")

        logger.info("解析完成: %d 字符", len(md_content))
        return md_content


class UnifiedPdfParser:
    """Unified PDF parser — PyMuPDF per-page extraction with page markers.

    This replaces both DoclingParser (KB upload) and PdfParser (chat attachments)
    to ensure consistent behavior and accurate page tracking.

    Output format: text with ``<!-- PAGE:N -->`` markers before each page's
    content, enabling all chunkers to extract accurate page ranges without
    relying on chars_per_page estimation.

    Fallback chain:
      1. PyMuPDF per-page (primary — accurate page numbers)
      2. Docling full-document (fallback — preserves tables but no page markers)
      3. PyMuPDF whole-document (last resort)
    """

    def __init__(self, prefer_docling: bool = False):
        """Initialize the unified parser.

        Args:
            prefer_docling: If True, try Docling first (preserves table structure
                as Markdown) then fall back to PyMuPDF. Note: Docling output
                has no page markers, so page tracking will be less accurate.
                If False (default), use PyMuPDF per-page with markers.
        """
        self.prefer_docling = prefer_docling

    def parse(self, pdf_path: Path) -> tuple[str, int]:
        """Parse PDF and return (text_with_page_markers, total_pages).

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            Tuple of (text, total_pages). Text contains <!-- PAGE:N --> markers
            when PyMuPDF per-page extraction is used.

        Raises:
            RuntimeError: If all parsing methods fail.
        """
        if self.prefer_docling:
            # Try Docling first for table structure
            try:
                docling_text = DoclingParser().parse(pdf_path)
                total_pages = self._get_page_count(pdf_path)
                logger.info(
                    "UnifiedPdfParser: Docling success (%d chars, %d pages, no page markers)",
                    len(docling_text), total_pages,
                )
                return docling_text, total_pages
            except Exception as e:
                logger.warning("UnifiedPdfParser: Docling failed (%s), falling back to PyMuPDF", e)

        # Primary: PyMuPDF per-page with markers
        try:
            text, total_pages = self._parse_pymupdf_per_page(pdf_path)
            logger.info(
                "UnifiedPdfParser: PyMuPDF per-page success (%d chars, %d pages, with markers)",
                len(text), total_pages,
            )
            return text, total_pages
        except Exception as e:
            logger.warning(
                "UnifiedPdfParser: PyMuPDF per-page failed (%s), trying Docling", e
            )

        # Fallback 1: Docling
        try:
            docling_text = DoclingParser().parse(pdf_path)
            total_pages = self._get_page_count(pdf_path)
            logger.info(
                "UnifiedPdfParser: Docling fallback success (%d chars, %d pages)",
                len(docling_text), total_pages,
            )
            return docling_text, total_pages
        except Exception as e:
            logger.warning(
                "UnifiedPdfParser: Docling also failed (%s), trying PyMuPDF whole-doc", e
            )

        # Fallback 2: PyMuPDF whole-document (no per-page markers)
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            parts = [doc[i].get_text("text") for i in range(doc.page_count)]
            total_pages = doc.page_count
            doc.close()
            text = "\n\n".join(parts)
            logger.info(
                "UnifiedPdfParser: PyMuPDF whole-doc fallback (%d chars, %d pages)",
                len(text), total_pages,
            )
            return text, total_pages
        except Exception as e:
            raise RuntimeError(f"所有 PDF 解析方式均失败: {e}") from e

    def parse_from_bytes(self, data: bytes) -> str:
        """Parse PDF from bytes (for chat attachments). Returns text only.

        Inserts page markers when possible.
        """
        import fitz
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            page_texts = []
            for i in range(doc.page_count):
                page_texts.append(doc[i].get_text("text"))
            total_pages = doc.page_count
            doc.close()

            from src.rag.chunking.base import build_text_with_page_markers
            text = build_text_with_page_markers(page_texts)
            logger.info(
                "UnifiedPdfParser: bytes parse success (%d chars, %d pages, with markers)",
                len(text), total_pages,
            )
            return text
        except Exception as e:
            logger.warning("UnifiedPdfParser: bytes parse failed (%s), returning empty", e)
            return ""

    def _parse_pymupdf_per_page(self, pdf_path: Path) -> tuple[str, int]:
        """Extract text per page with PyMuPDF and insert page markers."""
        import fitz
        doc = fitz.open(str(pdf_path))
        page_texts: list[str] = []
        for i in range(doc.page_count):
            page_texts.append(doc[i].get_text("text"))
        total_pages = doc.page_count
        doc.close()

        if not any(t.strip() for t in page_texts):
            raise RuntimeError("PyMuPDF extracted empty text (possibly scanned PDF)")

        from src.rag.chunking.base import build_text_with_page_markers
        text = build_text_with_page_markers(page_texts)
        return text, total_pages

    def _get_page_count(self, pdf_path: Path) -> int:
        """Get PDF page count using PyMuPDF."""
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            count = doc.page_count
            doc.close()
            return count
        except Exception:
            return 0


class TranslationPipeline:
    """LLM 翻译管线，带错误重试。"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        chunk_size: int = 6000,  # 每块最大字符数（以防超长文档）
        max_retries: int = 2,
    ):
        self.llm_client = llm_client or LLMClient()
        self.chunk_size = chunk_size
        self.max_retries = max_retries

    def _split_markdown(self, md: str) -> list[str]:
        """超长文档分块处理（按一级标题切分）。"""
        if len(md) <= self.chunk_size:
            return [md]

        # 按 ## 一级标题切分
        sections = re.split(r"(?=^## )", md, flags=re.MULTILINE)
        chunks = []
        current = ""
        for section in sections:
            if len(current) + len(section) > self.chunk_size and current:
                chunks.append(current.strip())
                current = section
            else:
                current += "\n\n" + section if current else section
        if current:
            chunks.append(current.strip())
        return chunks

    async def translate(
        self, raw_md: str, source: DocumentSource
    ) -> str:
        """
        将 Docling 解析的原始 Markdown 翻译为中文硬件文档格式。
        返回翻译后的 Markdown 字符串。
        """
        chunks = self._split_markdown(raw_md)

        if len(chunks) == 1:
            return await self._translate_chunk(chunks[0], source, is_full=True)

        # 多块翻译：给每块添加上下文
        translated_chunks = []
        for i, chunk in enumerate(chunks):
            context_hint = f"\n\n[这是第 {i+1}/{len(chunks)} 部分]"
            result = await self._translate_chunk(
                chunk + context_hint, source, is_full=(i == 0)
            )
            translated_chunks.append(result)

        return "\n\n---\n\n".join(translated_chunks)

    async def _translate_chunk(
        self, md_content: str, source: DocumentSource, is_full: bool = False
    ) -> str:
        """翻译单块内容。"""
        # 构建用户消息
        categories_info = {
            "dev-boards": "开发板/微控制器",
            "sensors": "传感器",
            "protocols": "通信协议",
            "peripherals": "外设模块",
        }
        category_cn = categories_info.get(source.category, source.category)

        user_message = f"""请将以下硬件 Datasheet 翻译为中文硬件文档。

文档信息：
- 标题：{source.title}
- 分类：{source.category}（{category_cn}）
- 来源 URL：{source.url}
- 标签：{', '.join(source.tags)}

{"请输出完整文档（含 YAML frontmatter）。" if is_full else "请翻译以下内容片段，保持 Markdown 格式，保留技术术语原文。"}

原文内容：
```markdown
{md_content}
```"""

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response: LLMResponse = await self.llm_client.chat(
                    user_message=user_message,
                    system_prompt=TRANSLATION_SYSTEM_PROMPT,
                    timeout=120.0,
                )
                if response.content and not response.content.startswith("⚠️"):
                    return response.content
                last_error = response.content
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                import asyncio
                wait = 2 ** attempt
                logger.warning("翻译重试 (%d/%d)，等待 %ds...", attempt + 1, self.max_retries, wait)
                await asyncio.sleep(wait)

        raise RuntimeError(f"翻译失败（已重试 {self.max_retries} 次）: {last_error}")


class DocumentProcessor:
    """文档处理器：Docling 解析 + LLM 翻译 完整流程。"""

    def __init__(
        self,
        parser: Optional[DoclingParser] = None,
        translator: Optional[TranslationPipeline] = None,
        output_dir: Optional[Path] = None,
    ):
        self.parser = parser or DoclingParser()
        self.translator = translator or TranslationPipeline()
        self.output_dir = output_dir or MARKDOWN_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _markdown_path(self, doc_id: str) -> Path:
        return self.output_dir / f"{doc_id}.md"

    def is_processed(self, doc_id: str) -> bool:
        """检查是否已处理（翻译后的 Markdown 是否存在）。"""
        return self._markdown_path(doc_id).exists()

    async def process_one(
        self, source: DocumentSource, pdf_path: Path, force: bool = False
    ) -> ProcessedDocument:
        """处理单篇文档：解析 PDF → 翻译为中文 Markdown。"""
        md_path = self._markdown_path(source.doc_id)

        # 跳过已处理的
        if md_path.exists() and not force:
            logger.info("已处理: %s", source.doc_id)
            translated = md_path.read_text(encoding="utf-8")
        else:
            # Step 1: Docling 解析
            raw_md = self.parser.parse(pdf_path)

            # Step 2: LLM 翻译
            logger.info("LLM 翻译中: %s", source.doc_id)
            translated = await self.translator.translate(raw_md, source)

            # 写入文件
            md_path.write_text(translated, encoding="utf-8")
            logger.info("已保存: %s", md_path)

        metadata = {
            "doc_id": source.doc_id,
            "title": source.title,
            "category": source.category,
            "source_url": source.url,
            "tags": source.tags,
            "last_updated": source.last_updated,
            "processed_at": datetime.now().isoformat(),
        }

        return ProcessedDocument(
            doc_id=source.doc_id,
            source=source,
            pdf_path=pdf_path,
            raw_markdown="",  # 不保留原始 MD 以节省内存
            translated_markdown=translated,
            metadata=metadata,
        )

    async def process_batch(
        self,
        pdf_paths: dict[str, Path],
        sources: list[DocumentSource],
        force: bool = False,
    ) -> list[ProcessedDocument]:
        """批量处理文档。"""
        results = []
        for source in sources:
            pdf_path = pdf_paths.get(source.doc_id)
            if not pdf_path:
                logger.warning("跳过 %s：PDF 不存在", source.doc_id)
                continue
            doc = await self.process_one(source, pdf_path, force=force)
            results.append(doc)
        return results
