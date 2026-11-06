"""5 x 200 tool-call context stress harness (1000 real tool invocations).

Run:
    D:/python/python.exe backend/tests/context_stress/run_stress.py

Every run drives real ToolRouter dispatches against the stress project. Each
run performs 20 turns x 10 calls = 200 executions and additionally a 30-call
single-turn guard probe (refusals expected after the budget is spent). Per-call
output caps and per-turn accumulated context are asserted on every call.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))

from src.agent import context_guard  # noqa: E402
from src.agent.context_guard import resolve_context_budget  # noqa: E402
from src.agent.core.toolkit import tool_router as tool_router_module  # noqa: E402
from src.agent.core.toolkit.tool_output_budget import (  # noqa: E402
    PLAN_TOOL_OUTPUT_TOKEN_BUDGET,
    PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP,
)
from src.agent.core.toolkit.tool_router import ToolRouter  # noqa: E402
from src.agent.exceptions import ToolContext  # noqa: E402
from src.agent.request_budget import estimate_payload_tokens  # noqa: E402
from src.agent.tools.groups.file_ops import glob as glob_mod  # noqa: E402
from src.agent.tools.groups.file_ops import grep as grep_mod  # noqa: E402
from src.agent.tools.groups.file_ops import list_files as list_mod  # noqa: E402
from src.agent.tools.groups.file_ops import read_file as read_mod  # noqa: E402

from build_stress_project import PROJECT, ROOT, build  # noqa: E402

RUNS = 5
REQUIRED_EXECUTIONS_PER_RUN = 200
TURNS_CAP = 120
CALLS_PER_TURN = 6
GUARD_PROBE_CALLS = 30
WINDOWS = [32_768, 65_536, 131_072, 206_233, 262_144]
OUTPUT_TOLERANCE = 400  # metadata/compaction note overhead

for module in (list_mod, glob_mod, grep_mod, read_mod):
    module.validate_path = lambda path, is_write: (True, "")
for module in (list_mod, glob_mod, grep_mod):
    module.is_path_allowed_for_tree = lambda path: True
read_mod.resolve_agent_path = lambda path: path


class _Recorder:
    def record(self, record):
        return None


tool_router_module._archive_tool_observation = (
    lambda envelope, spec, ctx: None
)


def _deepest_dir() -> Path:
    path = PROJECT / "deep"
    while path.is_dir() and any(p.is_dir() for p in path.iterdir()):
        path = next(p for p in path.iterdir() if p.is_dir())
    return path


def _make_call(rng: random.Random, large: bool) -> tuple[str, dict]:
    if not large:
        kind = rng.choice(["small_read", "small_glob", "small_grep", "small_list"])
        if kind == "small_read":
            return "read_file", {
                "path": str(PROJECT / "README.md"), "limit": 2,
            }
        if kind == "small_glob":
            return "glob", {
                "pattern": rng.choice(["src/*.py", "docs/*.txt", "*.md"]),
                "path": str(PROJECT), "max_results": 2,
            }
        if kind == "small_grep":
            return "grep", {
                "pattern": "needle",
                "path": str(rng.choice([PROJECT / "docs", PROJECT / "src"])),
                "max_results": 2,
            }
        return "list_files", {
            "path": str(PROJECT / "docs"), "max_depth": 1,
        }
    kind = rng.choices(
        ["list_files", "glob", "grep", "read_file"],
        weights=[30, 25, 25, 20],
    )[0]
    if kind == "list_files":
        path = rng.choice([
            PROJECT, PROJECT / "src", PROJECT / "docs",
            PROJECT / "node_modules", PROJECT / "deep", PROJECT / "unicode_目录",
            _deepest_dir(), PROJECT / "missing-dir",
        ])
        return kind, {
            "path": str(path),
            "max_depth": rng.choice([1, 2, 3, 5, 6]),
            "include_ignored": rng.random() < 0.15,
        }
    if kind == "glob":
        return kind, {
            "pattern": rng.choice([
                "**/*.py", "**/*.md", "*", "src/**/*.txt",
                "node_modules/**/*.js", "**/*.bin", "**/*.rs",
                "deep/**/leaf.txt",
            ]),
            "path": str(rng.choice([PROJECT, PROJECT / "src", PROJECT / "docs"])),
            "max_results": rng.choice([5, 20, 50, 100]),
            "include_ignored": rng.random() < 0.15,
        }
    if kind == "grep":
        return kind, {
            "pattern": rng.choice(["needle", "def ", "TODO", "[", "(?i)needle"]),
            "path": str(rng.choice([
                PROJECT, PROJECT / "src", PROJECT / "docs",
                PROJECT / "node_modules", PROJECT / "big_1mb.txt",
                PROJECT / "long_line.txt", PROJECT / "binary.bin",
            ])),
            "max_results": rng.choice([5, 20, 50, 100]),
            "include_ignored": rng.random() < 0.10,
        }
    return kind, {
        "path": str(rng.choice([
            PROJECT / "README.md", PROJECT / "big_1mb.txt",
            PROJECT / "big_10mb.txt", PROJECT / "long_line.txt",
            PROJECT / "binary.bin", PROJECT / "nul_binary.bin",
            PROJECT / "unicode_目录" / "文件-测试.md",
            PROJECT / "missing.txt", PROJECT / "denied" / "secret.txt",
        ])),
        "offset": rng.choice([0, 0, 100, 10_000]),
        "limit": rng.choice([1, 10, 2_000, 5_000]),
    }


def _data_count(result: dict, key: str) -> int:
    data = result.get("data") or {}
    value = data.get(key)
    if isinstance(value, int):
        return value
    return len(data.get("matches") or [])


def _measure(result: dict) -> tuple[int, int]:
    tokens = estimate_payload_tokens(result)
    chars = len(json.dumps(result, ensure_ascii=False))
    return tokens, chars


async def _dispatch(
    router: ToolRouter,
    specs: dict,
    name: str,
    args: dict,
    ctx: ToolContext,
    call_id: str,
    *,
    assert_plan_cap: bool = True,
) -> tuple[dict, int, int]:
    result = await router.dispatch(call_id, name, args, ctx, tool_spec=specs[name])
    tokens, chars = _measure(result)
    if assert_plan_cap and tokens > PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP + OUTPUT_TOLERANCE:
        raise AssertionError(
            f"{name} exceeded the per-call output cap: {tokens} tokens"
        )
    return result, tokens, chars


async def _run_one(run_index: int, metrics: dict) -> dict:
    rng = random.Random(20261107 + run_index)
    token_limit = resolve_context_budget(None, WINDOWS[run_index], 4096).token_limit
    router = ToolRouter(_Recorder())
    specs = {
        "list_files": list_mod.ListFilesTool(),
        "glob": glob_mod.GlobTool(),
        "grep": grep_mod.GrepTool(),
        "read_file": read_mod.ReadFileTool(),
    }
    result = {
        "run": run_index + 1,
        "window": WINDOWS[run_index],
        "token_limit": token_limit,
        "tool_calls": 0,
        "dispatches": 0,
        "turns": 0,
        "max_context_tokens": 0,
        "max_context_ratio": 0.0,
        "largest_output_tokens": 0,
        "largest_output_chars": 0,
        "largest_output_tool": "",
        "budget_limits": set(),
        "errors": [],
    }
    started = time.monotonic()

    executed = 0
    dispatches = 0
    turn = 0
    while executed < REQUIRED_EXECUTIONS_PER_RUN and turn < TURNS_CAP:
        large_turn = (turn % 5 == 0)
        ctx = ToolContext(permission_mode="plan", context_token_limit=token_limit)
        context_guard.reset_token_counter(0)
        turn_tokens = 0
        for call in range(CALLS_PER_TURN):
            name, args = _make_call(rng, large=large_turn and call == 0)
            call_id = f"run{run_index}-turn{turn}-call{call}"
            data, tokens, chars = await _dispatch(
                router, specs, name, args, ctx, call_id,
            )
            dispatches += 1
            if not data.get("metadata", {}).get("plan_tool_budget_exhausted"):
                executed += 1
            turn_tokens += tokens
            context_guard.accumulate_tokens(
                data, {"token_limit": token_limit},
            )
            budget = ctx.plan_tool_output_budget
            result["budget_limits"].add(budget.limit_tokens)
            if budget.spent_tokens > budget.limit_tokens:
                raise AssertionError(
                    f"run {run_index + 1} turn {turn}: budget overspend "
                    f"{budget.spent_tokens} > {budget.limit_tokens}"
                )
            if tokens > result["largest_output_tokens"]:
                result["largest_output_tokens"] = tokens
                result["largest_output_chars"] = chars
                result["largest_output_tool"] = name
            if chars > 500_000:
                raise AssertionError(f"{name} returned {chars} chars")
        counter = context_guard._CUMULATIVE_TOKENS.get()
        if counter > token_limit:
            raise AssertionError(
                f"run {run_index + 1} turn {turn}: context {counter} > {token_limit}"
            )
        if turn_tokens > result["max_context_tokens"]:
            result["max_context_tokens"] = turn_tokens
            result["max_context_ratio"] = round(turn_tokens / token_limit, 3)
        turn += 1
    if executed < REQUIRED_EXECUTIONS_PER_RUN:
        raise AssertionError(
            f"run {run_index + 1}: only {executed} executions in {turn} turns"
        )
    result["tool_calls"] = executed
    result["dispatches"] = dispatches
    result["turns"] = turn

    # Extra single-turn guard probe: large scans until the budget refuses.
    probe_ctx = ToolContext(permission_mode="plan", context_token_limit=token_limit)
    context_guard.reset_token_counter(0)
    probe_executed = 0
    refused = 0
    probe_tokens = 0
    for call in range(GUARD_PROBE_CALLS):
        name, args = rng.choice([
            ("list_files", {"path": str(PROJECT), "max_depth": 6}),
            ("glob", {"pattern": "**/*.py", "path": str(PROJECT), "max_results": 100}),
            ("grep", {"pattern": "needle", "path": str(PROJECT), "max_results": 100}),
            ("read_file", {"path": str(PROJECT / "big_10mb.txt"), "limit": 5_000}),
        ])
        data, tokens, _ = await _dispatch(
            router, specs, name, args, probe_ctx, f"run{run_index}-probe{call}",
        )
        probe_tokens += tokens
        context_guard.accumulate_tokens(data, {"token_limit": token_limit})
        if data.get("metadata", {}).get("plan_tool_budget_exhausted"):
            refused += 1
        else:
            probe_executed += 1
    counter = context_guard._CUMULATIVE_TOKENS.get()
    if counter > token_limit:
        raise AssertionError(
            f"run {run_index + 1} guard probe: context {counter} > {token_limit}"
        )
    if refused == 0:
        raise AssertionError("guard probe never refused a tool call")
    result["guard_probe"] = {
        "calls": GUARD_PROBE_CALLS,
        "executed": probe_executed,
        "refused": refused,
        "context_tokens": counter,
        "context_ratio": round(counter / token_limit, 3),
    }
    result["max_context_tokens"] = max(
        result["max_context_tokens"], counter,
    )
    result["max_context_ratio"] = round(
        result["max_context_tokens"] / token_limit, 3,
    )
    result["budget_limits"] = sorted(result["budget_limits"])

    # Escape hatches still work.
    escaped_glob, _, _ = await _dispatch(
        router, specs, "glob",
        {"pattern": "node_modules/**/*.js", "path": str(PROJECT)},
        ToolContext(permission_mode="default"), f"run{run_index}-escape-glob",
        assert_plan_cap=False,
    )
    listed, _, _ = await _dispatch(
        router, specs, "list_files",
        {"path": str(PROJECT), "include_ignored": True, "max_depth": 2},
        ToolContext(permission_mode="default"), f"run{run_index}-escape-list",
        assert_plan_cap=False,
    )
    grepped, _, _ = await _dispatch(
        router, specs, "grep",
        {"pattern": "needle", "path": str(PROJECT / "node_modules")},
        ToolContext(permission_mode="default"), f"run{run_index}-escape-grep",
        assert_plan_cap=False,
    )
    big_read, _, _ = await _dispatch(
        router, specs, "read_file",
        {"path": str(PROJECT / "big_1mb.txt"), "limit": 5_000},
        ToolContext(permission_mode="default"), f"run{run_index}-escape-read",
        assert_plan_cap=False,
    )
    result["escape_hatches"] = {
        "explicit_ignored_glob": _data_count(escaped_glob, "count"),
        "include_ignored_list": "node_modules" in json.dumps(listed),
        "explicit_ignored_grep": _data_count(grepped, "count"),
        "big_file_read_chars": len(big_read.get("output", "")),
    }
    if (
        result["escape_hatches"]["explicit_ignored_glob"] < 1
        or not result["escape_hatches"]["include_ignored_list"]
        or result["escape_hatches"]["explicit_ignored_grep"] < 1
        or len(big_read.get("output", "")) < 1_000
    ):
        raise AssertionError(f"escape hatch regression: {result['escape_hatches']}")

    # Bounded, clear error paths.
    errors = {}
    for label, name, args in (
        ("binary", "read_file", {"path": str(PROJECT / "binary.bin")}),
        ("nul_binary", "read_file", {"path": str(PROJECT / "nul_binary.bin")}),
        ("missing", "read_file", {"path": str(PROJECT / "missing.txt")}),
        ("denied", "read_file", {"path": str(PROJECT / "denied" / "secret.txt")}),
        ("missing_list", "list_files", {"path": str(PROJECT / "missing-dir")}),
    ):
        data, tokens, chars = await _dispatch(
            router, specs, name, args,
            ToolContext(permission_mode="default"), f"run{run_index}-err-{label}",
        )
        text = json.dumps(data, ensure_ascii=False)
        if tokens > 500 or chars > 5_000:
            raise AssertionError(f"{label} error output is unbounded: {tokens}")
        errors[label] = "binary file" in text or "not found" in text or "permission" in text or "Error" in text
    result["error_paths_bounded"] = errors

    # Junction loop must not duplicate matches.
    dup_glob, _, _ = await _dispatch(
        router, specs, "glob",
        {"pattern": "**/*.txt", "path": str(PROJECT), "max_results": 100},
        ToolContext(permission_mode="default"), f"run{run_index}-junction-glob",
    )
    paths = [
        match["path"]
        for match in ((dup_glob.get("data") or {}).get("matches") or [])
    ]
    if not paths:
        raise AssertionError("junction glob returned no matches")
    if len(paths) != len(set(paths)):
        raise AssertionError("junction loop produced duplicate glob matches")
    result["junction_unique_matches"] = len(paths)

    # Direct cap stressor: a synthetic large-output envelope must be compacted.
    from src.agent.core.toolkit.tool_output_budget import apply_plan_output_budget

    stress_ctx = ToolContext(permission_mode="plan", context_token_limit=token_limit)
    huge = {
        "success": True,
        "output": "x" * 200_000,
        "data": {"payload": "y" * 200_000, "items": ["z" * 5_000] * 50},
        "error": None,
        "metadata": {"tool_name": "search_docs"},
    }
    compacted = apply_plan_output_budget(huge, "search_docs", stress_ctx)
    cap_tokens = estimate_payload_tokens(compacted)
    if cap_tokens > PLAN_TOOL_SINGLE_OUTPUT_TOKEN_CAP + OUTPUT_TOLERANCE:
        raise AssertionError(f"cap stressor exceeded the cap: {cap_tokens}")
    result["cap_stressor_tokens"] = cap_tokens

    result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    result["executed_tool_calls"] = result["tool_calls"] + probe_executed
    return result


async def main() -> int:
    status = build()
    print("PROJECT", json.dumps(status, ensure_ascii=False))
    metrics = {"project": status, "runs": [], "total_tool_calls": 0}
    failures = []
    for run_index in range(RUNS):
        try:
            run = await _run_one(run_index, metrics)
        except Exception as exc:  # noqa: BLE001 - report every stress failure
            failures.append({"run": run_index + 1, "error": repr(exc)})
            print("RUN_FAILED", run_index + 1, repr(exc))
            continue
        metrics["runs"].append(run)
        metrics["total_tool_calls"] += run["executed_tool_calls"]
        print("RUN", json.dumps(run, ensure_ascii=False))
    metrics["total_required_calls"] = RUNS * REQUIRED_EXECUTIONS_PER_RUN
    metrics["failures"] = failures
    (ROOT / "stress-report.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print("TOTAL_EXECUTED", metrics["total_tool_calls"])
    print("FAILURES", len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
