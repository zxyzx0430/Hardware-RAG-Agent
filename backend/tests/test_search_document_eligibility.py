"""Regression coverage for document eligibility in the RAG retrieval path."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, KnowledgeBase, KnowledgeDoc
from src.rag import kb_manager as kb_manager_module
from src.rag import search as search_module
from src.rag.kb_manager import KnowledgeBaseManager, rrf_fusion
from src.rag.search import search_docs_core
from src.rag.vector_store import SearchResult


class _MemoryStore:
    def __init__(self, vector_results):
        self.vector_results = list(vector_results)
        self.vector_calls = 0

    def search(self, _query, k, score_threshold):
        self.vector_calls += 1
        return self.vector_results[:k]


def _candidate(doc_id: str) -> SearchResult:
    return SearchResult(
        content=f"content for {doc_id}",
        metadata={"doc_id": doc_id, "chunk_index": 0},
        score=0.8,
        doc_id=doc_id,
    )


@pytest.fixture
def rag_manager():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        db.add(KnowledgeBase(
            id="kb-eligibility",
            name="Eligibility test KB",
            collection_name="kb_eligibility_test",
            enabled=True,
            is_builtin=False,
        ))
        db.commit()

    manager = KnowledgeBaseManager(db_session_factory=session_factory)
    store = _MemoryStore([])
    manager._get_store = lambda _kb: store
    manager._bm25_search = lambda _kb_id, _query, k: list(store.vector_results[:k])
    manager._apply_big_chunk_lookup = lambda results: results

    def add_document(doc_id: str, *, status: str = "indexed", error_message=None):
        with session_factory() as db:
            db.add(KnowledgeDoc(
                doc_id=doc_id,
                kb_id="kb-eligibility",
                title=f"{doc_id}.md",
                status=status,
                error_message=error_message,
            ))
            db.commit()

    def set_document_state(doc_id: str, *, status: str, error_message=None):
        with session_factory() as db:
            row = db.query(KnowledgeDoc).filter_by(doc_id=doc_id).one()
            row.status = status
            row.error_message = error_message
            db.commit()

    yield SimpleNamespace(
        manager=manager,
        store=store,
        add_document=add_document,
        set_document_state=set_document_state,
        session_factory=session_factory,
    )
    engine.dispose()


@pytest.mark.parametrize("error_message", [None, ""])
@pytest.mark.asyncio
async def test_only_indexed_documents_without_errors_reach_fusion_and_expansion(
    rag_manager, monkeypatch, error_message,
):
    rag_manager.add_document("valid-doc", error_message=error_message)
    rag_manager.add_document("indexing-doc", status="indexing")
    rag_manager.store.vector_results = [
        _candidate("valid-doc"),
        _candidate("indexing-doc"),
    ]
    fusion_inputs = []
    expanded_ids = []

    def recording_fusion(vector_results, bm25_results):
        fusion_inputs.append((
            {item.doc_id for item in vector_results},
            {item.doc_id for item in bm25_results},
        ))
        return rrf_fusion(vector_results, bm25_results)

    def recording_parent_expansion(results):
        expanded_ids.extend(item.doc_id for item in results)
        return results

    monkeypatch.setattr(kb_manager_module, "rrf_fusion", recording_fusion)
    rag_manager.manager._apply_big_chunk_lookup = recording_parent_expansion

    results = await rag_manager.manager.search_all_enabled("query", k=5)

    assert [item.doc_id for item in results] == ["valid-doc"]
    assert fusion_inputs == [({"valid-doc"}, {"valid-doc"})]
    assert expanded_ids == ["valid-doc"]


@pytest.mark.parametrize(
    ("status", "error_message"),
    [
        ("indexing", None),
        ("error", None),
        ("unknown", None),
        ("indexed", "partial indexing failed"),
    ],
)
@pytest.mark.asyncio
async def test_ineligible_document_never_enters_fusion(
    rag_manager, monkeypatch, status, error_message,
):
    rag_manager.add_document(
        "invalid-doc", status=status, error_message=error_message,
    )
    rag_manager.store.vector_results = [_candidate("invalid-doc")]
    fusion_inputs = []

    def recording_fusion(vector_results, bm25_results):
        fusion_inputs.append((
            {item.doc_id for item in vector_results},
            {item.doc_id for item in bm25_results},
        ))
        return rrf_fusion(vector_results, bm25_results)

    monkeypatch.setattr(kb_manager_module, "rrf_fusion", recording_fusion)
    results = await rag_manager.manager.search_all_enabled("query", k=5)

    assert results == []
    assert fusion_inputs == []
    assert rag_manager.store.vector_calls == 0


@pytest.mark.asyncio
async def test_missing_document_record_is_not_searchable(rag_manager):
    rag_manager.store.vector_results = [_candidate("missing-doc")]

    results = await rag_manager.manager.search_all_enabled("query", k=5)

    assert results == []


def test_standalone_search_requires_an_indexed_document_record(rag_manager):
    rag_manager.add_document("valid-doc")
    rag_manager.add_document("indexing-doc", status="indexing")
    rag_manager.store.vector_results = [
        _candidate("valid-doc"),
        _candidate("indexing-doc"),
    ]

    results = rag_manager.manager.search("kb-eligibility", "query", k=5)

    assert [item.doc_id for item in results] == ["valid-doc"]


def test_standalone_search_drops_missing_records_before_vector_query(rag_manager):
    rag_manager.store.vector_results = [_candidate("missing-doc")]

    results = rag_manager.manager.search("kb-eligibility", "query", k=5)

    assert results == []
    assert rag_manager.store.vector_calls == 0


def test_standalone_search_drops_changed_scope_before_fusion(rag_manager, monkeypatch):
    rag_manager.add_document("valid-doc")
    rag_manager.store.vector_results = [_candidate("valid-doc")]
    fusion_calls = []

    def mutate_during_bm25(_kb_id, _query, _k):
        rag_manager.set_document_state("valid-doc", status="indexing")
        return [_candidate("valid-doc")]

    monkeypatch.setattr(rag_manager.manager, "_bm25_search", mutate_during_bm25)
    monkeypatch.setattr(
        kb_manager_module,
        "rrf_fusion",
        lambda *args, **kwargs: fusion_calls.append((args, kwargs)) or [],
    )

    results = rag_manager.manager.search("kb-eligibility", "query", k=5)

    assert results == []
    assert fusion_calls == []


def test_as_retriever_filters_both_candidate_sources(rag_manager):
    rag_manager.add_document("valid-doc")
    rag_manager.add_document("error-doc", status="error")
    rag_manager.store.vector_results = [
        _candidate("valid-doc"),
        _candidate("error-doc"),
    ]

    retriever = rag_manager.manager.as_retriever("kb-eligibility", k=5)
    results = retriever.invoke("query")

    assert [item.metadata["doc_id"] for item in results] == ["valid-doc"]


def test_as_retriever_fails_before_fusion_if_document_state_changes(rag_manager, monkeypatch):
    import src.rag.rrf_retriever as rrf_module

    rag_manager.add_document("valid-doc")
    rag_manager.store.vector_results = [_candidate("valid-doc")]
    fusion_calls = []

    def mutate_during_bm25(_kb_id, _query, _k):
        rag_manager.set_document_state("valid-doc", status="indexing")
        return [_candidate("valid-doc")]

    monkeypatch.setattr(rag_manager.manager, "_bm25_search", mutate_during_bm25)
    monkeypatch.setattr(
        rrf_module,
        "rrf_fusion",
        lambda *args, **kwargs: fusion_calls.append((args, kwargs)) or [],
    )
    retriever = rag_manager.manager.as_retriever("kb-eligibility", k=5)

    with pytest.raises(RuntimeError, match="changed during BM25 retrieval"):
        retriever.invoke("query")

    assert fusion_calls == []


@pytest.mark.asyncio
async def test_document_state_query_failure_stops_before_vector_search():
    kb = SimpleNamespace(id="kb-eligibility", name="Eligibility test KB", enabled=True)

    class _EnabledKbQuery:
        def filter(self, *_args):
            return self

        def all(self):
            return [kb]

    class _FailingSession:
        def query(self, entity, *_columns):
            if entity is KnowledgeBase:
                return _EnabledKbQuery()
            raise RuntimeError("document state database unavailable")

        def close(self):
            pass

    manager = KnowledgeBaseManager(db_session_factory=_FailingSession)
    store = _MemoryStore([_candidate("valid-doc")])
    manager._get_store = lambda _kb: store

    with pytest.raises(RuntimeError, match="document state database unavailable"):
        await manager.search_all_enabled("query", k=5)

    assert store.vector_calls == 0


@pytest.mark.asyncio
async def test_search_batch_is_dropped_if_source_signature_changes_during_await(monkeypatch):
    signature = {"value": 1}
    retrieval_started = asyncio.Event()
    release_retrieval = asyncio.Event()
    expected_result = [SimpleNamespace(doc_id="stale-doc", content="stale content")]

    class _Manager:
        def get_search_cache_signature(self, _kb_ids=None):
            return (signature["value"],)

        async def search_all_enabled(self, *_args, **_kwargs):
            retrieval_started.set()
            await release_retrieval.wait()
            return expected_result

    monkeypatch.setattr(kb_manager_module, "get_kb_manager", lambda: _Manager())
    search_module._SEARCH_CACHE.clear()
    pending = asyncio.create_task(search_docs_core("query", kb_ids=["kb-eligibility"]))
    await retrieval_started.wait()
    signature["value"] = 2
    release_retrieval.set()

    results = await pending

    assert results == []
    assert search_module._SEARCH_CACHE == {}
