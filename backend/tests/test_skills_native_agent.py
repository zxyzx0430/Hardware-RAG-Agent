"""Synthetic native-Agent Skills flows; no network, database, or user roots."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.api.chat_routes import ChatRequest
from src.agent.core.toolkit.permission_classifier import PermissionClassifier
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.exceptions import ToolContext
from src.agent.hitl_permission import evaluate_pending
from src.agent.mcp_authorization import MCPRequestAuthorizations
from src.agent.request_context import activate_tool_context
from src.agent.skill_runtime import (
    ImportSkillArgs,
    ImportSkillTool,
    SkillsRuntime,
    build_skills_runtime,
)
from src.skills.errors import SkillUnavailableError, SkillVersionChangedError
from src.skills.github_import import HTTPResult
from src.skills.repository import SkillRepository


_COMMIT = "a" * 40
_SKILL_BODY = (
    "---\nname: imported-safe\ndescription: Synthetic imported skill\n---\n"
    "SYNTHETIC-INSTRUCTIONS\n"
).encode("utf-8")
_RESOURCE_BODY = b"SYNTHETIC-RESOURCE\n"
_GITHUB_COMMIT = "c" * 40
_GITHUB_TREE = "d" * 40


def _external_native_staging_root(tmp_path: Path) -> Path:
    """Keep importer staging outside the isolated source copy, as platform tests do."""
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


class _FixedGitHubTransport:
    """Serve a fixed commit and its blobs without opening a network connection."""

    owner = "synthetic-owner"
    repo = "synthetic-repo"

    def __init__(self):
        self.calls: list[str] = []
        self.files = {
            "skills/imported-safe/SKILL.md": _SKILL_BODY,
            "skills/imported-safe/references/note.md": _RESOURCE_BODY,
        }
        self.entries = [
            {
                "path": path,
                "type": "blob",
                "mode": "100644",
                "sha": hashlib.sha1(
                    b"blob " + str(len(content)).encode("ascii") + b"\x00" + content
                ).hexdigest(),
                "size": len(content),
            }
            for path, content in self.files.items()
        ]

    def get(self, url: str, *, max_bytes: int, timeout: float) -> HTTPResult:
        self.calls.append(url)
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.hostname == "api.github.com" and path == (
            f"/repos/{self.owner}/{self.repo}/commits/{_GITHUB_COMMIT}"
        ):
            body = {
                "sha": _GITHUB_COMMIT,
                "commit": {"tree": {"sha": _GITHUB_TREE}},
            }
        elif parsed.hostname == "api.github.com" and path == (
            f"/repos/{self.owner}/{self.repo}/git/trees/{_GITHUB_TREE}"
        ):
            body = {"sha": _GITHUB_TREE, "truncated": False, "tree": self.entries}
        elif parsed.hostname == "raw.githubusercontent.com":
            segments = path.lstrip("/").split("/")
            assert segments[:3] == [self.owner, self.repo, _GITHUB_COMMIT]
            content_path = "/".join(segments[3:])
            return HTTPResult(200, {}, self.files[content_path])
        else:
            raise AssertionError(f"Unexpected synthetic GitHub URL: {url}")
        raw = json.dumps(body).encode("utf-8")
        assert len(raw) <= max_bytes
        return HTTPResult(200, {}, raw)


class _NoopAudit:
    def record(self, _record):
        return None


class _SyntheticImporter:
    """Install only deterministic fixture bytes into a temporary repository."""

    def __init__(self, repository: SkillRepository):
        self.repository = repository
        self.calls: list[tuple[dict, tuple[str, ...], bool]] = []

    def preview(self, url: str, *, ref: str | None = None, path: str | None = None) -> dict:
        return {
            "source": {
                "owner": "synthetic-owner", "repo": "synthetic-repo",
                "commit_sha": _COMMIT, "path": "skills",
            },
            "candidates": [{
                "id": "imported-safe", "name": "imported-safe",
                "description": "Synthetic imported skill", "path": "skills/imported-safe",
            }],
        }

    def import_skills(
        self,
        source: dict,
        paths: list[str],
        *,
        claude_adapter: bool = False,
    ) -> list[dict]:
        self.calls.append((dict(source), tuple(paths), claude_adapter))
        installed = self.repository.install_package(
            {
                "SKILL.md": _SKILL_BODY,
                "references/note.md": _RESOURCE_BODY,
            },
            {
                "owner": source["owner"],
                "repo": source["repo"],
                "commit_sha": source["commit_sha"],
                "path": "skills/imported-safe",
            },
            claude_adapter=claude_adapter,
        )
        return [installed]


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {"call_id": call_id, "name": name, "args": args}


async def _invoke_tool_node(tool, call_id: str, args: dict) -> str:
    message = AIMessage(
        content="",
        tool_calls=[{
            "id": call_id,
            "name": tool.name,
            "args": args,
            "type": "tool_call",
        }],
    )
    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([tool]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    result = await graph.compile().ainvoke({"messages": [message]})
    tool_message = next(
        item for item in result["messages"] if isinstance(item, ToolMessage)
    )
    return tool_message.content


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_toolnode_import_uses_request_spec_confirmation_and_off_snapshot_gate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    from src.agent import agent_factory
    from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY
    from src.agent.skill_runtime import PreviewSkillImportTool, ReadSkillResourceTool, LoadSkillTool

    repository = SkillRepository(
        tmp_path / "skills",
        staging_root=_external_native_staging_root(tmp_path),
    )
    runtime = build_skills_runtime("off", [], repository=repository)
    importer = _SyntheticImporter(repository)
    authorization = MCPRequestAuthorizations(())
    ctx = ToolContext(
        permission_mode="bypassPermissions",
        session_id="synthetic-native-skills",
        skills_mode="off",
        skills_runtime=runtime,
        mcp_authorization=authorization,
    )
    payload = ChatRequest(
        messages=[{"role": "user", "content": "Synthetic import request"}],
        skills_mode="off",
        permission_mode="bypassPermissions",
    )
    registered_import_tool = _TOOL_REGISTRY.get("import_skill")
    monkeypatch.setattr(agent_factory, "_load_disabled_tools", lambda: set())
    tools = agent_factory.build_tool_specs(
        payload,
        skills_runtime=runtime,
        tool_context=ctx,
        skill_importer=importer,
    )
    request_tools = ctx.request_tools
    import_tool = request_tools["import_skill"]
    assert isinstance(import_tool, ImportSkillTool)
    assert isinstance(request_tools["preview_skill_import"], PreviewSkillImportTool)
    assert isinstance(request_tools["load_skill"], LoadSkillTool)
    assert isinstance(request_tools["read_skill_resource"], ReadSkillResourceTool)
    assert _TOOL_REGISTRY.get("import_skill") is registered_import_tool
    assert import_tool in tools
    assert importer.repository is repository

    monkeypatch.setattr(
        ToolRouter,
        "get_default",
        classmethod(lambda _cls: ToolRouter(_NoopAudit())),
    )
    preview_text = await _invoke_tool_node(
        request_tools["preview_skill_import"],
        "preview-call",
        {"url": "https://github.com/synthetic-owner/synthetic-repo", "path": "skills"},
    )
    assert "imported-safe" in preview_text
    assert runtime.catalog() == []

    args = {
        "source": {
            "owner": "synthetic-owner",
            "repo": "synthetic-repo",
            "commit_sha": _COMMIT,
            "path": "skills",
        },
        "paths": ["skills/imported-safe"],
        "scope": "project",
        "claude_adapter": False,
    }
    call = _tool_call("import-call", "import_skill", args)
    assert PermissionClassifier().check(import_tool, args, ctx) == "ask"
    with activate_tool_context(ctx):
        assert evaluate_pending([call], "bypassPermissions") == "ask"
    assert authorization.next_confirmation(
        [call], request_tools, PermissionClassifier(), ctx,
    ) == call
    assert authorization.validate_confirmation("import-call", [call]) is True
    authorization.resolve_confirmation("import-call", decision)
    assert authorization.next_confirmation(
        [call], request_tools, PermissionClassifier(), ctx,
    ) is None

    import_text = await _invoke_tool_node(import_tool, "import-call", args)
    assert "import-call" in authorization._consumed
    if decision == "deny":
        assert importer.calls == []
        assert runtime.snapshots == ()
        assert "PERMISSION_DENIED" in import_text
        with pytest.raises(SkillUnavailableError):
            runtime.load_instructions("imported-safe")
        return

    assert len(importer.calls) == 1
    assert runtime.snapshots[-1].id == "imported-safe"
    assert runtime.snapshots[-1].content_hash in import_text
    assert runtime.catalog() == []
    assert runtime.consumed_bytes == 0
    assert "imported-safe" in import_text
    assert "SYNTHETIC-INSTRUCTIONS" not in import_text
    assert "package_path" not in import_text

    from app.api import chat_routes

    resumed = chat_routes._ensure_skills_runtime(
        chat_routes.AgentRequestSnapshot(
            skills_mode="off", skills_runtime=runtime, skill_snapshots=(),
        ),
    )
    assert resumed.skill_snapshots == runtime.snapshots

    load_text = await _invoke_tool_node(
        request_tools["load_skill"],
        "load-call",
        {"skill_id": "imported-safe"},
    )
    load_result = json.loads(load_text)
    assert load_result["data"]["instructions"] == _SKILL_BODY.decode("utf-8")
    assert load_result["data"]["package_path"] == str(repository.root / "imported-safe")
    assert load_result["data"]["resource_paths"] == ["references/note.md"]

    resource_text = await _invoke_tool_node(
        request_tools["read_skill_resource"],
        "resource-call",
        {"skill_id": "imported-safe", "path": "references/note.md"},
    )
    assert "SYNTHETIC-RESOURCE" in resource_text


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_auto_and_manual_runtimes_append_imports_without_repinning_existing_skills(
    tmp_path,
    mode: str,
) -> None:
    repository = SkillRepository(
        tmp_path / "skills",
        staging_root=_external_native_staging_root(tmp_path),
    )
    repository.create_legacy_skill(
        "pinned-safe",
        "---\nname: pinned-safe\ndescription: Existing pinned skill\n---\n"
        "SYNTHETIC-OLD-INSTRUCTIONS",
    )
    skill_ids = ["pinned-safe"] if mode == "manual" else None
    runtime = build_skills_runtime(mode, skill_ids, repository=repository)
    original = runtime.snapshots
    importer = _SyntheticImporter(repository)

    result = runtime.import_from_github(
        importer,
        {
            "owner": "synthetic-owner",
            "repo": "synthetic-repo",
            "commit_sha": _COMMIT,
            "path": "skills",
        },
        ["skills/imported-safe"],
    )

    assert runtime.snapshots[:len(original)] == original
    assert [item.id for item in runtime.snapshots] == ["pinned-safe", "imported-safe"]
    assert runtime.snapshots[0].content_hash == original[0].content_hash
    assert "imported-safe" in json.dumps(result)
    assert runtime.load_instructions("imported-safe")["instructions"] == _SKILL_BODY.decode("utf-8")


@pytest.mark.asyncio
async def test_import_tool_selects_configured_user_scope_without_default_roots(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.skills import github_import

    project_root = tmp_path / "project-skills"
    user_root = tmp_path / "user-skills"
    legacy_root = tmp_path / "legacy-skills"
    staging_root = _external_native_staging_root(tmp_path)
    claude_project = tmp_path / "project-claude-skills"
    claude_user = tmp_path / "user-claude-skills"
    repository = SkillRepository(
        scope="project",
        staging_root=staging_root,
        project_root=project_root,
        user_root=user_root,
        legacy_root=legacy_root,
        claude_project_root=claude_project,
        claude_user_root=claude_user,
    )
    runtime = SkillsRuntime("off", (), repository=repository)
    captured: dict[str, object] = {}

    class ScopeCaptureImporter:
        def __init__(self, selected_repository):
            captured["repository"] = selected_repository
            self.repository = selected_repository

        def import_skills(self, source, paths, *, claude_adapter=False):
            captured["paths"] = tuple(paths)
            captured["claude_adapter"] = claude_adapter
            installed = self.repository.install_package(
                {"SKILL.md": _SKILL_BODY},
                {
                    "owner": source["owner"],
                    "repo": source["repo"],
                    "commit_sha": source["commit_sha"],
                    "path": "skills/imported-safe",
                },
                claude_adapter=claude_adapter,
            )
            return [installed]

    monkeypatch.setattr(github_import, "GitHubSkillImporter", ScopeCaptureImporter)
    tool = ImportSkillTool(runtime)
    args = {
        "source": {
            "owner": "synthetic-owner",
            "repo": "synthetic-repo",
            "commit_sha": _COMMIT,
            "path": "skills",
        },
        "paths": ["skills/imported-safe"],
        "scope": "user",
        "claude_adapter": False,
    }

    result = await tool.execute(args, ToolContext())

    selected_repository = captured["repository"]
    assert selected_repository.scope == "user"
    assert selected_repository.root == user_root
    assert selected_repository.project_root == project_root
    assert selected_repository.legacy_root == legacy_root
    assert selected_repository.staging_root == staging_root
    assert selected_repository.claude_roots == {
        "project": claude_project,
        "user": claude_user,
    }
    assert captured["paths"] == ("skills/imported-safe",)
    assert runtime.snapshots[-1].id == "imported-safe"
    assert "SYNTHETIC-INSTRUCTIONS" not in json.dumps(result)


def test_import_scope_schema_defaults_to_project_and_explicit_root_cannot_switch(
    tmp_path,
) -> None:
    from pydantic import ValidationError

    from src.skills.errors import SkillValidationError

    source = {
        "owner": "synthetic-owner",
        "repo": "synthetic-repo",
        "commit_sha": _COMMIT,
        "path": "skills",
    }
    args = ImportSkillArgs(source=source, paths=["skills/imported-safe"])
    assert args.scope == "project"
    with pytest.raises(ValidationError):
        ImportSkillArgs(source=source, paths=["skills/imported-safe"], scope="global")

    repository = SkillRepository(tmp_path / "explicit-skills")
    with pytest.raises(SkillValidationError, match="cannot switch install scope"):
        repository.for_scope("user")


@pytest.mark.asyncio
async def test_toolnode_real_github_importer_uses_scoped_repository_and_loads_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agent import agent_factory
    from src.agent import audit_logger
    from src.agent.core.toolkit.audit_recorder import _redact_args
    from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY
    from src.agent.core.toolkit.tool_spec import ConfirmationRule
    from src.agent.skill_runtime import LoadSkillTool, SkillImportSourceArgs
    from src.skills import github_import
    from src.skills.github_import import GitHubSkillImporter

    project_root = tmp_path / "project-skills"
    user_root = tmp_path / "user-skills"
    legacy_root = tmp_path / "legacy-skills"
    staging_root = _external_native_staging_root(tmp_path)
    repository = SkillRepository(
        scope="project",
        staging_root=staging_root,
        project_root=project_root,
        user_root=user_root,
        legacy_root=legacy_root,
        claude_project_root=tmp_path / "project-claude-skills",
        claude_user_root=tmp_path / "user-claude-skills",
    )
    runtime = build_skills_runtime("off", [], repository=repository)
    transport = _FixedGitHubTransport()
    created_importers: list[GitHubSkillImporter] = []
    audit_records: list[dict] = []
    monkeypatch.setattr(audit_logger, "log_tool_call", lambda **kwargs: audit_records.append(kwargs))

    safe_audit_args = _redact_args({
        "source": SkillImportSourceArgs(**{
            "owner": "synthetic-owner",
            "repo": "synthetic-repo",
            "commit_sha": _GITHUB_COMMIT,
            "path": "skills",
        }),
        "nested": {"api_token": "synthetic-secret"},
    })
    assert safe_audit_args == {
        "source": {
            "owner": "synthetic-owner",
            "repo": "synthetic-repo",
            "commit_sha": _GITHUB_COMMIT,
            "path": "skills",
        },
        "nested": {"api_token": "***REDACTED***"},
    }

    class CapturingGitHubSkillImporter(GitHubSkillImporter):
        def __init__(self, selected_repository):
            super().__init__(selected_repository, transport=transport)
            created_importers.append(self)

    monkeypatch.setattr(
        github_import, "GitHubSkillImporter", CapturingGitHubSkillImporter
    )
    monkeypatch.setattr(agent_factory, "_load_disabled_tools", lambda: set())

    authorization = MCPRequestAuthorizations(())
    ctx = ToolContext(
        permission_mode="bypassPermissions",
        session_id="synthetic-real-importer",
        skills_mode="off",
        skills_runtime=runtime,
        mcp_authorization=authorization,
    )
    payload = ChatRequest(
        messages=[{"role": "user", "content": "Import and use the synthetic skill"}],
        skills_mode="off",
        permission_mode="bypassPermissions",
    )
    registered_import_tool = _TOOL_REGISTRY.get("import_skill")
    tools = agent_factory.build_tool_specs(
        payload,
        skills_runtime=runtime,
        tool_context=ctx,
    )
    request_tools = ctx.request_tools
    import_tool = request_tools["import_skill"]
    load_tool = request_tools["load_skill"]
    assert isinstance(load_tool, LoadSkillTool)
    assert import_tool in tools
    assert import_tool.requires_confirmation == ConfirmationRule.ALWAYS
    assert _TOOL_REGISTRY.get("import_skill") is registered_import_tool
    assert runtime.catalog() == []

    source = {
        "owner": _FixedGitHubTransport.owner,
        "repo": _FixedGitHubTransport.repo,
        "commit_sha": _GITHUB_COMMIT,
        "path": "skills",
    }
    args = {
        "source": source,
        "paths": ["skills/imported-safe"],
        "scope": "user",
        "claude_adapter": False,
    }
    call = _tool_call("real-import-call", "import_skill", args)
    assert PermissionClassifier().check(import_tool, args, ctx) == "ask"
    with activate_tool_context(ctx):
        assert evaluate_pending([call], "bypassPermissions") == "ask"
    assert authorization.next_confirmation(
        [call], request_tools, PermissionClassifier(), ctx,
    ) == call
    authorization.resolve_confirmation("real-import-call", "allow")

    import_text = await _invoke_tool_node(import_tool, "real-import-call", args)
    import_result = json.loads(import_text)
    assert import_result.get("success") is True, f"Import tool returned an error envelope: {import_result!r}"
    assert isinstance(import_result.get("data"), dict), f"Import tool returned no data: {import_result!r}"
    imported = import_result["data"]["skills"][0]
    assert imported["id"] == "imported-safe"
    assert imported["content_hash"]
    assert "SYNTHETIC-INSTRUCTIONS" not in import_text
    assert str(user_root / "imported-safe") not in import_text
    assert runtime.snapshots[-1].id == "imported-safe"
    assert runtime.snapshots[-1].content_hash == imported["content_hash"]
    assert runtime.catalog() == []
    assert repository.get_skill("imported-safe")["enabled"] is True
    import_audit = next(item for item in audit_records if item["tool_name"] == "import_skill")
    assert import_audit["args"]["source"] == source
    assert import_audit["args"]["paths"] == ["skills/imported-safe"]

    scoped_importer = next(
        item for item in created_importers if item.repository.scope == "user"
    )
    assert isinstance(scoped_importer, GitHubSkillImporter)
    assert scoped_importer.repository.root == user_root
    assert scoped_importer.repository.project_root == project_root
    assert scoped_importer.repository.legacy_root == legacy_root
    assert scoped_importer.repository.staging_root == staging_root
    assert any(_GITHUB_COMMIT in url for url in transport.calls)
    assert all(
        _GITHUB_COMMIT in url
        for url in transport.calls
        if "raw.githubusercontent.com" in url
    )

    load_text = await _invoke_tool_node(
        load_tool,
        "real-load-call",
        {"skill_id": "imported-safe"},
    )
    loaded = json.loads(load_text)
    assert loaded["data"]["instructions"] == _SKILL_BODY.decode("utf-8")
    assert loaded["data"]["package_path"] == str(user_root / "imported-safe")
    assert "references/note.md" in loaded["data"]["resource_paths"]


def test_resource_read_revalidates_package_after_repository_read(tmp_path, monkeypatch):
    repository = SkillRepository(tmp_path / "skills", staging_root=tmp_path / "staging")
    repository.create_legacy_skill(
        "synthetic-resource",
        "---\nname: synthetic-resource\ndescription: Synthetic resource\n---\n"
        "Synthetic instructions",
    )
    package = repository.root / "synthetic-resource"
    references = package / "references"
    references.mkdir()
    resource = references / "note.md"
    resource.write_text("Before read", encoding="utf-8")
    runtime = build_skills_runtime(
        "manual", ["synthetic-resource"], repository=repository,
    )
    original_read = repository.read_skill_resource

    def mutate_after_read(skill_id, path, expected_hash=None):
        content = original_read(skill_id, path, expected_hash)
        resource.write_text("Changed after repository read", encoding="utf-8")
        return content

    monkeypatch.setattr(repository, "read_skill_resource", mutate_after_read)
    with pytest.raises(SkillVersionChangedError):
        runtime.read_resource("synthetic-resource", "references/note.md")


def test_automatic_catalog_and_loaded_metadata_are_progressive(tmp_path):
    repository = SkillRepository(tmp_path / "skills", staging_root=tmp_path / "staging")
    skill_markdown = (
        "---\nname: synthetic-progressive\ndescription: Progressive metadata\n---\n"
        "SYNTHETIC-PROGRESSIVE-BODY"
    )
    repository.create_legacy_skill(
        "synthetic-progressive",
        skill_markdown,
    )
    runtime = build_skills_runtime("auto", [], repository=repository)

    catalog = runtime.catalog()
    assert len(catalog) == 1
    assert set(catalog[0]) == {"id", "name", "description", "content_hash"}
    assert "SYNTHETIC-PROGRESSIVE-BODY" not in json.dumps(catalog)
    loaded = runtime.load_instructions("synthetic-progressive")
    assert loaded["instructions"] == skill_markdown
    assert loaded["package_path"] == str(repository.root / "synthetic-progressive")
    assert loaded["resource_paths"] == []
