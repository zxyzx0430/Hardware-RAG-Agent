"""Read-only GitHub discovery and pinned, file-by-file skill import."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
import uuid
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.skills.errors import GitHubImportError, SkillError, SkillValidationError
from src.skills.format import (
    LEGACY_FILENAME,
    MAX_SKILL_MARKDOWN_BYTES,
    STANDARD_FILENAME,
    SkillIssue,
    diagnose_package_files,
    normalize_relative_path,
    parse_skill_document,
    validate_path_set,
)
from src.skills.repository import (
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_FILE_BYTES,
    MAX_PACKAGE_FILE_COUNT,
    SkillRepository,
)

ALLOWED_GITHUB_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
})
MAX_TREE_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_TREE_ENTRIES = 5_000
MAX_CANDIDATES = 100
MAX_SELECTED_CANDIDATES = 20
MAX_TOTAL_RAW_BYTES = 20 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 60.0
PER_REQUEST_TIMEOUT_SECONDS = 10.0
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


class GitHubTransport(Protocol):
    def get(self, url: str, *, max_bytes: int, timeout: float) -> HTTPResult: ...


class GitHubTransportError(Exception):
    def __init__(self, status: int, headers: Mapping[str, str] | None = None):
        super().__init__(f"GitHub returned HTTP {status}")
        self.status = status
        self.headers = dict(headers or {})


def _validate_remote_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise GitHubImportError("GitHub URL is malformed", code="GITHUB_URL_INVALID", status_code=400) from exc
    if (
        parsed.scheme != "https"
        or host not in ALLOWED_GITHUB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
    ):
        raise GitHubImportError("Only HTTPS requests to approved GitHub hosts are allowed", code="GITHUB_HOST_REJECTED", status_code=400)


def _response_socket(response: Any) -> Any | None:
    """Return urllib's underlying socket so each read honors the total deadline."""
    file_pointer = getattr(response, "fp", None)
    raw = getattr(file_pointer, "raw", None)
    sock = getattr(raw, "_sock", None)
    return sock if sock is not None else getattr(file_pointer, "_sock", None)


class _CheckedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibGitHubTransport:
    """Small stdlib transport with bounded reads and per-hop host validation."""

    def get(self, url: str, *, max_bytes: int, timeout: float) -> HTTPResult:
        _validate_remote_url(url)
        request = Request(
            url,
            headers={
                "User-Agent": "Hardware-RAG-Agent-Skills/1.0",
                "Accept": "application/vnd.github+json" if urlsplit(url).hostname == "api.github.com" else "application/octet-stream",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        opener = build_opener(_CheckedRedirectHandler())
        request_deadline = time.monotonic() + timeout
        try:
            with opener.open(request, timeout=timeout) as response:
                _validate_remote_url(response.geturl())
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise GitHubTransportError(413, response.headers)
                body = bytearray()
                response_socket = _response_socket(response)
                while True:
                    remaining = request_deadline - time.monotonic()
                    if remaining <= 0:
                        raise GitHubTransportError(504, response.headers)
                    if response_socket is not None:
                        try:
                            socket_open = response_socket.fileno() >= 0
                        except (AttributeError, OSError, ValueError):
                            socket_open = False
                        if socket_open:
                            try:
                                response_socket.settimeout(remaining)
                            except OSError:
                                # urllib may close the socket after consuming the
                                # declared Content-Length. Keep reading buffered
                                # response data and EOF, but still surface errors
                                # if the descriptor remains open.
                                try:
                                    socket_open = response_socket.fileno() >= 0
                                except (AttributeError, OSError, ValueError):
                                    socket_open = False
                                if socket_open:
                                    raise
                        if not socket_open:
                            response_socket = None
                    chunk = response.read1(min(64 * 1024, max_bytes + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise GitHubTransportError(413, response.headers)
                return HTTPResult(response.status, dict(response.headers.items()), bytes(body))
        except HTTPError as exc:
            raise GitHubTransportError(exc.code, dict(exc.headers.items()) if exc.headers else {}) from exc
        except (TimeoutError, URLError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise GitHubTransportError(504) from exc
            raise GitHubImportError("Could not reach GitHub", code="GITHUB_NETWORK_ERROR", status_code=502) from exc


@dataclass(frozen=True)
class _GitHubLocation:
    owner: str
    repo: str
    tree_tail: tuple[str, ...]


class GitHubSkillImporter:
    """Inspect GitHub tree objects and fetch selected blobs at one full SHA."""

    def __init__(
        self,
        repository: SkillRepository,
        transport: GitHubTransport | None = None,
        *,
        clock=time.monotonic,
    ):
        self.repository = repository
        self.transport = transport or UrllibGitHubTransport()
        self._clock = clock

    def preview(self, url: str, *, ref: str | None = None, path: str | None = None) -> dict[str, Any]:
        deadline = self._clock() + TOTAL_TIMEOUT_SECONDS
        location = self._parse_url(url)
        repository_info = self._json(
            self._api_url(location.owner, location.repo), deadline=deadline, max_bytes=1024 * 1024
        )
        resolved_ref, resolved_path = self._resolve_requested_tree(
            location, str(repository_info.get("default_branch", "")), ref=ref, path=path
        )
        commit_sha, tree_doc = self._commit_tree(location.owner, location.repo, resolved_ref, deadline=deadline)
        entries = self._tree_entries(tree_doc)
        source = {
            "owner": location.owner,
            "repo": location.repo,
            "commit_sha": commit_sha,
            "path": resolved_path,
        }
        candidates = self._discover_candidates(
            location.owner, location.repo, commit_sha, resolved_path, entries, deadline=deadline
        )
        return {"source": source, "candidates": candidates}

    def import_skills(self, source: Mapping[str, Any], paths: Sequence[str]) -> list[dict[str, Any]]:
        if not isinstance(source, Mapping):
            raise SkillValidationError("source must be an object", code="GITHUB_SOURCE_INVALID")
        owner = source.get("owner", "")
        repo = source.get("repo", "")
        commit_sha = source.get("commit_sha", "")
        base_path_value = source.get("path", "")
        if not all(isinstance(value, str) for value in (owner, repo, commit_sha, base_path_value)):
            raise SkillValidationError("GitHub source fields must be text", code="GITHUB_SOURCE_INVALID")
        base_path = normalize_relative_path(base_path_value, allow_root=True)
        self._validate_owner_repo(owner, repo)
        if not _SHA_RE.fullmatch(commit_sha):
            raise SkillValidationError("Import requires a full 40-character commit SHA", code="GITHUB_COMMIT_INVALID")
        if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or not paths:
            raise SkillValidationError("paths must select one or more skill directories", code="GITHUB_PATHS_REQUIRED")
        if len(paths) > MAX_SELECTED_CANDIDATES:
            raise SkillValidationError("At most 20 skills can be imported at once", code="GITHUB_TOO_MANY_SKILLS", status_code=413)
        if any(not isinstance(item, str) for item in paths):
            raise SkillValidationError("Selected skill paths must be text", code="GITHUB_PATH_INVALID")
        selected_paths = [normalize_relative_path(item, allow_root=True) for item in paths]
        if len(set(selected_paths)) != len(selected_paths):
            raise SkillValidationError("Duplicate skill directories were selected", code="GITHUB_PATHS_DUPLICATE")
        for selected in selected_paths:
            if base_path and selected != base_path and not selected.startswith(base_path + "/"):
                raise SkillValidationError("Selected skill is outside the previewed directory", code="GITHUB_PATH_OUTSIDE_PREVIEW")

        deadline = self._clock() + TOTAL_TIMEOUT_SECONDS
        resolved_sha, tree_doc = self._commit_tree(owner, repo, commit_sha, deadline=deadline)
        if resolved_sha.lower() != commit_sha.lower():
            raise GitHubImportError("Commit SHA changed between preview and import", code="GITHUB_COMMIT_CHANGED", status_code=409)
        entries = self._tree_entries(tree_doc)
        downloaded = 0
        packages: list[tuple[str, dict[str, bytes], dict[str, str]]] = []
        package_ids: set[str] = set()

        for selected_path in selected_paths:
            files = self._candidate_files(selected_path, entries)
            if len(files) > MAX_PACKAGE_FILE_COUNT:
                raise SkillValidationError("Selected skill exceeds the 128-file limit", code="SKILL_PACKAGE_TOO_LARGE", status_code=413)
            total = 0
            relative_files: dict[str, bytes] = {}
            for entry in files:
                full_path = entry["path"]
                relative = self._relative_to_skill(full_path, selected_path)
                self._validate_tree_file(entry, relative)
                size = entry.get("size")
                if not isinstance(size, int) or size < 0 or size > MAX_PACKAGE_FILE_BYTES:
                    raise SkillValidationError(f"Package file exceeds the 2 MiB limit: {relative}", code="SKILL_FILE_TOO_LARGE", status_code=413)
                total += size
                if total > MAX_PACKAGE_BYTES:
                    raise SkillValidationError("Selected skill exceeds the 10 MiB package limit", code="SKILL_PACKAGE_TOO_LARGE", status_code=413)
                if downloaded + size > MAX_TOTAL_RAW_BYTES:
                    raise SkillValidationError("Import download exceeds the 20 MiB total limit", code="GITHUB_DOWNLOAD_TOO_LARGE", status_code=413)
                raw_url = self._raw_url(owner, repo, resolved_sha, full_path)
                raw = self._bytes(raw_url, deadline=deadline, max_bytes=min(size, MAX_TOTAL_RAW_BYTES - downloaded))
                downloaded += len(raw)
                if len(raw) != size:
                    raise GitHubImportError(f"Downloaded file size changed: {relative}", code="GITHUB_BLOB_SIZE_MISMATCH", status_code=409)
                self._verify_blob(entry, raw)
                relative_files[relative] = raw

            validate_path_set(relative_files.keys())
            manifest_names = [name for name in (STANDARD_FILENAME, LEGACY_FILENAME) if name in relative_files]
            if len(manifest_names) != 1:
                raise SkillValidationError("Selected skill must contain exactly one SKILL.md or skill.md", code="SKILL_MANIFEST_INVALID")
            manifest = manifest_names[0]
            expected_dir = selected_path.rsplit("/", 1)[-1] if selected_path else None
            if expected_dir is None and manifest == LEGACY_FILENAME:
                expected_dir = repo
            parsed = parse_skill_document(
                relative_files[manifest],
                directory_name=expected_dir,
                filename=manifest,
                require_standard=manifest == STANDARD_FILENAME,
            )
            if parsed.name.casefold() in package_ids:
                raise SkillValidationError("Selected repository contains duplicate skill names", code="SKILL_NAME_COLLISION", status_code=409)
            package_ids.add(parsed.name.casefold())
            cleaned_source = {
                "owner": owner,
                "repo": repo,
                "commit_sha": resolved_sha,
                "path": selected_path,
            }
            packages.append((parsed.name, relative_files, cleaned_source))

        # Preflight all destination names before the first install so ordinary
        # conflicts cannot leave a partially imported selection.
        self.repository.assert_available(package_ids)
        installed: list[dict[str, Any]] = []
        install_nonces: list[str] = []
        try:
            for skill_id, files, package_source in packages:
                nonce = uuid.uuid4().hex
                install_nonces.append(nonce)
                installed.append({"id": skill_id, "_install_nonce": nonce})
                imported = self.repository.install_package(
                    files,
                    package_source,
                    _install_nonce=nonce,
                )
                installed[-1] = imported
        except Exception:
            # The repository publishes each package atomically. If a later
            # package fails, remove only this request's unchanged, disabled
            # packages. A same-commit reimport must not be mistaken for ours.
            for skill, nonce in reversed(list(zip(installed, install_nonces))):
                self.repository.remove_import_if_unchanged(
                    skill["id"],
                    install_nonce=nonce,
                )
            raise
        return installed

    def _discover_candidates(
        self,
        owner: str,
        repo: str,
        commit_sha: str,
        base_path: str,
        entries: list[dict[str, Any]],
        *,
        deadline: float,
    ) -> list[dict[str, Any]]:
        manifest_dirs: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            if entry.get("type") != "blob":
                continue
            path = entry.get("path")
            if not isinstance(path, str) or path.rsplit("/", 1)[-1] not in {STANDARD_FILENAME, LEGACY_FILENAME}:
                continue
            try:
                safe_path = normalize_relative_path(path)
            except SkillValidationError:
                continue
            directory = safe_path.rsplit("/", 1)[0] if "/" in safe_path else ""
            if base_path and directory != base_path and not directory.startswith(base_path + "/"):
                continue
            manifest_dirs.setdefault(directory, []).append(entry)

        if len(manifest_dirs) > MAX_CANDIDATES:
            raise SkillValidationError("Repository contains more than 100 skill candidates", code="GITHUB_TOO_MANY_CANDIDATES", status_code=413)

        candidates: list[dict[str, Any]] = []
        for directory, manifests in sorted(manifest_dirs.items()):
            candidate_issues: list[dict[str, str]] = []
            filenames = [item["path"].rsplit("/", 1)[-1] for item in manifests]
            if len(manifests) != 1:
                candidate_issues.append({
                    "code": "SKILL_FILE_AMBIGUOUS", "severity": "error",
                    "message": "Both SKILL.md and skill.md exist in this directory.",
                })
                candidates.append(self._candidate_record(directory, "", "", "review_required", candidate_issues, 0, 0))
                continue
            manifest_entry = manifests[0]
            manifest_name = filenames[0]
            package_entries = self._candidate_files(directory, entries)
            if len(package_entries) > MAX_PACKAGE_FILE_COUNT:
                candidate_issues.append({
                    "code": "SKILL_PACKAGE_TOO_LARGE", "severity": "error",
                    "message": "Skill contains more than 128 files.",
                })
            total_size = sum(int(item.get("size") or 0) for item in package_entries)
            if total_size > MAX_PACKAGE_BYTES:
                candidate_issues.append({
                    "code": "SKILL_PACKAGE_TOO_LARGE", "severity": "error",
                    "message": "Skill package exceeds 10 MiB.",
                })
            unsafe = self._package_tree_issues(directory, package_entries)
            candidate_issues.extend(unsafe)
            relative_paths = [self._relative_to_skill(item["path"], directory) for item in package_entries]
            try:
                validate_path_set(relative_paths)
                path_status, file_issues = diagnose_package_files(relative_paths)
                candidate_issues.extend(issue.as_dict() for issue in file_issues)
            except SkillValidationError as exc:
                candidate_issues.append({"code": exc.code, "severity": "error", "message": str(exc)})
                path_status = "review_required"

            name = directory.rsplit("/", 1)[-1] if directory else repo
            description = ""
            manifest_size = manifest_entry.get("size")
            if not isinstance(manifest_size, int) or manifest_size > MAX_SKILL_MARKDOWN_BYTES:
                candidate_issues.append({
                    "code": "SKILL_CONTENT_TOO_LARGE", "severity": "error",
                    "message": "Skill instructions exceed the 32 KiB limit.",
                })
            elif not any(issue["severity"] == "error" for issue in candidate_issues):
                raw = self._bytes(
                    self._raw_url(owner, repo, commit_sha, manifest_entry["path"]),
                    deadline=deadline,
                    max_bytes=MAX_SKILL_MARKDOWN_BYTES,
                )
                if len(raw) != manifest_size:
                    candidate_issues.append({
                        "code": "GITHUB_BLOB_SIZE_MISMATCH", "severity": "error",
                        "message": "Downloaded skill instructions differ from the repository tree.",
                    })
                else:
                    self._verify_blob(manifest_entry, raw)
                    try:
                        parsed = parse_skill_document(
                            raw,
                            directory_name=(
                                name if directory else None
                            ) if manifest_name == STANDARD_FILENAME else (name or repo),
                            filename=manifest_name,
                            require_standard=manifest_name == STANDARD_FILENAME,
                        )
                        name = parsed.name
                        description = parsed.description
                        candidate_issues.extend(parsed.issues_json)
                    except SkillValidationError as exc:
                        candidate_issues.append({"code": exc.code, "severity": "error", "message": str(exc)})

            if any(issue["severity"] == "error" for issue in candidate_issues):
                status_value = "review_required"
            elif candidate_issues or path_status == "partial" or manifest_name == LEGACY_FILENAME:
                status_value = "partial"
            else:
                status_value = "supported"
            candidates.append(self._candidate_record(
                directory, name, description, status_value, candidate_issues, len(package_entries), total_size,
                format="legacy" if manifest_name == LEGACY_FILENAME else "standard",
            ))
        return candidates

    def _candidate_files(self, directory: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        prefix = f"{directory}/" if directory else ""
        for entry in entries:
            path = entry.get("path")
            if not isinstance(path, str) or not path.startswith(prefix):
                continue
            if directory and path == directory:
                continue
            relative = path[len(prefix):]
            if not relative:
                continue
            if entry.get("type") == "blob" or entry.get("mode") == "160000":
                result.append(entry)
        return result

    def _package_tree_issues(self, directory: str, entries: list[dict[str, Any]]) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        prefix = f"{directory}/" if directory else ""
        for entry in entries:
            path = entry.get("path", "")
            relative = self._relative_to_skill(path, directory)
            try:
                normalize_relative_path(relative)
            except SkillValidationError as exc:
                issues.append({"code": exc.code, "severity": "error", "message": str(exc)})
                continue
            mode = str(entry.get("mode", ""))
            if mode == "120000":
                issues.append({"code": "SKILL_SYMLINK_REJECTED", "severity": "error", "message": f"Symlink is not allowed: {relative}"})
            elif mode == "160000" or entry.get("type") == "commit":
                issues.append({"code": "SKILL_SUBMODULE_REJECTED", "severity": "error", "message": f"Submodule is not allowed: {relative}"})
            elif entry.get("type") == "blob" and mode not in {"100644", "100755"}:
                issues.append({"code": "SKILL_FILE_MODE_REJECTED", "severity": "error", "message": f"Unsupported file mode: {relative}"})
            if "/" not in relative and relative.casefold() in {".enabled", ".skill-import.json"}:
                issues.append({"code": "SKILL_RESERVED_FILE", "severity": "error", "message": f"Package cannot replace host metadata: {relative}"})
        return issues

    def _validate_tree_file(self, entry: Mapping[str, Any], relative: str) -> None:
        try:
            normalize_relative_path(relative)
        except SkillValidationError:
            raise
        mode = str(entry.get("mode", ""))
        if mode == "120000":
            raise SkillValidationError(f"Symlinks are not allowed: {relative}", code="SKILL_SYMLINK_REJECTED")
        if mode == "160000" or entry.get("type") == "commit":
            raise SkillValidationError(f"Git submodules are not allowed: {relative}", code="SKILL_SUBMODULE_REJECTED")
        if entry.get("type") != "blob" or mode not in {"100644", "100755"}:
            raise SkillValidationError(f"Unsupported Git tree entry: {relative}", code="SKILL_FILE_MODE_REJECTED")
        if "/" not in relative and relative.casefold() in {".enabled", ".skill-import.json"}:
            raise SkillValidationError("Package attempts to overwrite host metadata", code="SKILL_RESERVED_FILE")

    def _commit_tree(self, owner: str, repo: str, ref: str, *, deadline: float) -> tuple[str, dict[str, Any]]:
        self._validate_owner_repo(owner, repo)
        if not ref or "\x00" in ref or ".." in ref:
            raise SkillValidationError("Git ref is invalid", code="GITHUB_REF_INVALID")
        commit_url = f"{self._api_url(owner, repo)}/commits/{quote(ref, safe='')}"
        commit = self._json(commit_url, deadline=deadline, max_bytes=2 * 1024 * 1024)
        sha = str(commit.get("sha", ""))
        tree_sha = str(((commit.get("commit") or {}).get("tree") or {}).get("sha", ""))
        if not _SHA_RE.fullmatch(sha) or not _SHA_RE.fullmatch(tree_sha):
            raise GitHubImportError("GitHub did not return full commit and tree SHAs", code="GITHUB_COMMIT_INVALID")
        tree_url = f"{self._api_url(owner, repo)}/git/trees/{quote(tree_sha, safe='')}?recursive=1"
        tree_doc = self._json(tree_url, deadline=deadline, max_bytes=MAX_TREE_RESPONSE_BYTES)
        if str(tree_doc.get("sha", "")).lower() != tree_sha.lower():
            raise GitHubImportError("Repository tree does not match the selected commit", code="GITHUB_TREE_MISMATCH", status_code=409)
        if tree_doc.get("truncated") is True:
            raise GitHubImportError("GitHub returned a truncated tree; import is refused", code="GITHUB_TREE_TRUNCATED", status_code=413)
        return sha, tree_doc

    @staticmethod
    def _tree_entries(tree_doc: Mapping[str, Any]) -> list[dict[str, Any]]:
        entries = tree_doc.get("tree")
        if not isinstance(entries, list):
            raise GitHubImportError("GitHub tree response has no entry list", code="GITHUB_TREE_INVALID")
        if len(entries) > MAX_TREE_ENTRIES:
            raise SkillValidationError("Repository tree exceeds 5,000 entries", code="GITHUB_TREE_TOO_LARGE", status_code=413)
        return [item for item in entries if isinstance(item, dict)]

    def _resolve_requested_tree(
        self,
        location: _GitHubLocation,
        default_branch: str,
        *,
        ref: str | None,
        path: str | None,
    ) -> tuple[str, str]:
        if not default_branch and not ref:
            raise GitHubImportError("GitHub repository has no default branch", code="GITHUB_DEFAULT_BRANCH_MISSING")
        explicit_path = normalize_relative_path(path, allow_root=True) if path is not None else None
        tail = list(location.tree_tail)
        if ref:
            resolved_ref = str(ref)
            if tail:
                tail_text = "/".join(tail)
                if tail_text == resolved_ref:
                    url_path = ""
                elif tail_text.startswith(resolved_ref + "/"):
                    url_path = tail_text[len(resolved_ref) + 1:]
                else:
                    raise SkillValidationError("URL ref does not match the explicit ref field", code="GITHUB_REF_PATH_CONFLICT")
                if explicit_path is not None and explicit_path != url_path:
                    raise SkillValidationError("URL directory conflicts with the explicit path field", code="GITHUB_REF_PATH_CONFLICT")
                resolved_path = url_path
            else:
                resolved_path = explicit_path or ""
            return self._validate_ref_path(resolved_ref, resolved_path)

        if not tail:
            return self._validate_ref_path(default_branch, explicit_path or "")

        tail_text = "/".join(tail)
        if tail_text == default_branch:
            resolved_ref, url_path = default_branch, ""
        elif tail_text.startswith(default_branch + "/"):
            resolved_ref, url_path = default_branch, tail_text[len(default_branch) + 1:]
        elif len(tail) == 1:
            resolved_ref, url_path = tail[0], ""
        else:
            raise SkillValidationError(
                "This GitHub URL is ambiguous because branch names can contain slashes; provide ref and path explicitly.",
                code="GITHUB_REF_AMBIGUOUS",
            )
        if explicit_path is not None:
            if url_path and explicit_path != url_path:
                raise SkillValidationError("URL directory conflicts with the explicit path field", code="GITHUB_REF_PATH_CONFLICT")
            url_path = explicit_path
        return self._validate_ref_path(resolved_ref, url_path)

    @staticmethod
    def _validate_ref_path(ref: str, path: str) -> tuple[str, str]:
        if not ref or len(ref) > 255 or "\x00" in ref or ".." in ref or ref.startswith("/") or ref.endswith("/"):
            raise SkillValidationError("Git ref is invalid", code="GITHUB_REF_INVALID")
        for component in ref.split("/"):
            if not component or component.startswith(".") or component.endswith(".") or component.endswith(".lock") or "\\" in component or " " in component:
                raise SkillValidationError("Git ref is invalid", code="GITHUB_REF_INVALID")
        return ref, normalize_relative_path(path, allow_root=True)

    def _parse_url(self, url: str) -> _GitHubLocation:
        if not isinstance(url, str):
            raise SkillValidationError("GitHub URL must be text", code="GITHUB_URL_INVALID")
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            port = parsed.port
            raw_segments = unquote(parsed.path, errors="strict").strip("/").split("/")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SkillValidationError("GitHub repository URL is malformed", code="GITHUB_URL_INVALID") from exc
        if parsed.scheme != "https" or host != "github.com" or parsed.username or parsed.password or port not in (None, 443):
            raise SkillValidationError("Only public HTTPS github.com repository URLs are accepted", code="GITHUB_HOST_REJECTED")
        if parsed.query or parsed.fragment or len(raw_segments) < 2:
            raise SkillValidationError("URL must identify a GitHub repository or directory", code="GITHUB_URL_INVALID")
        owner, repo = raw_segments[0], raw_segments[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        self._validate_owner_repo(owner, repo)
        remaining = raw_segments[2:]
        if not remaining:
            return _GitHubLocation(owner, repo, ())
        if remaining[0] != "tree":
            raise SkillValidationError("Only repository and tree-directory URLs are supported", code="GITHUB_URL_INVALID")
        tail = tuple(remaining[1:])
        if not tail or any(not part for part in tail):
            raise SkillValidationError("GitHub tree URL is incomplete", code="GITHUB_URL_INVALID")
        return _GitHubLocation(owner, repo, tail)

    @staticmethod
    def _validate_owner_repo(owner: str, repo: str) -> None:
        if not _OWNER_RE.fullmatch(owner) or owner in {".", ".."} or not _REPO_RE.fullmatch(repo) or repo in {".", ".."}:
            raise SkillValidationError("GitHub owner or repository name is invalid", code="GITHUB_SOURCE_INVALID")

    def _json(self, url: str, *, deadline: float, max_bytes: int) -> dict[str, Any]:
        raw = self._bytes(url, deadline=deadline, max_bytes=max_bytes)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubImportError("GitHub returned invalid JSON", code="GITHUB_RESPONSE_INVALID") from exc
        if not isinstance(value, dict):
            raise GitHubImportError("GitHub response must be a JSON object", code="GITHUB_RESPONSE_INVALID")
        return value

    def _bytes(self, url: str, *, deadline: float, max_bytes: int) -> bytes:
        _validate_remote_url(url)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise GitHubImportError("GitHub request exceeded the 60-second total deadline", code="GITHUB_TIMEOUT", status_code=504)
        try:
            response = self.transport.get(
                url,
                max_bytes=max_bytes,
                timeout=min(PER_REQUEST_TIMEOUT_SECONDS, remaining),
            )
        except GitHubTransportError as exc:
            remaining_header = next((v for k, v in exc.headers.items() if k.lower() == "x-ratelimit-remaining"), None)
            if exc.status == 404:
                raise GitHubImportError("GitHub repository, ref, or path was not found", code="GITHUB_NOT_FOUND", status_code=404) from exc
            if exc.status == 429 or (exc.status == 403 and remaining_header == "0"):
                raise GitHubImportError("GitHub API rate limit exceeded", code="GITHUB_RATE_LIMITED", status_code=429) from exc
            if exc.status == 413:
                raise GitHubImportError("GitHub response exceeded the configured download limit", code="GITHUB_RESPONSE_TOO_LARGE", status_code=413) from exc
            if exc.status == 504:
                raise GitHubImportError("GitHub request timed out", code="GITHUB_TIMEOUT", status_code=504) from exc
            raise GitHubImportError(f"GitHub returned HTTP {exc.status}", code="GITHUB_HTTP_ERROR", status_code=502) from exc
        except GitHubImportError:
            raise
        except Exception as exc:
            raise GitHubImportError("GitHub request failed", code="GITHUB_NETWORK_ERROR", status_code=502) from exc
        if response.status < 200 or response.status >= 300:
            raise GitHubImportError(f"GitHub returned HTTP {response.status}", code="GITHUB_HTTP_ERROR", status_code=502)
        body = response.body
        if not isinstance(body, bytes) or len(body) > max_bytes:
            raise GitHubImportError("GitHub response exceeded the configured download limit", code="GITHUB_RESPONSE_TOO_LARGE", status_code=413)
        if self._clock() > deadline:
            raise GitHubImportError("GitHub request exceeded the 60-second total deadline", code="GITHUB_TIMEOUT", status_code=504)
        return body

    @staticmethod
    def _api_url(owner: str, repo: str) -> str:
        GitHubSkillImporter._validate_owner_repo(owner, repo)
        return f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    @staticmethod
    def _raw_url(owner: str, repo: str, sha: str, path: str) -> str:
        parts = [owner, repo, sha, *path.split("/")]
        return "https://raw.githubusercontent.com/" + "/".join(quote(part, safe="-._~") for part in parts)

    @staticmethod
    def _relative_to_skill(path: str, skill_path: str) -> str:
        if not skill_path:
            return path
        prefix = skill_path + "/"
        if not path.startswith(prefix):
            raise SkillValidationError("GitHub path is outside the selected skill", code="GITHUB_PATH_OUTSIDE_SKILL")
        return path[len(prefix):]

    @staticmethod
    def _verify_blob(entry: Mapping[str, Any], content: bytes) -> None:
        expected = str(entry.get("sha", ""))
        actual = hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\x00" + content).hexdigest()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", expected) or actual.lower() != expected.lower():
            raise GitHubImportError("Downloaded file does not match the pinned Git tree blob", code="GITHUB_BLOB_HASH_MISMATCH", status_code=409)

    @staticmethod
    def _candidate_record(
        path: str,
        name: str,
        description: str,
        compatibility_status: str,
        issues: list[dict[str, str]],
        files_count: int,
        total_bytes: int,
        *,
        format: str = "unknown",
    ) -> dict[str, Any]:
        return {
            "path": path,
            "name": name,
            "description": description,
            "format": format,
            "compatibility_status": compatibility_status,
            "issues": issues,
            "files_count": files_count,
            "total_bytes": total_bytes,
        }
