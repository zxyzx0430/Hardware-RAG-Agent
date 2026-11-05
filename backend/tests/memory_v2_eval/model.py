"""Scenario schema and validation for Memory v2 evaluation.

The dataset is deliberately small: single-machine, single-user scenarios that
map to real workflows.  Validation is strict so a typo cannot silently weaken
a gate (for example a misspelled status would otherwise be counted as a pass).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "memory-v2-eval/1"

CHECKPOINT_FIELDS = (
    "goal",
    "constraints",
    "completed",
    "current_state",
    "decisions",
    "failed_attempts",
    "open_questions",
    "artifacts_and_evidence",
    "next_action",
)

TIERS = ("working", "project", "user", "episodic")
SCOPES = ("global", "project", "session")
STATUSES = ("proposed", "active", "rejected", "superseded")
KINDS = ("preference", "fact", "constraint", "decision", "procedure", "hardware_profile")
ORIGINS = ("explicit", "auto")
SOURCE_TYPES = ("user_statement", "tool_result", "assistant_inference")
NON_MEMORY_REASONS = (
    "one_off_task",
    "secret",
    "rag_fact",
    "hardware_profile",
    "other_project",
    "session_only",
    "duplicate",
    "rejected",
)
ACTION_TYPES = ("delete_session", "memory_clear", "approve", "reject")
FAILURE_MODES = ("storage", "projection")

# One category per high-value real-use scenario family from the Memory v2 plan.
REQUIRED_CATEGORIES = (
    "explicit_preference",
    "preference_correction",
    "project_convention",
    "decision_reversal",
    "duplicate",
    "one_off_noise",
    "secret",
    "rag_fact",
    "hardware_profile",
    "project_switch",
    "project_identity",
    "session_only",
    "session_delete",
    "memory_clear",
    "compaction_retention",
    "prompt_injection",
    "storage_failure",
    "irrelevant_retrieval",
    "cross_session",
    "auto_extract_toggle",
    "proposal_reject",
    "proposal_approve",
)

MIN_SCENARIOS = 20
MAX_SCENARIOS = 30


class ScenarioSchemaError(ValueError):
    """The scenario dataset is malformed or internally inconsistent."""


def _fail(path: str, message: str) -> None:
    raise ScenarioSchemaError(f"{path}: {message}")


def _require_str(value: Any, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str) or (non_empty and not value.strip()):
        _fail(path, "expected a non-empty string")
    if "\x00" in value:
        _fail(path, "must not contain NUL characters")
    return value


def _require_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "expected an integer")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "expected a list")
    return value


def _require_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "expected an object")
    return value


def _require_choice(value: Any, choices: tuple[str, ...], path: str) -> str:
    if value not in choices:
        _fail(path, f"expected one of {list(choices)}, got {value!r}")
    return value


def _require_terms(value: Any, path: str) -> tuple[str, ...]:
    terms = _require_list(value, path)
    if not terms:
        _fail(path, "at least one match term is required")
    normalized: list[str] = []
    for index, term in enumerate(terms):
        text = _require_str(term, f"{path}[{index}]")
        lowered = text.strip().lower()
        if lowered in normalized:
            _fail(path, f"duplicate match term {text!r}")
        normalized.append(lowered)
    return tuple(normalized)


def requires_secret(content: str) -> bool:
    """Conservative credential detector used only for dataset validation."""
    import re

    patterns = (
        r"\bsk-[A-Za-z0-9_-]{16,}\b",
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
    )
    return any(re.search(pattern, content) for pattern in patterns)


@dataclass(frozen=True)
class DurableGold:
    id: str
    content: str
    tier: str
    kind: str
    scope: str
    status: str
    match_terms: tuple[str, ...]
    project_id: str | None = None
    explicit: bool = False


@dataclass(frozen=True)
class NonMemoryGold:
    id: str
    content: str
    reason: str
    match_terms: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    content: str
    tier: str
    kind: str
    scope: str
    origin: str
    gate_turn: int
    confidence: float = 0.7
    source_type: str = "user_statement"
    source_name: str | None = None
    ephemeral: bool = False
    supersedes_terms: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    gold_ref: str | None = None


@dataclass(frozen=True)
class QuerySpec:
    id: str
    query: str
    match_terms: tuple[str, ...]
    project_id: str
    session_id: str
    expected_durable_ids: tuple[str, ...] = ()
    irrelevant_ids: tuple[str, ...] = ()
    cross_session: bool = False
    expect_no_injection: bool = False
    expected_context_terms: tuple[str, ...] = ()
    absent_context_terms: tuple[str, ...] = ()
    project_path: str | None = None


@dataclass(frozen=True)
class ActionSpec:
    type: str
    session_id: str | None = None
    match_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Turn:
    role: str
    content: str
    name: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    title: str
    description: str
    project_id: str
    session_id: str
    auto_extract_turns: tuple[int, ...]
    turns: tuple[Turn, ...]
    candidates: tuple[Candidate, ...]
    working_memory: dict[str, tuple[str, ...]]
    projection_after_turn: int
    working_memory_retained: bool
    gold_durable: tuple[DurableGold, ...]
    gold_non_memory: tuple[NonMemoryGold, ...]
    noise_durable: tuple[DurableGold, ...]
    queries: tuple[QuerySpec, ...]
    actions: tuple[ActionSpec, ...]
    failure_injection: tuple[str, ...]
    expectations: dict[str, Any]
    project_aliases: tuple[str, ...]


@dataclass(frozen=True)
class Dataset:
    schema_version: str
    scenarios: tuple[Scenario, ...]

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(scenario.category for scenario in self.scenarios)


def _parse_working_memory(raw: Any, path: str) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    document = _require_dict(raw, path)
    result: dict[str, tuple[str, ...]] = {}
    for field_name, values in document.items():
        if field_name not in CHECKPOINT_FIELDS:
            _fail(path, f"unknown working-memory field {field_name!r}")
        rows = _require_list(values, f"{path}.{field_name}")
        texts: list[str] = []
        for index, row in enumerate(rows):
            if isinstance(row, str):
                texts.append(_require_str(row, f"{path}.{field_name}[{index}]"))
            elif isinstance(row, dict):
                texts.append(
                    _require_str(row.get("text"), f"{path}.{field_name}[{index}].text")
                )
            else:
                _fail(f"{path}.{field_name}[{index}]", "expected a string or {{text}}")
        result[field_name] = tuple(texts)
    return result


def _parse_candidate(raw: Any, path: str, turn_count: int) -> Candidate:
    document = _require_dict(raw, path)
    content = _require_str(document.get("content"), f"{path}.content")
    tier = _require_choice(document.get("tier"), TIERS, f"{path}.tier")
    kind = _require_choice(document.get("kind"), KINDS, f"{path}.kind")
    scope = _require_choice(document.get("scope"), SCOPES, f"{path}.scope")
    origin = _require_choice(document.get("origin"), ORIGINS, f"{path}.origin")
    gate_turn = _require_int(document.get("gate_turn"), f"{path}.gate_turn")
    if not 1 <= gate_turn <= turn_count:
        _fail(f"{path}.gate_turn", f"must be within 1..{turn_count}")
    confidence = document.get("confidence", 0.7)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        _fail(f"{path}.confidence", "expected a number")
    if not 0.0 <= float(confidence) <= 1.0:
        _fail(f"{path}.confidence", "must be between 0 and 1")
    source_type = _require_choice(
        document.get("source_type", "user_statement"), SOURCE_TYPES, f"{path}.source_type"
    )
    source_name = document.get("source_name")
    if source_name is not None:
        source_name = _require_str(source_name, f"{path}.source_name")
    ephemeral = document.get("ephemeral", False)
    if not isinstance(ephemeral, bool):
        _fail(f"{path}.ephemeral", "expected a boolean")
    supersedes = document.get("supersedes_terms", [])
    supersedes_terms = (
        _require_terms(supersedes, f"{path}.supersedes_terms") if supersedes else ()
    )
    evidence = document.get("evidence", {})
    evidence = _require_dict(evidence, f"{path}.evidence")
    gold_ref = document.get("gold_ref")
    if gold_ref is not None:
        gold_ref = _require_str(gold_ref, f"{path}.gold_ref")
    return Candidate(
        content=content,
        tier=tier,
        kind=kind,
        scope=scope,
        origin=origin,
        gate_turn=gate_turn,
        confidence=float(confidence),
        source_type=source_type,
        source_name=source_name,
        ephemeral=ephemeral,
        supersedes_terms=supersedes_terms,
        evidence=dict(evidence),
        gold_ref=gold_ref,
    )


def _parse_gold(raw: Any, path: str, *, default_project_id: str | None) -> DurableGold:
    document = _require_dict(raw, path)
    gold_id = _require_str(document.get("id"), f"{path}.id")
    content = _require_str(document.get("content"), f"{path}.content")
    tier = _require_choice(document.get("tier"), TIERS, f"{path}.tier")
    kind = _require_choice(document.get("kind"), KINDS, f"{path}.kind")
    scope = _require_choice(document.get("scope"), SCOPES, f"{path}.scope")
    status = _require_choice(document.get("status"), STATUSES, f"{path}.status")
    match_terms = _require_terms(document.get("match_terms"), f"{path}.match_terms")
    project_id = document.get("project_id", default_project_id)
    if project_id is not None:
        project_id = _require_str(project_id, f"{path}.project_id")
    if scope == "project" and not project_id:
        _fail(f"{path}.project_id", "project-scoped gold needs a project_id")
    if scope == "global" and project_id:
        _fail(f"{path}.project_id", "global-scoped gold must not carry a project_id")
    explicit = document.get("explicit", False)
    if not isinstance(explicit, bool):
        _fail(f"{path}.explicit", "expected a boolean")
    return DurableGold(
        id=gold_id,
        content=content,
        tier=tier,
        kind=kind,
        scope=scope,
        status=status,
        match_terms=match_terms,
        project_id=project_id,
        explicit=explicit,
    )


def _parse_non_memory(raw: Any, path: str) -> NonMemoryGold:
    document = _require_dict(raw, path)
    gold_id = _require_str(document.get("id"), f"{path}.id")
    content = _require_str(document.get("content"), f"{path}.content")
    reason = _require_choice(document.get("reason"), NON_MEMORY_REASONS, f"{path}.reason")
    match_terms = _require_terms(document.get("match_terms"), f"{path}.match_terms")
    if reason == "secret" and not requires_secret(content):
        _fail(f"{path}.content", "a secret non-memory row must contain a credential pattern")
    return NonMemoryGold(id=gold_id, content=content, reason=reason, match_terms=match_terms)


def _parse_query(raw: Any, path: str, *, scenario: dict[str, Any]) -> QuerySpec:
    document = _require_dict(raw, path)
    query_id = _require_str(document.get("id"), f"{path}.id")
    query = _require_str(document.get("query"), f"{path}.query")
    match_terms = _require_terms(document.get("match_terms"), f"{path}.match_terms")
    project_id = _require_str(document.get("project_id"), f"{path}.project_id")
    session_id = _require_str(document.get("session_id"), f"{path}.session_id")
    expected = _require_list(
        document.get("expected_durable_ids", []), f"{path}.expected_durable_ids"
    )
    irrelevant = _require_list(document.get("irrelevant_ids", []), f"{path}.irrelevant_ids")
    expected_ids = tuple(
        _require_str(item, f"{path}.expected_durable_ids[{index}]")
        for index, item in enumerate(expected)
    )
    irrelevant_ids = tuple(
        _require_str(item, f"{path}.irrelevant_ids[{index}]")
        for index, item in enumerate(irrelevant)
    )
    cross_session = document.get("cross_session", False)
    if not isinstance(cross_session, bool):
        _fail(f"{path}.cross_session", "expected a boolean")
    expect_no_injection = document.get("expect_no_injection", False)
    if not isinstance(expect_no_injection, bool):
        _fail(f"{path}.expect_no_injection", "expected a boolean")
    expected_context = _require_list(
        document.get("expected_context_terms", []), f"{path}.expected_context_terms"
    )
    absent_context = _require_list(
        document.get("absent_context_terms", []), f"{path}.absent_context_terms"
    )
    project_path = document.get("project_path")
    if project_path is not None:
        project_path = _require_str(project_path, f"{path}.project_path")
    if not expected_ids and not irrelevant_ids and not expect_no_injection and not expected_context:
        _fail(
            path,
            "a query must declare expected entries, irrelevant entries, a no-injection "
            "expectation, or expected context terms",
        )
    all_gold_ids = {
        entry["id"] for entry in scenario.get("gold_durable", []) if isinstance(entry, dict)
    }
    all_other_ids = {
        entry["id"]
        for key in ("gold_non_memory", "noise_durable")
        for entry in scenario.get(key, [])
        if isinstance(entry, dict)
    }
    unknown_expected = [item for item in expected_ids if item not in all_gold_ids]
    if unknown_expected:
        _fail(f"{path}.expected_durable_ids", f"unknown ids {unknown_expected}")
    unknown_irrelevant = [
        item
        for item in irrelevant_ids
        if item not in all_gold_ids and item not in all_other_ids
    ]
    if unknown_irrelevant:
        _fail(f"{path}.irrelevant_ids", f"unknown ids {unknown_irrelevant}")
    return QuerySpec(
        id=query_id,
        query=query,
        match_terms=match_terms,
        project_id=project_id,
        session_id=session_id,
        expected_durable_ids=expected_ids,
        irrelevant_ids=irrelevant_ids,
        cross_session=cross_session,
        expect_no_injection=expect_no_injection,
        expected_context_terms=tuple(
            _require_str(item, f"{path}.expected_context_terms[{index}]")
            for index, item in enumerate(expected_context)
        ),
        absent_context_terms=tuple(
            _require_str(item, f"{path}.absent_context_terms[{index}]")
            for index, item in enumerate(absent_context)
        ),
        project_path=project_path,
    )


def _parse_action(raw: Any, path: str, scenario: dict[str, Any]) -> ActionSpec:
    document = _require_dict(raw, path)
    action_type = _require_choice(document.get("type"), ACTION_TYPES, f"{path}.type")
    session_id = document.get("session_id")
    if session_id is not None:
        session_id = _require_str(session_id, f"{path}.session_id")
    match_terms: tuple[str, ...] = ()
    if action_type in ("approve", "reject"):
        match_terms = _require_terms(
            document.get("match_terms"), f"{path}.match_terms"
        )
        known_terms = {
            term
            for entry in scenario.get("gold_durable", [])
            if isinstance(entry, dict)
            for term in entry.get("match_terms", [])
        }
        if not any(term.lower() in known_terms for term in match_terms):
            _fail(f"{path}.match_terms", "no gold durable entry uses these terms")
    return ActionSpec(type=action_type, session_id=session_id, match_terms=match_terms)


def _parse_scenario(raw: Any, path: str) -> Scenario:
    document = _require_dict(raw, path)
    scenario_id = _require_str(document.get("id"), f"{path}.id")
    category = _require_str(document.get("category"), f"{path}.category")
    if category not in REQUIRED_CATEGORIES:
        _fail(f"{path}.category", f"unknown category {category!r}")
    title = _require_str(document.get("title"), f"{path}.title")
    description = _require_str(document.get("description"), f"{path}.description")
    project_id = _require_str(document.get("project_id"), f"{path}.project_id")
    session_id = _require_str(document.get("session_id"), f"{path}.session_id")

    grammar = document.get("auto_extract_turns", [])
    auto_turns_raw = _require_list(grammar, f"{path}.auto_extract_turns")
    auto_extract_turns = tuple(
        _require_int(item, f"{path}.auto_extract_turns[{index}]")
        for index, item in enumerate(auto_turns_raw)
    )
    if len(set(auto_extract_turns)) != len(auto_extract_turns):
        _fail(f"{path}.auto_extract_turns", "duplicate turn numbers")
    if any(turn < 1 for turn in auto_extract_turns):
        _fail(f"{path}.auto_extract_turns", "turn numbers are 1-based")

    turns_raw = _require_list(document.get("turns"), f"{path}.turns")
    if not turns_raw:
        _fail(f"{path}.turns", "at least one turn is required")
    turns: list[Turn] = []
    for index, row in enumerate(turns_raw):
        item_path = f"{path}.turns[{index}]"
        item = _require_dict(row, item_path)
        role = _require_choice(item.get("role"), ("user", "assistant", "tool", "system"), f"{item_path}.role")
        content = _require_str(item.get("content"), f"{item_path}.content")
        name = item.get("name")
        if name is not None:
            name = _require_str(name, f"{item_path}.name")
        turns.append(Turn(role=role, content=content, name=name))
    if any(turn > len(turns) for turn in auto_extract_turns):
        _fail(f"{path}.auto_extract_turns", "turn number exceeds the turn count")

    fake_llm = document.get("fake_llm") or {}
    fake_llm = _require_dict(fake_llm, f"{path}.fake_llm")
    candidates_raw = _require_list(fake_llm.get("memories", []), f"{path}.fake_llm.memories")
    candidates = tuple(
        _parse_candidate(item, f"{path}.fake_llm.memories[{index}]", len(turns))
        for index, item in enumerate(candidates_raw)
    )

    working_memory = _parse_working_memory(
        fake_llm.get("working_memory", document.get("working_memory")),
        f"{path}.working_memory",
    )
    projection_after_turn = document.get("projection_after_turn", len(turns))
    projection_after_turn = _require_int(projection_after_turn, f"{path}.projection_after_turn")
    if not 1 <= projection_after_turn <= len(turns):
        _fail(f"{path}.projection_after_turn", "must be within 1..turn count")
    working_memory_retained = document.get("working_memory_retained", True)
    if not isinstance(working_memory_retained, bool):
        _fail(f"{path}.working_memory_retained", "expected a boolean")

    gold_durable = tuple(
        _parse_gold(item, f"{path}.gold_durable[{index}]", default_project_id=None)
        for index, item in enumerate(_require_list(document.get("gold_durable", []), f"{path}.gold_durable"))
    )
    gold_non_memory = tuple(
        _parse_non_memory(item, f"{path}.gold_non_memory[{index}]")
        for index, item in enumerate(_require_list(document.get("gold_non_memory", []), f"{path}.gold_non_memory"))
    )
    noise_durable = tuple(
        _parse_gold(item, f"{path}.noise_durable[{index}]", default_project_id=None)
        for index, item in enumerate(_require_list(document.get("noise_durable", []), f"{path}.noise_durable"))
    )

    seen_ids: set[str] = set()
    for entry in (*gold_durable, *gold_non_memory, *noise_durable):
        if entry.id in seen_ids:
            _fail(f"{path}", f"duplicate scenario-local id {entry.id!r}")
        seen_ids.add(entry.id)

    queries_raw = _require_list(document.get("queries"), f"{path}.queries")
    if not queries_raw:
        _fail(f"{path}.queries", "at least one query is required")
    queries = tuple(
        _parse_query(item, f"{path}.queries[{index}]", scenario=document)
        for index, item in enumerate(queries_raw)
    )
    query_ids = [query.id for query in queries]
    if len(set(query_ids)) != len(query_ids):
        _fail(f"{path}.queries", "duplicate query ids")

    actions = tuple(
        _parse_action(item, f"{path}.actions[{index}]", document)
        for index, item in enumerate(_require_list(document.get("actions", []), f"{path}.actions"))
    )

    failures_raw = _require_list(document.get("failure_injection", []), f"{path}.failure_injection")
    failure_injection = tuple(
        _require_choice(item, FAILURE_MODES, f"{path}.failure_injection[{index}]")
        for index, item in enumerate(failures_raw)
    )

    expectations = document.get("expectations", {})
    expectations = _require_dict(expectations, f"{path}.expectations")

    aliases_raw = _require_list(document.get("project_aliases", []), f"{path}.project_aliases")
    project_aliases = tuple(
        _require_str(item, f"{path}.project_aliases[{index}]")
        for index, item in enumerate(aliases_raw)
    )

    return Scenario(
        id=scenario_id,
        category=category,
        title=title,
        description=description,
        project_id=project_id,
        session_id=session_id,
        auto_extract_turns=auto_extract_turns,
        turns=tuple(turns),
        candidates=candidates,
        working_memory=working_memory,
        projection_after_turn=projection_after_turn,
        working_memory_retained=working_memory_retained,
        gold_durable=gold_durable,
        gold_non_memory=gold_non_memory,
        noise_durable=noise_durable,
        queries=queries,
        actions=actions,
        failure_injection=failure_injection,
        expectations=dict(expectations),
        project_aliases=project_aliases,
    )


def validate_dataset(
    raw: Any,
    *,
    min_scenarios: int = MIN_SCENARIOS,
    max_scenarios: int = MAX_SCENARIOS,
) -> Dataset:
    document = _require_dict(raw, "$")
    schema_version = _require_str(document.get("schema_version"), "$.schema_version")
    if schema_version != SCHEMA_VERSION:
        _fail("$.schema_version", f"expected {SCHEMA_VERSION!r}, got {schema_version!r}")
    rows = _require_list(document.get("scenarios"), "$.scenarios")
    if not min_scenarios <= len(rows) <= max_scenarios:
        _fail(
            "$.scenarios",
            f"expected {min_scenarios}..{max_scenarios} scenarios, got {len(rows)}",
        )
    scenarios = tuple(
        _parse_scenario(row, f"$.scenarios[{index}]") for index, row in enumerate(rows)
    )
    ids = [scenario.id for scenario in scenarios]
    if len(set(ids)) != len(ids):
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        _fail("$.scenarios", f"duplicate scenario ids: {duplicates}")
    return Dataset(schema_version=schema_version, scenarios=scenarios)


def load_dataset(path: str | Path) -> Dataset:
    dataset_path = Path(path)
    try:
        raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioSchemaError(f"cannot load {dataset_path}: {exc}") from exc
    return validate_dataset(raw)


def default_dataset_path() -> Path:
    return Path(__file__).with_name("scenarios.json")


__all__ = [
    "ACTION_TYPES",
    "ActionSpec",
    "CHECKPOINT_FIELDS",
    "Candidate",
    "Dataset",
    "DurableGold",
    "FAILURE_MODES",
    "KINDS",
    "MIN_SCENARIOS",
    "MAX_SCENARIOS",
    "NON_MEMORY_REASONS",
    "NonMemoryGold",
    "ORIGINS",
    "QuerySpec",
    "REQUIRED_CATEGORIES",
    "SCHEMA_VERSION",
    "SCOPES",
    "STATUSES",
    "Scenario",
    "ScenarioSchemaError",
    "TIERS",
    "Turn",
    "default_dataset_path",
    "load_dataset",
    "requires_secret",
    "validate_dataset",
]
