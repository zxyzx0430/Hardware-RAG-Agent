"""Filesystem watcher — SSE broadcaster using watchdog."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

WatchEvent = dict[str, Any]

# Identical events within this window are broadcast once. A single agent turn
# that writes the SQLite databases produces back-to-back WAL notifications;
# without coalescing the UI receives a toast storm.
_COALESCE_WINDOW_SECONDS: float = 0.75
_MAX_COALESCE_ENTRIES: int = 512

# Patchable monotonic clock (tests advance it without sleeping).
_clock = time.monotonic

# High-churn directories that never carry user-visible project changes.
_IGNORED_DIR_NAMES: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", "node_modules", ".pio", ".platformio",
})

# SQLite sidecar files: the app's own DB writes must not surface as file
# changes. The main DB and the LangGraph checkpoint DB both use WAL.
_DB_ARTIFACT_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal")
_DB_FILE_SUFFIXES: tuple[str, ...] = (".db", ".sqlite", ".sqlite3")


def _is_database_artifact(name: str) -> bool:
    """True for ``*.db-wal`` / ``*.sqlite-shm`` / ``*.db-journal`` files."""
    for suffix in _DB_ARTIFACT_SUFFIXES:
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            return base.endswith(_DB_FILE_SUFFIXES) or base in {
                "hardware_rag", "agent_checkpoints",
            }
    return False


def _is_hidden_path(raw_path: str) -> bool:
    """True when the path is watcher noise (DB artifacts or churn directories)."""
    path = Path(raw_path)
    if any(part in _IGNORED_DIR_NAMES for part in path.parts):
        return True
    return _is_database_artifact(path.name)


class _ExplorerEventHandler(FileSystemEventHandler):
    """Broadcast filesystem events to all registered client queues."""

    def __init__(self, root: Path):
        self.root = root
        self._queues: dict[str, asyncio.Queue[WatchEvent]] = {}
        self._recent: dict[tuple[str, str, bool], float] = {}
        self._lock = threading.Lock()

    def add_queue(self, queue_id: str, queue: asyncio.Queue[WatchEvent]) -> None:
        with self._lock:
            self._queues[queue_id] = queue

    def remove_queue(self, queue_id: str) -> None:
        with self._lock:
            self._queues.pop(queue_id, None)

    def on_any_event(self, event: FileSystemEvent) -> None:
        payload = self._build_payload(event)
        if payload and self._should_broadcast(payload):
            self._broadcast(payload)

    def _should_broadcast(self, payload: WatchEvent) -> bool:
        """Coalesce identical (type, path) events inside the debounce window."""
        key = (payload["type"], payload["path"], bool(payload["is_directory"]))
        now = _clock()
        with self._lock:
            last = self._recent.get(key)
            if last is not None and now - last < _COALESCE_WINDOW_SECONDS:
                return False
            if len(self._recent) >= _MAX_COALESCE_ENTRIES:
                cutoff = now - _COALESCE_WINDOW_SECONDS
                self._recent = {
                    item: seen for item, seen in self._recent.items() if seen >= cutoff
                }
                if len(self._recent) >= _MAX_COALESCE_ENTRIES:
                    self._recent.clear()
            self._recent[key] = now
            return True

    def _build_payload(self, event: FileSystemEvent) -> WatchEvent | None:
        if event.event_type == "modified" and event.is_directory:
            return None
        event_types = {
            "modified": "change",
            "created": "create",
            "deleted": "delete",
            "moved": "rename",
        }
        normalized_type = event_types.get(event.event_type)
        if normalized_type is None:
            return None
        source_path = str(Path(event.src_path))
        dest_path = str(Path(event.dest_path)) if event.dest_path else None
        if _is_hidden_path(source_path) and (
            dest_path is None or _is_hidden_path(dest_path)
        ):
            return None
        payload: WatchEvent = {
            "type": normalized_type,
            "path": source_path,
            "is_directory": event.is_directory,
        }
        if event.event_type == "moved" and dest_path:
            payload["src_path"] = source_path
            payload["dest_path"] = dest_path
        return payload

    def _broadcast(self, payload: WatchEvent) -> None:
        with self._lock:
            queues = list(self._queues.items())
        for queue_id, queue in queues:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("watch queue full, dropping event for %s", queue_id)


class _WatchSession:
    """A single observer watching one root path."""

    def __init__(self, root: Path):
        self.root = root
        self.handler = _ExplorerEventHandler(root)
        self.observer = Observer()
        self.ref_count = 0

    def start(self) -> bool:
        try:
            self.observer.schedule(self.handler, str(self.root), recursive=True)
            self.observer.start()
            return True
        except Exception as exc:
            logger.exception("failed to start watcher for %s: %s", self.root, exc)
            return False

    def stop(self) -> None:
        try:
            self.observer.stop()
            self.observer.join(timeout=2.0)
        except Exception as exc:
            logger.warning("error stopping watcher for %s: %s", self.root, exc)


class WatchManager:
    """Singleton manager that reuses observers per watched root."""

    def __init__(self):
        self._sessions: dict[Path, _WatchSession] = {}
        self._lock = threading.Lock()

    def subscribe(self, root: Path) -> tuple[str, asyncio.Queue[WatchEvent]]:
        session = self._get_or_create_session(root)
        queue_id = str(uuid.uuid4())
        queue: asyncio.Queue[WatchEvent] = asyncio.Queue(maxsize=256)
        session.handler.add_queue(queue_id, queue)
        session.ref_count += 1
        return queue_id, queue

    def unsubscribe(self, root: Path, queue_id: str) -> None:
        session = self._sessions.get(root)
        if not session:
            return
        session.handler.remove_queue(queue_id)
        session.ref_count -= 1
        if session.ref_count <= 0:
            self._remove_session(root)

    def _get_or_create_session(self, root: Path) -> _WatchSession:
        with self._lock:
            session = self._sessions.get(root)
            if session is None:
                session = _WatchSession(root)
                if session.start():
                    self._sessions[root] = session
                else:
                    raise RuntimeError(f"unable to watch {root}")
            return session

    def _remove_session(self, root: Path) -> None:
        with self._lock:
            session = self._sessions.pop(root, None)
        if session:
            session.stop()


_WATCH_MANAGER: WatchManager | None = None
_WATCH_MANAGER_LOCK = threading.Lock()


def get_watch_manager() -> WatchManager:
    global _WATCH_MANAGER
    if _WATCH_MANAGER is None:
        with _WATCH_MANAGER_LOCK:
            if _WATCH_MANAGER is None:
                _WATCH_MANAGER = WatchManager()
    return _WATCH_MANAGER
