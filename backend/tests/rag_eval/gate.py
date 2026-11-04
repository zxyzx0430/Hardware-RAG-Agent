"""Offline evaluation gates for the W5 quality pipeline.

Live scoring stays in run_golden_eval.py; this module only validates datasets
and checks an existing JSON report against the documented thresholds.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


GENERAL_DATASET = Path(__file__).with_name("golden_dataset.yaml")
PDF_DATASET = Path(__file__).with_name("golden_dataset_pdf.yaml")

THRESHOLDS = {
    "context_recall": 0.80,
    "faithfulness": 0.75,
    "answer_relevancy": 0.80,
    "context_precision": 0.70,
}
WEIGHTS = {
    "context_recall": 30,
    "faithfulness": 25,
    "answer_relevancy": 25,
    "context_precision": 20,
}
MIN_RAG_SAMPLES = 35
MIN_WEIGHTED_SCORE = round(
    sum(THRESHOLDS[name] * WEIGHTS[name] for name in THRESHOLDS), 2,
)


@dataclass(frozen=True)
class GateResult:
    ok: bool
    messages: tuple[str, ...]
    sample_count: int = 0


def _load(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("dataset root must be a mapping")
    samples = data.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("dataset must contain a non-empty samples list")
    if any(not isinstance(sample, dict) for sample in samples):
        raise ValueError("every sample must be a mapping")
    return data, samples


def validate_dataset(path: Path, minimum: int = 0) -> GateResult:
    try:
        data, samples = _load(path)
    except Exception as exc:
        return GateResult(False, (f"{path.name}: {exc}",))

    messages = []
    expected = (data.get("metadata") or {}).get("sample_count")
    if expected is not None and expected != len(samples):
        messages.append(
            f"{path.name}: sample_count {expected} != {len(samples)}"
        )
    ids = [sample.get("id") for sample in samples]
    if any(not isinstance(sample_id, str) or not sample_id for sample_id in ids):
        messages.append(f"{path.name}: every sample needs a non-empty id")
    if len(set(ids)) != len(ids):
        messages.append(f"{path.name}: duplicate sample ids")
    if len(samples) < minimum:
        messages.append(
            f"{path.name}: {len(samples)} samples < minimum {minimum}"
        )
    return GateResult(not messages, tuple(messages), len(samples))


def validate_datasets(
    paths: list[Path] | tuple[Path, ...],
    minimum_total: int = MIN_RAG_SAMPLES,
) -> GateResult:
    results = [validate_dataset(path) for path in paths]
    messages = [message for result in results for message in result.messages]
    total = sum(result.sample_count for result in results)
    if total < minimum_total:
        messages.append(f"combined samples {total} < minimum {minimum_total}")
    return GateResult(not messages, tuple(messages), total)


def evaluate_report(
    report: dict[str, Any],
    *,
    require_complete: bool = True,
) -> GateResult:
    messages = []
    scores = report.get("dimension_scores") or {}
    for name, threshold in THRESHOLDS.items():
        score = scores.get(name)
        if not isinstance(score, (int, float)):
            messages.append(f"{name}: missing score")
        elif score < threshold:
            messages.append(f"{name}: {score:.3f} < {threshold:.2f}")

    planned = report.get("sample_count")
    scored = report.get("scored_sample_count")
    if not isinstance(planned, int) or planned < MIN_RAG_SAMPLES:
        messages.append(f"sample_count {planned!r} < {MIN_RAG_SAMPLES}")
    if require_complete and scored != planned:
        messages.append(f"scored {scored!r} != planned {planned!r}")

    total = report.get("total_score")
    if not isinstance(total, (int, float)):
        messages.append("total_score is missing")
    elif total < MIN_WEIGHTED_SCORE:
        messages.append(
            f"total_score {total:.2f} < {MIN_WEIGHTED_SCORE:.2f}"
        )
    return GateResult(not messages, tuple(messages), planned or 0)
