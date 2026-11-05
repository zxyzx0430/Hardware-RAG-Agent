"""Read-only probe for the Memory v2 product surface.

The probe never creates or migrates a database and never calls a model.  It
answers one question: can the deterministic scenario runner bind to a merged
v2 implementation?  A missing interface is reported as ``pending`` by the
runner, never as a pass.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable

CoreCheck = tuple[str, Callable[[], str]]


def _import(name: str) -> Any:
    return importlib.import_module(name)


def _check_memory_models() -> str:
    module = _import("app.db.memory_models")
    entry = getattr(module, "MemoryEntry")
    columns = set(entry.__table__.columns.keys())
    required = {"tier", "status", "project_id", "evidence_json"}
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(f"MemoryEntry lacks columns {missing}")
    snapshot = getattr(module, "MemorySnapshot", None)
    if snapshot is None:
        from app.db.database import Base

        if "memory_snapshots" not in Base.metadata.tables:
            raise RuntimeError("MemorySnapshot model/table is absent")
        return "tier/status/project_id/evidence_json columns and memory_snapshots table present"
    return "v2 columns and MemorySnapshot model present"


def _check_store_methods() -> str:
    store = getattr(_import("app.db.memory_store"), "MemoryStore")
    required = ("update_status", "find_duplicate", "upsert_proposed", "purge_session", "search")
    missing = [name for name in required if not callable(getattr(store, name, None))]
    if missing:
        raise RuntimeError(f"MemoryStore lacks callables {missing}")
    search_params = inspect.signature(store.search).parameters
    extras = [name for name in ("project_id", "status", "session_id") if name not in search_params]
    suffix = f"; search signature lacks explicit {extras}" if extras else ""
    return f"v2 store methods present{suffix}"


def _check_context_builder() -> str:
    builder = getattr(_import("src.agent.memory_context"), "build_memory_context")
    params = inspect.signature(builder).parameters
    required = ("project_id", "session_id", "query")
    missing = [name for name in required if name not in params]
    if missing:
        raise RuntimeError(f"build_memory_context lacks parameters {missing}")
    return "build_memory_context accepts project_id/session_id/query"


def _check_project_identity() -> str:
    module = _import("src.agent.memory_project")
    resolver = getattr(module, "current_project_id", None)
    if not callable(resolver):
        raise RuntimeError("src.agent.memory_project.current_project_id is absent")
    return "current_project_id is callable"


def _check_schema_hook() -> str:
    module = _import("app.db.memory_v2_schema")
    hook = getattr(module, "apply_memory_v2_schema", None)
    if not callable(hook):
        raise RuntimeError("apply_memory_v2_schema is absent")
    return "apply_memory_v2_schema is callable"


def _check_policy_module() -> str:
    module = _import("src.agent.memory_policy")
    candidates = (
        "route_candidate",
        "apply_candidate",
        "extract_candidates",
        "propose_from_turns",
        "remember_candidate",
    )
    present = [name for name in candidates if callable(getattr(module, name, None))]
    if not present:
        raise RuntimeError(f"memory_policy exposes none of {list(candidates)}")
    return f"memory_policy exposes {present}"


def _check_projection_module() -> str:
    module = _import("src.agent.memory_projection")
    candidates = ("project_confirmed_checkpoint", "read_confirmed_checkpoint")
    present = [name for name in candidates if callable(getattr(module, name, None))]
    if not present:
        raise RuntimeError(f"memory_projection exposes none of {list(candidates)}")
    return f"memory_projection exposes {present}"


def _check_proposal_routes() -> str:
    module = _import("app.api.memory_routes")
    router = getattr(module, "router")
    paths = sorted({getattr(route, "path", "") for route in router.routes})
    proposal_paths = [path for path in paths if "proposal" in path]
    if not proposal_paths:
        raise RuntimeError(f"no proposal route in {paths}")
    return f"proposal routes present: {proposal_paths}"


CORE_CHECKS: tuple[CoreCheck, ...] = (
    ("memory_models", _check_memory_models),
    ("memory_store", _check_store_methods),
    ("memory_context", _check_context_builder),
    ("project_identity", _check_project_identity),
    ("memory_v2_schema", _check_schema_hook),
    ("memory_policy", _check_policy_module),
    ("memory_projection", _check_projection_module),
)

OPTIONAL_CHECKS: tuple[CoreCheck, ...] = (
    ("proposal_routes", _check_proposal_routes),
)


def probe_product_v2() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for name, probe in (*CORE_CHECKS, *OPTIONAL_CHECKS):
        try:
            detail = probe()
            checks.append({"name": name, "ok": True, "core": name in dict(CORE_CHECKS), "detail": detail})
        except Exception as exc:
            checks.append(
                {
                    "name": name,
                    "ok": False,
                    "core": name in dict(CORE_CHECKS),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
    core_missing = [item["name"] for item in checks if item["core"] and not item["ok"]]
    optional_missing = [item["name"] for item in checks if not item["core"] and not item["ok"]]
    available = not core_missing
    if available:
        summary = "Memory v2 core interfaces are importable"
    else:
        summary = (
            "Memory v2 core interfaces are not merged in this worktree; "
            f"pending {core_missing}"
        )
    return {
        "available": available,
        "checks": checks,
        "missing": core_missing,
        "optional_missing": optional_missing,
        "summary": summary,
    }


__all__ = ["CORE_CHECKS", "OPTIONAL_CHECKS", "probe_product_v2"]
