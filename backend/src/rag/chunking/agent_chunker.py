"""Agent chunker — LLM-driven semantic chunking with 3-round voting."""

import json
import logging
from pathlib import Path
from typing import Optional

from src.rag.chunking.base import (
    ChunkResult,
    BaseChunker,
    compute_fingerprint,
    verify_page_coverage,
)

logger = logging.getLogger(__name__)

_AGENT_CHUNK_PROMPT = """You are a document structure analyst. Analyze the following document text and identify logical sections/chapters.

For each section, output:
- start_page: approximate starting page number
- end_page: approximate ending page number
- title: concise section title (in the document's language)
- summary: one-sentence summary of the section's content

Output a JSON object with a "sections" array. Example:
{"sections": [{"start_page": 1, "end_page": 3, "title": "GPIO Configuration", "summary": "GPIO push-pull/open-drain modes"}]}

Document text (batch {batch_num}):
---
{text}
---

Output ONLY the JSON object, no additional text."""


class AgentChunker(BaseChunker):
    """
    LLM-driven chunking for complex documents.

    Flow:
    1. Extract TOC/bookmarks (PyMuPDF) or detect headers by font size
    2. Send document in batches to LLM for semantic section detection
    3. Run 3 independent rounds → majority vote on boundaries
    4. Verify page coverage
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        context_window: int = 256000,
        max_retries: int = 3,
        temperature: float = 0.0,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.context_window = context_window
        self.max_retries = max_retries
        self.temperature = temperature

    async def chunk(
        self,
        text: str,
        metadata: dict,
        file_path: Optional[Path] = None,
        total_pages: int = 0,
    ) -> list[ChunkResult]:
        """Execute agent chunking pipeline."""
        if not self.api_key:
            raise AgentChunkError(
                "AGENT_CHUNK_FAILED",
                "Agent chunker requires an API key. Configure it in RAG settings or KB settings.",
            )

        # Step 1: Extract TOC or generate pseudo-TOC
        toc = self._extract_toc(file_path, text, total_pages)

        # Step 2: Create batches from TOC
        batches = self._create_batches(text, toc, total_pages)

        # Step 3: Run 3 rounds of LLM chunking
        all_rounds: list[list[dict]] = []
        for round_num in range(3):
            try:
                sections = await self._run_chunking_round(batches, round_num + 1)
                all_rounds.append(sections)
            except Exception as e:
                logger.warning(f"Agent chunking round {round_num + 1} failed: {e}")
                if round_num == 0:
                    raise AgentChunkError("AGENT_CHUNK_FAILED", str(e)) from e

        if not all_rounds:
            raise AgentChunkError("AGENT_CHUNK_FAILED", "All 3 chunking rounds failed")

        # Step 4: Majority vote on boundaries
        voted_sections = self._majority_vote(all_rounds)

        # Step 5: Build ChunkResult from voted sections
        chunks = self._build_chunks(voted_sections, text, metadata, total_pages)

        # Step 6: Verify page coverage
        if total_pages > 0:
            coverage = verify_page_coverage(chunks, total_pages)
            if coverage["missing_pages"]:
                raise AgentChunkError(
                    "AGENT_CHUNK_INCOMPLETE",
                    f"Agent chunking missing pages: {coverage['missing_pages']}. "
                    f"Covered {len(coverage['covered_pages'])}/{total_pages} pages.",
                )

        logger.info(f"Agent chunking complete: {len(chunks)} chunks from {len(voted_sections)} sections")
        return chunks

    def _extract_toc(
        self,
        file_path: Optional[Path],
        text: str,
        total_pages: int,
    ) -> list[dict]:
        """Extract table of contents from PDF or generate pseudo-TOC."""
        # Try PyMuPDF for real TOC
        if file_path and file_path.suffix.lower() == ".pdf":
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(str(file_path))
                toc = doc.get_toc()
                doc.close()

                if toc:
                    return [
                        {
                            "level": entry[0],
                            "title": entry[1],
                            "start_page": entry[2],
                        }
                        for entry in toc
                    ]
            except Exception:
                logger.info("PyMuPDF TOC extraction failed, falling back to pseudo-TOC")

        # Pseudo-TOC: detect headers by text patterns
        return self._generate_pseudo_toc(text, total_pages)

    def _generate_pseudo_toc(self, text: str, total_pages: int) -> list[dict]:
        """Generate pseudo-TOC by detecting header-like lines."""
        import re

        lines = text.split("\n")
        toc: list[dict] = []
        current_page = 1
        chars_per_page = max(1, len(text) // max(1, total_pages)) if total_pages > 0 else 3000

        for i, line in enumerate(lines):
            stripped = line.strip()
            # Detect headers: short lines, possibly with numbering
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
                # Estimate page number
                chars_before = sum(len(l) + 1 for l in lines[:i])
                est_page = max(1, chars_before // chars_per_page + 1)
                toc.append({
                    "level": 1,
                    "title": stripped.lstrip("#").strip(),
                    "start_page": est_page,
                })

        if not toc:
            # Fallback: single section
            toc.append({
                "level": 1,
                "title": "Full Document",
                "start_page": 1,
            })

        return toc

    def _create_batches(
        self,
        text: str,
        toc: list[dict],
        total_pages: int,
    ) -> list[tuple[str, int, int]]:
        """
        Create batches from text based on TOC.

        Returns list of (batch_text, start_page, end_page).
        """
        if not toc:
            return [(text, 1, total_pages or 1)]

        batches: list[tuple[str, int, int]] = []
        chars_per_page = max(1, len(text) // max(1, total_pages)) if total_pages > 0 else 3000

        for i, entry in enumerate(toc):
            start_page = entry["start_page"]
            end_page = toc[i + 1]["start_page"] - 1 if i + 1 < len(toc) else (total_pages or start_page)

            # Extract text for this page range
            start_char = (start_page - 1) * chars_per_page
            end_char = end_page * chars_per_page
            batch_text = text[start_char:end_char]

            if batch_text.strip():
                batches.append((batch_text, start_page, end_page))

        # Check batch sizes against context window
        # Rough estimate: 1 token ≈ 4 chars
        max_chars = int(self.context_window * 0.8 * 4)

        final_batches: list[tuple[str, int, int]] = []
        for batch_text, sp, ep in batches:
            if len(batch_text) > max_chars:
                # Split oversized batch
                sub_batches = self._split_oversized_batch(batch_text, sp, ep, max_chars)
                final_batches.extend(sub_batches)
            else:
                final_batches.append((batch_text, sp, ep))

        return final_batches or [(text, 1, total_pages or 1)]

    def _split_oversized_batch(
        self,
        text: str,
        start_page: int,
        end_page: int,
        max_chars: int,
    ) -> list[tuple[str, int, int]]:
        """Split an oversized batch into smaller sub-batches."""
        sub_batches: list[tuple[str, int, int]] = []
        total_chars = len(text)
        pages = max(1, end_page - start_page + 1)
        chars_per_page = max(1, total_chars // pages)

        current_pos = 0
        current_page = start_page

        while current_pos < total_chars:
            chunk_end = min(current_pos + max_chars, total_chars)
            # Estimate end page
            chars_in_batch = chunk_end - current_pos
            pages_in_batch = max(1, chars_in_batch // chars_per_page)
            batch_end_page = min(current_page + pages_in_batch - 1, end_page)

            sub_batches.append((
                text[current_pos:chunk_end],
                current_page,
                batch_end_page,
            ))

            current_pos = chunk_end
            current_page = batch_end_page + 1

        return sub_batches

    async def _run_chunking_round(
        self,
        batches: list[tuple[str, int, int]],
        round_num: int,
    ) -> list[dict]:
        """Run one round of LLM chunking on all batches."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        all_sections: list[dict] = []

        for batch_text, start_page, end_page in batches:
            prompt = _AGENT_CHUNK_PROMPT.format(
                batch_num=f"{round_num}",
                text=batch_text[:80000],  # Cap to avoid token overflow
            )

            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                    response_format={"type": "json_object"},
                )

                content = response.choices[0].message.content
                parsed = json.loads(content)

                sections = parsed.get("sections", [])
                for section in sections:
                    section["round"] = round_num
                    all_sections.append(section)

            except Exception as e:
                logger.warning(f"LLM chunking failed for batch in round {round_num}: {e}")
                raise

        return all_sections

    def _majority_vote(self, all_rounds: list[list[dict]]) -> list[dict]:
        """
        Majority vote on section boundaries across rounds.

        Sections that appear in 2+ rounds are accepted.
        Disputed boundaries are flagged.
        """
        if len(all_rounds) == 1:
            return all_rounds[0]

        # Collect all unique section titles
        title_counts: dict[str, int] = {}
        title_examples: dict[str, dict] = {}

        for round_sections in all_rounds:
            seen_in_round = set()
            for section in round_sections:
                title = section.get("title", "")
                if title and title not in seen_in_round:
                    title_counts[title] = title_counts.get(title, 0) + 1
                    title_examples[title] = section
                    seen_in_round.add(title)

        # Accept sections appearing in majority of rounds
        threshold = max(2, len(all_rounds) // 2 + 1)
        accepted: list[dict] = []

        for title, count in title_counts.items():
            if count >= threshold:
                section = dict(title_examples[title])
                section["boundary_disputed"] = count < len(all_rounds)
                accepted.append(section)

        # Sort by start_page
        accepted.sort(key=lambda s: s.get("start_page", 0))

        return accepted

    def _build_chunks(
        self,
        sections: list[dict],
        full_text: str,
        metadata: dict,
        total_pages: int,
    ) -> list[ChunkResult]:
        """Build ChunkResult list from voted sections."""
        chunks: list[ChunkResult] = []
        chars_per_page = max(1, len(full_text) // max(1, total_pages)) if total_pages > 0 else 3000

        for i, section in enumerate(sections):
            start_page = section.get("start_page", 1)
            end_page = section.get("end_page", start_page)
            title = section.get("title", f"Section {i + 1}")
            summary = section.get("summary", "")
            disputed = section.get("boundary_disputed", False)

            # Extract text for this page range
            start_char = (start_page - 1) * chars_per_page
            end_char = end_page * chars_per_page
            section_text = full_text[start_char:end_char]

            if not section_text.strip():
                continue

            # Further split if section is too large
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200,
                separators=["\n## ", "\n### ", "\n\n", "\n", "。", ".", " ", ""],
                length_function=len,
            )
            sub_chunks = splitter.split_text(section_text)

            for j, sub_text in enumerate(sub_chunks):
                if not sub_text.strip():
                    continue

                chunk_meta = {
                    **metadata,
                    "chunk_index": len(chunks),
                    "section_title": title,
                    "section_summary": summary,
                    "boundary_disputed": disputed,
                    "chunk_size": len(sub_text),
                }

                chunks.append(ChunkResult(
                    text=sub_text,
                    metadata=chunk_meta,
                    page_range=(start_page, end_page),
                    fingerprint=compute_fingerprint(sub_text),
                    chunk_method="agent",
                    section_title=title,
                ))

        return chunks


class AgentChunkError(Exception):
    """Raised when agent chunking fails."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
