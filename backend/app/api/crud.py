"""
会话/消息/设置的 CRUD 路由。
通过 FastAPI 依赖注入获取数据库会话。
所有响应统一 {success, data} 格式（对齐 api-contract.md §2.3）。
"""

import datetime
import math
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.time_utils import to_utc_iso_z, utc_now
from app.api.dependencies import current_user_optional
from app.api.memory_epoch import MEMORY_CLEAR_EPOCH_KEY, stage_memory_clear_epoch
from app.api.session_epoch import (
    CONTEXT_EVENT_PREFIX,
    SESSION_DELETE_EPOCH_PREFIX,
    _session_prefix,
    stage_session_delete_epoch,
)

logger = logging.getLogger(__name__)
from app.db.models import Session as SessionModel
from app.db.models import Message as MessageModel
from app.db.models import Settings as SettingsModel
from app.db.models import CONTEXT_WINDOW_256K

db_router = APIRouter(prefix="/api")

# 会话标题预览长度（首条用户消息截取字符数）
TITLE_PREVIEW_LENGTH = 30


def _ok(data: dict | None = None) -> dict:
    """统一成功响应包装。"""
    return {"success": True, "data": data}


def _fail(code: str, message: str, details: dict | None = None, status_code: int = 400) -> dict:
    """统一失败响应包装（抛 HTTPException，detail 为契约格式）。"""
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message, "details": details}},
    )


# ═══════════════════════════════════════════
# Session CRUD
# ═══════════════════════════════════════════

class SessionCreate(BaseModel):
    title: str = "新对话"
    model: Optional[str] = ""
    project: Optional[str] = ""
    branch_from_session_id: Optional[str] = None
    branch_from_message_id: Optional[str] = None
    context_window: int = CONTEXT_WINDOW_256K
    id: Optional[str] = None


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None
    project: Optional[str] = None
    pinned: Optional[bool] = None
    branch_from_session_id: Optional[str] = None
    branch_from_message_id: Optional[str] = None
    context_window: Optional[int] = None


def _cap_agent_session_context_window(value: int) -> int:
    """Clamp persisted and serialized Agent session windows to the hard cap."""
    from src.agent.context_guard import AGENT_CONTEXT_WINDOW_CAP

    if type(value) is not int:
        raise ValueError("Session context_window must be an integer")
    return min(value, AGENT_CONTEXT_WINDOW_CAP)


def _serialize_session(s: SessionModel) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "model": s.model,
        "project": s.project or "",
        "pinned": s.pinned,
        "msg_count": s.msg_count,
        "branch_from_session_id": s.branch_from_session_id,
        "branch_from_message_id": s.branch_from_message_id,
        "context_window": _cap_agent_session_context_window(s.context_window),
        "version": getattr(s, "version", 0) or 0,
        "created_at": to_utc_iso_z(s.created_at),
        "updated_at": to_utc_iso_z(s.updated_at),
    }


@db_router.get("/sessions")
def list_sessions(offset: int = 0, limit: int = 50, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取所有会话列表，按更新时间倒序（分页）。"""
    sessions = (
        db.query(SessionModel)
        .order_by(SessionModel.updated_at.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )
    return _ok({"sessions": [_serialize_session(s) for s in sessions]})


@db_router.post("/sessions")
def create_session(payload: SessionCreate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """创建新会话。"""
    # 如果前端传了 id 且后端不存在同 id 会话，则使用前端 id（支持本地回退会话补录）
    if payload.id:
        existing = db.query(SessionModel).filter(SessionModel.id == payload.id).first()
        if existing:
            return _ok(_serialize_session(existing))
        sid = payload.id
    else:
        sid = f"s{uuid.uuid4().hex[:8]}"
    session = SessionModel(
        id=sid,
        title=payload.title,
        model=payload.model,
        project=payload.project,
        branch_from_session_id=payload.branch_from_session_id,
        branch_from_message_id=payload.branch_from_message_id,
        context_window=_cap_agent_session_context_window(payload.context_window),
    )
    logger.info("创建会话: id=%s title=%s", sid, payload.title)
    db.add(session)
    db.flush()  # ensure session.id is available for FK

    # Branch: copy messages from source session up to branch point
    if payload.branch_from_session_id:
        source_session = db.query(SessionModel).filter(
            SessionModel.id == payload.branch_from_session_id
        ).first()
        if source_session:
            for seq, msg in enumerate(source_session.messages, start=1):
                new_msg = MessageModel(
                    id=f"m{uuid.uuid4().hex[:8]}",
                    session_id=sid,
                    role=msg.role,
                    content=msg.content,
                    sources=msg.sources,
                    tool_calls=msg.tool_calls,
                    seq=seq,
                )
                db.add(new_msg)
                session.msg_count += 1
                # Stop after copying the branch point message (inclusive)
                if payload.branch_from_message_id and msg.id == payload.branch_from_message_id:
                    break

    db.commit()
    return _ok(_serialize_session(session))


@db_router.get("/sessions/{session_id}")
def get_session(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取单个会话（含消息）。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
    return _ok({
        **_serialize_session(s),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources or [],
                "tool_calls": m.tool_calls or [],
                "activity": m.activity,
                "seq": m.seq,
                "status": m.status,
                "tool_call_id": m.tool_call_id,
                "tool_name": m.tool_name,
                "created_at": to_utc_iso_z(m.created_at),
            }
            for m in s.messages
        ],
    })


@db_router.put("/sessions/{session_id}")
def update_session(session_id: str, payload: SessionUpdate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """更新会话属性。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        logger.warning("会话更新失败: id=%s 不存在", session_id)
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
    if payload.title is not None:
        s.title = payload.title
    if payload.model is not None:
        s.model = payload.model
    if payload.project is not None:
        s.project = payload.project
    if payload.pinned is not None:
        s.pinned = payload.pinned
    if payload.branch_from_session_id is not None:
        s.branch_from_session_id = payload.branch_from_session_id
    if payload.branch_from_message_id is not None:
        s.branch_from_message_id = payload.branch_from_message_id
    if payload.context_window is not None:
        s.context_window = _cap_agent_session_context_window(payload.context_window)
    logger.info("会话已更新: id=%s", session_id)
    s.version = (getattr(s, "version", 0) or 0) + 1
    s.updated_at = utc_now()
    db.commit()
    return _ok({"id": session_id, "updated": True})


# Fields that PATCH may update on a session (must stay in sync with SessionUpdate).
_SESSION_UPDATABLE_FIELDS = (
    "title", "model", "project", "pinned",
    "branch_from_session_id", "branch_from_message_id", "context_window",
)


def _apply_session_update(s: SessionModel, payload: SessionUpdate) -> None:
    """Apply non-None fields from SessionUpdate payload to session model."""
    for field in _SESSION_UPDATABLE_FIELDS:
        val = getattr(payload, field)
        if val is not None:
            if field == "context_window":
                val = _cap_agent_session_context_window(val)
            setattr(s, field, val)


@db_router.patch("/sessions/{session_id}")
def patch_session(session_id: str, payload: SessionUpdate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """Partial update session fields (e.g., context_window). Returns full session."""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        logger.warning("会话 PATCH 失败: id=%s 不存在", session_id)
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
    _apply_session_update(s, payload)
    s.version = (getattr(s, "version", 0) or 0) + 1
    s.updated_at = utc_now()
    db.commit()
    logger.info("会话已 PATCH: id=%s", session_id)
    return _ok(_serialize_session(s))


@db_router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """删除会话及其所有消息（级联删除）。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        logger.warning("会话删除失败: id=%s 不存在", session_id)
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
    logger.info("会话已删除: id=%s", session_id)
    stage_session_delete_epoch(db, session_id, datetime.datetime.utcnow())
    db.query(SettingsModel).filter(
        SettingsModel.key.startswith(_session_prefix(session_id), autoescape=True),
    ).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return _ok({"id": session_id, "deleted": True})

# ═══════════════════════════════════════════
# Message CRUD
# ═══════════════════════════════════════════

class MessageCreate(BaseModel):
    # Optional client ID lets the frontend safely retry a write after a timeout.
    # Older callers may omit it and keep the existing server-generated behavior.
    id: Optional[str] = None
    role: str  # "user" | "assistant"
    content: str
    sources: Optional[list] = None
    tool_calls: Optional[list] = None
    activity: Optional[dict] = None
    client_key: Optional[str] = None
    status: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None


@db_router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取会话的所有消息。"""
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    msgs = db.query(MessageModel).filter(
        MessageModel.session_id == session_id
    ).order_by(MessageModel.created_at, MessageModel.seq).all()
    return _ok({
        "version": (getattr(session, "version", 0) or 0) if session else 0,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources or [],
                "tool_calls": m.tool_calls or [],
                "activity": m.activity,
                "seq": m.seq,
                "status": m.status,
                "tool_call_id": m.tool_call_id,
                "tool_name": m.tool_name,
                "created_at": to_utc_iso_z(m.created_at),
                "updated_at": to_utc_iso_z(m.updated_at),
            }
            for m in msgs
        ],
    })


@db_router.post("/sessions/{session_id}/messages")
def create_message(session_id: str, payload: MessageCreate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """在会话中添加或幂等更新一条消息。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)

    msg = _find_existing_message(db, session_id, payload)
    if msg is not None and msg.session_id != session_id:
        raise _fail("CONFLICT", "消息 ID 已属于其他会话", status_code=409)

    msg = _apply_message_payload(db, session_id, s, payload, msg)
    logger.info("消息已保存: session=%s role=%s id=%s", session_id, payload.role, msg.id)
    db.commit()
    return _ok({
        "id": msg.id,
        "role": payload.role,
        "content": payload.content,
        "seq": msg.seq,
        "status": msg.status,
        "created_at": to_utc_iso_z(msg.created_at),
    })


def _find_existing_message(db: DBSession, session_id: str, payload: MessageCreate) -> MessageModel | None:
    if payload.id:
        return db.query(MessageModel).filter(MessageModel.id == payload.id).first()
    if payload.client_key:
        return db.query(MessageModel).filter(
            MessageModel.session_id == session_id,
            MessageModel.client_key == payload.client_key,
        ).first()
    return None


def _apply_message_payload(db: DBSession, session_id: str, s: SessionModel, payload: MessageCreate, msg: MessageModel | None) -> MessageModel:
    is_new = msg is None
    if is_new:
        msg = MessageModel(
            id=payload.id or f"m{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            seq=_next_message_seq(db, session_id),
        )
        s.msg_count = (s.msg_count or 0) + 1
        db.add(msg)
    msg.role = payload.role
    msg.content = payload.content
    msg.sources = payload.sources
    msg.tool_calls = payload.tool_calls
    msg.activity = payload.activity
    msg.status = payload.status or msg.status or "complete"
    if payload.client_key is not None:
        msg.client_key = payload.client_key
    if payload.tool_call_id is not None:
        msg.tool_call_id = payload.tool_call_id
    if payload.tool_name is not None:
        msg.tool_name = payload.tool_name
    msg.updated_at = utc_now()
    s.version = (getattr(s, "version", 0) or 0) + 1
    s.updated_at = utc_now()
    if is_new and payload.role == "user" and not s.title.startswith("对话"):
        # 用第一条用户消息做标题预览
        s.title = payload.content[:TITLE_PREVIEW_LENGTH] + ("..." if len(payload.content) > TITLE_PREVIEW_LENGTH else "")
    return msg


def _next_message_seq(db: DBSession, session_id: str) -> int:
    current = db.query(func.max(MessageModel.seq)).filter(
        MessageModel.session_id == session_id
    ).scalar()
    return (current or 0) + 1


@db_router.delete("/sessions/{session_id}/messages")
def truncate_messages(session_id: str, keep_count: int = 0, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """截断会话消息：保留前 keep_count 条，删除其余。keep_count=0 清空。
    用于前端 retry/editAndResend 时同步删除后端旧消息，防止刷新后重复。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
    msgs = db.query(MessageModel).filter(
        MessageModel.session_id == session_id
    ).order_by(MessageModel.created_at, MessageModel.seq).all()
    keep_count = max(0, min(keep_count, len(msgs)))
    to_delete = msgs[keep_count:]
    for m in to_delete:
        db.delete(m)
    s.msg_count = keep_count
    s.version = (getattr(s, "version", 0) or 0) + 1
    s.updated_at = utc_now()
    db.commit()
    logger.info("消息已截断: session=%s kept=%d deleted=%d", session_id, keep_count, len(to_delete))
    return _ok({"kept": keep_count, "deleted": len(to_delete)})


# ═══════════════════════════════════════════
# Settings CRUD
# ═══════════════════════════════════════════

# 设置键白名单
ALLOWED_SETTINGS_KEYS = {
    "activeProvider", "model", "visionModel", "imageModel",
    "temperature", "topK", "relevanceThreshold", "maxTokens", "systemPrompt", "longTermMemory",
    "chatFontSize", "themeMode", "lang", "permissionMode",
}


@db_router.get("/settings")
def get_settings(db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取所有设置键值对。"""
    rows = db.query(SettingsModel).all()
    return _ok({"settings": {
        r.key: r.value for r in rows
        if r.key != MEMORY_CLEAR_EPOCH_KEY
        and not r.key.startswith((SESSION_DELETE_EPOCH_PREFIX, CONTEXT_EVENT_PREFIX))
    }})


@db_router.put("/settings")
def update_settings(payload: dict, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """批量保存设置。只允许白名单中的键。"""
    # 白名单校验
    invalid_keys = set(payload.keys()) - ALLOWED_SETTINGS_KEYS
    if invalid_keys:
        raise _fail(
            "INVALID_SETTINGS_KEY",
            f"不允许的设置键: {', '.join(sorted(invalid_keys))}",
        )
    # Validate before changing any rows; memory is explicit user-authored text.
    if "longTermMemory" in payload:
        memory = payload["longTermMemory"]
        if not isinstance(memory, str) or len(memory) > 4000:
            raise _fail(
                "INVALID_LONG_TERM_MEMORY",
                "Long-term memory must be a string of at most 4000 characters.",
                status_code=422,
            )
    for key in ("topK", "relevanceThreshold"):
        if key not in payload:
            continue
        raw_value = payload[key]
        try:
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, str)):
                raise ValueError
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            raise _fail(
                "INVALID_RETRIEVAL_SETTING",
                f"{key} must be a finite number within its supported range.",
                status_code=422,
            )
        if not math.isfinite(numeric_value):
            raise _fail(
                "INVALID_RETRIEVAL_SETTING",
                f"{key} must be a finite number within its supported range.",
                status_code=422,
            )
        if key == "topK" and (not numeric_value.is_integer() or not 1 <= numeric_value <= 20):
            raise _fail(
                "INVALID_RETRIEVAL_SETTING",
                "topK must be an integer from 1 through 20.",
                status_code=422,
            )
        if key == "relevanceThreshold" and not 0 <= numeric_value <= 100:
            raise _fail(
                "INVALID_RETRIEVAL_SETTING",
                "relevanceThreshold must be from 0 through 100.",
                status_code=422,
            )
    now = utc_now()
    if payload.get("longTermMemory") == "":
        stage_memory_clear_epoch(db, now)
    for key, value in payload.items():
        existing = db.query(SettingsModel).filter(SettingsModel.key == key).first()
        val_str = str(value) if value is not None else None
        if existing:
            existing.value = val_str
            existing.updated_at = now
        else:
            db.add(SettingsModel(key=key, value=val_str, updated_at=now))
    db.commit()
    return _ok({"updated": True})
