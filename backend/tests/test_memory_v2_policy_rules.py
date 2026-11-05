"""Memory v2 policy pure functions and project wrapper."""

import pytest

from app.db.memory_store import STATUS_TRANSITIONS, MemorySecretError, MemoryValidationError
from src.agent import memory_policy, memory_project


def test_policy_transitions_match_store():
    assert memory_policy.LEGAL_TRANSITIONS == frozenset(STATUS_TRANSITIONS)


def test_normalize_candidate_defaults_to_proposed_project():
    candidate = memory_policy.normalize_candidate(
        {
            "content": "Use PlatformIO for builds.",
            "kind": "decision",
            "project_id": "proj-a",
            "evidence": {"seal_id": "seal-1"},
        }
    )
    assert candidate["status"] == "proposed"
    assert candidate["tier"] == "project"
    assert candidate["scope"] == "project"
    assert candidate["scope_key"] == "proj-a"
    assert candidate["principal_id"] == "local"


def test_normalize_candidate_user_tier_clears_project_id():
    candidate = memory_policy.normalize_candidate(
        {"content": "Use metric units.", "kind": "preference", "tier": "user", "project_id": "x"}
    )
    assert candidate["tier"] == "user"
    assert candidate["project_id"] == ""
    assert candidate["scope_key"] == ""


def test_project_candidate_requires_project_id():
    with pytest.raises(MemoryValidationError):
        memory_policy.normalize_candidate(
            {"content": "Project fact.", "kind": "fact", "tier": "project"}
        )


def test_candidates_cannot_use_working_or_episodic_tiers():
    for tier in ("working", "episodic"):
        with pytest.raises(MemoryValidationError):
            memory_policy.normalize_candidate(
                {"content": "Candidate.", "kind": "fact", "tier": tier, "project_id": "p"}
            )


def test_candidate_rejects_hardware_and_secrets():
    with pytest.raises(MemoryValidationError):
        memory_policy.normalize_candidate(
            {
                "content": "Board profile.",
                "kind": "fact",
                "project_id": "p",
                "structured": {"type": "hardware_profile"},
            }
        )
    with pytest.raises(MemorySecretError):
        memory_policy.normalize_candidate(
            {"content": "token: 9f8e7d6c5b4a2c3d", "kind": "fact", "project_id": "p"}
        )


def test_review_target_state_machine():
    assert memory_policy.review_target("proposed", "approve") == "active"
    assert memory_policy.review_target("proposed", "reject") == "rejected"
    with pytest.raises(memory_policy.IllegalTransitionError):
        memory_policy.review_target("active", "approve")
    with pytest.raises(memory_policy.IllegalTransitionError):
        memory_policy.review_target("rejected", "approve")
    with pytest.raises(MemoryValidationError):
        memory_policy.review_target("proposed", "delete")


def test_normalize_text_collapses_whitespace_and_case():
    assert memory_policy.normalize_text("  Same\tText ") == memory_policy.normalize_text(
        "same   text"
    )


def test_project_wrapper_is_cached_and_clearable(tmp_path, monkeypatch):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("HARDWARE_AGENT_PROJECT_PATH", str(first))
    memory_project.clear_cache()
    first_id = memory_project.current_project_id()
    assert first_id == memory_project.current_project_id()

    monkeypatch.setenv("HARDWARE_AGENT_PROJECT_PATH", str(second))
    second_id = memory_project.current_project_id()
    assert second_id != first_id
    assert memory_project.current_project_id() == second_id
    memory_project.clear_cache()
    assert memory_project.current_project_id() == second_id
    memory_project.clear_cache()
