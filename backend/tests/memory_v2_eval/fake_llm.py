"""Recorded fake extraction model for deterministic Memory v2 evaluation.

The dataset stores scripted model output under ``fake_llm.memories``.  This
class is the only extraction entry point used by the reference harness; it makes
the "fake LLM" explicit and guarantees no network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Candidate, Scenario


@dataclass(frozen=True)
class ExtractionResult:
    candidates: list[Candidate] = field(default_factory=list)
    suppressed_auto: list[Candidate] = field(default_factory=list)
    model_called: bool = False


class FakeExtractionLLM:
    """Scripted extraction output, grouped by the turn that produced it."""

    def __init__(self, candidates: list[Candidate] | tuple[Candidate, ...]):
        self._by_turn: dict[int, list[Candidate]] = {}
        for candidate in candidates:
            self._by_turn.setdefault(candidate.gate_turn, []).append(candidate)

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> "FakeExtractionLLM":
        return cls(scenario.candidates)

    def extract(self, turn_number: int, *, auto_enabled: bool) -> ExtractionResult:
        turn_candidates = list(self._by_turn.get(turn_number, ()))
        explicit = [item for item in turn_candidates if item.origin == "explicit"]
        automatic = [item for item in turn_candidates if item.origin == "auto"]
        if auto_enabled:
            return ExtractionResult(
                candidates=explicit + automatic,
                suppressed_auto=[],
                model_called=bool(automatic),
            )
        return ExtractionResult(
            candidates=explicit,
            suppressed_auto=automatic,
            model_called=False,
        )


__all__ = ["ExtractionResult", "FakeExtractionLLM"]
