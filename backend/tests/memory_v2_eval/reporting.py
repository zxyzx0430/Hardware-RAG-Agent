"""Report rendering for the Memory v2 evaluation runner."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    return text.replace("|", "\\|")


def _gate_table(gates: list[dict[str, Any]] | None) -> list[str]:
    if not gates:
        return ["_No gates evaluated._"]
    rows = [
        "| gate | value | threshold | hard | status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for gate in gates:
        if gate.get("value") is None:
            value = "pending"
        else:
            value = gate["value"]
        rows.append(
            "| {name} | {value} | {op} {threshold} | {hard} | {status} |".format(
                name=_cell(gate.get("name")),
                value=_cell(value),
                op=_cell(gate.get("op")),
                threshold=_cell(gate.get("threshold")),
                hard="yes" if gate.get("hard") else "no",
                status=_cell(gate.get("status")),
            )
        )
    return rows


def _scenario_table(results: list[dict[str, Any]] | None) -> list[str]:
    if not results:
        return ["_No scenarios evaluated._"]
    rows = [
        "| scenario | category | status | checks |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        passed = sum(1 for check in result.get("checks", []) if check.get("ok"))
        total = len(result.get("checks", []))
        status = "pass" if result.get("passed") else "fail"
        rows.append(
            f"| {_cell(result.get('scenario_id'))} | {_cell(result.get('category'))} | "
            f"{status} | {passed}/{total} |"
        )
    return rows


def render_report(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Memory v2 evaluation report",
        "",
        f"- Generated: {payload.get('generated_at')}",
        f"- Verdict: **{payload.get('verdict')}**",
        f"- Repo: `{payload.get('environment', {}).get('repo', '')}`",
        f"- Branch: `{payload.get('git', {}).get('branch', '')}`",
        f"- Commit: `{payload.get('git', {}).get('commit', '')}`",
        f"- Dataset: `{payload.get('dataset', {}).get('path', '')}` "
        f"({payload.get('dataset', {}).get('count', 0)} scenarios)",
        f"- Python: `{payload.get('environment', {}).get('executable', '')}`",
        f"- TEMP: `{payload.get('environment', {}).get('temp', '')}`",
        f"- SQLite: `{payload.get('environment', {}).get('sqlite_db_path', '')}`",
        "",
        "## Checks",
        "",
        "| check | status | detail |",
        "| --- | --- | --- |",
    ]
    for check in payload.get("checks", []):
        lines.append(
            f"| {_cell(check.get('name'))} | {_cell(check.get('status'))} | "
            f"{_cell(check.get('detail'))} |"
        )

    lines.extend(["", "## Reference harness gates (not product acceptance)", ""])
    harness = payload.get("harness") or {}
    lines.extend(_gate_table(harness.get("metrics", {}).get("gates")))
    lines.extend(
        [
            "",
            "Metric definitions: precision/recall use scenario gold rows; retrieval "
            "Recall@5/Precision@5 use each query's explicit expected ids; irrelevant "
            "injection uses explicitly seeded noise ids; working retention counts "
            "gold constraints only; continuity is mean recall of cross-session queries.",
        ]
    )
    lines.extend(["", "### Reference scenarios", ""])
    lines.extend(_scenario_table(harness.get("scenarios")))

    lines.extend(["", "## Product gates", ""])
    product = payload.get("product") or {}
    if product.get("status") == "pending":
        lines.append(f"Pending: {_cell(product.get('reason'))}")
    elif product.get("status") == "fail":
        lines.append(f"Failed: {_cell(product.get('reason'))}")
    elif product.get("status") == "pass":
        lines.append("Product v2 evaluation passed every deterministic gate.")
    else:
        lines.append("No product evaluation was executed.")
    if product.get("metrics"):
        lines.extend(["", *_gate_table(product["metrics"].get("gates"))])
    if product.get("scenarios"):
        lines.extend(["", "### Product scenarios", ""])
        lines.extend(_scenario_table(product["scenarios"]))

    lines.extend(["", "## pytest", ""])
    pytest_summary = payload.get("pytest") or {}
    if pytest_summary.get("status") == "pass":
        lines.append(
            f"{pytest_summary.get('tests', 0)} tests, "
            f"{pytest_summary.get('failures', 0)} failed, "
            f"{pytest_summary.get('errors', 0)} errors, "
            f"{pytest_summary.get('skipped', 0)} skipped "
            f"(log: `{pytest_summary.get('log', '')}`)"
        )
    else:
        lines.append(
            f"Status: {pytest_summary.get('status', 'not-run')}; "
            f"detail: {pytest_summary.get('detail', '')}"
        )

    lines.extend(["", "## Frontend", ""])
    frontend = payload.get("frontend") or {}
    lines.append(f"Status: {frontend.get('status', 'not-requested')}")
    if frontend.get("detail"):
        lines.append(f"Detail: {frontend['detail']}")

    lines.extend(["", "## Real-model evaluation", ""])
    real_model = payload.get("real_model") or {}
    lines.append(f"Status: {real_model.get('status', 'not-requested')}")
    if real_model.get("detail"):
        lines.append(f"Detail: {real_model['detail']}")

    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- The reference harness proves scenario schema, policy expectations, metric "
            "math and report rendering. It is not product acceptance.",
            "- Product results are only claimed when the probe binds to a merged "
            "Memory v2 implementation and every hard/quality/cost gate passes.",
            "- TestClient/HTTP probes are not browser acceptance.",
            "- No LLM judge is part of the deterministic v2.0 gate.",
            "- Unrun or unbound checks are reported as pending/unverified, never as pass.",
            "",
            "## Evidence",
            "",
        ]
    )
    for name, path in (payload.get("evidence") or {}).items():
        lines.append(f"- {name}: `{path}`")
    lines.append("")
    return "\n".join(lines)


def write_report(work_dir: str | Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    metrics_path = directory / "metrics.json"
    report_path = directory / "report.md"
    metrics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path.write_text(render_report(payload), encoding="utf-8")
    return report_path, metrics_path


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["render_report", "utc_timestamp", "write_report"]
