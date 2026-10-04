"""Exercise upload indexing states without touching the user's knowledge base."""

import asyncio
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import kb_routes
from app.db.models import KnowledgeBase, KnowledgeDoc
from src.rag.chunking.base import ChunkResult


class FakeChunker:
    def __init__(self, chunks):
        self.chunks = chunks

    async def chunk(self, **_kwargs):
        return self.chunks


class FakeManager:
    def __init__(self, kb):
        self.kb = kb
        self.ingested = 1
        self.ingest_error = None
        self.embeddings = object()
        self.deleted_doc_ids = []
        self._bm25_stale = set()

    def get_kb(self, _kb_id):
        return self.kb

    def ingest_chunks(self, _kb_id, _chunks, _doc_id):
        if self.ingest_error:
            raise self.ingest_error
        return self.ingested

    def _get_store(self, _kb):
        return SimpleNamespace(embeddings=self.embeddings, delete_document=self.deleted_doc_ids.append)

    def _rebuild_bm25(self, _kb_id):
        return None


@pytest.fixture
def isolated_upload(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    KnowledgeBase.__table__.create(engine)
    KnowledgeDoc.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    kb = KnowledgeBase(id="kb-test", name="Test", collection_name="isolated-test")
    with session_factory() as db:
        db.add(kb)
        db.commit()

    @contextmanager
    def db_context():
        with session_factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    manager = FakeManager(SimpleNamespace(id="kb-test", chunk_method="hybrid", small_chunk_size=800))
    chunks = [ChunkResult(
        text="STM32 GPIO modes are defined by MODER.", metadata={"chunk_index": 0},
        page_range=(1, 1), fingerprint="fixed-fingerprint", chunk_method="hybrid",
    )]
    chunker = FakeChunker(chunks)
    monkeypatch.setattr(kb_routes, "get_db_ctx", db_context)
    monkeypatch.setattr(kb_routes, "_get_kb_manager", lambda: manager)
    monkeypatch.setattr(kb_routes, "_get_kb_chunker", lambda *_args, **_kwargs: chunker)
    monkeypatch.setattr(kb_routes, "UPLOAD_DIR", tmp_path)
    yield manager, chunker, session_factory
    engine.dispose()


async def upload_and_wait(session_factory, filename="fixed.md"):
    file = UploadFile(file=BytesIO(b"# Fixed document\n\nSTM32 GPIO modes"), filename=filename)
    response = await kb_routes.kb_upload(
        file=file, kb_id="kb-test", chunk_method=None, chunk_size=None,
        small_chunk_size=None, user={"id": "test"},
    )
    assert response["success"] is True
    assert response["data"]["status"] == "indexing"
    await asyncio.gather(*tuple(kb_routes._bg_tasks))
    with session_factory() as db:
        doc = db.query(KnowledgeDoc).filter_by(doc_id=response["data"]["doc_id"]).one()
        return response["data"]["doc_id"], doc.status, doc.chunk_count, doc.error_message


@pytest.mark.asyncio
async def test_success_requires_positive_vector_count(isolated_upload):
    _manager, _chunker, sessions = isolated_upload
    _, status, chunks, error = await upload_and_wait(sessions)
    assert (status, chunks, error) == ("indexed", 1, "")


@pytest.mark.asyncio
async def test_no_valid_chunks_is_an_error(isolated_upload):
    manager, chunker, sessions = isolated_upload
    chunker.chunks = []
    _, status, chunks, error = await upload_and_wait(sessions)
    assert (status, chunks) == ("error", 0)
    assert "分块结果为空" in error
    assert manager.deleted_doc_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_embedding", [True, False])
async def test_zero_vectors_is_an_error_with_specific_reason(isolated_upload, missing_embedding):
    manager, _chunker, sessions = isolated_upload
    manager.ingested = 0
    manager.embeddings = None if missing_embedding else object()
    _, status, chunks, error = await upload_and_wait(sessions)
    assert (status, chunks) == ("error", 1)
    assert "不可检索" in error if missing_embedding else "未写入任何可检索向量" in error


@pytest.mark.asyncio
async def test_embedding_failure_can_retry_same_filename(isolated_upload):
    manager, _chunker, sessions = isolated_upload
    manager.ingest_error = RuntimeError("embedding service unavailable")
    first_id, status, _, error = await upload_and_wait(sessions)
    assert status == "error"
    assert "向量化入库失败" in error

    manager.ingest_error = None
    second_id, status, chunks, error = await upload_and_wait(sessions)
    assert second_id != first_id
    assert (status, chunks, error) == ("indexed", 1, "")
    assert first_id in manager.deleted_doc_ids
