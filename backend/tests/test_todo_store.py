"""Todo store persistence tests use an isolated SQLite database only."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.database import Base
from app.db.models import Session as SessionModel
from app.db.models import Todo
from app.db.todo_store import clear_todos, list_todos, replace_todos

TODOS = [
    {"content": "搭建环境", "status": "completed", "priority": "high"},
    {"content": "写代码", "status": "in_progress", "priority": "medium"},
    {"content": "验证", "status": "pending", "priority": "low"},
]


def _engine(path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def store_db(tmp_path, monkeypatch):
    path = tmp_path / "todos.sqlite"
    engine = _engine(path)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as db:
        db.add(SessionModel(id="session-a", title="A", model="test"))
        db.add(SessionModel(id="session-b", title="B", model="test"))
        db.commit()
    yield SimpleNamespace(path=path, engine=engine, factory=factory)
    engine.dispose()


def _public(rows):
    return [
        {key: row[key] for key in ("content", "status", "priority")}
        for row in rows
    ]


def test_replace_and_list_roundtrip(store_db):
    replace_todos("session-a", TODOS)

    assert _public(list_todos("session-a")) == TODOS
    with store_db.factory() as db:
        seqs = db.execute(
            select(Todo.seq)
            .where(Todo.session_id == "session-a")
            .order_by(Todo.seq)
        ).scalars().all()
    assert seqs == [0, 1, 2]


def test_replace_drops_previous_rows_and_reindexes(store_db):
    replace_todos("session-a", TODOS)
    replace_todos("session-a", [TODOS[0]])

    rows = list_todos("session-a")
    assert _public(rows) == [TODOS[0]]
    with store_db.factory() as db:
        seqs = db.execute(
            select(Todo.seq).where(Todo.session_id == "session-a")
        ).scalars().all()
    assert seqs == [0]


def test_empty_list_is_a_valid_clear(store_db):
    replace_todos("session-a", TODOS)

    assert replace_todos("session-a", []) == []
    assert list_todos("session-a") == []
    with store_db.factory() as db:
        assert db.query(Todo).filter(Todo.session_id == "session-a").count() == 0


def test_new_store_instance_sees_rows_after_restart(store_db, monkeypatch):
    replace_todos("session-a", TODOS)
    store_db.engine.dispose()

    restarted = _engine(store_db.path)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=restarted)
    monkeypatch.setattr(database, "SessionLocal", factory)
    try:
        assert _public(list_todos("session-a")) == TODOS
    finally:
        restarted.dispose()


def test_clear_todos_deletes_rows(store_db):
    replace_todos("session-a", TODOS)

    assert clear_todos("session-a") == len(TODOS)
    assert clear_todos("session-a") == 0
    assert list_todos("session-a") == []


def test_cross_session_isolation(store_db):
    replace_todos("session-a", [TODOS[0]])
    replace_todos("session-b", [TODOS[1]])

    assert _public(list_todos("session-a")) == [TODOS[0]]
    assert _public(list_todos("session-b")) == [TODOS[1]]

    replace_todos("session-a", [])
    assert list_todos("session-a") == []
    assert _public(list_todos("session-b")) == [TODOS[1]]


def test_unknown_session_is_rejected_by_foreign_key(store_db):
    with pytest.raises(IntegrityError):
        replace_todos("missing-session", [TODOS[0]])

    assert list_todos("missing-session") == []


def test_invalid_item_does_not_replace_existing_rows(store_db):
    replace_todos("session-a", [TODOS[0]])

    with pytest.raises(ValueError):
        replace_todos(
            "session-a",
            [{"content": "bad", "status": "unknown", "priority": "high"}],
        )

    assert _public(list_todos("session-a")) == [TODOS[0]]
