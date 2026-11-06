"""Plan store: versioning, conditional decisions, execution claim and validation."""

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import database
from app.db.database import Base
from app.db.models import Plan, Session as SessionModel
from app.db import plan_store


def _payload(title="Plan A", summary="Do the thing", steps=None):
    return {
        "title": title,
        "summary": summary,
        "steps": steps or [
            {"title": "Inspect docs", "detail": "Read the manual", "verification": "Cite pages"},
            {"title": "Patch code", "detail": "", "verification": "Run tests"},
        ],
        "risks": ["Board revision may differ"],
        "open_questions": ["Which toolchain?"],
    }


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as session:
        session.add(SessionModel(id="s1", title="A", model="test"))
        session.add(SessionModel(id="s2", title="B", model="test"))
        session.commit()
    yield factory
    engine.dispose()


def test_create_version_and_latest(factory):
    plan = plan_store.create_version("s1", _payload(), "r1")

    assert plan["version"] == 1
    assert plan["status"] == "pending"
    assert plan["steps"][0]["title"] == "Inspect docs"
    assert plan["created_at"].endswith("Z")
    assert plan_store.latest("s1")["id"] == plan["id"]


def test_new_version_supersedes_pending(factory):
    first = plan_store.create_version("s1", _payload(), "r1")
    second = plan_store.create_version("s1", _payload("Plan B"), "r2")

    assert second["version"] == 2
    assert plan_store.get(first["id"])["status"] == "superseded"
    assert plan_store.latest("s1")["id"] == second["id"]


def test_approved_unexecuted_is_superseded(factory):
    first = plan_store.create_version("s1", _payload(), "r1")
    plan_store.approve("s1", first["id"])
    plan_store.create_version("s1", _payload("Plan B"), "r2")

    assert plan_store.get(first["id"])["status"] == "superseded"


def test_executed_plan_is_not_superseded(factory):
    first = plan_store.create_version("s1", _payload(), "r1")
    plan_store.approve("s1", first["id"])
    assert plan_store.mark_executed(first["id"], "run-1") is True

    plan_store.create_version("s1", _payload("Plan B"), "r2")

    assert plan_store.get(first["id"])["status"] == "approved"
    assert plan_store.get(first["id"])["executed_run_id"] == "run-1"


def test_same_run_same_content_is_idempotent(factory):
    first = plan_store.create_version("s1", _payload(), "r1")
    again = plan_store.create_version("s1", _payload(), "r1")

    assert again["id"] == first["id"]
    assert again["version"] == 1


def test_decision_only_accepts_latest_pending(factory):
    first = plan_store.create_version("s1", _payload(), "r1")
    plan_store.approve("s1", first["id"])

    with pytest.raises(plan_store.PlanStateError):
        plan_store.approve("s1", first["id"])
    with pytest.raises(plan_store.PlanStateError):
        plan_store.reject("s1", first["id"])


def test_stale_plan_cannot_be_approved(factory):
    first = plan_store.create_version("s1", _payload(), "r1")
    plan_store.create_version("s1", _payload("Plan B"), "r2")

    with pytest.raises(plan_store.PlanStateError):
        plan_store.approve("s1", first["id"])


def test_mark_executed_claims_once(factory):
    plan = plan_store.create_version("s1", _payload(), "r1")
    plan_store.approve("s1", plan["id"])

    assert plan_store.mark_executed(plan["id"], "run-1") is True
    assert plan_store.mark_executed(plan["id"], "run-2") is False


def test_list_metadata_excludes_body(factory):
    plan_store.create_version("s1", _payload(), "r1")
    rows = plan_store.list_metadata("s1")

    assert rows[0]["version"] == 1
    assert "steps" not in rows[0]
    assert "summary" not in rows[0]


def test_validation_rejects_bad_payloads(factory):
    with pytest.raises(plan_store.PlanValidationError):
        plan_store.create_version("s1", _payload(title=""), "r1")
    empty_steps = _payload()
    empty_steps["steps"] = []
    with pytest.raises(plan_store.PlanValidationError):
        plan_store.create_version("s1", empty_steps, "r1")
    with pytest.raises(plan_store.PlanValidationError):
        plan_store.create_version("s1", _payload(steps=[{"title": "x"}] * 13), "r1")


def test_session_delete_cascades(factory):
    plan_store.create_version("s1", _payload(), "r1")
    with factory() as session:
        session.delete(session.get(SessionModel, "s1"))
        session.commit()
        assert session.execute(select(Plan)).scalars().all() == []


def test_delete_session_store_helper(factory):
    plan_store.create_version("s1", _payload(), "r1")
    assert plan_store.delete_session("s1") == 1
    assert plan_store.latest("s1") is None


def test_create_all_adds_plans_to_existing_database(tmp_path):
    from sqlalchemy import create_engine, inspect

    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=3")
        connection.exec_driver_sql("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    Base.metadata.create_all(bind=engine)
    try:
        assert inspect(engine).has_table("plans") is True
    finally:
        engine.dispose()


def test_concurrent_versions_are_unique(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import create_engine, event

    db_path = tmp_path / "concurrent.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _busy_timeout(connection, _record):
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory() as session:
        session.add(SessionModel(id="s1", title="A", model="test"))
        session.commit()

    def worker(index: int):
        return plan_store.create_version(
            "s1", {**_payload(), "title": f"Plan {index}"}, f"r{index}",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(worker, range(4)))

    assert sorted(result["version"] for result in results) == [1, 2, 3, 4]
    assert len({result["id"] for result in results}) == 4
    engine.dispose()
