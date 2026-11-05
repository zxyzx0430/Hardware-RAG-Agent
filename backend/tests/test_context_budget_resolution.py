"""Context budget resolution for registered and unknown models."""

import pytest

from src.agent.context_guard import (
    AGENT_CONTEXT_WINDOW_CAP,
    compute_token_limit,
    resolve_context_budget,
)
from src.agent.request_budget import (
    AgentRequestBudgetError,
    validate_agent_request_budget,
)
from src.llm.model_registry import DEFAULT_CONTEXT_WINDOW, get_context_window


def test_registered_model_uses_registered_window():
    budget = resolve_context_budget("gpt-4o")
    assert budget.model_window == 128000
    assert budget.effective_window == min(128000, AGENT_CONTEXT_WINDOW_CAP)
    assert budget.budget_source == "registered"
    assert get_context_window("gpt-4o") == 128000


def test_registered_model_is_tightened_by_explicit_override():
    budget = resolve_context_budget("gpt-4o", 20000, requested_max_tokens=1024)
    assert budget.effective_window == 20000
    assert budget.budget_source == "explicit_override"


def test_unknown_model_uses_conservative_default_without_override():
    budget = resolve_context_budget("vendor/custom-model")
    assert budget.model_window == DEFAULT_CONTEXT_WINDOW == 32768
    assert budget.effective_window == 32768
    assert budget.output_tokens == 4096
    assert budget.budget_source == "conservative_default"
    assert get_context_window("vendor/custom-model") == 32768


def test_unknown_model_honors_explicit_override_up_to_hard_cap():
    budget = resolve_context_budget("vendor/custom-model", 65536)
    assert budget.effective_window == 65536
    assert budget.budget_source == "explicit_override"
    capped = resolve_context_budget(
        "vendor/custom-model", AGENT_CONTEXT_WINDOW_CAP * 2,
    )
    assert capped.effective_window == AGENT_CONTEXT_WINDOW_CAP
    assert capped.budget_source == "explicit_override"


def test_compute_token_limit_uses_resolved_budget():
    assert compute_token_limit("vendor/custom-model") == resolve_context_budget(
        "vendor/custom-model",
    ).token_limit
    assert compute_token_limit("vendor/custom-model", 65536) == resolve_context_budget(
        "vendor/custom-model", 65536,
    ).token_limit


def test_unknown_model_wire_budget_uses_conservative_window():
    validate_agent_request_budget({
        "model": "vendor/custom-model",
        "messages": [{"role": "user", "content": "hello"}],
    })


def test_unknown_model_wire_budget_caps_output_reserve():
    with pytest.raises(AgentRequestBudgetError, match="invalid output capacity"):
        validate_agent_request_budget({
            "model": "vendor/custom-model",
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": "hello"}],
        })
