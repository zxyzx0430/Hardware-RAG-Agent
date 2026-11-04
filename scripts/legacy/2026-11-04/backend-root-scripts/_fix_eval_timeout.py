import re

path = r"E:\Desktop\agent\backend\tests\rag_eval\run_golden_eval.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_constants = """MAX_CONTEXT_CHARS = 2000
MAX_METRIC_RETRIES = 5
METRIC_RETRY_DELAY = 5
METRIC_HARD_TIMEOUT = 90


def _truncate_context(texts: list[str], max_chars: int = MAX_CONTEXT_CHARS) -> list[str]:
    \"\"\"截断过长的 context 文本，减少 judge LLM 的输入 token 量。

    9router 代理 nginx 超时约 60s，prompt 越长 LLM 响应越慢，容易 504。
    截断后 judge 仍能获得足够信息做判断，但输入 token 减少约 50%。
    \"\"\"
    return [t[:max_chars] + "...[截断]" if len(t) > max_chars else t for t in texts]


def _measure_with_timeout(metric, test_case, timeout: int = METRIC_HARD_TIMEOUT):
    \"\"\"用线程池包装 metric.measure()，超时则抛出 TimeoutError。\"\"\"
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(metric.measure, test_case)
        try:
            future.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"metric.measure() timed out after {timeout}s")


def setup_deepeval_judge(judge_model: str, judge_base_url: str, judge_api_key: str):
    \"\"\"Configure DeepEval to use a custom OpenAI-compatible LLM judge.

    DeepEval reads OPENAI_API_KEY and OPENAI_API_BASE from environment.
    We set them here so all metrics use the specified judge model.
    \"\"\"
    os.environ["OPENAI_API_KEY"] = judge_api_key
    if judge_base_url:
        # OpenAI Python SDK (v1+) reads OPENAI_BASE_URL, not OPENAI_API_BASE.
        # Set both for compatibility with older clients.
        os.environ["OPENAI_BASE_URL"] = judge_base_url
        os.environ["OPENAI_API_BASE"] = judge_base_url


MAX_CONTEXT_CHARS = 2000
MAX_METRIC_RETRIES = 5
METRIC_RETRY_DELAY = 5
METRIC_HARD_TIMEOUT = 90"""

new_constants = """MAX_CONTEXT_CHARS = 2000
MAX_METRIC_RETRIES = 5
METRIC_RETRY_DELAY = 5
METRIC_HARD_TIMEOUT = 90


def _truncate_context(texts: list[str], max_chars: int = MAX_CONTEXT_CHARS) -> list[str]:
    \"\"\"截断过长的 context 文本，减少 judge LLM 的输入 token 量。

    9router 代理 nginx 超时约 60s，prompt 越长 LLM 响应越慢，容易 504。
    截断后 judge 仍能获得足够信息做判断，但输入 token 减少约 50%。
    \"\"\"
    return [t[:max_chars] + "...[截断]" if len(t) > max_chars else t for t in texts]


def _measure_with_timeout(metric, test_case, timeout: int = METRIC_HARD_TIMEOUT):
    \"\"\"用线程池包装 metric.measure()，超时则抛出 TimeoutError。\"\"\"
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(metric.measure, test_case)
        try:
            future.result(timeout=timeout)
        except FuturesTimeout:
            raise TimeoutError(f"metric.measure() timed out after {timeout}s")


def setup_deepeval_judge(judge_model: str, judge_base_url: str, judge_api_key: str):
    \"\"\"Configure DeepEval to use a custom OpenAI-compatible LLM judge.

    DeepEval reads OPENAI_API_KEY and OPENAI_API_BASE from environment.
    We set them here so all metrics use the specified judge model.
    \"\"\"
    os.environ["OPENAI_API_KEY"] = judge_api_key
    if judge_base_url:
        # OpenAI Python SDK (v1+) reads OPENAI_BASE_URL, not OPENAI_API_BASE.
        # Set both for compatibility with older clients.
        os.environ["OPENAI_BASE_URL"] = judge_base_url
        os.environ["OPENAI_API_BASE"] = judge_base_url"""

assert old_constants in content, "OLD BLOCK NOT FOUND"
content = content.replace(old_constants, new_constants)
print(f"Step 1: constants + helpers cleaned (removed duplicate)")


old_run = """    # Build test case
    # - retrieval_context: what the RAG system actually retrieved
    # - expected_output: the golden standard answer
    # - context: the golden reference chunks (ground truth for recall)
    test_case = LLMTestCase(
        input=sample.question,
        actual_output=actual_output,
        expected_output=sample.standard_answer,
        retrieval_context=retrieval_context if retrieval_context else ["(no context retrieved)"],
        context=sample.reference_chunks,
    )

    scores = {}
    reasons = {}

    metrics_config = [
        ("context_recall", ContextualRecallMetric(model=judge_model)),
        ("faithfulness", FaithfulnessMetric(model=judge_model)),
        ("answer_relevancy", AnswerRelevancyMetric(model=judge_model)),
        ("context_precision", ContextualPrecisionMetric(model=judge_model)),
    ]

    # Metric-level retry wrapper — 9router proxy intermittently returns 504
    # Gateway Timeout; DeepEval's internal retry (2x) is not enough to ride out
    # the storm. We retry each whole metric up to MAX_METRIC_RETRIES times.
    MAX_METRIC_RETRIES = 3
    METRIC_RETRY_DELAY = 15  # seconds

    for name, metric in metrics_config:
        t0 = time.time()
        logger.info(f"[DeepEval] {sample.id} running {name}...")
        last_err: Exception | None = None
        for attempt in range(1, MAX_METRIC_RETRIES + 1):
            try:
                metric.measure(test_case)
                elapsed = time.time() - t0
                scores[name] = float(metric.score) if metric.score is not None else 0.0
                reasons[name] = getattr(metric, "reason", "") or ""
                logger.info(f"[DeepEval] {sample.id} {name} = {scores[name]:.4f} "
                            f"({elapsed:.1f}s, attempt {attempt}/{MAX_METRIC_RETRIES}) "
                            f"reason={reasons[name][:150]!r}")
                if scores[name] < 0.5:
                    logger.warning(f"[DeepEval] {sample.id} {name} LOW ({scores[name]:.2f}): "
                                   f"{reasons[name][:250]}")
                last_err = None
                break  # success, move to next metric
            except Exception as e:
                last_err = e
                elapsed = time.time() - t0
                logger.warning(f"[DeepEval] {sample.id} {name} attempt {attempt}/{MAX_METRIC_RETRIES} "
                               f"FAILED after {elapsed:.1f}s: {type(e).__name__}: {str(e)[:200]}")
                if attempt < MAX_METRIC_RETRIES:
                    logger.info(f"[DeepEval] {sample.id} {name} retrying in {METRIC_RETRY_DELAY}s...")
                    time.sleep(METRIC_RETRY_DELAY)
        if last_err is not None:
            elapsed = time.time() - t0
            scores[name] = 0.0
            reasons[name] = (f"Metric error after {MAX_METRIC_RETRIES} attempts: "
                             f"{type(last_err).__name__}: {last_err}")"""

new_run = """    ctx = _truncate_context(retrieval_context) if retrieval_context else ["(no context retrieved)"]
    test_case = LLMTestCase(
        input=sample.question,
        actual_output=actual_output,
        expected_output=sample.standard_answer,
        retrieval_context=ctx,
        context=sample.reference_chunks,
    )

    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}

    metrics_config = [
        ("context_recall", ContextualRecallMetric(model=judge_model)),
        ("faithfulness", FaithfulnessMetric(model=judge_model)),
        ("answer_relevancy", AnswerRelevancyMetric(model=judge_model)),
        ("context_precision", ContextualPrecisionMetric(model=judge_model)),
    ]

    for name, metric in metrics_config:
        t0 = time.time()
        logger.info(f"[DeepEval] {sample.id} running {name}...")
        last_err: Exception | None = None
        for attempt in range(1, MAX_METRIC_RETRIES + 1):
            try:
                _measure_with_timeout(metric, test_case)
                elapsed = time.time() - t0
                scores[name] = float(metric.score) if metric.score is not None else 0.0
                reasons[name] = getattr(metric, "reason", "") or ""
                logger.info(f"[DeepEval] {sample.id} {name} = {scores[name]:.4f} "
                            f"({elapsed:.1f}s, attempt {attempt}/{MAX_METRIC_RETRIES}) "
                            f"reason={reasons[name][:150]!r}")
                if scores[name] < 0.5:
                    logger.warning(f"[DeepEval] {sample.id} {name} LOW ({scores[name]:.2f}): "
                                   f"{reasons[name][:250]}")
                last_err = None
                break
            except Exception as e:
                last_err = e
                elapsed = time.time() - t0
                logger.warning(f"[DeepEval] {sample.id} {name} attempt {attempt}/{MAX_METRIC_RETRIES} "
                               f"FAILED after {elapsed:.1f}s: {type(e).__name__}: {str(e)[:200]}")
                if attempt < MAX_METRIC_RETRIES:
                    logger.info(f"[DeepEval] {sample.id} {name} retrying in {METRIC_RETRY_DELAY}s...")
                    time.sleep(METRIC_RETRY_DELAY)
        if last_err is not None:
            elapsed = time.time() - t0
            scores[name] = 0.0
            reasons[name] = (f"Metric error after {MAX_METRIC_RETRIES} attempts: "
                             f"{type(last_err).__name__}: {last_err}")"""

assert old_run in content, "OLD RUN BLOCK NOT FOUND"
content = content.replace(old_run, new_run)
print("Step 2: run_deepeval_metrics updated (truncate + timeout + retry)")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"File written: {path}")
print(f"Total lines: {len(content.splitlines())}")
