"""Base data classes and utilities for chunking."""

import hashlib
import re
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# Page marker system — enables accurate page tracking across all chunkers
# ═══════════════════════════════════════════════════════════════
# Format: <!-- PAGE:N --> inserted before each page's text.
# This is invisible in rendered Markdown, easy to parse, and LLM-readable.

PAGE_MARKER_RE = re.compile(r"<!-- PAGE:(\d+) -->")


def build_text_with_page_markers(page_texts: list[str]) -> str:
    """Build a single text string from per-page text, inserting page markers.

    Args:
        page_texts: List of text strings, one per page (index 0 = page 1).

    Returns:
        Text with <!-- PAGE:N --> markers before each page's content.
    """
    parts: list[str] = []
    for i, page_text in enumerate(page_texts):
        page_num = i + 1
        parts.append(f"<!-- PAGE:{page_num} -->\n{page_text}")
    return "\n\n".join(parts)


def parse_page_index(text: str) -> list[tuple[int, int, int]]:
    """Parse page markers and build a page index.

    Returns:
        List of (page_num, content_start_char, content_end_char) tuples.
        content_start_char is the offset right after the marker line.
        content_end_char is the offset of the next marker (or end of text).

        For text without any page markers, returns [(1, 0, len(text))].
    """
    markers = list(PAGE_MARKER_RE.finditer(text))
    if not markers:
        return [(1, 0, len(text))]

    index: list[tuple[int, int, int]] = []
    for i, m in enumerate(markers):
        page_num = int(m.group(1))
        # Content starts after the marker + newline
        content_start = m.end()
        # Skip the newline after marker
        if content_start < len(text) and text[content_start] == "\n":
            content_start += 1
        # Content ends at the next marker (or end of text)
        if i + 1 < len(markers):
            # Back up past whitespace before next marker
            content_end = markers[i + 1].start()
            # Strip trailing whitespace/newlines
            while content_end > content_start and text[content_end - 1] in "\n\r ":
                content_end -= 1
        else:
            content_end = len(text)
        index.append((page_num, content_start, content_end))
    return index


def get_text_for_page_range(text: str, start_page: int, end_page: int) -> str:
    """Extract the text content for a specific page range using page markers.

    Args:
        text: Full text with page markers.
        start_page: Starting page number (1-based).
        end_page: Ending page number (1-based, inclusive).

    Returns:
        Text content for the specified page range (without markers).
        Falls back to chars_per_page estimation if no markers found.
    """
    index = parse_page_index(text)
    if len(index) == 1 and index[0][0] == 1 and index[0][1] == 0:
        # No page markers — can't do exact extraction
        return text

    parts: list[str] = []
    for page_num, content_start, content_end in index:
        if start_page <= page_num <= end_page:
            parts.append(text[content_start:content_end])
    return "\n\n".join(parts)


def get_page_for_char(text: str, char_offset: int) -> int:
    """Find which page a character offset belongs to.

    Args:
        text: Full text with page markers.
        char_offset: Character offset in the text.

    Returns:
        Page number (1-based). Returns 1 if no markers found.
    """
    index = parse_page_index(text)
    for page_num, content_start, content_end in index:
        if content_start <= char_offset <= content_end:
            return page_num
    # If offset is in a marker line, find the page before it
    for i, (page_num, content_start, content_end) in enumerate(index):
        if char_offset < content_start:
            return page_num if i == 0 else index[i - 1][0]
    return index[-1][0] if index else 1


def strip_page_markers(text: str) -> str:
    """Remove all page markers from text."""
    return PAGE_MARKER_RE.sub("", text)


@dataclass
class ChunkResult:
    """A single chunk produced by a chunker."""

    text: str
    metadata: dict
    page_range: tuple[int, int]  # (start_page, end_page)
    fingerprint: str  # SHA256(text)
    chunk_method: str  # "agent" | "hybrid"
    section_title: str = ""  # Section title this chunk belongs to


def compute_fingerprint(text: str) -> str:
    """SHA256(content) for verifying chunk coverage integrity."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_page_coverage(chunks: list[ChunkResult], total_pages: int) -> dict:
    """
    Check if chunks cover all pages of the source document.

    Returns:
        {
            "covered_pages": list[int],
            "missing_pages": list[int],
            "duplicate_pages": list[int],
        }
    """
    covered: dict[int, int] = {}  # page -> count
    for chunk in chunks:
        start, end = chunk.page_range
        for page in range(start, end + 1):
            covered[page] = covered.get(page, 0) + 1

    all_pages = set(range(1, total_pages + 1)) if total_pages > 0 else set()
    covered_set = set(covered.keys())
    missing = sorted(all_pages - covered_set)
    duplicate = sorted([p for p, count in covered.items() if count > 1])

    return {
        "covered_pages": sorted(covered_set),
        "missing_pages": missing,
        "duplicate_pages": duplicate,
    }


class BaseChunker(ABC):
    """Abstract base class for all chunkers."""

    @abstractmethod
    async def chunk(
        self,
        text: str,
        metadata: dict,
        file_path: Optional[Path] = None,
        total_pages: int = 0,
    ) -> list[ChunkResult]:
        """Split text into chunks.

        Args:
            text: Full document text (already parsed from PDF/MD/TXT).
            metadata: Base metadata to attach to each chunk (doc_id, title, etc.).
            file_path: Original file path (for format detection).
            total_pages: Total page count of the source document.

        Returns:
            List of ChunkResult.
        """
        ...
