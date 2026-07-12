"""
Chat 路由 — /api/chat SSE + /api/models

迁移自 routes.py，共享工具见 common.py。
"""

import logging
import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.llm.client import LLMClient, LLMError
try:
    from openai import NotFoundError as OpenAINotFoundError
except ImportError:  # pragma: no cover - openai optional dependency
    OpenAINotFoundError = None  # type: ignore[assignment]
from app.api.auth import get_provider_key, resolve_credentials
from app.api.dependencies import current_user, current_user_optional
from app.api.common import get_db_ctx, make_client
from app.api.sse import sse_event
from app.api.errors import sanitize_error
from app.api.chat_helpers import (
    _record_token_usage,
    _process_attachments, _build_chat_history, _build_system_prompt,
)
from app.db.models import TokenUsage

logger = logging.getLogger(__name__)

# Agent path imports — guarded so a missing langgraph/langchain dependency degrades
# gracefully to the fallback LLM stream instead of breaking the whole chat route
# (spec §10.2: "LangGraph import 失败 → use_agent = False, 自动走 fallback").
try:
    from src.agent.agent_factory import (
        _should_use_agent, build_agent_config, build_tools, create_hardware_agent,
    )
    from src.agent.sse_adapter import stream_agent_to_sse
    from src.agent.compact.autocompact import autocompact_messages, should_autocompact
    from src.agent.prompts import MAX_TOKEN_RATIO
    _AGENT_PATH_AVAILABLE = True
except ImportError as _agent_import_err:
    _should_use_agent = None  # type: ignore[assignment]
    _AGENT_PATH_AVAILABLE = False
    logger.debug("Agent path unavailable, will use fallback LLM stream: %s", _agent_import_err)

router = APIRouter(prefix="/api")


class ChatErrorCode(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    RAG_FAILED = "RAG_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"


# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════

class ChatMessageSchema(BaseModel):
    role: str
    content: Optional[str | list[dict]] = None  # str=纯文本, list=[{type,text|image_url},...]


class ChatRequest(BaseModel):
    messages: list[ChatMessageSchema] = Field(min_length=1)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_k: Optional[int] = 5
    relevance_threshold: Optional[float] = 0.0  # 0.0-1.0, filter RAG results below this score
    system_prompt: Optional[str] = None
    long_term_memory: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    attachments: Optional[list[dict]] = None
    session_id: Optional[str] = None
    kb_ids: Optional[list[str]] = None  # Selected KB IDs for RAG search; None/empty = all enabled
    # Agent path opt-in (spec §10.1): when True + model supports tools + langgraph available,
    # the request flows through the ReAct agent instead of the single-shot LLM stream.
    use_agent: bool = False
    permission_mode: str = "default"  # bypassPermissions / default / acceptEdits (spec §7.1)
    tool_keys: dict[str, str] = {}  # per-request tool credentials, e.g. {"tavily": "..."}


class ModelsRequest(BaseModel):
    base_url: str
    provider: str = ""


# ═══════════════════════════════════════════
# Agent helpers
# ═══════════════════════════════════════════

def _build_agent_messages(payload: ChatRequest) -> list[dict]:
    """Convert ChatRequest.messages to langgraph agent input message dicts.

    Images are ISOLATED from the LLM: data URIs are cached and replaced with a
    text hint telling the Agent to call vision_analysis(image="cache:image_id"). This
    avoids 400 errors on text-only models (e.g. deepseek-v4-flash) and keeps
    base64 out of the LLM context (a screenshot can be 100KB+).

    Assistant-generated images are marked differently so the Agent does not
    mistake them for user uploads on follow-up turns.
    """
    messages: list[dict] = []
    for msg in payload.messages:
        content = _isolate_images_in_content(msg.content, msg.role)
        messages.append({"role": msg.role, "content": content})
    return messages


def _isolate_images_in_content(content, role: str = "user") -> str:
    """Return str content as-is; convert multimodal list to text only."""
    if isinstance(content, str):
        return content
    return _multimodal_to_text_with_image_hints(content, role)


def _multimodal_to_text_with_image_hints(parts, role: str = "user") -> str:
    """Strip image_url parts, cache them, append vision_analysis call hints.

    For user messages, instruct the Agent to call vision_analysis.
    For assistant messages, only label generated images so the Agent knows they
    are historical outputs, not new uploads.
    """
    from src.agent.tools.groups.retrieval.vision_analysis import cache_image
    texts: list[str] = []
    image_refs: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            texts.append(str(part.get("text", "")))
        elif ptype == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if not url:
                continue
            if url.startswith("data:"):
                image_id = cache_image(url)
                image_refs.append(f'cache:{image_id}')
            else:
                image_refs.append(url)
    if role == "assistant":
        for i, ref in enumerate(image_refs, 1):
            texts.append(f'[这是之前生成的图片 {i}，不是用户新上传的图片]')
        return "\n".join(texts)
    for i, ref in enumerate(image_refs, 1):
        texts.append(f'[用户上传了图片 {i}，请调用 vision_analysis(image="{ref}") 分析图片内容]')
    if image_refs:
        texts.append(f"共 {len(image_refs)} 张图片。分析图片时必须调用 vision_analysis 工具，不要凭空猜测图片内容。")
    return "\n".join(texts)


# ═══════════════════════════════════════════
# Pre-send history compaction
# ═══════════════════════════════════════════

def _dicts_to_langchain_messages(dicts: list[dict]) -> list:
    """Convert agent_input message dicts to langchain Message objects."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    cls_by_role = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    messages: list = []
    for d in dicts:
        cls = cls_by_role.get(d.get("role", "user"), HumanMessage)
        messages.append(cls(content=d.get("content", "")))
    return messages


def _langchain_messages_to_dicts(messages: list) -> list[dict]:
    """Convert langchain Message objects back to agent_input dicts."""
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    role_by_cls = {HumanMessage: "user", AIMessage: "assistant", SystemMessage: "system"}
    dicts: list[dict] = []
    for m in messages:
        role = role_by_cls.get(type(m), "user")
        content = getattr(m, "content", "")
        if not isinstance(content, str):
            content = str(content)
        dicts.append({"role": role, "content": content})
    return dicts


def _session_context_window(session_id: Optional[str]) -> Optional[int]:
    """Read context_window from the session row; None when unknown."""
    from src.agent.sse_helpers import _load_session_context_window
    return _load_session_context_window(session_id or "default")


def _compact_event_payload(threshold: int, context_window: int, msg_count: int) -> dict:
    """Build the context_compressing SSE payload."""
    return {
        "reason": "history_exceeds_threshold",
        "threshold_tokens": threshold,
        "context_window": context_window,
        "message_count": msg_count,
    }


async def _run_autocompact(messages: list, creds: dict, context_window: int) -> list:
    """Build an LLM client and run autocompact_messages."""
    llm_client = make_client(
        api_key=creds.get("api_key"), base_url=creds.get("base_url"), model=creds.get("model"),
    )
    return await autocompact_messages(messages, llm_client, context_window)


async def _check_and_compact_history(
    payload: ChatRequest, creds: dict, agent_input: dict,
) -> AsyncIterator[str]:
    """Pre-send: compact history when over MAX_TOKEN_RATIO of context window.

    Yields a context_compressing SSE event when compaction runs; mutates
    agent_input['messages'] in place with the compacted list. No-op (and no
    SSE) when history is within budget or context_window is unknown.
    """
    context_window = _session_context_window(payload.session_id)
    if not context_window:
        return
    lc_messages = _dicts_to_langchain_messages(agent_input["messages"])
    threshold = int(context_window * MAX_TOKEN_RATIO)
    if not should_autocompact(lc_messages, threshold):
        return
    yield sse_event(
        "context_compressing",
        _compact_event_payload(threshold, context_window, len(lc_messages)),
    )
    compacted = await _run_autocompact(lc_messages, creds, context_window)
    # Use identity check: autocompact returns the same list when no compaction
    # happened. A new list means compaction ran (even if count is unchanged,
    # e.g., 11 msgs → 1 summary + 10 recent — tokens decreased).
    if compacted is not lc_messages:
        agent_input["messages"] = _langchain_messages_to_dicts(compacted)


async def _run_agent_stream(payload: ChatRequest, creds: dict) -> AsyncIterator[str]:
    """Build the agent and yield SSE events from its stream.

    Raises on construction failure so the caller can fall back to the LLM stream.
    """
    from src.agent.agent_factory import reset_thread_checkpoint
    await reset_thread_checkpoint(payload.session_id or "default")
    agent = await _build_agent_for_payload(payload, creds)
    config = build_agent_config(payload.session_id or "default")
    agent_input = {"messages": _build_agent_messages(payload)}
    # Pre-send history check — compact if over MAX_TOKEN_RATIO of context window.
    async for sse in _check_and_compact_history(payload, creds, agent_input):
        yield sse
    call_counter: Counter = Counter()
    async for sse in stream_agent_to_sse(
        agent, agent_input, config, call_counter, payload.permission_mode,
        model=creds.get("model", ""),
    ):
        yield sse


async def _build_agent_for_payload(payload: ChatRequest, creds: dict):
    """Instantiate tools + the compiled ReAct agent for this request.

    enable_hitl is enabled for default/acceptEdits modes so the tools node
    pauses for permission gating; bypassPermissions runs tools directly.
    """
    tools = build_tools(payload)
    enable_hitl = payload.permission_mode != "bypassPermissions"
    kwargs: dict = {
        "model": creds["model"], "api_key": creds["api_key"],
        "base_url": creds["base_url"], "tools": tools,
        "enable_hitl": enable_hitl,
    }
    _optional_arg(kwargs, "temperature", payload.temperature)
    _optional_arg(kwargs, "max_tokens", payload.max_tokens)
    return await create_hardware_agent(**kwargs)


def _optional_arg(kwargs: dict, key: str, value) -> None:
    """Add key=value to kwargs only when value is not None (let the callee use its default)."""
    if value is not None:
        kwargs[key] = value


# ═══════════════════════════════════════════
# POST /api/chat — SSE 流式聊天
# ═══════════════════════════════════════════

@router.post("/chat")
async def chat_sse(payload: ChatRequest, request: Request, user: dict = Depends(current_user)):
    """RAG 流式聊天，严格匹配前端 SSE 事件协议。"""
    creds = resolve_credentials(payload, request)
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    model = creds["model"]
    provider = creds["provider"]

    async def event_generator():
        """SSE 流式生成器，带 CancelledError 处理 + idle timeout + finally 清理。"""
        import asyncio as _asyncio
        import time

        msgs = payload.messages

        try:
            # ── 附件 + 历史构建 ──
            attachment_texts, image_parts = await _process_attachments(payload)
            history, last_user_msg = _build_chat_history(msgs)
            if image_parts:
                text_part = last_user_msg if isinstance(last_user_msg, str) else str(last_user_msg)
                last_user_msg = [{"type": "text", "text": text_part}] + image_parts

            # ── system_prompt 构建（pre-RAG 已移除，Agent 自行决定是否检索）──
            system_prompt = _build_system_prompt(payload, attachment_texts, "")

            # ── LLM client（fallback 路径使用）──
            client = make_client(
                api_key=api_key, base_url=base_url, model=model,
                temperature=payload.temperature, max_tokens=payload.max_tokens,
            )

            # === Agent 主路径（spec §2.2 / §10.1）===
            # 所有请求默认走 Agent；Agent 不可用时（langgraph 未装/模型不支持 tools）
            # 走下方纯 LLM 流式 fallback（不检索知识库）。
            if _AGENT_PATH_AVAILABLE and _should_use_agent(payload, model):
                creds = {"api_key": api_key, "base_url": base_url, "model": model}
                try:
                    async for sse in _run_agent_stream(payload, creds):
                        yield sse
                    yield sse_event("done", {"success": True, "usage": None})
                    return
                except Exception as agent_exc:
                    logger.error("Agent path failed: %s", agent_exc)
                    yield sse_event("error", {
                        "code": ChatErrorCode.INTERNAL_ERROR,
                        "message": "Agent 调用失败",
                        "detail": sanitize_error(str(agent_exc)),
                    })
                    yield sse_event("done", {"success": False})
                    return

            # === Fallback: 纯 LLM 流式（Agent 不可用，不检索知识库）===
            try:
                usage_data = None
                full_response_text = ""  # Accumulated response for fallback token estimation
                # 使用 queue 包装 LLM stream，支持 idle timeout + heartbeat
                IDLE_TIMEOUT = 300  # 累计 5 分钟无数据断开
                HEARTBEAT_INTERVAL = 15  # 每 15s 发心跳，防止代理/浏览器断连
                last_event_time = time.monotonic()

                queue = _asyncio.Queue()

                async def _llm_worker(q):
                    """后台任务：消费 LLM stream 并推入队列。"""
                    try:
                        async for chunk in client.chat_stream(
                            user_message=last_user_msg, system_prompt=system_prompt,
                            history=history if len(history) > 0 else None,
                            api_key=api_key, base_url=base_url, model=model, provider=provider,
                        ):
                            await q.put(chunk)
                    except _asyncio.CancelledError:
                        logger.debug("SSE task cancelled")
                        pass  # 主协程取消，静默退出
                    except Exception as e:
                        logger.error(f"LLM stream error: {e}")
                        await q.put(e)
                    finally:
                        await q.put(None)  # 哨兵：标记结束

                worker_task = _asyncio.create_task(_llm_worker(queue))

                try:
                    while True:
                        try:
                            chunk = await _asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
                        except _asyncio.TimeoutError:
                            if time.monotonic() - last_event_time >= IDLE_TIMEOUT:
                                logger.error("LLM 响应超时 (5min idle)")
                                yield sse_event("error", {"code": ChatErrorCode.TIMEOUT, "message": "LLM 响应超时", "detail": "5 分钟内无新数据，连接已断开"})
                                yield sse_event("done", {"success": False})
                                return
                            yield sse_event("heartbeat", {})
                            continue

                        last_event_time = time.monotonic()

                        if chunk is None:
                            # 流正常结束
                            break
                        if isinstance(chunk, Exception):
                            raise chunk

                        if chunk.type == "thinking":
                            yield sse_event("thinking", {"content": chunk.content, "source": "reasoning"})
                        elif chunk.type == "usage":
                            usage_data = chunk.usage
                            logger.info(f"LLM usage: {usage_data}")
                            # Record token usage to database
                            if usage_data:
                                _record_token_usage(
                                    model=model, provider=provider or "",
                                    session_id=payload.session_id, usage_data=usage_data,
                                )
                        else:
                            full_response_text += chunk.content or ""
                            yield sse_event("text", {"content": chunk.content})

                    # Fallback: if provider didn't return usage in stream, estimate it
                    # (some providers like certain Ollama setups don't support stream_options)
                    if not usage_data:
                        try:
                            from src.llm.client import LLMClient as _LLMClient
                            # Estimate input tokens from messages
                            input_tokens = 0
                            for m in msgs:
                                input_tokens += _LLMClient._estimate_tokens(m.content)
                            if system_prompt:
                                input_tokens += _LLMClient._estimate_tokens(system_prompt)
                            # Estimate output tokens from accumulated response
                            output_tokens = _LLMClient._estimate_tokens(full_response_text)
                            usage_data = {
                                "prompt_tokens": input_tokens,
                                "completion_tokens": output_tokens,
                                "total_tokens": input_tokens + output_tokens,
                            }
                            logger.info(f"LLM usage (estimated): {usage_data}")
                            _record_token_usage(
                                model=model, provider=provider or "",
                                session_id=payload.session_id, usage_data=usage_data,
                            )
                        except Exception as est_err:
                            logger.warning(f"Token estimation failed: {est_err}")

                    # 正常结束，发送 done
                    done_payload: dict = {"success": True}
                    if usage_data:
                        done_payload["usage"] = usage_data
                    yield sse_event("done", done_payload)
                    return

                finally:
                    worker_task.cancel()
                    try:
                        await worker_task
                    except _asyncio.CancelledError:
                        pass

            except Exception as e:
                _emsg = str(e)
                _emsg_lower = _emsg.lower()
                if (OpenAINotFoundError is not None and isinstance(e, OpenAINotFoundError)) or \
                   "model_not_found" in _emsg_lower or \
                   "modelerror" in _emsg_lower or \
                   ("model" in _emsg_lower and ("not found" in _emsg_lower or "does not exist" in _emsg_lower or "is not supported" in _emsg_lower)):
                    _ecode: ChatErrorCode = ChatErrorCode.MODEL_NOT_FOUND
                    _emsg_display = f"模型不存在：{model}"
                    _edetail = _emsg
                elif isinstance(e, LLMError):
                    if "API Key" in _emsg or "AuthenticationError" in _emsg:
                        _ecode = ChatErrorCode.AUTH_FAILED
                        _emsg_display = "API Key 无效"
                        _edetail = "请检查设置中的 API Key 是否正确"
                    elif "频率" in _emsg or "rate" in _emsg.lower():
                        _ecode = ChatErrorCode.RATE_LIMITED
                        _emsg_display = "请求频率超限"
                        _edetail = "请稍后再试，或降低并发请求"
                    elif "超时" in _emsg or "timeout" in _emsg.lower():
                        _ecode = ChatErrorCode.TIMEOUT
                        _emsg_display = "LLM 响应超时"
                        _edetail = "请检查网络连接后重试"
                    else:
                        _ecode = ChatErrorCode.INTERNAL_ERROR
                        _emsg_display = "LLM 调用失败"
                        _edetail = _emsg
                else:
                    _ecode = ChatErrorCode.INTERNAL_ERROR
                    _emsg_display = "LLM 调用失败"
                    _edetail = sanitize_error(_emsg)
                logger.error(f"LLM call failed: {_ecode} - {_emsg_display}")
                yield sse_event("error", {"code": _ecode, "message": _emsg_display, "detail": _edetail})
                yield sse_event("done", {"success": False})
                return

        except _asyncio.CancelledError:
            logger.info("SSE 流被客户端取消（CancelledError）")
            # 捕获后重新 raise，让 FastAPI 正常终止
            raise

        finally:
            logger.debug("SSE 流结束，资源已释放")    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ═══════════════════════════════════════════
# POST /api/agent-sandbox/resume — HITL resume (v2-T3)
# ═══════════════════════════════════════════


class ResumeRequest(BaseModel):
    """Carries the original ChatRequest + user's HITL decision."""
    payload: ChatRequest
    decision: str  # 'allow' / 'deny' / 'stop'


@router.post("/agent-sandbox/resume")
async def resume_agent(req: ResumeRequest, request: Request, user: dict = Depends(current_user)):
    """Resume a paused Agent after user HITL decision."""
    if not _AGENT_PATH_AVAILABLE:
        return {"error": "Agent path unavailable"}
    creds = _resolve_creds(req.payload, request)
    agent = await _build_agent_for_payload(req.payload, creds)
    config = build_agent_config(req.payload.session_id or "default")
    call_counter: Counter = Counter()
    model = creds.get("model", "")
    return StreamingResponse(
        _resume_event_generator(agent, config, req, call_counter, model),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@dataclass
class ResumeContext:
    """Context for resuming an agent after HITL confirmation.

    Bundles the 5 _resume_event_generator params into a single object so the
    resume flow doesn't have to thread agent/config/req/counter/model through
    every helper signature (P2-I-1 parameter encapsulation).
    """
    agent: Any
    config: dict
    req: "ResumeRequest"
    call_counter: Counter
    model: str


async def _resume_event_generator_from_ctx(ctx: ResumeContext):
    """Yield SSE from the resume handler; tail with done/error.

    Reads all inputs from the ResumeContext dataclass. New call sites should
    build a ResumeContext and call this directly.
    """
    try:
        from src.agent.hitl_handler import resume_agent_after_user
        async for sse in resume_agent_after_user(
            ctx.agent, ctx.config, ctx.req.decision,
            ctx.call_counter, ctx.req.payload.permission_mode,
        ):
            yield sse
        yield sse_event("done", {"success": True, "usage": None})
    except Exception as exc:
        yield _resume_error_event(exc)


async def _resume_event_generator(agent, config, req, call_counter, model):
    """Backward-compat shim around _resume_event_generator_from_ctx (P2-I-1).

    Preserves the original 5-arg signature so resume_agent keeps working.
    New code should build a ResumeContext and call _resume_event_generator_from_ctx.
    """
    ctx = ResumeContext(
        agent=agent, config=config, req=req,
        call_counter=call_counter, model=model,
    )
    async for sse in _resume_event_generator_from_ctx(ctx):
        yield sse


def _resume_error_event(exc: Exception) -> str:
    """Build the error SSE event for a failed resume."""
    logger.exception("Agent resume failed")
    return sse_event("error", {"code": "AGENT_RESUME_FAILED", "message": str(exc)[:200]})


def _resolve_creds(payload: ChatRequest, request: Request) -> dict:
    """Extract api_key/base_url/model from headers + payload + settings."""
    return resolve_credentials(payload, request)


# ═══════════════════════════════════════════
# POST /api/models — 拉取模型列表
# ═══════════════════════════════════════════

@router.post("/models")
async def list_models(payload: ModelsRequest, request: Request, user: dict = Depends(current_user_optional)):
    """根据用户填写的 provider 配置获取可用模型列表。

    使用可选鉴权：验证新 provider 时浏览器可能还没有 session_token（鸡生蛋问题），
    实际的 API Key 通过 X-API-Key 请求头由 resolve_credentials 解析，端点自身
    已有 `if not api_key: return AUTH_FAILED` 防御，无需强制鉴权。
    """
    creds = resolve_credentials(payload, request)
    api_key = creds["api_key"]
    base_url = creds["base_url"]
    provider = creds["provider"]

    if not api_key:
        return {
            "success": False,
            "error": {"code": "AUTH_FAILED", "message": "未提供 API Key", "details": None},
        }

    client = LLMClient(api_key=api_key, base_url=base_url)
    try:
        models = await client.list_models(api_key=api_key, base_url=base_url, provider=provider)
        return {"success": True, "data": {"models": models or []}}
    except LLMError as e:
        return {
            "success": False,
            "error": {"code": "MODEL_FETCH_FAILED", "message": str(e), "details": None},
        }
    except Exception as e:
        logger.exception("模型列表获取异常")
        return {
            "success": False,
            "error": {
                "code": "MODEL_FETCH_FAILED",
                "message": f"模型列表获取失败: {sanitize_error(str(e))}",
                "details": None,
            },
        }


# ═══════════════════════════════════════════
# GET /api/token-usage/stats — Token 用量统计
# ═══════════════════════════════════════════

@router.get("/token-usage/stats")
def token_usage_stats(
    days: int = 30,
    session_id: Optional[str] = None,
    user: dict = Depends(current_user),
):
    """返回近 N 天的 Token 用量统计。

    返回:
        daily: 按天聚合 [{date, input, output, total}]
        by_model: 按模型分组 [{model, input, output, total, calls}]
        summary: {total_input, total_output, total_tokens, days}
    """
    days = max(1, min(days, 365))  # Clamp to valid range
    import datetime
    from sqlalchemy import func

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    try:
        with get_db_ctx() as db:
            # Daily aggregation
            daily_query = db.query(
                func.date(TokenUsage.created_at).label("date"),
                func.sum(TokenUsage.prompt_tokens).label("input"),
                func.sum(TokenUsage.completion_tokens).label("output"),
                func.sum(TokenUsage.total_tokens).label("total"),
            ).filter(TokenUsage.created_at >= cutoff)
            if session_id is not None:
                daily_query = daily_query.filter(TokenUsage.session_id == session_id)
            daily_rows = daily_query.group_by(
                func.date(TokenUsage.created_at)
            ).order_by(
                func.date(TokenUsage.created_at)
            ).all()

            daily = [
                {
                    "date": str(row.date),
                    "input": int(row.input or 0),
                    "output": int(row.output or 0),
                    "total": int(row.total or 0),
                }
                for row in daily_rows
            ]

            # By model aggregation
            model_query = db.query(
                TokenUsage.model,
                func.sum(TokenUsage.prompt_tokens).label("input"),
                func.sum(TokenUsage.completion_tokens).label("output"),
                func.sum(TokenUsage.total_tokens).label("total"),
                func.count(TokenUsage.id).label("calls"),
            ).filter(TokenUsage.created_at >= cutoff)
            if session_id is not None:
                model_query = model_query.filter(TokenUsage.session_id == session_id)
            model_rows = model_query.group_by(
                TokenUsage.model
            ).order_by(
                func.sum(TokenUsage.total_tokens).desc()
            ).all()

            by_model = [
                {
                    "model": row.model,
                    "input": int(row.input or 0),
                    "output": int(row.output or 0),
                    "total": int(row.total or 0),
                    "calls": int(row.calls or 0),
                }
                for row in model_rows
            ]

            # Summary
            total_input = sum(d["input"] for d in daily)
            total_output = sum(d["output"] for d in daily)
            total_tokens = sum(d["total"] for d in daily)

            return {
                "success": True,
                "data": {
                    "daily": daily,
                    "by_model": by_model,
                    "summary": {
                        "total_input": total_input,
                        "total_output": total_output,
                        "total_tokens": total_tokens,
                        "days": days,
                    },
                },
            }
    except Exception as e:
        logger.exception("Token 用量统计查询失败")
        return {
            "success": False,
            "error": {
                "code": "STATS_FAILED",
                "message": f"统计查询失败: {sanitize_error(str(e))}",
                "details": None,
            },
        }
