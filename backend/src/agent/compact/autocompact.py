"""Autocompact: LLM-powered summarization when token budget overflows.

When cumulative tokens exceed the caller-provided threshold (derived from
context_window * MAX_TOKEN_RATIO), the early message history is sent to an
LLM for summarization. The summary replaces the early messages as a single
SystemMessage, keeping only the most recent AUTOCOMPACT_KEEP_RECENT_MSGS
messages intact.

Circuit breaker: after AUTOCOMPACT_MAX_RETRIES consecutive failures, the
compaction is abandoned and a snipped message list (with deletion count) is
returned.

P1-T6: also provides recovery orchestration (autocompact + fallback model)
called by sse_adapter when the needs_compact flag is set.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.prompts import MAX_TOKEN_RATIO
from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Number of recent messages to keep intact (not summarized).
AUTOCOMPACT_KEEP_RECENT_MSGS: int = 10
# Max consecutive failures before abandoning autocompact.
AUTOCOMPACT_MAX_RETRIES: int = 3
# Fallback context window when config lacks one (matches model_registry default).
_DEFAULT_CONTEXT_WINDOW: int = 128000

# Summarizer prompt + fallback env var name.
_SUMMARIZER_SYSTEM: str = "你是一个对话摘要助手。"
_SUMMARIZER_PROMPT: str = (
    "你的任务是对以下对话创建详细摘要，重点保留：\n"
    "1. 用户的原始请求和意图\n"
    "2. 关键技术决策及理由\n"
    "3. 涉及的芯片型号、接线方案、文件路径\n"
    "4. 重要代码片段或配置\n"
    "5. 遇到的错误及解决方式\n"
    "6. 当前任务状态（做到哪一步）\n"
    "7. 待办事项和下一步\n"
    "用 500 字以内输出摘要。\n\n"
)
_SUMMARY_PREFIX: str = "[历史摘要]: "
_SNIP_NOTICE_TEMPLATE: str = "[已删除 {0} 条早期消息]"
_FALLBACK_MODEL_ENV: str = "FALLBACK_MODEL"
# Max chars of each early message included in the summarizer prompt.
_EARLY_MSG_CHAR_LIMIT: int = 500


def should_autocompact(messages: list, threshold: int) -> bool:
    """Return True when total estimated tokens exceed threshold."""
    total = sum(_msg_tokens(m) for m in messages)
    return total > threshold


def _msg_tokens(msg: Any) -> int:
    """Estimate tokens for a single message's content."""
    content = getattr(msg, "content", "")
    if not isinstance(content, str):
        content = str(content)
    return LLMClient._estimate_tokens(content)


async def autocompact_messages(
    messages: list, llm_client: Any, context_window: int,
) -> list:
    """Summarize early messages via LLM; keep recent ones intact.

    Returns a new list: [SystemMessage(summary), ...recent_messages].
    On repeated LLM failure, returns a snipped list with a deletion notice.
    """
    threshold = int(context_window * MAX_TOKEN_RATIO)
    if not should_autocompact(messages, threshold):
        return messages
    if len(messages) <= AUTOCOMPACT_KEEP_RECENT_MSGS:
        return messages
    early, recent = _split_messages(messages)
    summary = await _summarize_with_breaker(early, llm_client)
    if summary:
        return [SystemMessage(content=_SUMMARY_PREFIX + summary)] + recent
    snipped, _ = _snip_messages(messages, AUTOCOMPACT_KEEP_RECENT_MSGS)
    return snipped


def _split_messages(messages: list) -> tuple[list, list]:
    """Split into (early_to_summarize, recent_to_keep)."""
    cut = len(messages) - AUTOCOMPACT_KEEP_RECENT_MSGS
    return messages[:cut], messages[cut:]


async def _summarize_with_breaker(early: list, llm_client: Any) -> str:
    """Call LLM summarizer with circuit-breaker retry."""
    for attempt in range(AUTOCOMPACT_MAX_RETRIES):
        try:
            return await _call_summarizer(early, llm_client)
        except Exception as exc:
            logger.warning("autocompact attempt=%s failed: %s", attempt + 1, exc)
    logger.error("autocompact breaker open after %s tries", AUTOCOMPACT_MAX_RETRIES)
    return ""


async def _call_summarizer(early: list, llm_client: Any) -> str:
    """Call LLM to summarize early messages."""
    if llm_client is None:
        return ""
    text = _format_early_for_summary(early)
    response = await llm_client.chat(
        _SUMMARIZER_PROMPT + text,
        system_prompt=_SUMMARIZER_SYSTEM,
    )
    return _extract_response_text(response)


def _extract_response_text(response: Any) -> str:
    """Extract text from LLM response.content (str | list[ContentBlock] | None).

    Multimodal models return content as a list of ContentBlock objects; text
    models return a plain str. None / unexpected shapes fall back to "" so the
    summarizer never crashes on an unfamiliar content type.
    """
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            getattr(block, "text", "") for block in content if hasattr(block, "text")
        )
    if content is None:
        return ""
    return str(content)


def _format_early_for_summary(early: list) -> str:
    """Format early messages into a text block for the summarizer."""
    lines: list[str] = []
    for msg in early:
        role = type(msg).__name__.replace("Message", "").lower()
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = str(content)
        lines.append(f"[{role}]: {content[:_EARLY_MSG_CHAR_LIMIT]}")
    return "\n".join(lines)


def _snip_messages(messages: list, keep_recent: int) -> tuple[list, int]:
    """Drop early messages, keep recent with a deletion-count notice.

    Returns (new_messages, deleted_count). When messages fit within
    keep_recent, returns the original list unchanged with deleted_count=0.
    """
    deleted_count = max(len(messages) - keep_recent, 0)
    if deleted_count == 0:
        return messages, 0
    recent = messages[-keep_recent:]
    notice = SystemMessage(content=_SNIP_NOTICE_TEMPLATE.format(deleted_count))
    return [notice] + recent, deleted_count


# ═══════════════════════════════════════════
# P1-T6: Recovery orchestration (autocompact + fallback model)
# ═══════════════════════════════════════════

@dataclass
class RecoveryResult:
    """Outcome of a context-overflow recovery attempt."""
    should_retry: bool = False
    fallback_text: str = ""


async def attempt_context_recovery(
    agent: Any, config: dict, messages: list,
) -> RecoveryResult:
    """Try autocompact, then fallback model. Returns the recovery outcome.

    Step 1: autocompact messages + update agent state → should_retry.
    Step 2: if autocompact fails, try fallback model for a direct answer.
    """
    llm_client = _make_summary_client()
    if await _recover_with_autocompact(agent, config, messages, llm_client):
        return RecoveryResult(should_retry=True)
    fallback_text = await _try_fallback_model(messages)
    return RecoveryResult(fallback_text=fallback_text)


async def _recover_with_autocompact(
    agent: Any, config: dict, messages: list, llm_client: Any,
) -> bool:
    """Autocompact messages + update agent state. Returns True on success."""
    try:
        if not messages:
            return False
        context_window = _read_context_window(config)
        compacted = await autocompact_messages(messages, llm_client, context_window)
        # Same list object means no compaction happened (see autocompact_messages
        # no-op returns). A new list with equal length (e.g., 11 → 1 summary +
        # 10 recent) still reduced tokens — treat as success.
        if compacted is messages:
            return False
        await agent.update_state(config, {"messages": compacted})
        logger.info("autocompact_recovery done msg_count=%s", len(compacted))
        return True
    except Exception as exc:
        logger.error("autocompact_recovery failed: %s", exc)
        return False


def _make_summary_client() -> Any:
    """Build a LLMClient for summarization (uses global settings)."""
    try:
        from src.llm.client import LLMClient
        return LLMClient()
    except Exception as exc:
        logger.warning("summary_client init failed: %s", exc)
        return None


def _read_context_window(config: dict) -> int:
    """Read context_window from config's configurable section."""
    return config.get("configurable", {}).get(
        "context_window", _DEFAULT_CONTEXT_WINDOW,
    )


async def _try_fallback_model(messages: list) -> str:
    """Use fallback model to answer the last user question directly."""
    fallback_model = _read_fallback_model_env()
    if not fallback_model:
        return ""
    try:
        question = _extract_last_user_question(messages)
        if not question:
            return ""
        return await _call_fallback_llm(fallback_model, question)
    except Exception as exc:
        logger.error("fallback_model failed: %s", exc)
        return ""


def _read_fallback_model_env() -> str:
    """Read FALLBACK_MODEL env var. Empty string when unset."""
    return os.environ.get(_FALLBACK_MODEL_ENV, "").strip()


def _extract_last_user_question(messages: list) -> str:
    """Find the last HumanMessage content in the message list."""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


async def _call_fallback_llm(model: str, question: str) -> str:
    """Call the fallback model with a simple direct question."""
    from src.llm.client import LLMClient
    client = LLMClient()
    response = await client.chat(question, model=model)
    return response.content or ""
