"""Markdown mirror export for Memory v2.

SQLite stays the operational source of truth in v2.0. Every export writes the
same frozen layout that v2.1 will use as its file-first format:

    ~/.hardware-agent/memory/MEMORY.md               (user index)
    ~/.hardware-agent/memory/<topic>.md              (user topics)
    ~/.hardware-agent/projects/<project_id>/memory/MEMORY.md
    ~/.hardware-agent/projects/<project_id>/memory/<topic>.md

Only ``active`` entries enter the main index; pending proposals go to a
separate ``PROPOSALS.md``. Files are written atomically and only when their
content changed. Export failures are logged and never raised: the database and
chat path must not depend on the filesystem mirror.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.db.memory_store import MemoryStore, contains_secret, redact_secrets

logger = logging.getLogger(__name__)

HOME_ENV = "HARDWARE_AGENT_HOME"
DEFAULT_HOME_DIR = ".hardware-agent"
INDEX_FILE = "MEMORY.md"
PROPOSALS_FILE = "PROPOSALS.md"
SCHEMA_VERSION = 2
EXPORT_LIMIT = 200

_PROJECT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TOPIC_FILES = {
    "preference": "preferences.md",
    "fact": "facts.md",
    "constraint": "constraints.md",
    "decision": "decisions.md",
    "procedure": "procedures.md",
}
_TOPIC_ORDER = tuple(_TOPIC_FILES)

# Frozen v2.0 frontmatter contract; keep in sync with MemoryEntry.
FRONTMATTER_FIELDS = (
    "id",
    "tier",
    "status",
    "kind",
    "project_id",
    "principal_id",
    "source",
    "confidence",
    "created_at",
    "updated_at",
    "supersedes_id",
    "evidence",
)

_EXPORTED_TIERS = ("user", "project")


def memory_home(home: str | os.PathLike[str] | None = None) -> Path:
    if home is not None:
        return Path(home).expanduser()
    configured = os.environ.get(HOME_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_HOME_DIR


def user_memory_dir(home: str | os.PathLike[str] | None = None) -> Path:
    return memory_home(home) / "memory"


def project_memory_dir(
    project_id: str, home: str | os.PathLike[str] | None = None
) -> Path:
    if not isinstance(project_id, str) or _PROJECT_ID_RE.fullmatch(project_id) is None:
        raise ValueError("project_id is not safe for a filesystem path")
    return memory_home(home) / "projects" / project_id / "memory"


def _yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sanitize_mapping(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_sanitize_mapping(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_mapping(item) for key, item in value.items()}
    return value


def _safe_content(entry: dict[str, Any]) -> str | None:
    content = entry.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    if contains_secret(content):
        return None
    content = redact_secrets(content) or ""
    content = content.replace("\x00", "")
    if contains_secret(content):
        return None
    return content.strip()


def _frontmatter(entry: dict[str, Any]) -> list[str]:
    lines = ["---"]
    for field in FRONTMATTER_FIELDS:
        value = entry.get(field)
        if field == "evidence":
            value = _sanitize_mapping(value or {})
        lines.append(f"{field}: {_yaml_value(value)}")
    lines.append("---")
    return lines


def _entry_block(entry: dict[str, Any]) -> str | None:
    content = _safe_content(entry)
    if content is None:
        return None
    return "\n".join(["", *_frontmatter(entry), "", content, ""])


def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("created_at") or ""), str(entry.get("id") or ""))


def _topic_text(kind: str, entries: list[dict[str, Any]]) -> str:
    blocks = [
        block
        for block in (_entry_block(entry) for entry in sorted(entries, key=_sort_key))
        if block
    ]
    header = (
        f"# {kind}\n\n"
        "Active entries exported from the SQLite memory store. "
        "SQLite remains the source of truth in Memory v2.0.\n"
    )
    return header + "".join(blocks)


def _index_text(tier: str, entries: list[dict[str, Any]], project_id: str) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        kind = entry.get("kind")
        if kind in _TOPIC_FILES:
            counts[kind] = counts.get(kind, 0) + 1
    lines = [
        "---",
        f"tier: {_yaml_value(tier)}",
        f"project_id: {_yaml_value(project_id)}",
        f"schema_version: {SCHEMA_VERSION}",
        "generated_by: hardware-agent-memory-v2",
        "---",
        "",
        f"# {tier.capitalize()} memory",
        "",
        "Active entries exported from the SQLite memory store. "
        "SQLite remains the source of truth in Memory v2.0.",
        "",
        "| kind | count | file |",
        "| --- | --- | --- |",
    ]
    for kind in _TOPIC_ORDER:
        if not counts.get(kind):
            continue
        lines.append(f"| {kind} | {counts[kind]} | [{_TOPIC_FILES[kind]}]({_TOPIC_FILES[kind]}) |")
    lines.append("")
    return "\n".join(lines)


def _proposals_text(entries: list[dict[str, Any]]) -> str:
    blocks = [
        block
        for block in (_entry_block(entry) for entry in sorted(entries, key=_sort_key))
        if block
    ]
    header = (
        "# Proposed memory\n\n"
        "Pending review; not injected into context until approved.\n"
    )
    return header + "".join(blocks)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        try:
            stream = os.fdopen(handle, "w", encoding="utf-8", newline="\n")
        except Exception:
            os.close(handle)
            raise
        with stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    _atomic_write(path, text)
    return True


def _remove_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.warning("memory mirror could not remove %s", path, exc_info=True)


def _export_group(
    directory: Path,
    *,
    tier: str,
    project_id: str,
    entries: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
) -> dict[str, int]:
    written = 0
    failed = 0
    exportable = [entry for entry in entries if _safe_content(entry) is not None]
    exportable_proposals = [
        entry for entry in proposals if _safe_content(entry) is not None
    ]
    by_kind: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _TOPIC_FILES}
    for entry in exportable:
        kind = entry.get("kind")
        if kind in by_kind:
            by_kind[kind].append(entry)
    for kind, topic_file in _TOPIC_FILES.items():
        path = directory / topic_file
        kind_entries = by_kind[kind]
        if not kind_entries:
            _remove_if_exists(path)
            continue
        try:
            if _write_if_changed(path, _topic_text(kind, kind_entries)):
                written += 1
        except Exception:
            failed += 1
            logger.warning("memory mirror write failed for %s", path, exc_info=True)
    try:
        if _write_if_changed(
            directory / INDEX_FILE, _index_text(tier, exportable, project_id)
        ):
            written += 1
    except Exception:
        failed += 1
        logger.warning("memory mirror write failed for %s", directory / INDEX_FILE, exc_info=True)
    proposals_path = directory / PROPOSALS_FILE
    if exportable_proposals:
        try:
            if _write_if_changed(
                proposals_path, _proposals_text(exportable_proposals)
            ):
                written += 1
        except Exception:
            failed += 1
            logger.warning("memory mirror write failed for %s", proposals_path, exc_info=True)
    else:
        _remove_if_exists(proposals_path)
    return {"written": written, "failed": failed, "entries": len(exportable)}


def _active_entries(store: MemoryStore, **filters: Any) -> list[dict[str, Any]]:
    return store.list(status="active", limit=EXPORT_LIMIT, **filters)


def _proposed_entries(store: MemoryStore, **filters: Any) -> list[dict[str, Any]]:
    return store.list(status="proposed", limit=EXPORT_LIMIT, **filters)


def export_all(
    *,
    store: MemoryStore | None = None,
    home: str | os.PathLike[str] | None = None,
    project_id: str | None = None,
    principal_id: str = "local",
) -> dict[str, Any]:
    """Rewrite the whole mirror; never raises into the caller."""
    result: dict[str, Any] = {"user": None, "projects": {}, "failed": 0}
    try:
        store = store or MemoryStore()
        user_entries = _active_entries(store, tier="user", principal_id=principal_id)
        user_proposals = _proposed_entries(store, tier="user", principal_id=principal_id)
        result["user"] = _export_group(
            user_memory_dir(home),
            tier="user",
            project_id="",
            entries=user_entries,
            proposals=user_proposals,
        )
        if project_id is not None:
            project_ids = [project_id]
        else:
            project_rows = _active_entries(store, tier="project", principal_id=principal_id)
            proposed_rows = _proposed_entries(
                store, tier="project", principal_id=principal_id
            )
            project_ids = sorted(
                {
                    row.get("project_id")
                    for row in [*project_rows, *proposed_rows]
                    if isinstance(row.get("project_id"), str) and row.get("project_id")
                }
            )
        for identifier in project_ids:
            try:
                directory = project_memory_dir(identifier, home)
            except ValueError:
                logger.warning("memory mirror skipped unsafe project_id %r", identifier)
                result["failed"] += 1
                continue
            entries = _active_entries(
                store, tier="project", project_id=identifier, principal_id=principal_id
            )
            proposals = _proposed_entries(
                store, tier="project", project_id=identifier, principal_id=principal_id
            )
            group = _export_group(
                directory,
                tier="project",
                project_id=identifier,
                entries=entries,
                proposals=proposals,
            )
            result["projects"][identifier] = group
            result["failed"] += group["failed"]
        result["failed"] += (result["user"] or {}).get("failed", 0)
    except Exception:
        logger.warning("memory mirror export failed", exc_info=True)
        result["failed"] += 1
    return result


def export_entry(
    entry: dict[str, Any],
    *,
    store: MemoryStore | None = None,
    home: str | os.PathLike[str] | None = None,
    principal_id: str | None = None,
) -> bool:
    """Rewrite the entry's group (tier + project) so siblings stay present."""
    if not isinstance(entry, dict):
        return False
    tier = entry.get("tier")
    if tier not in _EXPORTED_TIERS:
        return False
    project_id = entry.get("project_id") or ""
    principal = principal_id or entry.get("principal_id") or "local"
    try:
        store = store or MemoryStore()
        if tier == "user":
            directory = user_memory_dir(home)
            identifier = ""
        else:
            if not isinstance(project_id, str) or not project_id:
                logger.warning("memory mirror skipped entry without project_id")
                return False
            directory = project_memory_dir(project_id, home)
            identifier = project_id
        entries = _active_entries(
            store, tier=tier, principal_id=principal, **(
                {"project_id": identifier} if tier == "project" else {}
            )
        )
        proposals = _proposed_entries(
            store, tier=tier, principal_id=principal, **(
                {"project_id": identifier} if tier == "project" else {}
            )
        )
        group = _export_group(
            directory,
            tier=tier,
            project_id=identifier,
            entries=entries,
            proposals=proposals,
        )
        return group["failed"] == 0
    except Exception:
        logger.warning("memory mirror entry export failed", exc_info=True)
        return False


__all__ = [
    "EXPORT_LIMIT",
    "FRONTMATTER_FIELDS",
    "HOME_ENV",
    "INDEX_FILE",
    "PROPOSALS_FILE",
    "export_all",
    "export_entry",
    "memory_home",
    "project_memory_dir",
    "user_memory_dir",
]
