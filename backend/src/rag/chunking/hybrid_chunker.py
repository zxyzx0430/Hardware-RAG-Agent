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

# Default separators optimized for Chinese hardware documents
_DEFAULT_SEPARATORS = ["\n## ", "\n### ", "\n#### ", "\n\n", "\n", "。", ".", " ", ""]


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

        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=self.separators,
            length_function=len,
        )
        self._small_splitter = RecursiveCharacterTextSplitter(
            chunk_size=small_chunk_size,
            chunk_overlap=min(100, chunk_overlap),
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
        """Execute hybrid chunking pipeline."""
        # Step 1: Structural split
        sections = self._structural_split(text, file_path)

        # Step 2: Recursive character split for oversized sections
        all_chunks: list[ChunkResult] = []
        chunk_index = 0

        for section_title, section_text, section_pages in sections:
            sub_chunks = self._splitter.split_text(section_text)
            for sub_text in sub_chunks:
                if not sub_text.strip():
                    continue

                # Step 3: Small-to-big mapping
                small_chunks = self._small_splitter.split_text(sub_text)
                big_chunk_text = sub_text  # The full section unit is the "big chunk"

                for small_text in small_chunks:
                    if not small_text.strip():
                        continue

                    chunk_meta = {
                        **metadata,
                        "chunk_index": chunk_index,
                        "section_title": section_title,
                        "small_chunk_id": f"{metadata.get('doc_id', 'unknown')}#s{chunk_index}",
                        "big_chunk_text": big_chunk_text[:2000],  # Truncate for metadata
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
        """Split Markdown by headers (## / ### / ---)."""
        # Split by ## and ### headers
        parts = re.split(r"\n(?=#{1,4}\s)", text)

        sections: list[tuple[str, str, tuple[int, int]]] = []
        page = 1

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Extract section title
            title_match = re.match(r"^(#{1,4})\s+(.+)", part)
            section_title = title_match.group(2).strip() if title_match else ""

            # Estimate page range (rough: ~3000 chars per page)
            est_pages = max(1, len(part) // 3000)
            page_range = (page, page + est_pages - 1)
            page += est_pages

            sections.append((section_title, part, page_range))

        return sections

    def _split_plain_text(self, text: str) -> list[tuple[str, str, tuple[int, int]]]:
        """Split plain text by blank lines and paragraph boundaries."""
        # Split by consecutive blank lines
        parts = re.split(r"\n\s*\n", text)

        sections: list[tuple[str, str, tuple[int, int]]] = []
        page = 1

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Try to detect a title (first non-empty line that's short)
            lines = part.split("\n")
            section_title = ""
            if lines and len(lines[0].strip()) < 80:
                section_title = lines[0].strip()

            est_pages = max(1, len(part) // 3000)
            page_range = (page, page + est_pages - 1)
            page += est_pages

            sections.append((section_title, part, page_range))

        if not sections:
            sections.append(("", text, (1, 1)))

        return sections
