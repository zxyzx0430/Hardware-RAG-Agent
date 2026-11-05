"""Scenario dataset schema tests."""

from __future__ import annotations

import copy
import json

import pytest

from tests.memory_v2_eval.model import (
    REQUIRED_CATEGORIES,
    ScenarioSchemaError,
    default_dataset_path,
    load_dataset,
    validate_dataset,
)


def _raw() -> dict:
    return json.loads(default_dataset_path().read_text(encoding="utf-8"))


def _validate_mutated(raw: dict):
    return validate_dataset(raw, min_scenarios=0, max_scenarios=50)


def test_default_dataset_has_20_to_30_scenarios():
    dataset = load_dataset(default_dataset_path())
    assert 20 <= len(dataset.scenarios) <= 30
    assert len(dataset.scenarios) == 22


def test_default_dataset_covers_every_required_category():
    dataset = load_dataset(default_dataset_path())
    assert set(REQUIRED_CATEGORIES) <= set(dataset.categories)


def test_default_dataset_ids_and_queries_are_consistent():
    dataset = load_dataset(default_dataset_path())
    ids = [scenario.id for scenario in dataset.scenarios]
    assert len(ids) == len(set(ids))
    for scenario in dataset.scenarios:
        assert scenario.queries, scenario.id
        assert scenario.working_memory, f"{scenario.id} has no gold working memory"
        gold_ids = {entry.id for entry in scenario.gold_durable}
        for query in scenario.queries:
            assert set(query.expected_durable_ids) <= gold_ids, (scenario.id, query.id)


def test_dataset_rejects_unknown_category():
    raw = _raw()
    raw["scenarios"][0]["category"] = "misc"
    with pytest.raises(ScenarioSchemaError, match="unknown category"):
        _validate_mutated(raw)


def test_dataset_rejects_duplicate_scenario_ids():
    raw = _raw()
    raw["scenarios"].append(copy.deepcopy(raw["scenarios"][0]))
    with pytest.raises(ScenarioSchemaError, match="duplicate scenario ids"):
        _validate_mutated(raw)


def test_dataset_rejects_unknown_status():
    raw = _raw()
    raw["scenarios"][0]["gold_durable"][0]["status"] = "remembered"
    with pytest.raises(ScenarioSchemaError, match="status"):
        _validate_mutated(raw)


def test_dataset_rejects_unknown_query_expected_id():
    raw = _raw()
    raw["scenarios"][0]["queries"][0]["expected_durable_ids"] = ["missing-id"]
    with pytest.raises(ScenarioSchemaError, match="unknown ids"):
        _validate_mutated(raw)


def test_dataset_rejects_unknown_working_memory_field():
    raw = _raw()
    raw["scenarios"][14]["fake_llm"]["working_memory"]["mood"] = ["curious"]
    with pytest.raises(ScenarioSchemaError, match="unknown working-memory field"):
        _validate_mutated(raw)


def test_dataset_rejects_wrong_schema_version():
    raw = _raw()
    raw["schema_version"] = "memory-v2-eval/0"
    with pytest.raises(ScenarioSchemaError, match="schema_version"):
        _validate_mutated(raw)


def test_dataset_rejects_secret_non_memory_without_secret_pattern():
    raw = _raw()
    for scenario in raw["scenarios"]:
        for entry in scenario["gold_non_memory"]:
            if entry["reason"] == "secret":
                entry["content"] = "just a normal sentence"
                entry["match_terms"] = ["normal"]
    with pytest.raises(ScenarioSchemaError, match="credential pattern"):
        _validate_mutated(raw)
