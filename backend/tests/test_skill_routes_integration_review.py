"""Independent HTTP review of Skills CRUD compatibility and validation."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import skill_routes
from app.api.dependencies import current_user, current_user_optional


@pytest.fixture()
def skill_client(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_routes, "SKILLS_DIR", tmp_path / "skills")
    app = FastAPI()
    app.include_router(skill_routes.router)
    app.dependency_overrides[current_user] = lambda: {"anonymous": True}
    app.dependency_overrides[current_user_optional] = lambda: {"anonymous": True}
    with TestClient(app) as client:
        yield client


def test_plain_legacy_creations_keep_distinct_ids(skill_client):
    first = skill_client.post("/api/skills", json={"content": "Synthetic instructions A."})
    second = skill_client.post("/api/skills", json={"content": "Synthetic instructions B."})
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] != second.json()["data"]["id"]
    assert len(skill_client.get("/api/skills").json()["data"]["skills"]) == 2


@pytest.mark.parametrize("value", [0, 1, "true", "yes", [], {}])
def test_enable_requires_json_boolean(skill_client, value):
    created = skill_client.post("/api/skills", json={"content": "Synthetic instructions."}).json()["data"]
    response = skill_client.patch(f"/api/skills/{created['id']}", json={"enabled": value})
    assert response.status_code == 422
    assert skill_client.get(f"/api/skills/{created['id']}").json()["data"]["enabled"] is True


def test_legacy_detail_edit_toggle_delete_and_missing_envelope(skill_client):
    created = skill_client.post("/api/skills", json={"content": "Synthetic instructions."}).json()["data"]
    path = f"/api/skills/{created['id']}"
    assert skill_client.get(path).json()["data"]["format"] == "legacy"
    assert skill_client.patch(path, json={"content": "Changed synthetic instructions."}).status_code == 200
    toggled = skill_client.patch(path + "/toggle").json()["data"]
    assert toggled == {"id": created["id"], "enabled": False}
    assert skill_client.delete(path).json()["success"] is True
    missing = skill_client.get(path)
    assert missing.status_code == 404
    assert missing.json()["success"] is False
    assert missing.json()["error"]["code"] == "SKILL_NOT_FOUND"
