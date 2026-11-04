"""Persist tool audit outcomes through the real Router and SQLAlchemy logger."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models import ToolAudit
from src.agent import audit_logger
from src.agent.core.toolkit.audit_recorder import AuditRecorder
from src.agent.core.toolkit import tool_router
from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.hitl_handler import _audit_deny_decisions
from src.skills.errors import SkillUnavailableError


class _AuditProbeArgs(BaseModel):
    value: str


class _AuditProbeTool(ToolSpec):
    name: str = "audit_result_probe"
    description: str = "Synthetic tool for audit persistence tests"
    args_schema: type[BaseModel] = _AuditProbeArgs
    risk_level: RiskLevel = RiskLevel.HIGH
    outcome: str = "success"

    async def execute(self, args: dict, ctx: ToolContext) -> dict:
        if self.outcome == "structured_failure":
            raise SkillUnavailableError("Synthetic selected skill is disabled")
        if self.outcome == "exception":
            raise RuntimeError("Synthetic executor failure")
        return {"output": f"completed: {args['value']}"}


@pytest.fixture
def audit_session_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """Bind the real logger to a test-only in-memory SQLite database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session_factory = sessionmaker(autoflush=False, bind=engine)
    monkeypatch.setattr(audit_logger, "SessionLocal", test_session_factory)
    try:
        yield test_session_factory
    finally:
        engine.dispose()


def _only_audit_row(factory: sessionmaker[Session], session_id: str) -> ToolAudit:
    with factory() as db:
        row = db.query(ToolAudit).filter_by(session_id=session_id).one()
        db.expunge(row)
        return row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_success", "expected_error_type"),
    [
        ("success", True, None),
        ("structured_failure", False, "EXEC_ERROR"),
        ("exception", False, "EXEC_ERROR"),
    ],
)
async def test_router_persists_call_id_and_execution_outcome(
    audit_session_factory: sessionmaker[Session],
    outcome: str,
    expected_success: bool,
    expected_error_type: str | None,
) -> None:
    session_id = f"audit-{outcome}"
    call_id = f"call-{outcome}"
    tool = _AuditProbeTool(outcome=outcome)
    router = ToolRouter(AuditRecorder())

    result = await router.dispatch(
        call_id=call_id,
        tool_name=tool.name,
        args={"value": "synthetic"},
        ctx=ToolContext(session_id=session_id),
        tool_spec=tool,
    )

    assert result["success"] is expected_success
    if expected_error_type is None:
        assert result["error"] is None
    else:
        assert result["error"]["error_type"] == expected_error_type

    row = _only_audit_row(audit_session_factory, session_id)
    assert row.call_id == call_id
    assert row.decision == "allow"
    assert row.success is expected_success
    assert row.error_type == expected_error_type


def test_hitl_deny_persists_denial_separately_from_execution_success(
    audit_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _AuditProbeTool()
    monkeypatch.setattr(tool_router, "_TOOL_REGISTRY", {tool.name: tool})

    _audit_deny_decisions(
        {"configurable": {"thread_id": "audit-hitl-deny"}},
        [{"name": tool.name, "args": {"value": "denied"}, "call_id": "deny-call"}],
        "classifier_high",
    )

    row = _only_audit_row(audit_session_factory, "audit-hitl-deny")
    assert row.call_id == "deny-call"
    assert row.decision == "deny"
    assert row.decision_source == "classifier_high"
    assert row.success is False
    assert row.error_type == "PERMISSION_DENIED"


def test_legacy_log_tool_call_signature_keeps_existing_defaults(
    audit_session_factory: sessionmaker[Session],
) -> None:
    audit_logger.log_tool_call(
        session_id="audit-legacy-call",
        tool_name="run_command",
        args={"command": "synthetic"},
        decision="executed",
        decision_source="mode_default",
        risk_level="low",
        exit_code=0,
        duration_ms=12,
    )

    row = _only_audit_row(audit_session_factory, "audit-legacy-call")
    assert row.decision == "executed"
    assert row.call_id is None
    assert row.success is True
    assert row.error_type is None
