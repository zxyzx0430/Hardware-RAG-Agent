"""Chunker factory — select chunker by method name."""

from src.rag.chunking.base import BaseChunker
from src.rag.chunking.hybrid_chunker import HybridChunker
from src.rag.chunking.agent_chunker import AgentChunker


def get_chunker(chunk_method: str, **kwargs) -> BaseChunker:
    """Get a chunker instance by method name.

    Args:
        chunk_method: "hybrid" or "agent"
        **kwargs: Passed to the chunker constructor.

    Returns:
        BaseChunker instance.

    Raises:
        ValueError: If chunk_method is unknown.
    """
    if chunk_method == "agent":
        return AgentChunker(**kwargs)
    elif chunk_method == "hybrid":
        return HybridChunker(**kwargs)
    else:
        raise ValueError(f"Unknown chunk_method: {chunk_method}. Use 'hybrid' or 'agent'.")
