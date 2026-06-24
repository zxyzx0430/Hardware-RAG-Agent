"""Base data classes and utilities for chunking."""

import hashlib
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


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
            "covered_pages": set[int],
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
        "covered_pages": covered_set,
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
