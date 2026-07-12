"""MultiQueryRetriever wrapper — optional RAG enhancement (defaults off).

Stage 4 Task 15. Wraps the existing RRFEnsembleRetriever (which preserves
the custom rrf_fusion algorithm with zero logic changes) with
langchain_classic's MultiQueryRetriever so the LangChain ecosystem can
generate N LLM query variants, retrieve for each, and return the unique
union.

Design stance (see docs/pitfalls.md Task 28 evaluation):
- **Default off.** The Agent's autonomous query-rewriting capability
  (prompts.py L38-44: "精炼检索词 + 改写 3 次降级 + list_kb_docs 定位")
  remains the default path. This module is opt-in only — callers must
  explicitly construct a MultiQueryRetriever.
- **rrf_fusion untouched.** The underlying RRFEnsembleRetriever still
  runs vector + BM25 → RRF fusion. MultiQueryRetriever only multiplies
  the input query; the fusion logic per query is identical.
- **Score caveat.** MultiQueryRetriever returns the unique union of
  Documents. The RRF display score is preserved in each Document's
  metadata (``metadata["score"]``), but deduplication may drop one of two
  equal-content chunks that came from different queries — downstream
  threshold / cross-KB sort consumers lose granularity. Acceptable for
  an opt-in enhancement; documented for future re-evaluation.
- **Single-KB wrap.** MultiQueryRetriever wraps a single BaseRetriever.
  For multi-KB scenarios only the first kb_id is wrapped (with a logged
  warning); cross-KB merge via MergerRetriever is left as a future
  enhancement.

Spec: migrate-langchain-1x-new-api §Task 15 (stage 4 RAG enhancement).
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_classic.retrievers import MultiQueryRetriever
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import ChatOpenAI

from src.config.settings import settings
from src.rag.kb_manager import KnowledgeBaseManager, get_kb_manager

logger = logging.getLogger(__name__)

# Top-k forwarded to the underlying RRFEnsembleRetriever. Matches the
# default k in KnowledgeBaseManager.as_retriever / search_all_enabled.
_DEFAULT_WRAPPED_K: int = 5

# Temperature for the query-generation LLM. Low temperature keeps the N
# generated variants focused on the original intent rather than diverging
# to unrelated topics (which would pollute retrieval with off-target hits).
_DEFAULT_QUERY_LLM_TEMPERATURE: float = 0.0

# Cosine score threshold forwarded to the underlying vector search inside
# RRFEnsembleRetriever. 0.0 = no threshold (matches search_all_enabled default).
_DEFAULT_SCORE_THRESHOLD: float = 0.0


def _build_default_llm() -> BaseLanguageModel:
    """Build a LangChain ChatOpenAI from project settings (low temperature)."""
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=_DEFAULT_QUERY_LLM_TEMPERATURE,
    )


def _resolve_llm(llm: Optional[BaseLanguageModel]) -> BaseLanguageModel:
    """Return the provided llm, or build the project default when None."""
    return llm if llm is not None else _build_default_llm()


def build_multi_query_retriever(
    kb_manager: KnowledgeBaseManager,
    kb_id: str,
    llm: Optional[BaseLanguageModel] = None,
) -> MultiQueryRetriever:
    """Wrap ``kb_manager.as_retriever(kb_id)`` with MultiQueryRetriever.

    The underlying RRFEnsembleRetriever preserves rrf_fusion (BM25 + vector
    → RRF ranking with original 0-1 display scores) with zero logic changes.
    MultiQueryRetriever generates N query variants via the LLM, retrieves for
    each variant through the same RRF pipeline, and returns the unique union.

    Args:
        kb_manager: KnowledgeBaseManager instance (multi-KB manager).
        kb_id: Single KB id to wrap — as_retriever requires a concrete kb_id.
        llm: Optional LangChain LLM for query generation; None → project
            default ChatOpenAI built from settings (low temperature).

    Returns:
        MultiQueryRetriever wrapping RRFEnsembleRetriever for the given KB.
    """
    base_retriever = kb_manager.as_retriever(
        kb_id, k=_DEFAULT_WRAPPED_K, score_threshold=_DEFAULT_SCORE_THRESHOLD,
    )
    resolved_llm = _resolve_llm(llm)
    logger.info("multi_query_retriever_built kb=%s k=%d", kb_id, _DEFAULT_WRAPPED_K)
    return MultiQueryRetriever.from_llm(retriever=base_retriever, llm=resolved_llm)


def get_multi_query_retriever(
    kb_ids: list[str],
    llm: Optional[BaseLanguageModel] = None,
) -> MultiQueryRetriever:
    """Factory: build a MultiQueryRetriever from kb_ids via the global singleton.

    Mirrors SearchDocsTool's ``_kb_ids: list[str]`` shape so the integration
    can pass the same kb_ids the tool already receives from frontend config.
    For multiple kb_ids, wraps the first KB and logs a warning
    (MultiQueryRetriever wraps a single BaseRetriever; cross-KB merge is a
    future enhancement — see module docstring).

    Args:
        kb_ids: List of KB IDs; only the first is wrapped (see limitation).
        llm: Optional LangChain LLM; None → project default ChatOpenAI.

    Returns:
        MultiQueryRetriever wrapping RRFEnsembleRetriever for the first KB.

    Raises:
        ValueError: When kb_ids is empty.
    """
    if not kb_ids:
        raise ValueError("kb_ids must contain at least one KB id")
    if len(kb_ids) > 1:
        logger.warning(
            "multi_query_retriever_multi_kb wrapping first only: %s (full=%s)",
            kb_ids[0], kb_ids,
        )
    kb_manager = get_kb_manager()
    return build_multi_query_retriever(kb_manager, kb_ids[0], llm=llm)
