"""
知识库路由 — /api/kb/upload /list /delete + collections 管理

使用新的 chunking 模块（hybrid / agent）和 KnowledgeBaseManager（多 KB + BM25 + RRF）。
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Request
from pydantic import BaseModel

from src.config.settings import settings
from app.db.models import KnowledgeDoc, KnowledgeBase
from app.api.common import get_db_ctx, sanitize_error
from app.api.auth import encrypt_key, decrypt_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".md", ".txt", ".py", ".c", ".h", ".ino", ".xlsx", ".xls", ".csv", ".json"}

# Default KB ID when none specified (fallback to builtin)
DEFAULT_KB_ID = "builtin-001"


# ═══════════════════════════════════════════
# File parsing helpers
# ═══════════════════════════════════════════

def _validate_file_magic(ext: str, content_bytes: bytes) -> None:
    """校验文件头 magic bytes 是否与扩展名匹配。"""
    if not content_bytes:
        return
    magic_map = {
        b"%PDF": ".pdf",
        b"PK": {".xlsx", ".xls", ".docx"},
    }
    for magic, expected in magic_map.items():
        if content_bytes.startswith(magic):
            if isinstance(expected, set):
                if ext not in expected:
                    raise ValueError(f"文件头为 Office 文档格式，但扩展名为 {ext}，请修正扩展名")
            elif ext != expected:
                raise ValueError(f"文件头为 {expected} 格式，但扩展名为 {ext}")


def _parse_file(ext: str, content_bytes: bytes, save_path: Path) -> tuple[str, int]:
    """根据文件扩展名解析文件，返回 (纯文本, 总页数)。"""
    if ext == ".pdf":
        from src.rag.document_processor import DoclingParser
        parser = DoclingParser()
        text = parser.parse(save_path)
        # Try to get page count via PyMuPDF
        total_pages = 0
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(save_path))
            total_pages = doc.page_count
            doc.close()
        except Exception:
            pass
        return text, total_pages
    elif ext in (".xlsx", ".xls"):
        from src.rag.file_parsers import ExcelParser
        return ExcelParser().parse_from_bytes(content_bytes), 0
    elif ext == ".csv":
        from src.rag.file_parsers import CsvParser
        return CsvParser().parse_from_bytes(content_bytes), 0
    elif ext == ".json":
        from src.rag.file_parsers import JsonParser
        return JsonParser().parse_from_bytes(content_bytes), 0
    else:
        return content_bytes.decode("utf-8", errors="replace"), 0


# ═══════════════════════════════════════════
# KB Manager helper
# ═══════════════════════════════════════════

def _get_kb_manager():
    """Lazy import to avoid circular deps."""
    from src.rag.kb_manager import get_kb_manager
    return get_kb_manager()


def _get_kb_chunker(kb: KnowledgeBase, chunk_method_override: Optional[str] = None, chunk_size: Optional[int] = None):
    """Get a chunker configured for a specific KB."""
    from src.rag.chunking import get_chunker

    method = chunk_method_override or kb.chunk_method or "hybrid"

    if method == "agent":
        # Decrypt agent chunker API key
        agent_key = ""
        if kb.agent_chunker_api_key_encrypted:
            try:
                agent_key = decrypt_key(kb.agent_chunker_api_key_encrypted)
            except Exception:
                logger.warning(f"Failed to decrypt agent chunker key for KB {kb.id}")

        return get_chunker(
            "agent",
            model=kb.agent_chunker_model or "gpt-4o-mini",
            base_url=kb.agent_chunker_base_url or "https://api.openai.com/v1",
            api_key=agent_key,
            context_window=kb.context_window or 256000,
        )
    else:
        kwargs = {}
        if chunk_size:
            kwargs["chunk_size"] = chunk_size
        return get_chunker("hybrid", **kwargs)


# ═══════════════════════════════════════════
# POST /api/kb/upload  (支持 kb_id + chunk_method)
# ═══════════════════════════════════════════

@router.post("/kb/upload")
async def kb_upload(
    file: UploadFile = File(...),
    kb_id: str = Form(DEFAULT_KB_ID),
    chunk_method: Optional[str] = Form(None),
    chunk_size: Optional[int] = Form(None),
    request: Request = None,
):
    """上传知识库文档，自动解析、分块、向量化入库。

    Args:
        file: 上传的文件 (PDF/MD/TXT/XLSX/CSV/JSON)
        kb_id: 目标知识库 ID，默认 builtin-001
        chunk_method: 覆盖 KB 默认分块方式 (hybrid / agent)
        chunk_size: 覆盖默认 chunk_size (仅 hybrid 有效)
    """
    if not file.filename:
        return {
            "success": False,
            "error": {"code": "INVALID_REQUEST", "message": "未提供文件名", "details": None},
        }
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {
            "success": False,
            "error": {"code": "UNSUPPORTED_FORMAT", "message": f"不支持的文件格式: {ext}", "details": None},
        }
    content_bytes = await file.read()
    if len(content_bytes) > MAX_UPLOAD_SIZE:
        return {
            "success": False,
            "error": {"code": "FILE_TOO_LARGE", "message": "文件大小超过限制 (50MB)", "details": {"max_size": MAX_UPLOAD_SIZE}},
        }
    try:
        _validate_file_magic(ext, content_bytes)
    except ValueError as e:
        return {
            "success": False,
            "error": {"code": "INVALID_FILE", "message": str(e), "details": None},
        }

    # Validate KB exists
    kb_manager = _get_kb_manager()
    kb = kb_manager.get_kb(kb_id)
    if not kb:
        return {
            "success": False,
            "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
        }

    # Validate chunk_method
    effective_method = chunk_method or kb.chunk_method or "hybrid"
    if effective_method not in ("hybrid", "agent"):
        return {
            "success": False,
            "error": {"code": "INVALID_REQUEST", "message": f"不支持的 chunk_method: {effective_method}", "details": None},
        }

    doc_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{doc_id}{ext}"
    save_path.write_bytes(content_bytes)

    # Write KnowledgeDoc record (status: indexing)
    with get_db_ctx() as db:
        record = KnowledgeDoc(
            doc_id=doc_id,
            kb_id=kb_id,
            title=file.filename,
            category="user_upload",
            file_type=ext.lstrip("."),
            file_size=len(content_bytes),
            chunk_count=0,
            chunk_method_used=effective_method,
            status="indexing",
        )
        db.add(record)

    # Background indexing
    async def _index_document():
        try:
            # Parse file
            text_content, total_pages = _parse_file(ext, content_bytes, save_path)
            if not text_content.strip():
                _update_doc_status(doc_id, "error", error_message="文件解析后内容为空")
                return

            # Get chunker (re-fetch KB to avoid detached session)
            kb_bg = kb_manager.get_kb(kb_id)
            if not kb_bg:
                _update_doc_status(doc_id, "error", error_message="知识库不存在")
                return

            chunker = _get_kb_chunker(kb_bg, chunk_method_override=effective_method, chunk_size=chunk_size)

            # Run chunking
            metadata = {
                "doc_id": doc_id,
                "title": file.filename,
                "file_type": ext.lstrip("."),
                "category": "user_upload",
            }
            chunks = await chunker.chunk(
                text=text_content,
                metadata=metadata,
                file_path=save_path,
                total_pages=total_pages,
            )

            if not chunks:
                _update_doc_status(doc_id, "error", error_message="分块结果为空")
                return

            # Ingest into KB
            ingested = kb_manager.ingest_chunks(kb_id, chunks, doc_id)

            # Verify page coverage
            from src.rag.chunking import verify_page_coverage
            coverage = verify_page_coverage(chunks, total_pages)

            _update_doc_status(
                doc_id,
                "indexed",
                chunk_count=ingested,
                coverage=coverage,
            )
            logger.info(f"文档入库完成: {doc_id} → {ingested} chunks (method={effective_method})")

        except Exception as e:
            logger.exception(f"后台索引失败: {doc_id}")
            if save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass
            _update_doc_status(doc_id, "error", error_message=sanitize_error(str(e)))

    asyncio.create_task(_index_document())

    return {
        "success": True,
        "data": {
            "doc_id": doc_id,
            "kb_id": kb_id,
            "filename": file.filename,
            "chunk_method_used": effective_method,
            "chunks": 0,
            "status": "indexing",
        },
    }


def _update_doc_status(
    doc_id: str,
    status: str,
    chunk_count: int = None,
    error_message: str = None,
    coverage: dict = None,
):
    """Update KnowledgeDoc status in DB."""
    try:
        with get_db_ctx() as db:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
            if not record:
                return
            record.status = status
            if chunk_count is not None:
                record.chunk_count = chunk_count
            if error_message is not None:
                record.error_message = error_message
            if coverage:
                # Store coverage as JSON in error_message field if status=error,
                # otherwise we skip (coverage only returned in upload response for success)
                pass
    except Exception:
        logger.exception(f"更新文档状态失败: {doc_id}")


# ═══════════════════════════════════════════
# GET /api/kb/list
# ═══════════════════════════════════════════

@router.get("/kb/list")
async def kb_list(kb_id: Optional[str] = None):
    """查询知识库文档列表。可按 kb_id 过滤。"""
    with get_db_ctx() as db:
        query = db.query(KnowledgeDoc)
        if kb_id:
            query = query.filter(KnowledgeDoc.kb_id == kb_id)
        docs = query.order_by(KnowledgeDoc.created_at.desc()).all()
        documents = [
            {
                "doc_id": d.doc_id,
                "kb_id": d.kb_id,
                "title": d.title,
                "category": d.category,
                "file_type": d.file_type,
                "file_size": d.file_size,
                "chunk_count": d.chunk_count,
                "chunk_method_used": d.chunk_method_used,
                "status": d.status,
                "error_message": d.error_message,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]
        return {"success": True, "data": {"documents": documents}}


# ═══════════════════════════════════════════
# POST /api/kb/delete
# ═══════════════════════════════════════════

class KbDeleteRequest(BaseModel):
    doc_id: str


@router.post("/kb/delete")
async def kb_delete(payload: KbDeleteRequest):
    """删除知识库文档。"""
    try:
        with get_db_ctx() as db:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == payload.doc_id).first()
            if not record:
                return {
                    "success": False,
                    "error": {"code": "DOC_NOT_FOUND", "message": f"文档不存在: {payload.doc_id}", "details": None},
                }

            kb_id = record.kb_id
            # Delete vectors from ChromaDB
            kb_manager = _get_kb_manager()
            kb = kb_manager.get_kb(kb_id) if kb_id else None
            if kb:
                store = kb_manager._get_store(kb)
                if store:
                    try:
                        deleted_chunks = store.delete_document(payload.doc_id)
                    except Exception:
                        deleted_chunks = 0
                        logger.warning(f"删除向量失败: {payload.doc_id}")
                else:
                    deleted_chunks = 0
            else:
                deleted_chunks = 0

            # Delete uploaded file
            for ext in ALLOWED_EXTENSIONS:
                file_path = UPLOAD_DIR / f"{payload.doc_id}{ext}"
                if file_path.exists():
                    file_path.unlink()
                    break

            db.delete(record)

            # Mark BM25 stale
            if kb_id:
                kb_manager._bm25_stale.add(kb_id)

            return {"success": True, "data": {"doc_id": payload.doc_id, "deleted_chunks": deleted_chunks}}
    except Exception as e:
        logger.exception("删除知识库文档失败")
        return {
            "success": False,
            "error": {
                "code": "DELETE_FAILED",
                "message": sanitize_error(f"删除失败: {e}"),
                "details": sanitize_error(str(e)),
            },
        }


# ═══════════════════════════════════════════
# KB Collection Management
# ═══════════════════════════════════════════

class CreateKBRequest(BaseModel):
    name: str
    description: str = ""
    chunk_method: str = "hybrid"  # hybrid / agent
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    agent_chunker_model: str = "gpt-4o-mini"
    agent_chunker_base_url: str = "https://api.openai.com/v1"
    agent_chunker_api_key: str = ""
    context_window: int = 256000


@router.get("/kb/collections")
async def list_collections():
    """列出所有知识库。"""
    try:
        kb_manager = _get_kb_manager()
        kbs = kb_manager.list_kbs()
        return {"success": True, "data": {"collections": kbs}}
    except Exception as e:
        logger.exception("列出知识库失败")
        return {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": sanitize_error(str(e)),
                "details": None,
            },
        }


@router.post("/kb/collections")
async def create_collection(req: CreateKBRequest):
    """创建知识库。"""
    try:
        if req.chunk_method not in ("hybrid", "agent"):
            return {
                "success": False,
                "error": {"code": "INVALID_REQUEST", "message": f"不支持的 chunk_method: {req.chunk_method}", "details": None},
            }

        kb_manager = _get_kb_manager()
        kb = kb_manager.create_kb(
            name=req.name,
            chunk_method=req.chunk_method,
            embedding_model=req.embedding_model,
            embedding_base_url=req.embedding_base_url,
            embedding_api_key=req.embedding_api_key,
            agent_chunker_model=req.agent_chunker_model,
            agent_chunker_base_url=req.agent_chunker_base_url,
            agent_chunker_api_key=req.agent_chunker_api_key,
            context_window=req.context_window,
            description=req.description,
        )
        return {
            "success": True,
            "data": {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description or "",
                "collection_name": kb.collection_name,
                "chunk_method": kb.chunk_method,
                "embedding_model": kb.embedding_model,
                "agent_chunker_model": kb.agent_chunker_model,
                "enabled": kb.enabled,
                "is_builtin": kb.is_builtin,
                "created_at": kb.created_at.isoformat() if kb.created_at else "",
            },
        }
    except Exception as e:
        logger.exception("创建知识库失败")
        return {
            "success": False,
            "error": {
                "code": "KB_CREATE_FAILED",
                "message": sanitize_error(str(e)),
                "details": None,
            },
        }


@router.get("/kb/collections/{kb_id}")
async def get_collection(kb_id: str):
    """获取单个知识库详情（含文档列表）。"""
    try:
        kb_manager = _get_kb_manager()
        kb = kb_manager.get_kb(kb_id)
        if not kb:
            return {
                "success": False,
                "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
            }

        with get_db_ctx() as db:
            docs = db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).all()
            documents = [
                {
                    "doc_id": d.doc_id,
                    "title": d.title,
                    "file_type": d.file_type,
                    "file_size": d.file_size,
                    "chunk_count": d.chunk_count,
                    "chunk_method_used": d.chunk_method_used,
                    "status": d.status,
                    "created_at": d.created_at.isoformat() if d.created_at else "",
                }
                for d in docs
            ]

        return {
            "success": True,
            "data": {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description or "",
                "collection_name": kb.collection_name,
                "chunk_method": kb.chunk_method,
                "embedding_model": kb.embedding_model,
                "embedding_base_url": kb.embedding_base_url or "",
                "agent_chunker_model": kb.agent_chunker_model,
                "agent_chunker_base_url": kb.agent_chunker_base_url or "",
                "context_window": kb.context_window,
                "enabled": kb.enabled,
                "is_builtin": kb.is_builtin,
                "created_at": kb.created_at.isoformat() if kb.created_at else "",
                "documents": documents,
            },
        }
    except Exception as e:
        logger.exception("获取知识库详情失败")
        return {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": sanitize_error(str(e)),
                "details": None,
            },
        }


@router.delete("/kb/collections/{kb_id}")
async def delete_collection(kb_id: str):
    """删除知识库（内置 KB 不可删除）。"""
    try:
        kb_manager = _get_kb_manager()
        kb = kb_manager.get_kb(kb_id)
        if not kb:
            return {
                "success": False,
                "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
            }
        if kb.is_builtin:
            return {
                "success": False,
                "error": {"code": "KB_NOT_DELETABLE", "message": "内置知识库不可删除", "details": None},
            }

        deleted = kb_manager.delete_kb(kb_id)
        if not deleted:
            return {
                "success": False,
                "error": {"code": "KB_DELETE_FAILED", "message": "删除失败", "details": None},
            }
        return {"success": True, "data": {"kb_id": kb_id}}
    except Exception as e:
        logger.exception("删除知识库失败")
        return {
            "success": False,
            "error": {
                "code": "KB_DELETE_FAILED",
                "message": sanitize_error(str(e)),
                "details": None,
            },
        }


class ToggleKBRequest(BaseModel):
    enabled: bool


@router.patch("/kb/collections/{kb_id}/toggle")
async def toggle_collection(kb_id: str, payload: ToggleKBRequest):
    """切换知识库搜索开关。"""
    try:
        kb_manager = _get_kb_manager()
        ok = kb_manager.toggle_kb(kb_id, payload.enabled)
        if not ok:
            return {
                "success": False,
                "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
            }
        return {"success": True, "data": {"kb_id": kb_id, "enabled": payload.enabled}}
    except Exception as e:
        logger.exception("切换知识库开关失败")
        return {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": sanitize_error(str(e)),
                "details": None,
            },
        }


# ═══════════════════════════════════════════
# GET /api/kb/embedding-models
# ═══════════════════════════════════════════

class EmbeddingModelsRequest(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""


@router.post("/kb/embedding-models")
async def list_embedding_models(payload: EmbeddingModelsRequest):
    """代理上游 embedding 模型列表。

    用 POST 而非 GET 是因为需要传 API Key（避免走 URL/logs）。
    """
    if not payload.api_key:
        return {
            "success": False,
            "error": {"code": "AUTH_FAILED", "message": "未提供 API Key", "details": None},
        }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {payload.api_key}"}
            url = payload.base_url.rstrip("/") + "/models"
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": {
                        "code": "MODEL_FETCH_FAILED",
                        "message": f"上游返回 {resp.status_code}",
                        "details": sanitize_error(resp.text[:500]),
                    },
                }
            data = resp.json()
            # Filter embedding models (heuristic: name contains 'embed')
            all_models = [m.get("id", "") for m in data.get("data", [])]
            embedding_models = [m for m in all_models if "embed" in m.lower()]
            if not embedding_models:
                # If no embedding-specific models found, return all
                embedding_models = all_models
            return {"success": True, "data": {"models": embedding_models}}
    except Exception as e:
        logger.exception("获取 embedding 模型列表失败")
        return {
            "success": False,
            "error": {
                "code": "MODEL_FETCH_FAILED",
                "message": sanitize_error(str(e)),
                "details": None,
            },
        }
