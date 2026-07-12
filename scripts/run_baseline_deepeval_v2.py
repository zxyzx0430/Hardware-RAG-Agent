#!/usr/bin/env python3
"""
Chunk-baseline-v2 — iterative DeepEval runner.

Loads ``data/benchmark/chunk-baseline-golden-v1.yaml`` (or a v2 variant),
calls the local Hardware RAG Agent API (``POST /api/chat``) for each sample,
then scores the result with five DeepEval metrics.

Outputs (per --round):
* data/benchmark/chunk-baseline-eval-v2-round{N}.json
* data/benchmark/chunk-baseline-eval-v2-round{N}.md
* data/benchmark/chunk-baseline-eval-v2-round{N}.log

Defaults are aligned with the historical high-score run:
  generation model = oc/deepseek-v4-flash
  judge model      = oc/deepseek-v4-flash
  system prompt    = RAG_OPTIMIZED_SYSTEM_PROMPT
"""
from __future__ import annotations

import argparse
import asyncio
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

import openai
import yaml
from deepeval.models.base_model import DeepEvalBaseLLM
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
DATASET_PATH = ROOT_DIR / "data" / "benchmark" / "chunk-baseline-golden-v1.yaml"
OUTPUT_DIR = ROOT_DIR / "data" / "benchmark"

sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from tests.rag_eval.run_golden_eval import (  # noqa: E402
    RAGChatClient,
    RAG_OPTIMIZED_SYSTEM_PROMPT,
)

DEFAULT_API_BASE = "http://127.0.0.1:58080/api"
DEFAULT_GENERATION_MODEL = "oc/deepseek-v4-flash"
DEFAULT_JUDGE_MODEL = "oc/deepseek-v4-flash"
OVERALL_TIMEOUT_SECONDS = 4 * 60 * 60
PER_SAMPLE_TIMEOUT_SECONDS = 12 * 60
PER_METRIC_TIMEOUT_SECONDS = 300
MAX_METRIC_RETRIES = 3
METRIC_RETRY_DELAYS = [2, 6, 12]
MAX_CONTEXT_CHARS = 3000

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


class OpenCodeJudge(DeepEvalBaseLLM):
    """DeepEval-compatible judge for OpenCode endpoints that reject max_tokens.

    Critical: a_generate MUST be truly async (use asyncio.to_thread) so that
    DeepEval's asyncio.gather() can parallelize concurrent LLM calls. A
    synchronous 'return self.generate(prompt)' blocks the event loop and
    forces sequential execution, causing metrics like faithfulness (4 calls)
    to always exceed the 60s per-metric timeout.
    """

    def __init__(self, model: str = "deepseek-v4-flash", api_key: str = "", base_url: str = ""):
        self.model_name = model
        # timeout=300s: opencode endpoints occasionally need >120s for a single
        # inference; a short client-side timeout triggers silent retries that
        # balloon total wall-clock time past PER_METRIC_TIMEOUT_SECONDS.
        # max_retries=0: our own MAX_METRIC_RETRIES loop handles retries.
        self.client = openai.OpenAI(
            api_key=api_key, base_url=base_url, timeout=300.0, max_retries=0
        )

    def load_model(self):
        return self.client

    def generate(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    async def a_generate(self, prompt: str) -> str:
        # Must offload the sync openai call to a worker thread, otherwise
        # the event loop is blocked and DeepEval's asyncio.gather() degrades
        # to sequential execution.
        return await asyncio.to_thread(self.generate, prompt)

    def get_model_name(self):
        return self.model_name


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


def _checkpoint_path(round_num: int) -> Path:
    return OUTPUT_DIR / f"chunk-baseline-eval-v2-round{round_num}.checkpoint.json"


def load_checkpoint(round_num: int) -> dict[str, Any]:
    """Load partial results from a previous run if available."""
    path = _checkpoint_path(round_num)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Loaded checkpoint with %d completed samples from %s", len(data.get("results", {})), path)
        return data
    except Exception as e:
        logger.warning("Failed to load checkpoint %s: %s", path, e)
        return {}


def save_checkpoint(round_num: int, results_map: dict[str, dict], config: dict) -> None:
    """Save partial results after each sample so we can resume."""
    path = _checkpoint_path(round_num)
    payload = {"config": config, "results": results_map}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to write checkpoint %s: %s", path, e)


def checkpoint_result_to_sample_result(data: dict) -> SampleResult:
    return SampleResult(
        id=data["id"],
        query=data["query"],
        source_pdf=data["source_pdf"],
        actual_output=data.get("actual_output", ""),
        retrieval_context=data.get("retrieval_context", []),
        scores=data.get("scores", {}),
        reasons=data.get("reasons", {}),
        latency_seconds=data.get("latency_seconds", 0.0),
        token_usage=data.get("token_usage", {}),
        error=data.get("error", ""),
    )


def sample_result_to_checkpoint_result(r: SampleResult) -> dict:
    return {
        "id": r.id,
        "query": r.query,
        "source_pdf": r.source_pdf,
        "actual_output": r.actual_output,
        "retrieval_context": r.retrieval_context,
        "scores": r.scores,
        "reasons": r.reasons,
        "latency_seconds": r.latency_seconds,
        "token_usage": r.token_usage,
        "error": r.error,
    }


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
    judge_base_url: str,
    judge_api_key: str,
) -> tuple[dict[str, float], dict[str, str]]:
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        FaithfulnessMetric,
        ContextualRecallMetric,
        ContextualPrecisionMetric,
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

    # OpenCode endpoints return empty content when max_tokens is supplied.
    if judge_base_url and "opencode" in judge_base_url.lower():
        judge_llm = OpenCodeJudge(model=judge_model, api_key=judge_api_key, base_url=judge_base_url)
    else:
        judge_llm = judge_model

    metrics_config = [
        ("context_recall", ContextualRecallMetric(model=judge_llm, threshold=0.0)),
        ("faithfulness", FaithfulnessMetric(model=judge_llm, threshold=0.0)),
        ("answer_relevancy", AnswerRelevancyMetric(model=judge_llm, threshold=0.0)),
        ("context_precision", ContextualPrecisionMetric(model=judge_llm, threshold=0.0)),
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
                if attempt <= len(METRIC_RETRY_DELAYS):
                    delay = METRIC_RETRY_DELAYS[attempt - 1]
                    logger.info("[DeepEval] %s %s retrying in %ds", sample.id, name, delay)
                    time.sleep(delay)
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
    judge_base_url: str,
    judge_api_key: str,
) -> SampleResult:
    result = SampleResult(id=sample.id, query=sample.query, source_pdf=sample.source_pdf)
    logger.info("=== [%d/%d] %s | %s ===", idx, total, sample.id, sample.query[:80])

    try:
        t0 = time.time()
        actual_output, retrieval_context, token_usage = _call_rag_with_timeout(
            client, sample.query, timeout=120.0
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
            sample, actual_output, retrieval_context, judge_model, judge_base_url, judge_api_key
        )
        result.scores = scores
        result.reasons = reasons
        logger.info(
            "[scores] %s CR=%.2f FA=%.2f AR=%.2f CP=%.2f",
            sample.id,
            scores.get("context_recall", 0),
            scores.get("faithfulness", 0),
            scores.get("answer_relevancy", 0),
            scores.get("context_precision", 0),
        )
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.exception("[%s] Evaluation failed", sample.id)

    return result


# Aligned with historical high-score run (run_golden_eval.py) weights.
SCORE_WEIGHTS = {
    "context_recall": 30,
    "faithfulness": 25,
    "answer_relevancy": 25,
    "context_precision": 20,
}
METRIC_NAMES = list(SCORE_WEIGHTS.keys())


def weighted_score(scores: dict[str, float]) -> float:
    """Return 0-1 weighted average matching historical scoring."""
    if not scores:
        return 0.0
    total = sum(scores.get(name, 0.0) * weight for name, weight in SCORE_WEIGHTS.items())
    return round(total / 100.0, 4)


def average_metric_scores(results: list[SampleResult]) -> dict[str, float]:
    avg: dict[str, float] = {}
    for name in METRIC_NAMES:
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
    dataset_path: Path,
) -> tuple[dict, str]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    overall = average_metric_scores(results)
    overall_avg = round(sum(overall.values()) / len(overall), 4) if overall else 0.0
    overall_weighted = weighted_score(overall)

    per_pdf: dict[str, dict] = {}
    for pdf in sorted(set(s.source_pdf for s in samples)):
        pdf_results = [r for r, s in zip(results, samples) if s.source_pdf == pdf and not r.error]
        per_pdf[pdf] = average_metric_scores(pdf_results)
        per_pdf[pdf]["_count"] = len(pdf_results)
        per_pdf[pdf]["_avg"] = round(sum(per_pdf[pdf][n] for n in METRIC_NAMES) / len(METRIC_NAMES), 4)
        per_pdf[pdf]["_weighted"] = weighted_score(per_pdf[pdf])

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
        "overall": {"average": overall_avg, "weighted": overall_weighted, "metrics": overall},
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
        f"# Chunk-Baseline-v2 DeepEval Round {config['round']} Report",
        "",
        f"- **Timestamp**: {timestamp}",
        f"- **Dataset**: `{dataset_path}`",
        f"- **KB ID**: {metadata.get('kb_id', 'builtin-001')}",
        f"- **Collection**: {metadata.get('collection', 'hardware-docs-test')}",
        f"- **Generation model**: `{config.get('model')}` @ `{config.get('base_url')}`",
        f"- **Judge model**: `{config.get('judge_model')}` @ `{config.get('judge_base_url')}`",
        "",
        "## Overall Average",
        "",
        f"**{overall_avg:.4f}** (simple average of 4 DeepEval metrics, 0-1 scale)",
        f"**{overall_weighted:.4f}** (weighted: CR 30 / FA 25 / AR 25 / CP 20)",
        "",
        "| Metric | Score | Weight |",
        "|--------|-------|--------|",
    ]
    for name in METRIC_NAMES:
        md_lines.append(f"| {name} | {overall[name]:.4f} | {SCORE_WEIGHTS[name]} |")

    md_lines.extend(["", "## Per-PDF Average", ""])
    md_lines.append("| PDF | Count | Avg | Weighted | " + " | ".join(METRIC_NAMES) + " |")
    md_lines.append("|-----|-------|-----|----------|" + "|".join(["-------"] * len(METRIC_NAMES)) + "|")
    for pdf in sorted(per_pdf):
        d = per_pdf[pdf]
        vals = " | ".join(f"{d[n]:.4f}" for n in METRIC_NAMES)
        md_lines.append(
            f"| `{Path(pdf).name}` | {d['_count']} | {d['_avg']:.4f} | {d['_weighted']:.4f} | {vals} |"
        )

    md_lines.extend(["", "## Per-Question Scores", ""])
    md_lines.append("| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |")
    md_lines.append("|----|-----|------|-----|----|----|----|----|-------|")
    for q in per_question:
        s = next((s for s in samples if s.id == q["id"]), None)
        pdf_name = Path(s.source_pdf).name if s else ""
        scores = q["scores"]
        md_lines.append(
            f"| {q['id']} | {pdf_name} | {q['question_type']} | {q['average']:.4f} | "
            f"{scores.get('context_recall', 0):.2f} | "
            f"{scores.get('faithfulness', 0):.2f} | "
            f"{scores.get('answer_relevancy', 0):.2f} | "
            f"{scores.get('context_precision', 0):.2f} | "
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
    offset = getattr(args, "offset", 0) or 0
    limit = getattr(args, "limit", 0) or 0
    if offset > 0:
        samples = samples[offset:]
        logger.info("Skipping first %d samples (offset mode)", offset)
    if limit > 0:
        samples = samples[:limit]
        logger.info("Limiting to first %d samples (fast iteration mode)", limit)
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
        "round": args.round,
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

    checkpoint = load_checkpoint(args.round) if not args.no_resume else {}
    completed_results: dict[str, SampleResult] = {}
    if checkpoint and "results" in checkpoint:
        for rid, rdata in checkpoint["results"].items():
            completed_results[rid] = checkpoint_result_to_sample_result(rdata)

    logger.info("=" * 60)
    logger.info("Chunk-baseline-v2 DeepEval Round %d starting | samples=%d | resume=%s", args.round, total, bool(completed_results))
    logger.info("RAG model=%s base_url=%s", args.model, args.base_url)
    logger.info("Judge model=%s base_url=%s", judge_model, judge_base_url)
    logger.info("API base=%s kb_id=%s", args.api_base_url, args.kb_id)
    logger.info("Parallel=%d", args.parallel)
    logger.info("=" * 60)

    pending_samples = [s for s in samples if s.id not in completed_results]
    if pending_samples:
        logger.info("Resuming: %d already done, %d pending", len(completed_results), len(pending_samples))

    results_map: dict[str, dict] = {}
    if checkpoint and "results" in checkpoint:
        results_map = dict(checkpoint["results"])

    results: list[SampleResult] = list(completed_results.values())
    t_start = time.time()

    def within_overall_timeout() -> bool:
        return time.time() - t_start < OVERALL_TIMEOUT_SECONDS

    def run_single(idx: int, sample: BaselineSample) -> SampleResult:
        try:
            return evaluate_sample(sample, idx, total, client, judge_model, judge_base_url, judge_api_key)
        except Exception as e:
            logger.exception("[%s] Evaluate failed", sample.id)
            return SampleResult(
                id=sample.id,
                query=sample.query,
                source_pdf=sample.source_pdf,
                error=f"{type(e).__name__}: {e}",
            )

    if args.parallel > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures: dict[concurrent.futures.Future, int] = {}
            for idx, sample in enumerate(pending_samples, len(completed_results) + 1):
                if not within_overall_timeout():
                    logger.warning("Overall timeout reached, stopping new submissions")
                    break
                fut = executor.submit(run_single, idx, sample)
                futures[fut] = idx

            for fut in concurrent.futures.as_completed(futures):
                try:
                    result = fut.result(timeout=PER_SAMPLE_TIMEOUT_SECONDS)
                except concurrent.futures.TimeoutError:
                    idx = futures[fut]
                    sample = pending_samples[idx - len(completed_results) - 1]
                    result = SampleResult(
                        id=sample.id,
                        query=sample.query,
                        source_pdf=sample.source_pdf,
                        error=f"Per-sample timeout after {PER_SAMPLE_TIMEOUT_SECONDS}s",
                    )
                    logger.error("[%s] Per-sample timeout", sample.id)
                except Exception as e:
                    idx = futures[fut]
                    sample = pending_samples[idx - len(completed_results) - 1]
                    result = SampleResult(
                        id=sample.id,
                        query=sample.query,
                        source_pdf=sample.source_pdf,
                        error=f"{type(e).__name__}: {e}",
                    )
                    logger.exception("[%s] Future failed", sample.id)
                results.append(result)
                results_map[result.id] = sample_result_to_checkpoint_result(result)
                save_checkpoint(args.round, results_map, config)
    else:
        for idx, sample in enumerate(pending_samples, len(completed_results) + 1):
            if not within_overall_timeout():
                result = SampleResult(
                    id=sample.id,
                    query=sample.query,
                    source_pdf=sample.source_pdf,
                    error="Skipped due to overall timeout",
                )
                results.append(result)
                results_map[result.id] = sample_result_to_checkpoint_result(result)
                save_checkpoint(args.round, results_map, config)
                continue
            result = run_single(idx, sample)
            results.append(result)
            results_map[result.id] = sample_result_to_checkpoint_result(result)
            save_checkpoint(args.round, results_map, config)

    results.sort(key=lambda r: r.id)
    samples_sorted = sorted(samples, key=lambda s: s.id)
    logger.info("=== Evaluation loop done | total=%.0fs ===", time.time() - t_start)

    report, md = generate_report(metadata, samples_sorted, results, config, Path(args.dataset))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_json = OUTPUT_DIR / f"chunk-baseline-eval-v2-round{args.round}.json"
    output_md = OUTPUT_DIR / f"chunk-baseline-eval-v2-round{args.round}.md"

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md)

    logger.info("Saved JSON: %s", output_json)
    logger.info("Saved MD:   %s", output_md)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk-baseline-v2 DeepEval iterative runner")
    parser.add_argument("--dataset", default=str(DATASET_PATH), help="Path to golden dataset YAML")
    parser.add_argument("--round", type=int, default=0, help="Iteration round number")
    parser.add_argument("--api-key", default=os.getenv("LLM_API_KEY", ""), help="LLM API key")
    parser.add_argument("--model", default=os.getenv("LLM_MODEL", DEFAULT_GENERATION_MODEL), help="LLM model")
    parser.add_argument("--base-url", default=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"), help="LLM base URL")
    parser.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", DEFAULT_JUDGE_MODEL), help="Judge model")
    parser.add_argument("--judge-base-url", default=os.getenv("JUDGE_BASE_URL", os.getenv("LLM_BASE_URL", "")), help="Judge base URL")
    parser.add_argument("--judge-api-key", default=os.getenv("JUDGE_API_KEY", os.getenv("LLM_API_KEY", "")), help="Judge API key")
    parser.add_argument("--api-base-url", default=DEFAULT_API_BASE, help="Hardware RAG Agent API base")
    parser.add_argument("--kb-id", default="builtin-001", help="Knowledge base ID")
    parser.add_argument("--top-k", type=int, default=12, help="Top-k retrieval count")
    parser.add_argument("--threshold", type=float, default=0.15, help="Relevance threshold")
    parser.add_argument("--parallel", type=int, default=1, help="Parallel worker count")
    parser.add_argument("--no-system-prompt", action="store_true", help="Disable optimized system prompt")
    parser.add_argument("--no-resume", action="store_true", help="Ignore existing checkpoint and start fresh")
    parser.add_argument("--limit", type=int, default=0, help="Only run first N samples (0=all, for fast iteration)")
    parser.add_argument("--offset", type=int, default=0, help="Skip first N samples (0=none)")
    args = parser.parse_args()

    output_log = OUTPUT_DIR / f"chunk-baseline-eval-v2-round{args.round}.log"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_mode = "a" if _checkpoint_path(args.round).exists() and not args.no_resume else "w"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(output_log, mode=log_mode, encoding="utf-8"),
        ],
        force=True,
    )

    report = run(args)

    print("\n" + "=" * 60)
    print(f"Chunk-baseline-v2 DeepEval Round {args.round} complete")
    print(f"  Overall average: {report['overall']['average']:.4f}")
    print(f"  Successful / Total: {report['summary']['successful']} / {report['summary']['total']}")
    print(f"  JSON: {OUTPUT_DIR / f'chunk-baseline-eval-v2-round{args.round}.json'}")
    print(f"  MD:   {OUTPUT_DIR / f'chunk-baseline-eval-v2-round{args.round}.md'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
