"""The explorer watcher filters DB artifacts and coalesces duplicate events."""

from __future__ import annotations

import asyncio

from watchdog.events import FileModifiedEvent, FileMovedEvent

from src.explorer import watcher


def _handler(tmp_path):
    handler = watcher._ExplorerEventHandler(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    handler.add_queue("q", queue)
    return handler, queue


def _drain(queue: asyncio.Queue) -> list[dict]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def test_database_artifacts_and_churn_dirs_are_filtered(tmp_path):
    handler, queue = _handler(tmp_path)

    for raw in (
        tmp_path / "hardware_rag.db-wal",
        tmp_path / "hardware_rag.db-shm",
        tmp_path / "agent_checkpoints.sqlite-wal",
        tmp_path / "agent_checkpoints.sqlite-shm",
        tmp_path / ".git" / "index",
        tmp_path / "node_modules" / "pkg" / "index.js",
        tmp_path / "__pycache__" / "module.pyc",
    ):
        handler.on_any_event(FileModifiedEvent(str(raw)))

    assert _drain(queue) == []


def test_normal_file_change_is_broadcast_once_then_coalesced(tmp_path, monkeypatch):
    handler, queue = _handler(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(watcher, "_clock", lambda: now[0])
    target = tmp_path / "src" / "main.ino"

    handler.on_any_event(FileModifiedEvent(str(target)))
    handler.on_any_event(FileModifiedEvent(str(target)))
    assert len(_drain(queue)) == 1

    now[0] += watcher._COALESCE_WINDOW_SECONDS + 0.01
    handler.on_any_event(FileModifiedEvent(str(target)))
    assert len(_drain(queue)) == 1


def test_distinct_paths_are_not_coalesced(tmp_path, monkeypatch):
    handler, queue = _handler(tmp_path)
    monkeypatch.setattr(watcher, "_clock", lambda: 2000.0)

    handler.on_any_event(FileModifiedEvent(str(tmp_path / "a.txt")))
    handler.on_any_event(FileModifiedEvent(str(tmp_path / "b.txt")))

    paths = {event["path"] for event in _drain(queue)}
    assert paths == {str(tmp_path / "a.txt"), str(tmp_path / "b.txt")}


def test_move_from_hidden_dir_to_visible_file_is_kept(tmp_path, monkeypatch):
    handler, queue = _handler(tmp_path)
    monkeypatch.setattr(watcher, "_clock", lambda: 3000.0)

    handler.on_any_event(
        FileMovedEvent(
            str(tmp_path / "__pycache__" / "module.pyc"),
            str(tmp_path / "module.pyc"),
        )
    )

    events = _drain(queue)
    assert len(events) == 1
    assert events[0]["type"] == "rename"
    assert events[0]["dest_path"] == str(tmp_path / "module.pyc")
