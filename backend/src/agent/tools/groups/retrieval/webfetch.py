"""
Hardware RAG Agent — WebFetchTool (retrieval group).

Fetches a URL, converts HTML to markdown, and returns the content along
with the user's prompt. The Agent (not the tool) interprets the content
against the prompt — keeping the tool free of LLM calls avoids a tool→LLM
loop and lets the Agent apply its own reasoning + source citation.

Markdown conversion prefers `markdownify`, falls back to `html2text`, then
to a regex tag stripper. Content is truncated to _MAX_CONTENT_BYTES.

Spec: add-grep-glob-todo-tools §Requirement: webfetch tool (Task 9).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)
_httpx_logger = logging.getLogger("httpx")

_WEBFETCH_HTTPX_URL_REDACTION = contextvars.ContextVar(
    "webfetch_httpx_url_redaction",
    default=False,
)
_HTTP_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_HTTPX_LOG_FILTER_LOCK = threading.Lock()
_HTTPX_LOG_FILTER_ACTIVE_SCOPES = 0


class _WebFetchHTTPXURLFilter(logging.Filter):
    """Redact URLs only in HTTPX records emitted by a WebFetch context."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _WEBFETCH_HTTPX_URL_REDACTION.get():
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted_message = _HTTP_URL_PATTERN.sub("[URL redacted]", message)
        if redacted_message != message:
            record.msg = redacted_message
            record.args = ()
        return True


_WEBFETCH_HTTPX_URL_FILTER = _WebFetchHTTPXURLFilter()


@contextmanager
def _webfetch_httpx_url_redaction_scope() -> Iterator[None]:
    """Temporarily redact HTTPX URLs for this context without affecting peers."""
    global _HTTPX_LOG_FILTER_ACTIVE_SCOPES

    context_token = _WEBFETCH_HTTPX_URL_REDACTION.set(True)
    registered = False
    try:
        with _HTTPX_LOG_FILTER_LOCK:
            if _HTTPX_LOG_FILTER_ACTIVE_SCOPES == 0:
                _httpx_logger.addFilter(_WEBFETCH_HTTPX_URL_FILTER)
            _HTTPX_LOG_FILTER_ACTIVE_SCOPES += 1
            registered = True
        yield
    finally:
        _WEBFETCH_HTTPX_URL_REDACTION.reset(context_token)
        if registered:
            with _HTTPX_LOG_FILTER_LOCK:
                _HTTPX_LOG_FILTER_ACTIVE_SCOPES -= 1
                if _HTTPX_LOG_FILTER_ACTIVE_SCOPES == 0:
                    _httpx_logger.removeFilter(_WEBFETCH_HTTPX_URL_FILTER)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_FETCH_TIMEOUT_SECONDS: int = 30
_MAX_CONTENT_BYTES: int = 50 * 1024
_USER_AGENT: str = "HardwareRAGAgent/1.0 WebFetchTool"
_TRUNCATE_SUFFIX: str = "...[truncated]"


# ═══════════════════════════════════════════
# Args schema
# ═══════════════════════════════════════════

class WebFetchArgs(BaseModel):
    url: str = Field(description="要抓取的网页 URL（含 http(s)://）")
    prompt: str = Field(description="希望从页面中提取的信息（由 Agent 自行理解内容后回答）")


# ═══════════════════════════════════════════
# Tool
# ═══════════════════════════════════════════

class WebFetchTool(ToolSpec):
    """Fetch a URL and return its content as markdown for the Agent to read."""
    name: str = "webfetch"
    description: str = (
        "抓取指定 URL 的网页内容并转为 markdown 返回。"
        "适用于查阅芯片官方手册、数据手册在线版、技术博客等。"
        "工具只返回内容，由 Agent 自行按 prompt 理解并回答（带来源标注）。"
        "内容超过 50KB 自动截断。"
    )
    args_schema: type = WebFetchArgs
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = _FETCH_TIMEOUT_SECONDS
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Fetch url, convert to markdown, return with prompt."""
        url = args.get("url", "")
        prompt = args.get("prompt", "")
        try:
            html = await self._fetch_html(url)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if exc.response.is_redirect:
                logger.warning(
                    "webfetch failed category=redirect_rejected status=%s",
                    status_code,
                )
                raise RuntimeError(
                    f"WebFetch rejected redirect response (HTTP {status_code}); "
                    "redirects are disabled."
                ) from None
            logger.warning(
                "webfetch failed category=http_status status=%s",
                status_code,
            )
            raise RuntimeError(
                f"WebFetch request failed with HTTP {status_code}."
            ) from None
        except httpx.TimeoutException:
            logger.warning("webfetch failed category=timeout")
            raise asyncio.TimeoutError("WebFetch request timed out.") from None
        except httpx.RequestError as exc:
            error_type = type(exc).__name__
            logger.warning(
                "webfetch failed category=network error_type=%s",
                error_type,
            )
            raise RuntimeError(
                f"WebFetch network request failed ({error_type})."
            ) from None
        except Exception as exc:
            logger.warning(
                "webfetch failed category=unexpected error_type=%s",
                type(exc).__name__,
            )
            raise RuntimeError("WebFetch request failed.") from None
        content, truncated = _html_to_markdown_truncated(html)
        if not content.strip():
            logger.warning("webfetch failed category=empty_body")
            raise RuntimeError("WebFetch response body is empty.")
        return {"url": url, "content": content, "prompt": prompt, "truncated": truncated}

    async def _fetch_html(self, url: str) -> str:
        """GET url with timeout, return response text."""
        with _webfetch_httpx_url_redaction_scope():
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
                resp = await client.get(url, headers={"User-Agent": _USER_AGENT})
                resp.raise_for_status()
                return resp.text


# ═══════════════════════════════════════════
# HTML → markdown conversion
# ═══════════════════════════════════════════

def _html_to_markdown_truncated(html: str) -> tuple[str, bool]:
    """Convert HTML to markdown and truncate to _MAX_CONTENT_BYTES."""
    md = _html_to_markdown(html)
    if len(md.encode("utf-8")) <= _MAX_CONTENT_BYTES:
        return md, False
    return _truncate_bytes(md), True


def _html_to_markdown(html: str) -> str:
    """Convert HTML to markdown via markdownify, html2text, or regex fallback."""
    try:
        from markdownify import markdownify
        return markdownify(html)
    except ImportError:
        return _html_to_markdown_fallback(html)


def _html_to_markdown_fallback(html: str) -> str:
    """Fallback: try html2text, then regex strip tags."""
    try:
        import html2text
        return html2text.html2text(html)
    except ImportError:
        return _strip_tags(html)


def _strip_tags(html: str) -> str:
    """Last-resort: strip script/style then all HTML tags."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text)


def _truncate_bytes(text: str) -> str:
    """Truncate text so its utf-8 encoding fits _MAX_CONTENT_BYTES."""
    encoded = text.encode("utf-8")[:_MAX_CONTENT_BYTES]
    return encoded.decode("utf-8", errors="ignore") + _TRUNCATE_SUFFIX
