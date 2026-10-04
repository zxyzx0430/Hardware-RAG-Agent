"""Unverified Agent paths must remain outside quality score denominators."""

from types import SimpleNamespace

from tests.rag_eval import run_eval
from tests.rag_eval.run_golden_eval import (
    GoldenSample, SampleResult, evaluate_sample, generate_report,
)
from tests.rag_eval.run_eval import Reporter


def test_golden_report_has_no_score_without_verified_samples():
    sample = GoldenSample(
        id="G001", question="GPIO?", standard_answer="MODER controls the GPIO mode.",
        reference_chunks=["MODER controls GPIO"], difficulty="easy",
        category="GPIO", target_doc="fixed.md", tags=["gpio"],
    )
    result = SampleResult(
        id="G001", question="GPIO?", error="RAG path: request_failed",
        path_evidence={"status": "request_failed"},
    )
    report, markdown = generate_report({}, [sample], [result], {"chat_mode": "agent"}, {})
    assert report["total_score"] is None
    assert report["scored_sample_count"] == 0
    assert report["path_status_counts"] == {"request_failed": 1}
    assert "not scored" in markdown


def test_rule_report_has_no_percentage_without_verified_samples(tmp_path):
    report = {
        "timestamp": "fixed", "total_questions": 1, "chat_mode": "agent",
        "weights": {
            "recall": 30, "answer_coverage": 25, "chunk_completeness": 25,
            "chunk_boundary": 10, "cross_section": 10,
        },
        "strategies": [{
            "name": "hybrid-800", "description": "fixed", "total_score": 0,
            "max_score": 0, "scored_count": 0,
            "path_status_counts": {"request_failed": 1},
            "question_results": [{
                "question_id": "G001", "difficulty": "easy", "question": "GPIO?",
                "target_doc": "fixed.md", "not_scored": True,
                "path_evidence": {"status": "request_failed"}, "error": "missing done",
            }],
            "dimension_scores": {key: 0 for key in (
                "recall", "answer_coverage", "chunk_completeness",
                "chunk_boundary", "cross_section",
            )},
        }],
    }
    path = Reporter(tmp_path).save_markdown(report, "fixed")
    text = path.read_text(encoding="utf-8")
    assert "未计分" in text
    assert "nan" not in text.lower()


def test_rule_report_shows_planned_questions_and_ingest_failure(tmp_path, monkeypatch):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def create_kb(self, name, strategy):
            raise RuntimeError("index service unavailable")

    monkeypatch.setattr(run_eval, "_fetch_builtin_embedding_config", lambda: {})
    monkeypatch.setattr(run_eval, "RAGTestClient", FailingClient)
    monkeypatch.setattr(run_eval, "OUTPUT_DIR", tmp_path)
    questions = [SimpleNamespace(id="G001"), SimpleNamespace(id="G002")]
    strategy = SimpleNamespace(name="fixed", description="fixed", chunk_method="recursive")

    report = run_eval.run_evaluation(
        api_key="", model="fixed", base_url="", embedding_key="",
        embedding_model="", embedding_base_url="", strategies=[strategy],
        questions=questions, doc_files=[],
    )
    failed = report["strategies"][0]
    assert failed["planned_count"] == 2
    assert failed["attempted_count"] == 0
    assert failed["question_results"] == []
    assert failed["path_status_counts"] == {"not_attempted": 2}
    assert failed["error"] == "index service unavailable"
    markdown = next(tmp_path.glob("rag_eval_*.md")).read_text(encoding="utf-8")
    assert "**可计分题数**: 0/2" in markdown
    assert "**已尝试题数**: 0/2" in markdown
    assert "**失败原因**: index service unavailable" in markdown


def test_rule_report_counts_http_failure_and_remaining_questions(tmp_path, monkeypatch):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def create_kb(self, name, strategy):
            return {"id": "kb-fixed"}

        def chat(self, question, kb_ids):
            raise TimeoutError("chat HTTP timeout")

    monkeypatch.setattr(run_eval, "_fetch_builtin_embedding_config", lambda: {})
    monkeypatch.setattr(run_eval, "RAGTestClient", FailingClient)
    monkeypatch.setattr(run_eval, "OUTPUT_DIR", tmp_path)
    questions = [
        SimpleNamespace(id="G001", difficulty="easy", question="GPIO?"),
        SimpleNamespace(id="G002", difficulty="easy", question="UART?"),
    ]
    strategy = SimpleNamespace(name="fixed", description="fixed", chunk_method="recursive")

    report = run_eval.run_evaluation(
        api_key="", model="fixed", base_url="", embedding_key="",
        embedding_model="", embedding_base_url="", strategies=[strategy],
        questions=questions, doc_files=[], keep_kb=True,
    )
    failed = report["strategies"][0]
    assert failed["planned_count"] == 2
    assert failed["attempted_count"] == 1
    assert failed["path_status_counts"] == {"request_failed": 1, "not_attempted": 1}
    assert failed["error"] == "chat HTTP timeout"
    markdown = next(tmp_path.glob("rag_eval_*.md")).read_text(encoding="utf-8")
    assert "**可计分题数**: 0/2" in markdown
    assert "**已尝试题数**: 1/2" in markdown
    assert "**失败原因**: chat HTTP timeout" in markdown


def test_rule_report_accumulates_stream_and_http_failures(tmp_path, monkeypatch):
    class MixedFailureClient:
        def __init__(self, **kwargs):
            self.calls = 0

        def create_kb(self, name, strategy):
            return {"id": "kb-fixed"}

        def chat(self, question, kb_ids):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("second request timed out")
            return {
                "answer": "", "sources": [], "error": "stream failed",
                "path_evidence": {"status": "request_failed"},
            }

    monkeypatch.setattr(run_eval, "_fetch_builtin_embedding_config", lambda: {})
    monkeypatch.setattr(run_eval, "RAGTestClient", MixedFailureClient)
    monkeypatch.setattr(run_eval, "OUTPUT_DIR", tmp_path)
    questions = [SimpleNamespace(
        id=f"G00{number}", difficulty="easy", question="GPIO?", target_doc="fixed.md",
    ) for number in (1, 2, 3)]
    strategy = SimpleNamespace(name="fixed", description="fixed", chunk_method="recursive")

    report = run_eval.run_evaluation(
        api_key="", model="fixed", base_url="", embedding_key="",
        embedding_model="", embedding_base_url="", strategies=[strategy],
        questions=questions, doc_files=[], keep_kb=True,
    )
    failed = report["strategies"][0]
    assert failed["planned_count"] == 3
    assert failed["attempted_count"] == 2
    assert failed["path_status_counts"] == {"request_failed": 2, "not_attempted": 1}
    assert len(failed["question_results"]) == 1


def test_golden_request_exception_counts_as_failed_path():
    sample = GoldenSample(
        id="G001", question="GPIO?", standard_answer="MODER controls the GPIO mode.",
        reference_chunks=["MODER controls GPIO"], difficulty="easy",
        category="GPIO", target_doc="fixed.md", tags=["gpio"],
    )

    class FailingClient:
        use_agent = True

        def chat(self, question):
            raise TimeoutError("chat request timed out")

    result = evaluate_sample(sample, 1, 1, FailingClient(), None, {}, "unused")
    assert result.error == "TimeoutError: chat request timed out"
    assert result.path_evidence["status"] == "request_failed"
    report, _ = generate_report({}, [sample], [result], {"chat_mode": "agent"}, {})
    assert report["path_status_counts"] == {"request_failed": 1}
    assert report["scored_sample_count"] == 0
