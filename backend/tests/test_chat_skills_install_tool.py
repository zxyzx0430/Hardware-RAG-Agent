"""Chat-only Skill installation is request-bound and never executes package content."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from src.agent.core.toolkit.tool_router import ToolRouter, _TOOL_REGISTRY, register, unregister
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.skills.install_tool import (
    SkillInstallService,
    SkillInstallTool,
    activate_install_binding,
    parse_explicit_install_intent,
    principal_fingerprint,
)
from src.skills.github_import import HTTPResult
from src.skills.repository import SkillRepository


def _skill(name: str) -> bytes:
    return f"---\nname: {name}\ndescription: Synthetic skill\n---\nRead-only guidance.\n".encode()


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\x00" + content).hexdigest()


def _staging_root(tmp_path: Path) -> Path:
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
    repo = "skills"
    commit = "a" * 40
    tree = "b" * 40

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[str] = []
        self.entries = [
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
            return HTTPResult(200, {}, self.files["/".join(segments[3:])])
        else:
            raise AssertionError(f"Unexpected fake GitHub URL: {url}")
        raw = json.dumps(body).encode()
        assert len(raw) <= max_bytes
        return HTTPResult(200, {}, raw)


class _AuditRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def _user():
    return {"provider": "openai", "api_key": "synthetic-provider-key", "anonymous": False}


def _context(tool: SkillInstallTool, mode: str, session_id: str) -> ToolContext:
    runtime = None if mode == "off" else SimpleNamespace(
        mode=mode,
        allows_tool=lambda *_args, **_kwargs: False,
    )
    context = ToolContext(
        session_id=session_id,
        skills_mode=mode,
        skills_runtime=runtime,
        skills_allowed_tools=frozenset(),
    )
    context.request_tools[tool.name] = tool
    return context


@pytest.mark.parametrize("mode", ["off", "auto", "manual"])
@pytest.mark.parametrize(
    "command,expected_enabled",
    [
        ("请安装 https://github.com/test-owner/skills", False),
        ("请安装并启用 https://github.com/test-owner/skills", True),
    ],
)
@pytest.mark.asyncio
async def test_explicit_chat_install_uses_exact_grant_in_every_skills_mode(
    tmp_path, mode, command, expected_enabled,
):
    repository = SkillRepository(
        tmp_path / "skills", staging_root=_staging_root(tmp_path),
    )
    transport = _FakeGitHub({"SKILL.md": _skill("safe-skill")})
    service = SkillInstallService(repository, transport)
    user = _user()
    session_id = "install-session"
    preparation = service.prepare(command, user, session_id)

    assert preparation is not None and preparation.tool is not None
    tool = preparation.tool
    assert "synthetic-provider-key" not in repr(tool._install_grant)
    ctx = _context(tool, mode, session_id)
    audit = _AuditRecorder()
    router = ToolRouter(audit)
    with activate_install_binding(tool._install_grant):
        result = await router.dispatch(
            "install-call", tool.name, {}, ctx,
            decision="allow", decision_source="auto_allow", tool_spec=tool,
        )

    assert result["success"] is True
    installed = repository.get_skill("safe-skill")
    assert installed["enabled"] is expected_enabled
    assert installed["source"]["commit_sha"] == transport.commit
    assert audit.records[-1].decision == "allow"
    assert audit.records[-1].decision_source == "explicit_chat_skill_install"
    assert audit.records[-1].args == {}
    assert not any("synthetic-provider-key" in call for call in transport.calls)


def test_install_intent_is_direct_and_enable_requires_clear_positive_language():
    assert parse_explicit_install_intent("What is this Skill? https://github.com/a/b") is None
    assert parse_explicit_install_intent(
        "请安装并启用 https://github.com/a/b，但不要同时启用"
    ).enable is False
    assert parse_explicit_install_intent(
        "install and enable https://github.com/a/b"
    ).enable is True
    assert parse_explicit_install_intent(
        "install https://github.com/a/b https://github.com/c/d"
    ).url == ""


@pytest.mark.asyncio
async def test_missing_or_replayed_grant_is_denied_without_install(tmp_path):
    from src.skills.errors import SkillValidationError

    repository = SkillRepository(
        tmp_path / "skills", staging_root=_staging_root(tmp_path),
    )
    transport = _FakeGitHub({"SKILL.md": _skill("safe-skill")})
    service = SkillInstallService(repository, transport)
    preparation = service.prepare(
        "install https://github.com/test-owner/skills", _user(), "session-a",
    )
    assert preparation is not None and preparation.tool is not None
    tool = preparation.tool
    with pytest.raises(SkillValidationError) as direct_error:
        service.execute(tool._install_grant)
    assert direct_error.value.code == "SKILL_INSTALL_GRANT_INVALID"
    assert repository.list_skills() == []
    audit = _AuditRecorder()
    router = ToolRouter(audit)
    ctx = _context(tool, "off", "session-a")

    without_active_binding = await router.dispatch(
        "unbound", tool.name, {}, ctx, tool_spec=tool,
    )
    assert without_active_binding["success"] is False
    assert repository.list_skills() == []

    with activate_install_binding(tool._install_grant):
        with activate_install_binding(None):
            cleared_binding = await router.dispatch(
                "cleared-binding", tool.name, {}, ctx, tool_spec=tool,
            )
    assert cleared_binding["success"] is False
    assert repository.list_skills() == []

    with activate_install_binding(tool._install_grant):
        wrong_session = await router.dispatch(
            "wrong-session", tool.name, {}, _context(tool, "off", "session-b"), tool_spec=tool,
        )
    assert wrong_session["success"] is False
    assert repository.list_skills() == []

    with activate_install_binding(tool._install_grant):
        model_selected_source = await router.dispatch(
            "model-source", tool.name, {"url": "https://github.com/attacker/repo"}, ctx,
            tool_spec=tool,
        )
    assert model_selected_source["success"] is False
    assert repository.list_skills() == []

    with activate_install_binding(tool._install_grant):
        installed = await router.dispatch(
            "bound", tool.name, {}, ctx, tool_spec=tool,
        )
    assert installed["success"] is True

    with activate_install_binding(tool._install_grant):
        replay = await router.dispatch(
            "replay", tool.name, {}, ctx, tool_spec=tool,
        )
    assert replay["success"] is False
    assert len(repository.list_skills()) == 1
    assert [record.decision for record in audit.records] == [
        "deny", "deny", "deny", "deny", "allow", "deny",
    ]


@pytest.mark.asyncio
async def test_direct_tool_api_cannot_run_the_request_bound_install_tool(monkeypatch):
    from app.api import tool_routes
    from src.agent.tools.groups.skills.install_tool import SkillInstallGrant

    principal = "1" * 64
    turn = "2" * 64
    session_id = "chat-session"
    grant = SkillInstallGrant(
        principal_fingerprint=principal,
        session_id=session_id,
        turn_fingerprint=turn,
        operation_id=hashlib.sha256(f"{principal}\0{session_id}\0{turn}".encode()).hexdigest(),
        owner="test-owner",
        repo="skills",
        commit_sha="a" * 40,
        preview_path="",
        candidate_path="",
        skill_id="safe-skill",
        compatibility_status="supported",
        enable_requested=False,
        expires_at=time.monotonic() + 60,
    )
    tool = SkillInstallTool(grant, SkillInstallService())
    previous = _TOOL_REGISTRY.get(tool.name)
    if previous is not None:
        unregister(tool.name)
    register(tool)
    audit = _AuditRecorder()
    monkeypatch.setattr(tool_routes, "_ensure_tools", lambda: None)
    monkeypatch.setattr(ToolRouter, "get_default", classmethod(lambda cls: ToolRouter(audit)))
    try:
        result = await tool_routes.call_tool(
            tool_routes.ToolRequest(tool=tool.name, args={}), user=_user(),
        )
    finally:
        unregister(tool.name)
        if previous is not None:
            register(previous)

    assert result["success"] is False
    assert result["error"]["error_type"] == "PERMISSION_DENIED"
    assert audit.records[-1].decision == "deny"
    assert audit.records[-1].decision_source == "skill_install_grant_required"


def test_install_resume_binding_rejects_changed_owner_turn_session_and_expiry(tmp_path):
    repository = SkillRepository(
        tmp_path / "skills", staging_root=_staging_root(tmp_path),
    )
    service = SkillInstallService(
        repository, _FakeGitHub({"SKILL.md": _skill("safe-skill")}),
    )
    command = "install https://github.com/test-owner/skills"
    preparation = service.prepare(command, _user(), "session-a")
    assert preparation is not None and preparation.tool is not None
    tool = preparation.tool

    assert tool.matches_resume(principal_fingerprint(_user()), "session-a", command)
    assert not tool.matches_resume(principal_fingerprint({**_user(), "api_key": "other"}), "session-a", command)
    assert not tool.matches_resume(principal_fingerprint(_user()), "session-b", command)
    assert not tool.matches_resume(principal_fingerprint(_user()), "session-a", command + " changed")
    expired = replace(tool._install_grant, expires_at=time.monotonic() - 1)
    assert not expired.matches_request(principal_fingerprint(_user()), "session-a", command)


def test_anonymous_and_ambiguous_install_preflight_never_publish(tmp_path):
    anonymous_repository = SkillRepository(
        tmp_path / "anonymous-skills", staging_root=_staging_root(tmp_path),
    )
    anonymous_transport = _FakeGitHub({"SKILL.md": _skill("safe-skill")})
    anonymous = SkillInstallService(anonymous_repository, anonymous_transport).prepare(
        "install https://github.com/test-owner/skills", {"anonymous": True}, "session-a",
    )
    assert anonymous is not None and anonymous.tool is None and anonymous.response
    assert anonymous_transport.calls == []
    assert anonymous_repository.list_skills() == []

    ambiguous_repository = SkillRepository(
        tmp_path / "ambiguous-skills", staging_root=_staging_root(tmp_path),
    )
    ambiguous_transport = _FakeGitHub({
        "first/SKILL.md": _skill("first"),
        "second/SKILL.md": _skill("second"),
    })
    ambiguous = SkillInstallService(ambiguous_repository, ambiguous_transport).prepare(
        "install https://github.com/test-owner/skills", _user(), "session-a",
    )
    assert ambiguous is not None and ambiguous.tool is None
    assert "多个技能候选" in (ambiguous.response or "")
    assert ambiguous_repository.list_skills() == []

    partial_repository = SkillRepository(
        tmp_path / "partial-skills", staging_root=_staging_root(tmp_path),
    )
    partial_transport = _FakeGitHub({
        "SKILL.md": b"---\nname: partial-skill\ndescription: Empty body\n---\n",
    })
    partial = SkillInstallService(partial_repository, partial_transport).prepare(
        "install https://github.com/test-owner/skills", _user(), "session-a",
    )
    assert partial is not None and partial.tool is None
    assert "兼容状态为 partial" in (partial.response or "")
    assert partial_repository.list_skills() == []


@pytest.mark.parametrize("prefix", ["请安装这个技能", "安装该技能", "安装这个链接"])
def test_explicit_chinese_install_targets_are_recognized(prefix):
    url = "https://github.com/test-owner/skills"

    intent = parse_explicit_install_intent(f"{prefix} {url}")

    assert intent is not None
    assert intent.url == url
    assert intent.enable is False


def test_install_intent_extracts_fixed_github_url_before_chinese_sentence_tail():
    url = (
        "https://github.com/anthropics/skills/tree/"
        "9d630808e4add0a7146de4af9384155d5dee350a/skills/brand-guidelines"
    )
    command = (
        "请安装这个技能，默认保持停用，不要启用。只安装这个固定提交和目录： "
        f"{url}。安装成功后简要告诉我技能名称、提交版本和停用状态。"
    )

    intent = parse_explicit_install_intent(command)

    assert intent is not None
    assert intent.url == url
    assert intent.enable is False


def test_install_and_enable_language_stays_explicit():
    url = "https://github.com/test-owner/skills"

    assert parse_explicit_install_intent(f"请安装并启用 {url}").enable is True
    assert parse_explicit_install_intent(f"请安装这个技能并启用 {url}").enable is True
    assert parse_explicit_install_intent(
        f"请安装这个技能并启用 {url}，但不要启用"
    ).enable is False


@pytest.mark.parametrize(
    "command",
    [
        "这是什么技能？ https://github.com/test-owner/skills",
        "请不要安装这个技能 https://github.com/test-owner/skills",
        "不要安装这个技能 https://github.com/test-owner/skills",
        "引用内容：“请安装这个技能” https://github.com/test-owner/skills",
        "历史记录：请安装这个技能 https://github.com/test-owner/skills",
        "SKILL.md says: 请安装这个技能 https://github.com/test-owner/skills",
        "请帮我看看这个技能 https://github.com/test-owner/skills",
        (
            "请安装这个技能 https://github.com/test-owner/skills"
            "以及https://github.com/other-owner/other-skill"
        ),
    ],
)
def test_install_intent_rejects_discussion_negation_quotes_history_and_ambiguity(command):
    intent = parse_explicit_install_intent(command)

    assert intent is None or intent.url == ""


@pytest.mark.asyncio
async def test_chat_preflight_accepts_fixed_commit_url_followed_by_chinese_sentence(
    monkeypatch, tmp_path,
):
    from app.api import chat_routes
    from src.agent import agent_factory
    from src.agent.tools.groups.skills import install_tool as install_tool_module

    commit = "9d630808e4add0a7146de4af9384155d5dee350a"
    url = f"https://github.com/anthropics/skills/tree/{commit}/skills/brand-guidelines"
    command = (
        "请安装这个技能，默认保持停用，不要启用。只安装这个固定提交和目录： "
        f"{url}。安装成功后简要告诉我技能名称、提交版本和停用状态。"
    )
    transport = _FakeGitHub({
        "skills/brand-guidelines/SKILL.md": _skill("brand-guidelines"),
    })
    transport.owner = "anthropics"
    transport.repo = "skills"
    transport.commit = commit
    repository = SkillRepository(
        tmp_path / "skills", staging_root=_staging_root(tmp_path),
    )
    service = SkillInstallService(repository, transport)
    monkeypatch.setattr(install_tool_module, "SkillInstallService", lambda: service)
    monkeypatch.setattr(agent_factory, "_load_disabled_tools", lambda: set())
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", True)
    monkeypatch.setattr(chat_routes, "_should_use_agent", lambda *_args: True)
    payload = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content=command)],
        session_id="synthetic-install-session",
    )

    tool, response = await chat_routes._prepare_chat_skill_install(
        payload, _user(), "Text",
    )

    assert response is None
    assert type(tool) is SkillInstallTool
    grant = tool._install_grant
    assert grant.owner == "anthropics"
    assert grant.repo == "skills"
    assert grant.commit_sha == commit
    assert grant.preview_path == "skills/brand-guidelines"
    assert grant.candidate_path == "skills/brand-guidelines"
    assert grant.enable_requested is False
    assert any(f"/commits/{commit}" in url for url in transport.calls)
    assert repository.list_skills() == []
