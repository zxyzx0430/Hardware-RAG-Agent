#!/usr/bin/env python3
"""
Chunk-baseline-v1 Phase 5 — DeepEval baseline runner.

Loads ``data/benchmark/chunk-baseline-golden-v1.yaml``, calls the local
Hardware RAG Agent API (``POST /api/chat``) for each sample, then scores the
result with five DeepEval metrics:

* answer_relevancy
* faithfulness
* context_recall
* context_precision
* context_relevancy

Outputs:
* data/benchmark/chunk-baseline-eval-v1.json
* data/benchmark/chunk-baseline-eval-v1.md
* data/benchmark/chunk-baseline-eval-v1.log

Configuration is read from ``backend/.env`` (LLM_API_KEY, LLM_BASE_URL,
LLM_MODEL) and can be overridden via CLI flags. No API keys are hard-coded.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
DATASET_PATH = ROOT_DIR / "data" / "benchmark" / "chunk-baseline-golden-v1.yaml"
OUTPUT_DIR = ROOT_DIR / "data" / "benchmark"
OUTPUT_JSON = OUTPUT_DIR / "chunk-baseline-eval-v1.json"
OUTPUT_MD = OUTPUT_DIR / "chunk-baseline-eval-v1.md"
OUTPUT_LOG = OUTPUT_DIR / "chunk-baseline-eval-v1.log"

sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from tests.rag_eval.run_golden_eval import (  # noqa: E402
    RAGChatClient,
    RAG_OPTIMIZED_SYSTEM_PROMPT,
)

DEFAULT_API_BASE = "http://127.0.0.1:58080/api"
OVERALL_TIMEOUT_SECONDS = 30 * 60
PER_SAMPLE_TIMEOUT_SECONDS = 2 * 60
PER_METRIC_TIMEOUT_SECONDS = 70
MAX_METRIC_RETRIES = 1
METRIC_RETRY_DELAY = 3
MAX_CONTEXT_CHARS = 1200

logger = logging.getLogger(__name__)


@dataclass
class BaselineSample:
    id: str
    query: str
    expected_answer: str
    source_pdf: str
    source_pages: list[int]
    relevant_chunks: list[str]
    question_type: str


@dataclass
class SampleResult:
    id: str
    query: str
    source_pdf: str
    actual_output: str = ""
    retrieval_context: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    latency_seconds: float = 0.0
    token_usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def load_dataset(path: Path) -> tuple[dict, list[BaselineSample]]:
    if not path.exists():
        raise FileNotFoundError(f"Golden dataset not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "samples" not in data:
        raise ValueError("Invalid dataset: missing 'samples' key")

    samples = []
    for s in data["samples"]:
        samples.append(
            BaselineSample(
                id=s["id"],
                query=s["query"],
                expected_answer=s["expected_answer"],
                source_pdf=s.get("source_pdf", ""),
                source_pages=s.get("source_pages", []),
                relevant_chunks=s.get("relevant_chunks", []),
                question_type=s.get("question_type", "unknown"),
            )
        )
    return data.get("metadata", {}), samples


def _truncate_context(texts: list[str], max_chars: int = MAX_CONTEXT_CHARS) -> list[str]:
    return [t[:max_chars] + "...[truncated]" if len(t) > max_chars else t for t in texts]


def _measure_metric(metric, test_case, timeout: int) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(metric.measure, test_case)
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"metric.measure() timed out after {timeout}s")


def setup_deepeval_judge(api_key: str, base_url: str) -> None:
    os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
        os.environ["OPENAI_API_BASE"] = base_url
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")


def run_deepeval_metrics(
    sample: BaselineSample,
    actual_output: str,
    retrieval_context: list[str],
    judge_model: str,
) -> tuple[dict[str, float], dict[str, str]]:
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        FaithfulnessMetric,
        ContextualRecallMetric,
        ContextualPrecisionMetric,
        ContextualRelevancyMetric,
    )
    from deepeval.test_case import LLMTestCase

    ctx = _truncate_context(retrieval_context) if retrieval_context else ["(no context retrieved)"]
    test_case = LLMTestCase(
        input=sample.query,
        actual_output=actual_output,
        expected_output=sample.expected_answer,
        retrieval_context=ctx,
        context=sample.relevant_chunks,
    )

    metrics_config = [
        ("answer_relevancy", AnswerRelevancyMetric(model=judge_model, threshold=0.0)),
        ("faithfulness", FaithfulnessMetric(model=judge_model, threshold=0.0)),
        ("context_recall", ContextualRecallMetric(model=judge_model, threshold=0.0)),
        ("context_precision", ContextualPrecisionMetric(model=judge_model, threshold=0.0)),
        ("context_relevancy", ContextualRelevancyMetric(model=judge_model, threshold=0.0)),
    ]

    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}

    for name, metric in metrics_config:
        t0 = time.time()
        last_err: Exception | None = None
        for attempt in range(1, MAX_METRIC_RETRIES + 1):
            try:
                _measure_metric(metric, test_case, PER_METRIC_TIMEOUT_SECONDS)
                scores[name] = float(metric.score) if metric.score is not None else 0.0
                reasons[name] = getattr(metric, "reason", "") or ""
                logger.info(
                    "[DeepEval] %s %s = %.4f (%.1fs, attempt %d/%d)",
                    sample.id, name, scores[name], time.time() - t0, attempt, MAX_METRIC_RETRIES
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "[DeepEval] %s %s attempt %d/%d failed: %s: %s",
                    sample.id, name, attempt, MAX_METRIC_RETRIES, type(e).__name__, str(e)[:200]
                )
                if attempt < MAX_METRIC_RETRIES:
                    time.sleep(METRIC_RETRY_DELAY)
        if last_err is not None:
            scores[name] = 0.0
            reasons[name] = f"Metric error: {type(last_err).__name__}: {last_err}"

    return scores, reasons


def _call_rag_with_timeout(
    client: RAGChatClient, question: str, timeout: float
) -> tuple[str, list[str], dict]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.chat, question)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(f"RAG API call timed out after {timeout}s") from e


def evaluate_sample(
    sample: BaselineSample,
    idx: int,
    total: int,
    client: RAGChatClient,
    judge_model: str,
) -> SampleResult:
    result = SampleResult(id=sample.id, query=sample.query, source_pdf=sample.source_pdf)
    logger.info("=== [%d/%d] %s | %s ===", idx, total, sample.id, sample.query[:80])

    try:
        t0 = time.time()
        actual_output, retrieval_context, token_usage = _call_rag_with_timeout(
            client, sample.query, timeout=60.0
        )
        result.latency_seconds = time.time() - t0
        result.actual_output = actual_output
        result.retrieval_context = retrieval_context
        result.token_usage = token_usage or {}
        logger.info(
            "[RAG] %s answer_len=%d context=%d latency=%.1fs",
            sample.id, len(actual_output), len(retrieval_context), result.latency_seconds
        )

        scores, reasons = run_deepeval_metrics(
            sample, actual_output, retrieval_context, judge_model
        )
        result.scores = scores
        result.reasons = reasons
        logger.info(
            "[scores] %s AR=%.2f FA=%.2f CR=%.2f CP=%.2f CRel=%.2f",
            sample.id,
            scores.get("answer_relevancy", 0),
            scores.get("faithfulness", 0),
            scores.get("context_recall", 0),
            scores.get("context_precision", 0),
            scores.get("context_relevancy", 0),
        )
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.exception("[%s] Evaluation failed", sample.id)

    return result


def average_metric_scores(results: list[SampleResult]) -> dict[str, float]:
    avg: dict[str, float] = {}
    metric_names = ["answer_relevancy", "faithfulness", "context_recall", "context_precision", "context_relevancy"]
    for name in metric_names:
        vals = [r.scores.get(name, 0.0) for r in results if not r.error]
        avg[name] = round(sum(vals) / len(vals), 4) if vals else 0.0
    return avg


def per_question_average(result: SampleResult) -> float:
    vals = list(result.scores.values())
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def generate_report(
    metadata: dict,
    samples: list[BaselineSample],
    results: list[SampleResult],
    config: dict,
) -> tuple[dict, str]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    metric_names = ["answer_relevancy", "faithfulness", "context_recall", "context_precision", "context_relevancy"]

    overall = average_metric_scores(results)
    overall_avg = round(sum(overall.values()) / len(overall), 4) if overall else 0.0

    per_pdf: dict[str, dict] = {}
    for pdf in sorted(set(s.source_pdf for s in samples)):
        pdf_results = [r for r, s in zip(results, samples) if s.source_pdf == pdf and not r.error]
        per_pdf[pdf] = average_metric_scores(pdf_results)
        per_pdf[pdf]["_count"] = len(pdf_results)
        per_pdf[pdf]["_avg"] = round(sum(per_pdf[pdf][n] for n in metric_names) / len(metric_names), 4)

    per_question = []
    for s, r in zip(samples, results):
        avg = per_question_average(r)
        per_question.append(
            {
                "id": r.id,
                "query": r.query,
                "source_pdf": s.source_pdf,
                "question_type": s.question_type,
                "actual_output": r.actual_output[:500],
                "retrieval_context_count": len(r.retrieval_context),
                "scores": r.scores,
                "average": avg,
                "error": r.error,
                "latency_seconds": round(r.latency_seconds, 2),
            }
        )

    valid_q = [q for q in per_question if not q["error"]]
    lowest = min(valid_q, key=lambda x: x["average"]) if valid_q else None

    report = {
        "timestamp": timestamp,
        "metadata": metadata,
        "config": config,
        "overall": {"average": overall_avg, "metrics": overall},
        "per_pdf": per_pdf,
        "per_question": per_question,
        "lowest_question": lowest,
        "summary": {
            "total": len(samples),
            "successful": len([r for r in results if not r.error]),
            "failed": len([r for r in results if r.error]),
        },
    }

    md_lines = [
        "# Chunk-Baseline-v1 DeepEval Baseline Report",
        "",
        f"- **Timestamp**: {timestamp}",
        f"- **Dataset**: `{DATASET_PATH}`",
        f"- **KB ID**: {metadata.get('kb_id', 'builtin-001')}",
        f"- **Collection**: {metadata.get('collection', 'hardware-docs-test')}",
        f"- **Generation model**: `{config.get('model')}` @ `{config.get('base_url')}`",
        f"- **Judge model**: `{config.get('judge_model')}` @ `{config.get('judge_base_url')}`",
        "",
        "## Overall Average",
        "",
        f"**{overall_avg:.4f}** (average of 5 DeepEval metrics, 0-1 scale)",
        "",
        "| Metric | Score |",
        "|--------|-------|",
    ]
    for name in metric_names:
        md_lines.append(f"| {name} | {overall[name]:.4f} |")

    md_lines.extend(["", "## Per-PDF Average", ""])
    md_lines.append("| PDF | Count | Average | " + " | ".join(metric_names) + " |")
    md_lines.append("|-----|-------|---------|" + "|".join(["-------"] * len(metric_names)) + "|")
    for pdf in sorted(per_pdf):
        d = per_pdf[pdf]
        vals = " | ".join(f"{d[n]:.4f}" for n in metric_names)
        md_lines.append(f"| `{Path(pdf).name}` | {d['_count']} | {d['_avg']:.4f} | {vals} |")

    md_lines.extend(["", "## Per-Question Scores", ""])
    md_lines.append("| ID | PDF | Type | Avg | AR | FA | CR | CP | CRel | Error |")
    md_lines.append("|----|-----|------|-----|----|----|----|----|------|-------|")
    for q in per_question:
        s = next((s for s in samples if s.id == q["id"]), None)
        pdf_name = Path(s.source_pdf).name if s else ""
        scores = q["scores"]
        md_lines.append(
            f"| {q['id']} | {pdf_name} | {q['question_type']} | {q['average']:.4f} | "
            f"{scores.get('answer_relevancy', 0):.2f} | "
            f"{scores.get('faithfulness', 0):.2f} | "
            f"{scores.get('context_recall', 0):.2f} | "
            f"{scores.get('context_precision', 0):.2f} | "
            f"{scores.get('context_relevancy', 0):.2f} | "
            f"{q['error'][:30] if q['error'] else ''} |"
        )

    if lowest:
        md_lines.extend(["", "## Lowest-Scoring Question", ""])
        md_lines.append(f"- **ID**: {lowest['id']}")
        md_lines.append(f"- **Query**: {lowest['query']}")
        md_lines.append(f"- **Source PDF**: `{lowest['source_pdf']}`")
        md_lines.append(f"- **Average score**: {lowest['average']:.4f}")
        md_lines.append("- **Metric breakdown**:")
        for name, val in lowest["scores"].items():
            md_lines.append(f"  - {name}: {val:.4f}")
        md_lines.append(f"- **Actual output preview**: {lowest['actual_output'][:300]}...")

    return report, "\n".join(md_lines)


def run(args: argparse.Namespace) -> dict:
    metadata, samples = load_dataset(Path(args.dataset))
    total = len(samples)

    judge_model = args.judge_model
    judge_base_url = args.judge_base_url or args.base_url
    judge_api_key = args.judge_api_key or args.api_key
    setup_deepeval_judge(judge_api_key, judge_base_url)

    client = RAGChatClient(
        api_base_url=args.api_base_url,
        api_key=args.api_key,
        model=args.model,
        base_url=args.base_url,
        kb_ids=[args.kb_id] if args.kb_id else None,
        top_k=args.top_k,
        relevance_threshold=args.threshold,
        system_prompt=RAG_OPTIMIZED_SYSTEM_PROMPT if not args.no_system_prompt else None,
    )

    config = {
        "model": args.model,
        "base_url": args.base_url,
        "judge_model": judge_model,
        "judge_base_url": judge_base_url,
        "api_base_url": args.api_base_url,
        "kb_id": args.kb_id,
        "top_k": args.top_k,
        "threshold": args.threshold,
        "parallel": args.parallel,
    }

    logger.info("=" * 60)
    logger.info("Chunk-baseline DeepEval starting | samples=%d", total)
    logger.info("RAG model=%s base_url=%s", args.model, args.base_url)
    logger.info("Judge model=%s base_url=%s", judge_model, judge_base_url)
    logger.info("API base=%s kb_id=%s", args.api_base_url, args.kb_id)
    logger.info("Parallel=%d", args.parallel)
    logger.info("=" * 60)

    results: list[SampleResult] = []
    t_start = time.time()

    def within_overall_timeout() -> bool:
        return time.time() - t_start < OVERALL_TIMEOUT_SECONDS

    if args.parallel > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures: dict[concurrent.futures.Future, int] = {}
            for idx, sample in enumerate(samples, 1):
                if not within_overall_timeout():
                    logger.warning("Overall timeout reached, stopping new submissions")
                    break
                fut = executor.submit(evaluate_sample, sample, idx, total, client, judge_model)
                futures[fut] = idx

            for fut in concurrent.futures.as_completed(futures):
                try:
                    result = fut.result(timeout=PER_SAMPLE_TIMEOUT_SECONDS)
                except concurrent.futures.TimeoutError:
                    idx = futures[fut]
                    sample = samples[idx - 1]
                    result = SampleResult(
                        id=sample.id,
                        query=sample.query,
                        source_pdf=sample.source_pdf,
                        error=f"Per-sample timeout after {PER_SAMPLE_TIMEOUT_SECONDS}s",
                    )
                    logger.error("[%s] Per-sample timeout", sample.id)
                except Exception as e:
                    idx = futures[fut]
                    sample = samples[idx - 1]
                    result = SampleResult(
                        id=sample.id,
                        query=sample.query,
                        source_pdf=sample.source_pdf,
                        error=f"{type(e).__name__}: {e}",
                    )
                    logger.exception("[%s] Future failed", sample.id)
                results.append(result)
    else:
        for idx, sample in enumerate(samples, 1):
            if not within_overall_timeout():
                result = SampleResult(
                    id=sample.id,
                    query=sample.query,
                    source_pdf=sample.source_pdf,
                    error="Skipped due to overall timeout",
                )
                results.append(result)
                continue
            try:
                result = evaluate_sample(sample, idx, total, client, judge_model)
            except Exception as e:
                result = SampleResult(
                    id=sample.id,
                    query=sample.query,
                    source_pdf=sample.source_pdf,
                    error=f"{type(e).__name__}: {e}",
                )
                logger.exception("[%s] Evaluate failed", sample.id)
            results.append(result)

    results.sort(key=lambda r: r.id)
    samples_sorted = sorted(samples, key=lambda s: s.id)
    logger.info("=== Evaluation loop done | total=%.0fs ===", time.time() - t_start)

    report, md = generate_report(metadata, samples_sorted, results, config)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    logger.info("Saved JSON: %s", OUTPUT_JSON)
    logger.info("Saved MD:   %s", OUTPUT_MD)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk-baseline-v1 DeepEval baseline runner")
    parser.add_argument("--dataset", default=str(DATASET_PATH), help="Path to golden dataset YAML")
    parser.add_argument("--api-key", default=os.getenv("LLM_API_KEY", ""), help="LLM API key")
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", "gpt-4o-mini"), help="LLM model")
    parser.add_argument("--base-url", default=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"), help="LLM base URL")
    parser.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", "qwen-turbo"), help="Judge model (default: qwen-turbo)")
    parser.add_argument("--judge-base-url", default=os.getenv("EMBEDDING_BASE_URL", ""), help="Judge base URL (default: EMBEDDING_BASE_URL)")
    parser.add_argument("--judge-api-key", default=os.getenv("EMBEDDING_API_KEY", ""), help="Judge API key (default: EMBEDDING_API_KEY)")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE, help="Hardware RAG Agent API base")
    parser.add_argument("--kb-id", default="builtin-001", help="Knowledge base ID")
    parser.add_argument("--top-k", type=int, default=8, help="Top-k retrieval count")
    parser.add_argument("--threshold", type=float, default=0.0, help="Relevance threshold")
    parser.add_argument("--parallel", type=int, default=2, help="Parallel worker count")
    parser.add_argument("--no-system-prompt", action="store_true", help="Disable optimized system prompt")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(OUTPUT_LOG, mode="w", encoding="utf-8"),
        ],
        force=True,
    )

    report = run(args)

    print("\n" + "=" * 60)
    print("Chunk-baseline DeepEval complete")
    print(f"  Overall average: {report['overall']['average']:.4f}")
    print(f"  Successful / Total: {report['summary']['successful']} / {report['summary']['total']}")
    print(f"  JSON: {OUTPUT_JSON}")
    print(f"  MD:   {OUTPUT_MD}")
    print(f"  LOG:  {OUTPUT_LOG}")
    print("=" * 60)


if __name__ == "__main__":
    main()
