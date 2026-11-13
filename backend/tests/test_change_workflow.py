from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import change_store
from app.db.database import Base
from src.agent import change_capture, change_revert
from src.agent.core.toolkit.tool_result_envelope import ResultMetadata, ToolResultEnvelope
from src.agent.exceptions import ToolContext


@pytest.fixture
def change_workflow_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'changes.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(change_store.database, "SessionLocal", factory)
    monkeypatch.setattr(change_capture.database, "SessionLocal", factory)
    monkeypatch.setattr(change_capture.database, "DB_PATH", str(tmp_path / "changes.sqlite"))
    monkeypatch.setattr(
        change_capture, "workspace_context",
        lambda: {
            "root": str(tmp_path), "is_git": False,
            "branch": None, "head": None, "dirty": [], "untracked": [],
        },
    )
    monkeypatch.setattr(change_capture, "_emit", lambda *args, **kwargs: None)
    yield tmp_path
    engine.dispose()


def _spec(name="write_file"):
    return SimpleNamespace(name=name, risk_level=SimpleNamespace(value="medium"))


def _ctx(run_id="run-1"):
    return ToolContext(session_id="session-1", run_id=run_id)


def _envelope(tool="write_file"):
    return ToolResultEnvelope(
        success=True, output="ok",
        metadata=ResultMetadata(tool_name=tool),
    )


def _capture(path, before: str, after: str, run_id="run-1") -> dict:
    path.write_text(before, encoding="utf-8")
    pending = change_capture.begin_capture(
        "call-1", _spec(), {"path": str(path)}, _ctx(run_id),
        target_paths=[str(path)],
    )
    path.write_text(after, encoding="utf-8")
    change_capture.finish_capture(pending, _envelope())
    return change_store.list_change_sets("session-1")[0]["items"][0]


def test_review_and_revert_restore_before_content(change_workflow_db):
    path = change_workflow_db / "firmware.c"
    item = _capture(path, "before\n", "after\n")

    reviewed = change_store.update_change_item(
        item["id"], review_status="accepted", review_note="looks good",
    )
    assert reviewed["review_status"] == "accepted"
    change_store.recompute_change_set_status(item["change_set_id"])
    assert change_store.get_change_set(item["change_set_id"])["status"] == "accepted"

    result = change_revert.revert_change_item(reviewed, str(change_workflow_db))
    assert result["ok"] is True
    assert path.read_text(encoding="utf-8") == "before\n"
    reverted = change_store.get_change_item(item["id"])
    assert reverted["status"] == "reverted"
    assert reverted["review_status"] == "rejected"


def test_plan_step_cursor_stamps_new_items(change_workflow_db, monkeypatch):
    monkeypatch.setattr(change_store, "resolve_run_plan", lambda run_id: ("pl_1", 1))
    monkeypatch.setattr(change_store, "resolve_run_plan_step_count", lambda run_id: 3)
    path = change_workflow_db / "firmware.c"
    assert change_capture.set_plan_step_cursor(
        session_id="session-1", run_id="run-1", step_index=1,
    ) is True
    item = _capture(path, "before\n", "after\n")
    assert item["plan_step_index"] == 1
    assert item["plan_step_id"] == "pl_1:v1:1"


def test_plan_step_cursor_requires_an_approved_plan(change_workflow_db):
    assert change_capture.set_plan_step_cursor(
        session_id="session-1", run_id="run-without-plan", step_index=0,
    ) is False


def test_revert_refuses_when_user_changed_the_file(change_workflow_db):
    path = change_workflow_db / "firmware.c"
    item = _capture(path, "before\n", "after\n")
    path.write_text("user edit\n", encoding="utf-8")

    result = change_revert.revert_change_item(item, str(change_workflow_db))

    assert result["ok"] is False
    assert "changed" in result["error"]
    assert path.read_text(encoding="utf-8") == "user edit\n"
    assert change_store.get_change_item(item["id"])["status"] == "conflict"
