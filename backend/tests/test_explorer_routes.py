"""Explorer routes must keep all test filesystem activity under tmp_path."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import types
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


def _resolves_to_target(link: Path, target: Path) -> bool:
    try:
        return link.resolve(strict=True) == target.resolve(strict=True)
    except (OSError, RuntimeError):
        return False


def _create_test_junction(link: Path, target: Path) -> None:
    if link.exists() or link.is_symlink():
        raise AssertionError(f"refusing to create a test junction over an existing path: {link}")
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if _resolves_to_target(link, target):
            os.rmdir(link)
        pytest.skip(f"could not create a temporary junction: {result.stderr}")
    if not _resolves_to_target(link, target):
        raise AssertionError(f"test junction did not resolve to its fake target: {link}")


def _remove_test_junction(link: Path, target: Path) -> None:
    if not _resolves_to_target(link, target):
        raise AssertionError(f"refusing to remove a junction with an unexpected target: {link}")
    os.rmdir(link)
    if link.exists() or not target.exists():
        raise AssertionError("junction removal affected an unexpected path")


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
    monkeypatch.setattr(auth, "require_setup_code", lambda _code: None)
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


def test_search_finds_a_matching_file_inside_the_authorized_root(workspace) -> None:
    client, root, _ = workspace
    marker = "explorer-normal-search-marker"
    file_path = root / "firmware.py"
    file_path.write_text(f"# {marker}\n", encoding="utf-8")
    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    response = client.post(
        "/api/explorer/search",
        headers=auth_headers(),
        json={"root_path": str(root), "query": marker},
    )

    assert opened.status_code == 200
    assert response.status_code == 200
    assert response.json()["data"]["results"] == [
        {
            "path": str(file_path),
            "name": file_path.name,
            "line_number": 1,
            "line_text": f"# {marker}",
            "match_count": 1,
        }
    ]


def test_search_skips_a_denied_resolved_junction_target(workspace, monkeypatch) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction behavior is platform-specific")

    client, root, _ = workspace
    target = root / "System32"
    target.mkdir()
    marker = "explorer-denied-resolved-junction-marker"
    target_file = target / "marker.txt"
    target_file.write_text(marker, encoding="utf-8")
    junction = root / "linked-system-files"
    _create_test_junction(junction, target)
    try:
        assert _resolves_to_target(junction, target)
        opened = client.post(
            "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
        )
        read = client.get(
            "/api/explorer/read",
            headers=auth_headers(),
            params={"path": str(target_file)},
        )
        path_type = type(root)
        original_iterdir = path_type.iterdir

        def only_expose_the_junction(path: Path):
            if path == root:
                return iter([junction])
            return original_iterdir(path)

        monkeypatch.setattr(path_type, "iterdir", only_expose_the_junction)
        search = client.post(
            "/api/explorer/search",
            headers=auth_headers(),
            json={"root_path": str(root), "query": marker},
        )

        assert opened.status_code == 200
        assert read.status_code == 403
        assert search.status_code == 200
        assert search.json()["data"]["results"] == []
    finally:
        _remove_test_junction(junction, target)


def test_search_keeps_system32_near_match_directory_searchable(workspace) -> None:
    client, root, _ = workspace
    marker = "explorer-normal-near-match-marker"
    file_path = root / "System32-notes" / "normal.txt"
    file_path.parent.mkdir()
    file_path.write_text(marker, encoding="utf-8")
    opened = client.post(
        "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
    )
    read = client.get(
        "/api/explorer/read",
        headers=auth_headers(),
        params={"path": str(file_path)},
    )
    search = client.post(
        "/api/explorer/search",
        headers=auth_headers(),
        json={"root_path": str(root), "query": marker},
    )

    assert opened.status_code == 200
    assert read.status_code == 200
    assert read.json()["content"] == marker
    assert search.status_code == 200
    assert [item["path"] for item in search.json()["data"]["results"]] == [
        str(file_path)
    ]


def test_search_does_not_follow_a_junction_outside_the_authorized_root(workspace) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction behavior is platform-specific")

    client, root, _ = workspace
    outside = root.parent / "outside"
    outside.mkdir()
    marker = "explorer-outside-junction-marker"
    outside_file = outside / "marker.txt"
    outside_file.write_text(marker, encoding="utf-8")
    junction = root / "linked-outside"
    _create_test_junction(junction, outside)
    try:
        assert _resolves_to_target(junction, outside)
        opened = client.post(
            "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
        )
        response = client.post(
            "/api/explorer/search",
            headers=auth_headers(),
            json={"root_path": str(root), "query": marker},
        )

        assert opened.status_code == 200
        assert response.status_code == 200
        assert response.json()["data"]["results"] == []
        assert outside_file.read_text(encoding="utf-8") == marker
    finally:
        _remove_test_junction(junction, outside)


def test_search_follows_an_in_root_junction_without_duplicate_hits(
    workspace, monkeypatch
) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction behavior is platform-specific")

    client, root, _ = workspace
    target = root / "real-source"
    target.mkdir()
    marker = "explorer-internal-junction-marker"
    target_file = target / "linked-source.py"
    target_file.write_text(f"# {marker}\n", encoding="utf-8")
    junction = root / "linked-source"
    _create_test_junction(junction, target)
    try:
        assert _resolves_to_target(junction, target)
        opened = client.post(
            "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
        )
        assert opened.status_code == 200

        path_type = type(root)
        original_iterdir = path_type.iterdir

        def only_expose_the_junction(path: Path):
            if path == root:
                return iter([junction])
            return original_iterdir(path)

        monkeypatch.setattr(path_type, "iterdir", only_expose_the_junction)
        response = client.post(
            "/api/explorer/search",
            headers=auth_headers(),
            json={"root_path": str(root), "query": marker},
        )

        assert response.status_code == 200
        assert response.json()["data"]["results"] == [
            {
                "path": str(junction / target_file.name),
                "name": target_file.name,
                "line_number": 1,
                "line_text": f"# {marker}",
                "match_count": 1,
            }
        ]
    finally:
        _remove_test_junction(junction, target)


def test_search_stops_at_a_junction_to_an_ancestor_without_duplicate_hits(workspace) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction behavior is platform-specific")

    client, root, _ = workspace
    nested = root / "nested"
    nested.mkdir()
    marker = "explorer-ancestor-loop-marker"
    marker_file = root / "root-marker.txt"
    marker_file.write_text(f"# {marker}\n", encoding="utf-8")
    junction = nested / "back-to-root"
    _create_test_junction(junction, root)
    try:
        opened = client.post(
            "/api/explorer/open", headers=auth_headers(), json={"path": str(root)}
        )
        response = client.post(
            "/api/explorer/search",
            headers=auth_headers(),
            json={"root_path": str(root), "query": marker, "max_results": 3},
        )

        assert opened.status_code == 200
        assert response.status_code == 200
        assert response.json()["data"]["results"] == [
            {
                "path": str(marker_file),
                "name": marker_file.name,
                "line_number": 1,
                "line_text": f"# {marker}",
                "match_count": 1,
            }
        ]
    finally:
        _remove_test_junction(junction, root)


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


def test_watch_accepts_the_bearer_header_and_requires_an_open_root(workspace) -> None:
    client, root, _ = workspace

    # The same Authorization header used by the fetch-based SSE client is the
    # only auth input; an unopened root is rejected with an actionable code.
    response = client.get(
        "/api/explorer/watch", params={"path": str(root)}, headers=auth_headers(),
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "EXPLORER_ROOT_NOT_AUTHORIZED"
    assert "重新打开" in body["error"]["message"]


def test_watch_without_bearer_header_is_401(workspace) -> None:
    client, root, _ = workspace

    response = client.get("/api/explorer/watch", params={"path": str(root)})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_duplicate_create_returns_one_readable_conflict_message(workspace) -> None:
    client, root, _ = workspace
    target = root / "big-3mb.txt"
    target.write_text("existing", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    response = client.post(
        "/api/explorer/create",
        headers=auth_headers(),
        json={"path": str(target), "type": "file"},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "EXPLORER_ALREADY_EXISTS"
    assert "已存在" in error["message"]
    assert "already exists" not in error["message"]
    assert error["message"].count("big-3mb.txt") == 1


def test_read_only_save_returns_access_denied_without_temp_path(workspace, monkeypatch) -> None:
    client, root, _ = workspace
    target = root / "readonly.txt"
    target.write_text("original", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    version = client.get(
        "/api/explorer/read", headers=auth_headers(), params={"path": str(target)}
    ).json()["version"]

    def deny_replace(source, destination):
        raise PermissionError(13, "Access is denied", str(source), None, str(destination))

    from src.explorer import files as explorer_files

    monkeypatch.setattr(explorer_files.os, "replace", deny_replace)

    response = client.post(
        "/api/explorer/write",
        headers=auth_headers(),
        json={"path": str(target), "content": "changed", "expected_version": version},
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "EXPLORER_ACCESS_DENIED"
    assert "只读" in error["message"] or "权限" in error["message"]
    assert "WinError" not in error["message"]
    assert ".tmp" not in error["message"]
    assert str(root) not in error["message"]
    assert error["details"] is None
    assert target.read_text(encoding="utf-8") == "original"
    assert list(root.glob(".*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute behavior")
def test_windows_read_only_file_save_returns_access_denied(workspace) -> None:
    client, root, _ = workspace
    target = root / "readonly.txt"
    target.write_text("original", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    version = client.get(
        "/api/explorer/read", headers=auth_headers(), params={"path": str(target)}
    ).json()["version"]
    os.chmod(target, stat.S_IREAD)
    try:
        response = client.post(
            "/api/explorer/write",
            headers=auth_headers(),
            json={"path": str(target), "content": "changed", "expected_version": version},
        )
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "EXPLORER_ACCESS_DENIED"
    assert "WinError" not in error["message"]
    assert ".tmp" not in error["message"]
    assert target.read_text(encoding="utf-8") == "original"
    assert list(root.glob(".*.tmp")) == []


def test_missing_file_returns_not_found_instead_of_security_error(workspace) -> None:
    client, root, _ = workspace
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    response = client.get(
        "/api/explorer/read",
        headers=auth_headers(),
        params={"path": str(root / "already-deleted.txt")},
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "EXPLORER_NOT_FOUND"
    assert "不存在" in error["message"]
    assert "security check" not in error["message"]


def test_open_missing_folder_returns_not_found(workspace) -> None:
    client, root, _ = workspace

    response = client.post(
        "/api/explorer/open",
        headers=auth_headers(),
        json={"path": str(root / "missing-folder")},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "EXPLORER_NOT_FOUND"


def test_unreadable_directory_reports_access_denied_instead_of_empty_list(
    workspace, monkeypatch
) -> None:
    client, root, _ = workspace
    denied = root / "denied-dir"
    denied.mkdir()
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    path_type = type(root)
    original_iterdir = path_type.iterdir

    def deny_unreadable_directory(path: Path):
        if path == denied:
            raise PermissionError(13, "Access is denied")
        return original_iterdir(path)

    monkeypatch.setattr(path_type, "iterdir", deny_unreadable_directory)

    response = client.get(
        "/api/explorer/dir", headers=auth_headers(), params={"path": str(denied)}
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "EXPLORER_ACCESS_DENIED"
    assert "权限" in error["message"]
    assert error["details"] is None


def test_unreadable_file_reports_access_denied(workspace, monkeypatch) -> None:
    client, root, _ = workspace
    target = root / "denied.txt"
    target.write_text("secret", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    path_type = type(root)
    original_read_bytes = path_type.read_bytes

    def deny_read_bytes(path: Path):
        if path == target:
            raise PermissionError(13, "Access is denied")
        return original_read_bytes(path)

    monkeypatch.setattr(path_type, "read_bytes", deny_read_bytes)

    response = client.get(
        "/api/explorer/read", headers=auth_headers(), params={"path": str(target)}
    )

    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "EXPLORER_ACCESS_DENIED"
    assert "WinError" not in error["message"]
    assert str(root) not in error["message"]


def test_reveal_builds_the_os_command_without_launching_a_window(
    workspace, monkeypatch
) -> None:
    client, root, _ = workspace
    target = root / "main.py"
    target.write_text("print('reveal')\n", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})
    calls: list[list[str]] = []

    fake_subprocess = types.SimpleNamespace(Popen=lambda args: calls.append(args))
    monkeypatch.setattr(explorer_routes, "subprocess", fake_subprocess)

    response = client.post(
        "/api/explorer/reveal", headers=auth_headers(), json={"path": str(target)}
    )
    missing = client.post(
        "/api/explorer/reveal",
        headers=auth_headers(),
        json={"path": str(root / "missing.txt")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["path"] == str(target)
    assert len(calls) == 1
    assert str(target) in calls[0]
    assert calls[0][0] in {"explorer", "open", "xdg-open"}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "EXPLORER_NOT_FOUND"
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive filesystem behavior")
def test_case_only_rename_is_allowed(workspace) -> None:
    client, root, _ = workspace
    original = root / "Case.txt"
    original.write_text("case payload", encoding="utf-8")
    client.post("/api/explorer/open", headers=auth_headers(), json={"path": str(root)})

    response = client.post(
        "/api/explorer/rename",
        headers=auth_headers(),
        json={"path": str(original), "new_name": "case.txt"},
    )

    assert response.status_code == 200
    assert [path.name for path in root.iterdir()] == ["case.txt"]
    assert (root / "case.txt").read_text(encoding="utf-8") == "case payload"
