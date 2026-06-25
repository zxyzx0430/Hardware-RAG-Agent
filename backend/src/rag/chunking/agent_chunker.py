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


# Separators for sub-splitting — same as HybridChunker.
# Note: "\n```\n" is absent because code blocks are protected with placeholders.
_SUB_SPLIT_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", "。", ".", " ", ""]

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
        temperature: float = 0.0,
        # New configurable parameters:
        num_rounds: int = 3,
        max_batch_chars: int = 80000,
        sub_chunk_size: int = 1000,
        sub_chunk_overlap: int = 0,
        small_chunk_size: int = 500,
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

        pipeline_start = time.time()
        trace = ChunkTrace(
            method="agent",
            model=self.model,
            num_rounds=self.num_rounds,
            temperature=self.temperature,
            config={
                "max_batch_chars": self.max_batch_chars,
                "sub_chunk_size": self.sub_chunk_size,
                "sub_chunk_overlap": self.sub_chunk_overlap,
                "small_chunk_size": self.small_chunk_size,
                "prompt_template_overridden": self.prompt_template != _DEFAULT_AGENT_CHUNK_PROMPT,
                "prompt_system_extra": bool(self.prompt_system_extra),
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

        # Step 2: Create batches
        logger.info(f"[AgentChunker] Step 2: Creating batches")
        batches = self._create_batches(text, toc, total_pages)
        trace.num_batches = len(batches)
        logger.info(f"[AgentChunker] Batches: {len(batches)} (max_chars={self.max_batch_chars})")
        for i, (bt, sp, ep) in enumerate(batches):
            logger.info(f"[AgentChunker]   batch {i+1}: pages {sp}-{ep}, {len(bt)} chars")

        # Step 3: Run LLM chunking rounds
        logger.info(f"[AgentChunker] Step 3: Running {self.num_rounds} LLM chunking rounds")
        all_rounds: list[list[dict]] = []
        for round_num in range(self.num_rounds):
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
                    f"[AgentChunker] Round {round_num + 1}/{self.num_rounds}: "
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
                if round_num == 0:
                    raise AgentChunkError("AGENT_CHUNK_FAILED", str(e)) from e

        if not all_rounds:
            raise AgentChunkError("AGENT_CHUNK_FAILED", f"All {self.num_rounds} chunking rounds failed")

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
        chars_per_page = max(1, len(text) // max(1, total_pages)) if total_pages > 0 else 3000

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
        chars_per_page = max(1, len(text) // max(1, total_pages)) if total_pages > 0 else 3000

        for i, entry in enumerate(toc):
            start_page = entry["start_page"]
            end_page = toc[i + 1]["start_page"] - 1 if i + 1 < len(toc) else (total_pages or start_page)
            start_char = (start_page - 1) * chars_per_page
            end_char = end_page * chars_per_page
            batch_text = text[start_char:end_char]
            if batch_text.strip():
                batches.append((batch_text, start_page, end_page))

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

            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                )

                content = response.choices[0].message.content
                elapsed = time.time() - call_start
                logger.info(
                    f"[AgentChunker]   LLM response: {len(content)} chars in {elapsed:.1f}s"
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

            except json.JSONDecodeError as e:
                logger.warning(
                    f"[AgentChunker]   LLM returned invalid JSON (batch {batch_idx + 1}): {e}"
                )
                raise
            except Exception as e:
                logger.warning(
                    f"[AgentChunker]   LLM call failed (batch {batch_idx + 1}): {e}"
                )
                raise

        return all_sections

    # ─── Majority vote ───────────────────────────────────────────

    def _majority_vote(self, all_rounds: list[list[dict]]) -> list[dict]:
        """Majority vote on section boundaries across rounds.

        Uses normalized title matching (strip whitespace, lowercase) to
        handle minor LLM output variations between rounds.
        """
        if len(all_rounds) == 1:
            return all_rounds[0]

        def _normalize_title(t: str) -> str:
            return re.sub(r"\s+", "", t).lower().strip()

        title_counts: dict[str, int] = {}
        title_examples: dict[str, dict] = {}
        title_confidences: dict[str, list[float]] = {}

        for round_sections in all_rounds:
            seen_in_round = set()
            for section in round_sections:
                raw_title = section.get("title", "")
                if not raw_title:
                    continue
                norm = _normalize_title(raw_title)
                if norm in seen_in_round:
                    continue
                seen_in_round.add(norm)
                title_counts[norm] = title_counts.get(norm, 0) + 1
                title_examples[norm] = section
                title_confidences.setdefault(norm, []).append(
                    section.get("confidence", 0.5)
                )

        threshold = max(2, len(all_rounds) // 2 + 1)
        accepted: list[dict] = []

        for norm, count in title_counts.items():
            if count >= threshold:
                section = dict(title_examples[norm])
                section["boundary_disputed"] = count < len(all_rounds)
                # Average confidence across rounds
                confs = title_confidences[norm]
                section["avg_confidence"] = sum(confs) / len(confs)
                accepted.append(section)

        accepted.sort(key=lambda s: s.get("start_page", 0))
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
        chars_per_page = max(1, len(full_text) // max(1, total_pages)) if total_pages > 0 else 3000
        trace_dict = trace.to_dict()

        for i, section in enumerate(sections):
            start_page = section.get("start_page", 1)
            end_page = section.get("end_page", start_page)
            title = section.get("title", f"Section {i + 1}")
            summary = section.get("summary", "")
            keywords = section.get("keywords", [])
            has_code = section.get("has_code_block", False)
            disputed = section.get("boundary_disputed", False)
            confidence = section.get("avg_confidence", section.get("confidence", 0.5))

            start_char = (start_page - 1) * chars_per_page
            end_char = end_page * chars_per_page
            section_text = full_text[start_char:end_char]
            if not section_text.strip():
                continue

            # Check if the entire section is a single code block
            is_whole_code_block = (
                section_text.strip().startswith("```")
                and section_text.strip().endswith("```")
            )

            if is_whole_code_block:
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
                    "agent_trace": trace_dict,
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
                key = f"\x00CB{len(code_map)}\x00"
                code_map[key] = m.group(0)
                return key

            placeholder_text = _INLINE_CODE_RE.sub(_stash_code, section_text)
            sub_chunks = self._sub_splitter.split_text(placeholder_text)

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
                    "agent_trace": trace_dict,
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
        return chunks

    # ─── Tiny chunk absorption ───────────────────────────────────

    def _merge_tiny_chunks(
        self, chunks: list[ChunkResult], threshold: int = 100
    ) -> list[ChunkResult]:
        """Merge non-code chunks shorter than threshold into adjacent chunks
        from the same section. Code blocks are always preserved as-is."""
        if len(chunks) <= 1:
            return chunks

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
                    continue
            merged.append(chunk)

        # Renumber chunk_index
        for i, chunk in enumerate(merged):
            chunk.metadata["chunk_index"] = i
        return merged


class AgentChunkError(Exception):
    """Raised when agent chunking fails."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
