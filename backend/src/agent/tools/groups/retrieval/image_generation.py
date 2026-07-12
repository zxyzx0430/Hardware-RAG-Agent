"""
Hardware RAG Agent — ImageGenerationTool (retrieval group).

Generates images (wiring diagrams, circuit schematics) from text prompts via
an OpenAI-compatible API. Uses a 3-step fallback strategy:

  1. /images/generations — standard OpenAI image endpoint (dedicated image model).
  2. Async polling — POST /tasks then GET /tasks/{id} until succeeded.
  3. Chat completion — fallback for chat models that can generate images;
     parse markdown image / data URI from response text.

Gracefully degrades: when image_model or api_key is empty, returns a fallback
message so the Agent keeps running without the generation capability.

Task 3.2.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════

_IMAGE_TIMEOUT_SECONDS: int = 120
_POLL_TIMEOUT_SECONDS: int = 120
_POLL_INTERVAL_SECONDS: float = 2.0
_POLL_MAX_ATTEMPTS: int = 60
_DEFAULT_SIZE: str = "1024x1024"
_FALLBACK_OUTPUT: str = "图片生成失败，未配置图像模型或调用出错。"
_FAIL_OUTPUT: str = "图片生成失败。"
_SUCCESS_OUTPUT: str = "图片已生成"
# Max chars of HTTP error body kept in error message (prevents giant HTML dumps).
_HTTP_ERROR_BODY_MAX_CHARS: int = 200
# Max chars of chat response previewed in the "no image" error message.
_CHAT_PREVIEW_MAX_CHARS: int = 120

# Chat fallback prompt wrapper: guides multimodal chat models to return image
# in a parseable format (markdown image syntax or data URI).
_CHAT_PROMPT_TEMPLATE: str = (
    "You are an image generation assistant. Generate an image based on the "
    "following request and return ONLY the image. "
    "Use markdown image syntax ![image](URL) if you have an image URL, "
    "or return the image as a data URI (data:image/...;base64,...). "
    "Do not include any other text or explanation.\n\n"
    "Image request: {prompt}\n"
    "Size: {size}"
)

_MARKDOWN_IMAGE_RE = re.compile(r"!\[.*?\]\((https?://[^\s)]+)\)")
_DATA_URI_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+")


# ═══════════════════════════════════════════
# Args / Output schemas
# ═══════════════════════════════════════════

class ImageGenerationArgs(BaseModel):
    prompt: str = Field(description="图片生成提示词")
    size: str = Field(default=_DEFAULT_SIZE, description="图片尺寸")


class ImageGenerationOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter)."""
    image_url: str | None = Field(None, description="生成的图片 URL")
    image_base64: str | None = Field(None, description="生成的图片 base64")
    error: str | None = Field(None, description="错误信息")


# ═══════════════════════════════════════════
# Tool
# ═══════════════════════════════════════════

class ImageGenerationTool(ToolSpec):
    """Generate images from text prompts via an OpenAI-compatible API."""
    name: str = "image_generation"
    description: str = (
        "根据文本提示词生成图片（接线示意图、电路图、硬件框图、通用图片等）。"
        "当用户明确要求生成图片、画图、示意图时使用。"
        "参数 prompt 必须是用户请求生成图片的完整描述。"
        "生成的图片会自动显示在对话流中，你只需在最终回答里用文字描述图片内容，"
        "不要在回答中再使用 ![...](...) markdown 图片语法。"
    )
    args_schema: type = ImageGenerationArgs
    output_schema: type[BaseModel] | None = ImageGenerationOutput
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = _IMAGE_TIMEOUT_SECONDS
    max_retries: int = 1

    _image_model: str = PrivateAttr(default="")
    _base_url: str = PrivateAttr(default="")
    _api_key: str = PrivateAttr(default="")

    def __init__(self, image_model: str = "", base_url: str = "", api_key: str = ""):
        super().__init__()
        self._image_model = image_model or ""
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Run image generation with 3-step fallback; return fallback on failure."""
        if not self._image_model or not self._api_key:
            logger.info("image_generation skipped: model or api_key not configured")
            return {"output": _FALLBACK_OUTPUT, "error": "not configured"}
        prompt = args.get("prompt", "")
        size = args.get("size", _DEFAULT_SIZE)
        return await self._try_strategies(prompt, size)

    async def _try_strategies(self, prompt: str, size: str) -> dict:
        """Try strategies in order; return first success or aggregate failure.

        Order: dedicated image endpoints first (/images/generations, /tasks),
        then chat completion as fallback (for chat models that can generate
        images).
        """
        failures: list[str] = []
        for strategy in (self._via_images, self._via_polling, self._via_chat):
            name = strategy.__name__
            result = await self._safe_call(strategy, prompt, size)
            if result.get("image_url") or result.get("image_base64"):
                return _build_success(result)
            err = result.get("_error") or "no image in response"
            failures.append(f"{name}: {err}")
            logger.info("image_generation %s failed: %s", name, err)
        detail = "; ".join(failures)
        return {
            "output": f"{_FAIL_OUTPUT} ({detail})",
            "error": detail,
        }

    async def _safe_call(self, strategy, prompt: str, size: str) -> dict:
        """Call a strategy coroutine, returning {} with _error on exception."""
        try:
            return await strategy(prompt, size)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = (exc.response.text or "")[:_HTTP_ERROR_BODY_MAX_CHARS]
            return {"_error": f"HTTP {status} {body}"}
        except Exception as exc:
            return {"_error": f"{type(exc).__name__}: {exc}"}

    async def _via_chat(self, prompt: str, size: str) -> dict:
        """Strategy 3 (fallback): chat completion, parse image from text.

        Wraps the prompt to guide multimodal chat models to return image in
        markdown image syntax or data URI format.
        """
        url = self._base_url + "/chat/completions"
        wrapped = _CHAT_PROMPT_TEMPLATE.format(prompt=prompt, size=size)
        body = {"model": self._image_model, "messages": [{"role": "user", "content": wrapped}]}
        data = await self._post_json(url, body)
        text = _extract_chat_text(data)
        if not text:
            return {"_error": "empty chat response"}
        parsed = _parse_image_from_text(text)
        if not parsed:
            preview = text[:_CHAT_PREVIEW_MAX_CHARS].replace("\n", " ")
            return {"_error": f"no image in chat response: {preview!r}"}
        return parsed

    async def _via_images(self, prompt: str, size: str) -> dict:
        """Strategy 1: /images/generations endpoint (dedicated image model)."""
        url = self._base_url + "/images/generations"
        body = {"model": self._image_model, "prompt": prompt, "size": size, "n": 1}
        data = await self._post_json(url, body)
        parsed = _parse_images_response(data)
        if not parsed:
            return {"_error": "no image in /images/generations response"}
        return parsed

    async def _via_polling(self, prompt: str, size: str) -> dict:
        """Strategy 2: async polling via /tasks endpoint."""
        url = self._base_url + "/tasks"
        body = {"model": self._image_model, "prompt": prompt, "size": size}
        data = await self._post_json(url, body)
        task_id = str(data.get("task_id") or data.get("id") or "")
        if not task_id:
            return {"_error": "no task_id in /tasks response"}
        return await self._poll_task(task_id)

    async def _post_json(self, url: str, body: dict) -> dict:
        """POST JSON with auth; return parsed JSON. Raises on failure.

        Some OpenAI-compatible providers (e.g. 9router) append SSE markers like
        `data: [DONE]` after the JSON body. We strip trailing non-JSON data.
        """
        import json
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            text = resp.text.strip()
            # Strip trailing SSE markers (e.g. "data: [DONE]") some providers add
            for marker in ("\ndata: [DONE]", "\r\ndata: [DONE]", "data: [DONE]"):
                if text.endswith(marker):
                    text = text[: -len(marker)].rstrip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Fallback: try to extract first JSON object via raw_decode
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(text.lstrip())
                return obj

    async def _poll_task(self, task_id: str) -> dict:
        """Poll GET /tasks/{id} until terminal status; return image result."""
        url = self._base_url + "/tasks/" + task_id
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(timeout=_POLL_TIMEOUT_SECONDS) as client:
            return await self._poll_loop(client, url, headers)

    async def _poll_loop(self, client: httpx.AsyncClient, url: str, headers: dict) -> dict:
        """Loop polling until succeeded/failed/timeout; return image result."""
        for _ in range(_POLL_MAX_ATTEMPTS):
            data = await self._fetch_task(client, url, headers)
            if data.get("status") == "succeeded":
                return _parse_task_response(data)
            if data.get("status") == "failed":
                return {"_error": "task failed"}
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        return {"_error": "polling timeout"}

    async def _fetch_task(self, client: httpx.AsyncClient, url: str, headers: dict) -> dict:
        """GET one task status snapshot."""
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


# ═══════════════════════════════════════════
# Response parsers (module-level)
# ═══════════════════════════════════════════

def _build_success(result: dict) -> dict:
    """Build a success envelope from a strategy result."""
    return {
        "output": _SUCCESS_OUTPUT,
        "image_url": result.get("image_url"),
        "image_base64": result.get("image_base64"),
    }


def _extract_chat_text(data: dict) -> str:
    """Extract assistant text from a chat completion response."""
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _parse_image_from_text(text: str) -> dict:
    """Parse a markdown image URL or base64 data URI from chat response text."""
    url_match = _MARKDOWN_IMAGE_RE.search(text)
    if url_match:
        return {"image_url": url_match.group(1)}
    data_match = _DATA_URI_RE.search(text)
    if data_match:
        return {"image_base64": data_match.group(0)}
    return {}


def _parse_images_response(data: dict) -> dict:
    """Parse /images/generations response: data[0].url or data[0].b64_json."""
    try:
        item = data["data"][0]
        return {"image_url": item.get("url"), "image_base64": item.get("b64_json")}
    except (KeyError, IndexError, TypeError):
        return {}


def _parse_task_response(data: dict) -> dict:
    """Parse an async task response for an image URL or base64."""
    result = data.get("result") or data.get("output") or {}
    if isinstance(result, dict):
        return {"image_url": result.get("url"), "image_base64": result.get("b64_json")}
    if isinstance(result, str):
        return {"image_url": result}
    return {}
