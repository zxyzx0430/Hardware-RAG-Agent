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

import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

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
        except Exception as exc:
            logger.warning("webfetch failed for %s: %s", url, exc)
            return _fetch_error(url, prompt, exc)
        content, truncated = _html_to_markdown_truncated(html)
        return {"url": url, "content": content, "prompt": prompt, "truncated": truncated}

    async def _fetch_html(self, url: str) -> str:
        """GET url with timeout, return response text."""
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


# ═══════════════════════════════════════════
# Error builder
# ═══════════════════════════════════════════

def _fetch_error(url: str, prompt: str, exc: Exception) -> dict:
    """Build fetch-error result dict (covers HTTP errors + network failures)."""
    detail = _http_status(exc) or str(exc)
    return {"url": url, "content": "", "prompt": prompt, "truncated": False, "error": f"fetch failed: {detail}"}


def _http_status(exc: Exception) -> str | None:
    """Extract HTTP status string from httpx.HTTPStatusError, else None."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return None
