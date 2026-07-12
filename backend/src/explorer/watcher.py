"""Filesystem watcher — SSE broadcaster using watchdog."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

WatchEvent = dict[str, Any]


class _ExplorerEventHandler(FileSystemEventHandler):
    """Broadcast filesystem events to all registered client queues."""

    def __init__(self, root: Path):
        self.root = root
        self._queues: dict[str, asyncio.Queue[WatchEvent]] = {}
        self._lock = threading.Lock()

    def add_queue(self, queue_id: str, queue: asyncio.Queue[WatchEvent]) -> None:
        with self._lock:
            self._queues[queue_id] = queue

    def remove_queue(self, queue_id: str) -> None:
        with self._lock:
            self._queues.pop(queue_id, None)

    def on_any_event(self, event: FileSystemEvent) -> None:
        payload = self._build_payload(event)
        if payload:
            self._broadcast(payload)

    def _build_payload(self, event: FileSystemEvent) -> WatchEvent | None:
        if event.event_type == "modified" and event.is_directory:
            return None
        payload: WatchEvent = {
            "type": event.event_type,
            "path": str(Path(event.src_path)),
        }
        if event.dest_path:
            payload["new_path"] = str(Path(event.dest_path))
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
