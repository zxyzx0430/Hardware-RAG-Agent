"""
Hardware RAG Agent — ViewImageTool (retrieval group).

Reads a LOCAL image file path, encodes it as a base64 data URI, and sends
it to an OpenAI-compatible vision model for analysis. Reuses the same
vision provider credentials as VisionAnalysisTool (constructor signature
is identical: vision_model / base_url / api_key).

Distinction from vision_analysis (spelled out in the tool description so
the Agent picks the right one):
  * vision_analysis accepts a URL or base64 string — driven by the USER
    uploading an image in the chat UI.
  * view_image accepts a local file path — driven by the AGENT itself
    deciding to look at an image it discovered on disk (e.g. a schematic
    under data/schematics/, a screenshot it just generated).

Reuses the SSE-marker stripping logic from vision_analysis: some providers
(e.g. 9router) append `data: [DONE]` after the JSON body, which breaks
json.loads if not stripped first.

Spec: add-grep-glob-todo-tools §Requirement: view_image tool (Task 10).
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import validate_path

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

_VISION_TIMEOUT_SECONDS: int = 60
_MAX_IMAGE_BYTES: int = 10 * 1024 * 1024
_ALLOWED_EXTS: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_EXT_TO_MIME: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}
_DEFAULT_MIME: str = "image/png"
_NOT_CONFIGURED_OUTPUT: str = "vision provider 未配置：请在设置页选择视觉模型 provider"
_SUPPORTED_FORMATS_HINT: str = "png/jpg/jpeg/gif/webp/bmp"
_SSE_MARKERS: tuple[str, ...] = ("\ndata: [DONE]", "\r\ndata: [DONE]", "data: [DONE]")


# ═══════════════════════════════════════════
# Args schema
# ═══════════════════════════════════════════

class ViewImageArgs(BaseModel):
    image_path: str = Field(description="本地图片文件绝对路径（png/jpg/jpeg/gif/webp/bmp，≤10MB）")
    prompt: str = Field(description="对图片的分析问题（如「这张引脚图的定义是什么」）")


# ═══════════════════════════════════════════
# Tool
# ═══════════════════════════════════════════

class ViewImageTool(ToolSpec):
    """Analyze a LOCAL image file via the configured vision model."""
    name: str = "view_image"
    description: str = (
        "读取本地图片文件路径，转 base64 后调用多模态模型分析"
        "（识别芯片引脚图、电路原理图、数据手册截图等）。"
        "与 vision_analysis 的区分：vision_analysis 接 URL/base64（用户上传驱动），"
        "view_image 接本地文件路径（Agent 主动驱动，适合 Agent 在工作流中自主查看磁盘上的图片）。"
        "支持格式：png/jpg/jpeg/gif/webp/bmp，上限 10MB。"
    )
    args_schema: type = ViewImageArgs
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
        """Run view_image; return error dict on any failure."""
        image_path = args.get("image_path", "")
        prompt = args.get("prompt", "")
        prepared = _prepare_image_input(image_path, self._vision_model, self._api_key)
        if isinstance(prepared, dict):
            return prepared
        return await self._analyze(prepared, prompt)

    async def _analyze(self, data_uri: str, prompt: str) -> dict:
        """Call vision model, return result or error dict."""
        try:
            analysis = await self._call_vision(data_uri, prompt)
            return {"output": analysis, "analysis": analysis}
        except Exception as exc:
            logger.warning("view_image failed: %s", exc)
            return _err(f"view_image failed: {exc}")

    async def _call_vision(self, data_uri: str, question: str) -> str:
        """POST chat/completions with image content; strip SSE markers."""
        url = self._base_url + "/chat/completions"
        body = _build_body(self._vision_model, data_uri, question)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(url, headers=_build_headers(self._api_key), json=body)
            resp.raise_for_status()
            return _parse_vision_response(resp.text)


# ═══════════════════════════════════════════
# Input preparation (path / config / file validation)
# ═══════════════════════════════════════════

def _prepare_image_input(image_path: str, vision_model: str, api_key: str) -> str | dict:
    """Validate path/config/file; return data_uri str or error dict."""
    if not vision_model or not api_key:
        return _err(_NOT_CONFIGURED_OUTPUT)
    err = _check_path(image_path)
    if err:
        return err
    return _read_image_as_data_uri(image_path)


def _check_path(image_path: str) -> dict | None:
    """Validate path; return error dict or None."""
    ok, reason = validate_path(image_path, is_write=False)
    if not ok:
        return _err(f"path not allowed: {reason}")
    return None


def _read_image_as_data_uri(image_path: str) -> str | dict:
    """Read image file, return data URI str or error dict."""
    ext = Path(image_path).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        return _err(f"not an image file: supported formats are {_SUPPORTED_FORMATS_HINT}")
    data = _read_image_bytes(image_path)
    if isinstance(data, dict):
        return data
    if len(data) > _MAX_IMAGE_BYTES:
        return _err("image too large: max 10MB")
    return _encode_data_uri(data, ext)


def _read_image_bytes(image_path: str) -> bytes | dict:
    """Read raw bytes; return error dict on failure."""
    try:
        return Path(image_path).read_bytes()
    except FileNotFoundError:
        return _err(f"file not found: {image_path}")
    except OSError as exc:
        return _err(f"read error: {exc}")


def _encode_data_uri(data: bytes, ext: str) -> str:
    """Encode bytes as base64 data URI."""
    mime = _EXT_TO_MIME.get(ext, _DEFAULT_MIME)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


# ═══════════════════════════════════════════
# Vision API request/response helpers
# ═══════════════════════════════════════════

def _build_headers(api_key: str) -> dict:
    """Build auth + content-type headers for the vision API."""
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _build_body(model: str, data_uri: str, question: str) -> dict:
    """Build OpenAI-compatible chat completion request body with image."""
    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
    }


def _parse_vision_response(text: str) -> str:
    """Parse vision API response, stripping trailing SSE markers first."""
    cleaned = _strip_sse_markers(text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(cleaned.lstrip())
    return _extract_content(data)


def _strip_sse_markers(text: str) -> str:
    """Strip trailing SSE markers like 'data: [DONE]' (9router etc.)."""
    for marker in _SSE_MARKERS:
        if text.endswith(marker):
            text = text[: -len(marker)].rstrip()
    return text


def _extract_content(data: dict) -> str:
    """Extract the assistant message content from a chat completion response."""
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


# ═══════════════════════════════════════════
# Error builder
# ═══════════════════════════════════════════

def _err(msg: str) -> dict:
    """Build error result dict."""
    return {"output": msg, "analysis": ""}
