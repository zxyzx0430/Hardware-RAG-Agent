"""Memory v1 acceptance tests.

Written against the frozen contract in
``E:/DevCaches/Temp/memory-v1-contract.md`` (tables, HTTP surface, tool names
and retrieval semantics), not against a particular implementation layout.

Dependency rules:
* A missing contract artefact (module, route, table) makes the affected test
  *skip* with the missing artefact named.  Skips are not passes; the runner in
  ``scripts/acceptance/memory_v1.py`` reports them separately and returns
  BLOCKED when nothing could be exercised.
* A present artefact that violates the contract *fails* the test.

Safety: the module only runs against an isolated SQLite database.  Running the
file on its own creates a temporary database automatically.  Inside a larger
suite (where ``app.db.database`` was already imported) the tests are skipped
unless the caller explicitly opts in with ``MEMORY_ACCEPTANCE_ISOLATED=1``.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEMP_BASES = (Path("E:/DevCaches/Temp"), Path("D:/DevCaches/Temp"))


def _temp_root() -> Path:
    for base in _TEMP_BASES:
        if base.is_dir():
            return base
    return Path(tempfile.gettempdir())


def _isolate_database() -> bool:
    if os.environ.get("MEMORY_ACCEPTANCE_ISOLATED") == "1":
        return True
    # A previous test module already bound app.db.database to a shared DB.
    # Never redirect (or touch) it implicitly.
    if "app.db.database" in sys.modules:
        return False
    root = Path(tempfile.mkdtemp(prefix="memory-v1-acceptance-", dir=str(_temp_root())))
    os.environ["SQLITE_DB_PATH"] = str(root / "memory-acceptance.sqlite")
    os.environ["MEMORY_ACCEPTANCE_ISOLATED"] = "1"
    os.environ.setdefault("AGENT_CHECKPOINTER_TYPE", "memory")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ["TEMP"] = str(root)
    os.environ["TMP"] = str(root)
    return True


_ISOLATED = _isolate_database()

pytestmark = pytest.mark.skipif(
    not _ISOLATED,
    reason=(
        "memory acceptance needs an isolated database: run this file on its own "
        "or set MEMORY_ACCEPTANCE_ISOLATED=1 with a temporary SQLITE_DB_PATH"
    ),
)

MEMORY_TABLE = "memory_entries"
PROFILE_TABLE = "hardware_profiles"

MEMORY_COLUMNS = (
    "id",
    "scope",
    "scope_key",
    "kind",
    "content",
    "structured_json",
    "source",
    "confidence",
    "created_at",
    "updated_at",
    "last_used_at",
    "expires_at",
    "deleted_at",
    "supersedes_id",
)

PROFILE_COLUMNS = (
    "id",
    "name",
    "aliases_json",
    "mcu",
    "package",
    "voltage",
    "clock",
    "pins_json",
    "peripherals_json",
    "toolchain",
    "sources_json",
    "rejected_json",
    "confidence",
    "use_count",
    "last_used_at",
    "created_at",
    "updated_at",
)

_SECRET_SAMPLES = (
    "sk-live-4f9a2c7e1b3d5f8a0c2e4b6d8f1a3c5e",
    "AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
)


# ---------------------------------------------------------------------------
# Isolation helpers
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    from app.db import database

    return Path(database.DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, name: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({name})")]


def _index_column_sets(conn: sqlite3.Connection, name: str) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for index_row in conn.execute(f"PRAGMA index_list({name})"):
        index_name = index_row["name"]
        columns = tuple(
            row["name"] for row in conn.execute(f"PRAGMA index_info({index_name})")
        )
        if columns:
            result.add(columns)
    return result


def _ensure_schema() -> None:
    """Import contract models and create tables on the isolated engine."""
    for module_name in ("app.db.memory_models", "app.db.hardware_profile_models"):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            pass
    from app.db.database import init_db

    init_db()


def _reset_tables() -> None:
    _ensure_schema()
    with _connect() as conn:
        for table in (MEMORY_TABLE, PROFILE_TABLE):
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table}")
        conn.commit()


def _require_table(name: str) -> sqlite3.Connection:
    _ensure_schema()
    conn = _connect()
    if not _table_exists(conn, name):
        conn.close()
        pytest.skip(
            f"{name} table was not registered by init_db; "
            "memory-v1 persistence dependency is not merged"
        )
    return conn


def _now_text(offset_seconds: float = 0.0) -> str:
    value = datetime.now(timezone.utc).timestamp() + offset_seconds
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def _insert_memory_row(**overrides: Any) -> str:
    """Insert a row directly (used for values the public API must reject)."""
    entry_id = overrides.pop("id", uuid.uuid4().hex)
    values = {
        "id": entry_id,
        "scope": "global",
        "scope_key": "",
        "kind": "fact",
        "content": "direct row",
        "structured_json": "{}",
        "source": "user",
        "confidence": 0.7,
        "created_at": _now_text(),
        "updated_at": _now_text(),
        "last_used_at": None,
        "expires_at": None,
        "deleted_at": None,
        "supersedes_id": None,
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {MEMORY_TABLE} ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        conn.commit()
    return entry_id


def _insert_profile_row(**overrides: Any) -> str:
    profile_id = overrides.pop("id", uuid.uuid4().hex)
    values = {
        "id": profile_id,
        "name": "MARKER_BOARD",
        "aliases_json": "[]",
        "mcu": "MARKER_MCU",
        "package": "",
        "voltage": "",
        "clock": "",
        "pins_json": "{}",
        "peripherals_json": "[]",
        "toolchain": "",
        "sources_json": "[]",
        "rejected_json": "[]",
        "confidence": 0.7,
        "use_count": 0,
        "last_used_at": None,
        "created_at": _now_text(),
        "updated_at": _now_text(),
    }
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {PROFILE_TABLE} ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        conn.commit()
    return profile_id


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient
    from app.main import create_app

    return TestClient(create_app())


def _json(response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _is_default_404(response) -> bool:
    if response.status_code != 404:
        return False
    payload = _json(response)
    return isinstance(payload, dict) and payload.get("detail") == "Not Found"


def _require_memory_api(client) -> None:
    response = client.get("/api/memory")
    if _is_default_404(response):
        pytest.skip(
            "GET /api/memory is not registered; memory routes dependency is not merged"
        )


def _data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and "success" in payload:
        return payload["data"]
    return payload


def _entry_of(item: Any) -> dict:
    if not isinstance(item, dict):
        return {}
    for key in ("entry", "memory", "item", "result", "data"):
        nested = item.get(key)
        if isinstance(nested, dict):
            return _entry_of(nested)
    return item


def _list_entries(payload: Any) -> list[dict]:
    node = _data(payload)
    if isinstance(node, list):
        return [_entry_of(item) for item in node]
    if isinstance(node, dict):
        for key in ("entries", "items", "memories", "results", "data", "list"):
            value = node.get(key)
            if isinstance(value, list):
                return [_entry_of(item) for item in value]
    return []


def _single_entry(payload: Any) -> dict:
    node = _data(payload)
    if isinstance(node, dict):
        for key in ("entry", "memory", "item", "result", "profile", "data"):
            value = node.get(key)
            if isinstance(value, dict):
                return _single_entry(value)
        return node
    return {}


def _content_of(entry: dict) -> str:
    value = entry.get("content")
    return value if isinstance(value, str) else ""


def _id_of(entry: dict) -> str:
    value = entry.get("id")
    return value if isinstance(value, str) else ""


def _create_memory(client, **overrides: Any) -> dict:
    body = {
        "scope": "global",
        "scope_key": "",
        "kind": "preference",
        "content": f"acceptance entry {uuid.uuid4().hex}",
        "source": "user",
        "confidence": 0.8,
    }
    body.update(overrides)
    response = client.post("/api/memory", json=body)
    assert response.status_code in (200, 201), (
        f"POST /api/memory returned {response.status_code}: {response.text[:500]}"
    )
    entry = _single_entry(_json(response))
    assert _id_of(entry), f"POST /api/memory response has no id: {_json(response)}"
    return entry


def _get_memories(client, **params: Any) -> list[dict]:
    response = client.get("/api/memory", params=params)
    assert response.status_code == 200, (
        f"GET /api/memory {params} returned {response.status_code}: {response.text[:500]}"
    )
    return _list_entries(_json(response))


# ---------------------------------------------------------------------------
# Tool helpers (contract tool names, tolerant of class/callable packaging)
# ---------------------------------------------------------------------------


def _tool_module(area: str) -> Any:
    module_name = (
        "src.agent.tools.groups.memory"
        if area == "memory"
        else "src.agent.tools.groups.hardware_profile"
    )
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        pytest.skip(f"{module_name} is missing; {area} tool dependency is not merged")


def _resolve_tool(area: str, tool_name: str) -> Any:
    module = _tool_module(area)
    try:
        from src.agent.agent_factory import ensure_default_tools_registered
        from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY

        ensure_default_tools_registered()
        spec = _TOOL_REGISTRY.get(tool_name)
        if spec is not None:
            return spec
    except Exception:
        pass

    candidate = getattr(module, tool_name, None)
    if callable(candidate) and not inspect.isclass(candidate):
        return candidate

    camel = "".join(part.capitalize() for part in tool_name.split("_")) + "Tool"
    cls = getattr(module, camel, None)
    if inspect.isclass(cls):
        try:
            return cls()
        except Exception as exc:  # pragma: no cover - implementation detail
            pytest.skip(f"cannot instantiate {camel}: {exc}")
    pytest.skip(
        f"{tool_name} tool not found (expected registry entry, module function "
        f"{tool_name}, or class {camel})"
    )


def _run(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _call_tool(area: str, tool_name: str, args: dict, **fallback_args: dict) -> Any:
    resolved = _resolve_tool(area, tool_name)
    ctx = None
    if hasattr(resolved, "execute"):
        from src.agent.exceptions import ToolContext

        ctx = ToolContext(session_id="memory-acceptance", permission_mode="acceptEdits")
        return _run(resolved.execute(args, ctx))
    try:
        return _run(resolved(**args))
    except TypeError:
        if fallback_args:
            return _run(resolved(**fallback_args))
        raise


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _find_key(value: Any, keys: tuple[str, ...]) -> Any:
    for node in _walk(value):
        if isinstance(node, dict):
            for key in keys:
                if key in node and isinstance(node[key], (str, int)):
                    return node[key]
    return None


def _find_dicts(value: Any, marker_key: str) -> list[dict]:
    return [
        node
        for node in _walk(value)
        if isinstance(node, dict) and marker_key in node
    ]


def _tool_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    for node in _walk(value):
        if isinstance(node, dict):
            for key in ("output", "text", "message"):
                if isinstance(node.get(key), str):
                    return node[key]
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Schema and route surface
# ---------------------------------------------------------------------------


def test_memory_table_schema_matches_contract():
    conn = _require_table(MEMORY_TABLE)
    try:
        columns = _columns(conn, MEMORY_TABLE)
        missing = [name for name in MEMORY_COLUMNS if name not in columns]
        assert not missing, f"{MEMORY_TABLE} is missing columns: {missing}"

        indexes = _index_column_sets(conn, MEMORY_TABLE)
        required = {
            ("scope", "scope_key", "kind"),
            ("deleted_at",),
            ("updated_at",),
        }
        missing_indexes = [item for item in required if item not in indexes]
        assert not missing_indexes, (
            f"{MEMORY_TABLE} is missing required indexes {missing_indexes}; "
            f"present: {sorted(indexes)}"
        )
    finally:
        conn.close()


def test_hardware_profile_table_schema_matches_contract():
    conn = _require_table(PROFILE_TABLE)
    try:
        columns = _columns(conn, PROFILE_TABLE)
        missing = [name for name in PROFILE_COLUMNS if name not in columns]
        assert not missing, f"{PROFILE_TABLE} is missing columns: {missing}"

        indexes = _index_column_sets(conn, PROFILE_TABLE)
        required = {("name",), ("updated_at",)}
        missing_indexes = [item for item in required if item not in indexes]
        assert not missing_indexes, (
            f"{PROFILE_TABLE} is missing required indexes {missing_indexes}; "
            f"present: {sorted(indexes)}"
        )
    finally:
        conn.close()


def test_memory_http_surface_registered():
    client = _client()
    _require_memory_api(client)
    marker = f"surface-{uuid.uuid4().hex}"
    entry = _create_memory(client, content=marker)
    entry_id = _id_of(entry)

    patch = client.patch(f"/api/memory/{entry_id}", json={"content": marker + "-v2"})
    assert patch.status_code in (200, 204), (
        f"PATCH /api/memory/{{id}} returned {patch.status_code}: {patch.text[:500]}"
    )
    search = client.post("/api/memory/search", json={"query": marker, "limit": 5})
    assert search.status_code == 200, (
        f"POST /api/memory/search returned {search.status_code}: {search.text[:500]}"
    )
    delete = client.delete(f"/api/memory/{entry_id}")
    assert delete.status_code in (200, 204), (
        f"DELETE /api/memory/{{id}} returned {delete.status_code}: {delete.text[:500]}"
    )


def test_memory_writes_require_auth_when_provider_configured():
    from unittest.mock import patch

    client = _client()
    _require_memory_api(client)
    configured = {"providers": {"acceptance": {"encrypted_key": "x"}}, "sessions": {}}
    with patch("app.api.auth._load_store", return_value=configured):
        response = client.post(
            "/api/memory",
            json={
                "scope": "global",
                "scope_key": "",
                "kind": "fact",
                "content": f"AUTH-{uuid.uuid4().hex}",
                "source": "user",
                "confidence": 0.8,
            },
        )
    assert response.status_code in (401, 403), (
        "memory writes must require normal auth once a provider is configured, "
        f"got {response.status_code}: {response.text[:300]}"
    )


# ---------------------------------------------------------------------------
# Generic memory CRUD, scope isolation, soft delete
# ---------------------------------------------------------------------------


def test_memory_crud_and_scope_isolation():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    global_marker = f"GLOBAL-{uuid.uuid4().hex}"
    project_a_marker = f"PROJECT-A-{uuid.uuid4().hex}"
    project_b_marker = f"PROJECT-B-{uuid.uuid4().hex}"
    session_marker = f"SESSION-{uuid.uuid4().hex}"

    global_entry = _create_memory(
        client, scope="global", scope_key="", kind="preference", content=global_marker
    )
    project_a = _create_memory(
        client,
        scope="project",
        scope_key="E:/work/project-a",
        kind="decision",
        content=project_a_marker,
    )
    _create_memory(
        client,
        scope="project",
        scope_key="E:/work/project-b",
        kind="decision",
        content=project_b_marker,
    )
    _create_memory(
        client,
        scope="session",
        scope_key="session-001",
        kind="fact",
        content=session_marker,
    )

    assert _id_of(global_entry) and _id_of(project_a)

    def contents(entries: list[dict]) -> set[str]:
        return {_content_of(entry) for entry in entries}

    assert contents(_get_memories(client, scope="global")) == {global_marker}
    assert contents(
        _get_memories(client, scope="project", scope_key="E:/work/project-a")
    ) == {project_a_marker}
    assert contents(
        _get_memories(client, scope="project", scope_key="E:/work/project-b")
    ) == {project_b_marker}
    assert contents(
        _get_memories(client, scope="session", scope_key="session-001")
    ) == {session_marker}

    decisions = _get_memories(client, kind="decision")
    assert contents(decisions) == {project_a_marker, project_b_marker}

    filtered = _get_memories(client, q="PROJECT-A")
    assert contents(filtered) == {project_a_marker}, (
        "q filter must match content within the requested scope"
    )

    scoped_search = client.post(
        "/api/memory/search",
        json={
            "query": "PROJECT",
            "scope": "project",
            "scope_key": "E:/work/project-a",
            "limit": 10,
        },
    )
    assert scoped_search.status_code == 200, scoped_search.text[:500]
    assert contents(_list_entries(_json(scoped_search))) == {project_a_marker}, (
        "search must honor scope and scope_key filters"
    )

    limited = _get_memories(client, limit=1)
    assert len(limited) == 1, f"limit=1 returned {len(limited)} entries"


def test_memory_expired_entries_are_hidden():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    marker = f"EXPIRED-{uuid.uuid4().hex}"
    _create_memory(
        client,
        content=marker,
        expires_at="2020-01-01T00:00:00Z",
    )
    assert marker not in {_content_of(item) for item in _get_memories(client, q=marker)}, (
        "an expired entry must not be listed"
    )
    search = client.post("/api/memory/search", json={"query": marker, "limit": 10})
    assert search.status_code == 200
    assert marker not in {_content_of(item) for item in _list_entries(_json(search))}


def test_memory_patch_updates_fields():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    marker = f"PATCH-{uuid.uuid4().hex}"
    entry = _create_memory(client, content=marker, confidence=0.4)
    entry_id = _id_of(entry)

    response = client.patch(
        f"/api/memory/{entry_id}",
        json={
            "content": marker + "-updated",
            "structured": {"board": "ESP32-S3"},
            "confidence": 0.95,
        },
    )
    assert response.status_code in (200, 204), response.text[:500]

    refreshed = _single_entry(_json(response)) if response.text else {}
    if not _id_of(refreshed):
        listed = _get_memories(client, q=marker)
        refreshed = next(
            (item for item in listed if _id_of(item) == entry_id), {}
        )
    assert _content_of(refreshed) == marker + "-updated"
    assert float(refreshed.get("confidence", 0)) >= 0.95 - 1e-9

    with _connect() as conn:
        row = conn.execute(
            f"SELECT content, confidence, structured_json FROM {MEMORY_TABLE} "
            "WHERE id = ?",
            (entry_id,),
        ).fetchone()
    assert row is not None
    assert row["content"] == marker + "-updated"
    assert float(row["confidence"]) >= 0.95 - 1e-9
    structured = json.loads(row["structured_json"])
    assert structured.get("board") == "ESP32-S3"


def test_memory_delete_is_soft_and_hidden_from_list_and_search():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    marker = f"SOFT-DELETE-{uuid.uuid4().hex}"
    entry = _create_memory(client, content=marker)
    entry_id = _id_of(entry)

    response = client.delete(f"/api/memory/{entry_id}")
    assert response.status_code in (200, 204), response.text[:500]

    assert all(
        _id_of(item) != entry_id for item in _get_memories(client, q=marker)
    ), "soft-deleted entry must not appear in GET /api/memory"

    search = client.post("/api/memory/search", json={"query": marker, "limit": 10})
    assert search.status_code == 200
    assert all(
        _id_of(item) != entry_id for item in _list_entries(_json(search))
    ), "soft-deleted entry must not appear in search"

    with _connect() as conn:
        row = conn.execute(
            f"SELECT deleted_at FROM {MEMORY_TABLE} WHERE id = ?", (entry_id,)
        ).fetchone()
    assert row is not None, "DELETE must soft-delete, not remove the row"
    assert row["deleted_at"] not in (None, ""), "deleted_at must be set"


def test_memory_search_ranks_relevant_entry_first():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    unique = f"ZEPHYRFLAG{uuid.uuid4().hex}".lower()
    noise = [
        f"unrelated note about soldering {uuid.uuid4().hex}",
        f"unrelated note about power supplies {uuid.uuid4().hex}",
        f"unrelated note about oscilloscopes {uuid.uuid4().hex}",
    ]
    for content in noise:
        _create_memory(client, kind="fact", content=content)
    target = _create_memory(
        client,
        kind="fact",
        content=f"Build flag {unique} enables the UART bridge on this board",
    )

    response = client.post("/api/memory/search", json={"query": unique, "limit": 3})
    assert response.status_code == 200, response.text[:500]
    results = _list_entries(_json(response))
    assert results, "search returned no results for an exact token"
    assert _id_of(results[0]) == _id_of(target), (
        "the entry containing the exact query token must rank first; "
        f"got {[_content_of(item) for item in results]}"
    )
    assert len(results) <= 3


@pytest.mark.parametrize("secret", _SECRET_SAMPLES)
def test_memory_write_rejects_secret_content(secret: str):
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    marker = f"SECRET-{uuid.uuid4().hex}"
    response = client.post(
        "/api/memory",
        json={
            "scope": "global",
            "scope_key": "",
            "kind": "fact",
            "content": f"{marker} {secret}",
            "source": "user",
            "confidence": 0.8,
        },
    )
    assert 400 <= response.status_code < 500, (
        "secret-looking content must be rejected (4xx), got "
        f"{response.status_code}: {response.text[:300]}"
    )
    with _connect() as conn:
        row = conn.execute(
            f"SELECT 1 FROM {MEMORY_TABLE} WHERE content LIKE ?",
            (f"%{marker}%",),
        ).fetchone()
    assert row is None, "rejected secret content must not be persisted"


def test_memory_write_rejects_secret_structured_fields():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    marker = f"STRUCTURED-SECRET-{uuid.uuid4().hex}"
    response = client.post(
        "/api/memory",
        json={
            "scope": "global",
            "scope_key": "",
            "kind": "fact",
            "content": marker,
            "structured": {"password": "hunter2", "note": "plain"},
            "source": "user",
            "confidence": 0.8,
        },
    )
    assert 400 <= response.status_code < 500, (
        "secret-looking structured fields must be rejected (4xx), got "
        f"{response.status_code}: {response.text[:300]}"
    )
    with _connect() as conn:
        row = conn.execute(
            f"SELECT 1 FROM {MEMORY_TABLE} WHERE content = ?", (marker,)
        ).fetchone()
    assert row is None


# ---------------------------------------------------------------------------
# Generic memory injection
# ---------------------------------------------------------------------------


_CONTEXT_NAME_HINTS = ("context", "block", "prompt", "inject")


def _resolve_context_builder():
    try:
        module = importlib.import_module("src.agent.memory_context")
    except ModuleNotFoundError:
        pytest.skip("src/agent/memory_context.py is missing; retrieval dependency is not merged")

    preferred = (
        "build_memory_context",
        "get_memory_context",
        "render_memory_context",
        "memory_context_block",
        "build_context_block",
        "memory_context",
    )
    for name in preferred:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    for name, candidate in vars(module).items():
        if name.startswith("_") or not callable(candidate):
            continue
        lowered = name.lower()
        if "memory" in lowered and any(hint in lowered for hint in _CONTEXT_NAME_HINTS):
            return candidate
    pytest.skip(
        "src.agent.memory_context exposes no callable memory-context builder "
        f"(looked for {preferred})"
    )


def _bind_call(fn, values: dict[str, Any], context: str) -> Any:
    signature = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if name in values:
            kwargs[name] = values[name]
        elif parameter.default is not inspect.Parameter.empty:
            continue
        else:
            pytest.skip(
                f"{context}: cannot bind required parameter '{name}' of "
                f"{getattr(fn, '__name__', fn)}; implementation seam differs from "
                "the acceptance harness"
            )
    return _run(fn(**kwargs))


def _result_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_result_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "block", "prompt", "content", "output", "context"):
            if isinstance(value.get(key), str):
                return value[key]
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def test_generic_injection_excludes_hardware_entries():
    _reset_tables()
    client = _client()
    _require_memory_api(client)

    memory_marker = f"MEMORY-INJECT-{uuid.uuid4().hex}"
    hardware_marker = f"HARDWARE-INJECT-{uuid.uuid4().hex}"

    normal = _create_memory(
        client, scope="global", kind="preference", content=memory_marker
    )
    assert _id_of(normal)

    # A hardware marker in the generic table must never be injected.  The API may
    # reject the internal kind, so insert the row directly.
    _insert_memory_row(kind="hardware_profile", content=hardware_marker)
    if _table_exists(_connect(), PROFILE_TABLE):
        _insert_profile_row(
            name=hardware_marker,
            mcu="MARKER_MCU_XYZ",
            pins_json=json.dumps({"GPIO0": "MARKER_PIN"}),
        )

    builder = _resolve_context_builder()
    text = _result_text(
        _bind_call(
            builder,
            {
                "scope": "global",
                "scope_key": "",
                "session_id": "memory-acceptance",
                "limit": 5,
                "max_tokens": 1000,
                "query": memory_marker,
                "db_path": str(_db_path()),
            },
            "memory_context builder",
        )
    )

    assert memory_marker in text, (
        "generic memory context must include the normal memory entry; "
        f"got: {text[:500]!r}"
    )
    assert hardware_marker not in text, (
        "generic memory context must not include hardware-profile content"
    )
    assert "MARKER_MCU_XYZ" not in text
    assert "MARKER_PIN" not in text

    with _connect() as conn:
        row = conn.execute(
            f"SELECT last_used_at FROM {MEMORY_TABLE} WHERE id = ?",
            (_id_of(normal),),
        ).fetchone()
    assert row is not None and row["last_used_at"] not in (None, ""), (
        "injected entries must have last_used_at updated"
    )


def test_hardware_mention_hint_only_without_profile_body():
    _reset_tables()
    profile_marker = f"HWPROFILE-{uuid.uuid4().hex}"
    alias = f"markerboard{uuid.uuid4().hex[:8]}"
    _require_table(PROFILE_TABLE)
    _insert_profile_row(
        name=profile_marker,
        aliases_json=json.dumps([alias]),
        mcu="MARKER_MCU_BODY",
        pins_json=json.dumps({"GPIO7": "MARKER_PIN_BODY"}),
        peripherals_json=json.dumps(["MARKER_PERIPHERAL_BODY"]),
        sources_json=json.dumps(["MARKER_SOURCE_BODY"]),
    )

    try:
        module = importlib.import_module("src.agent.hardware_mention")
    except ModuleNotFoundError:
        pytest.skip("src/agent/hardware_mention.py is missing; trigger dependency is not merged")

    candidates = [
        candidate
        for name, candidate in vars(module).items()
        if not name.startswith("_")
        and callable(candidate)
        and ("mention" in name.lower() or "hint" in name.lower())
    ]
    assert candidates, (
        "src.agent.hardware_mention exposes no mention/hint callable; "
        "expected a one-line hint builder"
    )
    builder = candidates[0]
    values = {
        "message": "",
        "user_message": "",
        "text": "",
        "latest_user_message": "",
        "query": "",
        "db_path": str(_db_path()),
        "session_id": "memory-acceptance",
    }

    def hint_for(message: str) -> str:
        local = dict(values)
        for key in ("message", "user_message", "text", "latest_user_message", "query"):
            local[key] = message
        return _result_text(
            _bind_call(builder, local, "hardware_mention hint builder")
        )

    no_hint = hint_for("Please summarize the notes from the last meeting.")
    assert not no_hint.strip(), (
        "a message without a hardware alias must not produce a profile hint; "
        f"got: {no_hint[:300]!r}"
    )

    hint = hint_for(f"How should I wire the {alias} board for UART logging?")
    assert hint.strip(), "a message mentioning a known alias must produce a hint"
    assert "hardware-profile" in hint or "load_skill" in hint, (
        "the hint must tell the agent to use the hardware-profile Skill; "
        f"got: {hint[:300]!r}"
    )
    for leaked in (
        "MARKER_MCU_BODY",
        "MARKER_PIN_BODY",
        "MARKER_PERIPHERAL_BODY",
        "MARKER_SOURCE_BODY",
    ):
        assert leaked not in hint, (
            "the hint must not contain profile body fields; "
            f"leaked {leaked}: {hint[:300]!r}"
        )


# ---------------------------------------------------------------------------
# Agent memory tools
# ---------------------------------------------------------------------------


def test_memory_tools_remember_search_forget():
    _reset_tables()
    _ensure_schema()
    _require_table(MEMORY_TABLE)

    marker = f"TOOL-MEMORY-{uuid.uuid4().hex}"
    remembered = _call_tool(
        "memory",
        "memory_remember",
        {
            "content": marker,
            "kind": "fact",
            "scope": "project",
            "structured": {"source": "acceptance"},
            "confidence": 0.8,
            "source": "agent",
        },
    )
    entry_id = _find_key(remembered, ("id", "memory_id", "entry_id"))
    assert entry_id, f"memory_remember returned no id: {remembered}"

    with _connect() as conn:
        row = conn.execute(
            f"SELECT scope, kind, content FROM {MEMORY_TABLE} WHERE id = ?",
            (entry_id,),
        ).fetchone()
    assert row is not None, "memory_remember must persist a memory entry"
    assert row["scope"] == "project"
    assert row["kind"] == "fact"
    assert marker in row["content"]

    searched = _call_tool("memory", "memory_search", {"query": marker, "limit": 5})
    assert marker in _tool_text(searched), (
        f"memory_search did not return the remembered entry: {searched}"
    )

    _call_tool("memory", "memory_forget", {"id": entry_id})
    with _connect() as conn:
        row = conn.execute(
            f"SELECT deleted_at FROM {MEMORY_TABLE} WHERE id = ?", (entry_id,)
        ).fetchone()
    assert row is not None
    assert row["deleted_at"] not in (None, ""), "memory_forget must soft-delete"


# ---------------------------------------------------------------------------
# Hardware profile tool
# ---------------------------------------------------------------------------


def _upsert_profile(profile: dict) -> Any:
    return _call_tool(
        "hardware_profile",
        "hardware_profile",
        {"action": "upsert", "profile": profile},
        action="upsert",
        **profile,
    )


def _search_profiles(query: str, limit: int = 5) -> Any:
    return _call_tool(
        "hardware_profile",
        "hardware_profile",
        {"action": "search", "query": query, "limit": limit},
    )


def _get_profile(profile_id: str) -> Any:
    return _call_tool(
        "hardware_profile",
        "hardware_profile",
        {"action": "get", "id": profile_id},
    )


def _profile_rows(payload: Any) -> list[dict]:
    rows = _find_dicts(payload, "name")
    return [row for row in rows if row.get("name")]


def test_hardware_profile_upsert_merge_and_alias_search():
    _reset_tables()
    _require_table(PROFILE_TABLE)

    name = f"ESP32-S3-DevKitC-{uuid.uuid4().hex[:6]}"
    alias = f"esp32s3-{uuid.uuid4().hex[:6]}"
    second_alias = f"s3-devkit-{uuid.uuid4().hex[:6]}"

    created = _upsert_profile(
        {
            "name": name,
            "aliases": [alias],
            "mcu": "ESP32-S3",
            "package": "QFN56",
            "voltage": "",
            "clock": "240MHz",
            "pins": {"GPIO0": "BOOT", "GPIO43": "UART0_TX"},
            "peripherals": ["UART", "WiFi"],
            "toolchain": "PlatformIO",
            "sources": ["datasheet-v1"],
            "rejected": ["GPIO99"],
            "confidence": 0.8,
        }
    )
    profile_id = _find_key(created, ("id", "profile_id"))
    assert profile_id, f"upsert returned no profile id: {created}"

    search = _search_profiles(alias)
    search_rows = _profile_rows(search)
    names = [row.get("name") for row in search_rows]
    assert name in names, (
        f"search by alias {alias!r} must find {name!r}; got {search}"
    )
    summary = next(row for row in search_rows if row.get("name") == name)
    assert summary.get("mcu") == "ESP32-S3", "search summary must include mcu"
    assert alias in (summary.get("aliases") or []), "search summary must include aliases"
    assert "confidence" in summary, "search summary must include confidence"

    fetched = _get_profile(profile_id)
    fetched_rows = _profile_rows(fetched)
    assert fetched_rows, f"get returned no profile: {fetched}"
    profile = fetched_rows[0]
    assert profile.get("mcu") == "ESP32-S3"
    assert profile.get("clock") == "240MHz"
    assert alias in (profile.get("aliases") or [])
    assert "GPIO43" in json.dumps(profile.get("pins") or {})

    with _connect() as conn:
        before = conn.execute(
            f"SELECT use_count, confidence FROM {PROFILE_TABLE} WHERE id = ?",
            (profile_id,),
        ).fetchone()
    assert before is not None
    before_count = int(before["use_count"])
    before_confidence = float(before["confidence"])

    merged = _upsert_profile(
        {
            "name": alias,
            "aliases": [second_alias],
            "voltage": "3.3V",
            "sources": ["datasheet-v2"],
            "confidence": 0.5,
        }
    )
    merged_id = _find_key(merged, ("id", "profile_id"))
    assert merged_id == profile_id, (
        "upsert by alias must merge into the existing profile, "
        f"got {merged_id!r} instead of {profile_id!r}"
    )

    fetched = _profile_rows(_get_profile(profile_id))[0]
    assert fetched.get("voltage") == "3.3V", "non-empty field must be filled"
    assert fetched.get("mcu") == "ESP32-S3", "empty incoming field must not wipe value"
    aliases = fetched.get("aliases") or []
    assert alias in aliases and second_alias in aliases, (
        f"aliases must be merged, got {aliases}"
    )
    sources = fetched.get("sources") or []
    assert "datasheet-v1" in sources and "datasheet-v2" in sources, (
        f"sources must be merged, got {sources}"
    )
    assert "GPIO99" in (fetched.get("rejected") or [])
    assert float(fetched.get("confidence", 0)) >= before_confidence - 1e-9, (
        "confidence must not decrease without explicit evidence"
    )

    with _connect() as conn:
        after = conn.execute(
            f"SELECT use_count FROM {PROFILE_TABLE} WHERE id = ?", (profile_id,)
        ).fetchone()
    assert int(after["use_count"]) > before_count, "upsert must bump use_count"


def test_hardware_profile_upsert_requires_name():
    _reset_tables()
    _require_table(PROFILE_TABLE)
    try:
        result = _upsert_profile({"mcu": "ESP32"})
    except Exception:
        return  # raising is an acceptable rejection
    error_field = result.get("error") if isinstance(result, dict) else ""
    text = _tool_text(result).lower()
    assert error_field or any(
        word in text for word in ("失败", "invalid", "required", "name")
    ), f"upsert without a name must be rejected, got: {result}"
