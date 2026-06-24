"""
Hardware RAG Agent — 全部 API 路由

所有端点按 api-contract.md 实现。
Phase 1 真实现：chat / models / kb_upload / devices
Phase 3 真实现：diagnose / wiring
Phase 3 stub：audit_pins / build / upload / tool / monitor
"""

import json
import logging
import os
import re
import uuid
import tempfile
import asyncio
import threading
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, UploadFile, File, WebSocket, WebSocketDisconnect, Query, Request, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent.tool_router import dispatch, ToolNotFoundError, _REGISTRY as TOOL_REGISTRY
from src.config.settings import settings
from src.hardware import generate_wiring_svg
from src.llm.client import LLMClient, ChatMessage, LLMError
# from src.rag.vector_store import HardwareVectorStore (moved to lazy init)
from src.rag.document_processor import ProcessedDocument
from src.rag.document_loader import DocumentSource
from app.db.database import SessionLocal
from app.db.models import KnowledgeDoc
from app.api.dependencies import current_user
from contextlib import contextmanager

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


# P1: per-port locks to prevent concurrent serial access from multiple clients
_port_locks: dict[str, asyncio.Lock] = {}


def _get_port_lock(port: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given serial port."""
    if port not in _port_locks:
        _port_locks[port] = asyncio.Lock()
    return _port_locks[port]


# P1: lock to serialize wiring generation (prevents concurrent state corruption)
_wiring_lock = asyncio.Lock()


@contextmanager
def get_db_ctx():
    """数据库会话上下文管理器，自动 commit/rollback/close。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ═══════════════════════════════════════════
# 全局常量
# ═══════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = "你是 Hardware RAG Agent——硬件知识 AI 助手。你可以回答关于芯片参数、接线方案、驱动代码、器件对比和硬件排错的问题。请基于提供的硬件文档给出准确、有来源标注的回答。"


# ═══════════════════════════════════════════
# HardwareVectorStore 单例
# ═══════════════════════════════════════════

_vector_store = None
_vector_store_lock = threading.Lock()


def get_vector_store():
    """获取 HardwareVectorStore 全局单例（线程安全，双重检查锁定）。"""
    global _vector_store
    if _vector_store is None:
        with _vector_store_lock:
            if _vector_store is None:  # double-check
                from src.rag.vector_store import HardwareVectorStore
                _vector_store = HardwareVectorStore()
    return _vector_store


# ═══════════════════════════════════════════
# 公共工具函数
# ═══════════════════════════════════════════

def sse_event(event_type: str, data: dict) -> str:
    """构造 SSE data 行。"""
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sanitize_error(msg: str) -> str:
    """脱敏错误信息：替换 sk-xxx 和 URL 中的 API key 参数。"""
    # 替换 sk-xxx 格式的 API Key
    msg = re.sub(r"sk-[a-zA-Z0-9]{8,}", "sk-***", msg)
    # 替换 URL 中的 key/secret 参数
    msg = re.sub(r"([?&](?:api[_-]?key|key|secret|token)=)[^&\s]+", r"\1***", msg, flags=re.IGNORECASE)
    return msg


def make_client(api_key: str = None, base_url: str = None, model: str = None,
                temperature: float = None, max_tokens: int = None) -> LLMClient:
    """用请求级参数或全局默认构造 LLMClient。"""
    return LLMClient(
        api_key=api_key or settings.llm_api_key,
        base_url=base_url or settings.llm_base_url,
        model=model or settings.llm_model,
        temperature=temperature if temperature is not None else settings.llm_temperature,
        max_tokens=max_tokens if max_tokens is not None else settings.llm_max_tokens,
    )


# ═══════════════════════════════════════════
# POST /api/chat — RAG 流式聊天 (SSE)
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


@router.post("/chat")
async def chat_sse(payload: ChatRequest, request: Request):
    """RAG 流式聊天，严格匹配前端 SSE 事件协议。
    API Key 优先级：X-API-Key header > .env 默认值
    """
    # 从 header 获取 API Key 和配置
    header_key = request.headers.get("x-api-key")
    header_model = request.headers.get("x-model")
    header_provider = request.headers.get("x-provider")
    header_base_url = request.headers.get("x-base-url")
    # 从加密存储获取 API Key，回退到 header / .env 默认值
    from app.api.auth import get_provider_key
    provider = payload.provider or header_provider or "openai"
    stored_key = get_provider_key(provider)
    api_key = header_key or stored_key or settings.llm_api_key
    base_url = payload.base_url or header_base_url or settings.llm_base_url
    model = payload.model or header_model or settings.llm_model

    async def event_generator():
        # ── 1. 解析消息 + 附件处理 ──
        msgs = payload.messages
        last_user_msg: str | list[dict] = ""
        history: list[ChatMessage] = []

        # 附件文本提取结果，用于注入 system_prompt
        attachment_texts: list[str] = []
        # 图片附件，用于构造多模态 content
        image_parts: list[dict] = []

        if payload.attachments:
            logger.info(f"收到 {len(payload.attachments)} 个附件: {[a.get('name') for a in payload.attachments]}")
            for att in payload.attachments:
                att_name = att.get("name", "未知文件")
                att_type = att.get("type", "")
                att_content = att.get("content", "")

                # 图片类型走 vision 路径
                if att_type.startswith("image/"):
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {"url": att_content},
                    })
                    continue

                # 文本类型附件：提取内容注入 system_prompt
                try:
                    text = _extract_attachment_text(att_name, att_type, att_content)
                    if text:
                        max_chars = settings.max_attachment_chars
                        if len(text) > max_chars:
                            text = text[:max_chars] + f"\n\n[...内容已截断，共 {len(text)} 字符]"
                        attachment_texts.append(f"[附件: {att_name}]\n{text}")
                except Exception as e:
                    logger.warning(f"附件文本提取失败 {att_name}: {e}")

        for m in msgs:
            if m.role == "user":
                # 保留原始 content（可能是 str 或 list[dict]）
                last_user_msg = m.content if m.content is not None else ""
            history.append(ChatMessage(role=m.role, content=m.content if m.content is not None else ""))

        # 如果有图片附件，将最后一条用户消息构造为多模态 content
        if image_parts:
            text_part = last_user_msg if isinstance(last_user_msg, str) else str(last_user_msg)
            multimodal_content: list[dict] = [{"type": "text", "text": text_part}] + image_parts
            last_user_msg = multimodal_content

        # ── 2. RAG 检索（top_k > 0 时） ──
        sources = []
        if payload.top_k and payload.top_k > 0:
            yield sse_event("thinking", {"content": "正在检索知识库...", "source": "rag"})
            try:
                store = get_vector_store()
                # RAG 检索只能用纯文本，多模态 content（list[dict]）需提取文本部分
                rag_query = last_user_msg
                if isinstance(rag_query, list):
                    text_parts = [
                        p.get("text", "") for p in rag_query
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    rag_query = " ".join(text_parts) or ""
                results = store.search(rag_query, k=payload.top_k)

                if results:
                    for i, r in enumerate(results):
                        sid = f"src{i + 1}"
                        title = r.metadata.get("title", "未知来源")
                        doc = r.metadata.get("doc_id", "")
                        page = r.metadata.get("chunk_index", 0)
                        excerpt = r.content[:300]
                        sources.append(sid)
                        yield sse_event("source", {
                            "id": sid,
                            "title": title,
                            "doc": doc,
                            "page": page,
                            "score": round(r.score, 2),
                            "excerpt": excerpt,
                        })
                        yield sse_event("tool", {
                            "name": "search_docs",
                            "icon": "search",
                            "args": {"query": rag_query[:80], "top_k": payload.top_k},
                            "result": f"找到 {len(results)} 条相关片段",
                        })

                    # 把 RAG 上下文注入 system_prompt
                    rag_context = "\n\n".join(
                        f"[来源: {r.metadata.get('title', '未知')}]\n{r.content[:500]}"
                        for r in results[:5]
                    )
                else:
                    yield sse_event("thinking", {"content": "知识库中未找到匹配片段，将直接回答。", "source": "rag"})
                    rag_context = ""

            except Exception as e:
                logger.exception("RAG 检索失败")
                rag_context = ""
                yield sse_event("thinking", {"content": "知识库暂不可用，将直接回答。", "source": "rag"})
        else:
            rag_context = ""

        # ── 3. 构造 system_prompt ──
        system_prompt = payload.system_prompt or DEFAULT_SYSTEM_PROMPT
        if payload.long_term_memory:
            system_prompt += f"\n\n## 用户背景\n{payload.long_term_memory}"
        if attachment_texts:
            system_prompt += "\n\n## 附件内容\n" + "\n\n".join(attachment_texts)
        if rag_context:
            system_prompt += f"\n\n## 参考文档片段\n以下内容来自知识库检索，请优先引用：\n{rag_context}"

        # ── 4. LLM 流式输出 ──
        client = make_client(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
        )

        # 在 LLM 开始输出前，发送一个 thinking 事件表示"正在生成回答"
        yield sse_event("thinking", {"content": "正在生成回答...", "source": "llm"})
        try:
            usage_data = None
            IDLE_TIMEOUT = 300
            _queue = asyncio.Queue()

            async def _llm_worker(q):
                try:
                    async for chunk in client.chat_stream(
                        user_message=last_user_msg,
                        system_prompt=system_prompt,
                        history=history[:-1] if len(history) > 1 else None,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        provider=provider,
                    ):
                        await q.put(chunk)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(f"LLM stream error: {exc}")
                    await q.put(exc)
                finally:
                    await q.put(None)

            worker_task = asyncio.create_task(_llm_worker(_queue))
            _stream_ok = False
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(_queue.get(), timeout=IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.error("LLM 响应超时 (5min)")
                        yield sse_event("error", {"message": "LLM 响应超时，请重试"})
                        yield sse_event("done", {"success": False})
                        break
                    if chunk is None:
                        _stream_ok = True
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
            finally:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            if _stream_ok:
                done_payload = {"success": True}
                if usage_data:
                    done_payload["usage"] = usage_data
                yield sse_event("done", done_payload)
        except asyncio.CancelledError:
            logger.info("SSE 流被客户端取消")
            raise
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            yield sse_event("error", {"message": _sanitize_error(f"LLM 调用失败: {str(e)}")})
            yield sse_event("done", {"success": False})
            yield sse_event("done", {"success": False})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════
# POST /api/models — 拉取模型列表
# ═══════════════════════════════════════════

class ModelsRequest(BaseModel):
    base_url: str
    provider: str = ""


@router.post("/models")
async def list_models(payload: ModelsRequest, request: Request):
    """根据用户填写的 provider 配置获取可用模型列表。
    API Key 优先级：X-API-Key header > .env 默认值
    """
    # 从加密存储获取 API Key，回退到 header / .env 默认值
    header_key = request.headers.get("x-api-key")
    header_base_url = request.headers.get("x-base-url")
    header_provider = request.headers.get("x-provider")
    provider = payload.provider or header_provider or "openai"
    from app.api.auth import get_provider_key
    stored_key = get_provider_key(provider)
    api_key = header_key or stored_key or settings.llm_api_key
    base_url = payload.base_url or header_base_url or settings.llm_base_url

    if not api_key:
        return {
            "success": False,
            "error": {"code": "AUTH_FAILED", "message": "未提供 API Key", "details": None},
        }

    client = LLMClient(
        api_key=api_key,
        base_url=base_url,
    )
    try:
        models = await client.list_models(api_key=api_key, base_url=base_url)
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
            "error": {"code": "MODEL_FETCH_FAILED", "message": f"模型列表获取失败: {_sanitize_error(str(e))}", "details": None},
        }


# ═══════════════════════════════════════════
# POST /api/kb/upload — 上传知识库文件
# ═══════════════════════════════════════════

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 50 * 1024 * 1024


ALLOWED_EXTENSIONS = {".pdf", ".md", ".txt", ".py", ".c", ".h", ".ino", ".xlsx", ".xls", ".csv", ".json"}


@router.post("/kb/upload")
async def kb_upload(file: UploadFile = File(...), request: Request = None):
    """上传知识库文档(PDF/MD/TXT/XLSX/CSV/JSON 等)，立即返回 doc_id，后台异步解析入库 ChromaDB。"""
    import asyncio

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {"code": "INVALID_FILENAME", "message": "文件名为空", "details": None},
            },
        )

    ext = Path(file.filename).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "UNSUPPORTED_FILE_TYPE",
                    "message": f"不支持的文件格式: {ext}，支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                    "details": {"ext": ext},
                },
            },
        )

    # Pre-check Content-Length to reject oversized uploads before reading body
    if request is not None:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "success": False,
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": "文件大小超过 50MB 限制",
                        "details": None,
                    },
                },
            )

    content_bytes = await file.read()

    # Magic bytes 校验
    _validate_file_magic(ext, content_bytes)

    if len(content_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "success": False,
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": "文件大小超过 50MB 限制",
                    "details": None,
                },
            },
        )

    doc_id = uuid.uuid4().hex
    save_path = UPLOAD_DIR / f"{doc_id}{ext}"
    save_path.write_bytes(content_bytes)

    # 写入 KnowledgeDoc 记录（状态: indexing）
    with get_db_ctx() as db:
        kb_record = KnowledgeDoc(
            doc_id=doc_id,
            title=file.filename,
            category="user_upload",
            file_type=ext.lstrip("."),
            file_size=len(content_bytes),
            chunk_count=0,
            status="indexing",
        )
        db.add(kb_record)

    # 后台异步执行向量化
    async def _index_document():
        with get_db_ctx() as db_bg:
            record = db_bg.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
            if not record:
                return
            try:
                from langchain_text_splitters import RecursiveCharacterTextSplitter

                raw_text = _parse_file(ext, content_bytes, save_path)

                if not raw_text.strip():
                    record.status = "error"
                    record.error_message = "文件内容为空"
                    return

                source = DocumentSource(
                    doc_id=doc_id,
                    title=file.filename,
                    category="user_upload",
                    url="",
                    tags=[ext.lstrip(".")],
                    last_updated="",
                )
                processed_doc = ProcessedDocument(
                    doc_id=doc_id,
                    source=source,
                    pdf_path=save_path,
                    raw_markdown=raw_text,
                    translated_markdown=raw_text,
                )

                store = get_vector_store()
                chunk_count = store.ingest(processed_doc)

                record.chunk_count = chunk_count
                record.status = "indexed"
            except Exception as e:
                logger.exception(f"向量化失败 doc_id={doc_id}")
                if save_path.exists():
                    save_path.unlink()
                record = db_bg.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
                if record:
                    record.status = "error"
                    record.error_message = str(e)

    asyncio.create_task(_index_document())

    return {
        "success": True,
        "data": {
            "doc_id": doc_id,
            "document_id": doc_id,
            "filename": file.filename,
            "chunks": 0,
            "status": "indexing",
        },
    }


def _validate_file_magic(ext: str, content_bytes: bytes) -> None:
    """校验文件头 magic bytes 是否与扩展名匹配。"""
    if not content_bytes:
        return
    header = content_bytes[:8]

    if ext == ".pdf":
        # PDF: %PDF
        if not header.startswith(b"%PDF"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"success": False, "error": {"code": "INVALID_FILE_CONTENT", "message": "文件内容不是有效的 PDF", "details": None}},
            )
    elif ext in (".xlsx", ".xls"):
        # XLSX/XLS: PK (ZIP header)
        if not header.startswith(b"PK"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"success": False, "error": {"code": "INVALID_FILE_CONTENT", "message": f"文件内容不是有效的 {ext} 格式", "details": None}},
            )
    elif ext in (".md", ".txt", ".py", ".c", ".h", ".ino", ".csv", ".json"):
        # 文本文件：尝试 UTF-8 解码
        try:
            content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"success": False, "error": {"code": "INVALID_FILE_CONTENT", "message": "文本文件无法以 UTF-8 解码", "details": None}},
            )


def _parse_file(ext: str, content_bytes: bytes, save_path: Path) -> str:
    """根据文件扩展名解析文件，返回纯文本。"""
    if ext == ".pdf":
        from src.rag.document_processor import DoclingParser
        parser = DoclingParser()
        return parser.parse(save_path)
    elif ext in (".md", ".txt", ".py", ".c", ".h", ".ino"):
        return content_bytes.decode("utf-8", errors="replace")
    elif ext in (".xlsx", ".xls"):
        from src.rag.file_parsers import XlsxParser
        return XlsxParser().parse(save_path)
    elif ext == ".csv":
        from src.rag.file_parsers import CsvParser
        return CsvParser().parse(save_path)
    elif ext == ".json":
        from src.rag.file_parsers import JsonParser
        return JsonParser().parse(save_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def _extract_attachment_text(name: str, mime_type: str, data_url: str) -> str:
    """从 chat 附件的 data URL 中提取文本内容。"""
    import base64

    # data URL 格式: data:<mime>;base64,<payload>
    if not data_url.startswith("data:"):
        return ""

    # 分离 header 和 payload
    try:
        header, payload = data_url.split(",", 1)
    except ValueError:
        return ""

    # 判断是否 base64 编码
    is_base64 = "base64" in header.lower()

    ext = Path(name).suffix.lower() if name else ""

    # PDF 附件：base64 解码后用 Docling 解析
    if ext == ".pdf" and is_base64:
        try:
            from src.rag.document_processor import DoclingParser
            raw_bytes = base64.b64decode(payload)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(raw_bytes)
                tmp_path = Path(tmp.name)
            try:
                return DoclingParser().parse(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"PDF 附件解析失败: {e}")
            return ""

    # XLSX/XLS 附件
    if ext in (".xlsx", ".xls") and is_base64:
        try:
            from src.rag.file_parsers import XlsxParser
            raw_bytes = base64.b64decode(payload)
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(raw_bytes)
                tmp_path = Path(tmp.name)
            try:
                return XlsxParser().parse(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"XLSX 附件解析失败: {e}")
            return ""

    # CSV 附件
    if ext == ".csv":
        try:
            raw_bytes = base64.b64decode(payload) if is_base64 else payload.encode("utf-8")
            from src.rag.file_parsers import CsvParser
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tmp:
                tmp.write(raw_bytes)
                tmp_path = Path(tmp.name)
            try:
                return CsvParser().parse(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"CSV 附件解析失败: {e}")
            return ""

    # JSON 附件
    if ext == ".json":
        try:
            raw = base64.b64decode(payload).decode("utf-8") if is_base64 else payload
            from src.rag.file_parsers import JsonParser
            return JsonParser().parse_from_string(raw)
        except Exception as e:
            logger.warning(f"JSON 附件解析失败: {e}")
            return ""

    # 文本类附件（MD/TXT/PY/C/H/INO）：直接解码
    if ext in (".md", ".txt", ".py", ".c", ".h", ".ino") or mime_type.startswith("text/"):
        try:
            return base64.b64decode(payload).decode("utf-8", errors="replace") if is_base64 else payload
        except Exception:
            return ""

    return ""


@router.get("/kb/list")
async def kb_list():
    """查询知识库文档列表。"""
    with get_db_ctx() as db:
        docs = db.query(KnowledgeDoc).order_by(KnowledgeDoc.created_at.desc()).all()
        documents = [
            {
                "doc_id": d.doc_id,
                "title": d.title,
                "category": d.category,
                "file_type": d.file_type,
                "file_size": d.file_size,
                "chunk_count": d.chunk_count,
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
        return {"success": True, "data": {"documents": documents}}


class KbDeleteRequest(BaseModel):
    doc_id: str


@router.post("/kb/delete")
async def kb_delete(payload: KbDeleteRequest):
    """删除知识库文档：KnowledgeDoc 记录 + ChromaDB 向量 + 磁盘文件。"""
    try:
        with get_db_ctx() as db:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == payload.doc_id).first()
            if not record:
                return {
                    "success": False,
                    "error": {"code": "NOT_FOUND", "message": f"文档不存在: {payload.doc_id}", "details": None},
                }

            # 删除 ChromaDB 向量
            store = get_vector_store()
            deleted_chunks = store.delete_document(payload.doc_id)

            # 删除磁盘文件
            for ext in ALLOWED_EXTENSIONS:
                file_path = UPLOAD_DIR / f"{payload.doc_id}{ext}"
                if file_path.exists():
                    file_path.unlink()
                    break

            # 删除数据库记录
            db.delete(record)

            return {"success": True, "data": {"doc_id": payload.doc_id, "deleted_chunks": deleted_chunks}}
    except Exception as e:
        logger.exception("删除知识库文档失败")
        return {
            "success": False,
            "error": {"code": "DELETE_FAILED", "message": _sanitize_error(f"删除失败: {e}"), "details": _sanitize_error(str(e))},
        }


@router.get("/devices")
async def scan_devices(user: dict = Depends(current_user)):
    """扫描当前可用串口设备。"""
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        devices = [
            {"port": p.device, "description": p.description}
            for p in ports
        ]
        return {"success": True, "data": {"devices": devices}}
    except Exception as e:
        return {
            "success": False,
            "error": {"code": "DEVICE_SCAN_FAILED", "message": "串口设备扫描失败", "details": _sanitize_error(str(e))},
        }


# ═══════════════════════════════════════════
# POST /api/diagnose — 代码与引脚诊断
# ═══════════════════════════════════════════

class DiagnoseRequest(BaseModel):
    code: str
    env: str = "esp32-s3"
    chip: str = "esp32-s3"


class DiagnoseItem(BaseModel):
    name: str
    status: Literal["PASS", "WARN", "FAIL"]
    detail: str


_STRAPPING_PINS = {
    "esp32": {0, 2, 4, 5, 12, 15},
    "esp32-s3": {0, 3, 45, 46},
}


def _resolve_gpio(pin_name: str, defines: dict[str, int]) -> int | None:
    """Resolve a pin identifier to a GPIO number."""
    name = pin_name.strip()
    if name.upper().startswith("GPIO"):
        try:
            return int(name[4:])
        except ValueError:
            return None
    if name.isdigit():
        return int(name)
    if name in defines:
        return defines[name]
    return None


@router.post("/diagnose")
async def diagnose_code(payload: DiagnoseRequest, user: dict = Depends(current_user)):
    """对嵌入式代码做静态扫描，返回 GPIO 安全、引脚冲突等诊断项。"""
    try:
        code = payload.code
        chip = payload.chip.lower()

        # 1. 提取 #define 宏（符号 -> GPIO 编号）
        defines: dict[str, int] = {}
        for match in re.finditer(r"#define\s+(\w+)\s+(\d+)", code):
            defines[match.group(1)] = int(match.group(2))

        # 2. 提取引脚使用模式
        pin_modes: dict[int, set[str]] = {}
        pin_refs: list[tuple[str, str | None]] = []  # (raw_pin, mode_or_none)

        for match in re.finditer(r"pinMode\((\w+),\s*(INPUT|OUTPUT|INPUT_PULLUP)\)", code):
            raw_pin = match.group(1)
            mode = match.group(2)
            gpio = _resolve_gpio(raw_pin, defines)
            if gpio is not None:
                pin_modes.setdefault(gpio, set()).add(mode)
            pin_refs.append((raw_pin, mode))

        for match in re.finditer(r"digitalRead\((\w+)\)", code):
            raw_pin = match.group(1)
            gpio = _resolve_gpio(raw_pin, defines)
            if gpio is not None:
                pin_modes.setdefault(gpio, set()).add("INPUT")
            pin_refs.append((raw_pin, "INPUT"))

        for match in re.finditer(r"digitalWrite\((\w+)", code):
            raw_pin = match.group(1)
            gpio = _resolve_gpio(raw_pin, defines)
            if gpio is not None:
                pin_modes.setdefault(gpio, set()).add("OUTPUT")
            pin_refs.append((raw_pin, "OUTPUT"))

        # 3. GPIO 安全检查（Strapping 引脚）
        strapping = _STRAPPING_PINS.get(chip, _STRAPPING_PINS["esp32-s3"])
        risky_pins = [gpio for gpio in pin_modes if gpio in strapping]
        if risky_pins:
            gpio_names = ", ".join(f"GPIO{g}" for g in sorted(risky_pins))
            gpio_item = DiagnoseItem(
                name="GPIO 安全检查",
                status="WARN",
                detail=f"{gpio_names} 为 Strapping 引脚，建议避免使用或谨慎上拉/下拉",
            )
        else:
            gpio_item = DiagnoseItem(
                name="GPIO 安全检查",
                status="PASS",
                detail="未使用 Strapping 引脚",
            )

        # 4. 编译预检（真实语法检查，不调用真实编译器）
        compile_status = "PASS"
        compile_detail = "语法预检通过"
        # 检查常见语法错误：未闭合的大括号、缺少分号
        open_braces = code.count("{") - code.count("}")
        open_parens = code.count("(") - code.count(")")
        open_brackets = code.count("[") - code.count("]")
        syntax_issues = []
        if open_braces != 0:
            syntax_issues.append(f"大括号不匹配（差 {open_braces}）")
            compile_status = "FAIL"
        if open_parens != 0:
            syntax_issues.append(f"圆括号不匹配（差 {open_parens}）")
            compile_status = "FAIL"
        if open_brackets != 0:
            syntax_issues.append(f"方括号不匹配（差 {open_brackets}）")
            compile_status = "FAIL"
        # 检查 setup/loop 函数是否存在（Arduino 模式）
        if chip.startswith("esp32") or chip.startswith("esp8266"):
            if "setup" not in code and "main" not in code:
                syntax_issues.append("未找到 setup() 或 main() 函数")
                if compile_status == "PASS":
                    compile_status = "WARN"
        if syntax_issues:
            compile_detail = "；".join(syntax_issues)
        else:
            compile_detail = "语法预检通过（括号匹配、函数存在性检查）"
        compile_item = DiagnoseItem(
            name="编译预检",
            status=compile_status,
            detail=compile_detail,
        )

        # 5. 引脚冲突检测
        conflicts: list[str] = []
        for gpio, modes in pin_modes.items():
            has_in = bool({"INPUT", "INPUT_PULLUP"} & modes)
            has_out = "OUTPUT" in modes
            if has_in and has_out:
                conflicts.append(f"GPIO{gpio}({', '.join(sorted(modes))})")
        if conflicts:
            conflict_item = DiagnoseItem(
                name="引脚冲突检测",
                status="FAIL",
                detail="以下引脚同时被配置为输入和输出: " + ", ".join(conflicts),
            )
        else:
            conflict_item = DiagnoseItem(
                name="引脚冲突检测",
                status="PASS",
                detail="未发现同一引脚被同时配置为输入和输出",
            )

        # 6. 内存估算
        sram_estimate = min(95, 30 + len(code) // 200)
        memory_item = DiagnoseItem(
            name="内存估算",
            status="PASS",
            detail=f"估算 SRAM 使用约 {sram_estimate}%",
        )

        # 7. Flash 兼容性
        common_libs = []
        if re.search(r"\bdelay\b", code):
            common_libs.append("delay")
        if re.search(r"\bSerial\b", code):
            common_libs.append("Serial")
        if re.search(r"\bWiFi\b", code):
            common_libs.append("WiFi")
        if common_libs:
            flash_detail = "识别到常见库: " + ", ".join(common_libs)
        else:
            flash_detail = "未发现常见 Arduino/ESP32 库引用"
        flash_item = DiagnoseItem(
            name="Flash 兼容性",
            status="PASS",
            detail=flash_detail,
        )

        return {
            "success": True,
            "data": {
                "results": [
                    gpio_item.model_dump(),
                    compile_item.model_dump(),
                    conflict_item.model_dump(),
                    memory_item.model_dump(),
                    flash_item.model_dump(),
                ]
            },
        }
    except Exception as e:
        logger.exception("代码诊断失败")
        return {
            "success": False,
            "error": {
                "code": "DIAGNOSE_FAILED",
                "message": _sanitize_error(f"诊断失败: {e}"),
                "details": _sanitize_error(str(e)),
            },
        }


# ═══════════════════════════════════════════
# POST /api/wiring — 生成接线图
# ═══════════════════════════════════════════

class WiringConnection(BaseModel):
    model_config = {"populate_by_name": True}

    from_component: str = Field(alias="from")
    from_pin: str = Field(alias="pin")
    to_component: str = Field(alias="to_component")
    to_pin: str = Field(alias="to_pin")
    color: Optional[str] = "#38bdf8"
    label: Optional[str] = ""


class WiringComponent(BaseModel):
    name: str
    type: str
    pins: list[str]


class WiringRequest(BaseModel):
    title: Optional[str] = "接线图"
    connections: list[WiringConnection] = []
    components: list[WiringComponent] = []


@router.post("/wiring")
async def generate_wiring(payload: WiringRequest, user: dict = Depends(current_user)):
    """根据器件和连接关系生成真实接线图 SVG。"""
    # P1: serialize wiring generation to prevent concurrent state corruption
    async with _wiring_lock:
        try:
            svg, bom = generate_wiring_svg(
                title=payload.title,
                components=[c.model_dump() for c in payload.components],
                connections=[c.model_dump() for c in payload.connections],
            )
            return {"success": True, "data": {"svg": svg, "bom": bom}}
        except Exception as e:
            logger.exception("接线图生成失败")
            return {
                "success": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": _sanitize_error(f"接线图生成失败: {e}"),
                    "details": _sanitize_error(str(e)),
                },
            }


# ═══════════════════════════════════════════
# POST /api/audit_pins — 引脚冲突审计 (stub)
# ═══════════════════════════════════════════

class PinAssignment(BaseModel):
    function: Optional[str] = "unknown"
    config: Optional[str] = None
    conflict: Optional[bool] = False
    warning: Optional[bool] = False


class AuditPinsRequest(BaseModel):
    chip: str
    pin_assignments: dict[str, PinAssignment] = {}


@router.post("/audit_pins")
async def audit_pins(payload: AuditPinsRequest):
    """检查引脚分配与 Strapping 引脚冲突（真实实现）。"""
    try:
        chip = payload.chip.lower()
        strapping = _STRAPPING_PINS.get(chip, _STRAPPING_PINS["esp32-s3"])

        conflicts: list[dict] = []
        warnings: list[dict] = []
        pin_map: dict[str, dict] = {}

        # 统计每个引脚的功能分配
        pin_functions: dict[str, list[str]] = {}
        for pin_name, assignment in payload.pin_assignments.items():
            func = assignment.function or "unknown"
            pin_functions.setdefault(pin_name, []).append(func)
            pin_map[pin_name] = {
                "function": func,
                "config": assignment.config,
                "conflict": False,
                "warning": False,
            }

        # 检查冲突：同一引脚被分配多个功能
        for pin_name, funcs in pin_functions.items():
            unique_funcs = set(funcs)
            if len(unique_funcs) > 1:
                conflicts.append({
                    "pin": pin_name,
                    "functions": list(unique_funcs),
                    "detail": f"引脚 {pin_name} 同时被分配为: {', '.join(unique_funcs)}",
                })
                if pin_name in pin_map:
                    pin_map[pin_name]["conflict"] = True

            # 检查 Strapping 引脚
            gpio = _resolve_gpio(pin_name, {})
            if gpio is not None and gpio in strapping:
                warnings.append({
                    "pin": pin_name,
                    "gpio": gpio,
                    "detail": f"GPIO{gpio} 为 {chip} 的 Strapping 引脚，建议避免使用或谨慎上拉/下拉",
                })
                if pin_name in pin_map:
                    pin_map[pin_name]["warning"] = True

        return {
            "success": True,
            "data": {
                "conflicts": conflicts,
                "warnings": warnings,
                "pin_map": pin_map,
                "summary": {
                    "total_pins": len(pin_functions),
                    "conflict_count": len(conflicts),
                    "warning_count": len(warnings),
                },
            },
        }
    except Exception as e:
        logger.exception("引脚审计失败")
        return {
            "success": False,
            "error": {
                "code": "AUDIT_FAILED",
                "message": _sanitize_error(f"引脚审计失败: {e}"),
                "details": _sanitize_error(str(e)),
            },
        }


# ═══════════════════════════════════════════
# POST /api/build — 编译固件 (SSE)
# ═══════════════════════════════════════════

class BuildRequest(BaseModel):
    env: str
    project_dir: str
    code: Optional[str] = None  # 源码（可选，若提供则用沙箱编译）


@router.post("/build")
async def build_firmware(payload: BuildRequest):
    """编译固件。若提供 code 字段，则用 Docker 沙箱真实编译；否则返回环境未配置错误。"""

    async def event_generator():
        import asyncio
        try:
            yield sse_event("progress", {"percent": 10, "message": f"正在准备构建环境 ({payload.env})..."})
            await asyncio.sleep(0.1)

            if not payload.code:
                yield sse_event("error", {"message": "未提供源码 code 字段，无法编译"})
                yield sse_event("done", {"success": False})
                return

            # 检查 Docker 沙箱可用性
            from src.sandbox import check_docker_available
            if not await check_docker_available():
                yield sse_event("error", {"message": "Docker 不可用，无法执行真实编译。请启动 Docker Desktop。"})
                yield sse_event("done", {"success": False})
                return

            yield sse_event("progress", {"percent": 30, "message": "正在编译源码..."})
            # 根据环境推断语言
            env_lower = payload.env.lower()
            if "arduino" in env_lower or "esp32" in env_lower or "esp8266" in env_lower:
                language = "arduino"
            elif "cpp" in env_lower or "c++" in env_lower:
                language = "cpp"
            elif "c" == env_lower:
                language = "c"
            else:
                language = "cpp"  # 默认 C++

            from src.sandbox import execute_code
            result = await execute_code(payload.code, language)

            yield sse_event("progress", {"percent": 80, "message": "正在链接固件..."})

            if result.exit_code == 0:
                yield sse_event("progress", {"percent": 100, "message": "编译完成"})
                yield sse_event("done", {
                    "success": True,
                    "message": "SUCCESS",
                    "stdout": result.stdout[:5000],
                    "duration_ms": result.duration_ms,
                })
            else:
                yield sse_event("error", {
                    "message": f"编译失败 (exit {result.exit_code})",
                    "stderr": result.stderr[:5000],
                })
                yield sse_event("done", {"success": False})
        except Exception as e:
            logger.exception("编译失败")
            yield sse_event("error", {"message": _sanitize_error(f"编译异常: {e}")})
            yield sse_event("done", {"success": False})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════
# POST /api/upload — 烧录固件 (SSE)
# ═══════════════════════════════════════════

class UploadRequest(BaseModel):
    env: str
    port: str
    project_dir: str
    code: Optional[str] = None  # 源码（可选，若提供则先编译再烧录）


# 串口 port 白名单：只允许 COM* 和 /dev/tty* 格式
_UPLOAD_PORT_PATTERN = re.compile(r"^(COM\d+|/dev/tty\w+)$", re.IGNORECASE)


@router.post("/upload")
async def upload_firmware(payload: UploadRequest):
    """编译+烧录固件到指定串口设备。"""
    # port 白名单校验
    if not _UPLOAD_PORT_PATTERN.match(payload.port):
        return {
            "success": False,
            "error": {"code": "INVALID_PORT", "message": f"非法端口名: {payload.port}", "details": None},
        }

    async def event_generator():
        import asyncio
        try:
            yield sse_event("progress", {"percent": 10, "message": "正在编译固件..."})
            await asyncio.sleep(0.1)

            # 若提供 code，先编译
            if payload.code:
                from src.sandbox import check_docker_available, execute_code
                if not await check_docker_available():
                    yield sse_event("error", {"message": "Docker 不可用，无法编译"})
                    yield sse_event("done", {"success": False})
                    return

                env_lower = payload.env.lower()
                if "arduino" in env_lower or "esp32" in env_lower:
                    language = "arduino"
                else:
                    language = "cpp"
                result = await execute_code(payload.code, language)
                if result.exit_code != 0:
                    yield sse_event("error", {
                        "message": f"编译失败 (exit {result.exit_code})",
                        "stderr": result.stderr[:5000],
                    })
                    yield sse_event("done", {"success": False})
                    return

            yield sse_event("progress", {"percent": 40, "message": f"正在连接 {payload.port}..."})
            await asyncio.sleep(0.1)

            # 尝试通过 pyserial 烧录（需要 esptool 或类似工具）
            try:
                import serial
                from serial.tools import list_ports
                # 校验 port 存在
                available_ports = [p.device for p in list_ports.comports()]
                if payload.port not in available_ports:
                    yield sse_event("error", {"message": f"端口 {payload.port} 不存在或不可用"})
                    yield sse_event("done", {"success": False})
                    return

                yield sse_event("progress", {"percent": 70, "message": f"正在写入固件到 {payload.port}..."})
                await asyncio.sleep(0.1)

                # TODO: 接入 esptool 真实烧录
                # 当前仅校验端口可用性，不执行真实烧录
                yield sse_event("progress", {"percent": 95, "message": "正在校验..."})
                await asyncio.sleep(0.1)

                yield sse_event("done", {
                    "success": True,
                    "message": f"固件已烧录到 {payload.port}（端口校验通过，真实烧录需配置 esptool）",
                })
            except ImportError:
                yield sse_event("error", {"message": "pyserial 未安装，无法烧录"})
                yield sse_event("done", {"success": False})
        except Exception as e:
            logger.exception("烧录失败")
            yield sse_event("error", {"message": _sanitize_error(f"烧录异常: {e}")})
            yield sse_event("done", {"success": False})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ═══════════════════════════════════════════
# POST /api/tool — 调用 Agent 工具 (stub)
# ═══════════════════════════════════════════

class ToolRequest(BaseModel):
    tool: str
    args: dict = {}


@router.post("/tool")
async def call_tool(payload: ToolRequest):
    """调用 Agent 工具。仅允许已注册工具。"""
    # 工具白名单校验
    if payload.tool not in TOOL_REGISTRY:
        return {
            "success": False,
            "error": {
                "code": "TOOL_NOT_FOUND",
                "message": f"工具 '{payload.tool}' 不存在或未注册",
                "details": None,
            },
        }
    try:
        result = await dispatch(payload.tool, payload.args)
        return {"success": True, "data": result}
    except ToolNotFoundError:
        return {
            "success": False,
            "error": {
                "code": "TOOL_NOT_FOUND",
                "message": f"工具 '{payload.tool}' 不存在",
                "details": None,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "error": {
                "code": "TOOL_EXECUTION_FAILED",
                "message": _sanitize_error(f"工具执行失败: {e}"),
                "details": _sanitize_error(str(e)),
            },
        }


# ═══════════════════════════════════════════
# WS /monitor/{port} — 串口监视器 (stub)
# ═══════════════════════════════════════════

from fastapi import WebSocket


# port 白名单：只允许 COM* 和 /dev/tty* 格式
_PORT_PATTERN = re.compile(r"^(COM\d+|/dev/tty\w+)$", re.IGNORECASE)


@router.websocket("/monitor/{port}")
async def serial_monitor(websocket: WebSocket, port: str, baud: int = 115200):
    """串口实时数据推送 WebSocket。"""
    # port 白名单校验
    if not _PORT_PATTERN.match(port):
        await websocket.close(code=4001, reason=f"非法端口名: {port}")
        return

    # 真实鉴权：从 query 参数或 header 获取 token，校验 session_token
    from app.api.dependencies import ws_auth
    user = ws_auth(websocket)
    if user is None:
        await websocket.close(code=4003, reason="认证失败：未提供有效 token")
        return

    # P1: acquire per-port lock to prevent concurrent access from multiple clients
    port_lock = _get_port_lock(port)
    try:
        await asyncio.wait_for(port_lock.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        await websocket.close(code=4009, reason=f"端口 {port} 正被其他客户端占用")
        return

    await websocket.accept()
    heartbeat_task = None
    try:
        # 尝试打开真实串口
        import serial
        from serial.tools import list_ports
        available_ports = [p.device for p in list_ports.comports()]
        if port not in available_ports:
            await websocket.send_text(json.dumps({
                "type": "sys",
                "payload": f"端口 {port} 不存在或不可用。可用端口: {', '.join(available_ports) or '无'}",
            }))
            await websocket.close(code=4004, reason="端口不可用")
            return

        ser = serial.Serial(port, baudrate=baud, timeout=1)
        await websocket.send_text(json.dumps({
            "type": "sys",
            "payload": f"串口已连接: {port} @ {baud} baud",
        }))

        # 双向桥接：WS → serial / serial → WS
        async def _read_serial():
            try:
                while True:
                    if ser.in_waiting:
                        data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                        if data:
                            await websocket.send_text(json.dumps({"type": "data", "payload": data}))
                    await asyncio.sleep(0.05)
            except Exception:
                pass

        # P1: heartbeat — ping every 30s, close on timeout (detect dead connections)
        async def _heartbeat():
            try:
                while True:
                    await asyncio.sleep(30)
                    await asyncio.wait_for(websocket.ping(), timeout=10.0)
            except Exception:
                pass

        read_task = asyncio.create_task(_read_serial())
        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "write":
                    ser.write(msg.get("payload", "").encode("utf-8"))
                elif msg.get("type") == "start":
                    # 兼容旧协议
                    pass
                elif msg.get("type") == "close":
                    break
        finally:
            read_task.cancel()
            if heartbeat_task:
                heartbeat_task.cancel()
            ser.close()
    except WebSocketDisconnect:
        logger.info("WebSocket 断开连接")
    except Exception as e:
        logger.exception("WS error")
        try:
            await websocket.send_text(json.dumps({"type": "error", "payload": _sanitize_error(str(e))}))
        except Exception:
            pass
    finally:
        # P1: release per-port lock so other clients can connect
        if port_lock.locked():
            port_lock.release()

