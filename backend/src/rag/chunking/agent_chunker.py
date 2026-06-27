"""Agent chunker — LLM-driven semantic chunking with transparency and configurability.

Design principles:
1. **Transparent**: Every LLM call is logged with prompt, response, timing. A
   ChunkTrace captures the full pipeline (rounds, votes, disputed boundaries)
   and is embedded in chunk metadata so the user can inspect exactly what the
   LLM decided and why.
2. **Configurable**: Prompt template, temperature, round count, batch size,
   and sub-chunk size are all constructor parameters. The KB layer can override
   them per knowledge base.
3. **Code-block-safe**: Sub-splitting uses the same placeholder protection as
   HybridChunker, so fenced code blocks are never fragmented.
4. **Observable**: The /api/kb/documents/{doc_id}/chunks endpoint exposes
   agent_trace, boundary_disputed, section_summary, and is_code_block fields.
"""

import asyncio
import json
import logging
import re
import statistics
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.rag.chunking.base import (
    ChunkResult,
    BaseChunker,
    compute_fingerprint,
    get_text_for_page_range,
    strip_page_markers,
    verify_page_coverage,
    PAGE_MARKER_RE,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Default prompt template — bilingual, code-block-aware, configurable
# ═══════════════════════════════════════════════════════════════
# This prompt is intentionally detailed: it tells the LLM exactly what
# constitutes a good section boundary, warns it about code blocks, and
# asks for a summary + keywords so downstream retrieval has rich metadata.
#
# The user can override this entire template via the `prompt_template`
# constructor parameter, or override just the system instructions via
# `prompt_system_extra` (appended to the base instructions).

_DEFAULT_AGENT_CHUNK_PROMPT = """\
你是一个文档结构分析专家。请分析以下文档文本，识别逻辑章节/段落。
You are a document structure analyst. Analyze the following document text and \
identify logical sections.

## 分析规则 / Analysis Rules

1. **语义完整性 / Semantic completeness**: 每个 section 应该是一个完整的语义单元，\
不要在句子中间断开。Each section should be a complete semantic unit — never break \
mid-sentence.
2. **代码块保护 / Code block protection**: 绝不在 ``` 围栏代码块中间断开。\
如果一个代码块超过 500 字符，将其作为独立 section。Never split inside a ``` fenced \
code block. If a code block exceeds 500 chars, make it its own section.
3. **上下文保持 / Context preservation**: section 的标题和开头几行应该提供足够上下文，\
让读者不需要看前一节就能理解本节内容。Each section's title and opening lines should \
provide enough context to understand it without reading the previous section.
4. **大小目标 / Size target**: 目标 section 大小 300-2000 字符。小于 100 字符的片段\
应合并到相邻 section。Target section size 300-2000 chars. Fragments under 100 chars \
should be merged into adjacent sections.
5. **标题层级 / Header hierarchy**: 识别 Markdown 标题层级（#, ##, ###, ####），\
保留层级路径作为 title。Detect Markdown header levels and preserve the hierarchy path.
6. **特殊内容 / Special content**: 表格、列表、参数定义应与它们的标题/说明保持在同一 section。\
Tables, lists, and parameter definitions should stay with their heading/description.

## 输出格式 / Output Format

输出 JSON 对象，包含 "sections" 数组。每个 section 包含：
Output a JSON object with a "sections" array. Each section contains:

- start_page: 起始页码（估算）/ approximate starting page number
- end_page: 结束页码（估算）/ approximate ending page number
- title: 简洁的章节标题（用文档语言）/ concise section title (in the document's language)
- summary: 一句话概括本节内容 / one-sentence summary of the section's content
- keywords: 2-5 个关键词，用于检索 / 2-5 keywords for retrieval
- has_code_block: 本节是否包含代码块 / whether this section contains code blocks
- confidence: 0.0-1.0，你对这个边界的置信度 / your confidence in this boundary (0.0-1.0)

示例 / Example:
{{"sections": [{{"start_page": 1, "end_page": 3, "title": "GPIO 配置", "summary": "GPIO 推挽/开漏模式配置", "keywords": ["GPIO", "push-pull", "open-drain"], "has_code_block": true, "confidence": 0.9}}]}}

## 待分析文档 / Document Text (batch {batch_num}, pages {page_range}):

---
{text}
---

只输出 JSON 对象，不要输出其他文字。Output ONLY the JSON object, no additional text."""


# Separators for sub-splitting — kept in sync with HybridChunker.
# Note: "\n```\n" is absent because code blocks are protected with placeholders.
#
# "\n\n**Q" (FAQ heading) and "\n\n|" (table boundary) are placed before
# "\n\n" so that FAQ questions and markdown tables stay attached to their
# preceding heading/intro. Without this, the splitter cuts at "\n\n"
# between "### Title\n\nintro" and the table/FAQ, producing tiny
# contextless chunks. See hybrid_chunker.py for full rationale.
_SUB_SPLIT_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n**Q", "\n\n|", "\n\n", "\n", "。", ".", " ", ""]

# Pattern for inline fenced code blocks (placeholder protection)
_INLINE_CODE_RE = re.compile(r"```[\s\S]*?```")


# ═══════════════════════════════════════════════════════════════
# ChunkTrace — transparency data structure
# ═══════════════════════════════════════════════════════════════

@dataclass
class RoundResult:
    """Records one round of LLM chunking."""
    round_num: int
    num_batches: int
    sections_found: int
    batch_details: list[dict] = field(default_factory=list)  # per-batch prompt/response summary
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChunkTrace:
    """Full trace of the agent chunking process, stored in chunk metadata.

    This makes the LLM's chunking decisions fully observable: you can see
    how many rounds ran, what each round found, which boundaries were
    disputed, and how long each step took.
    """
    method: str = "agent"
    model: str = ""
    num_rounds: int = 0
    temperature: float = 0.0
    toc_entries: int = 0
    num_batches: int = 0
    rounds: list[dict] = field(default_factory=list)
    voted_sections: int = 0
    disputed_sections: int = 0
    final_chunks: int = 0
    total_elapsed_seconds: float = 0.0
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# AgentChunker
# ═══════════════════════════════════════════════════════════════

class AgentChunker(BaseChunker):
    """LLM-driven chunker with full transparency and configurability.

    All parameters are configurable so the KB layer can tune behavior per
    knowledge base. The prompt template can be entirely overridden, or
    extra system instructions can be appended via `prompt_system_extra`.

    The chunking process is recorded in a ChunkTrace that is embedded in
    every chunk's metadata under the "agent_trace" key, making the LLM's
    decisions fully observable via the API.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        context_window: int = 256000,
        max_retries: int = 3,
        temperature: float = 0.1,
        # New configurable parameters:
        num_rounds: int = 3,
        max_batch_chars: int = 80000,
        sub_chunk_size: int = 1000,
        sub_chunk_overlap: int = 200,
        small_chunk_size: int = 500,
        max_chunks: int = 500,
        prompt_template: Optional[str] = None,
        prompt_system_extra: str = "",
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.context_window = context_window
        self.max_retries = max_retries
        self.temperature = temperature
        self.num_rounds = num_rounds
        self.max_batch_chars = max_batch_chars
        self.sub_chunk_size = sub_chunk_size
        self.sub_chunk_overlap = sub_chunk_overlap
        self.small_chunk_size = small_chunk_size
        self.max_chunks = max_chunks
        self.prompt_template = prompt_template or _DEFAULT_AGENT_CHUNK_PROMPT
        self.prompt_system_extra = prompt_system_extra

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
        """Execute agent chunking pipeline with full transparency."""
        if not self.api_key:
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                "Agent chunker requires an API key. Configure it in RAG settings or KB settings.",
            )

        # P0: Dynamically scale num_rounds based on document size to avoid
        # timeouts on large docs. With max_batch_chars=80000, a 150K doc splits
        # into 2 batches; num_rounds=3 means 6 LLM calls (~4-6 min), which
        # risks the 600s upload timeout especially with API retries.
        # Strategy: keep configured num_rounds for small docs, reduce for large.
        # Threshold=80K ensures any doc that needs >1 batch uses num_rounds=1.
        effective_num_rounds = self.num_rounds
        doc_size = len(text)
        if doc_size > 80_000 and self.num_rounds > 1:
            effective_num_rounds = 1
            logger.info(
                f"[AgentChunker] Large doc ({doc_size} chars > 80K): reducing "
                f"num_rounds {self.num_rounds} → 1 to avoid timeout"
            )

        # P0: For non-PDF documents (total_pages == 0) larger than one batch,
        # insert synthetic page markers every max_batch_chars characters.
        #
        # Without this, the entire document maps to "page 1" because
        # _generate_pseudo_toc sets chars_per_page = len(text) when
        # total_pages == 0. This causes:
        #   1. _create_batches returns a single batch covering the whole doc
        #   2. _run_chunking_round truncates that batch to max_batch_chars,
        #      so the LLM only analyzes the first ~47% of a 168K doc
        #   3. _build_chunks calls get_text_for_page_range(full_text, 1, 1),
        #      which returns the ENTIRE document (no page markers → fallback)
        #   4. The whole-document text is then sub-split by RecursiveCharacter-
        #      TextSplitter, making AgentChunker behave identically to
        #      HybridChunker — the LLM's section analysis is discarded.
        #
        # With synthetic page markers:
        #   - _generate_pseudo_toc assigns TOC entries to correct pages
        #   - _create_batches splits into N batches (one per page range)
        #   - _build_chunks uses get_text_for_page_range to extract only the
        #     relevant section's text
        if total_pages == 0 and doc_size > self.max_batch_chars:
            page_size = self.max_batch_chars
            lines = text.split("\n")
            pages: list[str] = []
            current_page: list[str] = []
            current_len = 0
            for line in lines:
                line_len = len(line) + 1  # +1 for the \n
                if current_len + line_len > page_size and current_page:
                    pages.append("\n".join(current_page))
                    current_page = [line]
                    current_len = line_len
                else:
                    current_page.append(line)
                    current_len += line_len
            if current_page:
                pages.append("\n".join(current_page))

            # Build text with page markers — reuse the same format as
            # build_text_with_page_markers so parse_page_index works.
            text = "\n\n".join(
                f"<!-- PAGE:{i + 1} -->\n{p}" for i, p in enumerate(pages)
            )
            total_pages = len(pages)
            logger.info(
                f"[AgentChunker] Non-PDF doc: inserted {total_pages} synthetic page markers "
                f"(page_size={page_size} chars) for batch splitting — "
                f"this enables per-batch LLM analysis and correct section extraction"
            )

        pipeline_start = time.time()
        trace = ChunkTrace(
            method="agent",
            model=self.model,
            num_rounds=effective_num_rounds,
            temperature=self.temperature,
            config={
                "max_batch_chars": self.max_batch_chars,
                "sub_chunk_size": self.sub_chunk_size,
                "sub_chunk_overlap": self.sub_chunk_overlap,
                "small_chunk_size": self.small_chunk_size,
                "prompt_template_overridden": self.prompt_template != _DEFAULT_AGENT_CHUNK_PROMPT,
                "prompt_system_extra": bool(self.prompt_system_extra),
                "configured_num_rounds": self.num_rounds,
                "effective_num_rounds": effective_num_rounds,
                "doc_size_chars": doc_size,
            },
        )

        # Step 1: Extract TOC
        logger.info(f"[AgentChunker] Step 1: Extracting TOC (file={file_path})")
        toc = self._extract_toc(file_path, text, total_pages)
        trace.toc_entries = len(toc)
        logger.info(f"[AgentChunker] TOC: {len(toc)} entries")
        for entry in toc[:10]:
            logger.info(f"[AgentChunker]   - {entry.get('title', '?')} (page {entry.get('start_page', '?')})")
        if len(toc) > 10:
            logger.info(f"[AgentChunker]   ... and {len(toc) - 10} more")

        # P0: Fallback for unstructured documents (no markdown headers).
        # When a document has < 2 '#'-prefixed headers, the LLM cannot
        # identify sections reliably — it tends to label everything as one
        # section, causing chunk content mixing and retrieval failures
        # (e.g., Q13 on doc 06-chaotic-embedded-notes.md). For such docs,
        # skip LLM analysis and use code-block-aware splitter instead.
        header_count = sum(
            1 for line in text.split("\n")
            if re.match(r"^#{1,4}\s+\S", line.strip())
        )
        if header_count < 2 and doc_size > 1000:
            logger.info(
                f"[AgentChunker] Fallback: only {header_count} markdown '#' headers "
                f"in {doc_size} chars — using code-block-aware splitter instead of LLM"
            )
            trace.config["fallback_reason"] = "no_structural_headers"
            trace.config["header_count"] = header_count
            chunks = self._fallback_chunk_unstructured(text, metadata, trace)
            trace.final_chunks = len(chunks)
            logger.info(f"[AgentChunker] Built {len(chunks)} chunks (fallback mode)")
            return chunks

        # Step 2: Create batches
        logger.info(f"[AgentChunker] Step 2: Creating batches")
        batches = self._create_batches(text, toc, total_pages)
        trace.num_batches = len(batches)
        logger.info(f"[AgentChunker] Batches: {len(batches)} (max_chars={self.max_batch_chars})")
        for i, (bt, sp, ep) in enumerate(batches):
            logger.info(f"[AgentChunker]   batch {i+1}: pages {sp}-{ep}, {len(bt)} chars")

        # Step 3: Run LLM chunking rounds
        logger.info(f"[AgentChunker] Step 3: Running {effective_num_rounds} LLM chunking rounds")
        all_rounds: list[list[dict]] = []
        for round_num in range(effective_num_rounds):
            round_start = time.time()
            try:
                sections = await self._run_chunking_round(batches, round_num + 1)
                all_rounds.append(sections)
                elapsed = time.time() - round_start
                round_result = RoundResult(
                    round_num=round_num + 1,
                    num_batches=len(batches),
                    sections_found=len(sections),
                    elapsed_seconds=elapsed,
                )
                trace.rounds.append(round_result.to_dict())
                logger.info(
                    f"[AgentChunker] Round {round_num + 1}/{effective_num_rounds}: "
                    f"{len(sections)} sections in {elapsed:.1f}s"
                )
            except Exception as e:
                elapsed = time.time() - round_start
                round_result = RoundResult(
                    round_num=round_num + 1,
                    num_batches=len(batches),
                    sections_found=0,
                    elapsed_seconds=elapsed,
                    error=str(e),
                )
                trace.rounds.append(round_result.to_dict())
                logger.warning(f"[AgentChunker] Round {round_num + 1} failed: {e}")
                # New-4: Only fail hard if the first round failed AND we have
                # no successful rounds to fall back on. Otherwise continue
                # with partial results so a single flaky round doesn't waste
                # the whole pipeline.
                if round_num == 0 and not all_rounds:
                    raise AgentChunkError("AGENT_CHUNK_FAILED", str(e)) from e
                logger.warning(
                    f"[AgentChunker] Round {round_num + 1} failed, continuing with "
                    f"{len(all_rounds)} successful round(s)"
                )

        if not all_rounds:
            raise AgentChunkError("AGENT_CHUNK_FAILED", f"All {effective_num_rounds} chunking rounds failed")

        # Step 4: Majority vote
        logger.info(f"[AgentChunker] Step 4: Majority vote on {len(all_rounds)} rounds")
        voted_sections = self._majority_vote(all_rounds)
        trace.voted_sections = len(voted_sections)
        trace.disputed_sections = sum(1 for s in voted_sections if s.get("boundary_disputed", False))
        logger.info(
            f"[AgentChunker] Voted: {len(voted_sections)} sections "
            f"({trace.disputed_sections} disputed)"
        )
        for s in voted_sections[:10]:
            disputed = " [DISPUTED]" if s.get("boundary_disputed") else ""
            logger.info(
                f"[AgentChunker]   - {s.get('title', '?')} "
                f"(pages {s.get('start_page', '?')}-{s.get('end_page', '?')}, "
                f"conf={s.get('confidence', '?')}){disputed}"
            )

        # Step 5: Build chunks
        logger.info(f"[AgentChunker] Step 5: Building chunks from voted sections")
        chunks = self._build_chunks(voted_sections, text, metadata, total_pages, trace)
        trace.final_chunks = len(chunks)
        logger.info(f"[AgentChunker] Built {len(chunks)} chunks")

        # P2-3: Warn if chunk count exceeds the configured limit, which
        # usually signals overly granular section detection by the LLM.
        if len(chunks) > self.max_chunks:
            logger.warning(
                f"[AgentChunker] Chunk count {len(chunks)} exceeds max_chunks={self.max_chunks}, "
                f"this may indicate overly granular section detection"
            )

        # Step 6: Verify page coverage
        if total_pages > 0:
            coverage = verify_page_coverage(chunks, total_pages)
            if coverage["missing_pages"]:
                logger.warning(
                    f"[AgentChunker] Page coverage gaps: {coverage['missing_pages']}"
                )

        trace.total_elapsed_seconds = time.time() - pipeline_start
        logger.info(
            f"[AgentChunker] Pipeline complete: {len(chunks)} chunks in "
            f"{trace.total_elapsed_seconds:.1f}s"
        )

        return chunks

    # ─── TOC extraction ──────────────────────────────────────────

    def _extract_toc(
        self,
        file_path: Optional[Path],
        text: str,
        total_pages: int,
    ) -> list[dict]:
        """Extract table of contents from PDF or generate pseudo-TOC."""
        if file_path and file_path.suffix.lower() == ".pdf":
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(file_path))
                toc = doc.get_toc()
                doc.close()
                if toc:
                    return [
                        {"level": entry[0], "title": entry[1], "start_page": entry[2]}
                        for entry in toc
                    ]
            except Exception:
                logger.info("[AgentChunker] PyMuPDF TOC extraction failed, using pseudo-TOC")
        return self._generate_pseudo_toc(text, total_pages)

    def _generate_pseudo_toc(self, text: str, total_pages: int) -> list[dict]:
        """Generate pseudo-TOC by detecting header-like lines."""
        lines = text.split("\n")
        toc: list[dict] = []
        # P1-8: For non-PDF (total_pages == 0) use len(text) so the whole
        # document maps to a single pseudo-page 1 instead of an arbitrary 3000.
        chars_per_page = (
            max(1, len(text) // max(1, total_pages))
            if total_pages > 0
            else max(1, len(text))
        )

        for i, line in enumerate(lines):
            stripped = line.strip()
            if (
                stripped
                and len(stripped) < 80
                and not stripped.endswith(".")
                and (
                    re.match(r"^(第[一二三四五六七八九十\d]+[章节])", stripped)
                    or re.match(r"^\d+(\.\d+)*\s+\S", stripped)
                    or re.match(r"^#{1,4}\s+\S", stripped)
                    # P2-5: Additional header patterns for chip-manual style
                    or re.match(r"^(TABLE|FIGURE|图|表)\s*\d+", stripped, re.IGNORECASE)
                    or re.match(r"^AN-\d+", stripped)
                    or re.match(r"^[A-Z][A-Za-z\s]{2,40}$", stripped)  # Short all-caps or title-case headings
                )
            ):
                chars_before = sum(len(l) + 1 for l in lines[:i])
                est_page = max(1, chars_before // chars_per_page + 1)
                toc.append({
                    "level": 1,
                    "title": stripped.lstrip("#").strip(),
                    "start_page": est_page,
                })

        if not toc:
            toc.append({"level": 1, "title": "Full Document", "start_page": 1})
        return toc

    # ─── Batching ────────────────────────────────────────────────

    def _create_batches(
        self, text: str, toc: list[dict], total_pages: int
    ) -> list[tuple[str, int, int]]:
        if not toc:
            return [(text, 1, total_pages or 1)]

        batches: list[tuple[str, int, int]] = []
        # P1-8: For non-PDF (total_pages == 0) use len(text) so the whole
        # document maps to a single pseudo-page instead of an arbitrary 3000.
        chars_per_page = (
            max(1, len(text) // max(1, total_pages))
            if total_pages > 0
            else max(1, len(text))
        )

        for i, entry in enumerate(toc):
            start_page = entry["start_page"]
            end_page = toc[i + 1]["start_page"] - 1 if i + 1 < len(toc) else (total_pages or start_page)
            start_char = (start_page - 1) * chars_per_page
            end_char = end_page * chars_per_page
            batch_text = text[start_char:end_char]
            if batch_text.strip():
                batches.append((batch_text, start_page, end_page))

        # New-1: Align batch boundaries to sentence/paragraph edges. If a
        # batch starts mid-sentence (not at a header or paragraph break),
        # pull the incomplete leading sentence into the previous batch so
        # the LLM receives complete context for every batch.
        if len(batches) > 1:
            aligned: list[tuple[str, int, int]] = [batches[0]]
            for batch_text, sp, ep in batches[1:]:
                prev_text, prev_sp, prev_ep = aligned[-1]
                first_line = batch_text.split("\n", 1)[0].strip()
                starts_at_boundary = (
                    not first_line
                    or first_line.startswith("#")
                    or re.match(r"^(第[一二三四五六七八九十\d]+[章节]|\d+(\.\d+)*\s)", first_line)
                    or prev_text.endswith("\n\n")
                    or prev_text.endswith((".", "!", "?", "。", "！", "？"))
                )
                if not starts_at_boundary and first_line:
                    # Find the first sentence terminator in the current batch
                    m = re.search(r"[.!?。！？]\s", batch_text)
                    if m:
                        split_pos = m.end()
                        incomplete = batch_text[:split_pos]
                        remainder = batch_text[split_pos:]
                        aligned[-1] = (prev_text + incomplete, prev_sp, prev_ep)
                        if remainder.strip():
                            aligned.append((remainder, sp, ep))
                        continue
                aligned.append((batch_text, sp, ep))
            batches = aligned

        max_chars = int(self.context_window * 0.8 * 4)
        final_batches: list[tuple[str, int, int]] = []
        for batch_text, sp, ep in batches:
            if len(batch_text) > max_chars:
                final_batches.extend(self._split_oversized_batch(batch_text, sp, ep, max_chars))
            else:
                final_batches.append((batch_text, sp, ep))
        return final_batches or [(text, 1, total_pages or 1)]

    def _split_oversized_batch(
        self, text: str, start_page: int, end_page: int, max_chars: int
    ) -> list[tuple[str, int, int]]:
        sub_batches: list[tuple[str, int, int]] = []
        total_chars = len(text)
        pages = max(1, end_page - start_page + 1)
        chars_per_page = max(1, total_chars // pages)
        current_pos = 0
        current_page = start_page
        while current_pos < total_chars:
            chunk_end = min(current_pos + max_chars, total_chars)
            chars_in_batch = chunk_end - current_pos
            pages_in_batch = max(1, chars_in_batch // chars_per_page)
            batch_end_page = min(current_page + pages_in_batch - 1, end_page)
            sub_batches.append((text[current_pos:chunk_end], current_page, batch_end_page))
            current_pos = chunk_end
            current_page = batch_end_page + 1
        return sub_batches

    # ─── LLM chunking round ─────────────────────────────────────

    async def _run_chunking_round(
        self, batches: list[tuple[str, int, int]], round_num: int
    ) -> list[dict]:
        """Run one round of LLM chunking on all batches."""
        from openai import AsyncOpenAI

        # Use default AsyncOpenAI settings — the SDK's built-in retry logic
        # (max_retries=2, exponential backoff) handles transient 504s.
        # Adding custom timeout/max_retries here previously CAUSED more 504s
        # by disabling the SDK's retry and failing too fast.
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        all_sections: list[dict] = []

        for batch_idx, (batch_text, start_page, end_page) in enumerate(batches):
            # Truncate batch text to max_batch_chars
            truncated = batch_text[:self.max_batch_chars]
            page_range = f"{start_page}-{end_page}"

            prompt = self.prompt_template.format(
                batch_num=f"{round_num}.{batch_idx + 1}",
                page_range=page_range,
                text=truncated,
            )
            if self.prompt_system_extra:
                prompt = prompt + "\n\n" + self.prompt_system_extra

            call_start = time.time()
            logger.info(
                f"[AgentChunker]   LLM call: round={round_num} batch={batch_idx + 1}/{len(batches)} "
                f"pages={page_range} prompt_len={len(prompt)}"
            )

            for attempt in range(self.max_retries + 1):
                try:
                    # P0: Use STREAMING to keep the connection alive past
                    # nginx's 60s timeout. Non-streaming calls 504 when the
                    # LLM takes >60s to generate the full response (common
                    # for 77K-char prompts that produce 5K+ completion tokens
                    # — the LLM needs ~200s to finish, but nginx closes the
                    # connection at 60s). Streaming sends tokens as they're
                    # generated, preventing idle timeouts. The proxy's nginx
                    # sees continuous data flow and keeps the connection open.
                    stream = await client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.temperature,
                        response_format={"type": "json_object"},
                        stream=True,
                        stream_options={"include_usage": True},
                    )

                    content_parts: list[str] = []
                    usage = None
                    async for event in stream:
                        if event.choices and event.choices[0].delta.content:
                            content_parts.append(event.choices[0].delta.content)
                        if getattr(event, "usage", None):
                            usage = event.usage

                    content = "".join(content_parts)
                    elapsed = time.time() - call_start
                    logger.info(
                        f"[AgentChunker]   LLM response: {len(content)} chars in {elapsed:.1f}s (streamed)"
                    )

                    # Log token usage for transparency/cost tracking
                    if usage:
                        logger.info(
                            f"[AgentChunker]   Token usage: prompt={usage.prompt_tokens}, "
                            f"completion={usage.completion_tokens}, "
                            f"total={usage.total_tokens}"
                        )

                    parsed = json.loads(content)
                    sections = parsed.get("sections", [])
                    for section in sections:
                        section["round"] = round_num
                        section["batch_idx"] = batch_idx
                        all_sections.append(section)

                    # Log section details for transparency
                    for s in sections:
                        logger.info(
                            f"[AgentChunker]     → {s.get('title', '?')} "
                            f"(pages {s.get('start_page', '?')}-{s.get('end_page', '?')}, "
                            f"conf={s.get('confidence', '?')}, code={s.get('has_code_block', '?')})"
                        )

                    break  # success
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[AgentChunker]   LLM JSON decode error "
                        f"(attempt {attempt+1}/{self.max_retries+1}, batch {batch_idx + 1}): {e}"
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise
                except Exception as e:
                    logger.warning(
                        f"[AgentChunker]   LLM call failed "
                        f"(attempt {attempt+1}/{self.max_retries+1}, batch {batch_idx + 1}): {e}"
                    )
                    if attempt < self.max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise

        return all_sections

    # ─── Majority vote ───────────────────────────────────────────

    def _majority_vote(self, all_rounds: list[list[dict]]) -> list[dict]:
        """Majority vote on section boundaries across rounds.

        Uses normalized title matching (strip whitespace, lowercase) to
        handle minor LLM output variations between rounds.

        Logging covers:
        - Number of voting agents (rounds) and their individual results
        - Vote weight distribution (currently equal weight per round)
        - Per-section vote tally: which rounds voted for/against
        - Dispute trigger conditions and resolution process
        - Final accepted/rejected decisions with rationale
        """
        num_agents = len(all_rounds)

        # Single round — no vote needed
        if num_agents == 1:
            logger.info(
                f"[AgentChunker::Vote] Single agent (round 1 only) — "
                f"no vote required, accepting {len(all_rounds[0])} sections as-is"
            )
            return all_rounds[0]

        # ── Log voting agents and their individual results ──
        logger.info(
            f"[AgentChunker::Vote] ═══ Majority Vote Start ═══"
        )
        logger.info(
            f"[AgentChunker::Vote] Voting agents: {num_agents} "
            f"(each agent = 1 LLM chunking round, equal weight=1.0)"
        )
        weight_per_agent = 1.0 / num_agents
        logger.info(
            f"[AgentChunker::Vote] Weight distribution: equal weight "
            f"{weight_per_agent:.4f} per agent (total=1.0)"
        )

        for i, round_sections in enumerate(all_rounds):
            titles = [s.get("title", "?")[:40] for s in round_sections]
            logger.info(
                f"[AgentChunker::Vote] Agent {i + 1}/{num_agents}: "
                f"found {len(round_sections)} sections: {titles}"
            )

        # ── Normalize titles and tally votes ──
        def _normalize_title(t: str) -> str:
            return re.sub(r"\s+", "", t).lower().strip()

        # P1-1: Cluster start_pages within ±2 tolerance so the same section
        # appearing at page 5 in round 1 and page 6 in round 2 is treated as
        # the same vote key (instead of strict page equality).
        title_page_clusters: dict[str, list[int]] = {}  # norm -> list of cluster centers

        def _get_page_cluster(norm: str, page: int) -> int:
            """Find existing cluster within ±2 tolerance, or create a new one."""
            clusters = title_page_clusters.setdefault(norm, [])
            for c in clusters:
                if abs(page - c) <= 2:
                    return c
            clusters.append(page)
            return page

        # Vote key is now (norm_title, page_cluster) instead of just norm_title
        title_counts: dict[tuple[str, int], int] = {}
        title_examples: dict[tuple[str, int], dict] = {}
        title_confidences: dict[tuple[str, int], list[float]] = {}
        title_rounds: dict[tuple[str, int], list[int]] = {}  # which rounds voted for this title
        title_variants: dict[tuple[str, int], list[str]] = {}  # actual title strings seen

        for round_idx, round_sections in enumerate(all_rounds):
            seen_in_round = set()
            for section in round_sections:
                raw_title = section.get("title", "")
                if not raw_title:
                    continue
                norm = _normalize_title(raw_title)
                start_page = section.get("start_page", 1)
                page_cluster = _get_page_cluster(norm, start_page)
                key = (norm, page_cluster)
                if key in seen_in_round:
                    continue
                seen_in_round.add(key)
                title_counts[key] = title_counts.get(key, 0) + 1
                # P1-2: Keep the example with highest confidence
                existing = title_examples.get(key)
                if existing is None or section.get("confidence", 0.5) > existing.get("confidence", 0.5):
                    title_examples[key] = section
                title_confidences.setdefault(key, []).append(
                    section.get("confidence", 0.5)
                )
                title_rounds.setdefault(key, []).append(round_idx + 1)
                title_variants.setdefault(key, []).append(raw_title)

        threshold = max(2, num_agents // 2 + 1)
        logger.info(
            f"[AgentChunker::Vote] Acceptance threshold: {threshold}/{num_agents} "
            f"(majority = ceil({num_agents}/2) = {threshold})"
        )

        # ── Evaluate each candidate section ──
        accepted: list[dict] = []
        rejected: list[dict] = []

        # Sort by first appearance order (start_page) for stable logging
        sorted_titles = sorted(
            title_counts.items(),
            key=lambda x: title_examples[x[0]].get("start_page", 0),
        )

        for key, count in sorted_titles:
            norm, page_cluster = key
            example = title_examples[key]
            confs = title_confidences[key]
            avg_conf = sum(confs) / len(confs)
            rounds_voted = title_rounds[key]
            rounds_not_voted = [r for r in range(1, num_agents + 1) if r not in rounds_voted]
            variants = title_variants[key]

            if count >= threshold:
                # ── Accepted ──
                is_disputed = count < num_agents
                section = dict(example)
                section["boundary_disputed"] = is_disputed
                section["avg_confidence"] = avg_conf

                # P1-1: Take MEDIAN start_page and end_page across rounds
                # (instead of using the last round's values) for stability.
                all_start_pages = []
                all_end_pages = []
                for round_idx, round_sections in enumerate(all_rounds):
                    for s in round_sections:
                        s_norm = _normalize_title(s.get("title", ""))
                        s_page = s.get("start_page", 1)
                        if s_norm == norm and abs(s_page - page_cluster) <= 2:
                            all_start_pages.append(s_page)
                            all_end_pages.append(s.get("end_page", 1))
                if all_start_pages:
                    section["start_page"] = int(statistics.median(all_start_pages))
                    section["end_page"] = int(statistics.median(all_end_pages))

                if is_disputed:
                    # ── Dispute resolution logging ──
                    logger.info(
                        f"[AgentChunker::Vote] ⚠ DISPUTED BOUNDARY: "
                        f"'{example.get('title', '?')}'"
                    )
                    logger.info(
                        f"[AgentChunker::Vote]   Dispute trigger: "
                        f"only {count}/{num_agents} agents found this section "
                        f"(threshold={threshold}, unanimity requires {num_agents})"
                    )
                    logger.info(
                        f"[AgentChunker::Vote]   Title variants seen: {variants}"
                    )
                    logger.info(
                        f"[AgentChunker::Vote]   Agents voted FOR: {rounds_voted} "
                        f"(confidences: {[f'{c:.2f}' for c in confs]})"
                    )
                    logger.info(
                        f"[AgentChunker::Vote]   Agents voted AGAINST (did not find): "
                        f"{rounds_not_voted}"
                    )
                    logger.info(
                        f"[AgentChunker::Vote]   Resolution: ACCEPTED with disputed flag "
                        f"(count {count} >= threshold {threshold}), "
                        f"avg_confidence={avg_conf:.3f}"
                    )
                    logger.info(
                        f"[AgentChunker::Vote]   Pages: "
                        f"{example.get('start_page', '?')}-{example.get('end_page', '?')}, "
                        f"keywords: {example.get('keywords', [])}"
                    )
                else:
                    # ── Unanimous acceptance ──
                    logger.info(
                        f"[AgentChunker::Vote] ✓ ACCEPTED (unanimous): "
                        f"'{example.get('title', '?')}' "
                        f"({count}/{num_agents} agents, avg_conf={avg_conf:.3f}, "
                        f"pages {example.get('start_page', '?')}-{example.get('end_page', '?')})"
                    )

                accepted.append(section)
            else:
                # ── Rejected ──
                rejected.append({
                    "title": example.get("title", "?"),
                    "count": count,
                    "threshold": threshold,
                    "rounds_voted": rounds_voted,
                    "avg_confidence": avg_conf,
                })
                logger.info(
                    f"[AgentChunker::Vote] ✗ REJECTED: "
                    f"'{example.get('title', '?')}' "
                    f"(only {count}/{num_agents} agents found it, "
                    f"threshold={threshold}, needed ≥{threshold})"
                )

        accepted.sort(key=lambda s: s.get("start_page", 0))

        # ── Vote summary ──
        logger.info(
            f"[AgentChunker::Vote] ═══ Vote Summary ═══"
        )
        logger.info(
            f"[AgentChunker::Vote] Total candidates: {len(title_counts)}, "
            f"Accepted: {len(accepted)} ({sum(1 for a in accepted if not a.get('boundary_disputed'))} unanimous, "
            f"{sum(1 for a in accepted if a.get('boundary_disputed'))} disputed), "
            f"Rejected: {len(rejected)}"
        )
        if rejected:
            logger.info(
                f"[AgentChunker::Vote] Rejected sections: "
                f"{[r['title'][:30] for r in rejected]}"
            )
        logger.info(
            f"[AgentChunker::Vote] Decision basis: "
            f"normalized title matching (case-insensitive, whitespace-insensitive), "
            f"equal weight per round, threshold = ceil({num_agents}/2) = {threshold}"
        )
        logger.info(
            f"[AgentChunker::Vote] ═══ Majority Vote End ═══"
        )

        return accepted

    # ─── Build chunks ────────────────────────────────────────────

    def _build_chunks(
        self,
        sections: list[dict],
        full_text: str,
        metadata: dict,
        total_pages: int,
        trace: ChunkTrace,
    ) -> list[ChunkResult]:
        """Build ChunkResult list from voted sections.

        Uses code-block-safe sub-splitting: fenced code blocks are protected
        with placeholders before RecursiveCharacterTextSplitter runs, then
        restored. Tiny chunks (<100 chars) are absorbed into adjacent chunks.
        """
        chunks: list[ChunkResult] = []
        # P1-7: Store only a trace summary in chunk metadata (full trace stays
        # on the `trace` object for logging) to keep chunk payloads small.
        # IMPORTANT: ChromaDB metadata only supports flat scalar values
        # (str/int/float/bool/None/list), NOT nested dicts. Serialize the
        # trace summary as a JSON string so it can be stored in ChromaDB.
        trace_summary = {
            "method": trace.method,
            "model": trace.model,
            "num_rounds": trace.num_rounds,
            "voted_sections": trace.voted_sections,
            "disputed_sections": trace.disputed_sections,
            "total_elapsed_seconds": round(trace.total_elapsed_seconds, 2),
        }
        trace_summary_json = json.dumps(trace_summary, ensure_ascii=False)

        for i, section in enumerate(sections):
            start_page = section.get("start_page", 1)
            end_page = section.get("end_page", start_page)
            title = section.get("title", f"Section {i + 1}")
            summary = section.get("summary", "")
            keywords = section.get("keywords", [])
            has_code = section.get("has_code_block", False)
            disputed = section.get("boundary_disputed", False)
            confidence = section.get("avg_confidence", section.get("confidence", 0.5))

            # P0-3: Use base utilities to reverse-calculate section text from
            # page numbers instead of a naive chars_per_page heuristic that
            # drifts when actual page sizes vary. This respects page markers
            # embedded in full_text by the upstream PDF loader.
            section_text = get_text_for_page_range(full_text, start_page, end_page)
            # #14: Strip page markers from chunk text so they never leak into
            # embeddings or user-visible output.
            section_text = strip_page_markers(section_text).strip()
            if not section_text.strip():
                logger.warning(
                    f"[AgentChunker::Build] Section {i + 1} '{title}': "
                    f"empty text (pages {start_page}-{end_page}), skipping"
                )
                continue

            logger.info(
                f"[AgentChunker::Build] Section {i + 1}/{len(sections)}: "
                f"'{title}' ({len(section_text)} chars, pages {start_page}-{end_page}, "
                f"code={has_code}, disputed={disputed}, conf={confidence:.2f})"
            )

            # Check if the entire section is a single code block
            is_whole_code_block = (
                section_text.strip().startswith("```")
                and section_text.strip().endswith("```")
            )

            if is_whole_code_block:
                logger.info(
                    f"[AgentChunker::Build]   → Whole code block detected "
                    f"({len(section_text)} chars), keeping as single chunk (no sub-split)"
                )
                chunk_meta = {
                    **metadata,
                    "chunk_index": len(chunks),
                    "section_title": title,
                    "section_summary": summary,
                    "section_keywords": keywords,
                    "boundary_disputed": disputed,
                    "section_confidence": confidence,
                    "has_code_block": True,
                    "is_code_block": True,
                    "chunk_size": len(section_text),
                    "agent_trace": trace_summary_json,
                }
                chunks.append(ChunkResult(
                    text=section_text,
                    metadata=chunk_meta,
                    page_range=(start_page, end_page),
                    fingerprint=compute_fingerprint(section_text),
                    chunk_method="agent",
                    section_title=title,
                ))
                continue

            # Protect inline code blocks with placeholders before sub-splitting
            code_map: dict[str, str] = {}

            def _stash_code(m: re.Match) -> str:
                # P2-4: Use a UUID-based placeholder so collisions are
                # impossible even if a previous restore left a stale key.
                key = f"\x00CB{uuid.uuid4().hex[:8]}\x00"
                code_map[key] = m.group(0)
                return key

            placeholder_text = _INLINE_CODE_RE.sub(_stash_code, section_text)
            if code_map:
                logger.info(
                    f"[AgentChunker::Build]   → Protected {len(code_map)} inline code block(s) "
                    f"with placeholders before sub-splitting"
                )
            sub_chunks = self._sub_splitter.split_text(placeholder_text)
            logger.info(
                f"[AgentChunker::Build]   → Sub-split into {len(sub_chunks)} chunks "
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
                    "boundary_disputed": disputed,
                    "section_confidence": confidence,
                    "has_code_block": has_code,
                    "is_code_block": False,
                    "chunk_size": len(sub_text),
                    "agent_trace": trace_summary_json,
                }
                chunks.append(ChunkResult(
                    text=sub_text,
                    metadata=chunk_meta,
                    page_range=(start_page, end_page),
                    fingerprint=compute_fingerprint(sub_text),
                    chunk_method="agent",
                    section_title=title,
                ))

        # Absorb tiny non-code chunks into adjacent same-section chunks
        chunks = self._merge_tiny_chunks(chunks)

        # P0: Deduplicate by fingerprint. For non-PDF docs with synthetic
        # page markers, ALL sections on the same page get the same text via
        # get_text_for_page_range(), producing massive duplication (e.g.,
        # 92 sections × 80 sub-chunks = 7360 chunks, but only ~160 unique).
        # Fingerprint dedup collapses these to the unique set.
        if chunks:
            seen_fps: set[str] = set()
            unique: list[ChunkResult] = []
            dup_count = 0
            for c in chunks:
                if c.fingerprint not in seen_fps:
                    seen_fps.add(c.fingerprint)
                    unique.append(c)
                else:
                    dup_count += 1
            if dup_count:
                logger.info(
                    f"[AgentChunker::Build] Deduplicated {dup_count} duplicate chunks "
                    f"({len(chunks)} → {len(unique)})"
                )
                # Re-index chunk_index after dedup
                for i, c in enumerate(unique):
                    c.metadata["chunk_index"] = i
                chunks = unique

        # P0: Enforce max_chunks by truncating (was warning-only).
        # Excess chunks usually means the LLM over-segmented or page-level
        # text extraction produced too many sub-chunks.
        if len(chunks) > self.max_chunks:
            logger.warning(
                f"[AgentChunker] Chunk count {len(chunks)} exceeds max_chunks={self.max_chunks}, "
                f"truncating to first {self.max_chunks}"
            )
            chunks = chunks[:self.max_chunks]

        return chunks

    # ─── Tiny chunk absorption ───────────────────────────────────

    # Pattern for filtering pure-symbol chunks (markdown horizontal rules,
    # table separators, etc.) that carry no semantic value.
    _SYMBOL_PATTERN = re.compile(r'^[\s\-_=*#|+.:`\s]+$')

    def _merge_tiny_chunks(
        self, chunks: list[ChunkResult], threshold: int = 100
    ) -> list[ChunkResult]:
        """Merge non-code chunks shorter than threshold into adjacent chunks
        from the same section. Code blocks are always preserved as-is.

        Two-pass merge (kept in sync with HybridChunker):
        - Pass 1 (backward): absorb tiny chunk into the PREVIOUS same-section
          chunk.
        - Pass 2 (forward): if a tiny chunk couldn't be merged backward
          (e.g. it's the first chunk of a section), absorb it into the NEXT
          same-section chunk (including code blocks, so a tiny intro before
          a code block stays with the code).

        Also filters out pure-symbol chunks (---, ===, |---|) that carry
        no semantic meaning and pollute retrieval.
        """
        if len(chunks) <= 1:
            return chunks

        # ── Pre-filter: drop pure-symbol chunks ──
        filtered: list[ChunkResult] = []
        dropped_count = 0
        for chunk in chunks:
            is_code = chunk.metadata.get("is_code_block", False)
            stripped = chunk.text.strip()
            if (not is_code and stripped and len(stripped) < 50
                    and self._SYMBOL_PATTERN.match(stripped)):
                dropped_count += 1
                continue
            filtered.append(chunk)
        chunks = filtered
        if len(chunks) <= 1:
            if dropped_count > 0:
                logger.info(
                    f"[AgentChunker::Merge] Dropped {dropped_count} pure-symbol chunk(s)"
                )
            return chunks

        merge_count = 0

        # ── Pass 1: Backward merge (tiny → previous same-section) ──
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
                    )
                    merge_count += 1
                    continue
            merged.append(chunk)

        # ── Pass 2: Forward merge (tiny → next same-section, incl. code) ──
        if len(merged) > 1:
            final: list[ChunkResult] = []
            pending_tiny: Optional[ChunkResult] = None

            for i, chunk in enumerate(merged):
                is_code = chunk.metadata.get("is_code_block", False)
                is_tiny = len(chunk.text) < threshold

                if pending_tiny is not None:
                    same_section = pending_tiny.section_title == chunk.section_title
                    if same_section:
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
                    if next_same_section:
                        pending_tiny = chunk
                        continue

                final.append(chunk)

            if pending_tiny is not None:
                final.append(pending_tiny)

            merged = final

        # Renumber chunk_index
        for i, chunk in enumerate(merged):
            chunk.metadata["chunk_index"] = i

        if merge_count > 0 or dropped_count > 0:
            logger.info(
                f"[AgentChunker::Merge] Merged {merge_count} tiny chunk(s), "
                f"dropped {dropped_count} symbol-only chunk(s): "
                f"{len(chunks) + dropped_count} → {len(merged)} chunks"
            )
        return merged

    # ─── Fallback for unstructured docs ──────────────────────────

    def _fallback_chunk_unstructured(
        self, text: str, metadata: dict, trace: ChunkTrace
    ) -> list[ChunkResult]:
        """Fallback for docs without markdown headers: code-block-aware split.

        Triggered when header_count < 2 (no structural '#'-headers). Instead
        of asking the LLM to identify sections (which fails on unstructured
        text — it labels everything as one section), this method:

        1. Splits text by code fences (```...```)
        2. Keeps each code block intact as a single chunk
        3. Splits non-code text with RecursiveCharacterTextSplitter
        4. Assigns meaningful section_title from nearby content keywords
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.sub_chunk_size,
            chunk_overlap=self.sub_chunk_overlap,
            separators=["\n\n", "\n", "。", ".", "；", ";", "，", ",", " ", ""],
        )

        # Split by code blocks — keep the fences attached
        parts = re.split(r"(```[\s\S]*?```)", text)
        chunks: list[ChunkResult] = []
        chunk_idx = 0

        # Track preceding text context for code block titles
        last_text_preview = ""

        for part in parts:
            if not part.strip():
                continue

            is_code = part.startswith("```") and part.endswith("```")

            if is_code:
                # Extract language hint
                first_line = part.split("\n", 1)[0].strip("`").strip()
                lang = first_line if first_line else "code"

                # Try to guess what the code is about from preceding text
                # e.g., "下面这段 STM32 SPI1 主机初始化代码" → "SPI1 主机初始化"
                title_hint = ""
                for kw_line in last_text_preview.split("\n")[-3:]:
                    kw_line = kw_line.strip()
                    if kw_line and len(kw_line) < 80:
                        # Look for "下面这段" or "以下代码" patterns
                        m = re.search(
                            r"(?:下面这段|以下这段|下面这段代码|这段代码|以下代码|示例代码)(.{2,50})",
                            kw_line,
                        )
                        if m:
                            title_hint = m.group(1).strip("，。的是：")
                            break

                if title_hint:
                    title = f"Code: {title_hint} ({lang})"
                else:
                    title = f"Code block ({lang})"

                chunk_meta = {
                    **metadata,
                    "chunk_index": chunk_idx,
                    "section_title": title,
                    "is_code_block": True,
                    "has_code_block": True,
                    "chunk_size": len(part),
                    "agent_trace": json.dumps({
                        "fallback": True,
                        "reason": "no_structural_headers",
                        "lang": lang,
                    }, ensure_ascii=False),
                }
                chunks.append(ChunkResult(
                    text=part,
                    metadata=chunk_meta,
                    page_range=(1, 1),
                    fingerprint=compute_fingerprint(part),
                    chunk_method="agent",
                    section_title=title,
                ))
                chunk_idx += 1
                last_text_preview = ""  # reset after code block
            else:
                # Non-code text: split with RecursiveCharacterTextSplitter
                sub_texts = splitter.split_text(part)
                last_text_preview = part  # save for next code block's title
                for st in sub_texts:
                    if not st.strip():
                        continue
                    # Generate title from first meaningful line
                    first_line = st.split("\n")[0].strip()
                    if len(first_line) > 60:
                        first_line = first_line[:60] + "..."
                    title = first_line if first_line else "Text segment"

                    chunk_meta = {
                        **metadata,
                        "chunk_index": chunk_idx,
                        "section_title": title,
                        "is_code_block": False,
                        "has_code_block": False,
                        "chunk_size": len(st),
                        "agent_trace": json.dumps({
                            "fallback": True,
                            "reason": "no_structural_headers",
                        }, ensure_ascii=False),
                    }
                    chunks.append(ChunkResult(
                        text=st,
                        metadata=chunk_meta,
                        page_range=(1, 1),
                        fingerprint=compute_fingerprint(st),
                        chunk_method="agent",
                        section_title=title,
                    ))
                    chunk_idx += 1

        logger.info(
            f"[AgentChunker::Fallback] Initial: {len(chunks)} chunks "
            f"({sum(1 for c in chunks if c.metadata.get('is_code_block'))} code, "
            f"{sum(1 for c in chunks if not c.metadata.get('is_code_block'))} text)"
        )

        # Merge tiny chunks (same as main pipeline)
        chunks = self._merge_tiny_chunks(chunks)

        # Deduplicate by fingerprint (same as main pipeline)
        if chunks:
            seen_fps: set[str] = set()
            unique: list[ChunkResult] = []
            dup_count = 0
            for c in chunks:
                if c.fingerprint not in seen_fps:
                    seen_fps.add(c.fingerprint)
                    unique.append(c)
                else:
                    dup_count += 1
            if dup_count:
                logger.info(
                    f"[AgentChunker::Fallback] Deduplicated {dup_count} duplicates "
                    f"({len(chunks)} → {len(unique)})"
                )
                chunks = unique

        # Enforce max_chunks
        if len(chunks) > self.max_chunks:
            logger.warning(
                f"[AgentChunker::Fallback] Chunk count {len(chunks)} > max_chunks={self.max_chunks}, "
                f"truncating"
            )
            chunks = chunks[:self.max_chunks]

        # Re-index
        for i, c in enumerate(chunks):
            c.metadata["chunk_index"] = i

        return chunks


class AgentChunkError(Exception):
    """Raised when agent chunking fails."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
