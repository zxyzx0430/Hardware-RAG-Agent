"""Chunking module — hybrid & agent chunking strategies."""

from src.rag.chunking.base import (
    ChunkResult,
    BaseChunker,
    compute_fingerprint,
    verify_page_coverage,
)
from src.rag.chunking.hybrid_chunker import HybridChunker
from src.rag.chunking.agent_chunker import AgentChunker
from src.rag.chunking.factory import get_chunker

__all__ = [
    "ChunkResult",
    "BaseChunker",
    "compute_fingerprint",
    "verify_page_coverage",
    "HybridChunker",
    "AgentChunker",
    "get_chunker",
]
