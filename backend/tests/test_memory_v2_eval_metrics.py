"""Metric and gate arithmetic tests (pure, deterministic)."""

from __future__ import annotations

import copy

import pytest

from tests.memory_v2_eval.metrics import compute_metrics, evaluate_gates, gate_summary
from tests.memory_v2_eval.model import load_dataset, default_dataset_path
from tests.memory_v2_eval.reference_backend import run_reference


def _metrics_base() -> dict:
    return {
        "security": {
            "secret_stored": 0,
            "secret_retrieved": 0,
            "cross_project_leaks": 0,
            "delete_residue": 0,
            "chat_failures": 0,
            "prompt_injection_successes": 0,
        },
        "extraction": {
            "precision": 0.90,
            "explicit_preference_recall": 0.95,
            "duplicate_rate": 0.05,
            "scope_project_accuracy": 0.97,
        },
        "retrieval": {
            "recall_at_5": 0.90,
            "precision_at_5": 0.80,
            "irrelevant_injection_rate": 0.05,
        },
        "working": {"constraint_retention": 1.0},
        "continuity": {"score": 1.0},
        "cost": {
            "extra_model_calls_per_gated_turn": 1.0,
            "max_injected_tokens": 900,
        },
    }


def test_all_gates_pass_at_threshold():
    gates = evaluate_gates(_metrics_base())
    assert all(gate["status"] == "pass" for gate in gates)
    assert gate_summary({"gates": gates}) == {
        "pass": len(gates),
        "fail": 0,
        "unverified": 0,
    }


def test_quality_threshold_boundaries_fail_below_required():
    metrics = _metrics_base()
    metrics["extraction"]["precision"] = 0.849
    metrics["extraction"]["explicit_preference_recall"] = 0.899
    metrics["extraction"]["duplicate_rate"] = 0.101
    metrics["extraction"]["scope_project_accuracy"] = 0.949
    metrics["retrieval"]["recall_at_5"] = 0.799
    metrics["retrieval"]["precision_at_5"] = 0.599
    metrics["retrieval"]["irrelevant_injection_rate"] = 0.101
    metrics["working"]["constraint_retention"] = 0.999
    metrics["continuity"]["score"] = 0.849
    failed = {
        gate["name"]
        for gate in evaluate_gates(metrics)
        if gate["status"] == "fail"
    }
    assert failed == {
        "extraction_precision",
        "explicit_preference_recall",
        "duplicate_rate",
        "scope_project_accuracy",
        "retrieval_recall_at_5",
        "retrieval_precision_at_5",
        "irrelevant_injection_rate",
        "working_constraint_retention",
        "cross_session_continuity",
    }


def test_hard_gate_any_nonzero_fails():
    metrics = _metrics_base()
    metrics["security"]["secret_stored"] = 1
    gates = {gate["name"]: gate for gate in evaluate_gates(metrics)}
    assert gates["secret_stored"]["status"] == "fail"
    assert gates["secret_stored"]["hard"] is True


def test_cost_gate_boundaries():
    metrics = _metrics_base()
    metrics["cost"]["extra_model_calls_per_gated_turn"] = 1.01
    metrics["cost"]["max_injected_tokens"] = 1001
    failed = {
        gate["name"]
        for gate in evaluate_gates(metrics)
        if gate["status"] == "fail"
    }
    assert failed == {"extra_model_calls_per_gated_turn", "max_injected_tokens"}


def test_missing_metric_is_unverified_not_pass():
    metrics = _metrics_base()
    metrics["retrieval"]["recall_at_5"] = None
    gates = {gate["name"]: gate for gate in evaluate_gates(metrics)}
    assert gates["retrieval_recall_at_5"]["status"] == "unverified"


def test_reference_harness_metrics_pass_every_gate():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    metrics = compute_metrics(results, list(dataset.scenarios))
    assert metrics["scenarios"]["total"] == len(dataset.scenarios)
    assert metrics["scenarios"]["failed"] == 0
    assert metrics["all_gates_pass"] is True
    assert metrics["hard_gate_failures"] == []


def test_duplicating_an_active_entry_raises_duplicate_rate():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    target = next(item for item in changed if item["scenario_id"] == "s05-duplicate-statement")
    active = next(entry for entry in target["entries"] if entry["status"] == "active")
    duplicate = copy.deepcopy(active)
    duplicate["id"] = active["id"] + "-copy"
    target["entries"].append(duplicate)
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert metrics["extraction"]["duplicate_rate"] > 0.0


def test_missing_retrieval_hit_lowers_recall():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    target = next(item for item in changed if item["scenario_id"] == "s01-explicit-preference")
    target["query_results"][0]["returned"] = []
    target["query_results"][0]["expected_hits"] = []
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert metrics["retrieval"]["recall_at_5"] < 1.0


def test_secret_leak_aggregates_into_hard_gate():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    changed[0]["security"]["secret_stored"] = 1
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert metrics["security"]["secret_stored"] == 1
    assert metrics["all_gates_pass"] is False
    assert "secret_stored" in metrics["hard_gate_failures"]


@pytest.mark.parametrize(
    "gate_name",
    [
        "secret_retrieved",
        "cross_project_leaks",
        "delete_residue",
        "chat_failures",
        "prompt_injection_successes",
    ],
)
def test_each_hard_gate_can_fail(gate_name):
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    changed[0]["security"][gate_name] = 1
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert gate_name in metrics["hard_gate_failures"]
    assert metrics["all_gates_pass"] is False


def test_scope_metric_detects_wrong_scope():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    target = next(item for item in changed if item["scenario_id"] == "s03-project-conventions")
    target["outcomes"][0]["tier"] = "user"
    target["outcomes"][0]["scope"] = "global"
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert metrics["extraction"]["scope_project_accuracy"] < 1.0


def test_irrelevant_injection_metric_detects_noise():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    target = next(item for item in changed if item["scenario_id"] == "s18-irrelevant-retrieval")
    target["query_results"][0]["injected"].append(
        {
            "id": "noise-injected",
            "content": "烙铁温度建议 350 度，IRRELEVANT-SOLDER。",
            "source": "durable",
        }
    )
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert metrics["retrieval"]["irrelevant_injection_rate"] > 0.0


def test_working_retention_metric_detects_lost_constraint():
    dataset = load_dataset(default_dataset_path())
    results = [run_reference(scenario) for scenario in dataset.scenarios]
    changed = copy.deepcopy(results)
    target = next(
        item for item in changed if item["scenario_id"] == "s15-compaction-constraint-retention"
    )
    target["working_retention"]["retained"] = 1
    metrics = compute_metrics(changed, list(dataset.scenarios))
    assert metrics["working"]["constraint_retention"] == 0.5
    gates = {gate["name"]: gate for gate in metrics["gates"]}
    assert gates["working_constraint_retention"]["status"] == "fail"
