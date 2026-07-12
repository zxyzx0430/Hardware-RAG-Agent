"""RRFEnsembleRetriever — wraps rrf_fusion as a LangChain BaseRetriever.

Preserves the custom RRF algorithm (BM25 penalty / soft normalization /
fingerprint dedup) with zero logic changes — only adapts the interface
so the LangChain ecosystem (MultiQueryRetriever, ContextualCompressionRetriever,
langgraph dev studio) can consume it.

Spec: migrate-langchain-1x-new-api §Task 2.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from src.rag.kb_manager import rrf_fusion
from src.rag.vector_store import SearchResult

logger = logging.getLogger(__name__)

# Default RRF constant (matches rrf_fusion default).
_DEFAULT_RRF_K: int = 60


class RRFEnsembleRetriever(BaseRetriever):
    """LangChain BaseRetriever wrapping the custom rrf_fusion algorithm.

    Receives two search providers (vector + bm25) as callables returning
    list[SearchResult]. Calls rrf_fusion to merge, then converts to
    LangChain Document for ecosystem compatibility.

    Custom algorithm preserved (BM25 penalty / soft norm / dedup) —
    zero modification to rrf_fusion.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    vector_retriever: Callable[[str], list[SearchResult]]
    bm25_retriever: Callable[[str], list[SearchResult]]
    rrf_constant_k: int = _DEFAULT_RRF_K

    def _get_relevant_documents(
        self, query: str, *, run_manager: Any = None,
    ) -> list[Document]:
        """Run vector + bm25 search, fuse via rrf_fusion, return Documents."""
        vector_results = self.vector_retriever(query)
        bm25_results = self.bm25_retriever(query)
        fused = rrf_fusion(
            vector_results, bm25_results, constant_k=self.rrf_constant_k,
        )
        logger.info(
            "rrf_retriever_fused query=%s vector=%d bm25=%d fused=%d",
            query[:40], len(vector_results), len(bm25_results), len(fused),
        )
        return [_to_document(r) for r in fused]


def _to_document(result: SearchResult) -> Document:
    """Convert SearchResult to LangChain Document with score in metadata."""
    metadata = {**result.metadata, "score": result.score, "doc_id": result.doc_id}
    return Document(page_content=result.content, metadata=metadata)
