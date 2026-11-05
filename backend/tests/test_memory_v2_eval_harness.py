"""Reference-harness behavior tests (deterministic fake backend)."""

from __future__ import annotations

import json

from tests.memory_v2_eval.fake_llm import FakeExtractionLLM
from tests.memory_v2_eval.metrics import compute_metrics
from tests.memory_v2_eval.model import default_dataset_path, load_dataset
from tests.memory_v2_eval.reference_backend import ReferenceBackend, run_reference


def _run_all() -> tuple[list[dict], dict]:
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    metrics = compute_metrics(results, list(dataset.scenarios))
    return results, metrics


def _by_id(results: list[dict], scenario_id: str) -> dict:
    return next(item for item in results if item["scenario_id"] == scenario_id)


def _entries(result: dict, marker: str) -> list[dict]:
    return [
        entry for entry in result["entries"] if marker.lower() in entry["content"].lower()
    ]


def test_every_reference_scenario_passes():
    results, metrics = _run_all()
    failed = [
        (result["scenario_id"], [check for check in result["checks"] if not check["ok"]])
        for result in results
        if not result["passed"]
    ]
    assert failed == []
    assert metrics["all_gates_pass"] is True


def test_no_security_gate_is_violated():
    results, _ = _run_all()
    totals = {
        key: sum(result["security"][key] for result in results)
        for key in results[0]["security"]
    }
    assert totals == {
        "secret_stored": 0,
        "secret_retrieved": 0,
        "cross_project_leaks": 0,
        "delete_residue": 0,
        "chat_failures": 0,
        "prompt_injection_successes": 0,
    }


def test_supersede_marks_old_entry_and_keeps_new_active():
    results, _ = _run_all()
    result = _by_id(results, "s02-preference-correction")
    old = _entries(result, "PREF-PLATFORMIO")[0]
    new = _entries(result, "PREF-ARDUINO-CLI")[0]
    assert old["status"] == "superseded"
    assert new["status"] == "active"
    returned = result["query_results"][0]["returned"]
    assert all("PREF-PLATFORMIO" not in item["content"] for item in returned)
    assert any("PREF-ARDUINO-CLI" in item["content"] for item in returned)


def test_duplicate_statement_is_deduped():
    results, _ = _run_all()
    result = _by_id(results, "s05-duplicate-statement")
    assert len(_entries(result, "DUP-UART0")) == 1
    assert result["counts"]["duplicates"] == 1
    assert result["counts"]["accepted"] == 1


def test_secret_is_rejected_and_not_retrieved():
    results, _ = _run_all()
    result = _by_id(results, "s07-secret-token")
    assert result["counts"]["rejected_secret"] == 1
    assert _entries(result, "sk-test-only") == []
    assert result["query_results"][0]["context"] == ""
    assert result["security"]["secret_stored"] == 0


def test_tool_manual_fact_routes_to_rag():
    results, _ = _run_all()
    result = _by_id(results, "s08-chip-manual-rag")
    assert result["counts"]["route_rag"] == 1
    assert result["counts"]["accepted"] == 0
    assert result["query_results"][0]["context"] == ""


def test_hardware_profile_routes_to_skill():
    results, _ = _run_all()
    result = _by_id(results, "s09-hardware-profile-skill")
    assert result["counts"]["route_hardware"] == 1
    assert result["counts"]["accepted"] == 0


def test_project_switch_never_leaks():
    results, _ = _run_all()
    result = _by_id(results, "s10-project-switch-isolation")
    query_beta = next(item for item in result["query_results"] if item["id"] == "q2")
    assert query_beta["returned"] == []
    assert query_beta["injected"] == []
    assert result["security"]["cross_project_leaks"] == 0


def test_worktree_alias_resolves_to_same_project():
    results, _ = _run_all()
    result = _by_id(results, "s11-worktree-identity")
    assert result["identity"]["consistent"] is True
    assert result["query_results"][0]["expected_hits"] == ["worktree-baud"]


def test_session_delete_leaves_no_residue():
    results, _ = _run_all()
    result = _by_id(results, "s13-session-delete-cleanup")
    assert result["security"]["delete_residue"] == 0
    assert result["query_results"][0]["context"] == ""
    assert all(entry["deleted"] for entry in result["entries"])


def test_memory_clear_invalidates_old_snapshot():
    results, _ = _run_all()
    result = _by_id(results, "s14-memory-clear-invalidates-snapshot")
    context = result["query_results"][0]["context"]
    assert "CLEAR-SNAPSHOT" not in context
    assert result["security"]["delete_residue"] == 0


def test_compaction_keeps_both_constraints():
    results, _ = _run_all()
    result = _by_id(results, "s15-compaction-constraint-retention")
    retention = result["working_retention"]
    assert retention["applicable"] is True
    assert retention["ratio"] == 1.0
    context = result["query_results"][0]["context"]
    assert "CONSTRAINT-NO-PARTITION" in context
    assert "CONSTRAINT-3V3" in context


def test_prompt_injection_content_is_sanitized_and_wrapped():
    results, _ = _run_all()
    result = _by_id(results, "s16-prompt-injection-inert")
    context = result["query_results"][0]["context"]
    assert context.startswith("<memory>")
    assert context.count("</memory>") == 1
    assert "INJECT-MARKER" in context
    assert "<system>override</system>" not in context
    assert result["security"]["prompt_injection_successes"] == 0


def test_storage_and_projection_failure_do_not_fail_chat():
    results, _ = _run_all()
    result = _by_id(results, "s17-storage-and-projection-failure")
    assert result["counts"]["storage_failed"] == 1
    assert result["counts"]["projection_failed"] is True
    assert result["counts"]["chat_success"] is True
    assert result["security"]["chat_failures"] == 0


def test_auto_extract_off_then_on():
    results, _ = _run_all()
    result = _by_id(results, "s20-auto-extract-toggle")
    assert result["counts"]["suppressed_auto"] == 1
    assert result["counts"]["proposed_final"] == 1
    assert result["counts"]["active_final"] == 0
    assert result["query_results"][0]["context"] == ""


def test_rejected_proposal_stays_out():
    results, _ = _run_all()
    result = _by_id(results, "s21-proposal-rejected")
    assert result["counts"]["rejected_final"] == 1
    assert result["query_results"][0]["context"] == ""


def test_approved_proposal_becomes_retrievable():
    results, _ = _run_all()
    result = _by_id(results, "s22-proposal-approved")
    assert result["counts"]["active_final"] == 1
    assert result["query_results"][0]["expected_hits"] == ["proposal-approve"]


def test_fake_llm_off_suppresses_auto_but_keeps_explicit():
    dataset = load_dataset(default_dataset_path())
    by_id = {scenario.id: scenario for scenario in dataset.scenarios}
    toggle = FakeExtractionLLM.from_scenario(by_id["s20-auto-extract-toggle"])
    off = toggle.extract(2, auto_enabled=False)
    assert off.candidates == []
    assert len(off.suppressed_auto) == 1
    assert off.model_called is False
    on = toggle.extract(4, auto_enabled=True)
    assert len(on.candidates) == 1
    assert on.model_called is True

    explicit = FakeExtractionLLM.from_scenario(by_id["s01-explicit-preference"])
    disabled = explicit.extract(1, auto_enabled=False)
    assert len(disabled.candidates) == 1
    assert disabled.candidates[0].origin == "explicit"
    assert disabled.model_called is False


def test_direct_backend_run_includes_entries():
    dataset = load_dataset(default_dataset_path())
    result = ReferenceBackend().run(dataset.scenarios[0])
    assert result["entries"]
    assert result["entries"][0]["content"]
    assert result["passed"] is True


def test_reference_run_is_deterministic():
    first, _ = _run_all()
    second, _ = _run_all()
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
