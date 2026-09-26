"""Explorer routes must keep all test filesystem activity under tmp_path."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from watchdog.events import FileModifiedEvent, FileMovedEvent

from app.api import auth
from app.api import explorer_routes
from src.explorer import security
from src.explorer import trash
from src.explorer.watcher import _ExplorerEventHandler


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "project"
    root.mkdir()
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("HWRAG_APP_DATA_DIR", str(app_data))
    monkeypatch.setattr(security, "_AUTHORIZED_ROOTS", {})
    monkeypatch.setattr(
        explorer_routes,
        "get_provider_key_by_session",
        lambda token: ("test", "fake-key")
        if token in {"valid-session", "other-session"}
        else None,
    )
    app = FastAPI()
    app.include_router(explorer_routes.router)
    with TestClient(app) as client:
        yield client, root, app_data


def auth_headers(session_token: str = "valid-session") -> dict[str, str]:
    return {"Authorization": f"Bearer {session_token}"}


def test_explorer_rejects_unauthenticated_open_search_and_write(workspace) -> None:
    client, root, _ = workspace
    responses = [
        client.post("/api/explorer/open", json={"path": str(root)}),
        client.post(
            "/api/explorer/search",
            json={"root_path": str(root), "query": "secret"},
        ),
        client.post(
            "/api/explorer/write",
            json={"path": str(root / "note.txt"), "content": "secret"},
        ),
        client.get("/api/explorer/browse", params={"path": str(root)}),
    ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401]
    assert responses[0].json() == {
        "success": False,
        "error": {
            "code": "AUTH_REQUIRED",
            "message": "Explorer requires a session",
            "details": None,
        },
    }
    assert not (root / "note.txt").exists()


def test_explorer_rejects_invalid_session_even_before_provider_setup(workspace) -> None:
    client, root, _ = workspace
    response = client.post(
        "/api/explorer/open",
        headers={"Authorization": "Bearer invalid-session"},
        json={"path": str(root)},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID"


def test_first_provider_setup_can_be_followed_by_an_authenticated_project_open(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(auth, "_load_store", lambda: {"providers": {}, "sessions": {}})
    monkeypatch.setattr(
        auth, "store_provider_key", lambda provider, _key, _base: "new-session"
    )
    monkeypatch.setattr(
        explorer_routes,
        "get_provider_key_by_session",
        lambda token: ("test", "fake-key") if token == "new-session" else None,
    )
    root = tmp_path / "project"
    root.mkdir()
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(explorer_routes.router)

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/store-key",
            json={"provider": "test", "api_key": "fake-key", "base_url": ""},
        )
        opened = client.post(
            "/api/explorer/open",
            headers={"Authorization": "Bearer new-session"},
            json={"path": str(root)},
        )

    assert response.status_code == 200
    assert response.json()["data"]["session_token"] == "new-session"
    assert opened.status_code == 200
    assert opened.json()["tree"][0]["path"] == str(root)


def test_open_does_not_authorize_a_parent_or_outside_directory(workspace) -> None:
    client, root, _ = workspace
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("outside marker", encoding="utf-8")

    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    assert opened.status_code == 200

    read = client.get(
        "/api/explorer/read",
        headers=auth_headers(),
        params={"path": str(outside / "private.txt")},
    )
    search = client.post(
        "/api/explorer/search",
        headers=auth_headers(),
        json={"root_path": str(outside), "query": "outside"},
    )
    write = client.post(
        "/api/explorer/write",
        headers=auth_headers(),
        json={"path": str(outside / "new.txt"), "content": "no", "expected_version": None},
    )

    assert read.status_code == 403
    assert search.status_code == 403
    assert write.status_code == 403
    assert not (outside / "new.txt").exists()


def test_authorized_root_can_open_tree_and_read_normal_file(workspace) -> None:
    client, root, _ = workspace
    normal_file = root / "main.py"
    normal_file.write_text("print('normal')\n", encoding="utf-8")

    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    read = client.get(
        "/api/explorer/read",
        headers=auth_headers(),
        params={"path": str(normal_file)},
    )

    assert opened.status_code == 200
    assert opened.json()["tree"][0]["path"] == str(root)
    assert read.status_code == 200
    assert read.json()["content"] == normal_file.read_bytes().decode("utf-8")


def test_folder_picker_browse_does_not_grant_project_access(workspace) -> None:
    client, root, _ = workspace
    file_path = root / "main.py"
    file_path.write_text("picker can list names", encoding="utf-8")

    browse = client.get(
        "/api/explorer/browse",
        headers=auth_headers(),
        params={"path": str(root)},
    )
    read = client.get(
        "/api/explorer/read",
        headers=auth_headers(),
        params={"path": str(file_path)},
    )
    search = client.post(
        "/api/explorer/search",
        headers=auth_headers(),
        json={"root_path": str(root), "query": "picker can list"},
    )
    write = client.post(
        "/api/explorer/write",
        headers=auth_headers(),
        json={
            "path": str(root / "new.txt"),
            "content": "not authorized yet",
            "expected_version": None,
        },
    )

    assert browse.status_code == 200
    assert read.status_code == 403
    assert search.status_code == 403
    assert write.status_code == 403
    assert not (root / "new.txt").exists()


def test_read_search_and_browse_subdirectory_do_not_reauthorize_after_restart(workspace) -> None:
    client, root, _ = workspace
    nested = root / "src"
    nested.mkdir()
    file_path = nested / "main.py"
    file_path.write_text("visible marker", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    monkeypatch_roots = security._AUTHORIZED_ROOTS
    monkeypatch_roots.clear()

    responses = [
        client.get("/api/explorer/dir", headers=auth_headers(), params={"path": str(root)}),
        client.get("/api/explorer/read", headers=auth_headers(), params={"path": str(file_path)}),
        client.post(
            "/api/explorer/search",
            headers=auth_headers(),
            json={"root_path": str(root), "query": "visible"},
        ),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert security._AUTHORIZED_ROOTS == {}


def test_project_authorization_is_scoped_to_the_session_that_opened_it(workspace) -> None:
    client, root, _ = workspace
    file_path = root / "main.py"
    file_path.write_text("normal marker", encoding="utf-8")

    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    assert opened.status_code == 200

    other_read = client.get(
        "/api/explorer/read",
        headers=auth_headers("other-session"),
        params={"path": str(file_path)},
    )
    other_search = client.post(
        "/api/explorer/search",
        headers=auth_headers("other-session"),
        json={"root_path": str(root), "query": "normal marker"},
    )
    other_write = client.post(
        "/api/explorer/write",
        headers=auth_headers("other-session"),
        json={
            "path": str(root / "new.txt"),
            "content": "no",
            "expected_version": None,
        },
    )

    assert [other_read.status_code, other_search.status_code, other_write.status_code] == [
        403,
        403,
        403,
    ]
    assert not (root / "new.txt").exists()

    second_open = client.post(
        "/api/explorer/open",
        headers=auth_headers("other-session"),
        json={"path": str(root)},
    )
    second_read = client.get(
        "/api/explorer/read",
        headers=auth_headers("other-session"),
        params={"path": str(file_path)},
    )
    assert second_open.status_code == 200
    assert second_read.status_code == 200


def test_stale_version_conflict_preserves_external_edit(workspace) -> None:
    client, root, _ = workspace
    file_path = root / "main.py"
    file_path.write_text("original", encoding="utf-8")
    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    assert opened.status_code == 200
    read = client.get(
        "/api/explorer/read", headers=auth_headers(), params={"path": str(file_path)}
    )
    old_version = read.json()["version"]

    external_content = "edited outside Explorer"
    file_path.write_text(external_content, encoding="utf-8")
    stale_save = client.post(
        "/api/explorer/write",
        headers=auth_headers(),
        json={
            "path": str(file_path),
            "content": "stale editor draft",
            "expected_version": old_version,
        },
    )

    assert stale_save.status_code == 409
    assert stale_save.json()["error"]["code"] == "VERSION_CONFLICT"
    assert file_path.read_text(encoding="utf-8") == external_content


def test_concurrent_versioned_saves_only_allow_one_writer(workspace) -> None:
    client, root, _ = workspace
    file_path = root / "main.py"
    file_path.write_text("original", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    version = client.get(
        "/api/explorer/read", headers=auth_headers(), params={"path": str(file_path)}
    ).json()["version"]
    barrier = Barrier(2)

    def save(content: str):
        barrier.wait()
        return client.post(
            "/api/explorer/write",
            headers=auth_headers(),
            json={"path": str(file_path), "content": content, "expected_version": version},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ["first save", "second save"]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert file_path.read_text(encoding="utf-8") in {"first save", "second save"}
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"


def test_versioned_write_saves_expected_content_and_returns_new_version(workspace) -> None:
    client, root, _ = workspace
    file_path = root / "new.py"
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    response = client.post(
        "/api/explorer/write",
        headers=auth_headers(),
        json={"path": str(file_path), "content": "print(1)\n", "expected_version": None},
    )

    assert response.status_code == 200
    assert file_path.read_text(encoding="utf-8") == "print(1)\n"
    assert response.json()["data"]["version"] == hashlib.sha256(file_path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".ENV.production",
        "id.key",
        "service.pem",
        "service.p12",
        "my_credentials.txt",
        "secrets.json",
    ],
)
def test_sensitive_files_cannot_be_read_or_searched(workspace, name: str) -> None:
    client, root, _ = workspace
    secret = root / name
    secret.write_text("sensitive-marker", encoding="utf-8")
    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    assert opened.status_code == 200
    assert str(secret) not in str(opened.json())

    read = client.get(
        "/api/explorer/read",
        headers=auth_headers(),
        params={"path": str(secret)},
    )
    diff = client.get(
        "/api/explorer/diff",
        headers=auth_headers(),
        params={"path": str(secret)},
    )
    search = client.post(
        "/api/explorer/search",
        headers=auth_headers(),
        json={"root_path": str(root), "query": "sensitive-marker"},
    )

    assert read.status_code == 403
    assert diff.status_code == 403
    assert search.status_code == 200
    assert all(item["path"] != str(secret) for item in search.json()["data"]["results"])


def test_delete_moves_file_to_recycle_area_and_restore_recovers_it(workspace) -> None:
    client, root, app_data = workspace
    target = root / "note.txt"
    target.write_text("recover me", encoding="utf-8")
    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    assert opened.status_code == 200

    deleted = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(target)}
    )
    assert deleted.status_code == 200, deleted.text
    item_id = deleted.json()["data"]["trash_id"]
    assert not target.exists()
    assert (app_data / "explorer-trash").exists()

    listed = client.get(
        "/api/explorer/trash", headers=auth_headers(), params={"root_path": str(root)}
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["item_id"] == item_id
    assert listed.json()["data"]["items"][0]["original_path"] == str(target)

    restored = client.post(
        "/api/explorer/restore",
        headers=auth_headers(),
        json={"root_path": str(root), "item_id": item_id},
    )
    assert restored.status_code == 200
    assert target.read_text(encoding="utf-8") == "recover me"
    assert list(root.glob(".*.restore")) == []
    assert client.get(
        "/api/explorer/trash", headers=auth_headers(), params={"root_path": str(root)}
    ).json()["data"]["items"] == []


def test_trash_round_trip_preserves_large_fake_binary_file(workspace) -> None:
    client, root, _ = workspace
    target = root / "large.bin"
    payload = bytes(range(251)) * 12_288
    target.write_bytes(payload)
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    deleted = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(target)}
    )
    assert deleted.status_code == 200, deleted.text
    restored = client.post(
        "/api/explorer/restore",
        headers=auth_headers(),
        json={"root_path": str(root), "item_id": deleted.json()["data"]["trash_id"]},
    )

    assert restored.status_code == 200
    assert target.read_bytes() == payload


def test_delete_directory_can_be_recovered_with_its_contents(workspace) -> None:
    client, root, _ = workspace
    target = root / "src"
    nested = target / "nested"
    nested.mkdir(parents=True)
    (nested / "main.py").write_text("keep this source", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    deleted = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(target)}
    )
    assert deleted.status_code == 200
    assert not target.exists()

    restored = client.post(
        "/api/explorer/restore",
        headers=auth_headers(),
        json={"root_path": str(root), "item_id": deleted.json()["data"]["trash_id"]},
    )

    assert restored.status_code == 200
    assert (nested / "main.py").read_text(encoding="utf-8") == "keep this source"


def test_restore_conflict_keeps_existing_file_and_recycle_item(workspace) -> None:
    client, root, _ = workspace
    target = root / "note.txt"
    target.write_text("original", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    deleted = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(target)}
    )
    item_id = deleted.json()["data"]["trash_id"]
    target.write_text("newer user file", encoding="utf-8")

    restored = client.post(
        "/api/explorer/restore",
        headers=auth_headers(),
        json={"root_path": str(root), "item_id": item_id},
    )

    assert restored.status_code == 409
    assert target.read_text(encoding="utf-8") == "newer user file"
    assert client.get(
        "/api/explorer/trash", headers=auth_headers(), params={"root_path": str(root)}
    ).json()["data"]["items"][0]["item_id"] == item_id


def test_concurrent_restores_do_not_overwrite_the_same_target(workspace) -> None:
    client, root, _ = workspace
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text("first payload", encoding="utf-8")
    second.write_text("second payload", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    first_id = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(first)}
    ).json()["data"]["trash_id"]
    second_id = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(second)}
    ).json()["data"]["trash_id"]
    target = root / "restored.txt"
    barrier = Barrier(2)

    def restore(item_id: str):
        barrier.wait()
        return client.post(
            "/api/explorer/restore",
            headers=auth_headers(),
            json={"root_path": str(root), "item_id": item_id, "target_path": str(target)},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(restore, [first_id, second_id]))

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert target.read_text(encoding="utf-8") in {"first payload", "second payload"}
    assert len(client.get(
        "/api/explorer/trash", headers=auth_headers(), params={"root_path": str(root)}
    ).json()["data"]["items"]) == 1


def test_failed_trash_copy_leaves_source_untouched(workspace, monkeypatch) -> None:
    client, root, app_data = workspace
    target = root / "important.txt"
    target.write_text("keep on copy failure", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    def fail_copy(_source: Path, _destination: Path) -> None:
        _destination.parent.mkdir(parents=True, exist_ok=True)
        _destination.write_bytes(b"partial fake copy")
        raise OSError("injected copy failure")

    monkeypatch.setattr(trash, "_copy_into", fail_copy)
    response = client.post(
        "/api/explorer/delete", headers=auth_headers(), json={"path": str(target)}
    )

    assert response.status_code == 500
    assert target.read_text(encoding="utf-8") == "keep on copy failure"
    bucket = app_data / "explorer-trash"
    assert not bucket.exists() or not list(bucket.rglob("metadata.json"))


def test_watcher_uses_the_event_names_expected_by_the_editor(workspace) -> None:
    _, root, _ = workspace
    handler = _ExplorerEventHandler(root)
    modified = handler._build_payload(FileModifiedEvent(str(root / "main.py")))
    moved = handler._build_payload(
        FileMovedEvent(str(root / "old.py"), str(root / "new.py"))
    )

    assert modified == {
        "type": "change",
        "path": str(root / "main.py"),
        "is_directory": False,
    }
    assert moved == {
        "type": "rename",
        "path": str(root / "old.py"),
        "is_directory": False,
        "src_path": str(root / "old.py"),
        "dest_path": str(root / "new.py"),
    }
