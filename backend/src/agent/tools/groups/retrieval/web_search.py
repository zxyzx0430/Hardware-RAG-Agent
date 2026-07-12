"""
Hardware RAG Agent — Tavily web_search ToolSpec (retrieval group).

Optional tool: only active when TAVILY_API_KEY is set. Free tier 1000 req/month.
On any failure (missing key, network, API error) returns a graceful fallback
message so the Agent keeps running on local KB alone.

Uses httpx directly (no tavily-python package dependency). Tavily API is a
simple POST /search — wrapping it in a client library adds a dependency for
no benefit.

Migrated from tools/web_search.py (Task 1, SubTask 1.3).
industrial-tool-runtime Task 2: refactored to ToolSpec.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval.search_docs import build_source_event_from_dict

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Constants (spec §6.2)
# ═══════════════════════════════════════════

DEFAULT_MAX_RESULTS: int = 5
MAX_RESULT_CHARS: int = 300
_SEARCH_TIMEOUT_SECONDS: int = 30
_TAVILY_DEFAULT_BASE_URL: str = "https://api.tavily.com"

# Truncation limits for Tavily result fields (display + safety caps).
_TITLE_MAX_CHARS: int = 100
_URL_MAX_CHARS: int = 500
_SUMMARY_TITLE_MAX_CHARS: int = 80

# Relevance thresholds (match search_docs for consistent source coloring)
_RELEVANCE_HIGH_THRESHOLD: float = 0.8
_RELEVANCE_MEDIUM_THRESHOLD: float = 0.5


def _clamp_score(score: Any) -> float:
    """Clamp Tavily relevance score to [0.0, 1.0] to avoid >100% percentages."""
    try:
        value = float(score or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return min(1.0, max(0.0, value))


# ═══════════════════════════════════════════
# Args schema
# ═══════════════════════════════════════════

class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索查询，例如 'ESP32-S3 datasheet GPIO electrical characteristics'")
    max_results: int = Field(
        default=DEFAULT_MAX_RESULTS,
        description=f"最大结果数，默认 {DEFAULT_MAX_RESULTS}",
    )


# ═══════════════════════════════════════════
# Tool
# ═══════════════════════════════════════════

class WebSearchOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    results: list[dict] = Field(default_factory=list, description="网页搜索结果列表")
    error: str | None = Field(None, description="失败时的错误信息")


class WebSearchTool(ToolSpec):
    """Search the web via Tavily. Returns summarized results; never raises."""
    name: str = "web_search"
    description: str = (
        "搜索互联网获取最新信息（芯片 errata、新版手册、社区讨论）。"
        "本地知识库没有结果时再用。返回网页摘要列表。"
    )
    args_schema: type = WebSearchArgs
    output_schema: type[BaseModel] | None = WebSearchOutput

    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = _SEARCH_TIMEOUT_SECONDS
    max_retries: int = 1

    _tavily_api_key: str = PrivateAttr(default="")
    _tavily_base_url: str = PrivateAttr(default="")

    def __init__(self, tavily_api_key: str = "", tavily_base_url: str = ""):
        super().__init__()
        self._tavily_api_key = tavily_api_key or ""
        self._tavily_base_url = (tavily_base_url or _TAVILY_DEFAULT_BASE_URL).rstrip("/")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run web search; return error dict with detail on any failure."""
        query = args.get("query", "")
        max_results = args.get("max_results", DEFAULT_MAX_RESULTS)
        if not self._tavily_api_key:
            logger.info("web_search skipped: no TAVILY_API_KEY configured")
            return {"output": "网页搜索失败：未配置 Tavily API Key，请在设置页配置。", "results": []}
        try:
            return await self._do_search(query, max_results, ctx)
        except Exception as exc:
            logger.warning("web_search failed (degraded): %s", exc)
            return {"output": f"网页搜索失败：{exc}", "results": [], "error": str(exc)}

    async def _do_search(self, query: str, max_results: int, ctx: ToolContext) -> dict:
        """Call Tavily /search endpoint via httpx; format results. Raises on failure."""
        url = f"{self._tavily_base_url}/search"
        payload = {"query": query, "max_results": max_results, "api_key": self._tavily_api_key}
        headers = {"Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            raw = response.json()
        results = _extract_results(raw)
        simplified = _simplify_results(results, ctx)
        summary = _build_summary(query, simplified)
        return {"output": summary, "results": simplified}


def _extract_results(raw: Any) -> list[dict]:
    """Pull the results list out of Tavily response (dict or object)."""
    if isinstance(raw, dict):
        return raw.get("results", []) or []
    return getattr(raw, "results", []) or []


def _simplify_results(results: list[dict], ctx: ToolContext) -> list[dict]:
    """Truncate each result and assign global srcN IDs via ToolContext.source_counter."""
    out: list[dict] = []
    for r in results:
        ctx.source_counter += 1
        src_id = f"src{ctx.source_counter}"
        score = _clamp_score(r.get("score", 0.0))
        content = (r.get("content") or "")[:MAX_RESULT_CHARS]
        title = (r.get("title", "") or "(untitled)")[:_TITLE_MAX_CHARS]
        url = (r.get("url", "") or "")[:_URL_MAX_CHARS]
        out.append({
            "id": src_id,
            "title": title,
            "doc": url,
            "source_url": url,
            "category": "web",
            "score": score,
            "score_percentage": round(score * 100, 1),
            "relevance_level": _classify_relevance(score),
            "content": content,
            "excerpt": content,
        })
    return out


def _classify_relevance(score: float) -> str:
    """Map Tavily score to high/medium/low label."""
    if score >= _RELEVANCE_HIGH_THRESHOLD:
        return "high"
    if score >= _RELEVANCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _build_summary(query: str, simplified: list[dict]) -> str:
    """Render a short text summary for the LLM; require [srcN] citations."""
    if not simplified:
        return f"未找到与 '{query}' 相关的网页。"
    lines = [f"找到 {len(simplified)} 条网页结果："]
    for r in simplified:
        sid = r.get("id", "src?")
        title = (r["title"] or r.get("source_url") or "(untitled)")[:_SUMMARY_TITLE_MAX_CHARS]
        lines.append(f"  [{sid}] {title} — {r['source_url']}")
    lines.append("回答时必须用 [srcN] 格式引用上述网页结果，N 对应 src1/src2/...")
    return "\n".join(lines)
