"""Hybrid chunker — structural split + recursive character split + small-to-big mapping."""

import re
import logging
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

# Separators for the small splitter. Note: "\n```\n" is intentionally
# absent — inline code blocks are protected with placeholders before
# splitting (see chunk()), so the splitter never sees ``` markers.
# This prevents fragmentation at code block boundaries, which produced
# dozens of 30-100 char contextless chunks between inline code snippets.
_DEFAULT_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", "。", ".", " ", ""]

# Pattern for inline fenced code blocks (used by placeholder protection)
_INLINE_CODE_RE = re.compile(r"```[\s\S]*?```")


class HybridChunker(BaseChunker):
    """
    Hybrid chunking strategy:

    1. Structural split: Markdown headers / blank lines / paragraph boundaries
    2. Recursive character split: oversized blocks → fine-grained chunks
    3. Small-to-big mapping: small chunks for retrieval, big chunks for LLM context
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        small_chunk_size: int = 500,
        separators: Optional[list[str]] = None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.small_chunk_size = small_chunk_size
        self.separators = separators or _DEFAULT_SEPARATORS

        # Single splitter: overlap=0 to eliminate duplicate chunks at boundaries.
        # The old design ran _splitter(1000/200) then _small_splitter(500/100) on
        # each sub-chunk, which re-chunked the 200-char overlap regions and
        # produced near-duplicate chunks. Now small_splitter cuts the section
        # directly, and big_chunk_text = full section (see chunk()).
        self._small_splitter = RecursiveCharacterTextSplitter(
            chunk_size=small_chunk_size,
            chunk_overlap=0,
            separators=self.separators,
            length_function=len,
        )

    async def chunk(
        self,
        text: str,
        metadata: dict,
        file_path: Optional[Path] = None,
        total_pages: int = 0,
    ) -> list[ChunkResult]:
        """Execute hybrid chunking pipeline (small-first then aggregate to big)."""
        # Step 1: Structural split
        sections = self._structural_split(text, file_path)

        # Step 2: Small-first chunking — small_splitter cuts each section directly.
        # big_chunk_text = full section text (truncated to 4000), so retrieval uses
        # the small chunk while LLM generation gets the complete structural unit.
        all_chunks: list[ChunkResult] = []
        chunk_index = 0

        for section_title, section_text, section_pages in sections:
            # Fenced code blocks are atomic: never invoke the small splitter
            # on them. RecursiveCharacterTextSplitter would fragment file
            # trees / code examples / configs into meaningless line groups
            # and produce overlapping chunks at piece-boundary rollback.
            # Keeping the code block whole preserves its structure; the
            # embedding is computed over the entire block, which is fine
            # because code blocks are self-contained semantic units.
            is_code_block = (
                section_text.startswith("```") and section_text.endswith("```")
            )

            if is_code_block:
                chunk_text = section_text
                chunk_meta = {
                    **metadata,
                    "chunk_index": chunk_index,
                    "section_title": section_title,
                    "small_chunk_id": f"{metadata.get('doc_id', 'unknown')}#s{chunk_index}",
                    "big_chunk_text": section_text[:4000],
                    "chunk_size": len(section_text),
                    "is_code_block": True,
                }
                all_chunks.append(ChunkResult(
                    text=chunk_text,
                    metadata=chunk_meta,
                    page_range=section_pages,
                    fingerprint=compute_fingerprint(chunk_text),
                    chunk_method="hybrid",
                    section_title=section_title,
                ))
                chunk_index += 1
                continue

            # Protect inline code blocks with placeholders so the small
            # splitter doesn't fragment at ``` boundaries. Without this,
            # the "\n\n" separator would cut between code blocks and
            # their surrounding text, producing many tiny contextless
            # chunks (e.g. "响应：`{ok: true}`" as a standalone chunk).
            code_map: dict[str, str] = {}

            def _stash_code(m: re.Match) -> str:
                key = f"\x00CB{len(code_map)}\x00"
                code_map[key] = m.group(0)
                return key

            placeholder_text = _INLINE_CODE_RE.sub(_stash_code, section_text)
            small_chunks = self._small_splitter.split_text(placeholder_text)
            for small_text in small_chunks:
                if not small_text.strip():
                    continue

                # Restore code blocks in each small chunk
                for ph, code in code_map.items():
                    if ph in small_text:
                        small_text = small_text.replace(ph, code)

                chunk_meta = {
                    **metadata,
                    "chunk_index": chunk_index,
                    "section_title": section_title,
                    "small_chunk_id": f"{metadata.get('doc_id', 'unknown')}#s{chunk_index}",
                    "big_chunk_text": section_text[:4000],
                    "chunk_size": len(small_text),
                }

                all_chunks.append(ChunkResult(
                    text=small_text,
                    metadata=chunk_meta,
                    page_range=section_pages,
                    fingerprint=compute_fingerprint(small_text),
                    chunk_method="hybrid",
                    section_title=section_title,
                ))
                chunk_index += 1

        # Post-merge: absorb tiny (<100 chars) non-code chunks into adjacent
        # chunks from the same section. RecursiveCharacterTextSplitter can
        # leave 20-80 char fragments between larger pieces (e.g. a single
        # bullet point stranded between two ~500 char chunks). These are
        # useless for retrieval and lack context.
        all_chunks = self._merge_tiny_chunks(all_chunks)

        # Verify page coverage if total_pages is known
        if total_pages > 0:
            coverage = verify_page_coverage(all_chunks, total_pages)
            if coverage["missing_pages"]:
                logger.warning(
                    f"Hybrid chunking missing pages: {coverage['missing_pages']}"
                )

        logger.info(f"Hybrid chunking complete: {len(all_chunks)} chunks from {len(sections)} sections")
        return all_chunks

    def _structural_split(
        self,
        text: str,
        file_path: Optional[Path] = None,
    ) -> list[tuple[str, str, tuple[int, int]]]:
        """
        Split text by document structure.

        Returns list of (section_title, section_text, (start_page, end_page)).
        """
        # Detect format
        is_markdown = False
        if file_path:
            ext = file_path.suffix.lower()
            is_markdown = ext in (".md", ".markdown")
        if not is_markdown:
            # Heuristic: check for Markdown headers
            is_markdown = bool(re.search(r"^#{1,4}\s", text, re.MULTILINE))

        if is_markdown:
            return self._split_markdown(text)
        else:
            return self._split_plain_text(text)

    def _split_markdown(self, text: str) -> list[tuple[str, str, tuple[int, int]]]:
        """Split Markdown by headers, with long fenced code blocks extracted
        as independent sections so they are never split mid-block.

        Short code blocks (<= ``small_chunk_size``) stay inline with their
        surrounding text so the small splitter can keep them together with
        their context (e.g. a 2-line API endpoint snippet next to its
        request/response description).

        Empty header-only sections (e.g. ``## Title`` immediately followed by
        ``### Subtitle``) are skipped — the parent title is preserved in the
        header stack so the next non-empty section's title path includes it.
        """
        # re.split with a capturing group keeps code blocks as separate items
        # in the result list (alternating text / code / text / ...).
        parts_with_code = re.split(r"(```[\s\S]*?```)", text)

        # First pass: merge short code blocks AND text parts into a single
        # text run so the header split produces one section per header, not
        # one section per inter-code-block text fragment.
        #
        # Without merging text parts, a section like:
        #   ### 3.5 API
        #   ```code1```
        #   响应：...
        #   ```code2```
        #   请求：...
        #   ```code3```
        # produces 3 separate sections (text+code1, text+code2, text+code3),
        # each ~40-100 chars — contextless fragments that can't be retrieved
        # meaningfully.
        #
        # Short code blocks are replaced with placeholders so their content
        # (e.g. bash "#" comments) isn't mistaken for Markdown headers.
        merged: list[tuple[bool, str]] = []  # (is_long_code_block, text_or_placeholder)
        code_placeholders: dict[str, str] = {}
        cb_idx = 0
        for part in parts_with_code:
            if not part or not part.strip():
                continue
            is_code = part.startswith("```") and part.endswith("```")
            if is_code and len(part) <= self.small_chunk_size:
                placeholder = f"\x00CB{cb_idx}\x00"
                cb_idx += 1
                code_placeholders[placeholder] = part
                if merged and not merged[-1][0]:
                    merged[-1] = (False, merged[-1][1] + placeholder)
                else:
                    merged.append((False, placeholder))
            elif is_code:
                # Long code block: independent entry (becomes its own section)
                merged.append((True, part))
            else:
                # Text part: merge into previous text entry to keep context
                # together. The header split below will separate sections.
                if merged and not merged[-1][0]:
                    merged[-1] = (False, merged[-1][1] + part)
                else:
                    merged.append((False, part))

        sections: list[tuple[str, str, tuple[int, int]]] = []
        header_stack: list[tuple[int, str]] = []  # [(level, title), ...]

        for is_long_code, part in merged:
            # Long fenced code block: emit as an independent section so the
            # small splitter never breaks it into meaningless line fragments.
            if is_long_code:
                section_title = " > ".join(t for _, t in header_stack) if header_stack else ""
                sections.append((section_title, part, (0, 0)))
                continue

            # Text part (may contain short inline code blocks as placeholders):
            # split by headers. Placeholders prevent code-block content from
            # being mistaken for headers.
            sub_parts = re.split(r"\n(?=#{1,4}\s)", part)
            for sub_part in sub_parts:
                sub_part = sub_part.strip()
                if not sub_part:
                    continue

                title_match = re.match(r"^(#{1,4})\s+(.+)", sub_part)
                if title_match:
                    level = len(title_match.group(1))
                    title = title_match.group(2).strip()
                    while header_stack and header_stack[-1][0] >= level:
                        header_stack.pop()
                    header_stack.append((level, title))
                    section_title = " > ".join(t for _, t in header_stack)

                    # Skip header-only sections (title with no body). The
                    # title is already on header_stack, so the next non-empty
                    # section will inherit it via the title path.
                    body = sub_part[title_match.end():].strip()
                    if not body:
                        continue
                else:
                    section_title = " > ".join(t for _, t in header_stack) if header_stack else ""

                # Restore code block placeholders now that header split is done.
                for ph, code in code_placeholders.items():
                    if ph in sub_part:
                        sub_part = sub_part.replace(ph, code)

                sections.append((section_title, sub_part, (0, 0)))

        return sections

    def _split_plain_text(self, text: str) -> list[tuple[str, str, tuple[int, int]]]:
        """Split plain text by blank lines and paragraph boundaries."""
        parts = re.split(r"\n\s*\n", text)

        sections: list[tuple[str, str, tuple[int, int]]] = []

        for part in parts:
            part = part.strip()
            if not part:
                continue

            lines = part.split("\n")
            section_title = ""
            if lines and len(lines[0].strip()) < 80:
                section_title = lines[0].strip()

            # Plain text files have no real page numbers
            sections.append((section_title, part, (0, 0)))

        if not sections:
            sections.append(("", text, (0, 0)))

        return sections

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

            # Try merging into previous chunk (same section)
            if merged:
                prev = merged[-1]
                same_section = prev.section_title == chunk.section_title
                prev_is_code = prev.metadata.get("is_code_block", False)
                if same_section and not prev_is_code:
                    new_text = prev.text + "\n" + chunk.text
                    new_meta = {**prev.metadata}
                    new_meta["chunk_size"] = len(new_text)
                    new_meta["big_chunk_text"] = new_text[:4000]
                    merged[-1] = ChunkResult(
                        text=new_text,
                        metadata=new_meta,
                        page_range=prev.page_range,
                        fingerprint=compute_fingerprint(new_text),
                        chunk_method=prev.chunk_method,
                        section_title=prev.section_title,
                    )
                    continue

            # Can't merge: keep as-is (will try next chunk)
            merged.append(chunk)

        # Renumber chunk_index
        for i, chunk in enumerate(merged):
            chunk.metadata["chunk_index"] = i
            chunk.metadata["small_chunk_id"] = (
                f"{chunk.metadata.get('doc_id', 'unknown')}#s{i}"
            )

        return merged
