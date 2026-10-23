"""Filesystem storage and request-scoped snapshots for Markdown skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
from typing import Any, Iterable, Literal, Mapping, Sequence
import uuid
import zipfile
import yaml

from src.skills.errors import (
    SkillConflictError,
    SkillNotFoundError,
    SkillResourceError,
    SkillUnavailableError,
    SkillValidationError,
    SkillVersionChangedError,
)
from src.skills.format import (
    LEGACY_FILENAME,
    MAX_RESOURCE_BYTES,
    MAX_SKILL_MARKDOWN_BYTES,
    STANDARD_FILENAME,
    ParsedSkill,
    SkillIssue,
    _is_valid_standard_name,
    diagnose_package_files,
    normalize_relative_path,
    parse_skill_document,
    resource_extension_supported,
    validate_path_set,
)

MAX_PACKAGE_FILE_COUNT = 128
MAX_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_PACKAGE_FILE_BYTES = 2 * 1024 * 1024
MAX_SELECTED_SKILLS = 3
MAX_AUTO_CATALOG_SKILLS = 100
IMPORT_METADATA_FILENAME = ".skill-import.json"
ENABLED_FILENAME = ".enabled"
_LEGACY_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_HOST_FILES = {IMPORT_METADATA_FILENAME.casefold(), ENABLED_FILENAME.casefold()}
_LOCK = threading.RLock()


def default_skills_root() -> Path:
    """Return the existing backend data location without creating it."""
    return Path(__file__).resolve().parents[2] / "data" / "skills"


@dataclass(frozen=True)
class SkillSnapshot:
    """Immutable request-time metadata; body and resources load by matching hash."""

    id: str
    name: str
    description: str
    content_hash: str
    format: str
    compatibility_status: str
    source: tuple[tuple[str, str], ...]
    issues: tuple[tuple[str, str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "content_hash": self.content_hash,
            "format": self.format,
            "compatibility_status": self.compatibility_status,
            "source": dict(self.source) if self.source else None,
            "issues": [
                {"code": code, "severity": severity, "message": message}
                for code, severity, message in self.issues
            ],
        }


class SkillRepository:
    """Read project/user packages with a read-only legacy compatibility root."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        scope: str = "project",
        staging_root: str | os.PathLike[str] | None = None,
        project_root: str | os.PathLike[str] | None = None,
        user_root: str | os.PathLike[str] | None = None,
        legacy_root: str | os.PathLike[str] | None = None,
        claude_project_root: str | os.PathLike[str] | None = None,
        claude_user_root: str | os.PathLike[str] | None = None,
    ):
        if scope not in {"project", "user"}:
            raise SkillValidationError("Skill install scope must be project or user", code="SKILL_SCOPE_INVALID")
        self.scope = scope
        self._explicit_root = root is not None
        self.project_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[3] / ".agents" / "skills"
        self.user_root = Path(user_root) if user_root is not None else Path.home() / ".agents" / "skills"
        self.legacy_root = Path(legacy_root) if legacy_root is not None else default_skills_root()
        if root is not None:
            self.root = Path(root)
            self.roots = ((scope, self.root),)
        else:
            self.root = self.project_root if scope == "project" else self.user_root
            self.roots = (
                ("project", self.project_root),
                ("user", self.user_root),
                ("legacy", self.legacy_root),
            )
        if self._explicit_root:
            default_claude = self.root.parent / ".claude" / "skills"
            self.claude_roots = {scope: Path(claude_project_root or claude_user_root or default_claude)}
        else:
            self.claude_roots = {
                "project": Path(claude_project_root) if claude_project_root is not None else self.project_root.parent.parent / ".claude" / "skills",
                "user": Path(claude_user_root) if claude_user_root is not None else self.user_root.parent.parent / ".claude" / "skills",
            }
        self.staging_root = Path(staging_root) if staging_root is not None else Path(tempfile.gettempdir())

    def for_scope(self, scope: Literal["project", "user"]) -> SkillRepository:
        """Return an install-scoped repository while preserving its configured roots."""
        if scope not in {"project", "user"}:
            raise SkillValidationError(
                "Skill install scope must be project or user",
                code="SKILL_SCOPE_INVALID",
            )
        if self._explicit_root:
            if scope != self.scope:
                raise SkillValidationError(
                    "An explicitly rooted repository cannot switch install scope",
                    code="SKILL_SCOPE_UNAVAILABLE",
                )
            return self
        return SkillRepository(
            scope=scope,
            staging_root=self.staging_root,
            project_root=self.project_root,
            user_root=self.user_root,
            legacy_root=self.legacy_root,
            claude_project_root=self.claude_roots["project"],
            claude_user_root=self.claude_roots["user"],
        )

    def list_skills(self) -> list[dict[str, Any]]:
        self._assert_root_safe()
        ids: list[str] = []
        seen: set[str] = set()
        for _scope, root in self.roots:
            if not root.exists():
                continue
            for child in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
                if self._is_link_or_reparse(child) or not child.is_dir() or child.name.startswith(".skill-stage-"):
                    continue
                try:
                    self._validate_id(child.name)
                except SkillValidationError:
                    continue
                folded = child.name.casefold()
                if folded not in seen:
                    seen.add(folded)
                    ids.append(child.name)
        result: list[dict[str, Any]] = []
        for skill_id in ids:
            try:
                result.append(self.get_skill(skill_id))
            except SkillNotFoundError:
                continue
            except SkillValidationError as exc:
                result.append(self._invalid_skill_info(skill_id, exc))
        return result

    def list_enabled_skills(self) -> list[dict[str, Any]]:
        """Return enabled, loadable skills as metadata only."""
        return [
            self._public_metadata(skill)
            for skill in self.list_skills()
            if skill.get("enabled") and skill.get("compatibility_status") != "review_required"
        ]

    def get_skill(self, skill_id: str, *, scope: str | None = None) -> dict[str, Any]:
        directory = self._skill_directory(skill_id, must_exist=True, scope=scope)
        actual_id = directory.name
        scope = next((name for name, root in self.roots if root.resolve() == directory.parent), self.scope)
        entries = {entry.name: entry for entry in directory.iterdir()}
        standard = entries.get(STANDARD_FILENAME)
        legacy = entries.get(LEGACY_FILENAME)
        manifest_case_variants = [
            entry for entry in directory.iterdir()
            if entry.name.casefold() in {STANDARD_FILENAME.casefold(), LEGACY_FILENAME.casefold()}
            and entry.name not in {STANDARD_FILENAME, LEGACY_FILENAME}
        ]
        issues: list[dict[str, str]] = []
        if standard is not None and legacy is not None:
            issues.append({
                "code": "SKILL_FILE_AMBIGUOUS",
                "severity": "error",
                "message": "Both SKILL.md and skill.md exist; choose one canonical instruction file.",
            })
        if manifest_case_variants:
            issues.append({
                "code": "SKILL_FILE_CASE_MISMATCH",
                "severity": "error",
                "message": "Skill instruction filenames must use the exact name SKILL.md or skill.md.",
            })
        skill_file = standard or legacy
        if skill_file is None:
            if manifest_case_variants:
                raise SkillValidationError(
                    "Skill instruction filename has incorrect casing",
                    code="SKILL_FILE_CASE_MISMATCH",
                )
            raise SkillNotFoundError(f"Skill not found: {skill_id}")
        self._assert_safe_file(skill_file, directory)
        try:
            raw = self._read_limited(skill_file, MAX_SKILL_MARKDOWN_BYTES)
            parsed = parse_skill_document(
                raw,
                directory_name=actual_id,
                filename=skill_file.name,
                require_standard=skill_file.name == STANDARD_FILENAME,
            )
            issues.extend(parsed.issues_json)
        except SkillValidationError as exc:
            raw = b""
            parsed = ParsedSkill(
                name=actual_id,
                description="",
                body="",
                raw_content="",
                metadata={},
                format="standard" if skill_file.name == STANDARD_FILENAME else "legacy",
                compatibility_status="review_required",
                issues=(),
            )
            issues.append({"code": exc.code, "severity": "error", "message": str(exc)})

        package_files, package_issues, content_hash = self._scan_package(directory)
        issues.extend(package_issues)
        file_status, file_issues = diagnose_package_files(
            [item["path"] for item in package_files if item["path"] not in _HOST_FILES]
        )
        issues.extend(issue.as_dict() for issue in file_issues)
        try:
            management_state = self._management_state(directory)
        except (OSError, SkillValidationError, json.JSONDecodeError):
            management_state = None
            issues.append({"code": "SKILL_HOST_STATE_INVALID", "severity": "error", "message": "Managed skill state is unreadable."})
        source = self._read_source_metadata(directory, issues, management_state)
        imported_hash = source.get("import_hash") if source else None
        locally_modified = bool(source and content_hash and imported_hash != content_hash)
        if source and locally_modified:
            source = {**source, "locally_modified": True}
        enabled = self._is_enabled(
            directory,
            issues,
            management_state,
            scope=scope,
            is_standard=standard is not None,
        )
        status_value = parsed.compatibility_status
        if any(issue.get("severity") == "error" for issue in issues):
            status_value = "review_required"
        elif status_value != "review_required" and (issues or file_status == "partial"):
            status_value = "partial"
        if status_value == "review_required":
            enabled = False

        claude_adapter = False
        if management_state and management_state.get("claude_adapter") is True:
            adapter_root = self.claude_roots.get(scope)
            adapter_ok = adapter_root is not None
            adapter_path = adapter_root / actual_id if adapter_root is not None else None
            if adapter_root is not None:
                adapter_ok = not any(self._is_link_or_reparse(parent) for parent in (adapter_root.parent, adapter_root))
            if adapter_ok and adapter_path is not None:
                if management_state.get("adapter_mode") == "symlink" and self._is_link_or_reparse(adapter_path):
                    try:
                        link_target = Path(os.readlink(adapter_path))
                        if not link_target.is_absolute():
                            link_target = adapter_path.parent / link_target
                        claude_adapter = link_target.resolve() == directory.resolve()
                    except OSError:
                        claude_adapter = False
                elif management_state.get("adapter_mode") == "copy" and adapter_path.is_dir() and not self._is_link_or_reparse(adapter_path):
                    try:
                        self._assert_no_links(adapter_path)
                        adapter_hash = management_state.get("adapter_hash") or management_state.get("import_hash")
                        claude_adapter = (
                            isinstance(adapter_hash, str)
                            and adapter_hash == content_hash
                            and self._hash_directory(adapter_path) == adapter_hash
                        )
                    except (OSError, SkillValidationError):
                        claude_adapter = False
            if not claude_adapter:
                issues.append({
                    "code": "SKILL_ADAPTER_OUT_OF_SYNC",
                    "severity": "warning",
                    "message": "The managed Claude adapter could not be verified as matching the canonical package.",
                })
                if status_value != "review_required":
                    status_value = "partial"

        scope_conflicts: list[dict[str, Any]] = []
        for other_scope, other_root in self.roots:
            if other_scope == scope or other_root.resolve() == directory.parent or not other_root.exists():
                continue
            other = other_root / actual_id
            if self._is_link_or_reparse(other) or not other.is_dir():
                continue
            try:
                self._assert_no_links(other)
                _other_files, _other_issues, other_hash = self._scan_package(other)
            except (OSError, SkillValidationError):
                other_hash = None
            scope_conflicts.append({
                "scope": other_scope,
                "install_path": str(other),
                "content_hash": other_hash,
            })

        return {
            "id": actual_id,
            "name": parsed.name,
            "description": parsed.description,
            "enabled": enabled,
            "scope": scope,
            "install_path": str(directory),
            "claude_adapter": claude_adapter,
            "scope_conflicts": scope_conflicts,
            "content": parsed.raw_content,
            "format": parsed.format,
            "compatibility_status": status_value,
            "issues": issues,
            "content_hash": content_hash,
            "source": source,
            "locally_modified": locally_modified,
            "metadata": dict(parsed.metadata),
            "resources": [
                {
                    "path": item["path"],
                    "size": item["size"],
                    "supported": self._is_supported_resource(item["path"]),
                }
                for item in package_files
                if item["path"] not in _HOST_FILES
            ],
        }

    def create_legacy_skill(self, skill_id: str, content: str) -> dict[str, Any]:
        """Create a standard package, wrapping only frontmatter-free editor text."""
        self._validate_id(skill_id)
        text_without_bom = content.removeprefix("\ufeff")
        has_frontmatter = bool(text_without_bom.splitlines()) and text_without_bom.splitlines()[0] == "---"
        if has_frontmatter:
            parsed = parse_skill_document(
                content,
                directory_name=skill_id,
                filename=STANDARD_FILENAME,
                require_standard=True,
            )
            standard_content = content
        else:
            parsed = parse_skill_document(content, directory_name=skill_id, filename=LEGACY_FILENAME)
            if not _is_valid_standard_name(skill_id):
                raise SkillValidationError("New skill name is not valid for the standard format", code="SKILL_NAME_INVALID")
            metadata = {"name": skill_id, "description": "User-created Agent skill."}
            frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
            standard_content = f"---\n{frontmatter}\n---\n{parsed.raw_content}"
            parse_skill_document(
                standard_content,
                directory_name=skill_id,
                filename=STANDARD_FILENAME,
                require_standard=True,
            )
        raw = standard_content.encode("utf-8")
        if len(raw) > MAX_SKILL_MARKDOWN_BYTES:
            raise SkillValidationError("SKILL.md exceeds the 32 KiB instruction limit", code="SKILL_CONTENT_TOO_LARGE", status_code=413)
        with _LOCK:
            self._ensure_root()
            self._assert_no_id_collision(skill_id)
            state_root = self.root.parent / "skills-state"
            state_path = state_root / f"{skill_id}.json"
            if self._is_link_or_reparse(state_root) or self._is_link_or_reparse(state_path) or state_path.exists():
                raise SkillConflictError(f"Managed state already exists for skill: {skill_id}")
            directory = self.root / skill_id
            directory.mkdir()
            managed_nonce = uuid.uuid4().hex
            managed_state = {
                "enabled": True,
                "claude_adapter": False,
                "_managed_nonce": managed_nonce,
                "managed_package_hash": None,
                "adapter_hash": None,
            }
            try:
                self._write_new_file(directory / STANDARD_FILENAME, raw)
                managed_state["managed_package_hash"] = self._hash_directory(directory)
                self._management_state(directory, update=managed_state, replace=False)
            except Exception:
                try:
                    if directory.is_dir() and not self._is_link_or_reparse(directory):
                        self._assert_no_links(directory)
                        expected_hash = managed_state.get("managed_package_hash")
                        if expected_hash is not None and self._hash_directory(directory) == expected_hash:
                            shutil.rmtree(directory)
                except (OSError, SkillValidationError):
                    pass
                if state_path.exists() and not self._is_link_or_reparse(state_path):
                    try:
                        saved = json.loads(self._read_limited(state_path, 16 * 1024).decode("utf-8"))
                        if isinstance(saved, dict) and saved.get("_managed_nonce") == managed_nonce:
                            state_path.unlink()
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillValidationError):
                        pass
                raise
        return self.get_skill(skill_id, scope=self.scope)

    def update_skill(self, skill_id: str, *, content: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        current = self.get_skill(skill_id)
        if current.get("scope") == "legacy":
            raise SkillUnavailableError(
                "Legacy compatibility skills are read-only; create or import a standard package to edit it.",
                code="SKILL_LEGACY_READ_ONLY",
            )
        if content is None and enabled is None:
            raise SkillValidationError("PATCH must include content or enabled")
        content_changed = content is not None and content != current["content"]
        if content_changed and enabled is True:
            raise SkillUnavailableError(
                "Save the edited content first, review it, then enable it in a separate request.",
                code="SKILL_REVIEW_REQUIRED",
            )
        if content_changed:
            directory = self._skill_directory(skill_id, must_exist=True, scope=current.get("scope"))
            state = self._management_state(directory)
            if state and state.get("claude_adapter") is True:
                adapter_root = self.claude_roots.get(current.get("scope"))
                if adapter_root is None:
                    raise SkillConflictError("Managed Claude adapter ownership cannot be verified; skill edit refused.")
                for parent in (adapter_root.parent, adapter_root):
                    if self._is_link_or_reparse(parent):
                        raise SkillConflictError("Managed Claude adapter path is unsafe; skill edit refused.")
                adapter_path = adapter_root / directory.name
                mode = state.get("adapter_mode")
                if mode == "copy":
                    if adapter_path.exists() and not self._is_link_or_reparse(adapter_path):
                        try:
                            self._assert_no_links(adapter_path)
                            expected_adapter_hash = state.get("adapter_hash") or state.get("import_hash")
                            if not isinstance(expected_adapter_hash, str) or self._hash_directory(adapter_path) != expected_adapter_hash:
                                raise SkillConflictError("Managed Claude copy was changed; skill edit refused to preserve it.")
                        except (OSError, SkillValidationError) as exc:
                            raise SkillConflictError("Managed Claude copy cannot be verified; skill edit refused.") from exc
                    raise SkillUnavailableError(
                        "This skill uses a managed Claude copy and requires adapter resynchronization; content edits are refused.",
                        code="SKILL_ADAPTER_RESYNC_REQUIRED",
                    )
                if mode != "symlink" or not self._is_link_or_reparse(adapter_path):
                    raise SkillConflictError("Managed Claude adapter ownership cannot be verified; skill edit refused.")
                try:
                    link_target = Path(os.readlink(adapter_path))
                    if not link_target.is_absolute():
                        link_target = adapter_path.parent / link_target
                    if link_target.resolve() != directory.resolve():
                        raise SkillConflictError("Managed Claude adapter target changed; skill edit refused.")
                except OSError as exc:
                    raise SkillConflictError("Managed Claude adapter ownership cannot be verified; skill edit refused.") from exc
            skill_file = self._instruction_file(directory)
            try:
                parsed = parse_skill_document(
                    content,
                    directory_name=skill_id,
                    filename=skill_file.name,
                    require_standard=skill_file.name == STANDARD_FILENAME,
                )
            except SkillValidationError:
                raise
            if parsed.compatibility_status == "review_required":
                raise SkillValidationError("Edited skill is not loadable", code="SKILL_REVIEW_REQUIRED")
            # Disable before replacing a third-party package so an interrupted
            # edit cannot leave changed instructions silently enabled.
            source = current.get("source")
            if source:
                self._set_enabled(directory, False)
            self._atomic_replace(skill_file, content.encode("utf-8"))
            state = self._management_state(directory)
            if source:
                updated_source = {**source, "locally_modified": True}
                if state is not None and "source" in state:
                    state["source"] = updated_source
                else:
                    self._atomic_replace(
                        directory / IMPORT_METADATA_FILENAME,
                        json.dumps(updated_source, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                    )
            if state is not None:
                current_hash = self._hash_directory(directory)
                state["managed_package_hash"] = current_hash
                if state.get("adapter_mode") == "symlink":
                    state["adapter_hash"] = current_hash
                self._management_state(directory, update=state)
        if enabled is not None:
            self.set_enabled(skill_id, enabled)
        return self.get_skill(skill_id)

    def toggle_enabled(self, skill_id: str) -> bool:
        skill = self.get_skill(skill_id)
        enabled = not skill["enabled"]
        self.set_enabled(skill_id, enabled)
        return enabled

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise SkillValidationError("enabled must be a boolean")
        directory = self._skill_directory(skill_id, must_exist=True)
        skill = self.get_skill(skill_id)
        if enabled and skill["compatibility_status"] == "review_required":
            raise SkillUnavailableError(
                "This skill has compatibility errors and cannot be enabled.",
                code="SKILL_REVIEW_REQUIRED",
            )
        self._set_enabled(directory, enabled)

    def delete_skill(self, skill_id: str) -> None:
        with _LOCK:
            directory = self._skill_directory(skill_id, must_exist=True)
            scope = next((name for name, root in self.roots if root.resolve() == directory.parent), self.scope)
            if scope == "legacy":
                raise SkillUnavailableError(
                    "Legacy compatibility skills are read-only and cannot be deleted here.",
                    code="SKILL_LEGACY_READ_ONLY",
                )
            self._assert_no_links(directory)
            package_hash = self._hash_directory(directory)
            try:
                state = self._management_state(directory)
            except (OSError, SkillValidationError, json.JSONDecodeError) as exc:
                raise SkillConflictError("Managed skill state cannot be verified; deletion refused.") from exc

            state_path = directory.parent.parent / "skills-state" / f"{directory.name}.json"
            adapter_path: Path | None = None
            adapter_mode: str | None = None
            if state is not None:
                nonce = state.get("_managed_nonce") or state.get("_install_nonce")
                expected_hash = state.get("managed_package_hash") or state.get("import_hash")
                if (
                    not isinstance(nonce, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", nonce)
                    or not isinstance(expected_hash, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                    or package_hash != expected_hash
                ):
                    raise SkillConflictError("Managed skill ownership or content hash cannot be verified; deletion refused.")
                if state.get("claude_adapter") is True:
                    adapter_root = self.claude_roots.get(scope)
                    if adapter_root is None:
                        raise SkillConflictError("Managed Claude adapter root is unavailable; deletion refused.")
                    for parent in (adapter_root.parent, adapter_root):
                        if self._is_link_or_reparse(parent):
                            raise SkillConflictError("Managed Claude adapter path is unsafe; deletion refused.")
                    adapter_path = adapter_root / directory.name
                    adapter_mode = state.get("adapter_mode")
                    if adapter_path.exists() or self._is_link_or_reparse(adapter_path):
                        if adapter_mode == "symlink":
                            if not self._is_link_or_reparse(adapter_path):
                                raise SkillConflictError("Claude adapter is no longer the managed symlink; deletion refused.")
                            try:
                                link_target = Path(os.readlink(adapter_path))
                                if not link_target.is_absolute():
                                    link_target = adapter_path.parent / link_target
                                if link_target.resolve() != directory.resolve():
                                    raise SkillConflictError("Claude adapter target changed; deletion refused.")
                            except OSError as exc:
                                raise SkillConflictError("Claude adapter ownership cannot be verified; deletion refused.") from exc
                        elif adapter_mode == "copy":
                            if self._is_link_or_reparse(adapter_path) or not adapter_path.is_dir():
                                raise SkillConflictError("Claude adapter copy is not a regular directory; deletion refused.")
                            try:
                                self._assert_no_links(adapter_path)
                                adapter_hash = state.get("adapter_hash") or state.get("import_hash")
                                if not isinstance(adapter_hash, str) or self._hash_directory(adapter_path) != adapter_hash:
                                    raise SkillConflictError("Claude adapter copy was changed; deletion refused to preserve it.")
                            except (OSError, SkillValidationError) as exc:
                                raise SkillConflictError("Claude adapter copy ownership cannot be verified; deletion refused.") from exc
                        else:
                            raise SkillConflictError("Managed Claude adapter mode is invalid; deletion refused.")

            if state is not None and (self._is_link_or_reparse(state_path) or not state_path.is_file()):
                raise SkillConflictError("Managed state file cannot be safely removed; deletion refused.")
            if adapter_path is not None and (adapter_path.exists() or self._is_link_or_reparse(adapter_path)):
                if adapter_mode == "symlink":
                    adapter_path.unlink()
                else:
                    shutil.rmtree(adapter_path)
            self._assert_no_links(directory)
            shutil.rmtree(directory)
            if state is not None:
                state_path.unlink()

    def snapshot_skills(self, skill_ids: Sequence[str] | None = None) -> tuple[SkillSnapshot, ...]:
        if skill_ids is None:
            selected = [skill["id"] for skill in self.list_enabled_skills()]
            if len(selected) > MAX_AUTO_CATALOG_SKILLS:
                raise SkillValidationError("Too many enabled skills for one request", code="SKILL_CATALOG_TOO_LARGE")
        else:
            if len(skill_ids) > MAX_SELECTED_SKILLS:
                raise SkillValidationError("At most three skills can be selected per request", code="SKILL_SELECTION_TOO_LARGE")
            if len(set(skill_ids)) != len(skill_ids):
                raise SkillValidationError("Duplicate skill IDs are not allowed", code="SKILL_SELECTION_DUPLICATE")
            selected = list(skill_ids)

        snapshots: list[SkillSnapshot] = []
        for skill_id in selected:
            skill = self.get_skill(skill_id)
            if not skill["enabled"]:
                raise SkillUnavailableError(f"Skill is disabled: {skill_id}", code="SKILL_DISABLED")
            if skill["compatibility_status"] == "review_required":
                raise SkillUnavailableError(f"Skill needs compatibility review: {skill_id}", code="SKILL_REVIEW_REQUIRED")
            if not skill["content_hash"]:
                raise SkillUnavailableError(f"Skill package is not hashable: {skill_id}", code="SKILL_INVALID_PACKAGE")
            source = skill.get("source") or {}
            snapshots.append(SkillSnapshot(
                id=skill["id"],
                name=skill["name"],
                description=skill["description"],
                content_hash=skill["content_hash"],
                format=skill["format"],
                compatibility_status=skill["compatibility_status"],
                source=tuple(sorted((str(k), str(v)) for k, v in source.items() if v is not None)),
                issues=tuple(
                    (item["code"], item["severity"], item["message"])
                    for item in skill["issues"]
                ),
            ))
        return tuple(snapshots)

    def load_skill(self, skill_id: str, expected_hash: str | None = None) -> dict[str, Any]:
        skill = self.get_skill(skill_id)
        if not skill["enabled"]:
            raise SkillUnavailableError(f"Skill is disabled: {skill_id}", code="SKILL_DISABLED")
        if skill["compatibility_status"] == "review_required":
            raise SkillUnavailableError(f"Skill needs compatibility review: {skill_id}", code="SKILL_REVIEW_REQUIRED")
        self._verify_expected_hash(skill_id, skill["content_hash"], expected_hash)
        directory = self._skill_directory(skill_id, must_exist=True)
        instruction_file = self._instruction_file(directory)
        self._assert_safe_file(instruction_file, directory)
        raw = self._read_limited(instruction_file, MAX_SKILL_MARKDOWN_BYTES)
        parsed = parse_skill_document(
            raw,
            directory_name=skill_id,
            filename=instruction_file.name,
            require_standard=instruction_file.name == STANDARD_FILENAME,
        )
        return {
            "id": skill_id,
            "name": parsed.name,
            "description": parsed.description,
            "content": parsed.body,
            "metadata": dict(parsed.metadata),
            "format": parsed.format,
            "content_hash": skill["content_hash"],
            "source": skill.get("source"),
            "issues": skill["issues"],
        }

    def read_skill_resource(self, skill_id: str, path: str, expected_hash: str | None = None) -> str:
        skill = self.get_skill(skill_id)
        if not skill["enabled"]:
            raise SkillUnavailableError(f"Skill is disabled: {skill_id}", code="SKILL_DISABLED")
        if skill["compatibility_status"] == "review_required":
            raise SkillUnavailableError(f"Skill needs compatibility review: {skill_id}", code="SKILL_REVIEW_REQUIRED")
        self._verify_expected_hash(skill_id, skill["content_hash"], expected_hash)
        try:
            normalized = normalize_relative_path(path)
        except SkillValidationError as exc:
            raise SkillResourceError(str(exc), code=exc.code) from exc
        first = normalized.split("/", 1)[0].casefold()
        if first not in {"references", "assets", "scripts"} or not resource_extension_supported(normalized):
            raise SkillResourceError(
                "This resource endpoint reads supported text files under references/, assets/, or scripts/ without executing them; script execution must use ordinary tool-permission checks.",
                code="SKILL_RESOURCE_UNSUPPORTED",
            )
        directory = self._skill_directory(skill_id, must_exist=True)
        resource = self._exact_resource_path(directory, normalized)
        self._assert_safe_file(resource, directory)
        raw = self._read_limited(resource, MAX_RESOURCE_BYTES)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SkillResourceError("Skill reference is not UTF-8 text", code="SKILL_RESOURCE_ENCODING") from exc
        if "\x00" in text:
            raise SkillResourceError("Skill resource is not plain text", code="SKILL_RESOURCE_BINARY")
        return text

    def export_skill(
        self,
        skill_id: str,
        *,
        scope: Literal["project", "user", "legacy"] | None = None,
    ) -> bytes:
        """Export one verified skill as a standard directory-rooted ZIP package."""
        skill = self.get_skill(skill_id, scope=scope)
        if skill.get("compatibility_status") == "review_required" or not isinstance(skill.get("content_hash"), str):
            raise SkillUnavailableError("This skill package is invalid and cannot be exported.", code="SKILL_INVALID_PACKAGE")
        directory = self._skill_directory(skill_id, must_exist=True, scope=skill.get("scope"))
        self._assert_no_links(directory)
        files, issues, scanned_hash = self._scan_package(directory)
        if issues or not isinstance(scanned_hash, str) or scanned_hash != skill["content_hash"]:
            raise SkillVersionChangedError("Skill package changed before export.", code="SKILL_VERSION_CHANGED")

        raw_files: dict[str, bytes] = {}
        total_size = 0
        for item in files:
            relative = item["path"]
            path = self._exact_resource_path(directory, relative)
            self._assert_safe_file(path, directory)
            content = self._read_limited(path, MAX_PACKAGE_FILE_BYTES)
            if len(content) != item["size"]:
                raise SkillVersionChangedError("Skill package changed while it was being exported.", code="SKILL_VERSION_CHANGED")
            total_size += len(content)
            if total_size > MAX_PACKAGE_BYTES:
                raise SkillValidationError("Skill package exceeds the 10 MiB export limit", code="SKILL_PACKAGE_TOO_LARGE", status_code=413)
            raw_files[relative] = content
        validate_path_set(raw_files)

        digest = hashlib.sha256()
        for relative in sorted(raw_files, key=lambda item: item.casefold()):
            digest.update(relative.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(raw_files[relative])
            digest.update(b"\x00")
        if digest.hexdigest() != skill["content_hash"] or self._hash_directory(directory) != skill["content_hash"]:
            raise SkillVersionChangedError("Skill package changed while it was being exported.", code="SKILL_VERSION_CHANGED")

        if STANDARD_FILENAME in raw_files:
            export_name = skill["name"]
            if not _is_valid_standard_name(export_name):
                raise SkillValidationError("Standard skill name is invalid", code="SKILL_NAME_INVALID")
            export_files = raw_files
        else:
            legacy = parse_skill_document(
                raw_files[LEGACY_FILENAME],
                directory_name=directory.name,
                filename=LEGACY_FILENAME,
            )
            if _is_valid_standard_name(legacy.name):
                export_name = legacy.name
            else:
                suffix = hashlib.sha256(directory.name.encode("utf-8")).hexdigest()[:12]
                export_name = f"legacy-skill-{suffix}"
            description = legacy.description
            if not description.strip() or len(description) > 1024:
                description = "Exported legacy skill package."
            frontmatter = yaml.safe_dump(
                {"name": export_name, "description": description},
                allow_unicode=True,
                sort_keys=False,
            ).rstrip()
            synthesized = f"---\n{frontmatter}\n---\n{legacy.body}".encode("utf-8")
            if len(synthesized) > MAX_SKILL_MARKDOWN_BYTES:
                raise SkillValidationError("Exported SKILL.md exceeds the 32 KiB instruction limit", code="SKILL_CONTENT_TOO_LARGE", status_code=413)
            export_files = {
                (STANDARD_FILENAME if path == LEGACY_FILENAME else path): content
                for path, content in raw_files.items()
            }
            export_files[STANDARD_FILENAME] = synthesized

        output = io.BytesIO()
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, content in sorted(export_files.items(), key=lambda item: item[0].casefold()):
                archive.writestr(f"{export_name}/{relative}", content)
        return output.getvalue()

    def _exact_resource_path(self, directory: Path, relative_path: str) -> Path:
        """Resolve each path segment by exact spelling on every platform."""
        current = directory
        for segment in relative_path.split("/"):
            if self._is_link_or_reparse(current) or not current.is_dir():
                raise SkillResourceError(
                    "Skill resource path is not a regular package path",
                    code="SKILL_RESOURCE_INVALID",
                )
            try:
                match = next((entry for entry in current.iterdir() if entry.name == segment), None)
            except OSError as exc:
                raise SkillResourceError(
                    "Skill resource path could not be inspected",
                    code="SKILL_RESOURCE_INVALID",
                ) from exc
            if match is None:
                raise SkillResourceError(
                    f"Skill resource was not found with the requested spelling: {relative_path}",
                    code="SKILL_RESOURCE_NOT_FOUND",
                    status_code=404,
                )
            current = match
        return current

    def install_package(
        self,
        files: Mapping[str, bytes],
        source: Mapping[str, str],
        *,
        _install_nonce: str | None = None,
        claude_adapter: bool = False,
    ) -> dict[str, Any]:
        """Install validated package bytes and keep host state outside the package."""
        if not isinstance(claude_adapter, bool):
            raise SkillValidationError("claude_adapter must be a boolean", code="SKILL_ADAPTER_INVALID")
        if not files:
            raise SkillValidationError("Selected skill package is empty", code="SKILL_PACKAGE_EMPTY")
        if len(files) > MAX_PACKAGE_FILE_COUNT:
            raise SkillValidationError("Selected skill exceeds the 128-file limit", code="SKILL_PACKAGE_TOO_LARGE", status_code=413)
        original_paths = list(files.keys())
        paths = validate_path_set(original_paths)
        normalized_files: dict[str, bytes] = {}
        for path, original in zip(paths, original_paths):
            content = files[original]
            if not isinstance(content, bytes):
                raise SkillValidationError(f"Package file is not bytes: {path}", code="SKILL_PACKAGE_INVALID")
            normalized_files[path] = content
        total_size = 0
        for path, content in normalized_files.items():
            if len(content) > MAX_PACKAGE_FILE_BYTES:
                raise SkillValidationError(f"Package file exceeds the 2 MiB limit: {path}", code="SKILL_FILE_TOO_LARGE", status_code=413)
            total_size += len(content)
            if total_size > MAX_PACKAGE_BYTES:
                raise SkillValidationError("Selected skill exceeds the 10 MiB package limit", code="SKILL_PACKAGE_TOO_LARGE", status_code=413)
            if "/" not in path and path.casefold() in _HOST_FILES:
                raise SkillValidationError("Package attempts to overwrite host metadata", code="SKILL_RESERVED_FILE")

        standard = STANDARD_FILENAME in normalized_files
        legacy = LEGACY_FILENAME in normalized_files
        if standard == legacy:
            raise SkillValidationError("Package must contain exactly one SKILL.md or legacy skill.md", code="SKILL_MANIFEST_INVALID")
        manifest = STANDARD_FILENAME if standard else LEGACY_FILENAME
        source_path = source.get("path", "")
        try:
            source_path = normalize_relative_path(source_path, allow_root=True)
        except SkillValidationError:
            raise
        directory_name = PurePosixPath(source_path).name if source_path else None
        if standard:
            parsed = parse_skill_document(
                normalized_files[manifest],
                directory_name=directory_name,
                filename=manifest,
                require_standard=True,
            )
        else:
            parsed = parse_skill_document(
                normalized_files[manifest],
                directory_name=directory_name or str(source.get("repo") or "legacy-skill"),
                filename=manifest,
            )
        skill_id = parsed.name
        self._validate_id(skill_id)
        path_status, package_issues = diagnose_package_files(normalized_files.keys())
        if standard and path_status == "partial":
            compatibility = "partial"
        else:
            compatibility = parsed.compatibility_status
        if compatibility == "review_required":
            raise SkillValidationError("Skill package cannot be imported until its format is corrected", code="SKILL_REVIEW_REQUIRED")

        cleaned_source = {
            "provider": "github",
            "owner": str(source.get("owner", "")),
            "repo": str(source.get("repo", "")),
            "commit_sha": str(source.get("commit_sha", "")),
            "path": source_path,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        install_nonce = _install_nonce or uuid.uuid4().hex
        if not re.fullmatch(r"[0-9a-f]{32}", install_nonce):
            raise SkillValidationError("Install transaction nonce is invalid", code="SKILL_PACKAGE_INVALID")
        cleaned_source["_install_nonce"] = install_nonce
        if not re.fullmatch(r"[0-9a-fA-F]{40}", cleaned_source["commit_sha"]):
            raise SkillValidationError("Source commit_sha must be a full 40-character SHA", code="GITHUB_COMMIT_INVALID")

        self._ensure_external_staging()
        external_stage = Path(tempfile.mkdtemp(prefix="hardware-rag-skill-", dir=self.staging_root))
        local_stage: Path | None = None
        destination = self.root / skill_id
        state_path = self.root.parent / "skills-state" / f"{skill_id}.json"
        adapter_path: Path | None = None
        adapter_stage: Path | None = None
        adapter_mode: str | None = None
        package_hash: str | None = None
        canonical_owned = False
        adapter_owned = False
        try:
            for path, content in normalized_files.items():
                staged_file = external_stage.joinpath(*path.split("/"))
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                self._write_new_file(staged_file, content)
            package_hash = self._hash_directory(external_stage)
            cleaned_source["import_hash"] = package_hash

            with _LOCK:
                self._ensure_root()
                self._assert_no_id_collision(skill_id)
                if self._is_link_or_reparse(state_path.parent) or self._is_link_or_reparse(state_path) or state_path.exists():
                    raise SkillConflictError(f"Managed state already exists for skill: {skill_id}")
                local_stage = Path(tempfile.mkdtemp(prefix=".skill-stage-", dir=self.root))
                shutil.copytree(external_stage, local_stage, dirs_exist_ok=True)
                try:
                    os.rename(local_stage, destination)
                    local_stage = None
                    canonical_owned = True
                except FileExistsError as exc:
                    raise SkillConflictError(f"Skill already exists: {skill_id}") from exc
                except OSError as exc:
                    if destination.exists():
                        raise SkillConflictError(f"Skill already exists: {skill_id}") from exc
                    raise
                if claude_adapter:
                    adapter_root = self.claude_roots.get(self.scope)
                    if adapter_root is None:
                        raise SkillValidationError("Claude adapter root is unavailable for this scope", code="SKILL_ADAPTER_INVALID")
                    for parent in (adapter_root.parent, adapter_root):
                        if self._is_link_or_reparse(parent):
                            raise SkillValidationError("Claude adapter path may not contain symlinks or reparse points", code="SKILL_PATH_LINK")
                        if parent.exists() and not parent.is_dir():
                            raise SkillConflictError("Claude adapter destination is not a directory")
                    adapter_root.mkdir(parents=True, exist_ok=True)
                    adapter_path = adapter_root / skill_id
                    if self._is_link_or_reparse(adapter_path) or adapter_path.exists():
                        raise SkillConflictError(f"Claude skill already exists: {skill_id}")
                    try:
                        adapter_path.symlink_to(destination.resolve(), target_is_directory=True)
                        adapter_mode = "symlink"
                        adapter_owned = True
                    except (OSError, NotImplementedError):
                        if self._is_link_or_reparse(adapter_path) or adapter_path.exists():
                            raise SkillConflictError(f"Claude skill already exists: {skill_id}")
                        adapter_stage = Path(tempfile.mkdtemp(
                            prefix=f".skill-adapter-stage-{install_nonce}-",
                            dir=adapter_root,
                        ))
                        shutil.copytree(destination, adapter_stage, dirs_exist_ok=True)
                        self._assert_no_links(adapter_stage)
                        if self._hash_directory(adapter_stage) != package_hash:
                            raise SkillValidationError(
                                "Claude adapter copy does not match the installed package",
                                code="SKILL_ADAPTER_HASH_MISMATCH",
                            )
                        os.rename(adapter_stage, adapter_path)
                        adapter_stage = None
                        adapter_mode = "copy"
                        adapter_owned = True

                self._management_state(
                    destination,
                    update={
                        "enabled": True,
                        "source": cleaned_source,
                        "_install_nonce": install_nonce,
                        "_managed_nonce": install_nonce,
                        "import_hash": package_hash,
                        "managed_package_hash": package_hash,
                        "claude_adapter": bool(adapter_mode),
                        "adapter_mode": adapter_mode,
                        "adapter_hash": package_hash if adapter_mode else None,
                    },
                    replace=False,
                )
        except Exception:
            if adapter_owned and adapter_path is not None and adapter_mode == "symlink" and self._is_link_or_reparse(adapter_path):
                try:
                    link_target = Path(os.readlink(adapter_path))
                    if not link_target.is_absolute():
                        link_target = adapter_path.parent / link_target
                    if link_target.resolve() == destination.resolve():
                        adapter_path.unlink()
                except OSError:
                    pass
            elif adapter_owned and adapter_path is not None and adapter_mode == "copy" and adapter_path.is_dir() and not self._is_link_or_reparse(adapter_path):
                try:
                    self._assert_no_links(adapter_path)
                    if package_hash is not None and self._hash_directory(adapter_path) == package_hash:
                        shutil.rmtree(adapter_path)
                except (OSError, SkillValidationError):
                    pass
            if canonical_owned and destination.is_dir() and not self._is_link_or_reparse(destination):
                try:
                    self._assert_no_links(destination)
                    if package_hash is not None and self._hash_directory(destination) == package_hash:
                        shutil.rmtree(destination)
                except (OSError, SkillValidationError):
                    pass
            if state_path.is_file() and not self._is_link_or_reparse(state_path):
                try:
                    saved = json.loads(self._read_limited(state_path, 16 * 1024).decode("utf-8"))
                    if (
                        isinstance(saved, dict)
                        and saved.get("_install_nonce") == install_nonce
                        and saved.get("_managed_nonce") == install_nonce
                        and saved.get("managed_package_hash") == package_hash
                    ):
                        state_path.unlink()
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, SkillValidationError):
                    pass
            raise
        finally:
            if adapter_stage is not None and adapter_stage.exists() and not self._is_link_or_reparse(adapter_stage):
                shutil.rmtree(adapter_stage, ignore_errors=True)
            if local_stage is not None and local_stage.exists():
                shutil.rmtree(local_stage, ignore_errors=True)
            if external_stage.exists():
                shutil.rmtree(external_stage, ignore_errors=True)
        return self.get_skill(skill_id, scope=self.scope)

    def remove_import_if_unchanged(self, skill_id: str, *, install_nonce: str) -> bool:
        """Roll back only this request's unchanged, still-owned import."""
        with _LOCK:
            try:
                directory = self._skill_directory(skill_id, must_exist=True, scope=self.scope)
                state = self._management_state(directory)
                if (
                    not isinstance(state, dict)
                    or state.get("_install_nonce") != install_nonce
                    or state.get("_managed_nonce") != install_nonce
                    or state.get("enabled") is not True
                ):
                    return False
                self._assert_no_links(directory)
                if self._actual_named_child(directory, ENABLED_FILENAME) is not None:
                    return False
                expected_hash = state.get("import_hash")
                if not isinstance(expected_hash, str) or self._hash_directory(directory) != expected_hash:
                    return False
                adapter_path: Path | None = None
                adapter_mode = state.get("adapter_mode")
                if state.get("claude_adapter") is True:
                    adapter_root = self.claude_roots.get(self.scope)
                    if adapter_root is None:
                        return False
                    for parent in (adapter_root.parent, adapter_root):
                        if self._is_link_or_reparse(parent):
                            return False
                    adapter_path = adapter_root / directory.name
                    if adapter_path.exists() or self._is_link_or_reparse(adapter_path):
                        if adapter_mode == "symlink":
                            if not self._is_link_or_reparse(adapter_path):
                                return False
                            link_target = Path(os.readlink(adapter_path))
                            if not link_target.is_absolute():
                                link_target = adapter_path.parent / link_target
                            if link_target.resolve() != directory.resolve():
                                return False
                        elif adapter_mode == "copy":
                            if self._is_link_or_reparse(adapter_path) or not adapter_path.is_dir():
                                return False
                            self._assert_no_links(adapter_path)
                            adapter_hash = state.get("adapter_hash") or expected_hash
                            if not isinstance(adapter_hash, str) or self._hash_directory(adapter_path) != adapter_hash:
                                return False
                        else:
                            return False
                state_path = directory.parent.parent / "skills-state" / f"{directory.name}.json"
                if self._is_link_or_reparse(state_path) or not state_path.is_file():
                    return False
                saved = json.loads(self._read_limited(state_path, 16 * 1024).decode("utf-8"))
                if (
                    not isinstance(saved, dict)
                    or saved.get("_install_nonce") != install_nonce
                    or saved.get("_managed_nonce") != install_nonce
                    or saved.get("managed_package_hash") != expected_hash
                ):
                    return False
                if adapter_path is not None and (adapter_path.exists() or self._is_link_or_reparse(adapter_path)):
                    if adapter_mode == "symlink":
                        adapter_path.unlink()
                    else:
                        shutil.rmtree(adapter_path)
                self._assert_no_links(directory)
                shutil.rmtree(directory)
                state_path.unlink()
                return True
            except (OSError, SkillValidationError, SkillNotFoundError, json.JSONDecodeError):
                return False

    def _public_metadata(self, skill: dict[str, Any]) -> dict[str, Any]:
        return {key: skill.get(key) for key in (
            "id", "name", "description", "enabled", "format",
            "compatibility_status", "issues", "content_hash", "source", "locally_modified",
        )}

    def _invalid_skill_info(self, skill_id: str, error: SkillValidationError) -> dict[str, Any]:
        return {
            "id": skill_id,
            "name": skill_id,
            "description": "",
            "enabled": False,
            "content": "",
            "format": "invalid",
            "compatibility_status": "review_required",
            "issues": [{"code": error.code, "severity": "error", "message": str(error)}],
            "content_hash": None,
            "source": None,
            "locally_modified": False,
            "metadata": {},
            "resources": [],
        }

    def _instruction_file(self, directory: Path) -> Path:
        names = {entry.name for entry in directory.iterdir()}
        has_standard = STANDARD_FILENAME in names
        has_legacy = LEGACY_FILENAME in names
        has_wrong_case = any(
            name.casefold() in {STANDARD_FILENAME.casefold(), LEGACY_FILENAME.casefold()}
            and name not in {STANDARD_FILENAME, LEGACY_FILENAME}
            for name in names
        )
        if has_wrong_case:
            raise SkillValidationError(
                "Skill instruction filenames must use the exact name SKILL.md or skill.md.",
                code="SKILL_FILE_CASE_MISMATCH",
            )
        if has_standard and has_legacy:
            raise SkillValidationError(
                "Both SKILL.md and skill.md exist; choose one canonical instruction file.",
                code="SKILL_FILE_AMBIGUOUS",
            )
        if has_standard:
            return directory / STANDARD_FILENAME
        if has_legacy:
            return directory / LEGACY_FILENAME
        raise SkillNotFoundError(f"Skill not found: {directory.name}")

    def _scan_package(self, directory: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], str | None]:
        files: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        try:
            self._assert_no_links(directory)
            for path in sorted(directory.rglob("*"), key=lambda p: p.as_posix().casefold()):
                if path.is_dir():
                    continue
                relative = path.relative_to(directory).as_posix()
                if relative.casefold() in _HOST_FILES:
                    continue
                normalize_relative_path(relative)
                size = path.stat().st_size
                files.append({"path": relative, "size": size})
                if len(files) > MAX_PACKAGE_FILE_COUNT or size > MAX_PACKAGE_FILE_BYTES:
                    raise SkillValidationError("Skill package exceeds file count or per-file limits", code="SKILL_PACKAGE_TOO_LARGE")
            if sum(item["size"] for item in files) > MAX_PACKAGE_BYTES:
                raise SkillValidationError("Skill package exceeds the 10 MiB limit", code="SKILL_PACKAGE_TOO_LARGE")
            validate_path_set(item["path"] for item in files)
            digest = self._hash_files(directory, [item["path"] for item in files])
            return files, issues, digest
        except (OSError, SkillValidationError) as exc:
            code = exc.code if isinstance(exc, SkillValidationError) else "SKILL_PACKAGE_UNSAFE"
            issues.append({"code": code, "severity": "error", "message": str(exc)})
            return files, issues, None

    def _management_state(
        self,
        directory: Path,
        *,
        update: dict[str, Any] | None = None,
        replace: bool = True,
    ) -> dict[str, Any] | None:
        """Read or atomically update host state beside, never inside, a skill package."""
        state_root = directory.parent.parent / "skills-state"
        state_path = state_root / f"{directory.name}.json"
        if self._is_link_or_reparse(state_root.parent) or self._is_link_or_reparse(state_root):
            raise SkillValidationError("Managed state path may not be a symlink or reparse point", code="SKILL_PATH_LINK")
        if update is None:
            if not state_root.exists():
                return None
            if not state_root.is_dir() or self._is_link_or_reparse(state_path):
                raise SkillValidationError("Managed state path is invalid", code="SKILL_HOST_STATE_INVALID")
            if not state_path.exists():
                return None
            if not state_path.is_file():
                raise SkillValidationError("Managed state must be a regular file", code="SKILL_HOST_STATE_INVALID")
            value = json.loads(self._read_limited(state_path, 16 * 1024).decode("utf-8"))
            if not isinstance(value, dict):
                raise SkillValidationError("Managed state must be an object", code="SKILL_HOST_STATE_INVALID")
            if "enabled" in value and not isinstance(value["enabled"], bool):
                raise SkillValidationError("Managed enabled state must be a boolean", code="SKILL_HOST_STATE_INVALID")
            if "claude_adapter" in value and not isinstance(value["claude_adapter"], bool):
                raise SkillValidationError("Managed Claude state must be a boolean", code="SKILL_HOST_STATE_INVALID")
            return value

        if not isinstance(update, dict) or not isinstance(update.get("enabled"), bool):
            raise SkillValidationError("Managed state requires a boolean enabled value", code="SKILL_HOST_STATE_INVALID")
        state_root.mkdir(parents=True, exist_ok=True)
        if self._is_link_or_reparse(state_root) or self._is_link_or_reparse(state_path):
            raise SkillValidationError("Managed state path may not be a symlink or reparse point", code="SKILL_PATH_LINK")
        if state_path.exists() and not replace:
            raise SkillConflictError(f"Managed state already exists for skill: {directory.name}")
        raw = json.dumps(update, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if state_path.exists():
            self._atomic_replace(state_path, raw)
        else:
            self._write_new_file(state_path, raw)
        return update

    def _read_source_metadata(
        self,
        directory: Path,
        issues: list[dict[str, str]],
        management_state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            if management_state is not None and "source" in management_state:
                value = management_state.get("source")
                if value is None:
                    return None
                if not isinstance(value, dict) or value.get("provider") != "github":
                    raise ValueError("invalid managed source metadata")
                return {key: item for key, item in value.items() if key != "_install_nonce"}
            path = self._actual_named_child(directory, IMPORT_METADATA_FILENAME)
            if path is None:
                return None
            self._assert_safe_file(path, directory)
            raw = self._read_limited(path, 16 * 1024)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict) or value.get("provider") != "github":
                raise ValueError("invalid source metadata")
            return {key: item for key, item in value.items() if key != "_install_nonce"}
        except Exception:
            issues.append({"code": "SKILL_SOURCE_METADATA_INVALID", "severity": "error", "message": "Imported source metadata is unreadable."})
            return None

    def _is_enabled(
        self,
        directory: Path,
        issues: list[dict[str, str]],
        management_state: dict[str, Any] | None = None,
        *,
        scope: str,
        is_standard: bool,
    ) -> bool:
        if any(issue.get("code") == "SKILL_HOST_STATE_INVALID" for issue in issues):
            return False
        if management_state is not None:
            enabled = management_state.get("enabled")
            if isinstance(enabled, bool):
                return enabled
            issues.append({"code": "SKILL_HOST_STATE_INVALID", "severity": "error", "message": "Managed enabled state is missing or invalid."})
            return False
        try:
            flag = self._actual_named_child(directory, ENABLED_FILENAME)
            if flag is None:
                return is_standard and scope in {"project", "user"}
            self._assert_safe_file(flag, directory)
            if not flag.is_file():
                raise SkillValidationError("Skill enabled marker must be a regular file", code="SKILL_ENABLED_MARKER_INVALID")
            return True
        except (SkillValidationError, OSError) as exc:
            code = exc.code if isinstance(exc, SkillValidationError) else "SKILL_ENABLED_MARKER_INVALID"
            issues.append({"code": code, "severity": "error", "message": str(exc)})
            return False

    def _set_enabled(self, directory: Path, enabled: bool) -> None:
        self._assert_no_links(directory)
        state = self._management_state(directory)
        if state is None:
            state = {
                "_managed_nonce": uuid.uuid4().hex,
                "managed_package_hash": self._hash_directory(directory),
                "claude_adapter": False,
                "adapter_mode": None,
                "adapter_hash": None,
            }
        else:
            managed_nonce = state.get("_managed_nonce")
            if not isinstance(managed_nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", managed_nonce):
                import_nonce = state.get("_install_nonce")
                import_hash = state.get("import_hash")
                if (
                    not isinstance(import_nonce, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", import_nonce)
                    or not isinstance(import_hash, str)
                    or self._hash_directory(directory) != import_hash
                ):
                    raise SkillConflictError("Managed skill ownership cannot be verified; enabled state was not changed.")
                state["_managed_nonce"] = import_nonce
            if not isinstance(state.get("managed_package_hash"), str):
                current_hash = self._hash_directory(directory)
                if state.get("import_hash") not in {None, current_hash}:
                    raise SkillConflictError("Managed skill content changed; enabled state was not changed.")
                state["managed_package_hash"] = current_hash
        state["enabled"] = enabled
        self._management_state(directory, update=state)

    @staticmethod
    def _actual_named_child(directory: Path, name: str) -> Path | None:
        """Find an exact child name without Windows case-insensitive aliases."""
        matches = list(directory.iterdir())
        exact = next((entry for entry in matches if entry.name == name), None)
        if exact is not None:
            return exact
        if any(entry.name.casefold() == name.casefold() for entry in matches):
            raise SkillValidationError(
                f"Host metadata file must use exact casing: {name}",
                code="SKILL_HOST_FILE_CASE_INVALID",
            )
        return None

    def _skill_directory(
        self,
        skill_id: str,
        *,
        must_exist: bool,
        scope: str | None = None,
    ) -> Path:
        self._validate_id(skill_id)
        self._assert_root_safe()
        roots = self.roots
        if scope is not None:
            roots = tuple(item for item in roots if item[0] == scope)
            if not roots:
                raise SkillNotFoundError(f"Skill not found in scope {scope}: {skill_id}")
        for _root_scope, root in roots:
            if not root.exists():
                continue
            try:
                entries = list(root.iterdir())
            except OSError as exc:
                raise SkillValidationError("Skill root could not be inspected", code="SKILL_ROOT_INVALID") from exc
            candidate = next((entry for entry in entries if entry.name == skill_id), None)
            if candidate is None and any(entry.name.casefold() == skill_id.casefold() for entry in entries):
                raise SkillValidationError(
                    "Skill ID casing does not match the installed package directory",
                    code="SKILL_ID_CASE_MISMATCH",
                )
            if candidate is None:
                continue
            if self._is_link_or_reparse(candidate):
                raise SkillValidationError("Skill directory may not be a symlink or reparse point", code="SKILL_PATH_LINK")
            try:
                real_root = root.resolve(strict=True)
                real_candidate = candidate.resolve(strict=True)
                real_candidate.relative_to(real_root)
            except (OSError, ValueError) as exc:
                raise SkillValidationError("Skill directory escapes the configured skill root", code="SKILL_PATH_ESCAPE") from exc
            if not real_candidate.is_dir():
                raise SkillNotFoundError(f"Skill not found: {skill_id}")
            return real_candidate
        if must_exist:
            raise SkillNotFoundError(f"Skill not found: {skill_id}")
        target_scope = scope or self.scope
        target_root = next((root for name, root in self.roots if name == target_scope), self.root)
        return target_root / skill_id

    def _assert_root_safe(self) -> None:
        checked: set[str] = set()
        for _scope, root in self.roots:
            for path in (root.parent, root):
                key = os.path.normcase(str(path.absolute()))
                if key in checked:
                    continue
                checked.add(key)
                if self._is_link_or_reparse(path):
                    raise SkillValidationError("Configured skill root may not contain symlinks or reparse points", code="SKILL_ROOT_LINK")
                if path == root and path.exists() and not path.is_dir():
                    raise SkillValidationError("Configured skill root is not a directory", code="SKILL_ROOT_INVALID")

    def _ensure_root(self) -> None:
        self._assert_root_safe()
        self.root.mkdir(parents=True, exist_ok=True)
        self._assert_root_safe()

    def _ensure_external_staging(self) -> None:
        try:
            self.staging_root.mkdir(parents=True, exist_ok=True)
            real_staging = self.staging_root.resolve(strict=True)
            project_root = Path(__file__).resolve().parents[3]
            real_staging.relative_to(project_root)
        except ValueError:
            return
        except OSError as exc:
            raise SkillValidationError("Temporary staging directory is unavailable", code="SKILL_STAGING_FAILED") from exc
        raise SkillValidationError("Temporary staging directory must be outside the project", code="SKILL_STAGING_INSIDE_PROJECT")

    def _assert_no_id_collision(self, skill_id: str) -> None:
        folded = skill_id.casefold()
        self._assert_root_safe()
        for scope, root in self.roots:
            if not root.exists():
                continue
            for entry in root.iterdir():
                if entry.name.casefold() == folded:
                    raise SkillConflictError(
                        f"Skill ID collides with an installed skill in {scope} scope: {skill_id}"
                    )

    def assert_available(self, skill_ids: Iterable[str]) -> None:
        """Preflight unique destination IDs without creating the skill root."""
        ids = list(skill_ids)
        folded: set[str] = set()
        for skill_id in ids:
            self._validate_id(skill_id)
            key = skill_id.casefold()
            if key in folded:
                raise SkillConflictError(f"Selected packages contain a skill ID collision: {skill_id}")
            folded.add(key)
        with _LOCK:
            self._assert_root_safe()
            for skill_id in ids:
                self._assert_no_id_collision(skill_id)

    def _assert_safe_file(self, path: Path, package_root: Path) -> None:
        self._assert_no_links(path, package_root=package_root)
        try:
            package_real = package_root.resolve(strict=True)
            real = path.resolve(strict=True)
            real.relative_to(package_real)
        except (OSError, ValueError) as exc:
            raise SkillValidationError("Skill file escapes its package root", code="SKILL_PATH_ESCAPE") from exc
        if not real.is_file():
            raise SkillResourceError("Skill resource is not a regular file", code="SKILL_RESOURCE_INVALID")

    def _assert_no_links(self, root: Path, *, package_root: Path | None = None) -> None:
        base = package_root or root
        if self._is_link_or_reparse(base):
            raise SkillValidationError("Symlinks and reparse points are not supported", code="SKILL_PATH_LINK")
        if package_root is not None:
            try:
                relative = root.relative_to(base)
            except ValueError as exc:
                raise SkillValidationError("Skill path escapes its package root", code="SKILL_PATH_ESCAPE") from exc
            current = base
            for part in relative.parts:
                current = current / part
                if self._is_link_or_reparse(current):
                    raise SkillValidationError("Symlinks and reparse points are not supported", code="SKILL_PATH_LINK")
                normalize_relative_path(current.relative_to(base).as_posix())
            return

        if self._is_link_or_reparse(root):
            raise SkillValidationError("Symlinks and reparse points are not supported", code="SKILL_PATH_LINK")
        if root.is_dir():
            for path in root.rglob("*"):
                if self._is_link_or_reparse(path):
                    raise SkillValidationError("Symlinks and reparse points are not supported", code="SKILL_PATH_LINK")
                normalize_relative_path(path.relative_to(base).as_posix())

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        try:
            info = path.lstat()
        except (FileNotFoundError, OSError):
            return False
        attributes = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)

    @staticmethod
    def _read_limited(path: Path, limit: int) -> bytes:
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
        if len(content) > limit:
            raise SkillValidationError("Skill file exceeds the configured size limit", code="SKILL_FILE_TOO_LARGE", status_code=413)
        return content

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(content)

    @staticmethod
    def _atomic_replace(path: Path, content: bytes) -> None:
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(content)
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _hash_files(directory: Path, relative_paths: Iterable[str]) -> str:
        digest = hashlib.sha256()
        for relative in sorted(relative_paths, key=lambda item: item.casefold()):
            path = directory.joinpath(*relative.split("/"))
            digest.update(relative.encode("utf-8"))
            digest.update(b"\x00")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(64 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\x00")
        return digest.hexdigest()

    def _hash_directory(self, directory: Path) -> str:
        paths = []
        for path in directory.rglob("*"):
            if path.is_dir():
                continue
            relative = path.relative_to(directory).as_posix()
            if relative.casefold() in _HOST_FILES:
                continue
            paths.append(relative)
        return self._hash_files(directory, paths)

    @staticmethod
    def _verify_expected_hash(skill_id: str, current_hash: str | None, expected_hash: str | None) -> None:
        if expected_hash is not None and current_hash != expected_hash:
            raise SkillVersionChangedError(
                f"Skill content changed after request snapshot: {skill_id}",
                code="SKILL_VERSION_CHANGED",
            )

    @staticmethod
    def _is_supported_resource(path: str) -> bool:
        first = path.split("/", 1)[0].casefold()
        return first in {"references", "assets"} and resource_extension_supported(path)

    @staticmethod
    def _validate_id(skill_id: str) -> None:
        if not isinstance(skill_id, str) or not (
            _is_valid_standard_name(skill_id) or _LEGACY_SKILL_ID_RE.fullmatch(skill_id)
        ):
            raise SkillValidationError("Invalid skill id", code="SKILL_INVALID_ID")


_DEFAULT_REPOSITORY = SkillRepository()


def list_enabled_skills(*, root: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    """Stable module-level catalog API used by the Agent integration."""
    repository = SkillRepository(root) if root is not None else _DEFAULT_REPOSITORY
    return repository.list_enabled_skills()


def snapshot_skills(skill_ids: Sequence[str] | None = None, *, root: str | os.PathLike[str] | None = None) -> tuple[SkillSnapshot, ...]:
    """Capture immutable request-time hashes without loading instruction bodies."""
    repository = SkillRepository(root) if root is not None else _DEFAULT_REPOSITORY
    return repository.snapshot_skills(skill_ids)


def load_skill(skill_id: str, expected_hash: str | None = None, *, root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    repository = SkillRepository(root) if root is not None else _DEFAULT_REPOSITORY
    return repository.load_skill(skill_id, expected_hash)


def read_skill_resource(skill_id: str, path: str, expected_hash: str | None = None, *, root: str | os.PathLike[str] | None = None) -> str:
    repository = SkillRepository(root) if root is not None else _DEFAULT_REPOSITORY
    return repository.read_skill_resource(skill_id, path, expected_hash)
