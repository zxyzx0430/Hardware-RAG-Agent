"""Chunking module — hybrid, agent & multimodal chunking strategies."""

from src.rag.chunking.base import (
    ChunkResult,
    BaseChunker,
    compute_fingerprint,
    verify_page_coverage,
    PAGE_MARKER_RE,
    build_text_with_page_markers,
    parse_page_index,
    get_text_for_page_range,
    get_page_for_char,
    strip_page_markers,
)
from src.rag.chunking.hybrid_chunker import HybridChunker
from src.rag.chunking.agent_chunker import AgentChunker
from src.rag.chunking.factory import get_chunker

__all__ = [
    "ChunkResult",
    "BaseChunker",
    "compute_fingerprint",
    "verify_page_coverage",
    "PAGE_MARKER_RE",
    "build_text_with_page_markers",
    "parse_page_index",
    "get_text_for_page_range",
    "get_page_for_char",
    "strip_page_markers",
    "HybridChunker",
    "AgentChunker",
    "get_chunker",
]
