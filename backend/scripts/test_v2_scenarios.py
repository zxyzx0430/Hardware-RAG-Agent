"""v2-T6 permission logic scenarios — adapted to PermissionClassifier (v2 §2).

Replaces the legacy permission_gate.check_permission API (removed in v2 §2)
with PermissionClassifier.check(spec, args, ctx). Expectations updated to
match v2 semantics:
  * S10 'python --version' -> ask: python is NOT in LOW_RISK_PREFIXES, so
    classify_risk grades it MEDIUM and _decide_high returns ASK.
  * S12 read_file '.git/config' under bypassPermissions -> allow: bypass
    mode is allow-all (spec §2.3). read_file is LOW so it also auto-allows
    in default; path_guard no longer screens LOW tools (spec §2.3 A2).

Live classifier: src.agent.core.toolkit.permission_classifier.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.agent.core.toolkit.permission_classifier import PermissionClassifier
from src.agent.core.toolkit.tool_spec import RiskLevel

# Tool name -> static risk level (mirrors tool registry spec).
_TOOL_RISK: dict[str, RiskLevel] = {
    "read_file": RiskLevel.LOW,
    "write_file": RiskLevel.MEDIUM,
    "edit_file": RiskLevel.MEDIUM,
    "run_command": RiskLevel.HIGH,
}

_SCENARIOS: list[tuple[str, str, dict[str, Any], str, str]] = [
    ("S7 read_file acceptEdits", "read_file", {"path": "backend/main.py"}, "acceptEdits", "allow"),
    ("S8 write_file acceptEdits", "write_file", {"path": "sandbox_workspace/test.py", "content": "print(1)"}, "acceptEdits", "allow"),
    ("S9 edit_file default", "edit_file", {"path": "backend/main.py"}, "default", "ask"),
    ("S10 python --version acceptEdits", "run_command", {"command": "python --version"}, "acceptEdits", "ask"),
    ("S11 rm -rf acceptEdits", "run_command", {"command": "rm -rf /tmp/x"}, "acceptEdits", "ask"),
    ("S12 .git/config bypass allow", "read_file", {"path": ".git/config"}, "bypassPermissions", "allow"),
    ("S13 echo bypass allow", "run_command", {"command": "echo hello"}, "bypassPermissions", "allow"),
]


def run_scenario(name: str, tool: str, args: dict[str, Any], mode: str, expected: str) -> bool:
    """Run one scenario through PermissionClassifier; print PASS/FAIL."""
    spec = SimpleNamespace(name=tool, risk_level=_TOOL_RISK[tool])
    ctx = SimpleNamespace(permission_mode=mode)
    actual = PermissionClassifier().check(spec, args, ctx)
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: got={actual} want={expected}")
    return ok


def main() -> None:
    """Run all scenarios; exit non-zero if any fail."""
    passed = sum(1 for s in _SCENARIOS if run_scenario(*s))
    total = len(_SCENARIOS)
    print(f"\n{passed}/{total} scenarios passed")
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
