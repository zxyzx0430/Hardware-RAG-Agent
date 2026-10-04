"""Fail-closed DeepEval scoring, terminal protocol, and denominator tests."""

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from tests.rag_eval import run_golden_eval as golden
from tests.rag_eval.chat_evidence import extract_agent_context, summarize_chat_evidence


def _sample(sample_id="G001"):
    return golden.GoldenSample(
        id=sample_id, question="What does MODER configure?",
        standard_answer="MODER configures a GPIO pin mode.",
        reference_chunks=["MODER configures GPIO pin mode."],
        difficulty="easy", category="GPIO", target_doc="gpio.md", tags=["gpio"],
    )


def _path_events(done=None):
    events = [
        {"type": "tool_call", "tool": "search_docs"},
        {"type": "source", "id": "src1", "kb_id": "kb-test", "excerpt": "MODER"},
        {"type": "tool_result", "tool": "search_docs", "success": True},
    ]
    if done is not None:
        events.append(done)
    return events


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -0.01, 1.01, True, "0.5", {}])
def test_invalid_metric_values_are_unavailable(value):
    assert golden._valid_metric_score(value) is None


@pytest.mark.parametrize("value", [0, 0.0, 0.25, 1, 1.0])
def test_finite_metric_values_in_unit_range_are_valid(value):
    assert golden._valid_metric_score(value) == float(value)


def test_total_requires_all_four_valid_metrics():
    scores = {name: 0.5 for name in golden.SCORE_WEIGHTS}
    assert golden.compute_weighted_score(scores) is not None
    for name in golden.SCORE_WEIGHTS:
        incomplete = dict(scores)
        incomplete[name] = None
        assert golden.compute_weighted_score(incomplete) is None


def test_report_keeps_partial_metric_denominators_separate_from_path_failures():
    samples = [_sample("G001"), _sample("G002")]
    scored_input = golden.SampleResult(
        id="G001", question=samples[0].question, attempted=True, eligible=True,
        quality_input_status="complete",
        path_evidence={"status": "verified_source_path"},
        scores={
            "context_recall": 0.0, "faithfulness": 0.25,
            "answer_relevancy": None, "context_precision": 1.0,
        },
        metric_failures={"answer_relevancy": "provider_failure"},
    )
    awaiting = golden.SampleResult(
        id="G002", question=samples[1].question, attempted=True, eligible=False,
        answer_error="awaiting_confirmation", quality_input_status="excerpt_only",
        path_evidence={"status": "awaiting_confirmation"},
    )

    report, markdown = golden.generate_report(
        {}, samples, [scored_input, awaiting],
        {"evaluation_kind": "standard_deepeval"}, {},
    )

    assert report["planned_count"] == 2
    assert report["attempted_count"] == 2
    assert report["eligible_count"] == 1
    assert report["scored_sample_count"] == 0
    assert report["total_score"] is None
    assert report["standard_score_status"] == "unavailable"
    assert report["dimension_scores"] == {
        "context_recall": 0.0, "faithfulness": 0.25,
        "answer_relevancy": None, "context_precision": 1.0,
    }
    assert report["dimension_scored_counts"] == {
        "context_recall": 1, "faithfulness": 1,
        "answer_relevancy": 0, "context_precision": 1,
    }
    assert report["dimension_scored_denominators"] == {
        name: 1 for name in golden.SCORE_WEIGHTS
    }
    assert report["metric_failure_count"] == 1
    assert report["answer_failure_count"] == 0
    assert report["path_status_counts"]["awaiting_confirmation"] == 1
    assert report["recall_hit_rate"] == 0.0
    assert "not scored" in markdown


def test_empty_quality_denominator_formats_as_not_scored():
    sample = _sample()
    result = golden.SampleResult(
        id=sample.id, question=sample.question,
        path_evidence={"status": "request_failed"},
    )
    report, markdown = golden.generate_report({}, [sample], [result], {}, {})
    assert report["recall_hit_rate"] is None
    assert golden._format_rate(report["recall_hit_rate"]) == "not scored"
    assert "Recall Hit Rate**: not scored" in markdown


def test_terminal_protocol_accepts_legacy_done_but_rejects_waiting():
    answer = "MODER configures GPIO mode [src1]."
    legacy = summarize_chat_evidence(
        _path_events({"type": "done", "success": True}), answer,
    )
    completed = summarize_chat_evidence(
        _path_events({"type": "done", "success": True, "completed": True}), answer,
    )
    awaiting = summarize_chat_evidence(
        _path_events({
            "type": "done", "success": True, "completed": False,
            "awaiting_confirmation": True,
        }), answer,
    )
    required_event = summarize_chat_evidence(
        _path_events({"type": "done", "success": True, "completed": True})
        + [{"type": "tool_confirm_required"}], answer,
    )
    failed = summarize_chat_evidence(
        _path_events({"type": "done", "success": False, "completed": False}), answer,
    )

    assert legacy["status"] == completed["status"] == "verified_source_path"
    assert awaiting["status"] == required_event["status"] == "awaiting_confirmation"
    assert awaiting["done_succeeded"] is False
    assert required_event["done_succeeded"] is False
    assert failed["status"] == "request_failed"
    assert failed["done_succeeded"] is False


def test_only_full_successful_parent_context_is_quality_input():
    content = "complete parent context " * 180
    source = {
        "type": "source", "id": "src1", "kb_id": "kb-test",
        "excerpt": content[:80], "page": 4,
    }
    event = {
        "type": "tool_result", "tool": "search_docs", "success": True,
        "result": {
            "success": True,
            "data": {"results": [{
                "id": "src1", "title": "GPIO manual", "page": 4,
                "section_title": "Register map", "chunk_index": 7,
                "content": content, "private_prompt": "must not be copied",
            }]},
        },
    }
    contexts, status, metadata = extract_agent_context([event], [source])
    excerpt_context, excerpt_status, _ = extract_agent_context([], [source])

    assert status == "complete"
    assert contexts == [content]
    assert len(contexts[0]) > 2000
    assert metadata == [{
        "id": "src1", "title": "GPIO manual", "page": 4,
        "section_title": "Register map", "chunk_index": 7,
    }]
    assert excerpt_status == "excerpt_only"
    assert excerpt_context == [content[:80]]


def test_excerpt_only_verified_path_is_not_eligible_for_standard_metrics(monkeypatch):
    sample = _sample()

    class ExcerptOnlyClient:
        use_agent = True

        def chat(self, _question):
            return (
                "MODER sets mode [src1].", ["short excerpt"], {},
                {
                    "status": "verified_source_path", "answer_error": "",
                    "quality_input_status": "excerpt_only", "source_evidence": [],
                },
            )

    monkeypatch.setattr(
        golden, "run_deepeval_metrics",
        lambda *_args, **_kwargs: pytest.fail("excerpt-only input must not be graded"),
    )
    result = golden.evaluate_sample(sample, 1, 1, ExcerptOnlyClient(), None, {}, "judge")
    assert result.eligible is False
    assert result.quality_input_status == "excerpt_only"
    assert result.scores == {name: None for name in golden.SCORE_WEIGHTS}


def test_runtime_preflight_failure_is_reported_not_scored_before_setup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(golden, "_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(golden, "load_dataset", lambda _path: ({"version": "test"}, [_sample()]))

    def unsupported_runtime():
        raise golden.DeepEvalRuntimeError("requires Python 3.10-3.12")

    monkeypatch.setattr(golden, "preflight_deepeval_runtime", unsupported_runtime)
    monkeypatch.setattr(
        golden, "setup_deepeval_judge",
        lambda *_args: pytest.fail("judge setup must not run after failed preflight"),
    )
    monkeypatch.setattr(sys, "argv", ["run_golden_eval"])

    assert golden.main() == 2
    output = capsys.readouterr().out
    assert "Evaluation not scored" in output
    assert "requires Python 3.10-3.12" in output


def test_unsupported_python_is_rejected_without_loading_or_calling_a_judge():
    with pytest.raises(golden.DeepEvalRuntimeError, match="Python 3.10-3.12"):
        golden._validate_deepeval_runtime((3, 13), "4.0.7")


def test_grading_failure_categories_remain_distinct():
    class ProviderError(Exception):
        pass

    ProviderError.__module__ = "openai"
    assert golden._grading_failure_category(TimeoutError()) == "grading_timeout"
    assert golden._grading_failure_category(ValueError("bad schema")) == "format_failure"
    assert golden._grading_failure_category(ProviderError()) == "provider_failure"


def test_deepeval_metrics_attempt_once_and_keep_partial_valid_results(monkeypatch):
    call_counts = {name: 0 for name in golden.SCORE_WEIGHTS}
    cancelled = []

    class SlowMetric:
        def __init__(self, model):
            self.score = None
            self.reason = ""

        async def a_measure(self, _case):
            call_counts["context_recall"] += 1
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(True)

    def make_fast_metric(name):
        class FastMetric:
            def __init__(self, model):
                self.score = None
                self.reason = "ok"

            async def a_measure(self, _case):
                call_counts[name] += 1
                self.score = 0.5
                return None
        return FastMetric

    metrics_module = types.ModuleType("deepeval.metrics")
    metrics_module.ContextualRecallMetric = SlowMetric
    metrics_module.FaithfulnessMetric = make_fast_metric("faithfulness")
    metrics_module.AnswerRelevancyMetric = make_fast_metric("answer_relevancy")
    metrics_module.ContextualPrecisionMetric = make_fast_metric("context_precision")
    test_case_module = types.ModuleType("deepeval.test_case")
    test_case_module.LLMTestCase = lambda **kwargs: SimpleNamespace(**kwargs)
    package_module = types.ModuleType("deepeval")
    package_module.__path__ = []
    monkeypatch.setitem(sys.modules, "deepeval", package_module)
    monkeypatch.setitem(sys.modules, "deepeval.metrics", metrics_module)
    monkeypatch.setitem(sys.modules, "deepeval.test_case", test_case_module)

    scores, reasons = golden.run_deepeval_metrics(
        _sample(), "answer", ["full context"], "fake-judge", timeout_seconds=0.02,
    )

    assert call_counts == {name: 1 for name in golden.SCORE_WEIGHTS}
    assert cancelled == [True]
    assert scores["context_recall"] is None
    assert reasons["context_recall"] == "grading_timeout"
    assert scores["faithfulness"] == scores["answer_relevancy"] == 0.5
    assert scores["context_precision"] == 0.5


def test_cancelled_async_metric_leaves_no_running_task():
    cancelled = []

    class SlowMetric:
        async def a_measure(self, _case):
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(True)

    async def exercise_deadline():
        with pytest.raises(asyncio.TimeoutError):
            await golden._measure_with_timeout(SlowMetric(), object(), timeout=0.01)
        remaining = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert remaining == []

    asyncio.run(exercise_deadline())
    assert cancelled == [True]
