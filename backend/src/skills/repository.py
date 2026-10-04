"""Filesystem storage and request-scoped snapshots for Markdown skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
from typing import Any, Iterable, Mapping, Sequence
import uuid

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
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
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
    """Read and manage skill packages below a configurable install root."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *, staging_root: str | os.PathLike[str] | None = None):
        self.root = Path(root) if root is not None else default_skills_root()
        self.staging_root = Path(staging_root) if staging_root is not None else Path(tempfile.gettempdir())

    def list_skills(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        self._assert_root_safe()
        result: list[dict[str, Any]] = []
        for child in sorted(self.root.iterdir(), key=lambda p: p.name.casefold()):
            if self._is_link_or_reparse(child) or not child.is_dir() or not _SKILL_ID_RE.fullmatch(child.name):
                continue
            if child.name.startswith(".skill-stage-"):
                continue
            try:
                result.append(self.get_skill(child.name))
            except SkillNotFoundError:
                continue
            except SkillValidationError as exc:
                result.append(self._invalid_skill_info(child.name, exc))
        return result

    def list_enabled_skills(self) -> list[dict[str, Any]]:
        """Return enabled, loadable skills as metadata only."""
        return [
            self._public_metadata(skill)
            for skill in self.list_skills()
            if skill.get("enabled") and skill.get("compatibility_status") != "review_required"
        ]

    def get_skill(self, skill_id: str) -> dict[str, Any]:
        directory = self._skill_directory(skill_id, must_exist=True)
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
                directory_name=skill_id,
                filename=skill_file.name,
                require_standard=skill_file.name == STANDARD_FILENAME,
            )
            issues.extend(parsed.issues_json)
        except SkillValidationError as exc:
            raw = b""
            parsed = ParsedSkill(
                name=skill_id,
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
        source = self._read_source_metadata(directory, issues)
        imported_hash = source.get("import_hash") if source else None
        locally_modified = bool(source and content_hash and imported_hash != content_hash)
        if source and locally_modified:
            source = {**source, "locally_modified": True}
        enabled = self._is_enabled(directory, issues)
        status_value = parsed.compatibility_status
        if any(issue.get("severity") == "error" for issue in issues):
            status_value = "review_required"
        elif status_value != "review_required" and (issues or file_status == "partial"):
            status_value = "partial"
        if status_value == "review_required":
            enabled = False

        return {
            "id": skill_id,
            "name": parsed.name,
            "description": parsed.description,
            "enabled": enabled,
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
        self._validate_id(skill_id)
        try:
            parse_skill_document(content, directory_name=skill_id, filename=LEGACY_FILENAME)
        except SkillValidationError:
            raise
        with _LOCK:
            self._ensure_root()
            self._assert_no_id_collision(skill_id)
            directory = self.root / skill_id
            directory.mkdir()
            try:
                self._write_new_file(directory / LEGACY_FILENAME, content.encode("utf-8"))
                self._write_new_file(directory / ENABLED_FILENAME, b"")
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
        return self.get_skill(skill_id)

    def update_skill(self, skill_id: str, *, content: str | None = None, enabled: bool | None = None) -> dict[str, Any]:
        current = self.get_skill(skill_id)
        if content is None and enabled is None:
            raise SkillValidationError("PATCH must include content or enabled")
        content_changed = content is not None and content != current["content"]
        if content_changed and enabled is True:
            raise SkillUnavailableError(
                "Save the edited content first, review it, then enable it in a separate request.",
                code="SKILL_REVIEW_REQUIRED",
            )
        if content_changed:
            directory = self._skill_directory(skill_id, must_exist=True)
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
            if source:
                updated_source = {**source, "locally_modified": True}
                self._atomic_replace(
                    directory / IMPORT_METADATA_FILENAME,
                    json.dumps(updated_source, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                )
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
        directory = self._skill_directory(skill_id, must_exist=True)
        self._assert_no_links(directory)
        shutil.rmtree(directory)

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
        if first not in {"references", "assets"} or not resource_extension_supported(normalized):
            raise SkillResourceError(
                "Only text resources under references/ or assets/ are readable; scripts are inert.",
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
    ) -> dict[str, Any]:
        """Install validated raw files atomically and leave the skill disabled."""
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
        try:
            for path, content in normalized_files.items():
                destination = external_stage.joinpath(*path.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_new_file(destination, content)
            (external_stage / IMPORT_METADATA_FILENAME).write_text(
                json.dumps(cleaned_source, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            package_hash = self._hash_directory(external_stage)
            cleaned_source["import_hash"] = package_hash
            (external_stage / IMPORT_METADATA_FILENAME).write_text(
                json.dumps(cleaned_source, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )

            with _LOCK:
                self._ensure_root()
                self._assert_no_id_collision(skill_id)
                local_stage = Path(tempfile.mkdtemp(prefix=".skill-stage-", dir=self.root))
                shutil.rmtree(local_stage)
                shutil.copytree(external_stage, local_stage)
                destination = self.root / skill_id
                try:
                    os.rename(local_stage, destination)
                    local_stage = None
                except FileExistsError as exc:
                    raise SkillConflictError(f"Skill already exists: {skill_id}") from exc
                except OSError as exc:
                    if destination.exists():
                        raise SkillConflictError(f"Skill already exists: {skill_id}") from exc
                    raise
        finally:
            if local_stage is not None and local_stage.exists():
                shutil.rmtree(local_stage, ignore_errors=True)
            if external_stage.exists():
                shutil.rmtree(external_stage, ignore_errors=True)
        return self.get_skill(skill_id)

    def remove_import_if_unchanged(self, skill_id: str, *, install_nonce: str) -> bool:
        """Roll back only this request's unchanged, still-disabled import."""
        with _LOCK:
            try:
                directory = self._skill_directory(skill_id, must_exist=True)
                metadata_path = self._actual_named_child(directory, IMPORT_METADATA_FILENAME)
                if metadata_path is None or not metadata_path.is_file():
                    return False
                self._assert_safe_file(metadata_path, directory)
                self._assert_no_links(directory)
                value = json.loads(self._read_limited(metadata_path, 16 * 1024).decode("utf-8"))
                if not isinstance(value, dict) or value.get("_install_nonce") != install_nonce:
                    return False
                if self._actual_named_child(directory, ENABLED_FILENAME) is not None:
                    return False
                expected_hash = value.get("import_hash")
                if not isinstance(expected_hash, str) or self._hash_directory(directory) != expected_hash:
                    return False
                self._assert_no_links(directory)
                shutil.rmtree(directory)
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

    def _read_source_metadata(self, directory: Path, issues: list[dict[str, str]]) -> dict[str, Any] | None:
        try:
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

    def _is_enabled(self, directory: Path, issues: list[dict[str, str]]) -> bool:
        try:
            flag = self._actual_named_child(directory, ENABLED_FILENAME)
            if flag is None:
                return False
            self._assert_safe_file(flag, directory)
            if not flag.is_file():
                raise SkillValidationError("Skill enabled marker must be a regular file", code="SKILL_ENABLED_MARKER_INVALID")
            return True
        except (SkillValidationError, OSError) as exc:
            code = exc.code if isinstance(exc, SkillValidationError) else "SKILL_ENABLED_MARKER_INVALID"
            issues.append({"code": code, "severity": "error", "message": str(exc)})
            return False

    def _set_enabled(self, directory: Path, enabled: bool) -> None:
        flag = self._actual_named_child(directory, ENABLED_FILENAME)
        if flag is not None:
            self._assert_safe_file(flag, directory)
            if not flag.is_file():
                raise SkillValidationError("Skill enabled marker must be a regular file", code="SKILL_ENABLED_MARKER_INVALID")
        if enabled:
            if flag is None:
                self._write_new_file(directory / ENABLED_FILENAME, b"")
        elif flag is not None:
            flag.unlink()

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

    def _skill_directory(self, skill_id: str, *, must_exist: bool) -> Path:
        self._validate_id(skill_id)
        self._assert_root_safe()
        candidate = self.root / skill_id
        if not candidate.exists():
            if must_exist:
                raise SkillNotFoundError(f"Skill not found: {skill_id}")
            return candidate
        if self._is_link_or_reparse(candidate):
            raise SkillValidationError("Skill directory may not be a symlink or reparse point", code="SKILL_PATH_LINK")
        try:
            real_root = self.root.resolve(strict=True)
            real_candidate = candidate.resolve(strict=True)
            real_candidate.relative_to(real_root)
        except (OSError, ValueError) as exc:
            raise SkillValidationError("Skill directory escapes the configured skill root", code="SKILL_PATH_ESCAPE") from exc
        if not real_candidate.is_dir():
            raise SkillNotFoundError(f"Skill not found: {skill_id}")
        return real_candidate

    def _assert_root_safe(self) -> None:
        if self.root.exists() and self._is_link_or_reparse(self.root):
            raise SkillValidationError("Configured skill root may not be a symlink or reparse point", code="SKILL_ROOT_LINK")

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
        for entry in self.root.iterdir():
            if entry.name.casefold() == folded:
                raise SkillConflictError(f"Skill ID collides with an installed skill: {skill_id}")

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
        if not self.root.exists():
            return
        with _LOCK:
            self._assert_root_safe()
            existing = {entry.name.casefold() for entry in self.root.iterdir()}
            conflict = next((skill_id for skill_id in ids if skill_id.casefold() in existing), None)
            if conflict is not None:
                raise SkillConflictError(f"Skill ID collides with an installed skill: {conflict}")

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
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.fullmatch(skill_id):
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
