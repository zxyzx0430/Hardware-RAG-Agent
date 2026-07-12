"""Base data classes and utilities for chunking."""

import hashlib
import json
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

        For text without any page markers, returns [] (empty list); callers
        must handle this case explicitly.
    """
    markers = list(PAGE_MARKER_RE.finditer(text))
    if not markers:
        return []   # No markers found; callers must handle explicitly

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
    if not index:
        # No page markers — can't do exact extraction, return full text as fallback
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
    if not index:
        # No page markers — can't determine page, return 1 as fallback
        return 1
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


# ═══════════════════════════════════════════════════════════════
# Boundary-aware truncation — for ParentDocument big chunks
# ═══════════════════════════════════════════════════════════════

_BOUNDARY_MARKS: tuple[str, ...] = ("\n\n", "。", ".", "\n")


def truncate_at_boundary(text: str, max_chars: int = 4000, tolerance: int = 100) -> str:
    """Truncate text at the nearest sentence/paragraph boundary.

    Boundary priority: paragraph (\\n\\n) > period (。/.) > newline (\\n) > hard cut.
    Searches within [max_chars - tolerance, max_chars + tolerance] for the best boundary.
    Falls back to hard cut at max_chars if no boundary found.

    Args:
        text: Input text to truncate.
        max_chars: Target maximum character count.
        tolerance: Search window radius around max_chars.

    Returns:
        Truncated text ending at a boundary when possible, or hard-cut at max_chars.
    """
    if len(text) <= max_chars + tolerance:
        return text

    search_start = max(0, max_chars - tolerance)
    search_end = min(len(text), max_chars + tolerance)

    for mark in _BOUNDARY_MARKS:
        cut_pos = _find_nearest_boundary(text, mark, max_chars, search_start, search_end)
        if cut_pos is not None:
            return text[:cut_pos]

    return text[:max_chars]


def _find_nearest_boundary(
    text: str, mark: str, anchor: int, search_start: int, search_end: int
) -> Optional[int]:
    """Find the nearest occurrence of mark to anchor within [search_start, search_end].

    Returns the cut position (after the boundary chars) or None.
    """
    backward = text.rfind(mark, search_start, anchor + 1)
    forward = text.find(mark, anchor, search_end + 1)

    if backward == -1 and forward == -1:
        return None
    if backward == -1:
        return forward + len(mark)
    if forward == -1:
        return backward + len(mark)

    backward_dist = anchor - backward
    forward_dist = forward - anchor
    if forward_dist < backward_dist:
        return forward + len(mark)
    return backward + len(mark)


# ═══════════════════════════════════════════════════════════════
# Structure protection — keep tables/register-fields intact during splitting
# ═══════════════════════════════════════════════════════════════

_TABLE_ROW_RE = re.compile(
    r"(?:^|\n)(\|(?:[^\n]*\|)+)(?=\n|$)",
    re.MULTILINE,
)
_REGISTER_FIELD_RE = re.compile(
    r"(?:^|\n)((?:0x[0-9A-Fa-f]{2,4}\s+[^\n]+(?:\n0x[0-9A-Fa-f]{2,4}\s+[^\n]+)*)"
    r"|(?:\d{2}\s+\d\s+[^\n]*(?:BIT|REG|ADDR|MASK|ENABLE|DISABLE|RESERVED)[^\n]*"
    r"(?:\n\d{2}\s+\d\s+[^\n]*(?:BIT|REG|ADDR|MASK|ENABLE|DISABLE|RESERVED)[^\n]*)*))",
    re.MULTILINE | re.IGNORECASE,
)


def protect_structures(text: str) -> tuple[str, dict[str, str]]:
    """Replace Markdown tables and register-field blocks with placeholders.

    Call before RecursiveCharacterTextSplitter to prevent tables from being
    split across chunks. Restore with restore_structures() after splitting.
    """
    placeholders: dict[str, str] = {}

    def _save(match: re.Match) -> str:
        key = f"@@PROTECT_{len(placeholders)}@@"
        placeholders[key] = match.group(0)
        return key

    protected = _TABLE_ROW_RE.sub(_save, text)
    protected = _REGISTER_FIELD_RE.sub(_save, protected)
    return protected, placeholders


def restore_structures(text: str, placeholders: dict[str, str]) -> str:
    """Restore placeholders back to original table/register content."""
    for key, original in placeholders.items():
        text = text.replace(key, original)
    return text


@dataclass
class ChunkResult:
    """A single chunk produced by a chunker."""

    text: str
    metadata: dict
    page_range: tuple[int, int]  # (start_page, end_page)
    fingerprint: str  # SHA256(text)
    chunk_method: str  # "agent" | "hybrid"
    section_title: str = ""  # Section title this chunk belongs to
    # big_chunk_id: pointer to a BigChunk row (format: "{doc_id}#b{section_idx}").
    # Stored in ChromaDB metadata so retrieval can join small→big.
    big_chunk_id: str = ""
    # big_chunk_text: full section text (boundary-truncated). Consumed by the
    # ingest stage to populate the big_chunks table; NOT stored in ChromaDB
    # metadata to avoid redundancy.
    big_chunk_text: str = ""


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


# ═══════════════════════════════════════════════════════════════
# Robust JSON parsing — shared by AgentChunker and MultimodalChunker
# ═══════════════════════════════════════════════════════════════

def parse_json_robust(content: str) -> Optional[dict]:
    """Parse JSON from LLM output with 5-layer fallback.

    Used by both AgentChunker and MultimodalChunker to handle LLMs that
    wrap JSON in markdown fences, add preamble text, use single quotes,
    or include trailing commas.

    Layer 1: direct json.loads
    Layer 2: extract from ```json ... ``` fenced block
    Layer 3: extract outermost { ... } via regex
    Layer 4: fix common errors (single quotes, trailing commas,
             control chars, Python bools/None) then retry Layer 1-3
    Layer 5: extract from unmarked ``` ... ``` fenced block

    Returns parsed dict or None if all layers fail.
    """
    if not content or not content.strip():
        return None

    # Layer 1: direct parse
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass

    # Layer 2: ```json ... ``` fenced block
    json_block_match = re.search(r"```json\s*([\s\S]*?)```", content, re.IGNORECASE)
    if json_block_match:
        try:
            return json.loads(json_block_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Layer 3: outermost { ... }
    brace_match = re.search(r"\{[\s\S]*\}", content)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    # Layer 4: fix common errors then retry
    fixed = content
    # Strip markdown fences if present
    fixed = re.sub(r"```(?:json)?\s*", "", fixed)
    fixed = fixed.replace("```", "")
    # Single quotes → double quotes (only outside already-double-quoted strings)
    fixed = re.sub(r"(?<!\\)'", '"', fixed)
    # Trailing commas before } or ]
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    # Control characters → space
    fixed = re.sub(r"[\x00-\x1f]", " ", fixed)
    # Python bools/None → JSON bools/null
    fixed = re.sub(r"\bTrue\b", "true", fixed)
    fixed = re.sub(r"\bFalse\b", "false", fixed)
    fixed = re.sub(r"\bNone\b", "null", fixed)
    try:
        return json.loads(fixed)
    except (json.JSONDecodeError, TypeError):
        pass
    # Retry Layer 2-3 on fixed content
    json_block_match = re.search(r"```json\s*([\s\S]*?)```", fixed, re.IGNORECASE)
    if json_block_match:
        try:
            return json.loads(json_block_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    brace_match = re.search(r"\{[\s\S]*\}", fixed)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    # Layer 5: unmarked ``` ... ``` block
    unmarked_match = re.search(r"```\s*([\s\S]*?)```", content)
    if unmarked_match:
        try:
            return json.loads(unmarked_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    return None


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
