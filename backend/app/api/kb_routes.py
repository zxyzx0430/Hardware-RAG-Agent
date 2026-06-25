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

# Hold strong references to background indexing tasks so they aren't GC'd.
# Python's event loop only keeps weak refs to tasks; without this, a task can
# be collected mid-execution, raising CancelledError (BaseException) that
# escapes the `except Exception` handler and leaves doc status stuck at "indexing".
_bg_tasks: set = set()


# ═══════════════════════════════════════════
# File parsing helpers
# ═══════════════════════════════════════════

def _validate_file_magic(ext: str, content_bytes: bytes) -> None:
    """校验文件头 magic bytes 是否与扩展名匹配。"""
    if not content_bytes:
        return
    magic_map = {
        b"%PDF": ".pdf",
        b"PK": {".xlsx", ".xls"},
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
            # The following parameters use AgentChunker defaults but are
            # wired here so future KB-level config (e.g. a chunk_config JSON
            # column) can override them without touching this function.
            # num_rounds=getattr(kb, "agent_num_rounds", 3),
            # max_batch_chars=getattr(kb, "agent_max_batch_chars", 80000),
            # sub_chunk_size=getattr(kb, "agent_sub_chunk_size", 1000),
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

    # Check for duplicate filename in same KB
    with get_db_ctx() as db:
        existing = db.query(KnowledgeDoc).filter(
            KnowledgeDoc.kb_id == kb_id,
            KnowledgeDoc.title == file.filename
        ).first()
        # Extract scalar values inside the session to avoid DetachedInstanceError
        existing_doc_id = existing.doc_id if existing else None
        existing_status = existing.status if existing else None
    if existing_doc_id:
        # Auto-reclaim orphan records left by failed uploads (error) or crashed
        # indexing tasks (stale "indexing" status). Only block re-upload when
        # the previous upload genuinely succeeded ("indexed") — in that case
        # the user must explicitly delete first.
        if existing_status in ("error", "indexing"):
            logger.info(f"清理孤儿记录 {existing_doc_id} (status={existing_status}) 以便重新上传 {file.filename}")
            try:
                with get_db_ctx() as db:
                    rec = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == existing_doc_id).first()
                    if rec:
                        kb_id_of_rec = rec.kb_id
                        db.delete(rec)
                        if kb_id_of_rec:
                            _get_kb_manager()._bm25_stale.add(kb_id_of_rec)
                # Best-effort vector cleanup
                try:
                    kb_existing = kb_manager.get_kb(kb_id)
                    if kb_existing:
                        store = kb_manager._get_store(kb_existing)
                        if store:
                            store.delete_document(existing_doc_id)
                except Exception:
                    logger.warning(f"清理孤儿向量失败（不影响继续上传）: {existing_doc_id}")
                # Best-effort file cleanup
                for ext in ALLOWED_EXTENSIONS:
                    orphan_path = UPLOAD_DIR / f"{existing_doc_id}{ext}"
                    if orphan_path.exists():
                        try:
                            orphan_path.unlink()
                        except Exception:
                            logger.warning(f"清理孤儿文件失败: {orphan_path}")
                        break
            except Exception:
                logger.exception(f"清理孤儿记录失败: {existing_doc_id}")
                return {
                    "success": False,
                    "error": {
                        "code": "DUPLICATE_FILE",
                        "message": f"知识库中已存在同名文件: {file.filename}，且自动清理失败，请先手动删除旧文件再上传",
                        "details": {"existing_doc_id": existing_doc_id, "existing_status": existing_status},
                    },
                }
        else:
            return {
                "success": False,
                "error": {
                    "code": "DUPLICATE_FILE",
                    "message": f"知识库中已存在同名文件: {file.filename}，请先删除旧文件再上传",
                    "details": {"existing_doc_id": existing_doc_id, "existing_status": existing_status},
                },
            }

    # Validate chunk_method
    effective_method = chunk_method or kb.chunk_method or "hybrid"
    if effective_method not in ("hybrid", "agent"):
        return {
            "success": False,
            "error": {"code": "INVALID_REQUEST", "message": f"不支持的 chunk_method: {effective_method}", "details": None},
        }

    # Validate chunk_size range
    if chunk_size is not None and (chunk_size < 100 or chunk_size > 10000):
        return {
            "success": False,
            "error": {"code": "INVALID_REQUEST", "message": "chunk_size 必须在 100-10000 之间", "details": None},
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

            # Always record actual chunk count (ingested may be 0 if embedding not configured)
            actual_chunk_count = len(chunks)

            if ingested == 0:
                # Embedding not configured — chunks were created but not vectorized
                _update_doc_status(
                    doc_id,
                    "indexed",
                    chunk_count=actual_chunk_count,
                    error_message="未配置 Embedding 模型，已分块但未向量化。配置 Embedding 后请删除重新上传。",
                )
            else:
                _update_doc_status(
                    doc_id,
                    "indexed",
                    chunk_count=actual_chunk_count,
                    coverage=coverage,
                )
            logger.info(f"文档入库完成: {doc_id} → {actual_chunk_count} chunks (向量化: {ingested}, method={effective_method})")

        except asyncio.CancelledError:
            logger.warning(f"后台索引被取消: {doc_id}")
            _update_doc_status(doc_id, "error", error_message="索引任务被取消")
            raise
        except Exception as e:
            logger.exception(f"后台索引失败: {doc_id}")
            if save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass
            _update_doc_status(doc_id, "error", error_message=sanitize_error(str(e)))

    task = asyncio.create_task(_index_document())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

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
                import json as _json
                record.coverage_json = _json.dumps(coverage, default=str)
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
    """删除知识库文档：向量 → DB 记录（事务内）→ 文件（事务外 best-effort）。

    文件删除放在事务外，避免 Windows 文件锁导致 unlink 失败时整个事务回滚、
    DB 记录删不掉的死循环。文件删不掉只记日志，不影响 DB 一致性。
    """
    actual_doc_id = None
    kb_id = None
    deleted_chunks = 0

    # ── Phase 1: Delete vectors + DB record in transaction ──
    try:
        with get_db_ctx() as db:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == payload.doc_id).first()
            if not record:
                return {
                    "success": False,
                    "error": {"code": "DOC_NOT_FOUND", "message": f"文档不存在: {payload.doc_id}", "details": None},
                }

            kb_id = record.kb_id
            actual_doc_id = record.doc_id
            kb_manager = _get_kb_manager()

            # Step 1: Delete vectors from ChromaDB
            kb = kb_manager.get_kb(kb_id) if kb_id else None
            if kb:
                store = kb_manager._get_store(kb)
                if store:
                    try:
                        deleted_chunks = store.delete_document(actual_doc_id)
                    except Exception:
                        logger.exception(f"删除向量失败: {actual_doc_id}")
                        return {
                            "success": False,
                            "error": {"code": "VECTOR_DELETE_FAILED", "message": "向量删除失败，DB 记录已保留以便重试", "details": None},
                        }

            # Step 2: Delete DB record (vectors already cleaned)
            db.delete(record)

            # Mark BM25 stale
            if kb_id:
                kb_manager._bm25_stale.add(kb_id)
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

    # ── Phase 2: Best-effort file deletion (outside transaction) ──
    # File deletion failure must NOT rollback the DB record.
    if actual_doc_id:
        for ext in ALLOWED_EXTENSIONS:
            file_path = UPLOAD_DIR / f"{actual_doc_id}{ext}"
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    logger.warning(f"文件删除失败（不影响 DB 记录）: {file_path}")
                break

    return {"success": True, "data": {"doc_id": actual_doc_id, "deleted_chunks": deleted_chunks}}


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
# PATCH /api/kb/collections/{kb_id}/rename
# ═══════════════════════════════════════════

class RenameKBRequest(BaseModel):
    name: str


@router.patch("/kb/collections/{kb_id}/rename")
async def rename_collection(kb_id: str, payload: RenameKBRequest):
    """重命名知识库。"""
    try:
        with get_db_ctx() as db:
            kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
            if not kb:
                return {
                    "success": False,
                    "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
                }
            old_name = kb.name
            kb.name = payload.name.strip()
            if not kb.name:
                return {
                    "success": False,
                    "error": {"code": "INVALID_REQUEST", "message": "名称不能为空", "details": None},
                }
            return {"success": True, "data": {"kb_id": kb_id, "old_name": old_name, "new_name": kb.name}}
    except Exception as e:
        logger.exception("重命名知识库失败")
        return {
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": sanitize_error(str(e)), "details": None},
        }


# ═══════════════════════════════════════════
# PATCH /api/kb/collections/{kb_id}/config
# ═══════════════════════════════════════════

class UpdateKBConfigRequest(BaseModel):
    embedding_model: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_api_key: Optional[str] = None  # None=keep, ""=clear, "xxx"=set
    agent_chunker_model: Optional[str] = None
    agent_chunker_base_url: Optional[str] = None
    agent_chunker_api_key: Optional[str] = None
    chunk_method: Optional[str] = None
    context_window: Optional[int] = None
    description: Optional[str] = None


@router.patch("/kb/collections/{kb_id}/config")
async def update_kb_config(kb_id: str, payload: UpdateKBConfigRequest):
    """更新知识库配置（embedding/agent chunker/分块策略等）。"""
    try:
        kb_manager = _get_kb_manager()
        kb = kb_manager.update_kb_config(
            kb_id,
            embedding_model=payload.embedding_model,
            embedding_base_url=payload.embedding_base_url,
            embedding_api_key=payload.embedding_api_key,
            agent_chunker_model=payload.agent_chunker_model,
            agent_chunker_base_url=payload.agent_chunker_base_url,
            agent_chunker_api_key=payload.agent_chunker_api_key,
            chunk_method=payload.chunk_method,
            context_window=payload.context_window,
            description=payload.description,
        )
        if not kb:
            return {
                "success": False,
                "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
            }
        return {"success": True, "data": {"kb_id": kb_id, "message": "配置已更新，Store 缓存已失效"}}
    except Exception as e:
        logger.exception("更新知识库配置失败")
        return {
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": sanitize_error(str(e)), "details": None},
        }
# ═══════════════════════════════════════════

@router.get("/kb/documents/{doc_id}/chunks")
async def get_doc_chunks(doc_id: str):
    """获取文档的所有 chunks（内容 + 元数据）。"""
    try:
        with get_db_ctx() as db:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
            if not record:
                return {
                    "success": False,
                    "error": {"code": "DOC_NOT_FOUND", "message": f"文档不存在: {doc_id}", "details": None},
                }
            kb_id = record.kb_id

        kb_manager = _get_kb_manager()
        chunks = kb_manager.get_doc_chunks(kb_id, doc_id) if kb_id else []

        # Build response with relevant fields
        chunk_list = []
        for c in chunks:
            meta = c.get("metadata", {})
            # Extract agent trace (may be large — include key summary fields
            # but omit the full rounds detail to keep response size sane).
            agent_trace = meta.get("agent_trace")
            trace_summary = None
            if agent_trace and isinstance(agent_trace, dict):
                trace_summary = {
                    "method": agent_trace.get("method"),
                    "model": agent_trace.get("model"),
                    "num_rounds": agent_trace.get("num_rounds"),
                    "temperature": agent_trace.get("temperature"),
                    "toc_entries": agent_trace.get("toc_entries"),
                    "num_batches": agent_trace.get("num_batches"),
                    "voted_sections": agent_trace.get("voted_sections"),
                    "disputed_sections": agent_trace.get("disputed_sections"),
                    "final_chunks": agent_trace.get("final_chunks"),
                    "total_elapsed_seconds": round(agent_trace.get("total_elapsed_seconds", 0), 2),
                    "rounds": [
                        {
                            "round_num": r.get("round_num"),
                            "sections_found": r.get("sections_found"),
                            "elapsed_seconds": round(r.get("elapsed_seconds", 0), 2),
                            "error": r.get("error"),
                        }
                        for r in agent_trace.get("rounds", [])
                    ],
                }
            chunk_list.append({
                "id": c.get("id", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "content": c.get("content", ""),
                "content_length": len(c.get("content", "")),
                "page_start": meta.get("page_start"),
                "page_end": meta.get("page_end"),
                "section_title": meta.get("section_title", ""),
                "chunk_method": meta.get("chunk_method", ""),
                "chunk_size": meta.get("chunk_size", 0),
                "title": meta.get("title", ""),
                "small_chunk_id": meta.get("small_chunk_id", ""),
                "big_chunk_text": meta.get("big_chunk_text", ""),
                "fingerprint": meta.get("fingerprint", ""),
                # New transparency fields:
                "is_code_block": meta.get("is_code_block", False),
                "has_code_block": meta.get("has_code_block", False),
                "boundary_disputed": meta.get("boundary_disputed", False),
                "section_summary": meta.get("section_summary", ""),
                "section_keywords": meta.get("section_keywords", []),
                "section_confidence": meta.get("section_confidence"),
                "agent_trace": trace_summary,
            })

        return {
            "success": True,
            "data": {
                "doc_id": doc_id,
                "kb_id": kb_id,
                "total_chunks": len(chunk_list),
                "chunks": chunk_list,
            },
        }
    except Exception as e:
        logger.exception("获取文档 chunks 失败")
        return {
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": sanitize_error(str(e)), "details": None},
        }


# ═══════════════════════════════════════════
# GET /api/kb/chunks/{small_chunk_id}
# ═══════════════════════════════════════════

@router.get("/kb/chunks/{small_chunk_id}")
async def get_chunk_by_small_id(small_chunk_id: str):
    """按 small_chunk_id 查询单个 chunk 的完整信息（含 big_chunk_text）。

    small_chunk_id 格式: {doc_id}#s{chunk_index}
    前端点击"查看完整上下文"时调用此接口拉取 big_chunk_text。
    """
    try:
        # Parse doc_id from small_chunk_id: "{doc_id}#s{chunk_index}"
        if "#" not in small_chunk_id:
            return {
                "success": False,
                "error": {"code": "INVALID_ID", "message": "small_chunk_id 格式错误，缺少 '#'", "details": None},
            }
        doc_id = small_chunk_id.split("#", 1)[0]

        # Look up kb_id from KnowledgeDoc record
        with get_db_ctx() as db:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
            if not record:
                return {
                    "success": False,
                    "error": {"code": "DOC_NOT_FOUND", "message": f"文档不存在: {doc_id}", "details": None},
                }
            kb_id = record.kb_id

        kb_manager = _get_kb_manager()
        chunk = kb_manager.get_chunk_by_small_id(kb_id, small_chunk_id)
        if not chunk:
            return {
                "success": False,
                "error": {"code": "CHUNK_NOT_FOUND", "message": f"Chunk 不存在: {small_chunk_id}", "details": None},
            }

        meta = chunk.get("metadata", {})
        return {
            "success": True,
            "data": {
                "id": chunk.get("id", ""),
                "small_chunk_id": small_chunk_id,
                "content": chunk.get("content", ""),
                "content_length": len(chunk.get("content", "")),
                "big_chunk_text": meta.get("big_chunk_text", ""),
                "section_title": meta.get("section_title", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "chunk_size": meta.get("chunk_size", 0),
                "chunk_method": meta.get("chunk_method", ""),
                "page_start": meta.get("page_start"),
                "page_end": meta.get("page_end"),
                "title": meta.get("title", ""),
                "doc_id": doc_id,
                "kb_id": kb_id,
            },
        }
    except Exception as e:
        logger.exception("按 small_chunk_id 查询 chunk 失败")
        return {
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": sanitize_error(str(e)), "details": None},
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
        import ipaddress
        from urllib.parse import urlparse

        # SSRF protection: block private/internal IP ranges
        parsed = urlparse(payload.base_url)
        hostname = parsed.hostname or ""
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return {
                    "success": False,
                    "error": {"code": "SSRF_BLOCKED", "message": "不允许访问内网地址", "details": None},
                }
        except ValueError:
            pass  # Not an IP, it's a domain name — allowed

        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {payload.api_key}"}
            base = payload.base_url.rstrip("/")
            url = base if base.endswith("/models") else base + "/models"
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


# ═══════════════════════════════════════════════
# POST /api/kb/{kb_id}/export — Export KB data as JSON
# ═══════════════════════════════════════════════
@router.post("/kb/{kb_id}/export")
async def kb_export(kb_id: str):
    """Export a KB's ChromaDB data (documents + embeddings + metadatas) as JSON.

    The exported file can be imported into another instance using the same embedding model.
    """
    kb_manager = _get_kb_manager()
    kb = kb_manager.get_kb(kb_id)
    if not kb:
        return {
            "success": False,
            "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
        }
    try:
        export_data = kb_manager.export_kb(kb_id)
        return {"success": True, "data": export_data}
    except Exception as e:
        logger.exception(f"导出知识库失败: {kb_id}")
        return {
            "success": False,
            "error": {"code": "EXPORT_FAILED", "message": sanitize_error(str(e)), "details": None},
        }


# ═══════════════════════════════════════════════
# POST /api/kb/{kb_id}/import — Import KB data from JSON file
# ═══════════════════════════════════════════════
@router.post("/kb/{kb_id}/import")
async def kb_import(
    kb_id: str,
    file: UploadFile = File(...),
):
    """Import a previously exported KB JSON file into an existing KB.

    The target KB should use the same embedding model as the source.
    This directly writes embeddings into ChromaDB without re-embedding.

    The JSON file should be the output of POST /api/kb/{kb_id}/export.
    """
    kb_manager = _get_kb_manager()
    kb = kb_manager.get_kb(kb_id)
    if not kb:
        return {
            "success": False,
            "error": {"code": "KB_NOT_FOUND", "message": f"知识库不存在: {kb_id}", "details": None},
        }

    if not file.filename:
        return {
            "success": False,
            "error": {"code": "INVALID_REQUEST", "message": "未提供文件名", "details": None},
        }

    try:
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            return {
                "success": False,
                "error": {"code": "FILE_TOO_LARGE", "message": f"文件大小超过限制 ({MAX_UPLOAD_SIZE // 1024 // 1024}MB)", "details": None},
            }
        import json
        export_data = json.loads(content.decode("utf-8"))

        # Validate structure
        if "data" not in export_data:
            return {
                "success": False,
                "error": {"code": "INVALID_FORMAT", "message": "文件格式无效：缺少 data 字段", "details": None},
            }

        # Warn if embedding model mismatch
        source_model = export_data.get("embedding_model", "")
        if source_model and kb.embedding_model and source_model != kb.embedding_model:
            logger.warning(
                f"Embedding model mismatch: source={source_model}, target={kb.embedding_model}"
            )

        imported = kb_manager.import_kb(kb_id, export_data)

        # Create KnowledgeDoc records keyed by the SOURCE doc_id stored in
        # each chunk's metadata. This is critical: ChromaDB stores vectors
        # with the original doc_id, so the DB record's doc_id MUST match for
        # delete to actually remove vectors. Previously we generated a new
        # uuid here, which meant imports could never be cleaned up.
        if imported > 0:
            metadatas = export_data.get("data", {}).get("metadatas", []) or []
            source_name = export_data.get("name", "未知")
            chunk_method = export_data.get("chunk_method", "hybrid")

            # Group chunks by source doc_id → (title, count)
            doc_groups: dict[str, dict] = {}
            for meta in metadatas:
                if not isinstance(meta, dict):
                    continue
                src_doc_id = meta.get("doc_id", "")
                if not src_doc_id:
                    continue
                if src_doc_id not in doc_groups:
                    doc_groups[src_doc_id] = {
                        "title": meta.get("title") or f"[导入] {source_name}",
                        "count": 0,
                    }
                doc_groups[src_doc_id]["count"] += 1

            with get_db_ctx() as db:
                for src_doc_id, info in doc_groups.items():
                    # If a record with this doc_id already exists in the
                    # target KB (e.g. re-import), update it instead of
                    # raising a unique constraint violation.
                    existing = db.query(KnowledgeDoc).filter(
                        KnowledgeDoc.doc_id == src_doc_id
                    ).first()
                    if existing:
                        existing.kb_id = kb_id
                        existing.title = info["title"]
                        existing.chunk_count = info["count"]
                        existing.chunk_method_used = chunk_method
                        existing.status = "indexed"
                        existing.error_message = None
                    else:
                        db.add(KnowledgeDoc(
                            doc_id=src_doc_id,
                            kb_id=kb_id,
                            title=info["title"],
                            category="imported",
                            file_type="json",
                            file_size=0,
                            chunk_count=info["count"],
                            chunk_method_used=chunk_method,
                            status="indexed",
                        ))

        return {
            "success": True,
            "data": {
                "imported_chunks": imported,
                "source_kb": export_data.get("name", ""),
                "source_model": source_model,
            },
        }
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": {"code": "INVALID_FORMAT", "message": "文件不是有效的 JSON", "details": None},
        }
    except Exception as e:
        logger.exception(f"导入知识库失败: {kb_id}")
        return {
            "success": False,
            "error": {"code": "IMPORT_FAILED", "message": sanitize_error(str(e)), "details": None},
        }
