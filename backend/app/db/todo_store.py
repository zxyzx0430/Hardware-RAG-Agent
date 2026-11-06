"""SQLite storage for the server-authoritative session TODO list.

Replacement is one transaction: the session's rows are deleted and the new list
is inserted with ``seq`` 0..n-1 before a single commit, so readers never observe
a partially replaced list. An empty list is a valid clear. ``SessionLocal`` is
resolved lazily so tests can patch ``database.SessionLocal``.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, select

from app.db import database
from app.db.models import Todo

MAX_TODO_ITEMS = 50
DEFAULT_STATUS = "pending"
DEFAULT_PRIORITY = "medium"
_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})
_VALID_PRIORITIES = frozenset({"high", "medium", "low"})


def _utc_now_naive() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _clean_todo(item: Any) -> dict[str, str]:
    if not isinstance(item, Mapping):
        raise ValueError("todo item must be an object")
    content = str(item.get("content", "")).strip()
    if not content:
        raise ValueError("todo content is required")
    return {
        "content": content,
        "status": _clean_choice(item.get("status"), _VALID_STATUSES, DEFAULT_STATUS, "status"),
        "priority": _clean_choice(item.get("priority"), _VALID_PRIORITIES, DEFAULT_PRIORITY, "priority"),
    }


def _clean_choice(value: Any, allowed: frozenset[str], default: str, field: str) -> str:
    choice = str(value) if value is not None else default
    if choice not in allowed:
        raise ValueError(f"invalid todo {field}: {choice}")
    return choice


def _clean_todos(todos: list[Any]) -> list[dict[str, str]]:
    if len(todos) > MAX_TODO_ITEMS:
        raise ValueError(f"too many todo items: {len(todos)} > {MAX_TODO_ITEMS}")
    return [_clean_todo(item) for item in todos]


def _todo_dict(row: Todo) -> dict[str, Any]:
    return {
        "content": row.content or "",
        "status": row.status or DEFAULT_STATUS,
        "priority": row.priority or DEFAULT_PRIORITY,
        "updated_at": row.updated_at,
    }


def _insert_rows(session, session_id: str, todos: list[dict[str, str]], now: _dt.datetime) -> None:
    session.add_all([
        _todo_row(session_id, seq, item, now)
        for seq, item in enumerate(todos)
    ])


def _todo_row(session_id: str, seq: int, item: dict[str, str], now: _dt.datetime) -> Todo:
    return Todo(
        session_id=session_id,
        seq=seq,
        content=item["content"],
        status=item["status"],
        priority=item["priority"],
        updated_at=now,
    )


def replace_todos(session_id: str, todos: list[Any]) -> list[dict[str, Any]]:
    """Atomically replace one session's list; ``todos=[]`` clears it."""
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id is required")
    cleaned = _clean_todos(list(todos))
    now = _utc_now_naive()
    with database.SessionLocal() as session:
        session.execute(delete(Todo).where(Todo.session_id == session_id))
        _insert_rows(session, session_id, cleaned, now)
        session.commit()
    return [{**item, "updated_at": now} for item in cleaned]


def list_todos(session_id: str) -> list[dict[str, Any]]:
    """Return the stored list ordered by ``seq`` (empty when absent)."""
    with database.SessionLocal() as session:
        rows = session.execute(
            select(Todo).where(Todo.session_id == session_id).order_by(Todo.seq)
        ).scalars().all()
        return [_todo_dict(row) for row in rows]


def clear_todos(session_id: str) -> int:
    """Delete every stored row for one session; return the deleted count."""
    with database.SessionLocal() as session:
        result = session.execute(delete(Todo).where(Todo.session_id == session_id))
        session.commit()
        return int(result.rowcount or 0)
