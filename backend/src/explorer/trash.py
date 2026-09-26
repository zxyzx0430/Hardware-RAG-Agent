"""Persistent, per-project recycle storage for Explorer deletes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_RESTORE_LOCK = threading.Lock()


class ExplorerTrashError(Exception):
    """Raised when an Explorer recycle operation cannot be completed safely."""


def trash_base_dir() -> Path:
    """Return the persistent app-data directory (overrideable in isolated tests)."""
    configured = os.environ.get("HWRAG_APP_DATA_DIR")
    if configured:
        base = Path(configured)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "HardwareRAGAgent"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "hardware-rag-agent"
    return base / "explorer-trash"


def list_trash(root: Path) -> list[dict[str, Any]]:
    """List recoverable items whose metadata is scoped to *root*."""
    root_dir = _root_bucket(root)
    if not root_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for item_dir in root_dir.iterdir():
        metadata_path = item_dir / "metadata.json"
        if not item_dir.is_dir() or not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if _normalized(Path(metadata["root_path"])) != _normalized(root):
                continue
            items.append(metadata)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("ignoring unreadable Explorer trash record %s", metadata_path)
    items.sort(key=lambda item: item.get("deleted_at", ""), reverse=True)
    return items


def move_to_trash(path: Path, root: Path) -> str:
    """Copy, verify, then detach an item into persistent app-data storage."""
    source = path.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    if source == resolved_root or not _is_relative(source, resolved_root):
        raise ExplorerTrashError("the project root itself cannot be moved to trash")
    if not source.exists():
        raise FileNotFoundError(source)
    _assert_supported_tree(source)

    root_dir = _root_bucket(resolved_root)
    root_dir.mkdir(parents=True, exist_ok=True)
    item_id = uuid.uuid4().hex
    staging = root_dir / f".{item_id}.pending"
    item_dir = root_dir / item_id
    payload = staging / "payload"
    backup = source.with_name(f".{source.name}.hwrag-trash-{item_id}")
    metadata = {
        "item_id": item_id,
        "root_path": str(resolved_root),
        "original_path": str(source),
        "name": source.name,
        "kind": "directory" if source.is_dir() else "file",
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        staging.mkdir(parents=True)
        _copy_into(source, payload)
        if not _paths_match(source, payload):
            raise ExplorerTrashError("the copied item did not match the original")
        (staging / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        # Publish the verified recovery copy before moving the original.
        os.replace(staging, item_dir)
        payload = item_dir / "payload"
        try:
            os.replace(source, backup)
        except OSError:
            shutil.rmtree(item_dir, ignore_errors=True)
            raise
        if not _paths_match(backup, payload):
            try:
                os.replace(backup, source)
                shutil.rmtree(item_dir, ignore_errors=True)
            except OSError:
                logger.exception("item changed during recycle operation and backup could not be restored: %s", backup)
            raise ExplorerTrashError("the item changed while it was being moved; no copy was discarded")
        try:
            _remove_path(backup)
        except OSError:
            # The committed recycle copy is complete; a hidden duplicate is safer
            # than treating a recoverable delete as failed or risking data loss.
            logger.warning("could not remove temporary Explorer delete backup %s", backup)
        return item_id
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def restore_from_trash(
    root: Path,
    item_id: str,
    target_path: Path | None = None,
    *,
    session_id: str,
) -> dict[str, Any]:
    """Restore an item without overwriting a file or directory already present."""
    if not item_id or any(char not in "0123456789abcdef" for char in item_id.lower()):
        raise ExplorerTrashError("invalid trash item id")
    item_id = item_id.lower()
    resolved_root = root.resolve(strict=True)
    item_dir = _root_bucket(resolved_root) / item_id
    metadata_path = item_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError("trash item not found")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if _normalized(Path(metadata.get("root_path", ""))) != _normalized(resolved_root):
        raise ExplorerTrashError("trash item belongs to a different project root")

    original = Path(metadata["original_path"])
    target = (target_path or original).resolve(strict=False)
    if not _is_relative(target, resolved_root) or target == resolved_root:
        raise ExplorerTrashError("restore target is outside the authorized project root")
    from src.explorer.security import validate_path

    target = validate_path(str(target), must_exist=False, session_id=session_id)
    if not target.parent.is_dir():
        raise ExplorerTrashError("restore target parent directory does not exist")
    _assert_no_symlinks(target.parent, resolved_root)

    payload = item_dir / "payload"
    stage = target.with_name(f".{target.name}.{item_id}.restore")
    with _RESTORE_LOCK:
        if target.exists():
            raise FileExistsError(f"restore target already exists: {target}")
        if stage.exists():
            raise ExplorerTrashError("restore staging path already exists")
        try:
            _copy_into(payload, stage)
            if not _paths_match(payload, stage):
                raise ExplorerTrashError("restored copy did not match the recycle item")
            _commit_no_replace(stage, target)
            if not _paths_match(payload, target):
                raise ExplorerTrashError("restored copy changed during recovery; recycle item was retained")
        except Exception:
            if stage.exists():
                _remove_path(stage)
            raise
        try:
            shutil.rmtree(item_dir)
        except OSError as exc:
            # The restored file is complete. Leave the recovery copy if app-data
            # cleanup fails; the next list operation may show a duplicate entry.
            logger.warning("restored %s but could not remove recycle copy: %s", target, exc)
    metadata["restored_path"] = str(target)
    return metadata


def _root_bucket(root: Path) -> Path:
    normalized = _normalized(root.resolve(strict=False))
    key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return trash_base_dir() / key


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_relative(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _assert_supported_tree(path: Path) -> None:
    if path.is_symlink():
        raise ExplorerTrashError("symbolic links cannot be moved to Explorer trash")
    if path.is_file():
        return
    if not path.is_dir():
        raise ExplorerTrashError("special filesystem entries cannot be moved to Explorer trash")
    for child in path.iterdir():
        _assert_supported_tree(child)


def _assert_no_symlinks(path: Path, root: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ExplorerTrashError("restore path traverses a symbolic link")


def _copy_into(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def _commit_no_replace(stage: Path, target: Path) -> None:
    """Publish a restored item without replacing an entry created by another writer."""
    if stage.is_file():
        _copy_file_no_replace(stage, target)
        stage.unlink()
        return
    # mkdir is an atomic exclusive claim: it fails if any entry already owns
    # the requested target name. Children are then copied with exclusive creates.
    target.mkdir()
    _copy_directory_contents_no_replace(stage, target)
    shutil.copystat(stage, target)
    _remove_path(stage)


def _copy_directory_contents_no_replace(source: Path, destination: Path) -> None:
    for child in source.iterdir():
        target_child = destination / child.name
        if child.is_dir():
            target_child.mkdir()
            _copy_directory_contents_no_replace(child, target_child)
            shutil.copystat(child, target_child)
        else:
            _copy_file_no_replace(child, target_child)


def _copy_file_no_replace(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        # Some filesystems do not support hard links. Exclusive creation keeps
        # the same no-overwrite behavior while copying the bytes into place.
        with source.open("rb") as input_file, target.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file)
        shutil.copystat(source, target)


def _paths_match(source: Path, destination: Path) -> bool:
    if source.is_file() != destination.is_file() or source.is_dir() != destination.is_dir():
        return False
    if source.is_file():
        return _file_hash(source) == _file_hash(destination)
    source_children = {child.name: child for child in source.iterdir()}
    destination_children = {child.name: child for child in destination.iterdir()}
    return source_children.keys() == destination_children.keys() and all(
        _paths_match(source_child, destination_children[name])
        for name, source_child in source_children.items()
    )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
