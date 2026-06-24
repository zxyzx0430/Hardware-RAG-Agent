"""
Chat 路由 — /api/chat SSE + /api/models

迁移自 routes.py，共享工具见 common.py。
"""

import logging
import json
import base64
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.llm.client import LLMClient, ChatMessage, LLMError
from app.api.auth import get_provider_key
from app.api.common import (
    sse_event, sanitize_error,
    make_client, DEFAULT_SYSTEM_PROMPT, extract_attachment_text,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ═══════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════

class ChatMessageSchema(BaseModel):
    role: str
    content: str | list[dict] = None  # str=纯文本, list=[{type,text|image_url},...]


class ChatRequest(BaseModel):
    messages: list[ChatMessageSchema] = Field(min_length=1)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_k: Optional[int] = 5
    system_prompt: Optional[str] = None
    long_term_memory: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    attachments: Optional[list[dict]] = None


class ModelsRequest(BaseModel):
    base_url: str
    provider: str = ""


# ═══════════════════════════════════════════
# POST /api/chat — SSE 流式聊天
# ═══════════════════════════════════════════

@router.post("/chat")
async def chat_sse(payload: ChatRequest, request: Request):
    """RAG 流式聊天，严格匹配前端 SSE 事件协议。"""
    header_key = request.headers.get("x-api-key")
    header_model = request.headers.get("x-model")
    header_provider = request.headers.get("x-provider")
    header_base_url = request.headers.get("x-base-url")
    provider = payload.provider or header_provider or "openai"
    stored_key = get_provider_key(provider)
    api_key = header_key or stored_key or settings.llm_api_key
    base_url = payload.base_url or header_base_url or settings.llm_base_url
    model = payload.model or header_model or settings.llm_model

    async def event_generator():
        """SSE 流式生成器，带 CancelledError 处理 + idle timeout + finally 清理。"""
        import asyncio as _asyncio
        
        # ── 初始化变量（确保 finally 中可访问） ──
        msgs = payload.messages
        last_user_msg: str | list[dict] = ""
        history: list[ChatMessage] = []
        attachment_texts: list[str] = []
        image_parts: list[dict] = []
        
        try:
            if payload.attachments:
                logger.info(f"收到 {len(payload.attachments)} 个附件: {[a.get('name') for a in payload.attachments]}")
                for att in payload.attachments:
                    att_name = att.get("name", "未知文件")
                    att_type = att.get("type", "")
                    att_content = att.get("content", "")
                    if att_type.startswith("image/"):
                        image_parts.append({
                            "type": "image_url",
                            "image_url": {"url": att_content},
                        })
                        continue
                    try:
                        text = extract_attachment_text(att_name, att_type, att_content)
                        if text:
                            max_chars = settings.max_attachment_chars
                            if len(text) > max_chars:
                                text = text[:max_chars] + f"\n\n[...内容已截断，共 {len(text)} 字符]"
                        attachment_texts.append(f"[附件: {att_name}]\n{text}")
                    except Exception as e:
                        logger.warning(f"附件文本提取失败 {att_name}: {e}")
        
            for m in msgs:
                if m.role == "user":
                    last_user_msg = m.content if m.content is not None else ""
                history.append(ChatMessage(role=m.role, content=m.content if m.content is not None else ""))
        
            if image_parts:
                text_part = last_user_msg if isinstance(last_user_msg, str) else str(last_user_msg)
                multimodal_content: list[dict] = [{"type": "text", "text": text_part}] + image_parts
                last_user_msg = multimodal_content
        
            # ── RAG 检索 ──
            rag_context = ""
            sources = []
            if payload.top_k and payload.top_k > 0:
                yield sse_event("thinking", {"content": "正在检索知识库...", "source": "rag"})
                try:
                    from src.rag.kb_manager import get_kb_manager
                    kb_manager = get_kb_manager()
                    # Extract plain text for RAG search (last_user_msg may be multimodal list)
                    rag_query_text = last_user_msg if isinstance(last_user_msg, str) else str(last_user_msg)
                    results = kb_manager.search_all_enabled(rag_query_text, k=payload.top_k)
                    if results:
                        for i, r in enumerate(results):
                            sid = f"src{i + 1}"
                            title = r.metadata.get("title", "未知来源")
                            doc = r.doc_id or r.metadata.get("doc_id", "")
                            page = r.metadata.get("chunk_index", 0)
                            score = float(r.score) if r.score else 0.0
                            excerpt = r.content[:200] if r.content else ""
                            yield sse_event("source", {
                                "id": sid, "title": title, "doc": doc,
                                "page": page, "score": score, "excerpt": excerpt,
                                "kb_id": r.kb_id, "kb_name": r.kb_name,
                            })
                            sources.append({
                                "id": sid, "title": title, "content": r.content[:500],
                                "doc": doc, "page": page, "score": score,
                                "kb_id": r.kb_id, "kb_name": r.kb_name,
                            })
                        rag_query = last_user_msg[:80] if isinstance(last_user_msg, str) else str(last_user_msg)[:80]
                        yield sse_event("tool", {
                            "name": "search_docs", "icon": "search",
                            "args": {"query": rag_query, "top_k": payload.top_k},
                            "result": f"找到 {len(results)} 条相关片段",
                        })
                        rag_context = "\n\n".join(
                            f"[来源: {r.kb_name} / {r.metadata.get('title', '未知')}]\n{r.content[:500]}"
                            for r in results
                        )
                except Exception as e:
                    logger.warning(f"RAG 检索失败: {e}")
        
            # ── 构建 system_prompt ──
            # 使用 is None 检查，避免空字符串被 or 吞没
            system_prompt = payload.system_prompt if payload.system_prompt is not None else DEFAULT_SYSTEM_PROMPT
            logger.info(f"system_prompt received: '{payload.system_prompt[:80] if payload.system_prompt is not None else '(None, using default)'}'")
            if attachment_texts:
                system_prompt += "\n\n## 用户附件\n以下内容来自用户上传的附件：\n" + "\n---\n".join(attachment_texts)
            if rag_context:
                system_prompt += f"\n\n## 参考文档片段\n以下内容来自知识库检索，请优先引用：\n{rag_context}"
        
            # ── LLM 流式输出 ──
            client = make_client(
                api_key=api_key, base_url=base_url, model=model,
                temperature=payload.temperature, max_tokens=payload.max_tokens,
            )
            yield sse_event("thinking", {"content": "正在生成回答...", "source": "llm"})
        
            try:
                usage_data = None
                # 使用 queue 包装 LLM stream，支持 idle timeout
                IDLE_TIMEOUT = 300  # 5 分钟无数据断开
        
                queue = _asyncio.Queue()
        
                async def _llm_worker(q):
                    """后台任务：消费 LLM stream 并推入队列。"""
                    try:
                        async for chunk in client.chat_stream(
                            user_message=last_user_msg, system_prompt=system_prompt,
                            history=history[:-1] if len(history) > 1 else None,
                            api_key=api_key, base_url=base_url, model=model, provider=provider,
                        ):
                            await q.put(chunk)
                    except _asyncio.CancelledError:
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
                            chunk = await _asyncio.wait_for(queue.get(), timeout=IDLE_TIMEOUT)
                        except _asyncio.TimeoutError:
                            logger.error("LLM 响应超时 (5min)")
                            yield sse_event("error", {"message": "LLM 响应超时，请重试"})
                            yield sse_event("done", {"success": False})
                            return

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
                        else:
                            # TODO: ReAct loop
                            yield sse_event("text", {"content": chunk.content})

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
                logger.error(f"LLM call failed: {e}")
                yield sse_event("error", {"message": sanitize_error(f"LLM 调用失败: {str(e)}")})
                yield sse_event("done", {"success": False})
        
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
# POST /api/models — 拉取模型列表
# ═══════════════════════════════════════════

@router.post("/models")
async def list_models(payload: ModelsRequest, request: Request):
    """根据用户填写的 provider 配置获取可用模型列表。"""
    header_key = request.headers.get("x-api-key")
    header_base_url = request.headers.get("x-base-url")
    header_provider = request.headers.get("x-provider")
    provider = payload.provider or header_provider or "openai"
    stored_key = get_provider_key(provider)
    api_key = header_key or stored_key or settings.llm_api_key
    base_url = payload.base_url or header_base_url or settings.llm_base_url

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
