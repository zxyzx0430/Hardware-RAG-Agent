from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import change_store
from app.db.database import Base as DatabaseBase
from src.agent import change_capture
from src.agent.core.toolkit.tool_result_envelope import (
    ResultMetadata,
    ToolResultEnvelope,
)
from src.agent.exceptions import ToolContext


@pytest.fixture
def change_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'changes.sqlite'}")
    DatabaseBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(change_store.database, "SessionLocal", factory)
    monkeypatch.setattr(change_capture.database, "DB_PATH", str(tmp_path / "changes.sqlite"))
    monkeypatch.setattr(
        change_capture, "workspace_context",
        lambda: {
            "root": str(tmp_path), "is_git": False,
            "branch": None, "head": None, "dirty": [], "untracked": [],
        },
    )
    monkeypatch.setattr(change_capture, "_emit", lambda *args, **kwargs: None)
    yield
    engine.dispose()


def _spec(name="write_file"):
    return SimpleNamespace(name=name, risk_level=SimpleNamespace(value="medium"))


def _ctx(run_id):
    return ToolContext(session_id="session-1", run_id=run_id)


def _envelope():
    return ToolResultEnvelope(
        success=True, output="ok",
        metadata=ResultMetadata(tool_name="write_file"),
    )


def test_captures_before_after_and_persists_change_set(change_db, tmp_path):
    path = tmp_path / "firmware.c"
    path.write_bytes(b"before\n")
    pending = change_capture.begin_capture(
        "call-1", _spec(), {"path": str(path)}, _ctx("run-1"),
        target_paths=[str(path)],
    )
    path.write_bytes(b"after\n")
    change_capture.finish_capture(pending, _envelope())

    sets = change_store.list_change_sets("session-1")
    assert len(sets) == 1
    assert sets[0]["run_id"] == "run-1"
    items = sets[0]["items"]
    assert len(items) == 1
    assert items[0]["path"] == str(path)
    assert items[0]["status"] == "observed"
    assert items[0]["before_sha256"] != items[0]["after_sha256"]
    assert items[0]["diff_stats"]["additions"] >= 1
    assert change_capture.read_blob(items[0]["before_blob_ref"]) == "before\n"
    assert change_capture.read_blob(items[0]["after_blob_ref"]) == "after\n"


def test_unobserved_tool_still_produces_visible_item(change_db):
    pending = change_capture.begin_capture(
        "call-2", _spec("undo_edit"), {}, _ctx("run-2"), target_paths=[],
    )
    change_capture.finish_capture(pending, _envelope())

    items = change_store.list_change_sets("session-1")[0]["items"]
    assert items[0]["status"] == "unobserved"
    assert items[0]["path"] == ""
