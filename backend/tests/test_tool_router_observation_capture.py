"""In-memory tests for ToolRouter result observation capture.

These tests cover the router's persistence seam, not tool permission or HITL
authorization. The archive function is replaced so no database is touched.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.api import tool_observations
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import ToolSpec


REFERENCE = "r" * 43


class _SyntheticArgs(BaseModel):
    value: str


class _SyntheticTool(ToolSpec):
    name: str = "synthetic_observation"
    description: str = "Return a deterministic in-memory result for router tests."
    args_schema: type[BaseModel] = _SyntheticArgs

    async def execute(self, args, ctx):
        return {
            "output": f"synthetic output: {args['value']}",
            "details": {"full_result_marker": "result body retained", "count": 3},
        }


class _ImportSkillTool(_SyntheticTool):
    name: str = "import_skill"


class _PreviewSkillImportTool(_SyntheticTool):
    name: str = "preview_skill_import"


class _ReadToolObservationTool(_SyntheticTool):
    name: str = "read_tool_observation"


class _MemoryAuditRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def _scope():
    return tool_observations.make_tool_observation_scope(
        principal_id="a" * 64,
        session_id="session-1",
        request_id="c" * 32,
        session_delete_epoch="",
    )


def _context(*, include_scope=True):
    context = SimpleNamespace(mcp_authorization=None, kb_scope=None)
    if include_scope:
        context.tool_observation_scope = _scope()
    return context


def _patch_archive(monkeypatch, return_value):
    calls = []

    def archive(canonical_envelope, tool_name, scope):
        calls.append((canonical_envelope, tool_name, scope))
        return return_value

    monkeypatch.setattr(tool_observations, "archive_tool_observation", archive)
    return calls


def _dispatch(spec, context):
    router = ToolRouter(_MemoryAuditRecorder())
    return asyncio.run(
        router.dispatch(
            call_id="router-call-1",
            tool_name=spec.name,
            args={"value": "sample"},
            ctx=context,
            tool_spec=spec,
        )
    )


def test_dispatch_archives_complete_canonical_result_only_with_host_scope(
    monkeypatch,
):
    calls = _patch_archive(monkeypatch, REFERENCE)
    context = _context()
    spec = _SyntheticTool()

    result = _dispatch(spec, context)

    assert len(calls) == 1
    canonical_envelope, archived_tool_name, archived_scope = calls[0]
    assert archived_tool_name == spec.name
    assert archived_scope is context.tool_observation_scope

    persisted = json.loads(canonical_envelope)
    assert set(persisted) == {"success", "output", "data", "error", "metadata"}
    assert persisted["success"] is True
    assert persisted["output"] == "synthetic output: sample"
    assert persisted["data"] == {
        "details": {"full_result_marker": "result body retained", "count": 3}
    }
    assert persisted["error"] is None
    assert persisted["metadata"]["tool_name"] == spec.name
    assert persisted["metadata"]["call_id"] == "router-call-1"
    assert "hwrag_tool_observation_archived" not in persisted["metadata"]
    assert "hwrag_tool_observation_ref" not in persisted["metadata"]
    assert json.dumps(
        persisted,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ) == canonical_envelope

    assert result["output"] == "synthetic output: sample"
    assert result["data"] == persisted["data"]
    assert result["metadata"]["hwrag_tool_observation_archived"] is True
    assert result["metadata"]["hwrag_tool_observation_ref"] == REFERENCE


def test_dispatch_without_host_scope_does_not_archive_or_add_reference(monkeypatch):
    calls = _patch_archive(monkeypatch, REFERENCE)

    result = _dispatch(_SyntheticTool(), _context(include_scope=False))

    assert calls == []
    assert result["success"] is True
    assert result["output"] == "synthetic output: sample"
    assert result["metadata"]["hwrag_tool_observation_archived"] is False
    assert "hwrag_tool_observation_ref" not in result["metadata"]


@pytest.mark.parametrize(
    "tool_type",
    [_ImportSkillTool, _PreviewSkillImportTool, _ReadToolObservationTool],
    ids=["import-skill", "preview-skill-import", "read-tool-observation"],
)
def test_dispatch_does_not_archive_explicitly_excluded_tools(monkeypatch, tool_type):
    calls = _patch_archive(monkeypatch, REFERENCE)

    result = _dispatch(tool_type(), _context())

    assert calls == []
    assert result["success"] is True
    assert result["output"] == "synthetic output: sample"
    assert result["metadata"]["hwrag_tool_observation_archived"] is False
    assert "hwrag_tool_observation_ref" not in result["metadata"]


def test_dispatch_keeps_normal_result_when_archive_store_is_unavailable(monkeypatch):
    calls = _patch_archive(monkeypatch, None)

    result = _dispatch(_SyntheticTool(), _context())

    assert len(calls) == 1
    assert result["success"] is True
    assert result["output"] == "synthetic output: sample"
    assert result["data"] == {
        "details": {"full_result_marker": "result body retained", "count": 3}
    }
    assert result["metadata"]["hwrag_tool_observation_archived"] is False
    assert "hwrag_tool_observation_ref" not in result["metadata"]
