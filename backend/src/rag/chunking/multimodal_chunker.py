"""Multimodal chunker — vision-LLM-driven PDF chunking with high precision.

Uses page images instead of text to identify section boundaries, enabling:
1. Accurate page numbers (each image = one page)
2. Table structure recognition (LLM sees actual table layout)
3. Cross-page section detection (LLM sees page breaks)
4. Better boundary detection for scanned/image-heavy PDFs

Falls back to AgentChunker for non-PDF files or if vision is unavailable.
"""

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.rag.chunking.base import (
    ChunkResult,
    BaseChunker,
    compute_fingerprint,
    verify_page_coverage,
    strip_page_markers,
)
from src.rag.chunking.agent_chunker import AgentChunkError

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Bilingual prompt — asks the vision LLM to identify section boundaries
# ═══════════════════════════════════════════════════════════════
# The LLM sees actual page images, so it can detect tables, code blocks,
# and cross-page sections that text-based analysis would miss. Page
# numbers are exact because each image corresponds to one PDF page.
_MULTIMODAL_ANALYSIS_PROMPT = """\
你是一个文档结构分析专家。请分析以下 PDF 页面图片，识别逻辑章节边界。
You are a document structure analyst. Analyze these PDF page images and \
identify logical section boundaries.

分析规则 / Analysis Rules:
1. 每个 section 应该是一个完整的语义单元 / Each section should be a complete \
semantic unit.
2. 表格应与它们的标题保持在同一 section / Tables should stay with their \
titles in the same section.
3. 代码块应作为独立 section（如果超过 500 字符）/ Code blocks exceeding \
500 chars should be their own section.
4. 注意跨页的 section（同一章节可能跨越多页）/ Handle cross-page sections \
(a single section may span multiple pages).
5. 使用每张图片前标注的实际页码 / Use the actual page numbers labeled \
before each image.

本批次页面 / Pages in this batch: {page_list}

输出 JSON / Output JSON:
{{"sections": [{{"title": "...", "start_page": N, "end_page": N, "summary": "...", "keywords": [...], "has_code_block": false, "has_table": false, "confidence": 0.9}}]}}

字段说明 / Field descriptions:
- title: 简洁的章节标题（用文档语言）/ concise section title (in the document's language)
- start_page: 起始页码（实际页码）/ starting page number (actual page number)
- end_page: 结束页码（实际页码）/ ending page number (actual page number)
- summary: 一句话概括本节内容 / one-sentence summary of the section
- keywords: 2-5 个关键词，用于检索 / 2-5 keywords for retrieval
- has_code_block: 本节是否包含代码块 / whether this section contains code blocks
- has_table: 本节是否包含表格 / whether this section contains tables
- confidence: 0.0-1.0，你对这个边界的置信度 / your confidence in this boundary (0.0-1.0)

只输出 JSON 对象，不要输出其他文字。Output ONLY the JSON object, no additional text."""


# Separators for sub-splitting — same as HybridChunker / AgentChunker.
# "\n```\n" is absent because code blocks are protected with placeholders.
_SUB_SPLIT_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", "。", ".", " ", ""]

# Pattern for inline fenced code blocks (placeholder protection)
_INLINE_CODE_RE = re.compile(r"```[\s\S]*?```")


# ═══════════════════════════════════════════════════════════════
# MultimodalTrace — transparency data structure
# ═══════════════════════════════════════════════════════════════

@dataclass
class MultimodalTrace:
    """Summary trace of the multimodal chunking process, stored in chunk
    metadata under the "agent_trace" key for API compatibility."""

    method: str = "multimodal"
    model: str = ""
    num_pages: int = 0
    num_batches: int = 0
    sections_found: int = 0
    total_elapsed_seconds: float = 0.0
    token_usage: dict = field(default_factory=dict)  # prompt_tokens, completion_tokens, total

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# MultimodalChunker
# ═══════════════════════════════════════════════════════════════

class MultimodalChunker(BaseChunker):
    """Vision-LLM-driven PDF chunker.

    Renders each PDF page as an image, sends batches of page images to a
    vision-capable LLM (OpenAI-compatible API), and uses the LLM's
    section-boundary analysis to build chunks. Chunk content is extracted
    from the actual page text via PyMuPDF, so embeddings are computed over
    real text — not OCR or LLM-generated summaries.

    Advantages over text-based chunkers:
    - Exact page numbers (each image = one page, no estimation).
    - Table and code-block detection via visual layout.
    - Cross-page section awareness (LLM sees page breaks).

    Falls back with a clear error if the model does not support vision.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        max_retries: int = 3,
        batch_size: int = 5,  # pages per LLM call
        dpi: int = 200,
        sub_chunk_size: int = 1000,
        sub_chunk_overlap: int = 200,
        temperature: float = 0.1,
        max_chunks: int = 500,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.max_retries = max_retries
        self.batch_size = batch_size
        self.dpi = dpi
        self.sub_chunk_size = sub_chunk_size
        self.sub_chunk_overlap = sub_chunk_overlap
        self.temperature = temperature
        self.max_chunks = max_chunks

        # Sub-splitter with code-block-safe separators
        self._sub_splitter = RecursiveCharacterTextSplitter(
            chunk_size=sub_chunk_size,
            chunk_overlap=sub_chunk_overlap,
            separators=_SUB_SPLIT_SEPARATORS,
            length_function=len,
        )

    # ─── Main pipeline ───────────────────────────────────────────

    async def chunk(
        self,
        text: str,
        metadata: dict,
        file_path: Optional[Path] = None,
        total_pages: int = 0,
    ) -> list[ChunkResult]:
        """Execute multimodal chunking pipeline.

        Steps: render pages → batch analyze → build chunks → sub-split →
        merge tiny. Raises AgentChunkError if file_path is missing/not a
        PDF, if no API key is configured, or if the model lacks vision.
        """
        # ── Validate inputs ──
        if not self.api_key:
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                "Multimodal chunker requires an API key. Configure it in RAG settings or KB settings.",
            )

        if file_path is None or not file_path.exists():
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                f"Multimodal chunker requires an existing PDF file_path, got: {file_path}",
            )

        if file_path.suffix.lower() != ".pdf":
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                f"Multimodal chunker only supports PDF files, got: {file_path.suffix or 'no extension'}. "
                "Use 'agent' or 'hybrid' chunking for non-PDF files.",
            )

        pipeline_start = time.time()
        logger.info(
            f"[MultimodalChunker] Pipeline start: file={file_path.name}, "
            f"model={self.model}, dpi={self.dpi}, batch_size={self.batch_size}"
        )

        # ── Step 1: Render pages as images + extract text ──
        logger.info(f"[MultimodalChunker] Step 1: Rendering PDF pages")
        render_start = time.time()
        page_data = self._render_pages(file_path)
        render_elapsed = time.time() - render_start
        num_pages = len(page_data)

        if num_pages == 0:
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                f"PDF has 0 pages or failed to render: {file_path}",
            )

        total_image_bytes = sum(len(p["image_b64"]) for p in page_data)
        logger.info(
            f"[MultimodalChunker] Rendered {num_pages} pages in {render_elapsed:.1f}s "
            f"(total image data ~{total_image_bytes // 1024} KB base64)"
        )

        # ── Step 2: Create batches with 1-page overlap and analyze each ──
        # Overlap ensures cross-batch sections are seen by both adjacent
        # batches, so _merge_cross_batch_sections can stitch them back
        # together instead of splitting one logical section into two chunks.
        batches = self._create_batches_with_overlap(page_data)
        logger.info(
            f"[MultimodalChunker] Step 2: Batch analysis "
            f"({num_pages} pages, {self.batch_size}/batch with 1-page overlap = "
            f"{len(batches)} batches)"
        )

        all_sections: list[dict] = []
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for batch_idx, batch in enumerate(batches):
            batch_start = time.time()
            page_nums = [p["page_num"] for p in batch]
            logger.info(
                f"[MultimodalChunker]   Batch {batch_idx + 1}/{len(batches)}: "
                f"pages {page_nums[0]}-{page_nums[-1]} ({len(batch)} pages)"
            )

            try:
                sections, batch_tokens = await self._analyze_batch(batch)
            except AgentChunkError:
                raise
            except Exception as e:
                raise AgentChunkError(
                    "AGENT_CHUNK_FAILED",
                    f"Batch {batch_idx + 1} analysis failed after retries: {e}",
                ) from e

            batch_elapsed = time.time() - batch_start
            all_sections.extend(sections)

            # Accumulate token usage
            for k in token_usage:
                token_usage[k] += batch_tokens.get(k, 0)

            logger.info(
                f"[MultimodalChunker]   Batch {batch_idx + 1} done: "
                f"{len(sections)} sections in {batch_elapsed:.1f}s, "
                f"tokens={batch_tokens}"
            )
            for s in sections:
                logger.info(
                    f"[MultimodalChunker]     → {s.get('title', '?')} "
                    f"(pages {s.get('start_page', '?')}-{s.get('end_page', '?')}, "
                    f"conf={s.get('confidence', '?')}, "
                    f"code={s.get('has_code_block', '?')}, "
                    f"table={s.get('has_table', '?')})"
                )

        # ── Step 2b: Merge sections split across batch boundaries ──
        # Adjacent batches share 1 overlap page. If a section appears in
        # both (similar title + overlapping page range), merge them into a
        # single section to avoid splitting one logical section into two.
        raw_count = len(all_sections)
        all_sections = self._merge_cross_batch_sections(all_sections)
        if len(all_sections) != raw_count:
            logger.info(
                f"[MultimodalChunker] Cross-batch merge: "
                f"{raw_count} → {len(all_sections)} sections"
            )

        logger.info(
            f"[MultimodalChunker] Batch analysis complete: "
            f"{len(all_sections)} sections total, tokens={token_usage}"
        )

        # Fallback: if LLM returned no sections, treat the whole doc as one section
        if not all_sections:
            logger.warning(
                "[MultimodalChunker] No sections returned by LLM, "
                "falling back to single section covering all pages"
            )
            all_sections = [{
                "title": metadata.get("title", "Full Document"),
                "start_page": 1,
                "end_page": num_pages,
                "summary": "",
                "keywords": [],
                "has_code_block": False,
                "has_table": False,
                "confidence": 0.3,
            }]

        # ── Step 3: Build chunks from sections + page text ──
        logger.info(
            f"[MultimodalChunker] Step 3: Building chunks from {len(all_sections)} sections"
        )
        chunks = self._build_chunks(all_sections, page_data, metadata)
        logger.info(
            f"[MultimodalChunker] Built {len(chunks)} chunks "
            f"(before tiny-merge)"
        )

        # ── Step 4: Merge tiny chunks ──
        chunks = self._merge_tiny_chunks(chunks)
        logger.info(
            f"[MultimodalChunker] After tiny-merge: {len(chunks)} chunks"
        )

        # ── Step 4b: max_chunks guard ──
        if len(chunks) > self.max_chunks:
            logger.warning(
                f"[MultimodalChunker] Chunk count {len(chunks)} exceeds max_chunks="
                f"{self.max_chunks}. Consider increasing sub_chunk_size or using a "
                f"larger batch_size. Truncation is NOT applied — all chunks are kept."
            )

        # ── Step 5: Verify page coverage ──
        if num_pages > 0:
            coverage = verify_page_coverage(chunks, num_pages)
            if coverage["missing_pages"]:
                logger.warning(
                    f"[MultimodalChunker] Page coverage gaps: {coverage['missing_pages']}"
                )
            if coverage["duplicate_pages"]:
                logger.info(
                    f"[MultimodalChunker] Overlapping pages (expected for cross-page sections): "
                    f"{coverage['duplicate_pages']}"
                )

        # ── Build trace and inject into every chunk ──
        total_elapsed = time.time() - pipeline_start
        trace = MultimodalTrace(
            method="multimodal",
            model=self.model,
            num_pages=num_pages,
            num_batches=len(batches),
            sections_found=len(all_sections),
            total_elapsed_seconds=total_elapsed,
            token_usage=token_usage,
        )
        trace_dict = trace.to_dict()
        for chunk in chunks:
            chunk.metadata["agent_trace"] = trace_dict

        logger.info(
            f"[MultimodalChunker] Pipeline complete: {len(chunks)} chunks "
            f"from {num_pages} pages in {total_elapsed:.1f}s "
            f"({len(batches)} batches, {len(all_sections)} sections, tokens={token_usage})"
        )

        return chunks

    # ─── Page rendering ──────────────────────────────────────────

    def _render_pages(self, file_path: Path) -> list[dict]:
        """Render each PDF page as a PNG image (base64) and extract text.

        Returns:
            List of {"page_num": int, "image_b64": str, "text": str},
            one entry per page (1-based page_num).
        """
        import fitz  # PyMuPDF

        doc = fitz.open(str(file_path))
        # zoom factor: 72 is PDF default DPI
        zoom = self.dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        pages: list[dict] = []
        try:
            for i in range(doc.page_count):
                page = doc[i]
                # Render page as pixmap → PNG bytes → base64
                pix = page.get_pixmap(matrix=mat)
                image_bytes = pix.tobytes("png")
                image_b64 = base64.b64encode(image_bytes).decode("ascii")
                # Extract text for chunk content
                page_text = page.get_text("text") or ""
                pages.append({
                    "page_num": i + 1,
                    "image_b64": image_b64,
                    "text": page_text,
                })
        finally:
            doc.close()

        return pages

    # ─── Batch analysis (vision LLM) ────────────────────────────

    async def _analyze_batch(self, batch: list[dict]) -> tuple[list[dict], dict]:
        """Send a batch of page images to the vision LLM and parse sections.

        Args:
            batch: List of page dicts (page_num, image_b64, text).

        Returns:
            Tuple of (sections list, token_usage dict).

        Raises:
            AgentChunkError: If the model does not support vision, or if all
                retries are exhausted.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

        page_nums = [p["page_num"] for p in batch]
        page_list_str = ", ".join(str(n) for n in page_nums)
        prompt_text = _MULTIMODAL_ANALYSIS_PROMPT.format(page_list=page_list_str)

        # Build multimodal content: prompt text + interleaved page labels/images
        content: list[dict] = [{"type": "text", "text": prompt_text}]
        for page in batch:
            content.append({
                "type": "text",
                "text": f"--- Page {page['page_num']} ---",
            })
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{page['image_b64']}"
                },
            })

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                call_start = time.time()
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": content}],
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                )
                elapsed = time.time() - call_start

                raw_content = response.choices[0].message.content or ""
                logger.info(
                    f"[MultimodalChunker]     LLM response: {len(raw_content)} chars "
                    f"in {elapsed:.1f}s (attempt {attempt + 1})"
                )

                parsed = json.loads(raw_content)
                sections = parsed.get("sections", [])
                if not isinstance(sections, list):
                    sections = []

                # Extract token usage
                usage = response.usage
                token_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
                }

                return sections, token_usage

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    f"[MultimodalChunker]     JSON decode error (attempt {attempt + 1}): {e}"
                )
            except Exception as e:
                last_error = e
                # Detect vision-unsupported errors and fail fast
                if self._is_vision_error(e):
                    raise AgentChunkError(
                        "VISION_NOT_SUPPORTED",
                        f"Model '{self.model}' does not support vision/image input: {e}. "
                        "Use 'agent' or 'hybrid' chunking instead.",
                    ) from e
                logger.warning(
                    f"[MultimodalChunker]     LLM call failed (attempt {attempt + 1}): {e}"
                )

            # Exponential backoff before next retry
            if attempt < self.max_retries - 1:
                wait = 2 ** attempt
                logger.info(
                    f"[MultimodalChunker]     Retrying in {wait}s ..."
                )
                await asyncio.sleep(wait)

        raise AgentChunkError(
            "AGENT_CHUNK_FAILED",
            f"Batch analysis failed after {self.max_retries} retries: {last_error}",
        )

    # ─── Build chunks ────────────────────────────────────────────

    def _build_chunks(
        self,
        sections: list[dict],
        page_data: list[dict],
        metadata: dict,
    ) -> list[ChunkResult]:
        """Build ChunkResult list from LLM-identified sections.

        For each section, concatenate the actual page text (from PyMuPDF)
        from start_page to end_page. Sub-split with code-block-safe
        RecursiveCharacterTextSplitter. Strip page markers from final text.
        """
        # Build page_num → text lookup
        page_text_map = {p["page_num"]: p["text"] for p in page_data}
        max_page = max(page_text_map.keys()) if page_text_map else 1

        chunks: list[ChunkResult] = []

        for i, section in enumerate(sections):
            start_page = int(section.get("start_page", 1))
            end_page = int(section.get("end_page", start_page))

            # Clamp to valid range
            start_page = max(1, min(start_page, max_page))
            end_page = max(start_page, min(end_page, max_page))

            title = section.get("title", f"Section {i + 1}") or f"Section {i + 1}"
            summary = section.get("summary", "")
            keywords = section.get("keywords", [])
            has_code = bool(section.get("has_code_block", False))
            has_table = bool(section.get("has_table", False))
            confidence = section.get("confidence", 0.5)

            # Concatenate actual page text for this section
            page_texts = [
                page_text_map.get(p, "")
                for p in range(start_page, end_page + 1)
            ]
            section_text = "\n\n".join(t for t in page_texts if t)
            # Strip page markers (safety — fitz text has none, but keep consistent)
            section_text = strip_page_markers(section_text).strip()

            if not section_text:
                logger.warning(
                    f"[MultimodalChunker::Build] Section {i + 1} '{title}': "
                    f"empty text for pages {start_page}-{end_page}, skipping"
                )
                continue

            logger.info(
                f"[MultimodalChunker::Build] Section {i + 1}/{len(sections)}: "
                f"'{title}' ({len(section_text)} chars, pages {start_page}-{end_page}, "
                f"code={has_code}, table={has_table}, conf={confidence})"
            )

            # Check if the entire section is a single code block
            is_whole_code_block = (
                section_text.startswith("```") and section_text.endswith("```")
            )

            if is_whole_code_block:
                logger.info(
                    f"[MultimodalChunker::Build]   → Whole code block detected "
                    f"({len(section_text)} chars), keeping as single chunk (no sub-split)"
                )
                chunk_meta = {
                    **metadata,
                    "chunk_index": len(chunks),
                    "section_title": title,
                    "section_summary": summary,
                    "section_keywords": keywords,
                    "section_confidence": confidence,
                    "has_code_block": True,
                    "has_table": has_table,
                    "is_code_block": True,
                    "chunk_size": len(section_text),
                    "chunk_method": "multimodal",
                }
                chunks.append(ChunkResult(
                    text=section_text,
                    metadata=chunk_meta,
                    page_range=(start_page, end_page),
                    fingerprint=compute_fingerprint(section_text),
                    chunk_method="multimodal",
                    section_title=title,
                ))
                continue

            # Protect inline code blocks with placeholders before sub-splitting
            code_map: dict[str, str] = {}

            def _stash_code(m: re.Match) -> str:
                key = f"\x00CB{len(code_map)}\x00"
                code_map[key] = m.group(0)
                return key

            placeholder_text = _INLINE_CODE_RE.sub(_stash_code, section_text)
            if code_map:
                logger.info(
                    f"[MultimodalChunker::Build]   → Protected {len(code_map)} inline "
                    f"code block(s) with placeholders before sub-splitting"
                )

            sub_chunks = self._sub_splitter.split_text(placeholder_text)
            logger.info(
                f"[MultimodalChunker::Build]   → Sub-split into {len(sub_chunks)} chunks "
                f"(target size={self.sub_chunk_size}, overlap={self.sub_chunk_overlap})"
            )

            for sub_text in sub_chunks:
                if not sub_text.strip():
                    continue
                # Restore code blocks
                for ph, code in code_map.items():
                    if ph in sub_text:
                        sub_text = sub_text.replace(ph, code)

                chunk_meta = {
                    **metadata,
                    "chunk_index": len(chunks),
                    "section_title": title,
                    "section_summary": summary,
                    "section_keywords": keywords,
                    "section_confidence": confidence,
                    "has_code_block": has_code,
                    "has_table": has_table,
                    "is_code_block": False,
                    "chunk_size": len(sub_text),
                    "chunk_method": "multimodal",
                }
                chunks.append(ChunkResult(
                    text=sub_text,
                    metadata=chunk_meta,
                    page_range=(start_page, end_page),
                    fingerprint=compute_fingerprint(sub_text),
                    chunk_method="multimodal",
                    section_title=title,
                ))

        # Renumber chunk_index
        for i, chunk in enumerate(chunks):
            chunk.metadata["chunk_index"] = i

        return chunks

    # ─── Tiny chunk absorption ───────────────────────────────────

    def _merge_tiny_chunks(
        self, chunks: list[ChunkResult], threshold: int = 100
    ) -> list[ChunkResult]:
        """Merge non-code chunks shorter than threshold into adjacent chunks
        from the same section. Code blocks are always preserved as-is."""
        if len(chunks) <= 1:
            return chunks

        merge_count = 0
        merged: list[ChunkResult] = []
        for chunk in chunks:
            is_code = chunk.metadata.get("is_code_block", False)
            is_tiny = len(chunk.text) < threshold

            if is_code or not is_tiny:
                merged.append(chunk)
                continue

            if merged:
                prev = merged[-1]
                same_section = prev.section_title == chunk.section_title
                prev_is_code = prev.metadata.get("is_code_block", False)
                if same_section and not prev_is_code:
                    logger.info(
                        f"[MultimodalChunker::Merge] Absorbed tiny chunk "
                        f"({len(chunk.text)} chars < {threshold} threshold) "
                        f"into previous chunk in section '{chunk.section_title}' "
                        f"(prev {len(prev.text)} → {len(prev.text) + 1 + len(chunk.text)} chars)"
                    )
                    new_text = prev.text + "\n" + chunk.text
                    new_meta = {**prev.metadata}
                    new_meta["chunk_size"] = len(new_text)
                    merged[-1] = ChunkResult(
                        text=new_text,
                        metadata=new_meta,
                        page_range=prev.page_range,
                        fingerprint=compute_fingerprint(new_text),
                        chunk_method=prev.chunk_method,
                        section_title=prev.section_title,
                    )
                    merge_count += 1
                    continue
            merged.append(chunk)

        # Renumber chunk_index
        for i, chunk in enumerate(merged):
            chunk.metadata["chunk_index"] = i

        if merge_count > 0:
            logger.info(
                f"[MultimodalChunker::Merge] Absorbed {merge_count} tiny chunk(s): "
                f"{len(chunks)} → {len(merged)} chunks"
            )
        return merged

    # ─── Batch overlap + cross-batch section merging ────────────

    def _create_batches_with_overlap(self, page_data: list[dict]) -> list[list[dict]]:
        """Create batches with a 1-page overlap for cross-batch continuity.

        With batch_size=5 and overlap=1, a 12-page document yields:
          Batch 1: pages 1-5
          Batch 2: pages 5-9   (page 5 shared with batch 1)
          Batch 3: pages 9-12  (page 9 shared with batch 2)

        The overlap lets the vision LLM see the boundary context on both
        sides, so _merge_cross_batch_sections can stitch a section that
        spans the boundary back into a single section.
        """
        n = len(page_data)
        if n <= self.batch_size:
            return [page_data]

        overlap = 1
        step = self.batch_size - overlap  # advance by (batch_size - 1) each round
        batches: list[list[dict]] = []
        i = 0
        while i < n:
            batch = page_data[i : i + self.batch_size]
            batches.append(batch)
            if i + self.batch_size >= n:
                break
            i += step
        return batches

    def _merge_cross_batch_sections(self, sections: list[dict]) -> list[dict]:
        """Merge sections that were split across batch boundaries.

        Due to 1-page overlap, a section spanning a boundary appears in
        two adjacent batches. If two sections share overlapping page
        ranges AND have similar titles, they are merged into one section
        covering the combined page range. This prevents a single logical
        section from becoming two chunks.
        """
        if len(sections) <= 1:
            return sections

        # Sort by start_page, then end_page for determinism
        sorted_sections = sorted(
            sections,
            key=lambda s: (int(s.get("start_page", 0)), int(s.get("end_page", 0))),
        )

        merged: list[dict] = [dict(sorted_sections[0])]
        merge_count = 0
        for section in sorted_sections[1:]:
            prev = merged[-1]
            prev_end = int(prev.get("end_page", 0))
            curr_start = int(section.get("start_page", 0))
            curr_end = int(section.get("end_page", curr_start))

            # Sections overlap (or touch) at a batch boundary?
            # curr_start <= prev_end means they share at least the overlap page.
            if curr_start <= prev_end:
                if self._titles_similar(
                    prev.get("title", ""), section.get("title", "")
                ):
                    # Merge: extend the previous section's end_page
                    prev["end_page"] = max(prev_end, curr_end)
                    # Keep the higher-confidence title/summary
                    if float(section.get("confidence", 0)) > float(
                        prev.get("confidence", 0)
                    ):
                        prev["title"] = section.get("title", prev.get("title"))
                        prev["summary"] = section.get(
                            "summary", prev.get("summary")
                        )
                    # Union of flags and keywords
                    prev["has_code_block"] = bool(
                        prev.get("has_code_block") or section.get("has_code_block")
                    )
                    prev["has_table"] = bool(
                        prev.get("has_table") or section.get("has_table")
                    )
                    prev["confidence"] = max(
                        float(prev.get("confidence", 0)),
                        float(section.get("confidence", 0)),
                    )
                    prev_kws = set(prev.get("keywords", []) or [])
                    curr_kws = set(section.get("keywords", []) or [])
                    prev["keywords"] = list(prev_kws | curr_kws)
                    merge_count += 1
                    continue

            # Also handle the case where a later section is fully contained
            # in the previous one (LLM reported a sub-region as a section).
            if curr_end <= prev_end and self._titles_similar(
                prev.get("title", ""), section.get("title", "")
            ):
                merge_count += 1
                continue

            merged.append(dict(section))

        if merge_count > 0:
            logger.info(
                f"[MultimodalChunker::CrossBatch] Merged {merge_count} "
                f"cross-batch section(s): {len(sections)} → {len(merged)} sections"
            )
        return merged

    @staticmethod
    def _titles_similar(t1: str, t2: str) -> bool:
        """Check if two section titles refer to the same logical section.

        Normalizes by lowercasing and stripping numbering, whitespace, and
        common punctuation. Returns True if the normalized forms match
        exactly or one contains the other.
        """
        if not t1 or not t2:
            return False

        def _normalize(s: str) -> str:
            # Strip leading numbering like "3.2.1", "第3章", "Chapter 3"
            s = re.sub(r"^(第[一二三四五六七八九十\d]+[章节]|chapter\s*\d+|section\s*\d+)", "", s.lower())
            # Remove digits, dots, whitespace, and common separators
            return re.sub(r"[\d\.\s\-_:：、，,.()（）#\[\]]+", "", s)

        n1, n2 = _normalize(t1), _normalize(t2)
        if not n1 or not n2:
            return False
        if n1 == n2:
            return True
        # One contains the other handles "Overview" vs "Overview of GPIO"
        if len(n1) >= 4 and len(n2) >= 4 and (n1 in n2 or n2 in n1):
            return True
        return False

    # ─── Vision error detection ─────────────────────────────────

    @staticmethod
    def _is_vision_error(error: Exception) -> bool:
        """Heuristically detect errors indicating the model lacks vision support."""
        err_str = str(error).lower()
        vision_keywords = [
            "image",
            "vision",
            "multimodal",
            "does not support",
            "not supported",
            "unsupported",
            "invalid image",
            "no image",
            "can't process image",
        ]
        return any(kw in err_str for kw in vision_keywords)
