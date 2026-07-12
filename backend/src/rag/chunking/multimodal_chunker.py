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
    parse_page_index,
    parse_json_robust,
    protect_structures,
    truncate_at_boundary,
    PAGE_MARKER_RE,
)
from src.rag.chunking.agent_chunker import AgentChunkError
from src.rag.chunking._constants import INLINE_CODE_RE, SUB_SPLIT_SEPARATORS

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


# Separators and INLINE_CODE_RE now imported from src.rag.chunking._constants
# (shared with AgentChunker / HybridChunker).

# Heuristic for detecting vector schematics (e.g., Eagle/Altium exported PDFs)
# where page.get_images() returns 0 because the diagram is composed of
# vector lines and text. We look for component reference designators and
# section keywords that strongly indicate a schematic page.
_SCHEMATIC_KEYWORD_RE = re.compile(
    r"\b(schematic|configuration|adapter|converter|converter)\b",
    re.IGNORECASE,
)
_COMPONENT_REF_RE = re.compile(
    r"\b([CRUQXDFTYP][A-Z]?\d+[A-Z]?|IC\d+|MAX\d+|PC\d+|74\w+)\b",
    re.IGNORECASE,
)


def _looks_like_schematic(text: str) -> bool:
    """Return True if text appears to describe a schematic/vector diagram.

    Used as a fallback when PyMuPDF page.get_images() reports no embedded
    images. Vector CAD exports (common for datasheets) store diagrams as
    lines and text, not as embedded raster images.
    """
    if not text:
        return False
    # Need at least one schematic keyword and several component references
    has_keyword = bool(_SCHEMATIC_KEYWORD_RE.search(text))
    refs = set(_COMPONENT_REF_RE.findall(text))
    return has_keyword and len(refs) >= 5


# Pattern for merging cross-page tables.
# protect_structures emits @@PROTECT_N@@ placeholders; _stash emits \x00PHN\x00
# page-marker placeholders. When a table spans pages, page markers land between
# rows and split one logical table into two @@PROTECT_N@@ placeholders.
_CROSS_PAGE_TABLE_RE = re.compile(
    r"(@@PROTECT_\d+@@)"           # first table placeholder
    r"((?:\s*\x00PH\d+\x00\s*)*)"  # page-marker placeholders in between
    r"(@@PROTECT_\d+@@)"           # second table placeholder
)


def _merge_cross_page_tables(text: str, pmap: dict) -> str:
    """Merge @@PROTECT_N@@ placeholders separated only by page-marker placeholders.

    When a Markdown table spans multiple pages, the page-marker placeholder
    (\\x00PHN\\x00) lands between table rows and causes protect_structures to
    split one logical table into two separate @@PROTECT_N@@ placeholders. This
    merges them back into a single placeholder so the table is kept intact
    through sub-splitting.
    """
    while True:
        m = _CROSS_PAGE_TABLE_RE.search(text)
        if not m:
            break
        first_key, _middle, second_key = m.group(1), m.group(2), m.group(3)
        if first_key in pmap and second_key in pmap:
            # Merge: append second table content to first, drop second placeholder.
            pmap[first_key] = pmap[first_key] + "\n" + pmap[second_key]
            del pmap[second_key]
            text = text[:m.start()] + first_key + text[m.end():]
        else:
            # Avoid infinite loop if keys not found (shouldn't happen).
            break
    return text


# ═══════════════════════════════════════════════════════════════
# Stage-1 prompt — global TOC extraction from low-res thumbnails
# ═══════════════════════════════════════════════════════════════
# Sent with 10-page low-res (dpi=100) thumbnails. The LLM only needs
# to identify chapter-level boundaries, not fine-grained sections.
_TOC_EXTRACT_PROMPT = """\
你是一个文档结构分析专家。请分析以下 PDF 页面缩略图，识别这些页面属于哪些章节。
You are a document structure analyst. Analyze these PDF page thumbnails and \
identify which chapters/sections these pages belong to.

任务 / Task:
- 识别这些页面所属的章节（chapter/section 级别，不是子小节）
- 输出每个章节的标题、起始页、结束页
- 同一章节跨越多页时，合并为一个 section

本批次页面 / Pages in this batch: {page_list}

输出 JSON / Output JSON:
{{"sections": [{{"title": "...", "start_page": N, "end_page": N}}]}}

字段说明 / Field descriptions:
- title: 章节标题（用文档语言）/ chapter title (in the document's language)
- start_page: 起始页码（实际页码）/ starting page number (actual page number)
- end_page: 结束页码（实际页码）/ ending page number (actual page number)

只输出 JSON 对象，不要输出其他文字。Output ONLY the JSON object, no additional text."""


# ═══════════════════════════════════════════════════════════════
# Stage-2 prompt — single-page fallback analysis (Plan D)
# ═══════════════════════════════════════════════════════════════
# Used when TOC extraction fails. Each call gets ONE high-res page.
_SINGLE_PAGE_ANALYSIS_PROMPT = """\
请分析这一页 PDF，识别它属于哪个章节，以及这一页的内容类型。
Analyze this single PDF page and identify which section it belongs to and \
the content type of this page.

输出 JSON / Output JSON:
{{"title": "...", "start_page": {page_num}, "end_page": {page_num}, "summary": "...", "keywords": [...], "has_code_block": false, "has_table": false, "confidence": 0.9}}

字段说明 / Field descriptions:
- title: 这一页所属的章节标题 / section title this page belongs to
- start_page: {page_num}
- end_page: {page_num}
- summary: 一句话概括这一页内容 / one-sentence summary of this page
- keywords: 2-5 个关键词 / 2-5 keywords
- has_code_block: 本页是否包含代码块 / whether this page contains code blocks
- has_table: 本页是否包含表格 / whether this page contains tables
- confidence: 0.0-1.0

只输出 JSON 对象。Output ONLY the JSON object。"""


# ═══════════════════════════════════════════════════════════════
# Image description prompt — generates text summaries for tables,
# pinout diagrams, and other visual-only content.
# ═══════════════════════════════════════════════════════════════
_IMAGE_DESCRIBE_PROMPT = """\
请详细描述这张 PDF 页面图片中的视觉内容，特别关注：
1. 如果是表格：输出表头、每一行的关键数据、单位和注释。
2. 如果是引脚图/封装图：列出每个引脚的编号、名称和功能。
3. 如果是流程图/框图：说明模块之间的连接关系和数据流。
4. 如果是普通插图：说明它展示的核心概念。

请用结构化中文或英文文本输出，不要省略关键数字和名称。"""


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
        small_chunk_size: int = 800,  # tiny-chunk merge threshold (synced with HybridChunker)
        temperature: float = 0.1,
        max_chunks: int = 500,
        vision_concurrency: int = 4,
        low_text_density_threshold: int = 300,  # chars per page; below this triggers image description
        timeout: float = 300.0,  # 5 minutes per vision LLM call
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.max_retries = max_retries
        self.batch_size = batch_size
        self.dpi = dpi
        self.sub_chunk_size = sub_chunk_size
        self.sub_chunk_overlap = sub_chunk_overlap
        self.small_chunk_size = small_chunk_size
        self.timeout = timeout
        # Moonshot AI's kimi-k2.7-code only accepts temperature=1.0.
        # Clamp instead of failing the whole chunking pipeline.
        self.temperature = temperature
        if "kimi" in self.model.lower() and self.temperature != 1.0:
            logger.warning(
                f"[MultimodalChunker] Model '{self.model}' requires temperature=1.0; "
                f"clamping from {self.temperature} to 1.0"
            )
            self.temperature = 1.0
        self.max_chunks = max_chunks
        # Concurrency limit for parallel Vision LLM calls within a single PDF.
        # Stage-1 (TOC groups) and Stage-2 (detail batches) both use this to
        # parallelize independent LLM calls via asyncio.gather + Semaphore.
        # Keep ≤5 to avoid LLM API rate limits.
        self.vision_concurrency = vision_concurrency
        # Average chars-per-page below which a section is considered text-sparse
        # and queued for vision-LLM image description (tables/schematics/diagrams).
        self.low_text_density_threshold = low_text_density_threshold

        # Sub-splitter with code-block-safe separators
        self._sub_splitter = RecursiveCharacterTextSplitter(
            chunk_size=sub_chunk_size,
            chunk_overlap=sub_chunk_overlap,
            separators=SUB_SPLIT_SEPARATORS,
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
        """Execute multimodal chunking pipeline (Plan C enhanced).

        Pipeline:
          1. Render pages (high-res dpi=200 for detail, low-res dpi=100 for TOC)
          2. Stage-1: Extract global TOC from low-res thumbnails (2-round vote)
             → Fallback A: retry TOC at dpi=150 (1 round)
             → Fallback B: Plan D single-page analysis
             → Fallback C: Plan B sliding window (overlap=3)
          3. Pack batches by TOC section boundaries
          4. Stage-2: Detail analysis per batch (2-round vote + context)
          5. Merge cross-batch sections
          6. Build chunks + merge tiny
          7. Verify page coverage + inject trace

        Raises AgentChunkError if file_path is missing/not a PDF, if no
        API key is configured, or if the model lacks vision.
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
            f"[MultimodalChunker] Pipeline start (Plan C enhanced): "
            f"file={file_path.name}, model={self.model}, dpi={self.dpi}, batch_size={self.batch_size}"
        )

        # ── Step 1: Render pages (high-res + low-res) ──
        logger.info(f"[MultimodalChunker] Step 1: Rendering PDF pages (dual resolution)")
        render_start = time.time()
        page_data = self._render_pages(file_path)  # high-res for Stage-2
        low_res_pages = self._render_pages_low_res(file_path, dpi=100)  # for Stage-1 TOC
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
            f"(high-res ~{total_image_bytes // 1024} KB, low-res ~{sum(len(p['image_b64']) for p in low_res_pages) // 1024} KB)"
        )

        # ── Step 2: Stage-1 — Global TOC extraction (Plan C) ──
        logger.info(f"[MultimodalChunker] Step 2: Stage-1 TOC extraction (2-round vote)")
        toc: list[dict] = []
        batching_strategy = "unknown"

        try:
            toc = await self._extract_global_toc(low_res_pages, num_rounds=2)
        except AgentChunkError:
            raise  # vision not supported — abort
        except Exception as e:
            logger.warning(
                f"[MultimodalChunker::TOC] Stage-1 failed: {e}; trying Fallback A (dpi=150, 1 round)"
            )

        # Fallback A: retry TOC at dpi=150, 1 round
        if not toc or len(toc) < 2:
            try:
                logger.info(f"[MultimodalChunker] Fallback A: retry TOC at dpi=150, 1 round")
                low_res_v2 = self._render_pages_low_res(file_path, dpi=150)
                toc = await self._extract_global_toc(low_res_v2, num_rounds=1)
            except Exception as e:
                logger.warning(
                    f"[MultimodalChunker::TOC] Fallback A failed: {e}; trying Fallback B (Plan D)"
                )
                toc = []

        # Fallback B: Plan D single-page analysis
        if not toc or len(toc) < 2:
            try:
                logger.info(
                    f"[MultimodalChunker] Fallback B: Plan D single-page analysis"
                )
                toc = await self._fallback_single_page_analysis(page_data)
                batching_strategy = "plan_d"
            except AgentChunkError:
                raise
            except Exception as e:
                logger.warning(
                    f"[MultimodalChunker::PlanD] Fallback B failed: {e}; trying Fallback C (Plan B overlap=3)"
                )
                toc = []

        # Decide batching strategy
        if toc and len(toc) >= 2:
            batches = self._pack_batches_by_toc(toc, page_data)
            batching_strategy = "toc"
            logger.info(
                f"[MultimodalChunker] Step 2 result: TOC success ({len(toc)} sections, "
                f"{len(batches)} batches by TOC)"
            )
        else:
            # Fallback C: Plan B sliding window with overlap=3
            logger.warning(
                f"[MultimodalChunker] All TOC/PlanD fallbacks failed; using Fallback C "
                f"(Plan B sliding window, overlap=3)"
            )
            batches = self._create_batches_with_overlap(page_data, overlap=3)
            batching_strategy = "plan_b_overlap3"
            logger.info(
                f"[MultimodalChunker] Step 2 result: Plan B ({len(batches)} batches, overlap=3)"
            )

        # ── Step 3: Stage-2 — Detail analysis per batch (2-round vote) ──
        logger.info(
            f"[MultimodalChunker] Step 3: Stage-2 detail analysis "
            f"({len(batches)} batches, 2-round vote, strategy={batching_strategy})"
        )

        all_sections: list[dict] = []
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # For TOC strategy: if TOC already has rich section info, we can
        # skip Stage-2 and use TOC sections directly. But TOC prompt only
        # asks for {title, start_page, end_page}, missing summary/keywords/
        # has_code_block/has_table/confidence. So we still need Stage-2 to
        # enrich each batch with detailed analysis.
        async def _process_one_batch(
            batch_idx: int, batch: list[dict], sem: asyncio.Semaphore
        ) -> tuple[int, list[dict], dict, float]:
            """Process one Stage-2 batch with multi-round voting (parallel-safe)."""
            batch_start = time.time()
            page_nums = [p["page_num"] for p in batch]
            logger.info(
                f"[MultimodalChunker]   Batch {batch_idx + 1}/{len(batches)}: "
                f"pages {page_nums[0]}-{page_nums[-1]} ({len(batch)} pages)"
            )

            # Build context pages: the 1 page before and 1 page after this
            # batch (text only), to help the LLM avoid truncating sections
            # that continue into adjacent batches.
            context_pages: list[dict] = []
            first_page = page_nums[0]
            last_page = page_nums[-1]
            if first_page > 1:
                prev_page = next((p for p in page_data if p["page_num"] == first_page - 1), None)
                if prev_page:
                    context_pages.append(prev_page)
            if last_page < num_pages:
                next_page = next((p for p in page_data if p["page_num"] == last_page + 1), None)
                if next_page:
                    context_pages.append(next_page)

            try:
                async with sem:
                    sections, batch_tokens = await self._analyze_batch(
                        batch, num_rounds=2, context_pages=context_pages
                    )
            except AgentChunkError:
                raise
            except Exception as e:
                raise AgentChunkError(
                    "AGENT_CHUNK_FAILED",
                    f"Batch {batch_idx + 1} analysis failed after retries: {e}",
                ) from e

            batch_elapsed = time.time() - batch_start
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
            return batch_idx, sections, batch_tokens, batch_elapsed

        # Parallel batch processing with concurrency limit.
        # asyncio.gather runs all batches concurrently; Semaphore caps the
        # number of in-flight Vision LLM calls to self.vision_concurrency.
        # return_exceptions=True prevents cascading cancellation: if one
        # batch raises (or is cancelled), other batches still complete.
        # We collect exceptions and re-raise as AgentChunkError after gather
        # returns, so CancelledError (BaseException) can't escape and crash
        # the backend process.
        sem = asyncio.Semaphore(self.vision_concurrency)
        results = await asyncio.gather(
            *[_process_one_batch(i, b, sem) for i, b in enumerate(batches)],
            return_exceptions=True,
        )

        # Separate successful results from exceptions
        successes: list[tuple[int, list[dict], dict, float]] = []
        first_exc: BaseException | None = None
        for r in results:
            if isinstance(r, BaseException):
                if first_exc is None:
                    first_exc = r
                logger.warning(
                    f"[MultimodalChunker] Batch failed with {type(r).__name__}: {r}"
                )
            else:
                successes.append(r)

        if not successes:
            # All batches failed — re-raise so caller can handle
            if isinstance(first_exc, AgentChunkError):
                raise first_exc
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                f"All {len(batches)} Stage-2 batches failed. First error: {first_exc}",
            ) from first_exc

        if first_exc is not None:
            logger.warning(
                f"[MultimodalChunker] {len(results) - len(successes)}/{len(results)} "
                f"batches failed, continuing with {len(successes)} successful batches"
            )

        # Sort by batch_idx to preserve page order, then aggregate
        results_sorted = sorted(successes, key=lambda x: x[0])
        for batch_idx, sections, batch_tokens, batch_elapsed in results_sorted:
            all_sections.extend(sections)
            for k in token_usage:
                token_usage[k] += batch_tokens.get(k, 0)

        # ── Step 4: Merge sections split across batch boundaries ──
        raw_count = len(all_sections)
        all_sections = self._merge_cross_batch_sections(all_sections)
        if len(all_sections) != raw_count:
            logger.info(
                f"[MultimodalChunker] Cross-batch merge: "
                f"{raw_count} → {len(all_sections)} sections"
            )

        # ── Step 4b: Fill page coverage gaps ──
        # Vision LLMs occasionally leave small gaps between adjacent
        # sections (e.g. a page of tables/diagrams is mis-attributed to
        # neither neighboring chapter). Ensure every page is indexed.
        gap_count_before = len(all_sections)
        all_sections = self._fill_page_gaps(all_sections, num_pages)
        if len(all_sections) != gap_count_before:
            logger.info(
                f"[MultimodalChunker] Gap fill: "
                f"{gap_count_before} → {len(all_sections)} sections"
            )

        logger.info(
            f"[MultimodalChunker] Stage-2 complete: "
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

        # ── Step 5: Build chunks from sections + page text ──
        logger.info(
            f"[MultimodalChunker] Step 5: Building chunks from {len(all_sections)} sections"
        )
        chunks = self._build_chunks(all_sections, page_data, metadata)
        logger.info(
            f"[MultimodalChunker] Built {len(chunks)} chunks (before tiny-merge)"
        )

        # ── Step 5b: Generate image description chunks for tables/diagrams ──
        image_desc_chunks = await self._build_image_description_chunks(
            all_sections, page_data, metadata
        )
        if image_desc_chunks:
            chunks.extend(image_desc_chunks)
            logger.info(
                f"[MultimodalChunker] Added {len(image_desc_chunks)} image "
                f"description chunks; total {len(chunks)}"
            )

        # ── Step 6: Merge tiny chunks ──
        chunks = self._merge_tiny_chunks(chunks)
        logger.info(
            f"[MultimodalChunker] After tiny-merge: {len(chunks)} chunks"
        )

        # ── Step 6b: max_chunks guard ──
        if len(chunks) > self.max_chunks:
            logger.warning(
                f"[MultimodalChunker] Chunk count {len(chunks)} exceeds max_chunks="
                f"{self.max_chunks}. Consider increasing sub_chunk_size or using a "
                f"larger batch_size. Truncation is NOT applied — all chunks are kept."
            )

        # ── Step 7: Verify page coverage ──
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
        trace_dict["batching_strategy"] = batching_strategy
        # Serialize to JSON string — ChromaDB metadata only accepts
        # str/int/float/bool/list/None, not nested dicts. AgentChunker
        # already does this (see agent_chunker.py lines 1029/1082/1337/1373).
        trace_json = json.dumps(trace_dict, ensure_ascii=False)
        for chunk in chunks:
            chunk.metadata["agent_trace"] = trace_json

        logger.info(
            f"[MultimodalChunker] Pipeline complete: {len(chunks)} chunks "
            f"from {num_pages} pages in {total_elapsed:.1f}s "
            f"(strategy={batching_strategy}, {len(batches)} batches, "
            f"{len(all_sections)} sections, tokens={token_usage})"
        )

        return chunks

    # ─── Page rendering ──────────────────────────────────────────

    def _render_pages(self, file_path: Path, dpi: Optional[int] = None) -> list[dict]:
        """Render each PDF page as a PNG image (base64) and extract text.

        Args:
            file_path: PDF file path.
            dpi: Optional DPI override. If None, uses self.dpi (default 200).

        Returns:
            List of {"page_num": int, "image_b64": str, "text": str},
            one entry per page (1-based page_num).
        """
        import fitz  # PyMuPDF

        effective_dpi = dpi if dpi is not None else self.dpi
        doc = fitz.open(str(file_path))
        # zoom factor: 72 is PDF default DPI
        zoom = effective_dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        pages: list[dict] = []
        try:
            for i in range(doc.page_count):
                page = doc[i]
                # Render page as pixmap → PNG bytes → base64
                pix = page.get_pixmap(matrix=mat)
                image_bytes = pix.tobytes("png")
                image_b64 = base64.b64encode(image_bytes).decode("ascii")
                # Extract text for chunk content with page marker so sub-chunks
                # can recover accurate page ranges after splitting.
                page_text = page.get_text("text") or ""
                # Extract tables as Markdown so _TABLE_ROW_RE can protect them
                # from being split across chunks (register tables, pin tables, etc.)
                try:
                    tabs = page.find_tables()
                    for tab in tabs:
                        try:
                            md = tab.to_markdown()
                        except Exception as exc:
                            logger.warning("table to_markdown failed page=%s: %s", i + 1, exc)
                            continue
                        if md and md.strip():
                            page_text = page_text.rstrip() + "\n\n" + md.strip() + "\n"
                except Exception as e:
                    logger.warning(f"page table extraction failed: {e}")
                page_text = f"<!-- PAGE:{i + 1} -->\n{page_text}"
                # 检测页面是否含有嵌入式图片（引角图、封装图、电路原理图、时序图等）
                # 让 _needs_image_description 能强制为含图页面生成 image description，
                # 避免仅依赖 LLM 的 has_table 标记导致关键图片被遗漏
                embedded_images = page.get_images()
                pages.append({
                    "page_num": i + 1,
                    "image_b64": image_b64,
                    "text": page_text,
                    "has_embedded_images": len(embedded_images) > 0,
                    "embedded_image_count": len(embedded_images),
                })
        finally:
            doc.close()

        return pages

    def _render_pages_low_res(self, file_path: Path, dpi: int = 100) -> list[dict]:
        """Render PDF pages at low resolution for Stage-1 TOC extraction.

        Low-res thumbnails (dpi=100) are much smaller than full-res
        (dpi=200), saving tokens when we only need chapter-level
        structure. Stage-2 detail analysis uses full-res via
        _render_pages().
        """
        return self._render_pages(file_path, dpi=dpi)

    # ─── JSON robust parsing (delegates to base.parse_json_robust) ──

    @staticmethod
    def _parse_json_robust(content: str) -> Optional[dict]:
        """Delegate to base.parse_json_robust (shared with AgentChunker).

        5-layer fallback: direct → ```json``` block → {…} extract →
        fix errors (single quotes, trailing commas, Python bools) →
        unmarked ``` block.
        """
        return parse_json_robust(content)

    # ─── Robust vision LLM call (streaming + JSON fallback) ──────

    async def _robust_vision_call(
        self,
        content: list[dict],
        max_retries: Optional[int] = None,
        expect_json: bool = True,
    ) -> tuple[Optional[dict], dict]:
        """Call vision LLM with streaming + robust JSON parsing + retries.

        - Uses streaming to avoid nginx 60s timeout (validated in
          AgentChunker, see pitfalls.md 2026-06-27).
        - First attempt uses response_format={"type":"json_object"};
          retries omit it (some proxies/models reject this param).
        - JSON parsed via 5-layer _parse_json_robust fallback.
        - Exponential backoff between retries.

        Returns (parsed_dict_or_None, token_usage). On vision-unsupported
        errors, raises AgentChunkError("VISION_NOT_SUPPORTED", ...).
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )
        retries = max_retries if max_retries is not None else self.max_retries
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        last_error: Optional[Exception] = None

        for attempt in range(retries):
            try:
                call_start = time.time()
                # First attempt: include response_format. Retries: omit it
                # (some proxies/models raise on this param).
                kwargs = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": self.temperature,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if attempt == 0 and expect_json:
                    kwargs["response_format"] = {"type": "json_object"}

                stream = await client.chat.completions.create(**kwargs)

                content_parts: list[str] = []
                usage = None
                async for event in stream:
                    if event.choices and event.choices[0].delta.content:
                        content_parts.append(event.choices[0].delta.content)
                    if getattr(event, "usage", None):
                        usage = event.usage

                raw_content = "".join(content_parts)
                elapsed = time.time() - call_start
                logger.info(
                    f"[MultimodalChunker]     LLM response: {len(raw_content)} chars "
                    f"in {elapsed:.1f}s (attempt {attempt + 1}, stream=True)"
                )

                token_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
                }

                if expect_json:
                    parsed = self._parse_json_robust(raw_content)
                    if parsed is not None:
                        return parsed, token_usage
                    last_error = json.JSONDecodeError(
                        "5-layer JSON parse failed", raw_content, 0
                    )
                    logger.warning(
                        f"[MultimodalChunker]     JSON parse failed (attempt {attempt + 1}), "
                        f"raw preview: {raw_content[:200]!r}"
                    )
                else:
                    return {"raw_content": raw_content}, token_usage

            except Exception as e:
                last_error = e
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
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.info(
                    f"[MultimodalChunker]     Retrying in {wait}s ..."
                )
                await asyncio.sleep(wait)

        return None, token_usage

    # ─── Batch analysis (vision LLM) ────────────────────────────

    async def _analyze_batch(
        self,
        batch: list[dict],
        num_rounds: int = 2,
        context_pages: Optional[list[dict]] = None,
    ) -> tuple[list[dict], dict]:
        """Send a batch of page images to the vision LLM and parse sections.

        Uses multi-round voting (default 2 rounds): the LLM independently
        analyzes the same batch N times, then majority vote picks the
        consensus sections (±1 page tolerance). This filters out sporadic
        boundary errors.

        Args:
            batch: List of page dicts (page_num, image_b64, text).
            num_rounds: Number of independent LLM calls for voting.
            context_pages: Optional neighboring pages (text only) to give
                the LLM cross-batch context, preventing section truncation
                at batch boundaries.

        Returns:
            Tuple of (sections list, token_usage dict).

        Raises:
            AgentChunkError: If the model does not support vision, or if all
                rounds and retries are exhausted.
        """
        page_nums = [p["page_num"] for p in batch]
        page_list_str = ", ".join(str(n) for n in page_nums)
        prompt_text = _MULTIMODAL_ANALYSIS_PROMPT.format(page_list=page_list_str)

        # Build multimodal content: prompt text + optional context + page images
        content: list[dict] = [{"type": "text", "text": prompt_text}]

        # Add neighboring-page text as context (no images) to help the LLM
        # avoid truncating sections that continue into adjacent batches.
        if context_pages:
            context_summary_parts = []
            for cp in context_pages:
                pn = cp["page_num"]
                text_preview = (cp.get("text") or "")[:300].replace("\n", " ")
                context_summary_parts.append(f"Page {pn}: {text_preview}")
            context_text = (
                "\n相邻页面摘要（仅供参考，不要包含在你的输出中）/\n"
                "Adjacent page summaries (for context only, do NOT include in output):\n"
                + "\n".join(context_summary_parts)
            )
            content.append({"type": "text", "text": context_text})

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

        # Multi-round voting
        round_results: list[list[dict]] = []
        total_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for round_idx in range(num_rounds):
            logger.info(
                f"[MultimodalChunker]     Voting round {round_idx + 1}/{num_rounds}"
            )
            parsed, tokens = await self._robust_vision_call(
                content, max_retries=self.max_retries, expect_json=True
            )
            for k in total_token_usage:
                total_token_usage[k] += tokens.get(k, 0)

            if parsed is None:
                logger.warning(
                    f"[MultimodalChunker]     Round {round_idx + 1} returned no parseable JSON"
                )
                continue

            sections = parsed.get("sections", [])
            if isinstance(sections, list) and sections:
                round_results.append(sections)

        if not round_results:
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                f"Batch analysis failed: all {num_rounds} voting rounds returned no sections",
            )

        # Majority vote: if only 1 round succeeded, use it directly.
        if len(round_results) == 1:
            voted_sections = round_results[0]
        else:
            voted_sections = self._majority_vote_sections(round_results)

        consensus_rate = (
            len(voted_sections) / max(len(round_results[0]), 1)
            if round_results
            else 0
        )
        logger.info(
            f"[MultimodalChunker]     Voting complete: {len(voted_sections)} sections "
            f"(consensus rate ~{consensus_rate:.2f}, {len(round_results)}/{num_rounds} rounds succeeded)"
        )

        return voted_sections, total_token_usage

    @staticmethod
    def _majority_vote_sections(round_results: list[list[dict]]) -> list[dict]:
        """Majority vote on sections from multiple LLM rounds.

        Clusters sections by (start_page, end_page) with ±1 page tolerance.
        Returns the sections from the round that has the most overlap with
        the consensus clusters (i.e., the "most typical" round). This is
        simpler than merging sections across rounds and avoids fabricating
        hybrid boundaries.

        Args:
            round_results: List of sections lists, one per round.

        Returns:
            Voted sections list.
        """
        if len(round_results) == 1:
            return round_results[0]

        # Collect all (start, end) pairs with their source round index
        all_pairs: list[tuple[int, int, int]] = []  # (start, end, round_idx)
        for r_idx, sections in enumerate(round_results):
            for s in sections:
                try:
                    sp = int(s.get("start_page", 0))
                    ep = int(s.get("end_page", sp))
                    all_pairs.append((sp, ep, r_idx))
                except (ValueError, TypeError):
                    continue

        if not all_pairs:
            return round_results[0]

        # Cluster pairs with ±1 page tolerance
        clusters: list[list[tuple[int, int, int]]] = []
        for pair in all_pairs:
            placed = False
            for cluster in clusters:
                rep = cluster[0]
                if (abs(pair[0] - rep[0]) <= 1 and abs(pair[1] - rep[1]) <= 1):
                    cluster.append(pair)
                    placed = True
                    break
            if not placed:
                clusters.append([pair])

        # For each cluster, count how many rounds contributed
        cluster_round_counts = []
        for cluster in clusters:
            rounds_in_cluster = set(p[2] for p in cluster)
            cluster_round_counts.append(len(rounds_in_cluster))

        # Pick the round whose sections appear in the most consensus clusters
        round_scores = [0] * len(round_results)
        for cluster, count in zip(clusters, cluster_round_counts):
            if count >= 2:  # consensus: appears in ≥2 rounds
                for p in cluster:
                    round_scores[p[2]] += 1

        best_round = max(range(len(round_results)), key=lambda i: round_scores[i])
        return round_results[best_round]

    # ─── Image description chunks ────────────────────────────────

    def _needs_image_description(
        self, section: dict, page_text_map: dict, page_data: list[dict] | None = None,
    ) -> bool:
        """判断一个 section 是否需要生成图片描述 chunk。

        策略：优先用 PyMuPDF 检测页面是否含嵌入式图片（引角图、封装图、电路原理图等），
        只要 section 范围内有任何一页含图就强制生成。回退到原有的启发式判断
        （has_table 标记 或 文本密度低于阈值）。
        """
        start = int(section.get("start_page", 1))
        end = int(section.get("end_page", start))

        # 优先检查：页面是否含有嵌入式图片（来自 _render_pages 的检测结果）
        if page_data:
            page_info = {p["page_num"]: p for p in page_data}
            for p in range(start, end + 1):
                info = page_info.get(p)
                if info and info.get("has_embedded_images"):
                    return True

        # 回退1：检测矢量电路图（page.get_images()=0 但 visually 含图）
        section_text = "\n".join(
            page_text_map.get(p, "") for p in range(start, end + 1)
        )
        if _looks_like_schematic(section_text):
            return True

        # 回退2：原有启发式判断（LLM 标记的表格 或 文本密度低）
        page_count = max(1, end - start + 1)
        avg_text = len(section_text) / page_count
        has_table = bool(section.get("has_table", False))
        return has_table or avg_text < self.low_text_density_threshold

    def _pick_description_page(
        self, section: dict, page_data: list[dict]
    ) -> Optional[dict]:
        """Pick the best page image to describe for a section."""
        start = int(section.get("start_page", 1))
        end = int(section.get("end_page", start))
        pages = [p for p in page_data if start <= p["page_num"] <= end]
        if not pages:
            return None
        # Prefer the page with the richest image (largest base64 size).
        return max(pages, key=lambda p: len(p.get("image_b64", "")))

    async def _describe_page_image(self, page: dict) -> Optional[str]:
        """Ask the vision LLM to describe a single page image."""
        image_b64 = page.get("image_b64")
        if not image_b64:
            return None
        content = [
            {"type": "text", "text": _IMAGE_DESCRIBE_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            },
        ]
        result, _ = await self._robust_vision_call(
            content, max_retries=2, expect_json=False
        )
        return result.get("raw_content", "").strip() if result else None

    async def _build_image_description_chunks(
        self,
        sections: list[dict],
        page_data: list[dict],
        metadata: dict,
    ) -> list[ChunkResult]:
        """为表格/图片/示意图生成独立的描述 chunk。

        预计算哪些 section 需要描述及其目标页面，然后按 page_num 去重，
        避免同一页面被多个 section 重复描述。顺序执行避免共享可变状态问题。
        """
        page_text_map = {p["page_num"]: p["text"] for p in page_data}

        # 预计算：确定哪些 section 需要图片描述，按页去重
        tasks_info: list[tuple[int, dict, dict]] = []
        seen_pages: set[int] = set()
        for idx, section in enumerate(sections):
            if not self._needs_image_description(section, page_text_map, page_data):
                continue
            page = self._pick_description_page(section, page_data)
            if page is None:
                continue
            page_num = page["page_num"]
            if page_num in seen_pages:
                continue
            seen_pages.add(page_num)
            tasks_info.append((idx, section, page))

        if not tasks_info:
            return []

        desc_chunks: list[ChunkResult] = []
        for idx, section, page in tasks_info:
            description = await self._describe_page_image(page)
            if not description:
                continue
            start = int(section.get("start_page", page["page_num"]))
            end = int(section.get("end_page", page["page_num"]))
            title = section.get("title", f"Section {idx + 1}")
            text = f"[图片描述 / Image description for {title}, pages {start}-{end}]\n{description}"
            # Image description is its own parent: the description text is
            # the complete content, so it serves as its own big_chunk.
            big_chunk_id = f"{metadata.get('doc_id', 'unknown')}#img{idx}"
            desc_chunks.append(ChunkResult(
                text=text,
                metadata={
                    **metadata,
                    "chunk_index": -1,
                    "section_title": title,
                    "chunk_size": len(text),
                    "chunk_method": "multimodal",
                    "content_type": "image_description",
                    "source_page": page["page_num"],
                    "big_chunk_id": big_chunk_id,
                },
                page_range=(start, end),
                fingerprint=compute_fingerprint(text),
                chunk_method="multimodal",
                section_title=title,
                big_chunk_id=big_chunk_id,
                big_chunk_text=text,
            ))
        return desc_chunks

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
        # Track assigned pages to prevent section boundary overlap (Task 2:
        # dedup root cause). Overlapping sections re-index the same pages,
        # producing duplicate chunks after sub-splitting. Each page is only
        # assigned to the first section that claims it; later sections get
        # only the unassigned pages in their range. Empty sections are kept
        # (no skipping) to preserve LLM-identified boundaries — the dedup
        # fallback in kb_manager.ingest_chunks handles residual duplicates.
        assigned_pages: set[int] = set()

        for i, section in enumerate(sections):
            start_page = int(section.get("start_page", 1))
            end_page = int(section.get("end_page", start_page))

            # Clamp to valid range
            start_page = max(1, min(start_page, max_page))
            end_page = max(start_page, min(end_page, max_page))

            title = section.get("title", f"Section {i + 1}") or f"Section {i + 1}"

            # Collect unassigned pages in this section's range
            section_pages = [
                p for p in range(start_page, end_page + 1)
                if p not in assigned_pages
            ]
            assigned_pages.update(section_pages)
            if not section_pages:
                logger.info(
                    f"[MultimodalChunker::Build] Section {i + 1} '{title}': "
                    f"all pages {start_page}-{end_page} already assigned, "
                    f"section will be empty (skipped)"
                )
                continue

            summary = section.get("summary", "")
            keywords = section.get("keywords", [])
            has_code = bool(section.get("has_code_block", False))
            has_table = bool(section.get("has_table", False))
            confidence = section.get("confidence", 0.5)

            # Concatenate actual page text for this section (only unassigned pages)
            page_texts = [
                page_text_map.get(p, "")
                for p in section_pages
            ]
            section_text = "\n\n".join(t for t in page_texts if t)
            # Keep page markers so sub-chunks can recover accurate page ranges.
            # They are stripped from final chunk text after page_range is determined.

            if not section_text.strip():
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

            # Check if the entire section is a single code block.
            # NOTE: section_text is built from "\n\n".join(page_texts) where each
            # page text starts with "<!-- PAGE:N -->\n", so it always begins with
            # "<!-- PAGE:" — strip markers first or startswith("```") is always False.
            stripped_section = strip_page_markers(section_text).strip()
            is_whole_code_block = (
                stripped_section.startswith("```") and stripped_section.endswith("```")
            )

            # Build big_chunk_id + big_chunk_text for ParentDocument retrieval.
            # All sub_chunks from this section share the same big_chunk_id/text.
            doc_id = metadata.get("doc_id", "unknown")
            big_chunk_id = f"{doc_id}#b{i}"
            big_chunk_text = truncate_at_boundary(stripped_section)

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
                    "big_chunk_id": big_chunk_id,
                    "small_chunk_id": f"{doc_id}#s{len(chunks)}",
                }
                # Determine accurate page range from markers inside the text
                page_nums = [p[0] for p in parse_page_index(section_text)]
                if page_nums:
                    code_start_page = min(page_nums)
                    code_end_page = max(page_nums)
                else:
                    code_start_page = start_page
                    code_end_page = end_page
                code_text = strip_page_markers(section_text).strip()
                chunks.append(ChunkResult(
                    text=code_text,
                    metadata=chunk_meta,
                    page_range=(code_start_page, code_end_page),
                    fingerprint=compute_fingerprint(code_text),
                    chunk_method="multimodal",
                    section_title=title,
                    big_chunk_id=big_chunk_id,
                    big_chunk_text=big_chunk_text,
                ))
                continue

            placeholder_map: dict[str, str] = {}
            ph_counter = [0]

            def _stash(m: re.Match) -> str:
                key = f"\x00PH{ph_counter[0]}\x00"
                ph_counter[0] += 1
                placeholder_map[key] = m.group(0)
                return key

            placeholder_text = INLINE_CODE_RE.sub(_stash, section_text)

            # Protect page markers from being split mid-tag (e.g. "<!-- PAGE"
            # + ":3 -->"). Without this, RecursiveCharacterTextSplitter can
            # break markers across chunk boundaries, causing parse_page_index
            # to fail and fall back to the LLM-reported start_page (often 1).
            placeholder_text = PAGE_MARKER_RE.sub(_stash, placeholder_text)

            # Protect Markdown tables and register-field blocks from splitting
            placeholder_text, table_map = protect_structures(placeholder_text)
            placeholder_map.update(table_map)

            # Merge adjacent table placeholders separated only by page-marker
            # placeholders. When a table spans multiple pages, the page-marker
            # placeholder (\x00PHN\x00) sits between table rows and causes
            # protect_structures to split one logical table into two separate
            # @@PROTECT_N@@ placeholders. Merge them back into a single table.
            placeholder_text = _merge_cross_page_tables(
                placeholder_text, placeholder_map
            )

            if placeholder_map:
                logger.info(
                    f"[MultimodalChunker::Build]   → Protected {len(placeholder_map)} "
                    f"structure(s) (code/table/register) before sub-splitting"
                )

            sub_chunks = self._sub_splitter.split_text(placeholder_text)
            logger.info(
                f"[MultimodalChunker::Build]   → Sub-split into {len(sub_chunks)} chunks "
                f"(target size={self.sub_chunk_size}, overlap={self.sub_chunk_overlap})"
            )

            for sub_text in sub_chunks:
                if not sub_text.strip():
                    continue
                for _ in range(len(placeholder_map) + 1):
                    changed = False
                    for ph, original in placeholder_map.items():
                        if ph in sub_text:
                            sub_text = sub_text.replace(ph, original)
                            changed = True
                    if not changed:
                        break

                # Check for actual page markers (parse_page_index defaults to
                # page 1 when no markers found, which masks missing markers).
                # Use direct regex check so we can fall back to the section's
                # page range when markers are absent (e.g., lost in overlap).
                markers_in_sub = PAGE_MARKER_RE.findall(sub_text)
                if markers_in_sub:
                    sub_page_nums = [int(p) for p in markers_in_sub]
                    sub_start_page = min(sub_page_nums)
                    sub_end_page = max(sub_page_nums)
                else:
                    sub_start_page = start_page
                    sub_end_page = end_page

                final_text = strip_page_markers(sub_text).strip()
                if not final_text:
                    continue

                chunk_meta = {
                    **metadata,
                    "chunk_index": len(chunks),
                    "section_title": title,
                    "section_summary": summary,
                    "section_keywords": keywords,
                    "section_confidence": confidence,
                    "has_code_block": has_code,
                    "has_table": has_table,
                    "section_page_start": start_page,
                    "section_page_end": end_page,
                    "is_code_block": False,
                    "chunk_size": len(final_text),
                    "chunk_method": "multimodal",
                    "big_chunk_id": big_chunk_id,
                    "small_chunk_id": f"{doc_id}#s{len(chunks)}",
                }
                chunks.append(ChunkResult(
                    text=final_text,
                    metadata=chunk_meta,
                    page_range=(sub_start_page, sub_end_page),
                    fingerprint=compute_fingerprint(final_text),
                    chunk_method="multimodal",
                    section_title=title,
                    big_chunk_id=big_chunk_id,
                    big_chunk_text=big_chunk_text,
                ))

        # Renumber chunk_index
        for i, chunk in enumerate(chunks):
            chunk.metadata["chunk_index"] = i

        return chunks

    # ─── Tiny chunk absorption ───────────────────────────────────

    # Pattern for filtering out pure-symbol chunks (e.g. "---", "===", "|---|")
    _SYMBOL_PATTERN = re.compile(r'^[\s\-_=*#|+.:`"\s]+$')

    def _merge_tiny_chunks(
        self, chunks: list[ChunkResult], threshold: int = 100
    ) -> list[ChunkResult]:
        """Merge non-code chunks shorter than threshold into adjacent chunks.

        Three-pass merge (synced with HybridChunker):
        - Pre-filter: drop pure-symbol chunks (< 50 chars, only symbols)
        - Pass 1: backward merge (tiny → previous same-section chunk)
        - Pass 2: forward merge (tiny → next chunk, with cross-section support)
          - cross_section_ok when pending_tiny has no section_title (orphan)
          - cross_section_ok when combined_len <= small_chunk_size
        """
        if len(chunks) <= 1:
            return chunks

        # ── Pre-filter: drop pure-symbol chunks ──
        filtered: list[ChunkResult] = []
        dropped = 0
        for chunk in chunks:
            is_code = chunk.metadata.get("is_code_block", False)
            text = chunk.text.strip()
            if (
                not is_code
                and len(text) < 50
                and self._SYMBOL_PATTERN.match(text)
            ):
                dropped += 1
                continue
            filtered.append(chunk)
        if dropped > 0:
            logger.info(
                f"[MultimodalChunker::Merge] Dropped {dropped} pure-symbol chunk(s)"
            )
        chunks = filtered
        if len(chunks) <= 1:
            return chunks

        # ── Pass 1: backward merge (tiny → previous same-section) ──
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
                        big_chunk_id=prev.big_chunk_id,
                        big_chunk_text=prev.big_chunk_text,
                    )
                    merge_count += 1
                    continue
            merged.append(chunk)

        # ── Pass 2: forward merge (tiny → next chunk, with cross-section) ──
        # Handles tiny chunks that are the FIRST of a section (backward merge
        # failed because previous chunk is a different section).
        # Cross-section merge conditions:
        # - pending_tiny has empty section_title (orphan paragraph)
        # - OR combined length <= small_chunk_size (table-heavy docs)
        if len(merged) > 1:
            final: list[ChunkResult] = []
            pending_tiny: Optional[ChunkResult] = None

            for i, chunk in enumerate(merged):
                is_code = chunk.metadata.get("is_code_block", False)
                is_tiny = len(chunk.text) < threshold

                if pending_tiny is not None:
                    same_section = pending_tiny.section_title == chunk.section_title
                    combined_len = len(pending_tiny.text) + 1 + len(chunk.text)
                    # Only allow cross-section merge for orphan paragraphs
                    # (no section_title) or when combined size is very small
                    # (<= small_chunk_size / 2). The previous threshold
                    # (<= small_chunk_size) was too permissive and merged
                    # unrelated tiny tails from adjacent sections.
                    cross_section_threshold = self.small_chunk_size // 2
                    cross_section_ok = (
                        not pending_tiny.section_title
                        or combined_len <= cross_section_threshold
                    )
                    if same_section or cross_section_ok:
                        new_text = pending_tiny.text + "\n" + chunk.text
                        new_meta = {**chunk.metadata}
                        new_meta["chunk_size"] = len(new_text)
                        merged_chunk = ChunkResult(
                            text=new_text,
                            metadata=new_meta,
                            page_range=chunk.page_range,
                            fingerprint=compute_fingerprint(new_text),
                            chunk_method=chunk.chunk_method,
                            section_title=chunk.section_title,
                            big_chunk_id=chunk.big_chunk_id,
                            big_chunk_text=chunk.big_chunk_text,
                        )
                        final.append(merged_chunk)
                        pending_tiny = None
                        merge_count += 1
                        continue
                    else:
                        final.append(pending_tiny)
                        pending_tiny = None

                if is_tiny and not is_code and i < len(merged) - 1:
                    next_chunk = merged[i + 1]
                    next_same_section = next_chunk.section_title == chunk.section_title
                    next_combined_len = len(chunk.text) + 1 + len(next_chunk.text)
                    cross_section_eligible = (
                        not chunk.section_title
                        or next_combined_len <= self.small_chunk_size
                    )
                    if next_same_section or cross_section_eligible:
                        pending_tiny = chunk
                        continue

                final.append(chunk)

            if pending_tiny is not None:
                final.append(pending_tiny)

            merged = final

        # Renumber chunk_index and small_chunk_id (synced with HybridChunker)
        for i, chunk in enumerate(merged):
            chunk.metadata["chunk_index"] = i
            chunk.metadata["small_chunk_id"] = (
                f"{chunk.metadata.get('doc_id', 'unknown')}#s{i}"
            )

        if merge_count > 0 or dropped > 0:
            logger.info(
                f"[MultimodalChunker::Merge] Merged {merge_count} tiny chunk(s), "
                f"dropped {dropped} symbol chunk(s): "
                f"{len(chunks) + dropped + merge_count} → {len(merged)} chunks"
            )
        return merged

    # ─── Batch overlap + cross-batch section merging ────────────

    def _create_batches_with_overlap(
        self, page_data: list[dict], overlap: int = 1
    ) -> list[list[dict]]:
        """Create batches with N-page overlap for cross-batch continuity.

        With batch_size=5 and overlap=1, a 12-page document yields:
          Batch 1: pages 1-5
          Batch 2: pages 5-9   (page 5 shared with batch 1)
          Batch 3: pages 9-12  (page 9 shared with batch 2)

        The overlap lets the vision LLM see the boundary context on both
        sides, so _merge_cross_batch_sections can stitch a section that
        spans the boundary back into a single section.

        This is the Fallback C (Plan B) path — used only when TOC-driven
        batching fails. The default overlap=1 is for normal fallback;
        Plan B last-resort uses overlap=3 for stronger continuity.
        """
        n = len(page_data)
        if n <= self.batch_size:
            return [page_data]

        step = self.batch_size - overlap
        if step <= 0:
            step = 1  # safety guard
        batches: list[list[dict]] = []
        i = 0
        while i < n:
            batch = page_data[i : i + self.batch_size]
            batches.append(batch)
            if i + self.batch_size >= n:
                break
            i += step
        return batches

    # ─── Stage-1: Global TOC extraction (Plan C) ────────────────

    async def _extract_global_toc(
        self,
        low_res_pages: list[dict],
        num_rounds: int = 2,
        group_size: int = 10,
    ) -> list[dict]:
        """Extract global table-of-contents from low-res page thumbnails.

        Groups pages into chunks of `group_size` (default 10), sends each
        group to the vision LLM with 2-round voting, then merges sections
        that span group boundaries via _merge_cross_group_sections.

        Returns a list of {title, start_page, end_page} dicts. Returns
        empty list if all groups fail (caller should fall back to Plan D).
        """
        if not low_res_pages:
            return []

        n = len(low_res_pages)
        # Group pages into chunks of group_size (no overlap — TOC merge
        # handles cross-group continuity).
        groups: list[list[dict]] = []
        for i in range(0, n, group_size):
            groups.append(low_res_pages[i : i + group_size])

        logger.info(
            f"[MultimodalChunker::TOC] Extracting global TOC: "
            f"{n} pages in {len(groups)} groups ({group_size}/group, {num_rounds} rounds)"
        )

        all_toc_sections: list[dict] = []
        total_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        async def _process_one_group(
            g_idx: int, group: list[dict], sem: asyncio.Semaphore
        ) -> tuple[int, list[dict], dict]:
            """Process one TOC group with multi-round voting (parallel-safe)."""
            page_nums = [p["page_num"] for p in group]
            page_list_str = ", ".join(str(n) for n in page_nums)
            prompt_text = _TOC_EXTRACT_PROMPT.format(page_list=page_list_str)

            content: list[dict] = [{"type": "text", "text": prompt_text}]
            for page in group:
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

            # Multi-round voting for this group (serial within group, parallel across groups)
            round_results: list[list[dict]] = []
            group_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            async with sem:
                for r_idx in range(num_rounds):
                    logger.info(
                        f"[MultimodalChunker::TOC]   Group {g_idx + 1}/{len(groups)} "
                        f"round {r_idx + 1}/{num_rounds} (pages {page_nums[0]}-{page_nums[-1]})"
                    )
                    parsed, tokens = await self._robust_vision_call(
                        content, max_retries=self.max_retries, expect_json=True
                    )
                    for k in group_tokens:
                        group_tokens[k] += tokens.get(k, 0)

                    if parsed is None:
                        continue
                    sections = parsed.get("sections", [])
                    if isinstance(sections, list) and sections:
                        round_results.append(sections)

            if not round_results:
                logger.warning(
                    f"[MultimodalChunker::TOC]   Group {g_idx + 1} returned no sections"
                )
                return g_idx, [], group_tokens

            # Pick the round with the most sections (or majority vote if tied)
            if len(round_results) == 1:
                group_sections = round_results[0]
            else:
                group_sections = self._majority_vote_sections(round_results)

            logger.info(
                f"[MultimodalChunker::TOC]   Group {g_idx + 1} done: "
                f"{len(group_sections)} sections"
            )
            return g_idx, group_sections, group_tokens

        # Parallel group processing with concurrency limit.
        # asyncio.gather runs all groups concurrently; Semaphore caps the
        # number of in-flight Vision LLM calls to self.vision_concurrency.
        # return_exceptions=True prevents cascading cancellation: if one
        # group raises (or is cancelled), other groups still complete.
        # Partial failures are tolerated (we merge whatever groups succeeded),
        # so TOC extraction degrades gracefully instead of crashing.
        sem = asyncio.Semaphore(self.vision_concurrency)
        results = await asyncio.gather(
            *[_process_one_group(i, g, sem) for i, g in enumerate(groups)],
            return_exceptions=True,
        )

        # Separate successful results from exceptions
        successes: list[tuple[int, list[dict], dict]] = []
        first_exc: BaseException | None = None
        for r in results:
            if isinstance(r, BaseException):
                if first_exc is None:
                    first_exc = r
                logger.warning(
                    f"[MultimodalChunker::TOC] Group failed with {type(r).__name__}: {r}"
                )
            else:
                successes.append(r)

        if not successes:
            # All groups failed — re-raise so caller's fallback chain kicks in
            if isinstance(first_exc, AgentChunkError):
                raise first_exc
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                f"All {len(groups)} TOC groups failed. First error: {first_exc}",
            ) from first_exc

        if first_exc is not None:
            logger.warning(
                f"[MultimodalChunker::TOC] {len(results) - len(successes)}/{len(results)} "
                f"groups failed, continuing with {len(successes)} successful groups"
            )

        # Sort by g_idx to preserve page order, then aggregate tokens + sections
        results_sorted = sorted(successes, key=lambda x: x[0])
        for g_idx, group_sections, group_tokens in results_sorted:
            all_toc_sections.extend(group_sections)
            for k in total_tokens:
                total_tokens[k] += group_tokens.get(k, 0)

        # Merge sections that span group boundaries
        raw_count = len(all_toc_sections)
        all_toc_sections = self._merge_cross_group_sections(all_toc_sections)
        if len(all_toc_sections) != raw_count:
            logger.info(
                f"[MultimodalChunker::TOC] Cross-group merge: "
                f"{raw_count} → {len(all_toc_sections)} sections"
            )

        logger.info(
            f"[MultimodalChunker::TOC] Global TOC extracted: "
            f"{len(all_toc_sections)} sections, tokens={total_tokens}"
        )
        return all_toc_sections

    def _merge_cross_group_sections(self, sections: list[dict]) -> list[dict]:
        """Merge TOC sections that span group boundaries.

        Similar to _merge_cross_batch_sections but for TOC extraction:
        groups have NO overlap, so we merge sections where the next
        section's start_page is within ±1 of the previous section's
        end_page AND titles are similar. This stitches a chapter that
        was split across two 10-page groups back into one section.
        """
        if len(sections) <= 1:
            return sections

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

            # Sections are adjacent or overlapping (±1 page tolerance for
            # LLM page-number imprecision across groups).
            if curr_start <= prev_end + 1:
                if self._titles_similar(
                    prev.get("title", ""), section.get("title", "")
                ):
                    prev["end_page"] = max(prev_end, curr_end)
                    if float(section.get("confidence", 0)) > float(
                        prev.get("confidence", 0)
                    ):
                        prev["title"] = section.get("title", prev.get("title"))
                    merge_count += 1
                    continue

            # Skip fully-contained sections (LLM reported a sub-region)
            if curr_end <= prev_end and self._titles_similar(
                prev.get("title", ""), section.get("title", "")
            ):
                merge_count += 1
                continue

            merged.append(dict(section))

        if merge_count > 0:
            logger.info(
                f"[MultimodalChunker::TOC] Merged {merge_count} "
                f"cross-group section(s): {len(sections)} → {len(merged)}"
            )
        return merged

    def _pack_batches_by_toc(
        self, toc: list[dict], high_res_pages: list[dict]
    ) -> list[list[dict]]:
        """Pack high-res pages into batches aligned with TOC section boundaries.

        - Sections ≤ batch_size pages: sent as one batch (no truncation).
        - Sections > batch_size pages: split internally at paragraph
          boundaries (\n\n) into sub-batches of ≤ batch_size pages.
        - No overlap between batches (TOC boundaries are natural splits).

        This ensures sections are never truncated at arbitrary page
        boundaries — only at real section boundaries or paragraph breaks.
        """
        page_map = {p["page_num"]: p for p in high_res_pages}
        max_page = max(page_map.keys()) if page_map else 0

        batches: list[list[dict]] = []
        for section in toc:
            start = max(1, int(section.get("start_page", 1)))
            end = min(max_page, int(section.get("end_page", start)))
            if end < start:
                continue

            section_pages = [page_map[p] for p in range(start, end + 1) if p in page_map]
            if not section_pages:
                continue

            if len(section_pages) <= self.batch_size:
                batches.append(section_pages)
            else:
                # Split at paragraph boundaries: find the page in the
                # middle whose text ends with \n\n (paragraph break).
                i = 0
                while i < len(section_pages):
                    end_i = min(i + self.batch_size, len(section_pages))
                    # Try to shift end_i back to a paragraph boundary
                    if end_i < len(section_pages):
                        for j in range(end_i, i + 1, -1):
                            text = section_pages[j - 1].get("text", "")
                            if text.rstrip().endswith("\n\n") or "\n\n" in text[-100:]:
                                end_i = j
                                break
                    batches.append(section_pages[i:end_i])
                    i = end_i

        logger.info(
            f"[MultimodalChunker::Pack] Packed {len(high_res_pages)} pages into "
            f"{len(batches)} batches by TOC ({len(toc)} sections)"
        )
        return batches

    async def _fallback_single_page_analysis(
        self, high_res_pages: list[dict]
    ) -> list[dict]:
        """Plan D fallback: analyze each page individually.

        Used when TOC extraction fails. Each page gets its own LLM call
        with 1 round (no voting — already degraded). Sections are then
        merged by title similarity via _merge_cross_group_sections.

        Returns a list of section dicts.
        """
        logger.info(
            f"[MultimodalChunker::PlanD] Single-page analysis: "
            f"{len(high_res_pages)} pages"
        )
        all_sections: list[dict] = []
        total_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for page in high_res_pages:
            page_num = page["page_num"]
            prompt_text = _SINGLE_PAGE_ANALYSIS_PROMPT.format(page_num=page_num)

            content: list[dict] = [{"type": "text", "text": prompt_text}]
            content.append({
                "type": "text",
                "text": f"--- Page {page_num} ---",
            })
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{page['image_b64']}"
                },
            })

            parsed, tokens = await self._robust_vision_call(
                content, max_retries=self.max_retries, expect_json=True
            )
            for k in total_tokens:
                total_tokens[k] += tokens.get(k, 0)

            if parsed is None:
                logger.warning(
                    f"[MultimodalChunker::PlanD]   Page {page_num} returned no JSON"
                )
                continue

            # Single-page prompt returns a single section dict, not a list
            if "sections" in parsed:
                sections = parsed.get("sections", [])
                if isinstance(sections, list):
                    all_sections.extend(sections)
            else:
                # Treat the dict itself as one section
                all_sections.append(parsed)

        # Merge adjacent pages with similar titles into multi-page sections
        merged = self._merge_cross_group_sections(all_sections)
        logger.info(
            f"[MultimodalChunker::PlanD] Done: {len(merged)} sections "
            f"from {len(high_res_pages)} pages, tokens={total_tokens}"
        )
        return merged

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

    def _fill_page_gaps(
        self, sections: list[dict], num_pages: int
    ) -> list[dict]:
        """Ensure every PDF page is covered by at least one section.

        Vision-LLM section boundary detection can leave small gaps between
        adjacent sections (e.g. a page of dense tables is mis-attributed to
        the previous or next chapter). This creates orphaned pages that are
        never indexed. We detect uncovered page ranges and inject synthetic
        continuation sections so no page text is lost.
        """
        if not sections or num_pages <= 0:
            return sections

        # Sort sections by start_page for deterministic gap scanning
        sorted_sections = sorted(
            sections,
            key=lambda s: (int(s.get("start_page", 0)), int(s.get("end_page", 0))),
        )

        covered: set[int] = set()
        for s in sorted_sections:
            start = max(1, int(s.get("start_page", 1)))
            end = min(num_pages, int(s.get("end_page", start)))
            covered.update(range(start, end + 1))

        if len(covered) >= num_pages:
            return sections

        # Find uncovered intervals
        gaps: list[tuple[int, int]] = []
        gap_start: int | None = None
        for p in range(1, num_pages + 1):
            if p not in covered:
                if gap_start is None:
                    gap_start = p
            else:
                if gap_start is not None:
                    gaps.append((gap_start, p - 1))
                    gap_start = None
        if gap_start is not None:
            gaps.append((gap_start, num_pages))

        if not gaps:
            return sections

        # Build a new section list that keeps originals and adds gap fillers
        filled: list[dict] = []
        section_idx = 0
        for gap_start, gap_end in gaps:
            # Add original sections that end before this gap
            while (
                section_idx < len(sorted_sections)
                and int(sorted_sections[section_idx].get("end_page", 0)) < gap_start
            ):
                filled.append(dict(sorted_sections[section_idx]))
                section_idx += 1

            # Pick a neighbor title: previous section preferred, next as fallback
            neighbor_title = "Uncategorized"
            neighbor_summary = ""
            neighbor_keywords: list[str] = []
            if filled:
                neighbor_title = filled[-1].get("title", "Uncategorized")
                neighbor_summary = filled[-1].get("summary", "")
                neighbor_keywords = list(filled[-1].get("keywords", []) or [])
            elif section_idx < len(sorted_sections):
                neighbor_title = sorted_sections[section_idx].get("title", "Uncategorized")
                neighbor_summary = sorted_sections[section_idx].get("summary", "")
                neighbor_keywords = list(sorted_sections[section_idx].get("keywords", []) or [])

            gap_title = f"{neighbor_title} (continued)"
            gap_section = {
                "title": gap_title,
                "start_page": gap_start,
                "end_page": gap_end,
                "summary": neighbor_summary or f"Continuation pages {gap_start}-{gap_end}",
                "keywords": neighbor_keywords,
                "has_code_block": False,
                "has_table": True,  # gaps often contain tables/diagrams between sections
                "confidence": 0.5,
            }
            filled.append(gap_section)
            logger.info(
                f"[MultimodalChunker::GapFill] Filled pages {gap_start}-{gap_end} "
                f"as '{gap_title}'"
            )

        # Append remaining original sections
        while section_idx < len(sorted_sections):
            filled.append(dict(sorted_sections[section_idx]))
            section_idx += 1

        return filled

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
