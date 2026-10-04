"""Exercise nonblocking KB indexing and deletion races with isolated storage."""

import asyncio
import ast
import contextvars
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import kb_routes
from app.db.models import KnowledgeBase, KnowledgeDoc
from src.rag.chunking.base import ChunkResult


class FakeChunker:
    async def chunk(self, **_kwargs):
        return [ChunkResult(
            text="indexed test text", metadata={"chunk_index": 0},
            page_range=(1, 1), fingerprint="index-test-fingerprint",
            chunk_method="hybrid",
        )]


class FakeStore:
    def __init__(self):
        self.embeddings = object()
        self.deleted_doc_ids = []

    def delete_document(self, doc_id):
        self.deleted_doc_ids.append(doc_id)
        return 0

    def delete_collection(self):
        return None


class FakeManager:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.kb_alive = True
        self.kb = SimpleNamespace(
            id="kb-test", name="Test KB", is_builtin=False,
            chunk_method="hybrid", small_chunk_size=800,
        )
        self.store = FakeStore()
        self.ingest_started = None
        self.release_ingest = None
        self.ingest_error = None
        self.ingest_calls = []
        self._bm25_stale = set()

    def get_kb(self, _kb_id):
        return self.kb if self.kb_alive else None

    def _get_store(self, _kb):
        return self.store

    def ingest_chunks(self, kb_id, chunks, doc_id):
        self.ingest_calls.append((kb_id, doc_id))
        if self.ingest_started is not None:
            self.ingest_started.set()
            if not self.release_ingest.wait(5):
                raise TimeoutError("test ingest release was not signalled")
        if self.ingest_error is not None:
            raise self.ingest_error
        return len(chunks)

    def _rebuild_bm25(self, _kb_id):
        return None

    def invalidate_search_cache(self):
        return None

    def delete_kb(self, kb_id):
        if not self.kb_alive or kb_id != self.kb.id:
            return False
        self.store.delete_collection()
        with self.session_factory() as db:
            db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).delete()
            kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
            if kb:
                db.delete(kb)
            db.commit()
        self.kb_alive = False
        return True


@pytest.fixture
def isolated_index(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'index-test.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    KnowledgeBase.__table__.create(engine)
    KnowledgeDoc.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        db.add(KnowledgeBase(
            id="kb-test", name="Test KB", collection_name="index_responsiveness",
            enabled=True, is_builtin=False,
        ))
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

    manager = FakeManager(session_factory)
    app = FastAPI()
    app.include_router(kb_routes.router)
    monkeypatch.setattr(kb_routes, "get_db_ctx", db_context)
    monkeypatch.setattr(kb_routes, "_get_kb_manager", lambda: manager)
    monkeypatch.setattr(kb_routes, "_get_kb_chunker", lambda *_a, **_k: FakeChunker())
    monkeypatch.setattr(kb_routes, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(kb_routes, "_INDEX_SEMAPHORE", asyncio.Semaphore(2))
    kb_routes.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with kb_routes._ACTIVE_INDEX_LOCK:
        kb_routes._ACTIVE_INDEX_DOC_IDS.clear()

    state = SimpleNamespace(
        app=app, manager=manager, session_factory=session_factory,
    )
    yield state
    engine.dispose()


async def wait_thread_event(event: threading.Event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 5), timeout=6)


async def start_upload(state, filename="test.md"):
    previous = set(kb_routes._bg_tasks)
    response = await kb_routes.kb_upload(
        file=UploadFile(file=BytesIO(b"# Indexed test document"), filename=filename),
        kb_id="kb-test", chunk_method=None, chunk_size=None,
        small_chunk_size=None, user={"id": "test"},
    )
    assert response["success"] is True
    task = next(iter(set(kb_routes._bg_tasks) - previous))
    return response["data"]["doc_id"], task


async def fetch_status(client, doc_id):
    response = await client.get("/api/kb/list", params={"kb_id": "kb-test"})
    assert response.status_code == 200
    return next(
        row for row in response.json()["data"]["documents"]
        if row["doc_id"] == doc_id
    )


def _runner_shutdown_program():
    module = ast.parse(Path(kb_routes.__file__).read_text(encoding="utf-8"))
    helper = next(
        node for node in ast.walk(module)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_index_worker"
    )
    helper_source = ast.unparse(helper)
    return f'''import asyncio
import contextvars
import json
import threading
import time

{helper_source}

DOC_ID = "runner-shutdown-doc"
started = threading.Event()
release = threading.Event()
finished = threading.Event()
permit = asyncio.Semaphore(1)
active_docs = {{DOC_ID}}
status = {{"value": "indexing"}}

def blocking_worker():
    started.set()
    release.wait(3)
    finished.set()
    return "worker-finished"

def snapshot():
    return {{
        "worker_finished": finished.is_set(),
        "status": status["value"],
        "permit_held": permit.locked(),
        "doc_active": DOC_ID in active_docs,
    }}

def observe_runner_cancel(task):
    deadline = time.monotonic() + 3
    while task.cancelling() == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    if task.cancelling() == 0:
        print("RUNNER_CANCEL_NOT_OBSERVED", flush=True)
        release.set()
        return
    print("RUNNER_CANCEL_REACHED", flush=True)
    time.sleep(0.2)
    print("BLOCKED_STATE=" + json.dumps(snapshot(), sort_keys=True), flush=True)
    release.set()

async def route_task():
    try:
        async with permit:
            _result, cancellation_requested = await _run_index_worker(blocking_worker)
            status["value"] = "error" if cancellation_requested else "indexed"
    except asyncio.CancelledError:
        status["value"] = "error"
        raise
    finally:
        active_docs.discard(DOC_ID)

async def main():
    task = asyncio.create_task(route_task())
    started_ok = await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
    if not started_ok:
        raise RuntimeError("worker thread did not start")
    print("THREAD_STARTED", flush=True)
    threading.Thread(target=observe_runner_cancel, args=(task,), daemon=True).start()

asyncio.run(main())
print("FINAL_STATE=" + json.dumps({{
    **snapshot(),
    "permit_released": not permit.locked(),
}}, sort_keys=True), flush=True)
'''


def _child_environment_without_credentials():
    environment = os.environ.copy()
    secret_markers = (
        "API_KEY", "API_TOKEN", "HF_TOKEN", "HUGGINGFACE", "OPENAI",
        "ANTHROPIC", "GEMINI", "SECRET",
    )
    for name in list(environment):
        if any(marker in name.upper() for marker in secret_markers):
            environment.pop(name, None)
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    return environment


@pytest.mark.asyncio
async def test_runner_shutdown_waits_for_real_index_worker_thread():
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", _runner_shutdown_program()],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_child_environment_without_credentials(),
    )
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired as timeout_error:
        partial_output = timeout_error.stdout or ""
        if isinstance(partial_output, bytes):
            partial_output = partial_output.decode(errors="replace")
        process.terminate()
        try:
            output, _ = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
        if not output:
            output = partial_output
        assert "THREAD_STARTED" in output, (
            f"helper subprocess did not reach its worker-start marker; "
            f"pid={process.pid}; output={output!r}"
        )
        assert "RUNNER_CANCEL_REACHED" in output, (
            f"helper subprocess timed out before runner cancellation was "
            f"confirmed; pid={process.pid}; output={output!r}"
        )
        assert "BLOCKED_STATE=" in output, (
            f"helper subprocess timed out before the blocked-state snapshot; "
            f"pid={process.pid}; output={output!r}"
        )
        pytest.fail(
            "real helper remained stuck after confirmed asyncio runner "
            f"cancellation; child pid {process.pid} was terminated\n{output}"
        )

    assert process.returncode == 0, output
    assert "THREAD_STARTED" in output
    assert "RUNNER_CANCEL_REACHED" in output
    blocked_line = next(
        line for line in output.splitlines()
        if line.startswith("BLOCKED_STATE=")
    )
    blocked_state = json.loads(blocked_line.partition("=")[2])
    assert blocked_state == {
        "worker_finished": False,
        "status": "indexing",
        "permit_held": True,
        "doc_active": True,
    }
    final_line = next(
        line for line in output.splitlines()
        if line.startswith("FINAL_STATE=")
    )
    final_state = json.loads(final_line.partition("=")[2])
    assert final_state == {
        "worker_finished": True,
        "status": "error",
        "permit_held": False,
        "doc_active": False,
        "permit_released": True,
    }


@pytest.mark.asyncio
async def test_run_index_worker_copies_contextvars():
    request_id = contextvars.ContextVar("request_id", default="missing")
    token = request_id.set("upload-request-17")
    try:
        result, cancellation_requested = await kb_routes._run_index_worker(
            request_id.get,
        )
    finally:
        request_id.reset(token)

    assert result == "upload-request-17"
    assert cancellation_requested is False


@pytest.mark.asyncio
async def test_run_index_worker_propagates_worker_cancelled_error():
    def raise_cancelled_error():
        raise asyncio.CancelledError("worker-raised cancellation")

    with pytest.raises(asyncio.CancelledError) as raised:
        await kb_routes._run_index_worker(raise_cancelled_error)

    assert str(raised.value) == "worker-raised cancellation"


@pytest.mark.asyncio
async def test_run_index_worker_propagates_worker_exception():
    expected = RuntimeError("worker-raised failure")

    def raise_worker_exception():
        raise expected

    with pytest.raises(RuntimeError) as raised:
        await kb_routes._run_index_worker(raise_worker_exception)

    assert raised.value is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage", ["parse", "ingest"])
async def test_blocking_index_work_does_not_block_asgi_list(
    isolated_index, monkeypatch, blocked_stage,
):
    started = threading.Event()
    release = threading.Event()
    if blocked_stage == "parse":
        def blocking_parser(*_args):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test parser release was not signalled")
            return "parsed test text", 0

        monkeypatch.setattr(kb_routes, "_parse_file", blocking_parser)
    else:
        monkeypatch.setattr(
            kb_routes, "_parse_file", lambda *_args: ("parsed test text", 0),
        )
        isolated_index.manager.ingest_started = started
        isolated_index.manager.release_ingest = release

    doc_id, task = await start_upload(isolated_index)
    try:
        await wait_thread_event(started)
        async with AsyncClient(
            transport=ASGITransport(app=isolated_index.app),
            base_url="http://test",
        ) as client:
            start = time.perf_counter()
            response = await asyncio.wait_for(
                client.get("/api/kb/list", params={"kb_id": "kb-test"}),
                timeout=1.0,
            )
            elapsed = time.perf_counter() - start
        assert response.status_code == 200
        assert elapsed < 1.0
        assert any(
            row["doc_id"] == doc_id
            for row in response.json()["data"]["documents"]
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_index_concurrency_bound_survives_task_cancellation(
    isolated_index, monkeypatch,
):
    class ObservedSemaphore:
        def __init__(self, limit):
            self.inner = asyncio.Semaphore(limit)
            self.attempts = 0
            self.waiting = 0
            self.third_attempted = asyncio.Event()

        async def __aenter__(self):
            self.attempts += 1
            if self.attempts == 3:
                self.third_attempted.set()
            self.waiting += 1
            try:
                await self.inner.acquire()
            finally:
                self.waiting -= 1
            return self

        async def __aexit__(self, *_exc):
            self.inner.release()

    semaphore = ObservedSemaphore(2)
    monkeypatch.setattr(kb_routes, "_INDEX_SEMAPHORE", semaphore)
    release = threading.Event()
    two_active = threading.Event()
    active_lock = threading.Lock()
    active = 0
    parser_calls = 0
    maximum_active = 0

    def blocking_parser(*_args):
        nonlocal active, parser_calls, maximum_active
        with active_lock:
            parser_calls += 1
            active += 1
            maximum_active = max(maximum_active, active)
            if active == 2:
                two_active.set()
        if not release.wait(5):
            raise TimeoutError("test parser release was not signalled")
        with active_lock:
            active -= 1
        return "parsed test text", 0

    monkeypatch.setattr(kb_routes, "_parse_file", blocking_parser)
    docs_and_tasks = [
        await start_upload(isolated_index, f"test-{index}.md")
        for index in range(3)
    ]
    try:
        await wait_thread_event(two_active)
        await asyncio.wait_for(semaphore.third_attempted.wait(), timeout=2)
        assert parser_calls == 2
        assert maximum_active == 2
        assert semaphore.waiting == 1

        for _ in range(3):
            docs_and_tasks[0][1].cancel()
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not docs_and_tasks[0][1].done()
        assert docs_and_tasks[0][1].cancelling() == 3
        assert semaphore.waiting == 1
        assert parser_calls == 2
        assert kb_routes._is_index_task_active(docs_and_tasks[0][0])
        with isolated_index.session_factory() as db:
            held_record = db.query(KnowledgeDoc).filter_by(
                doc_id=docs_and_tasks[0][0]
            ).one()
            assert held_record.status == "indexing"

    finally:
        release.set()
        await asyncio.gather(
            *(task for _doc_id, task in docs_and_tasks),
            return_exceptions=True,
        )

    assert maximum_active == 2
    assert len(isolated_index.manager.ingest_calls) == 2
    assert not kb_routes._is_index_task_active(docs_and_tasks[0][0])
    with isolated_index.session_factory() as db:
        statuses = {
            row.doc_id: (row.status, row.error_message)
            for row in db.query(KnowledgeDoc).all()
        }
    assert statuses[docs_and_tasks[0][0]][0] == "error"
    assert "取消" in statuses[docs_and_tasks[0][0]][1]
    assert all(
        statuses[doc_id][0] == "indexed"
        for doc_id, _task in docs_and_tasks[1:]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_kind", ["document", "knowledge_base"])
async def test_deleted_target_is_not_resurrected_by_late_parser(
    isolated_index, monkeypatch, delete_kind,
):
    started = threading.Event()
    release = threading.Event()

    def blocking_parser(*_args):
        started.set()
        if not release.wait(5):
            raise TimeoutError("test parser release was not signalled")
        return "parsed after deletion", 0

    monkeypatch.setattr(kb_routes, "_parse_file", blocking_parser)
    doc_id, task = await start_upload(isolated_index)
    try:
        await wait_thread_event(started)
        async with AsyncClient(
            transport=ASGITransport(app=isolated_index.app),
            base_url="http://test",
        ) as client:
            if delete_kind == "document":
                response = await client.post("/api/kb/delete", json={"doc_id": doc_id})
            else:
                response = await client.delete("/api/kb/collections/kb-test")
        assert response.status_code == 200
        assert response.json()["success"] is True
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    assert isolated_index.manager.ingest_calls == []
    with isolated_index.session_factory() as db:
        assert db.query(KnowledgeDoc).filter_by(doc_id=doc_id).first() is None
        if delete_kind == "knowledge_base":
            assert db.query(KnowledgeBase).filter_by(id="kb-test").first() is None


@pytest.mark.asyncio
async def test_document_delete_serializes_with_inflight_ingest(
    isolated_index, monkeypatch,
):
    class ObservedMutationLock:
        def __init__(self):
            self.inner = threading.Lock()
            self.attempts = 0
            self.guard = threading.Lock()
            self.second_attempted = threading.Event()

        def __enter__(self):
            with self.guard:
                self.attempts += 1
                if self.attempts == 2:
                    self.second_attempted.set()
            self.inner.acquire()
            return self

        def __exit__(self, *_exc):
            self.inner.release()

    mutation_lock = ObservedMutationLock()
    monkeypatch.setattr(kb_routes, "_INDEX_MUTATION_LOCK", mutation_lock)
    monkeypatch.setattr(
        kb_routes, "_parse_file", lambda *_args: ("parsed test text", 0),
    )
    isolated_index.manager.ingest_started = threading.Event()
    isolated_index.manager.release_ingest = threading.Event()
    doc_id, task = await start_upload(isolated_index)
    try:
        await wait_thread_event(isolated_index.manager.ingest_started)
        async with AsyncClient(
            transport=ASGITransport(app=isolated_index.app),
            base_url="http://test",
        ) as client:
            delete_task = asyncio.create_task(
                client.post("/api/kb/delete", json={"doc_id": doc_id})
            )
            await wait_thread_event(mutation_lock.second_attempted)
            assert not delete_task.done()
            isolated_index.manager.release_ingest.set()
            response = await asyncio.wait_for(delete_task, timeout=2)
        assert response.status_code == 200
        assert response.json()["success"] is True
    finally:
        isolated_index.manager.release_ingest.set()
        await asyncio.gather(task, return_exceptions=True)

    with isolated_index.session_factory() as db:
        assert db.query(KnowledgeDoc).filter_by(doc_id=doc_id).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["parse", "parse_cancelled", "ingest"])
async def test_parser_and_ingester_failures_retain_error_status(
    isolated_index, monkeypatch, failure_stage,
):
    if failure_stage == "parse_cancelled":
        def cancel_parser(*_args):
            raise asyncio.CancelledError("controlled worker cancellation")

        monkeypatch.setattr(kb_routes, "_parse_file", cancel_parser)
    elif failure_stage == "parse":
        def fail_parser(*_args):
            raise ValueError("controlled parser failure")

        monkeypatch.setattr(kb_routes, "_parse_file", fail_parser)
    else:
        monkeypatch.setattr(
            kb_routes, "_parse_file", lambda *_args: ("parsed test text", 0),
        )
        isolated_index.manager.ingest_error = RuntimeError("controlled ingest failure")

    doc_id, task = await start_upload(isolated_index)
    await asyncio.gather(task, return_exceptions=True)
    with isolated_index.session_factory() as db:
        record = db.query(KnowledgeDoc).filter_by(doc_id=doc_id).one()
        assert record.status == "error"
        assert record.error_message
        assert not kb_routes._is_index_task_active(doc_id)
        if failure_stage == "ingest":
            assert "向量化入库失败" in record.error_message
        if failure_stage == "parse_cancelled":
            assert "取消" in record.error_message
