"""Parsing and compatibility checks for portable Agent Skills packages."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
import math
from pathlib import PurePosixPath
from typing import Any, Iterable

import yaml

from src.skills.errors import SkillValidationError

STANDARD_FILENAME = "SKILL.md"
LEGACY_FILENAME = "skill.md"
MAX_SKILL_MARKDOWN_BYTES = 32 * 1024
MAX_RESOURCE_BYTES = 32 * 1024
TEXT_RESOURCE_EXTENSIONS = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".toml"}
)

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_WINDOWS_DEVICE_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


@dataclass(frozen=True)
class SkillIssue:
    code: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass(frozen=True)
class ParsedSkill:
    name: str
    description: str
    body: str
    raw_content: str
    metadata: dict[str, Any]
    format: str
    compatibility_status: str
    issues: tuple[SkillIssue, ...]

    @property
    def issues_json(self) -> list[dict[str, str]]:
        return [issue.as_dict() for issue in self.issues]


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def parse_skill_document(
    raw: bytes | str,
    *,
    directory_name: str | None,
    filename: str,
    require_standard: bool = False,
) -> ParsedSkill:
    """Parse a skill file and return normalized metadata plus Markdown body.

    ``SKILL.md`` imports are validated against the standard format. The legacy
    lowercase ``skill.md`` format remains readable and can omit frontmatter.
    """
    if isinstance(raw, bytes):
        if len(raw) > MAX_SKILL_MARKDOWN_BYTES:
            raise SkillValidationError(
                "SKILL.md exceeds the 32 KiB instruction limit",
                code="SKILL_CONTENT_TOO_LARGE",
                status_code=413,
            )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SkillValidationError("Skill instructions must be UTF-8 text", code="SKILL_INVALID_ENCODING") from exc
    else:
        text = raw.removeprefix("\ufeff")
        if len(text.encode("utf-8")) > MAX_SKILL_MARKDOWN_BYTES:
            raise SkillValidationError(
                "SKILL.md exceeds the 32 KiB instruction limit",
                code="SKILL_CONTENT_TOO_LARGE",
                status_code=413,
            )

    if "\x00" in text:
        raise SkillValidationError("Skill instructions contain a NUL byte", code="SKILL_INVALID_TEXT")

    issues: list[SkillIssue] = []
    lines = text.splitlines()
    has_frontmatter = bool(lines) and lines[0] == "---"
    if not has_frontmatter:
        if require_standard or filename == STANDARD_FILENAME:
            raise SkillValidationError("SKILL.md must start with YAML frontmatter", code="SKILL_FRONTMATTER_REQUIRED")
        issues.append(SkillIssue(
            "LEGACY_NO_FRONTMATTER", "warning",
            "Legacy skill.md has no metadata; automatic selection needs a description.",
        ))
        return ParsedSkill(
            name=directory_name or "legacy-skill",
            description="",
            body=text,
            raw_content=text,
            metadata={},
            format="legacy",
            compatibility_status="partial",
            issues=tuple(issues),
        )

    closing = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if closing is None:
        raise SkillValidationError("Skill YAML frontmatter is not closed", code="SKILL_FRONTMATTER_INVALID")
    yaml_text = "\n".join(lines[1:closing])
    try:
        metadata = yaml.load(yaml_text, Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise SkillValidationError("Skill YAML frontmatter is invalid", code="SKILL_FRONTMATTER_INVALID") from exc
    if not isinstance(metadata, dict) or any(not isinstance(k, str) for k in metadata):
        raise SkillValidationError("Skill YAML frontmatter must be a string-keyed mapping", code="SKILL_FRONTMATTER_INVALID")
    _validate_json_metadata(metadata)

    body = "\n".join(lines[closing + 1:]).lstrip("\n")
    name = metadata.get("name")
    description = metadata.get("description")
    if filename == STANDARD_FILENAME or require_standard:
        _validate_standard_metadata(metadata, directory_name)
        name = metadata["name"]
        description = metadata["description"]
        file_format = "standard"
    else:
        file_format = "legacy"
        if not isinstance(name, str) or not name.strip():
            name = directory_name
            issues.append(SkillIssue("LEGACY_NAME_MISSING", "warning", "Legacy skill has no name; its folder name is used."))
        if not isinstance(description, str) or not description.strip():
            description = ""
            issues.append(SkillIssue(
                "LEGACY_DESCRIPTION_MISSING", "warning",
                "Legacy skill has no description and will not be auto-selected.",
            ))
        if isinstance(name, str) and name != directory_name:
            issues.append(SkillIssue("LEGACY_NAME_MISMATCH", "warning", "Legacy name differs from its folder name."))

    _validate_optional_metadata(metadata)
    if metadata.get("allowed-tools"):
        issues.append(SkillIssue(
            "ALLOWED_TOOLS_NOT_GRANTED", "warning",
            "The experimental allowed-tools field is recorded but does not grant tools.",
        ))
    if not body.strip():
        issues.append(SkillIssue("EMPTY_INSTRUCTIONS", "warning", "Skill instructions are empty."))

    if file_format == "legacy" and not any(issue.severity == "error" for issue in issues):
        status = "partial"
    elif issues:
        status = "partial"
    else:
        status = "supported"

    return ParsedSkill(
        name=str(name),
        description=str(description),
        body=body,
        raw_content=text,
        metadata=metadata,
        format=file_format,
        compatibility_status=status,
        issues=tuple(issues),
    )


def _validate_json_metadata(value: Any) -> None:
    """Reject YAML values that cannot be safely serialized as bounded JSON."""
    active: set[int] = set()
    visited = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal visited
        visited += 1
        if visited > 4096 or depth > 20:
            raise SkillValidationError(
                "Skill frontmatter metadata is too deeply nested or too large",
                code="SKILL_METADATA_LIMIT_EXCEEDED",
            )
        if item is None or isinstance(item, (str, bool)):
            return
        if isinstance(item, int) and not isinstance(item, bool):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise SkillValidationError(
                    "Skill metadata numbers must be finite",
                    code="SKILL_METADATA_INVALID",
                )
            return
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in active:
                raise SkillValidationError(
                    "Recursive YAML aliases are not allowed in skill metadata",
                    code="SKILL_METADATA_CYCLE",
                )
            active.add(identity)
            try:
                if isinstance(item, dict):
                    for key, child in item.items():
                        if not isinstance(key, str):
                            raise SkillValidationError(
                                "Skill metadata mapping keys must be strings",
                                code="SKILL_METADATA_INVALID",
                            )
                        visit(child, depth + 1)
                else:
                    for child in item:
                        visit(child, depth + 1)
            finally:
                active.remove(identity)
            return
        raise SkillValidationError(
            "Skill metadata must contain JSON-compatible values",
            code="SKILL_METADATA_INVALID",
        )

    visit(value, 0)


def _validate_standard_metadata(metadata: dict[str, Any], directory_name: str | None) -> None:
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not _NAME_RE.fullmatch(name) or "--" in name:
        raise SkillValidationError("Skill name must be 1-64 lowercase letters, numbers, or single hyphens", code="SKILL_NAME_INVALID")
    if directory_name is not None and name != directory_name:
        raise SkillValidationError("Skill name must match its package directory", code="SKILL_NAME_DIRECTORY_MISMATCH")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        raise SkillValidationError("Skill description must contain 1-1024 characters", code="SKILL_DESCRIPTION_INVALID")


def _validate_optional_metadata(metadata: dict[str, Any]) -> None:
    for key in ("license", "compatibility"):
        if key in metadata and not isinstance(metadata[key], str):
            raise SkillValidationError(f"Skill {key} metadata must be text", code="SKILL_METADATA_INVALID")
    if isinstance(metadata.get("compatibility"), str) and len(metadata["compatibility"]) > 500:
        raise SkillValidationError("Skill compatibility metadata exceeds 500 characters", code="SKILL_METADATA_INVALID")
    if "metadata" in metadata:
        extra = metadata["metadata"]
        if not isinstance(extra, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in extra.items()):
            raise SkillValidationError("Skill metadata must be a string-to-string mapping", code="SKILL_METADATA_INVALID")
    if "allowed-tools" in metadata and not isinstance(metadata["allowed-tools"], str):
        raise SkillValidationError("Skill allowed-tools must be a space-separated string", code="SKILL_METADATA_INVALID")


def normalize_relative_path(path: str, *, allow_root: bool = False) -> str:
    """Validate a repository/package path with Windows and POSIX rules."""
    if path == "" and allow_root:
        return ""
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path or ":" in path:
        raise SkillValidationError("Skill path is invalid", code="SKILL_PATH_INVALID")
    if path.startswith("/") or path.startswith("//") or _DRIVE_RE.match(path):
        raise SkillValidationError("Absolute and drive paths are not allowed", code="SKILL_PATH_INVALID")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SkillValidationError("Path traversal or empty path components are not allowed", code="SKILL_PATH_INVALID")
    for part in parts:
        if part != unicodedata.normalize("NFC", part):
            raise SkillValidationError("Path names must use NFC Unicode normalization", code="SKILL_PATH_NORMALIZATION")
        if part.endswith((" ", ".")) or _WINDOWS_DEVICE_RE.fullmatch(part):
            raise SkillValidationError("Windows reserved path names are not allowed", code="SKILL_PATH_RESERVED")
    for part in parts:
        if any(ord(char) < 32 for char in part) or any(char in '<>"|?*' for char in part):
            raise SkillValidationError(
                "Control characters and Windows-invalid characters are not allowed in skill paths",
                code="SKILL_PATH_INVALID",
            )
    return "/".join(parts)


def validate_path_set(paths: Iterable[str]) -> list[str]:
    """Reject case/Unicode collisions and file-vs-directory prefix conflicts."""
    normalized: list[str] = []
    keys: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for raw in paths:
        path = normalize_relative_path(raw)
        key = unicodedata.normalize("NFKC", path).casefold()
        if key in keys and keys[key] != path:
            raise SkillValidationError("Case-insensitive or Unicode path collision", code="SKILL_PATH_COLLISION")
        segments = key.split("/")
        for i in range(1, len(segments)):
            parent = "/".join(segments[:i])
            if kinds.get(parent) == "file":
                raise SkillValidationError("A package file is also used as a directory", code="SKILL_PATH_COLLISION")
        if kinds.get(key) == "directory":
            raise SkillValidationError("A package path is both a file and directory", code="SKILL_PATH_COLLISION")
        if key in keys:
            raise SkillValidationError("Duplicate package path", code="SKILL_PATH_COLLISION")
        if any(existing.startswith(key + "/") for existing in kinds if kinds[existing] == "file"):
            raise SkillValidationError("A package file is also used as a directory", code="SKILL_PATH_COLLISION")
        keys[key] = path
        kinds[key] = "file"
        for i in range(1, len(segments)):
            kinds.setdefault("/".join(segments[:i]), "directory")
        normalized.append(path)
    return normalized


def resource_extension_supported(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in TEXT_RESOURCE_EXTENSIONS


def diagnose_package_files(paths: Iterable[str]) -> tuple[str, tuple[SkillIssue, ...]]:
    """Report inert scripts and resources that this adapter will not read."""
    issues: list[SkillIssue] = []
    partial = False
    for path in paths:
        try:
            normalized = normalize_relative_path(path)
        except SkillValidationError:
            raise
        lower = normalized.casefold()
        if lower.startswith("scripts/") or PurePosixPath(lower).suffix in {".py", ".sh", ".ps1", ".bat", ".cmd", ".exe"}:
            if not any(issue.code == "SCRIPTS_NOT_EXECUTED" for issue in issues):
                issues.append(SkillIssue(
                    "SCRIPTS_NOT_EXECUTED", "warning",
                    "Scripts are preserved as inert files; this app will not execute them or install dependencies.",
                ))
            partial = True
        first = lower.split("/", 1)[0]
        if first in {"references", "assets"} and not resource_extension_supported(normalized):
            issues.append(SkillIssue(
                "RESOURCE_TYPE_UNSUPPORTED", "warning",
                f"Resource '{normalized}' is preserved but is not a supported text resource.",
            ))
            partial = True
    return ("partial" if partial else "supported"), tuple(issues)
