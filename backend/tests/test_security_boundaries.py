"""Negative security-boundary tests for the W2 hardening work.

Each class covers one batch:
  * path guard allowlist / secret deny / reparse escape
Later batches append command, build, auth, MCP and webfetch cases.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.agent import path_guard
from src.agent.path_guard import (
    is_path_allowed_for_tree,
    resolve_guard_path,
    validate_path,
)

SECRET_NAMES = [
    ".env",
    ".env.local",
    ".env.production",
    ".enc_key",
    ".enc_key.bak",
    "keys_store.json",
    "keys_store.json.tmp",
]


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_secret_files_are_denied(name: str) -> None:
    ok, reason = validate_path(name, is_write=False)
    assert ok is False
    assert "deny pattern" in reason


def test_path_outside_allowlist_is_denied(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setattr(path_guard, "ALLOWED_DIRS", (allowed,))

    assert validate_path(str(outside / "note.txt"), is_write=False)[0] is False
    assert validate_path(str(allowed / "note.txt"), is_write=False)[0] is True


def test_traversal_is_denied() -> None:
    ok, reason = validate_path("..\\..\\Windows\\win.ini", is_write=False)
    assert ok is False
    assert "traversal" in reason


def test_windows_namespace_prefix_is_denied() -> None:
    assert resolve_guard_path("\\\\?\\C:\\Windows\\win.ini") == ""


def test_ads_is_denied() -> None:
    assert resolve_guard_path(str(Path("note.txt")) + ":secret") == ""


def test_symlink_escape_is_denied(monkeypatch, tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("SECRET", encoding="utf-8")
    link = allowed / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is not permitted on this host")
    monkeypatch.setattr(path_guard, "ALLOWED_DIRS", (allowed,))

    ok, reason = validate_path(str(link), is_write=False)
    assert ok is False
    assert "could not be resolved" in reason
    assert is_path_allowed_for_tree(str(link)) is False


def test_grep_walk_skips_secret_files(monkeypatch, tmp_path: Path) -> None:
    from src.agent.tools.groups.file_ops.grep import _GrepCfg, _grep_sync

    import re

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "notes.txt").write_text("SAFE_MARKER", encoding="utf-8")
    (allowed / ".env.local").write_text("SECRET_MARKER", encoding="utf-8")
    (allowed / "keys_store.json").write_text("SECRET_MARKER", encoding="utf-8")
    monkeypatch.setattr(path_guard, "ALLOWED_DIRS", (allowed,))

    matches, _ = _grep_sync(str(allowed), _GrepCfg(re.compile("MARKER"), "*", 100))
    contents = [item["content"] for item in matches]
    assert contents == ["SAFE_MARKER"]


class TestCommandPolicy:
    @pytest.mark.parametrize(
        "command",
        [
            "ls C:\\Users",
            "dir ..",
            "grep -r sk- C:\\Users",
            "where /r C:\\ python.exe",
            "git status && git diff",
            "git status; Remove-Item important.txt",
            "git diff --output=changes.txt",
            "git show --ext-diff",
            "git log -n abc",
            "git status ..\\..\\Windows",
            "pwd extra",
        ],
    )
    def test_unsafe_commands_are_not_auto_allowable(self, command: str) -> None:
        from src.agent.core.toolkit.command_policy import is_auto_allowable

        assert is_auto_allowable(command) is False

    @pytest.mark.parametrize(
        "command",
        ["pwd", "git status", "git status --short", "git diff --stat",
         "git log -n 3 --oneline", "git show --stat HEAD"],
    )
    def test_safe_commands_remain_auto_allowable(self, command: str) -> None:
        from src.agent.core.toolkit.command_policy import is_auto_allowable

        assert is_auto_allowable(command) is True

    def test_bypass_mode_does_not_skip_high_risk(self) -> None:
        from src.agent.core.toolkit.permission_classifier import (
            ALLOW,
            ASK,
            PermissionClassifier,
        )
        from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
        from src.agent.exceptions import ToolContext
        from pydantic import BaseModel

        class _CommandArgs(BaseModel):
            command: str

        class _CommandTool(ToolSpec):
            name: str = "run_command"
            description: str = "test"
            args_schema: type[BaseModel] = _CommandArgs
            risk_level: RiskLevel = RiskLevel.HIGH

            async def execute(self, args, ctx):
                raise AssertionError("permission check must not execute the tool")

        classifier = PermissionClassifier()
        bypass = ToolContext(permission_mode="bypassPermissions")
        assert classifier.check(
            _CommandTool(), {"command": "rm -rf /"}, bypass
        ) == ASK
        assert classifier.check(
            _CommandTool(), {"command": "git status"}, bypass
        ) == ALLOW

    def test_invalid_permission_mode_is_rejected(self) -> None:
        from pydantic import ValidationError
        from app.api.chat_routes import ChatRequest

        with pytest.raises(ValidationError):
            ChatRequest(
                messages=[{"role": "user", "content": "hi"}],
                permission_mode="root",
            )


class TestBuildBoundary:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import create_app

        return TestClient(create_app())

    @pytest.fixture
    def authed_client(self, client):
        from app.api.dependencies import require_session

        client.app.dependency_overrides[require_session] = lambda: "test-token"
        yield client
        client.app.dependency_overrides.clear()

    def test_build_requires_session(self, client) -> None:
        response = client.post(
            "/api/build",
            json={"code": "void setup(){}", "confirm": True},
        )
        assert response.status_code == 401

    def test_build_requires_explicit_confirmation(self, authed_client) -> None:
        response = authed_client.post("/api/build", json={"code": "void setup(){}"})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "options",
        [
            {"extra_scripts": "pre:evil.py"},
            {"platform_packages": "symlink://C:/evil"},
            {"build_flags": "-DSAFE\n[machine]\nextra_scripts=pre:evil.py"},
            {"upload_speed": "9600; Remove-Item x"},
        ],
    )
    def test_build_options_are_whitelisted(self, authed_client, options) -> None:
        response = authed_client.post(
            "/api/build",
            json={"code": "void setup(){}", "options": options, "confirm": True},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "lib_deps",
        [
            ["https://github.com/evil/lib"],
            ["file://C:/evil"],
            ["evil/lib\n[machine]\nextra_scripts=pre:evil.py"],
            ["a/b; Remove-Item x"],
        ],
    )
    def test_lib_deps_reject_urls_paths_and_injection(
        self, authed_client, lib_deps
    ) -> None:
        response = authed_client.post(
            "/api/build",
            json={"code": "void setup(){}", "lib_deps": lib_deps, "confirm": True},
        )
        assert response.status_code == 422

    def test_upload_rejects_client_binary_path(self, authed_client) -> None:
        response = authed_client.post(
            "/api/upload",
            json={"binary_path": ".build/tmp/x/firmware.bin", "confirm": True},
        )
        assert response.status_code == 422

    def test_upload_rejects_foreign_build_id(self, authed_client) -> None:
        from app.api import build_registry

        build_registry.clear_builds()
        build_id = build_registry.register_build(
            ".build/tmp/x/.pio/build/esp32s3/firmware.bin",
            "other-token",
            board="esp32-s3",
            platform="esp32",
            framework="arduino",
            lib_deps=(),
        )
        response = authed_client.post(
            "/api/upload",
            json={"build_id": build_id, "port": "COM1", "confirm": True},
        )
        events = [
            line for line in response.text.splitlines()
            if '"BUILD_NOT_FOUND"' in line
        ]
        assert events
        build_registry.clear_builds()

    def test_session_id_traversal_is_rejected(self) -> None:
        from src.hardware.build_validation import BuildInputError, sanitize_session_id

        for value in ["..", "../..", "C:\\Windows", "a/b", ""]:
            with pytest.raises(BuildInputError):
                sanitize_session_id(value)


class TestAuthAndMCPBoundary:
    def test_first_store_key_requires_console_code(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient
        from app.api import auth, setup_code
        from app.main import create_app

        monkeypatch.setattr(auth, "STORE_PATH", tmp_path / "keys_store.json")
        monkeypatch.setattr(auth, "ENCRYPTION_KEY_PATH", tmp_path / "enc_key")
        monkeypatch.setattr(setup_code, "SETUP_CODE_PATH", tmp_path / "setup_code")
        monkeypatch.setattr(setup_code, "_cache", "")
        client = TestClient(create_app())
        payload = {"provider": "test", "api_key": "sk-synthetic"}

        assert client.post("/api/auth/store-key", json=payload).status_code == 403
        payload["setup_code"] = setup_code.get_setup_code()
        response = client.post("/api/auth/store-key", json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["session_token"]

    @pytest.mark.parametrize(
        "config",
        [
            {"id": "x", "command": "powershell.exe"},
            {"id": "x", "command": "cmd.exe"},
            {"id": "x", "command": "bash", "args": ["-c", "id"]},
            {"id": "x", "command": "python", "args": ["-c", "print(1)"]},
            {"id": "x", "command": "python", "env": {"LD_PRELOAD": "x"}},
        ],
    )
    def test_mcp_config_rejects_shells_and_inline_code(self, config) -> None:
        from pydantic import ValidationError
        from app.api.mcp_routes import MCPServerConfig

        with pytest.raises(ValidationError):
            MCPServerConfig.model_validate(config, strict=True)

    def test_mcp_allows_module_launch(self) -> None:
        import sys
        from app.api.mcp_routes import MCPServerConfig

        config = MCPServerConfig.model_validate(
            {"id": "x", "command": sys.executable, "args": ["-m", "some_server"]},
            strict=True,
        )
        assert config.args == ["-m", "some_server"]

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/mcp/servers"),
            ("get", "/api/mcp/servers"),
            ("get", "/api/agent-sandbox/audit"),
            ("get", "/api/agent-sandbox/policy"),
        ],
    )
    def test_sensitive_routes_require_session(self, method, path) -> None:
        from fastapi.testclient import TestClient
        from app.main import create_app

        client = TestClient(create_app())
        kwargs = {"json": {"id": "x", "command": "python"}} if method == "post" else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401


class TestWebFetchBoundary:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:58080/health",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://localhost/",
            "http://foo.localhost/",
            "http://foo.internal/",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://0.0.0.0/",
            "file:///C:/Windows/win.ini",
            "http://user:pass@example.com/",
            "ftp://example.com/file",
        ],
    )
    def test_local_and_non_http_targets_are_blocked(self, url: str) -> None:
        from src.agent.tools.groups.retrieval.webfetch import _validate_target

        with pytest.raises(RuntimeError):
            _validate_target(url)

    def test_egress_allowlist_blocks_other_hosts(self, monkeypatch) -> None:
        from src.agent.tools.groups.retrieval import webfetch

        monkeypatch.setenv("WEBFETCH_ALLOWED_HOSTS", "example.com")
        with pytest.raises(RuntimeError):
            webfetch._validate_target("https://evil.invalid/collect")

    def test_webfetch_is_not_low_risk(self) -> None:
        from src.agent.core.toolkit.tool_spec import RiskLevel
        from src.agent.tools.groups.retrieval.webfetch import WebFetchTool

        assert WebFetchTool().risk_level == RiskLevel.MEDIUM
