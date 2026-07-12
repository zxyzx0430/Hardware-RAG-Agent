"""v3-T7 verification: audit log write + 30-day cleanup + query filters.

Run from backend/ with:  python -m scripts.test_v3_t7_audit

Covers the audit-logging acceptance items from v3-T7:
  - Every tool call writes a SQLite row (log_tool_call)
  - query_logs filters by session_id / tool_name / decision / risk_level
  - cleanup_old_logs deletes records older than N days
"""
from __future__ import annotations

import datetime
import sys

sys.path.insert(0, ".")

from src.agent.audit_logger import cleanup_old_logs, log_tool_call, query_logs  # noqa: E402
from app.db.models import ToolAudit  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402


def _ok(name: str, cond: bool) -> None:
    print(f"  {name}: {'PASS' if cond else 'FAIL'}")
    if not cond:
        raise AssertionError(name)


def _seed(record: ToolAudit) -> None:
    db = SessionLocal()
    try:
        db.add(record)
        db.commit()
    finally:
        db.close()


def test_log_write() -> None:
    """log_tool_call writes a row that query_logs can read."""
    sid = "test-v3-t7-write"
    log_tool_call(
        session_id=sid, tool_name="run_command",
        args={"command": "python --version"}, decision="executed",
        decision_source="mode_default", risk_level="low",
        exit_code=0, duration_ms=120,
    )
    logs = query_logs(session_id=sid, limit=10)
    _ok("log write readable", len(logs) >= 1)
    latest = logs[-1]
    _ok("tool_name captured", latest["tool_name"] == "run_command")
    _ok("decision captured", latest["decision"] == "executed")
    _ok("risk_level captured", latest["risk_level"] == "low")
    _ok("exit_code captured", latest["exit_code"] == 0)


def test_query_filters() -> None:
    """query_logs honors tool_name / decision / risk_level filters."""
    sid = "test-v3-t7-filter"
    log_tool_call(session_id=sid, tool_name="read_file", args={"path": "a"},
                  decision="allow", decision_source="mode_bypass", risk_level="low")
    log_tool_call(session_id=sid, tool_name="run_command", args={"command": "rm"},
                  decision="ask", decision_source="classifier_high", risk_level="high")
    by_tool = query_logs(session_id=sid, tool_name="read_file")
    _ok("tool_name filter", all(r["tool_name"] == "read_file" for r in by_tool))
    by_decision = query_logs(session_id=sid, decision="ask")
    _ok("decision filter", all(r["decision"] == "ask" for r in by_decision))
    by_risk = query_logs(session_id=sid, risk_level="high")
    _ok("risk_level filter", all(r["risk_level"] == "high" for r in by_risk))


def test_cleanup_old_logs() -> None:
    """cleanup_old_logs deletes rows older than the cutoff."""
    sid = "test-v3-t7-cleanup"
    # Insert a row dated 40 days ago — beyond the 30-day default retention.
    old_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)
    _seed(ToolAudit(
        timestamp=old_ts, session_id=sid, tool_name="read_file",
        args_summary="{}", decision="allow", decision_source="mode_bypass",
        risk_level="low",
    ))
    before = query_logs(session_id=sid, limit=100)
    deleted = cleanup_old_logs(days=30)
    after = query_logs(session_id=sid, limit=100)
    _ok("cleanup deleted at least 1", deleted >= 1)
    _ok("cleanup reduced count", len(after) < len(before))


def main() -> None:
    print("v3-T7 audit log verification:")
    test_log_write()
    test_query_filters()
    test_cleanup_old_logs()
    print("All v3-T7 audit scenarios PASS")


if __name__ == "__main__":
    main()
