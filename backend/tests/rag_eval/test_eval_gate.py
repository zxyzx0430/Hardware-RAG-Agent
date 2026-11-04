"""Offline regression tests for the W5 evaluation gate."""

from pathlib import Path

from tests.rag_eval.gate import (
    GENERAL_DATASET,
    PDF_DATASET,
    MIN_WEIGHTED_SCORE,
    evaluate_report,
    validate_dataset,
    validate_datasets,
)


def _report(**overrides):
    report = {
        "sample_count": 35,
        "scored_sample_count": 35,
        "total_score": 80.0,
        "dimension_scores": {
            "context_recall": 0.9,
            "faithfulness": 0.9,
            "answer_relevancy": 0.9,
            "context_precision": 0.9,
        },
    }
    report.update(overrides)
    return report


def test_both_golden_datasets_validate():
    result = validate_datasets([GENERAL_DATASET, PDF_DATASET])

    assert result.ok
    assert result.sample_count == 35


def test_dataset_count_mismatch_is_rejected(tmp_path: Path):
    dataset = tmp_path / "bad.yaml"
    dataset.write_text(
        "metadata:\n  sample_count: 2\nsamples:\n  - id: A\n",
        encoding="utf-8",
    )

    result = validate_dataset(dataset)

    assert not result.ok
    assert any("sample_count" in message for message in result.messages)


def test_complete_report_passes_thresholds():
    result = evaluate_report(_report())

    assert result.ok
    assert result.sample_count == 35


def test_low_metric_fails():
    report = _report()
    report["dimension_scores"]["context_recall"] = 0.5

    result = evaluate_report(report)

    assert not result.ok
    assert any("context_recall" in message for message in result.messages)


def test_partial_report_fails_unless_allowed():
    report = _report(scored_sample_count=30)

    assert not evaluate_report(report).ok
    assert evaluate_report(report, require_complete=False).ok


def test_weighted_score_floor():
    report = _report(total_score=MIN_WEIGHTED_SCORE - 0.01)

    result = evaluate_report(report)

    assert not result.ok
    assert any("total_score" in message for message in result.messages)
