"""
Hardware RAG Agent — VisionAnalysisTool (retrieval group).

Analyzes image content (chip pinout diagrams, circuit schematics, datasheet
screenshots) via an OpenAI-compatible chat/completions endpoint with image_url
content. Supports both URL and base64 data-URI input.

Gracefully degrades: when vision_model or api_key is empty, returns a fallback
message so the Agent keeps running without the vision capability.

Task 3.1.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

_VISION_TIMEOUT_SECONDS: int = 60
_FALLBACK_OUTPUT: str = "vision provider 未配置：请在设置页选择视觉模型 provider"

# In-process image cache: front-end uploads → chat_routes caches data URI here →
# Agent calls vision_analysis(image="cache:image_id") → tool resolves from cache.
# Keeps base64 out of the LLM context (a single screenshot can be 100KB+).
_IMAGE_CACHE: dict[str, str] = {}
_IMAGE_CACHE_MAX: int = 100


def cache_image(data_uri: str) -> str:
    """Cache a base64 data URI; return image_id for vision_analysis to retrieve."""
    import uuid
    image_id = f"img_{uuid.uuid4().hex[:8]}"
    _IMAGE_CACHE[image_id] = data_uri
    _evict_if_needed()
    return image_id


def get_cached_image(image_id: str) -> str | None:
    """Return cached data URI for image_id, or None on miss."""
    return _IMAGE_CACHE.get(image_id)


def _evict_if_needed() -> None:
    """Drop oldest entries when cache exceeds _IMAGE_CACHE_MAX."""
    if len(_IMAGE_CACHE) <= _IMAGE_CACHE_MAX:
        return
    overflow = len(_IMAGE_CACHE) - _IMAGE_CACHE_MAX
    for key in list(_IMAGE_CACHE.keys())[:overflow]:
        _IMAGE_CACHE.pop(key, None)


# ═══════════════════════════════════════════
# Args / Output schemas
# ═══════════════════════════════════════════

class VisionAnalysisArgs(BaseModel):
    image: str = Field(description="图片 URL、base64 data URI、或 cache:image_id（前端上传图片的缓存引用）")
    question: str = Field(description="对图片的分析问题")


class VisionAnalysisOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    analysis: str = Field(description="模型对图片的分析结果")
    error: str | None = Field(None, description="错误信息")


# ═══════════════════════════════════════════
# Tool
# ═══════════════════════════════════════════

class VisionAnalysisTool(ToolSpec):
    """Analyze image content via an OpenAI-compatible vision model."""
    name: str = "vision_analysis"
    description: str = (
        "分析图片内容（芯片引脚图、电路图、数据手册截图等）。"
        "支持 URL 和 base64 输入。"
        "分析图片后如需查证手册中的具体参数，可结合 search_docs 检索本地知识库。"
    )
    args_schema: type = VisionAnalysisArgs
    output_schema: type[BaseModel] | None = VisionAnalysisOutput
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = _VISION_TIMEOUT_SECONDS
    max_retries: int = 1

    _vision_model: str = PrivateAttr(default="")
    _base_url: str = PrivateAttr(default="")
    _api_key: str = PrivateAttr(default="")

    def __init__(self, vision_model: str = "", base_url: str = "", api_key: str = ""):
        super().__init__()
        self._vision_model = vision_model or ""
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run vision analysis; return fallback dict on any failure."""
        if not self._vision_model or not self._api_key:
            logger.info("vision_analysis skipped: model or api_key not configured")
            return {"output": _FALLBACK_OUTPUT, "analysis": ""}
        try:
            image = _resolve_image_ref(args.get("image", ""))
            analysis = await self._call_vision(image, args.get("question", ""))
            return {"output": analysis, "analysis": analysis}
        except Exception as exc:
            logger.warning("vision_analysis failed: %s", exc)
            return {"output": _FALLBACK_OUTPUT, "analysis": "", "error": str(exc)}

    async def _call_vision(self, image: str, question: str) -> str:
        """POST chat/completions with image content; return analysis text.

        Some OpenAI-compatible providers (e.g. 9router) append SSE markers like
        `data: [DONE]` after the JSON body. We strip trailing non-JSON data.
        """
        import json
        url = self._base_url + "/chat/completions"
        body = self._build_body(image, question)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, headers=self._build_headers(), json=body)
            resp.raise_for_status()
            text = resp.text.strip()
            for marker in ("\ndata: [DONE]", "\r\ndata: [DONE]", "data: [DONE]"):
                if text.endswith(marker):
                    text = text[: -len(marker)].rstrip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                decoder = json.JSONDecoder()
                data, _ = decoder.raw_decode(text.lstrip())
            return _extract_content(data)

    def _build_headers(self) -> dict:
        """Build auth + content-type headers for the vision API."""
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _build_body(self, image: str, question: str) -> dict:
        """Build OpenAI-compatible chat completion request body with image."""
        return {
            "model": self._vision_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            }],
        }


def _extract_content(data: dict) -> str:
    """Extract the assistant message content from a chat completion response."""
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _resolve_image_ref(image_ref: str) -> str:
    """Resolve image ref: cache:image_id → cached data URI, else return as-is."""
    if image_ref.startswith("cache:"):
        image_id = image_ref[len("cache:"):]
        cached = get_cached_image(image_id)
        if cached:
            return cached
        logger.warning("vision_analysis image cache miss: %s", image_id)
        return ""
    return image_ref
