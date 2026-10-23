"""Request-local Skills runtime checks use temporary, synthetic packages."""

import json
import pytest

from src.agent.skill_runtime import build_skills_runtime, LoadSkillTool, ReadSkillResourceTool
from src.agent.exceptions import ToolContext
from src.skills.errors import SkillUnavailableError, SkillValidationError, SkillVersionChangedError
from src.skills.repository import SkillRepository


@pytest.fixture()
def repository(tmp_path):
    root = tmp_path / "skills"
    package = root / "synthetic-safe"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_bytes(
        (
            "---\nname: synthetic-safe\ndescription: Synthetic instructions\n---\n"
            "Use references/note.md. Never execute scripts.\n"
        ).encode("utf-8")
    )
    (package / ".enabled").write_text("", encoding="utf-8")
    (package / "references").mkdir()
    (package / "references" / "note.md").write_text("Synthetic note", encoding="utf-8")
    return SkillRepository(root)


def test_auto_catalog_is_metadata_only(repository):
    runtime = build_skills_runtime("auto", [], repository=repository)
    catalog = runtime.catalog()
    assert catalog[0]["id"] == "synthetic-safe"
    assert set(catalog[0]) == {"id", "name", "description", "content_hash"}
    assert "Never execute scripts" not in json.dumps(catalog)
    assert runtime.consumed_bytes == 0


@pytest.mark.parametrize("mode,ids", [("unknown", []), ("manual", []), ("manual", ["synthetic-safe"] * 2), ("off", ["synthetic-safe"])])
def test_invalid_selection_is_not_silently_downgraded(repository, mode, ids):
    with pytest.raises(SkillValidationError):
        build_skills_runtime(mode, ids, repository=repository)


@pytest.mark.asyncio
async def test_tools_load_body_and_resource_with_safe_evidence(repository):
    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository)
    instructions = await LoadSkillTool(runtime).execute({"skill_id": "synthetic-safe"}, ToolContext())
    assert instructions["instructions"] == (
        "---\nname: synthetic-safe\ndescription: Synthetic instructions\n---\n"
        "Use references/note.md. Never execute scripts.\n"
    )
    assert instructions["package_path"] == str(repository.root / "synthetic-safe")
    assert "references/note.md" in instructions["resource_paths"]
    note = await ReadSkillResourceTool(runtime).execute({"skill_id": "synthetic-safe", "path": "references/note.md"}, ToolContext())
    assert note["content"] == "Synthetic note"
    evidence = runtime.evidence()
    assert len(evidence) == 2
    assert "Synthetic note" not in json.dumps(evidence)
    assert "Never execute scripts" not in json.dumps(evidence)
    assert runtime.consumed_bytes > 0


def test_disabled_changed_and_unknown_packages_are_not_read(repository):
    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository)
    with pytest.raises(SkillUnavailableError):
        runtime.load_instructions("other-package")
    repository.set_enabled("synthetic-safe", False)
    with pytest.raises(SkillUnavailableError):
        runtime.load_instructions("synthetic-safe")
    repository.set_enabled("synthetic-safe", True)
    (repository.root / "synthetic-safe" / "references" / "note.md").write_text("Changed note", encoding="utf-8")
    with pytest.raises(SkillVersionChangedError):
        runtime.read_resource("synthetic-safe", "references/note.md")


def test_budget_failure_does_not_return_truncated_content(repository):
    runtime = build_skills_runtime("manual", ["synthetic-safe"], repository=repository, max_context_bytes=10)
    with pytest.raises(SkillValidationError):
        runtime.load_instructions("synthetic-safe")
    assert runtime.consumed_bytes == 0
    assert runtime.evidence() == ()


def test_request_budgets_are_not_shared(repository):
    first = build_skills_runtime("auto", [], repository=repository)
    second = build_skills_runtime("auto", [], repository=repository)
    first.load_instructions("synthetic-safe")
    assert first.consumed_bytes > 0
    assert second.consumed_bytes == 0
    assert second.evidence() == ()


def test_off_mode_hides_catalog_and_only_loads_explicitly_imported_skills(repository):
    runtime = build_skills_runtime("off", [], repository=repository)
    assert runtime.catalog() == []
    with pytest.raises(SkillUnavailableError):
        runtime.load_instructions("synthetic-safe")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["write", "mcp_empty", "mcp_marker", "kb_scope", "kb_scope_off"])
async def test_router_independently_blocks_forged_dispatch(repository, case):
    """Ordinary tools remain usable while KB scope and MCP grants remain enforced."""
    from pydantic import PrivateAttr
    from src.agent.core.toolkit.tool_router import ToolRouter
    from src.agent.core.toolkit.tool_spec import ToolSpec

    executed = []
    audited = []

    class SpyTool(ToolSpec):
        name: str = "search_docs"
        description: str = "Synthetic scope spy"
        _kb_ids: list[str] | None = PrivateAttr(default=None)

        async def execute(self, args, ctx):
            executed.append(args)
            return {"output": "Synthetic result"}

    class CaptureAudit:
        def record(self, record):
            audited.append(record)

    runtime = build_skills_runtime("auto", [], repository=repository)
    spec = SpyTool()
    spec._kb_ids = ["synthetic-kb"]
    if case == "write":
        spec.name = "run_command"
    elif case == "mcp_empty":
        spec.mcp_info = {}
    elif case == "mcp_marker":
        spec.mcp_info = {"server_name": "synthetic-external"}
    elif case in {"kb_scope", "kb_scope_off"}:
        spec._kb_ids = ["outside-request"]
    ctx = ToolContext(
        permission_mode="bypassPermissions",
        skills_mode="off" if case == "kb_scope_off" else "auto",
        skills_runtime=runtime, skills_allowed_tools=frozenset({spec.name}),
        kb_scope=("synthetic-kb",),
    )
    result = await ToolRouter(CaptureAudit()).dispatch(
        "synthetic-forged-call", spec.name, {}, ctx,
        decision="allow", decision_source="user_allow", tool_spec=spec,
    )
    if case == "write":
        assert executed == [{}]
        assert result["success"] is True
    else:
        assert executed == []
        assert result["success"] is False
        if case in {"kb_scope", "kb_scope_off"}:
            assert result["error"]["error_type"] == "KB_SCOPE_MISMATCH"
        else:
            assert result["error"]["error_type"] == "PERMISSION_DENIED"
        assert len(audited) == 1
        assert audited[0].decision == "deny"


def test_metadata_free_legacy_is_available_manually_but_not_auto_selected(repository):
    legacy = repository.root / "synthetic-legacy"
    legacy.mkdir()
    (legacy / "skill.md").write_text("Synthetic legacy instructions.", encoding="utf-8")
    (legacy / ".enabled").write_text("", encoding="utf-8")
    automatic = build_skills_runtime("auto", [], repository=repository)
    assert [item["id"] for item in automatic.catalog()] == ["synthetic-safe"]
    manual = build_skills_runtime("manual", ["synthetic-legacy"], repository=repository)
    assert manual.load_instructions("synthetic-legacy")["instructions"] == "Synthetic legacy instructions."
