"""BgeRerankerCompressor — wraps rerank as a LangChain BaseDocumentCompressor.

Preserves the batch rerank optimization (single predict call for all chunks
across multiple KBs) with zero logic changes. Adapts to BaseDocumentCompressor
interface so LangChain ContextualCompressionRetriever can consume it.

Spec: migrate-langchain-1x-new-api §Task 3.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_core.documents.compressor import BaseDocumentCompressor
from pydantic import ConfigDict

from src.rag.reranker import rerank

logger = logging.getLogger(__name__)

# Default top_k: 0 = return all (reranked). Caller can override.
_DEFAULT_TOP_K: int = 0


class BgeRerankerCompressor(BaseDocumentCompressor):
    """LangChain BaseDocumentCompressor wrapping the custom rerank function.

    Calls rerank() with all chunk contents in a single batch predict,
    preserving the cross-KB batch optimization. Reorders Documents by
    rerank score (descending). Falls back to original order with score=0.0
    when the reranker model is unavailable (graceful degradation).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    top_k: int = _DEFAULT_TOP_K

    def compress_documents(
        self, documents: list[Document], query: str, callbacks: Any = None,
    ) -> list[Document]:
        """Rerank documents by query relevance using cross-encoder."""
        if not documents:
            return []
        chunks = [d.page_content for d in documents]
        ranked = rerank(query, chunks, top_k=self.top_k)
        logger.info(
            "reranker_compressor_reranked query=%s in=%d out=%d",
            query[:40], len(documents), len(ranked),
        )
        return [_pick(documents, idx, score) for idx, score in ranked]


def _pick(documents: list[Document], idx: int, score: float) -> Document:
    """Return a copy of documents[idx] with rerank_score injected."""
    src = documents[idx]
    metadata = {**src.metadata, "rerank_score": score}
    return Document(page_content=src.page_content, metadata=metadata)
