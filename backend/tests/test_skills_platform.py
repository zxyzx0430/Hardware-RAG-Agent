"""Skills package tests use temporary roots and fixed GitHub responses only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import skill_routes
from src.skills import github_import as github_import_module
from src.skills.errors import GitHubImportError, SkillConflictError, SkillValidationError
from src.skills.format import parse_skill_document, validate_path_set
from src.skills.github_import import (
    GitHubSkillImporter,
    GitHubTransportError,
    HTTPResult,
    UrllibGitHubTransport,
)
from src.skills.repository import SkillRepository


def _skill(name: str, body: str = "Instructions") -> bytes:
    return f"---\nname: {name}\ndescription: Synthetic test skill\n---\n{body}\n".encode()


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\x00" + content).hexdigest()


def _external_staging_root(tmp_path: Path) -> Path:
    """Keep package staging outside the isolated source copy as production requires."""
    project_root = Path(__file__).resolve().parents[3]
    candidate = tmp_path.resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return candidate / "staging"
    while True:
        parent = candidate.parent
        try:
            parent.relative_to(project_root)
        except ValueError:
            return parent / "skill-staging" / tmp_path.name
        candidate = parent


class _FakeGitHub:
    owner = "test-owner"
    repo = "different-repo-name"
    commit = "a" * 40
    tree = "b" * 40

    def __init__(self, files: dict[str, bytes], *, entries_override=None):
        self.files = files
        self.calls: list[str] = []
        self.entries = entries_override or [
            {
                "path": path,
                "type": "blob",
                "mode": "100644",
                "sha": _blob_sha(content),
                "size": len(content),
            }
            for path, content in files.items()
        ]

    def get(self, url: str, *, max_bytes: int, timeout: float) -> HTTPResult:
        self.calls.append(url)
        parsed = urlsplit(url)
        path = parsed.path
        if path == f"/repos/{self.owner}/{self.repo}":
            body = {"default_branch": "main"}
        elif path.startswith(f"/repos/{self.owner}/{self.repo}/commits/"):
            body = {"sha": self.commit, "commit": {"tree": {"sha": self.tree}}}
        elif path == f"/repos/{self.owner}/{self.repo}/git/trees/{self.tree}":
            body = {"sha": self.tree, "truncated": False, "tree": self.entries}
        elif parsed.hostname == "raw.githubusercontent.com":
            segments = path.lstrip("/").split("/")
            content_path = "/".join(segments[3:])
            return HTTPResult(200, {}, self.files[content_path])
        else:
            raise AssertionError(f"Unexpected fake GitHub URL: {url}")
        raw = json.dumps(body).encode()
        assert len(raw) <= max_bytes
        return HTTPResult(200, {}, raw)


def test_recursive_and_non_json_metadata_are_rejected_and_listable(tmp_path):
    recursive = (
        "---\nname: cyclic-skill\ndescription: Circular metadata\n"
        "extra: &loop [*loop]\n---\nbody\n"
    )
    with pytest.raises(SkillValidationError) as error:
        parse_skill_document(recursive, directory_name="cyclic-skill", filename="SKILL.md", require_standard=True)
    assert error.value.code == "SKILL_METADATA_CYCLE"

    date_value = "---\nname: date-skill\ndescription: Date metadata\nextra: 2026-10-03\n---\nbody\n"
    with pytest.raises(SkillValidationError, match="JSON-compatible"):
        parse_skill_document(date_value, directory_name="date-skill", filename="SKILL.md", require_standard=True)

    root = tmp_path / "skills"
    package = root / "cyclic-skill"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(recursive, encoding="utf-8")
    (package / ".enabled").write_text("", encoding="utf-8")
    listed = SkillRepository(root).list_skills()[0]
    assert listed["compatibility_status"] == "review_required"
    assert any(issue["code"] == "SKILL_METADATA_CYCLE" for issue in listed["issues"])
    json.dumps(listed)


@pytest.mark.parametrize(
    "paths",
    [
        ["folder/note.md", "folder"],
        ["folder", "folder/note.md"],
        ["references/A.md", "references/Ａ.md"],
    ],
)
def test_package_path_collisions_are_rejected(paths):
    with pytest.raises(SkillValidationError):
        validate_path_set(paths)


def test_install_accepts_root_skill_name_different_from_repository(tmp_path):
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    installed = repository.install_package(
        {"SKILL.md": _skill("custom-skill")},
        {"owner": "test-owner", "repo": "different-repo-name", "commit_sha": "a" * 40, "path": ""},
    )
    assert installed["id"] == "custom-skill"
    assert installed["enabled"] is False
    assert installed["source"]["repo"] == "different-repo-name"


def test_non_bytes_package_content_is_rejected_before_conversion(tmp_path):
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    with pytest.raises(SkillValidationError) as error:
        repository.install_package(
            {"SKILL.md": bytearray(_skill("custom-skill"))},
            {"owner": "test-owner", "repo": "repo", "commit_sha": "a" * 40, "path": ""},
        )
    assert error.value.code == "SKILL_PACKAGE_INVALID"


def test_rollback_will_not_delete_a_user_enabled_or_modified_import(tmp_path):
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    nonce = "1" * 32
    installed = repository.install_package(
        {"SKILL.md": _skill("custom-skill")},
        {"owner": "test-owner", "repo": "repo", "commit_sha": "a" * 40, "path": ""},
        _install_nonce=nonce,
    )
    directory = tmp_path / "skills" / installed["id"]
    (directory / ".enabled").write_text("", encoding="utf-8")
    (directory / "SKILL.md").write_bytes(_skill("custom-skill", "User changed this"))

    assert repository.remove_import_if_unchanged(installed["id"], install_nonce=nonce) is False
    assert (directory / "SKILL.md").read_text(encoding="utf-8").endswith("User changed this\n")
    assert (directory / ".enabled").exists()


def test_import_uses_pinned_blob_and_leaves_root_skill_disabled(tmp_path):
    raw = _skill("custom-skill", "Read-only instructions")
    transport = _FakeGitHub({"SKILL.md": raw})
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)

    preview = importer.preview(f"https://github.com/{transport.owner}/{transport.repo}")
    candidate = preview["candidates"][0]
    assert candidate["path"] == ""
    assert candidate["name"] == "custom-skill"
    assert candidate["compatibility_status"] == "supported"

    imported = importer.import_skills(preview["source"], [""])
    assert imported[0]["id"] == "custom-skill"
    assert imported[0]["enabled"] is False
    assert "Read-only instructions" not in transport.calls[0]
    assert all(transport.commit in call for call in transport.calls if "raw.githubusercontent.com" in call)


@pytest.mark.parametrize("user_changes_first_package,expected_exists", [(False, False), (True, True)])
def test_multi_import_rollback_only_removes_unchanged_packages(
    tmp_path, user_changes_first_package, expected_exists
):
    class FailSecondInstallRepository(SkillRepository):
        install_count = 0

        def install_package(self, files, source, *, _install_nonce=None):
            self.install_count += 1
            if self.install_count == 2:
                first = self.root / "first-skill"
                if user_changes_first_package:
                    (first / ".enabled").write_text("", encoding="utf-8")
                raise SkillConflictError("synthetic second-package failure")
            return super().install_package(
                files,
                source,
                _install_nonce=_install_nonce,
            )

    files = {
        "skills/first-skill/SKILL.md": _skill("first-skill"),
        "skills/second-skill/SKILL.md": _skill("second-skill"),
    }
    transport = _FakeGitHub(files)
    repository = FailSecondInstallRepository(
        tmp_path / "skills",
        staging_root=_external_staging_root(tmp_path),
    )
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(SkillConflictError, match="second-package"):
        importer.import_skills(source, ["skills/first-skill", "skills/second-skill"])

    first_exists = (tmp_path / "skills" / "first-skill").exists()
    assert first_exists is expected_exists
    assert not (tmp_path / "skills" / "second-skill").exists()
    if user_changes_first_package:
        assert (tmp_path / "skills" / "first-skill" / ".enabled").exists()


def test_github_import_rejects_total_deadline_after_controlled_transport(tmp_path):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    class SlowTransport:
        def __init__(self, clock):
            self.clock = clock

        def get(self, url, *, max_bytes, timeout):
            self.clock.now += 61.0
            return HTTPResult(200, {}, b'{"default_branch":"main"}')

    clock = Clock()
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, SlowTransport(clock), clock=clock)
    with pytest.raises(GitHubImportError) as error:
        importer.preview("https://github.com/test-owner/repo")
    assert error.value.code == "GITHUB_TIMEOUT"
    assert error.value.status_code == 504


def test_urllib_transport_reads_eof_after_response_socket_closes(monkeypatch):
    body = b"complete response body"
    url = "https://api.github.com/repos/owner/repo"

    class SocketLike:
        closed = False
        timeout_calls = 0

        def fileno(self):
            return -1 if self.closed else 42

        def settimeout(self, timeout):
            if self.closed:
                raise OSError(10038, "An operation was attempted on something that is not a socket")
            self.timeout_calls += 1

    response_socket = SocketLike()

    class Raw:
        _sock = response_socket

    class FilePointer:
        raw = Raw()

    class Response:
        status = 200
        headers = {"Content-Length": str(len(body))}
        fp = FilePointer()
        read_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return url

        def read1(self, _max_bytes):
            self.read_calls += 1
            if self.read_calls == 1:
                response_socket.closed = True
                return body
            return b""

    response = Response()

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout == 5
            return response

    monkeypatch.setattr(github_import_module, "build_opener", lambda _handler: Opener())

    result = UrllibGitHubTransport().get(url, max_bytes=len(body), timeout=5)

    assert result.body == body
    assert response.read_calls == 2
    assert response_socket.timeout_calls == 1


def test_urllib_transport_checks_deadline_after_response_socket_closes(monkeypatch):
    body = b"complete response body"
    url = "https://api.github.com/repos/owner/repo"

    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(github_import_module, "time", clock)

    class SocketLike:
        closed = False

        def fileno(self):
            return -1 if self.closed else 42

        def settimeout(self, _timeout):
            assert not self.closed

    response_socket = SocketLike()

    class Raw:
        _sock = response_socket

    class FilePointer:
        raw = Raw()

    class Response:
        status = 200
        headers = {"Content-Length": str(len(body))}
        fp = FilePointer()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return url

        def read1(self, _max_bytes):
            response_socket.closed = True
            clock.now = 6.0
            return body

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout == 5
            return Response()

    monkeypatch.setattr(github_import_module, "build_opener", lambda _handler: Opener())

    with pytest.raises(GitHubTransportError) as error:
        UrllibGitHubTransport().get(url, max_bytes=len(body), timeout=5)

    assert error.value.status == 504


@pytest.mark.parametrize(
    "url,expected_code",
    [
        ("http://github.com/owner/repo", "GITHUB_HOST_REJECTED"),
        ("https://127.0.0.1/owner/repo", "GITHUB_HOST_REJECTED"),
        ("https://user:secret@github.com/owner/repo", "GITHUB_HOST_REJECTED"),
        ("https://github.com.evil.example/owner/repo", "GITHUB_HOST_REJECTED"),
        ("https://github.com/owner/repo?token=secret", "GITHUB_URL_INVALID"),
    ],
)
def test_github_preview_rejects_nonpublic_or_credential_urls(tmp_path, url, expected_code):
    transport = _FakeGitHub({})
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)

    with pytest.raises(SkillValidationError) as error:
        importer.preview(url)

    assert error.value.code == expected_code
    assert transport.calls == []


def test_github_preview_rejects_truncated_tree(tmp_path):
    class TruncatedTree(_FakeGitHub):
        def get(self, url: str, *, max_bytes: int, timeout: float) -> HTTPResult:
            if urlsplit(url).path.endswith(f"/git/trees/{self.tree}"):
                body = {"sha": self.tree, "truncated": True, "tree": []}
                return HTTPResult(200, {}, json.dumps(body).encode())
            return super().get(url, max_bytes=max_bytes, timeout=timeout)

    transport = TruncatedTree({})
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)

    with pytest.raises(GitHubImportError) as error:
        importer.preview(f"https://github.com/{transport.owner}/{transport.repo}")

    assert error.value.code == "GITHUB_TREE_TRUNCATED"
    assert error.value.status_code == 413


@pytest.mark.parametrize(
    "entry_type,mode,expected_code",
    [
        ("blob", "120000", "SKILL_SYMLINK_REJECTED"),
        ("commit", "160000", "SKILL_SUBMODULE_REJECTED"),
    ],
)
def test_github_import_rejects_symlink_and_submodule_entries(
    tmp_path, entry_type, mode, expected_code
):
    manifest = _skill("sample-skill")
    entry_path = "skills/sample-skill/linked-item"
    entries = [
        {
            "path": entry_path,
            "type": entry_type,
            "mode": mode,
            "sha": "a" * 40,
            "size": 0,
        },
        {
            "path": "skills/sample-skill/SKILL.md",
            "type": "blob",
            "mode": "100644",
            "sha": _blob_sha(manifest),
            "size": len(manifest),
        },
    ]
    transport = _FakeGitHub({"skills/sample-skill/SKILL.md": manifest}, entries_override=entries)
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(SkillValidationError) as error:
        importer.import_skills(source, ["skills/sample-skill"])

    assert error.value.code == expected_code
    assert not (tmp_path / "skills" / "sample-skill").exists()


@pytest.mark.parametrize(
    "mismatch,expected_code",
    [
        ("sha", "GITHUB_BLOB_HASH_MISMATCH"),
        ("size", "GITHUB_BLOB_SIZE_MISMATCH"),
    ],
)
def test_github_import_rejects_manifest_blob_hash_or_size_mismatch(
    tmp_path, mismatch, expected_code
):
    manifest = _skill("sample-skill")
    entry = {
        "path": "skills/sample-skill/SKILL.md",
        "type": "blob",
        "mode": "100644",
        "sha": _blob_sha(manifest),
        "size": len(manifest),
    }
    if mismatch == "sha":
        entry["sha"] = "0" * 40
    else:
        entry["size"] += 1
    transport = _FakeGitHub(
        {entry["path"]: manifest},
        entries_override=[entry],
    )
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(GitHubImportError) as error:
        importer.import_skills(source, ["skills/sample-skill"])

    assert error.value.code == expected_code
    assert not (tmp_path / "skills" / "sample-skill").exists()


@pytest.mark.parametrize(
    "http_status,expected_code,expected_status",
    [
        (429, "GITHUB_RATE_LIMITED", 429),
        (504, "GITHUB_TIMEOUT", 504),
    ],
)
def test_github_http_rate_limit_and_timeout_are_reported_as_failures(
    tmp_path, http_status, expected_code, expected_status
):
    class FailingTransport:
        def get(self, url: str, *, max_bytes: int, timeout: float) -> HTTPResult:
            raise GitHubTransportError(http_status)

    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, FailingTransport())

    with pytest.raises(GitHubImportError) as error:
        importer.preview("https://github.com/owner/repo")

    assert error.value.code == expected_code
    assert error.value.status_code == expected_status


def test_github_import_rejects_a_file_over_the_per_file_limit(tmp_path, monkeypatch):
    manifest = _skill("sample-skill")
    large_file = b"x" * (len(manifest) + 1)
    monkeypatch.setattr(github_import_module, "MAX_PACKAGE_FILE_BYTES", len(manifest))
    files = {
        "skills/sample-skill/SKILL.md": manifest,
        "skills/sample-skill/references/large.txt": large_file,
    }
    transport = _FakeGitHub(files)
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(SkillValidationError) as error:
        importer.import_skills(source, ["skills/sample-skill"])

    assert error.value.code == "SKILL_FILE_TOO_LARGE"
    raw_urls = [url for url in transport.calls if "raw.githubusercontent.com" in url]
    assert raw_urls == [
        "https://raw.githubusercontent.com/test-owner/different-repo-name/"
        f"{transport.commit}/skills/sample-skill/SKILL.md"
    ]


def test_github_import_rejects_package_total_size_limit(tmp_path, monkeypatch):
    manifest = _skill("sample-skill")
    resource = b"resource"
    monkeypatch.setattr(github_import_module, "MAX_PACKAGE_FILE_BYTES", len(manifest) + 16)
    monkeypatch.setattr(github_import_module, "MAX_PACKAGE_BYTES", len(manifest) + len(resource) - 1)
    files = {
        "skills/sample-skill/SKILL.md": manifest,
        "skills/sample-skill/references/a.txt": resource,
    }
    transport = _FakeGitHub(files)
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(SkillValidationError) as error:
        importer.import_skills(source, ["skills/sample-skill"])

    assert error.value.code == "SKILL_PACKAGE_TOO_LARGE"
    assert not (tmp_path / "skills" / "sample-skill").exists()


def test_github_import_rejects_total_download_size_across_selected_skills(tmp_path, monkeypatch):
    first = _skill("first-skill")
    second = _skill("second-skill")
    monkeypatch.setattr(github_import_module, "MAX_TOTAL_RAW_BYTES", len(first) + len(second) - 1)
    files = {
        "skills/first-skill/SKILL.md": first,
        "skills/second-skill/SKILL.md": second,
    }
    transport = _FakeGitHub(files)
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(SkillValidationError) as error:
        importer.import_skills(
            source,
            ["skills/first-skill", "skills/second-skill"],
        )

    assert error.value.code == "GITHUB_DOWNLOAD_TOO_LARGE"
    assert not (tmp_path / "skills").exists()


def test_github_import_rejects_package_file_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(github_import_module, "MAX_PACKAGE_FILE_COUNT", 2)
    files = {
        "skills/sample-skill/SKILL.md": _skill("sample-skill"),
        "skills/sample-skill/references/a.txt": b"a",
        "skills/sample-skill/references/b.txt": b"b",
    }
    transport = _FakeGitHub(files)
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)
    source = {
        "owner": transport.owner,
        "repo": transport.repo,
        "commit_sha": transport.commit,
        "path": "skills",
    }

    with pytest.raises(SkillValidationError) as error:
        importer.import_skills(source, ["skills/sample-skill"])

    assert error.value.code == "SKILL_PACKAGE_TOO_LARGE"
    assert not any("raw.githubusercontent.com" in url for url in transport.calls)


def test_github_preview_rejects_tree_entry_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(github_import_module, "MAX_TREE_ENTRIES", 1)
    transport = _FakeGitHub({"one.txt": b"1", "two.txt": b"2"})
    repository = SkillRepository(tmp_path / "skills", staging_root=_external_staging_root(tmp_path))
    importer = GitHubSkillImporter(repository, transport)

    with pytest.raises(SkillValidationError) as error:
        importer.preview(f"https://github.com/{transport.owner}/{transport.repo}")

    assert error.value.code == "GITHUB_TREE_TOO_LARGE"


def test_skill_parser_accepts_utf8_bom_and_rejects_duplicate_yaml_keys():
    bom_document = b"\xef\xbb\xbf" + _skill("bom-skill")
    parsed = parse_skill_document(
        bom_document,
        directory_name="bom-skill",
        filename="SKILL.md",
        require_standard=True,
    )
    assert parsed.name == "bom-skill"

    duplicate_key_document = (
        "---\nname: duplicate-skill\ndescription: first\n"
        "description: second\n---\nbody\n"
    )
    with pytest.raises(SkillValidationError) as error:
        parse_skill_document(
            duplicate_key_document,
            directory_name="duplicate-skill",
            filename="SKILL.md",
            require_standard=True,
        )
    assert error.value.code == "SKILL_FRONTMATTER_INVALID"


def _api_client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(skill_routes, "SKILLS_DIR", tmp_path / "skills")
    app = FastAPI()
    app.include_router(skill_routes.router)
    app.dependency_overrides[skill_routes.current_user] = lambda: {"id": "synthetic-user"}
    app.dependency_overrides[skill_routes.current_user_optional] = lambda: {"id": "synthetic-user"}
    return TestClient(app)


def test_skill_crud_routes_preserve_success_data_contract(monkeypatch, tmp_path):
    with _api_client(monkeypatch, tmp_path) as client:
        created = client.post(
            "/api/skills",
            json={"content": "---\nname: sample-skill\ndescription: test\n---\nbody"},
        )
        assert created.status_code == 200
        skill = created.json()["data"]
        assert skill["id"] == "sample-skill"
        assert skill["enabled"] is True

        assert client.get("/api/skills").json()["data"]["skills"][0]["id"] == "sample-skill"
        assert client.get("/api/skills/sample-skill").json()["data"]["content"]
        patched = client.patch("/api/skills/sample-skill", json={"enabled": False})
        assert patched.json()["data"]["enabled"] is False


def test_github_routes_are_authenticated_static_endpoints_with_success_data(monkeypatch, tmp_path):
    calls = {}

    class FakeImporter:
        def __init__(self, repository):
            calls["root"] = repository.root

        def preview(self, url, *, ref=None, path=None):
            calls["preview"] = (url, ref, path)
            return {"source": {"owner": "owner", "repo": "repo", "commit_sha": "a" * 40, "path": ""}, "candidates": []}

        def import_skills(self, source, paths):
            calls["import"] = (source, paths)
            return [{"id": "sample-skill", "enabled": False}]

    monkeypatch.setattr(skill_routes, "GitHubSkillImporter", FakeImporter)
    with _api_client(monkeypatch, tmp_path) as client:
        preview = client.post("/api/skills/github/preview", json={"url": "https://github.com/owner/repo"})
        assert preview.status_code == 200
        assert preview.json()["data"]["source"]["commit_sha"] == "a" * 40
        imported = client.post(
            "/api/skills/github/import",
            json={
                "source": {"owner": "owner", "repo": "repo", "commit_sha": "a" * 40, "path": ""},
                "paths": [""],
            },
        )
        assert imported.status_code == 200
        assert imported.json()["data"]["skills"][0]["enabled"] is False
    assert calls["preview"] == ("https://github.com/owner/repo", None, None)
    assert calls["import"][1] == [""]
