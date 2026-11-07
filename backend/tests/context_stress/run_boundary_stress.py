"""Boundary stress: fill the context budget to and beyond its limit.

Unlike run_stress.py (tool-output accumulation capped by per-call budgets),
this harness drives the shared budget gates themselves at 90/99/99.9/100/
100.1/110/130 % of each window's guard limit, plus the final wire-payload
check. A failure must be a typed ContextLimitError / AgentRequestBudgetError
with a clean SSE payload -- never an uncaught exception or corrupted counter.

Run:
    D:/python/python.exe backend/tests/context_stress/run_boundary_stress.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))

from src.agent import context_guard  # noqa: E402
from src.agent.context_guard import (  # noqa: E402
    context_limit_event,
    estimate_context_tokens,
    resolve_context_budget,
)
from src.agent.exceptions import ContextLimitError  # noqa: E402
from src.agent.request_budget import (  # noqa: E402
    AgentRequestBudgetError,
    estimate_payload_tokens,
    validate_agent_request_budget,
)
from src.llm.client import LLMClient  # noqa: E402

WINDOWS = [131_072, 200_000, 206_233, 262_144]
GUARD_RATIOS = [0.5, 0.9, 0.99, 0.999, 1.0, 1.001, 1.1, 1.3]
WIRE_MODEL = "claude-3-5-sonnet"  # registered 200k window
WIRE_RATIOS = [0.5, 0.9, 0.99, 0.999, 1.0, 1.001, 1.1]
TOOL_OUTPUT_TOKENS = 4_700  # measured per-call cap in the existing stress run
TOOL_FILL_TARGET_RATIO = 0.999


def _chars_per_token() -> float:
    sample = "a" * 20_000
    tokens = LLMClient._estimate_tokens(sample)
    if tokens <= 0:
        raise AssertionError("token estimator returned no tokens")
    return len(sample) / tokens


def _text_for_tokens(target: int, chars_per_token: float) -> str:
    return "a" * max(1, int(target * chars_per_token))


def _text_for_wire_cost(target_bytes: int) -> str:
    # A JSON string costs roughly four bytes per text token; calibrate the
    # serialized payload instead of trusting the ratio for the exact gate.
    low, high = 0, target_bytes
    while low < high:
        mid = (low + high + 1) // 2
        payload = {"model": WIRE_MODEL, "messages": [{"role": "user", "content": "a" * mid}]}
        cost = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if cost <= target_bytes:
            low = mid
        else:
            high = mid - 1
    return "a" * max(1, low)


def _guard_probe(window: int, ratio: float, chars_per_token: float) -> dict:
    budget = resolve_context_budget(None, window, 4096)
    limit = budget.token_limit
    target = int(limit * ratio)
    text = _text_for_tokens(target, chars_per_token)
    context_guard.reset_token_counter(0)
    context_guard.accumulate_tokens(text, {"token_limit": limit})
    cumulative = context_guard._CUMULATIVE_TOKENS.get()
    over = cumulative >= limit
    error_event = None
    if over:
        error_event = context_limit_event(
            ContextLimitError(cumulative=cumulative, limit=limit),
        )
        if "CONTEXT_LIMIT" not in error_event:
            raise AssertionError("CONTEXT_LIMIT event missing from overflow payload")
    # A following small request must still work after an overflow probe.
    context_guard.reset_token_counter(0)
    context_guard.accumulate_tokens("small follow-up", {"token_limit": limit})
    after = context_guard._CUMULATIVE_TOKENS.get()
    if after <= 0 or after >= limit:
        raise AssertionError("counter did not recover after an overflow probe")
    return {
        "ratio": ratio,
        "window": window,
        "token_limit": limit,
        "target_tokens": target,
        "actual_tokens": cumulative,
        "actual_ratio": round(cumulative / limit, 4),
        "over_limit": over,
        "event": "CONTEXT_LIMIT" if over else None,
    }


def _estimate_probe(window: int, ratio: float, chars_per_token: float) -> dict:
    budget = resolve_context_budget(None, window, 4096)
    limit = budget.token_limit
    text = _text_for_tokens(int(limit * ratio), chars_per_token)
    messages = [{"role": "user", "content": text}]
    estimated = estimate_context_tokens(messages)
    return {
        "ratio": ratio,
        "token_limit": limit,
        "estimated_tokens": estimated,
        "actual_ratio": round(estimated / limit, 4),
        "over_limit": estimated >= limit,
    }


def _wire_probe(ratio: float) -> dict:
    budget = resolve_context_budget(WIRE_MODEL)
    window = budget.effective_window
    reserve = budget.output_tokens
    target_cost = int((window * ratio) - 256 - reserve)
    text = _text_for_wire_cost(max(1, target_cost))
    payload = {"model": WIRE_MODEL, "messages": [{"role": "user", "content": text}]}
    cost = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    refused = False
    try:
        validate_agent_request_budget(payload)
    except AgentRequestBudgetError:
        refused = True
    return {
        "ratio": ratio,
        "window": window,
        "output_reserve": reserve,
        "payload_bytes": cost,
        "refused": refused,
        "expected_refused": cost + 256 + reserve > window,
    }


def _tool_fill_probe(window: int) -> dict:
    budget = resolve_context_budget(None, window, 4096)
    limit = budget.token_limit
    target = int(limit * TOOL_FILL_TARGET_RATIO)
    context_guard.reset_token_counter(0)
    calls = 0
    cumulative = 0
    while cumulative < target:
        envelope = {
            "success": True,
            "output": "x" * (TOOL_OUTPUT_TOKENS * 4),
            "metadata": {"tool_name": "read_file"},
        }
        context_guard.accumulate_tokens(envelope, {"token_limit": limit})
        cumulative = context_guard._CUMULATIVE_TOKENS.get()
        calls += 1
        if calls > 200:
            raise AssertionError("tool fill did not reach the guard limit")
    before_overflow = cumulative
    # One more real-sized output crosses the guard; the request must stop with
    # a typed error while the accumulated counter stays readable.
    context_guard.accumulate_tokens(
        {"success": True, "output": "y" * (TOOL_OUTPUT_TOKENS * 4)},
        {"token_limit": limit},
    )
    overflow = context_guard._CUMULATIVE_TOKENS.get()
    event = context_limit_event(ContextLimitError(cumulative=overflow, limit=limit))
    return {
        "window": window,
        "token_limit": limit,
        "calls": calls,
        "before_overflow": before_overflow,
        "before_ratio": round(before_overflow / limit, 4),
        "after_overflow": overflow,
        "after_ratio": round(overflow / limit, 4),
        "event": "CONTEXT_LIMIT" if "CONTEXT_LIMIT" in event else None,
    }


def main() -> int:
    started = time.monotonic()
    chars_per_token = _chars_per_token()
    report = {
        "chars_per_token": round(chars_per_token, 4),
        "windows": [],
        "wire": [],
        "tool_fill": [],
        "failures": [],
    }

    for window in WINDOWS:
        guard_results = []
        estimate_results = []
        for ratio in GUARD_RATIOS:
            try:
                guard_results.append(_guard_probe(window, ratio, chars_per_token))
                estimate_results.append(_estimate_probe(window, ratio, chars_per_token))
            except Exception as exc:  # noqa: BLE001 - report all boundary failures
                report["failures"].append({
                    "window": window, "ratio": ratio, "error": repr(exc),
                })
        for row in guard_results:
            expected = row["ratio"] >= 1.0
            if row["over_limit"] != expected:
                report["failures"].append({
                    "window": window, "type": "guard_threshold",
                    "ratio": row["ratio"], "actual_ratio": row["actual_ratio"],
                })
        report["windows"].append({
            "window": window,
            "token_limit": guard_results[0]["token_limit"] if guard_results else None,
            "guard": guard_results,
            "estimate": estimate_results,
        })
        try:
            report["tool_fill"].append(_tool_fill_probe(window))
        except Exception as exc:  # noqa: BLE001
            report["failures"].append({"window": window, "type": "tool_fill", "error": repr(exc)})

    for ratio in WIRE_RATIOS:
        try:
            row = _wire_probe(ratio)
            report["wire"].append(row)
            if row["refused"] != row["expected_refused"]:
                report["failures"].append({"type": "wire_threshold", **row})
        except Exception as exc:  # noqa: BLE001
            report["failures"].append({"type": "wire", "ratio": ratio, "error": repr(exc)})

    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    out = Path(__file__).resolve().parent / "boundary-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("FAILURES", len(report["failures"]))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
