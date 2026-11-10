"""Safe after-the-fact revert for captured ChangeItems.

Only files inside the ChangeSet workspace root are touched. A revert refuses
when the current content no longer matches the captured post-state, so a user
edit is never silently overwritten.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.db import change_store
from src.agent.change_capture import file_digest, read_blob


def _inside_root(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        target = os.path.normcase(str(Path(path).resolve(strict=False)))
        workspace = os.path.normcase(str(Path(root).resolve(strict=False)))
        return os.path.commonpath([target, workspace]) == workspace
    except (OSError, ValueError):
        return False


def _write_before(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def _revert_one(item: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    item_id = item.get("id")
    path = item.get("path")
    if not isinstance(path, str) or not path:
        return {"item_id": item_id, "ok": False, "error": "unobserved item has no file path"}
    if item.get("status") == "reverted":
        return {"item_id": item_id, "ok": True, "already_reverted": True}
    if item.get("status") != "observed":
        return {"item_id": item_id, "ok": False, "error": f"item status is {item.get('status')}"}
    if not _inside_root(path, workspace_root):
        return {"item_id": item_id, "ok": False, "error": "path is outside the workspace root"}

    target = Path(path)
    if target.is_symlink() or (target.exists() and target.is_dir()):
        return {"item_id": item_id, "ok": False, "error": "target is a symlink or directory"}

    after_sha = item.get("after_sha256")
    before_sha = item.get("before_sha256")
    current = file_digest(path)

    if after_sha is not None and current != after_sha:
        change_store.update_change_item(item_id, status="conflict")
        return {
            "item_id": item_id,
            "ok": False,
            "error": "current file changed after the captured change",
            "status": "conflict",
        }

    if before_sha is None:
        # Created file: remove only if it is still the captured post-state.
        if current is None:
            pass
        elif current != after_sha:
            change_store.update_change_item(item_id, status="conflict")
            return {"item_id": item_id, "ok": False, "error": "created file changed"}
        else:
            try:
                target.unlink()
            except OSError as exc:
                return {"item_id": item_id, "ok": False, "error": str(exc)}
    else:
        before = read_blob(item.get("before_blob_ref"))
        if before is None:
            return {"item_id": item_id, "ok": False, "error": "before content is unavailable"}
        if after_sha is None and current is not None:
            return {"item_id": item_id, "ok": False, "error": "deleted file was recreated outside the run"}
        try:
            _write_before(target, before)
        except OSError as exc:
            return {"item_id": item_id, "ok": False, "error": str(exc)}

    change_store.update_change_item(item_id, status="reverted")
    change_store.record_event(
        change_set_id=item["change_set_id"],
        change_item_id=item_id,
        event_type="reverted",
        payload={"item_id": item_id, "path": path},
    )
    return {"item_id": item_id, "ok": True, "path": path}


def revert_change_item(item: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    result = _revert_one(item, workspace_root)
    if result.get("ok"):
        _emit_revert(item["change_set_id"], item.get("id"), result)
    return result


def revert_change_set(change_set: dict[str, Any]) -> dict[str, Any]:
    workspace_root = str(change_set.get("workspace_root") or "")
    items = list(change_set.get("items") or [])
    observed = [item for item in items if item.get("status") == "observed"]
    if not observed:
        return {"ok": True, "results": [], "status": "reverted"}
    results: list[dict[str, Any]] = []
    for item in reversed(observed):
        results.append(_revert_one(item, workspace_root))
    failed = [result for result in results if not result.get("ok")]
    status = "partially_reverted" if failed else "reverted"
    change_store.set_change_set_status(change_set["id"], status)
    change_store.record_event(
        change_set_id=change_set["id"],
        change_item_id=None,
        event_type="reverted" if not failed else "revert_failed",
        payload={"results": results, "status": status},
    )
    _emit_revert(change_set["id"], None, {"results": results, "status": status})
    return {"ok": not failed, "results": results, "status": status}


def _emit_revert(change_set_id: str | None, item_id: str | None, payload: dict[str, Any]) -> None:
    if not change_set_id:
        return
    try:
        from src.agent.change_capture import emit_change_event
        change_set = change_store.get_change_set(change_set_id)
        if change_set:
            emit_change_event(
                change_set["run_id"],
                "change_reverted",
                {"change_set_id": change_set_id, "item_id": item_id, **payload},
            )
    except Exception:
        pass
