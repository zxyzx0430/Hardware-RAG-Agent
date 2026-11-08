"""SubmitPlanTool: plan creation in every mode, validation and idempotency."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import database
from app.db.database import Base
from app.db.models import Session as SessionModel
from app.db import plan_store
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.execution.submit_plan import SubmitPlanTool

ARGS = {
    "title": "Bring up the board",
    "summary": "Inspect then execute",
    "steps": [
        {"title": "Inspect", "detail": "Read the manual", "verification": "Cite pages"},
    ],
    "risks": ["Board revision may differ"],
    "open_questions": [],
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
        session.commit()
    yield factory
    engine.dispose()


def _ctx(mode="plan", session_id="s1", run_id="r1"):
    return ToolContext(permission_mode=mode, session_id=session_id, run_id=run_id)


@pytest.mark.asyncio
async def test_submit_plan_in_plan_mode(factory):
    result = await SubmitPlanTool().execute(ARGS, _ctx())

    assert result["_plan_update"] is True
    assert result["plan"]["version"] == 1
    assert result["plan"]["status"] == "pending"
    assert plan_store.latest("s1")["id"] == result["plan"]["id"]


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "yolo", "bypassPermissions", "plan"])
@pytest.mark.asyncio
async def test_submit_plan_works_in_every_permission_mode(factory, mode):
    result = await SubmitPlanTool().execute(ARGS, _ctx(mode=mode, run_id=f"r-{mode}"))

    assert result["_plan_update"] is True
    assert result["plan"]["status"] == "pending"
    assert plan_store.latest("s1")["id"] == result["plan"]["id"]


@pytest.mark.asyncio
async def test_submit_plan_requires_real_session(factory):
    result = await SubmitPlanTool().execute(ARGS, _ctx(session_id="default"))

    assert result["_plan_update"] is False
    assert "session" in result["output"]


@pytest.mark.asyncio
async def test_submit_plan_validates_payload(factory):
    result = await SubmitPlanTool().execute({**ARGS, "steps": []}, _ctx())

    assert result["_plan_update"] is False
    assert plan_store.latest("s1") is None


@pytest.mark.asyncio
async def test_submit_plan_same_run_is_idempotent(factory):
    first = await SubmitPlanTool().execute(ARGS, _ctx())
    second = await SubmitPlanTool().execute(ARGS, _ctx())

    assert first["plan"]["id"] == second["plan"]["id"]
    assert len(plan_store.list_metadata("s1")) == 1
