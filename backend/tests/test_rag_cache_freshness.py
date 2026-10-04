"""Regression coverage for search-cache freshness across KB lifecycle changes."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import kb_routes
from app.db.models import Base, KnowledgeBase, KnowledgeDoc
from src.rag import kb_manager as kb_manager_module
from src.rag.kb_manager import KnowledgeBaseManager
from src.rag.search import search_docs_core, _SEARCH_CACHE


class MemoryStore:
    def __init__(self, documents):
        self.documents = documents
        self.ingest_error = None

    def delete_collection(self):
        self.documents.clear()

    def delete_document(self, doc_id):
        return int(self.documents.pop(doc_id, None) is not None)

    def ingest_chunks(self, chunks, doc_id):
        self.documents[doc_id] = chunks[0].text
        if self.ingest_error is not None:
            raise self.ingest_error
        return len(chunks)

    def import_data(self, data):
        documents = data.get("documents", [])
        metadatas = data.get("metadatas", [])
        for index, content in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            doc_id = metadata.get("doc_id", f"imported-{index}")
            self.documents[doc_id] = content
        return len(documents)


@pytest.fixture
def isolated_kb(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def db_context():
        db = session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    with session_factory() as db:
        db.add(KnowledgeBase(
            id="kb-test", name="Test KB", collection_name="kb_test_cache_freshness",
            enabled=True, is_builtin=False,
        ))
        db.add(KnowledgeDoc(
            doc_id="doc-test", kb_id="kb-test", title="manual.md",
            status="indexed", chunk_count=1,
        ))
        db.commit()

    documents = {"doc-test": "cached original content"}
    store = MemoryStore(documents)
    stores = {"kb-test": store}
    manager = KnowledgeBaseManager(db_session_factory=session_factory)
    manager._get_store = lambda kb: stores.setdefault(
        kb.id, MemoryStore({}),
    )
    manager._rebuild_bm25 = lambda _kb_id: None
    manager._delete_big_chunks_by_kb = lambda _kb_id: None
    calls = {"count": 0}
    delayed = {"started": None, "release": None}

    async def search_all_enabled(
        _query, k=5, kb_ids=None, score_threshold=0.0, doc_filter="",
    ):
        calls["count"] += 1
        if kb_ids:
            scoped_ids = kb_ids
        else:
            scoped_ids = [
                row["id"] for row in manager.list_kbs() if row["enabled"]
            ]

        results = []
        for scoped_id in scoped_ids:
            kb = manager.get_kb(scoped_id)
            if not kb or not kb.enabled:
                continue
            results.extend(
                {"kb_id": scoped_id, "doc_id": doc_id, "content": content}
                for doc_id, content in manager._get_store(kb).documents.items()
            )
        snapshot = results[:k]

        if delayed["started"] is not None and calls["count"] == 1:
            delayed["started"].set()
            await delayed["release"].wait()

        return snapshot

    manager.search_all_enabled = search_all_enabled
    monkeypatch.setattr(kb_manager_module, "get_kb_manager", lambda: manager)
    monkeypatch.setattr(kb_routes, "_get_kb_manager", lambda: manager)
    monkeypatch.setattr(kb_routes, "get_db_ctx", db_context)
    monkeypatch.setattr(kb_routes, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(kb_manager_module, "BM25_DIR", tmp_path / "bm25")
    kb_routes.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _SEARCH_CACHE.clear()

    yield SimpleNamespace(
        manager=manager,
        documents=documents,
        stores=stores,
        calls=calls,
        delayed=delayed,
        session_factory=session_factory,
    )

    _SEARCH_CACHE.clear()
    engine.dispose()


@pytest.mark.parametrize("scope", [None, ["kb-test"]], ids=["all-enabled", "explicit-kb"])
@pytest.mark.asyncio
async def test_deleted_kb_never_returns_cached_content(isolated_kb, scope):
    first = await search_docs_core("freshness", kb_ids=scope)
    assert [item["content"] for item in first] == ["cached original content"]

    deleted = kb_routes.delete_collection("kb-test")
    assert deleted["success"] is True

    second = await search_docs_core("freshness", kb_ids=scope)
    assert second == []
    assert isolated_kb.calls["count"] == 2


@pytest.mark.parametrize("scope", [None, ["kb-test"]], ids=["all-enabled", "explicit-kb"])
@pytest.mark.asyncio
async def test_document_delete_never_returns_cached_content(isolated_kb, scope):
    first = await search_docs_core("freshness", kb_ids=scope)
    assert [item["content"] for item in first] == ["cached original content"]

    deleted = await kb_routes.kb_delete(
        kb_routes.KbDeleteRequest(doc_id="doc-test"), user={"id": "test"},
    )
    assert deleted["success"] is True

    second = await search_docs_core("freshness", kb_ids=scope)
    assert second == []
    assert isolated_kb.calls["count"] == 2


@pytest.mark.parametrize("scope", [None, ["kb-test"]], ids=["all-enabled", "explicit-kb"])
@pytest.mark.asyncio
async def test_kb_toggle_changes_cached_search_scope_immediately(isolated_kb, scope):
    first = await search_docs_core("freshness", kb_ids=scope)
    assert len(first) == 1

    assert isolated_kb.manager.toggle_kb("kb-test", False) is True
    assert await search_docs_core("freshness", kb_ids=scope) == []

    assert isolated_kb.manager.toggle_kb("kb-test", True) is True
    refreshed = await search_docs_core("freshness", kb_ids=scope)
    assert [item["content"] for item in refreshed] == ["cached original content"]
    assert isolated_kb.calls["count"] == 3


@pytest.mark.parametrize("scope", [None, ["kb-test"]], ids=["all-enabled", "explicit-kb"])
@pytest.mark.asyncio
async def test_reingest_replaces_cached_document_content(isolated_kb, scope):
    first = await search_docs_core("freshness", kb_ids=scope)
    assert [item["content"] for item in first] == ["cached original content"]

    ingested = isolated_kb.manager.ingest_chunks(
        "kb-test",
        [SimpleNamespace(
            metadata={}, fingerprint="new-fingerprint", section_title="",
            text="reindexed content",
        )],
        "doc-test",
    )
    assert ingested == 1

    refreshed = await search_docs_core("freshness", kb_ids=scope)
    assert [item["content"] for item in refreshed] == ["reindexed content"]
    assert isolated_kb.calls["count"] == 2


@pytest.mark.asyncio
async def test_successful_import_invalidates_cached_content(isolated_kb):
    await search_docs_core("freshness")

    imported = isolated_kb.manager.import_kb("kb-test", {
        "data": {
            "documents": ["imported content"],
            "metadatas": [{"doc_id": "doc-imported"}],
        },
    })
    assert imported == 1

    refreshed = await search_docs_core("freshness")
    assert {item["content"] for item in refreshed} == {
        "cached original content", "imported content",
    }
    assert isolated_kb.calls["count"] == 2


@pytest.mark.asyncio
async def test_kb_config_update_invalidates_cached_search(isolated_kb):
    await search_docs_core("freshness", kb_ids=["kb-test"])

    updated = isolated_kb.manager.update_kb_config(
        "kb-test", description="Updated configuration",
    )
    assert updated.description == "Updated configuration"

    refreshed = await search_docs_core("freshness", kb_ids=["kb-test"])
    assert [item["content"] for item in refreshed] == ["cached original content"]
    assert isolated_kb.calls["count"] == 2


@pytest.mark.asyncio
async def test_new_enabled_kb_changes_default_cache_scope(isolated_kb):
    first = await search_docs_core("freshness")
    assert {item["content"] for item in first} == {"cached original content"}

    added = isolated_kb.manager.create_kb(name="Second enabled KB")
    isolated_kb.stores[added.id] = MemoryStore({"doc-second": "second KB content"})

    refreshed = await search_docs_core("freshness")
    assert {item["content"] for item in refreshed} == {
        "cached original content", "second KB content",
    }
    assert isolated_kb.calls["count"] == 2


@pytest.mark.asyncio
async def test_partial_ingest_failure_still_invalidates_cache(isolated_kb):
    await search_docs_core("freshness", kb_ids=["kb-test"])
    isolated_kb.stores["kb-test"].ingest_error = RuntimeError("partial store failure")

    with pytest.raises(RuntimeError, match="partial store failure"):
        isolated_kb.manager.ingest_chunks(
            "kb-test",
            [SimpleNamespace(
                metadata={}, fingerprint="partial-fingerprint", section_title="",
                text="partially written content",
            )],
            "doc-test",
        )

    refreshed = await search_docs_core("freshness", kb_ids=["kb-test"])
    assert [item["content"] for item in refreshed] == ["partially written content"]
    assert isolated_kb.calls["count"] == 2


@pytest.mark.asyncio
async def test_inflight_old_retrieval_cannot_repopulate_usable_cache(isolated_kb):
    isolated_kb.delayed["started"] = asyncio.Event()
    isolated_kb.delayed["release"] = asyncio.Event()
    old_request = asyncio.create_task(
        search_docs_core("in-flight", kb_ids=["kb-test"])
    )
    await asyncio.wait_for(isolated_kb.delayed["started"].wait(), timeout=2)

    assert isolated_kb.manager.toggle_kb("kb-test", False) is True
    isolated_kb.delayed["release"].set()
    old_result = await old_request
    # The in-flight caller may receive its snapshot or a safely discarded result.
    assert old_result in ([{"kb_id": "kb-test", "doc_id": "doc-test", "content": "cached original content"}], [])

    current_result = await search_docs_core("in-flight", kb_ids=["kb-test"])
    assert current_result == []
    assert isolated_kb.calls["count"] == 2
