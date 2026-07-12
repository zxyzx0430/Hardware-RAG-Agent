"""Chunker factory — select chunker by method name."""

from src.rag.chunking.base import BaseChunker
from src.rag.chunking.hybrid_chunker import HybridChunker
from src.rag.chunking.agent_chunker import AgentChunker


def get_chunker(chunk_method: str, **kwargs) -> BaseChunker:
    """Get a chunker instance by method name.

    Args:
        chunk_method: "hybrid", "agent", or "multimodal"
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
    elif chunk_method == "multimodal":
        from src.rag.chunking.multimodal_chunker import MultimodalChunker
        return MultimodalChunker(**kwargs)
    else:
        raise ValueError(f"Unknown chunk_method: {chunk_method}. Use 'hybrid', 'agent', or 'multimodal'.")
