"""Skill activity persistence and history tests against real HTTP/JSON storage."""

import copy
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.crud import db_router
from app.db.database import Base, get_db
from app.db.models import Message as MessageModel
from app.db.models import Session as SessionModel


@pytest.fixture
def storage():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        db.add_all([SessionModel(id="one", title="Synthetic"), SessionModel(id="two", title="Other")])
    app = FastAPI()
    app.include_router(db_router)

    def isolated_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def skill_step(name="load_skill", **changes):
    resource = name == "read_skill_resource"
    args = {"skill_id": "synthetic-skill"}
    trace = {
        "skill_id": "synthetic-skill", "stage": "resource" if resource else "instructions",
        "content_hash": "a" * 64, "success": True,
    }
    if resource:
        args["path"] = trace["path"] = "References/Example.MD"
    return {
        "type": "tool", "id": "step-1", "call_id": "call-1", "name": name,
        "status": "done", "args": json.dumps(args), "result": "RAW_SKILL_RESULT",
        "skill_trace": trace, **changes,
    }


def post(client, activity, message_id="message-1"):
    return client.post("/api/sessions/one/messages", json={
        "id": message_id, "role": "assistant", "content": "Synthetic body",
        "sources": [{"id": "src-1"}], "activity": activity,
    })


def test_post_saves_safe_skill_step_without_raw_or_unknown_fields(storage):
    client, factory = storage
    step = skill_step(content="RAW_SKILL_BODY", label="RAW_SKILL_LABEL", unknown="RAW_SKILL_UNKNOWN")
    step["skill_trace"]["instructions"] = "RAW_SKILL_INSTRUCTIONS"
    assert post(client, {"status": "done", "steps": [step]}).status_code == 200
    with factory() as db:
        stored = db.get(MessageModel, "message-1").activity
    assert "RAW_SKILL" not in json.dumps(stored)
    assert stored["steps"][0]["result"] == "Skill instructions loaded"


@pytest.mark.parametrize("step_type", [None, "thinking", [], {}])
@pytest.mark.parametrize("name", ["load_skill", "read_skill_resource"])
def test_skill_name_with_invalid_type_cannot_bypass_post_or_history(storage, step_type, name):
    client, factory = storage
    step = skill_step(name, type=step_type, content="RAW_SKILL_BODY")
    step.pop("skill_trace")
    if step_type is None:
        step.pop("type")
    activity = {"steps": [step]}
    assert post(client, activity).status_code == 200
    with factory() as db:
        assert "RAW_SKILL" not in json.dumps(db.get(MessageModel, "message-1").activity)
    with factory.begin() as db:
        db.add(MessageModel(id="legacy-bad-type", session_id="one", role="assistant", content="Keep body", activity=activity))
    for endpoint in ("/api/sessions/one/messages", "/api/sessions/one"):
        messages = client.get(endpoint).json()["data"]["messages"]
        assert len(messages) == 2
        assert "RAW_SKILL" not in json.dumps([item["activity"] for item in messages])
    with factory() as db:
        assert db.get(MessageModel, "legacy-bad-type").activity == activity


@pytest.mark.parametrize("field,value", [("status", []), ("status", {}), ("name", []), ("name", {})])
def test_malformed_skill_metadata_does_not_break_message_history(storage, field, value):
    client, _ = storage
    assert post(client, {"steps": [skill_step(**{field: value})]}).status_code == 200
    message = client.get("/api/sessions/one/messages").json()["data"]["messages"][0]
    assert message["content"] == "Synthetic body"
    assert "RAW_SKILL" not in json.dumps(message["activity"])


@pytest.mark.parametrize("name", ["load_skill", "read_skill_resource"])
def test_valid_skill_provenance_survives_both_history_endpoints(storage, name):
    client, _ = storage
    step = skill_step(name)
    assert post(client, {"steps": [step]}).status_code == 200
    for endpoint in ("/api/sessions/one/messages", "/api/sessions/one"):
        message = client.get(endpoint).json()["data"]["messages"][0]
        safe = message["activity"]["steps"][0]
        assert safe["status"] == "done"
        assert safe["skill_trace"] == step["skill_trace"]
        assert safe["call_id"] == "call-1"
        assert message["sources"] == [{"id": "src-1"}]


def test_get_sanitizes_legacy_rows_without_rewriting_them(storage):
    client, factory = storage
    ordinary = {"type": "tool", "id": "ordinary", "name": "search_docs", "args": "ordinary args", "result": "ordinary result"}
    reasoning = {"type": "thinking", "id": "thinking", "source": "reasoning", "content": "Synthetic reasoning"}
    legacy = {"durationMs": 17, "status": "done", "steps": [skill_step(content="RAW_SKILL_BODY"), ordinary, reasoning]}
    expected = copy.deepcopy(legacy)
    with factory.begin() as db:
        db.add(MessageModel(id="legacy", session_id="one", role="assistant", content="Keep message body", activity=legacy))
    for endpoint in ("/api/sessions/one/messages", "/api/sessions/one"):
        result = client.get(endpoint)
        assert result.status_code == 200
        message = result.json()["data"]["messages"][0]
        assert message["id"] == "legacy" and message["content"] == "Keep message body"
        assert message["activity"]["steps"][1:] == [ordinary, reasoning]
        assert message["activity"]["durationMs"] == 17
        assert "RAW_SKILL" not in json.dumps(message["activity"])
        with factory() as db:
            assert db.get(MessageModel, "legacy").activity == expected


@pytest.mark.parametrize("change", [
    {"skill_trace": None},
    {"skill_trace": {"skill_id": "synthetic-skill", "stage": "instructions", "success": True}},
    {"args": '{"skill_id":"different"}'},
    {"args": "not-json"},
    {"success": False},
    {"call_id": []},
    {"id": "x" * 257},
    {"error": "RAW_SKILL_ERROR"},
    {"result": {"success": False}},
    {"result": {"success": True}},
    {"result": {"success": True, "data": {"success": False}}},
    {"result": {"success": True, "data": {"content_hash": "a" * 64}}},
])
def test_missing_or_conflicting_success_fails_closed(storage, change):
    client, _ = storage
    assert post(client, {"steps": [skill_step(**change)]}).status_code == 200
    safe = client.get("/api/sessions/one/messages").json()["data"]["messages"][0]["activity"]["steps"][0]
    assert safe["status"] == "error" and safe["result"] == "Skill operation failed"
    assert safe.get("skill_trace", {}).get("success") is not True
    assert "RAW_SKILL" not in json.dumps(safe)


@pytest.mark.parametrize("field,value", [
    ("skill_id", "../bad"), ("skill_id", "x" * 129), ("skill_id", True),
    ("stage", "resource"), ("stage", []),
    ("content_hash", "A" * 64), ("content_hash", "a" * 63), ("content_hash", True),
    ("success", "true"), ("success", 1), ("error", "RAW_SKILL_ERROR"),
])
def test_invalid_trace_values_never_become_success(storage, field, value):
    client, _ = storage
    step = skill_step()
    step["skill_trace"][field] = value
    assert post(client, {"steps": [step]}).status_code == 200
    safe = client.get("/api/sessions/one/messages").json()["data"]["messages"][0]["activity"]["steps"][0]
    assert safe["status"] == "error"
    assert safe.get("skill_trace", {}).get("success") is not True


@pytest.mark.parametrize("path", [
    "../secret.md", "/references/a.md", "references/../a.md", "references\\a.md",
    "references/CON.md", "references/a.md:stream", "references/a.md\n",
    "scripts/a.py", "references/a.png", "references/a\u202eb.md", "references/" + "x" * 1025 + ".md",
])
def test_resource_path_validation_fails_closed(storage, path):
    client, _ = storage
    step = skill_step("read_skill_resource", args=json.dumps({"skill_id": "synthetic-skill", "path": path}))
    step["skill_trace"]["path"] = path
    assert post(client, {"steps": [step]}).status_code == 200
    safe = client.get("/api/sessions/one/messages").json()["data"]["messages"][0]["activity"]["steps"][0]
    assert safe["status"] == "error"
    assert "path" not in safe.get("skill_trace", {})
    assert json.loads(safe["args"]) == {}


@pytest.mark.parametrize("path", ["assets/.hidden.txt", "references/参考.TXT"])
def test_legal_text_resource_paths_remain_compatible(storage, path):
    client, _ = storage
    step = skill_step("read_skill_resource", args=json.dumps({"skill_id": "synthetic-skill", "path": path}))
    step["skill_trace"]["path"] = path
    assert post(client, {"steps": [step]}).status_code == 200
    safe = client.get("/api/sessions/one/messages").json()["data"]["messages"][0]["activity"]["steps"][0]
    assert safe["status"] == "done"
    assert safe["skill_trace"]["path"] == path


@pytest.mark.parametrize("status", ["pending", "running", "error", "cancelled"])
def test_unfinished_or_failed_step_never_reports_success(storage, status):
    client, _ = storage
    assert post(client, {"steps": [skill_step(status=status)]}).status_code == 200
    safe = client.get("/api/sessions/one/messages").json()["data"]["messages"][0]["activity"]["steps"][0]
    assert safe["skill_trace"]["success"] is False
    assert safe["result"] == "Skill operation failed"
    assert safe["status"] == ("error" if status == "cancelled" else status)


def test_projection_is_idempotent_nonmutating_and_keeps_pending_marker(storage):
    from app.api.skill_activity import project_skill_activity

    client, factory = storage
    activity = {"steps": [skill_step(duration=2.5, startTime=123)], "__hwrag_pending_save": True}
    before = copy.deepcopy(activity)
    first = project_skill_activity(activity)
    assert activity == before
    assert project_skill_activity(first) == first
    assert first["steps"][0]["duration"] == 2.5
    assert first["__hwrag_pending_save"] is True
    assert post(client, activity).status_code == post(client, first).status_code == 200
    with factory() as db:
        assert db.query(MessageModel).count() == 1
        assert db.get(SessionModel, "one").msg_count == 1


@pytest.mark.parametrize("activity", ["RAW_SKILL_OLD_JSON", ["RAW_SKILL_OLD_JSON"], {"steps": "RAW_SKILL_OLD_JSON"}, {"steps": [None, "RAW_SKILL_OLD_JSON"]}])
def test_legacy_malformed_json_shapes_keep_the_message_not_the_raw_activity(storage, activity):
    client, factory = storage
    with factory.begin() as db:
        db.add(MessageModel(id="legacy-shape", session_id="one", role="assistant", content="Keep body", activity=activity))
    for endpoint in ("/api/sessions/one/messages", "/api/sessions/one"):
        response = client.get(endpoint)
        assert response.status_code == 200
        message = response.json()["data"]["messages"][0]
        assert message["content"] == "Keep body"
        assert "RAW_SKILL" not in json.dumps(message["activity"])
    with factory() as db:
        assert db.get(MessageModel, "legacy-shape").activity == activity
