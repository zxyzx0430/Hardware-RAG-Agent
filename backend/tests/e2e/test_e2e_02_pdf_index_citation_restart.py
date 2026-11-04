"""E2E 2: upload -> index -> source citation -> restart -> retrieval."""

import asyncio
import json
from contextlib import contextmanager
from io import BytesIO

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import kb_routes
from app.db.models import BigChunk, KnowledgeBase, KnowledgeDoc
from src.agent.exceptions import ToolContext
from src.agent.sse_helpers import build_source_events
from src.agent.tools.groups.retrieval.search_docs import SearchDocsTool
from src.rag import kb_manager as manager_module
from src.rag import search as search_module
from src.rag import vector_store as vector_module
from src.rag.chunking.base import ChunkResult


class FixedEmbeddings:
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0] if "GPIO" in text.upper() else [0.0, 1.0, 0.0]


class FixedChunker:
    async def chunk(self, **_kwargs):
        return [ChunkResult(
            text="STM32 GPIO MODER selects input, output, alternate, and analog modes.",
            metadata={"chunk_index": 0, "title": "fixed.md"},
            page_range=(1, 1),
            fingerprint="fixed-gpio",
            chunk_method="hybrid",
        )]


def _build_store(tmp_path, sessions):
    store = vector_module.HardwareVectorStore(
        collection_name="fixed-chain", persist_dir=tmp_path / "chroma"
    )
    store.embeddings = FixedEmbeddings()
    manager = manager_module.KnowledgeBaseManager(db_session_factory=sessions)
    manager._stores["kb-fixed"] = store
    return manager, store


@pytest.mark.asyncio
async def test_upload_index_source_and_restart_retrieval(monkeypatch, tmp_path):
    db_path = tmp_path / "e2e-chain.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    for model in (KnowledgeBase, KnowledgeDoc, BigChunk):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(KnowledgeBase(
            id="kb-fixed", name="Fixed", collection_name="fixed-chain", enabled=True,
        ))
        db.commit()

    @contextmanager
    def db_context():
        with sessions() as db:
            yield db
            db.commit()

    manager, _store = _build_store(tmp_path, sessions)
    monkeypatch.setattr(vector_module, "SessionLocal", sessions)
    monkeypatch.setattr(manager_module, "BM25_DIR", tmp_path / "bm25")
    monkeypatch.setattr(kb_routes, "UPLOAD_DIR", tmp_path / "uploads")
    kb_routes.UPLOAD_DIR.mkdir()
    monkeypatch.setattr(kb_routes, "get_db_ctx", db_context)
    monkeypatch.setattr(kb_routes, "_get_kb_manager", lambda: manager)
    monkeypatch.setattr(kb_routes, "_get_kb_chunker", lambda *_a, **_kw: FixedChunker())
    monkeypatch.setattr(manager_module, "get_kb_manager", lambda: manager)
    search_module._SEARCH_CACHE.clear()
    try:
        response = await kb_routes.kb_upload(
            file=UploadFile(
                file=BytesIO(b"# GPIO\n\nMODER selects modes."), filename="fixed.md",
            ),
            kb_id="kb-fixed",
            chunk_method="hybrid",
            chunk_size=None,
            small_chunk_size=None,
            user={"id": "isolated"},
        )
        assert response["data"]["status"] == "indexing"
        await asyncio.gather(*tuple(kb_routes._bg_tasks))
        with sessions() as db:
            doc = db.query(KnowledgeDoc).filter_by(
                doc_id=response["data"]["doc_id"]
            ).one()
            assert doc.status == "indexed"
            assert doc.chunk_count == 1

        result = await SearchDocsTool(kb_ids=["kb-fixed"]).execute(
            {"query": "STM32 GPIO MODER"}, ToolContext()
        )
        assert result["results"]
        source_events = [
            json.loads(item.removeprefix("data: ").strip())
            for item in build_source_events({"data": result})
        ]
        assert source_events[0]["id"].startswith("src")

        restarted_manager, _restarted_store = _build_store(tmp_path, sessions)
        monkeypatch.setattr(kb_routes, "_get_kb_manager", lambda: restarted_manager)
        monkeypatch.setattr(manager_module, "get_kb_manager", lambda: restarted_manager)
        search_module._SEARCH_CACHE.clear()

        restarted = await SearchDocsTool(kb_ids=["kb-fixed"]).execute(
            {"query": "STM32 GPIO MODER"}, ToolContext()
        )
        assert restarted["results"]
        assert "GPIO MODER" in str(restarted["results"])
    finally:
        search_module._SEARCH_CACHE.clear()
        engine.dispose()
