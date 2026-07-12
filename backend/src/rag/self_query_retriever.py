"""SelfQueryRetriever wrapper — optional RAG enhancement (defaults off).

Stage 4 Task 17. Uses langchain_classic's SelfQueryRetriever so an LLM can
turn a natural-language query into (semantic query + structured metadata
filter) and run it against the Chroma vector store. Example: "STM32F4 的
GPIO 章节 第 50 页之后" → filter {chip_series: $eq STM32F4, page_start:
$gte 50} + semantic query "GPIO configuration".

Design stance (see module docstring of multi_query_retriever.py):
- **Default off.** The Agent's autonomous query-rewriting capability
  (prompts.py: refine + 3-step fallback + list_kb_docs) remains the
  default path. This module is opt-in only — callers must explicitly
  construct a SelfQueryRetriever. The existing ``doc_filter`` parameter
  in SearchDocsTool (Python-side substring filtering on title/doc_id)
  is preserved unchanged.
- **Bypasses RRF.** Unlike MultiQueryRetriever (which wraps the
  RRFEnsembleRetriever and preserves rrf_fusion), SelfQueryRetriever
  MUST wrap the underlying langchain_chroma.Chroma vectorstore directly.
  This is a LangChain API constraint: ``SelfQueryRetriever.from_llm``
  takes ``vectorstore: VectorStore`` (not BaseRetriever) because it
  needs the translator (ChromaTranslator) to convert structured queries
  into metadata filters via ``vectorstore.search(query, filter=...)``.
  RRFEnsembleRetriever is a BaseRetriever without filter support.
  Side effect: retrieval is pure Chroma vector similarity + LLM-generated
  metadata filter — no BM25, no RRF fusion, no cross-encoder rerank.
- **Single-KB wrap.** SelfQueryRetriever wraps a single Chroma store.
  For multiple kb_ids only the first is wrapped (with a logged warning);
  cross-KB merge is left as a future enhancement.

Chroma $contains evaluation (see task spec §"Chroma $contains 评估"):
- **Not supported at the metadata-filter level.** ChromaTranslator
  (langchain_community/query_constructors/chroma.py) declares
  ``allowed_comparators = [EQ, NE, GT, GTE, LT, LTE]`` — no LIKE/CONTAINS.
- The ``$contains`` token that appears in
  langchain_community/vectorstores/chroma.py L683 belongs to the
  ``where_document`` parameter (full-text document content filtering),
  NOT to the metadata ``where`` filter. They are independent ChromaDB
  features; SelfQueryRetriever only translates metadata filters.
- **Degradation:** string fields (doc_id / title / section_title /
  category / chunk_method / kb_id) fall back to ``$eq`` exact match.
  Substring matching keeps using the existing ``doc_filter`` parameter
  in SearchDocsTool (Python-side ``_matches_doc_filter`` in kb_manager.py)
  — that path is unchanged.

Spec: migrate-langchain-1x-new-api §Task 17 (stage 4 RAG enhancement).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_classic.chains.query_constructor.schema import AttributeInfo
from langchain_classic.retrievers import SelfQueryRetriever
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import ChatOpenAI

from src.config.settings import settings
from src.rag.kb_manager import KnowledgeBaseManager, get_kb_manager

logger = logging.getLogger(__name__)

# Top-k forwarded to the underlying Chroma similarity_search. Matches the
# default k in KnowledgeBaseManager.as_retriever / search_all_enabled.
_DEFAULT_WRAPPED_K: int = 5

# Temperature for the query-construction LLM. Low temperature keeps the
# generated structured filter deterministic rather than hallucinating
# metadata values that don't exist in the corpus.
_DEFAULT_QUERY_LLM_TEMPERATURE: float = 0.0

# Cosine score threshold forwarded to Chroma similarity_search.
# 0.0 = no threshold (matches search_all_enabled default).
_DEFAULT_SCORE_THRESHOLD: float = 0.0

# Description of page contents fed to the query-constructor LLM so it can
# decide which part of a natural-language query is semantic vs. metadata.
_DOCUMENT_CONTENTS_DESCRIPTION: str = (
    "Hardware manual chunk from chip datasheets and embedded development "
    "guides (GPIO, registers, peripherals, pinout, electrical specs)"
)


def _identity_fields() -> list[AttributeInfo]:
    """Metadata fields identifying the document / KB a chunk belongs to."""
    return [
        AttributeInfo(
            name="doc_id",
            description="Document identifier, e.g. 'baseline-esp32-s3-datasheet-v2'",
            type="string",
        ),
        AttributeInfo(
            name="title",
            description="Document filename, e.g. 'esp32-s3_datasheet.pdf'",
            type="string",
        ),
        AttributeInfo(
            name="kb_id",
            description="Knowledge base identifier, e.g. 'kb-a1b2c3d4'",
            type="string",
        ),
    ]


def _classification_fields() -> list[AttributeInfo]:
    """Metadata fields classifying the chunk's content category / section."""
    return [
        AttributeInfo(
            name="category",
            description="Document category, e.g. 'MCU', 'Sensor', 'Display', 'Power'",
            type="string",
        ),
        AttributeInfo(
            name="section_title",
            description="Section heading, e.g. 'GPIO Configuration', 'Memory Map'",
            type="string",
        ),
        AttributeInfo(
            name="chunk_method",
            description="Chunking method: 'hybrid' (size-based) or 'agent' (LLM)",
            type="string",
        ),
    ]


def _position_fields() -> list[AttributeInfo]:
    """Metadata fields locating the chunk within its source document."""
    return [
        AttributeInfo(
            name="chunk_index",
            description="Index of the chunk within its parent document (0-based)",
            type="integer",
        ),
        AttributeInfo(
            name="page_start",
            description="Starting page number in the source PDF (1-based)",
            type="integer",
        ),
        AttributeInfo(
            name="page_end",
            description="Ending page number in the source PDF (1-based)",
            type="integer",
        ),
    ]


def _build_default_metadata_field_info() -> list[AttributeInfo]:
    """Build default metadata field info for hardware manual chunks.

    Mirrors the chunk metadata written by HardwareVectorStore.ingest_chunks
    and KnowledgeBaseManager.ingest_chunks (kb_id injection). String fields
    fall back to ``$eq`` exact match (see module docstring: Chroma $contains
    not supported at metadata-filter level).
    """
    return [*_identity_fields(), *_classification_fields(), *_position_fields()]


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


def _resolve_metadata_field_info(
    metadata_field_info: Optional[list[AttributeInfo]],
) -> list[AttributeInfo]:
    """Return provided metadata_field_info, or build the default set when None."""
    if metadata_field_info is not None:
        return metadata_field_info
    return _build_default_metadata_field_info()


def build_self_query_retriever(
    kb_manager: KnowledgeBaseManager,
    kb_id: str,
    llm: Optional[BaseLanguageModel] = None,
    metadata_field_info: Optional[list[AttributeInfo]] = None,
) -> SelfQueryRetriever:
    """Build a SelfQueryRetriever over the Chroma store for the given KB.

    Wraps the underlying ``langchain_chroma.Chroma`` vectorstore directly
    (NOT RRFEnsembleRetriever) because ``SelfQueryRetriever.from_llm``
    requires a VectorStore to translate structured queries into metadata
    filters via ChromaTranslator. Side effect: bypasses RRF + BM25 + rerank.
    See module docstring for the full rationale.

    Args:
        kb_manager: KnowledgeBaseManager instance (multi-KB manager).
        kb_id: Single KB id to wrap — Chroma store is per-KB.
        llm: Optional LangChain LLM for query construction; None → project
            default ChatOpenAI built from settings (low temperature).
        metadata_field_info: Optional list of AttributeInfo describing the
            chunk metadata schema; None → default hardware-manual schema
            (doc_id / title / category / section_title / chunk_method /
            kb_id / chunk_index / page_start / page_end).

    Returns:
        SelfQueryRetriever wrapping the Chroma store for the given KB.

    Raises:
        ValueError: When kb_id is unknown or the Chroma store is unavailable
            (e.g. embedding not configured — store.db returns None).
    """
    chroma = _resolve_chroma_store(kb_manager, kb_id)
    fields = _resolve_metadata_field_info(metadata_field_info)
    logger.info("self_query_built kb=%s fields=%d", kb_id, len(fields))
    return SelfQueryRetriever.from_llm(
        llm=_resolve_llm(llm), vectorstore=chroma,
        document_contents=_DOCUMENT_CONTENTS_DESCRIPTION,
        metadata_field_info=fields,
        search_kwargs={"k": _DEFAULT_WRAPPED_K, "score_threshold": _DEFAULT_SCORE_THRESHOLD},
    )


def _resolve_chroma_store(kb_manager: KnowledgeBaseManager, kb_id: str) -> Any:
    """Resolve the langchain_chroma.Chroma vectorstore for a KB.

    SelfQueryRetriever.from_llm requires a VectorStore (not a BaseRetriever)
    so it can translate structured queries into metadata filters via
    ChromaTranslator. Accesses kb_manager._get_store (private; a public
    accessor is proposed in the task report for future cleanup).

    Args:
        kb_manager: KnowledgeBaseManager instance (multi-KB manager).
        kb_id: Single KB id whose Chroma store is needed.

    Returns:
        langchain_chroma.Chroma instance for the KB.

    Raises:
        ValueError: When kb_id is unknown or the Chroma store is unavailable
            (e.g. embedding not configured — store.db returns None).
    """
    kb = kb_manager.get_kb(kb_id)
    if kb is None:
        raise ValueError(f"KB not found: {kb_id}")
    store = kb_manager._get_store(kb)  # noqa: SLF001
    chroma = store.db if store is not None else None
    if chroma is None:
        raise ValueError(f"Chroma store unavailable for KB {kb_id}")
    return chroma


def get_self_query_retriever(
    kb_ids: list[str],
    llm: Optional[BaseLanguageModel] = None,
    metadata_field_info: Optional[list[AttributeInfo]] = None,
) -> SelfQueryRetriever:
    """Factory: build a SelfQueryRetriever from kb_ids via the global singleton.

    Mirrors SearchDocsTool's ``_kb_ids: list[str]`` shape so the integration
    can pass the same kb_ids the tool already receives from frontend config.
    For multiple kb_ids, wraps the first KB and logs a warning
    (SelfQueryRetriever wraps a single Chroma store; cross-KB merge is a
    future enhancement — see module docstring).

    Args:
        kb_ids: List of KB IDs; only the first is wrapped (see limitation).
        llm: Optional LangChain LLM; None → project default ChatOpenAI.
        metadata_field_info: Optional metadata schema; None → default
            hardware-manual schema.

    Returns:
        SelfQueryRetriever wrapping the Chroma store for the first KB.

    Raises:
        ValueError: When kb_ids is empty.
    """
    if not kb_ids:
        raise ValueError("kb_ids must contain at least one KB id")
    if len(kb_ids) > 1:
        logger.warning(
            "self_query_retriever_multi_kb wrapping first only: %s (full=%s)",
            kb_ids[0], kb_ids,
        )
    kb_manager = get_kb_manager()
    return build_self_query_retriever(
        kb_manager, kb_ids[0], llm=llm,
        metadata_field_info=metadata_field_info,
    )
