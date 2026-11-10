import pytest

from app.api import change_routes


@pytest.mark.asyncio
async def test_list_change_sets_returns_workspace_and_sets(monkeypatch):
    monkeypatch.setattr(change_routes, "workspace_context", lambda: {
        "root": "E:/project", "is_git": False, "branch": None,
        "head": None, "dirty": [], "untracked": [],
    })
    monkeypatch.setattr(change_routes.change_store, "list_change_sets", lambda sid, limit: [{
        "id": "cs_1", "session_id": sid, "run_id": "r_1", "items": [],
    }])
    monkeypatch.setattr(change_routes, "_require_session", lambda db, sid, user: None)

    result = await change_routes.list_change_sets("s1", 20, None, {})

    assert result["success"] is True
    assert result["data"]["workspace"]["root"] == "E:/project"
    assert result["data"]["change_sets"][0]["id"] == "cs_1"


def test_change_item_diff_requires_owner_and_returns_blob_content(monkeypatch):
    monkeypatch.setattr(change_routes.change_store, "get_change_item", lambda cid: {
        "id": cid, "change_set_id": "cs_1", "before_blob_ref": "before",
        "after_blob_ref": "after",
    })
    monkeypatch.setattr(change_routes.change_store, "get_change_set", lambda cid: {
        "id": cid, "session_id": "s1",
    })
    monkeypatch.setattr(change_routes, "_require_session", lambda db, sid, user: None)
    monkeypatch.setattr(
        change_routes, "read_blob",
        lambda ref: {"before": "old", "after": "new"}.get(ref),
    )

    result = change_routes.get_change_item_diff("ci_1", None, {})

    assert result["success"] is True
    assert result["data"]["before_content"] == "old"
    assert result["data"]["after_content"] == "new"
    assert result["data"]["available"] is True
