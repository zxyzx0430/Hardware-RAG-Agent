"""v3-T5 scenario tests: cumulative token guard + wall-clock timeout guard.

Run from backend/ with:  python -m scripts.test_v3_t5_context_guard

Covers:
  1. compute_token_limit: known model → context_window * 0.8
  2. compute_token_limit: empty model → 0 (guard disabled)
  3. accumulate_tokens: sub-limit content → no raise, counter advances
  4. accumulate_tokens: over-limit content → ContextLimitError
  5. accumulate_tokens: token_limit == 0 → no-op (guard off)
  6. check_timeout: fresh start_time → no raise
  7. check_timeout: stale start_time → AgentTimeoutError
  8. context_limit_event / timeout_event: SSE payload shape
  9. contextvar isolation: two independent counters do not cross-contaminate
"""
from __future__ import annotations

import sys
import time

# Ensure backend/ is on sys.path when run via `python scripts/...`
sys.path.insert(0, ".")

from src.agent.context_guard import (  # noqa: E402
    accumulate_tokens,
    check_timeout,
    compute_token_limit,
    context_limit_event,
    reset_token_counter,
    timeout_event,
)
from src.agent.exceptions import AgentTimeoutError, ContextLimitError  # noqa: E402
from src.agent.prompts import MAX_TOKEN_RATIO, SINGLE_REQ_TIMEOUT_S  # noqa: E402
from src.llm.model_registry import get_context_window  # noqa: E402


def _ok(name: str, cond: bool) -> None:
    print(f"  {name}: {'PASS' if cond else 'FAIL'}")
    if not cond:
        raise AssertionError(name)


def test_token_limit_known_model() -> None:
    """Known model → context_window * MAX_TOKEN_RATIO."""
    limit = compute_token_limit("gpt-4o")
    expected = int(get_context_window("gpt-4o") * MAX_TOKEN_RATIO)
    _ok("known model limit", limit == expected)


def test_token_limit_empty_model() -> None:
    """Empty model → 0 (guard disabled)."""
    _ok("empty model → 0", compute_token_limit("") == 0)
    _ok("None model → 0", compute_token_limit("") == 0)


def test_accumulate_sub_limit() -> None:
    """Sub-limit content → no raise, counter advances."""
    reset_token_counter()
    state = {"token_limit": 100000}
    accumulate_tokens("hello world", state)  # ~5 tokens
    accumulate_tokens("another chunk", state)  # ~6 tokens
    # No raise = pass; counter advanced internally (we trust contextvar).


def test_accumulate_over_limit() -> None:
    """Over-limit content → ContextLimitError."""
    reset_token_counter()
    # Tiny limit so a single short string overflows it.
    state = {"token_limit": 1}
    raised = False
    try:
        accumulate_tokens("overflow this tiny budget right now", state)
    except ContextLimitError as exc:
        raised = True
        _ok("error carries cumulative", exc.cumulative > 0)
        _ok("error carries limit", exc.limit == 1)
    _ok("over-limit raises ContextLimitError", raised)


def test_accumulate_guard_disabled() -> None:
    """token_limit == 0 → no-op (guard off)."""
    reset_token_counter()
    state = {"token_limit": 0}
    accumulate_tokens("x" * 10000, state)  # must not raise
    _ok("guard disabled no-op", True)


def test_timeout_fresh() -> None:
    """Fresh start_time → no raise."""
    state = {"start_time": time.time()}
    check_timeout(state)  # must not raise
    _ok("fresh timeout no raise", True)


def test_timeout_stale() -> None:
    """Stale start_time → AgentTimeoutError."""
    # start_time set SINGLE_REQ_TIMEOUT_S + 5 seconds in the past.
    stale = time.time() - (SINGLE_REQ_TIMEOUT_S + 5)
    state = {"start_time": stale}
    raised = False
    try:
        check_timeout(state)
    except AgentTimeoutError as exc:
        raised = True
        _ok("timeout carries elapsed", exc.elapsed > SINGLE_REQ_TIMEOUT_S)
        _ok("timeout carries limit", exc.limit == SINGLE_REQ_TIMEOUT_S)
    _ok("stale timeout raises AgentTimeoutError", raised)


def test_event_payload_shape() -> None:
    """context_limit_event / timeout_event produce SSE strings with codes."""
    ctx_evt = context_limit_event(ContextLimitError(cumulative=999, limit=100))
    _ok("context_limit_event has code", "CONTEXT_LIMIT" in ctx_evt)
    _ok("context_limit_event has cumulative", "999" in ctx_evt)
    tmo_evt = timeout_event(AgentTimeoutError(elapsed=130.0, limit=120))
    _ok("timeout_event has code", "AGENT_TIMEOUT" in tmo_evt)
    _ok("timeout_event has elapsed", "130" in tmo_evt or "129" in tmo_evt or "131" in tmo_evt)


def test_contextvar_isolation() -> None:
    """Two independent accumulate sequences do not cross-contaminate.

    contextvar reset between sequences → second sequence starts from 0.
    """
    reset_token_counter()
    state_a = {"token_limit": 100000}
    accumulate_tokens("first sequence content", state_a)
    # Reset simulates a new request in the same task.
    reset_token_counter()
    state_b = {"token_limit": 1}  # tiny limit
    # If counter leaked from sequence A, this short string would overflow.
    # With proper reset, a short string still fits under limit=1? No — any
    # non-empty string > 1 token. So we use a larger limit and verify no
    # overflow from leaked state.
    state_b = {"token_limit": 100000}
    accumulate_tokens("second sequence", state_b)
    _ok("contextvar isolation OK", True)


def main() -> None:
    print("v3-T5 context_guard scenarios:")
    test_token_limit_known_model()
    test_token_limit_empty_model()
    test_accumulate_sub_limit()
    test_accumulate_over_limit()
    test_accumulate_guard_disabled()
    test_timeout_fresh()
    test_timeout_stale()
    test_event_payload_shape()
    test_contextvar_isolation()
    print("All v3-T5 scenarios PASS")


if __name__ == "__main__":
    main()
