"""
会话/消息/设置的 CRUD 路由。
通过 FastAPI 依赖注入获取数据库会话。
所有响应统一 {success, data} 格式（对齐 api-contract.md §2.3）。
"""

import datetime
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.api.dependencies import current_user

logger = logging.getLogger(__name__)
from app.db.models import Session as SessionModel
from app.db.models import Message as MessageModel
from app.db.models import Settings as SettingsModel

db_router = APIRouter(prefix="/api")


def _ok(data) -> dict:
    """统一成功响应包装。"""
    return {"success": True, "data": data}


def _fail(code: str, message: str, details=None, status_code: int = 400) -> HTTPException:
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


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None
    project: Optional[str] = None
    pinned: Optional[bool] = None
    branch_from_session_id: Optional[str] = None
    branch_from_message_id: Optional[str] = None


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
        "created_at": s.created_at.isoformat() if s.created_at else "",
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    }


@db_router.get("/sessions")
def list_sessions(offset: int = 0, limit: int = 50, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
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
def create_session(payload: SessionCreate, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
    """创建新会话。"""
    sid = f"s{uuid.uuid4().hex[:8]}"
    session = SessionModel(
        id=sid,
        title=payload.title,
        model=payload.model,
        project=payload.project,
        branch_from_session_id=payload.branch_from_session_id,
        branch_from_message_id=payload.branch_from_message_id,
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
            for msg in source_session.messages:
                # If branch_from_message_id is set, only copy up to that message
                if payload.branch_from_message_id and msg.id != payload.branch_from_message_id:
                    continue
                new_msg = MessageModel(
                    id=f"m{uuid.uuid4().hex[:8]}",
                    session_id=sid,
                    role=msg.role,
                    content=msg.content,
                    sources=msg.sources,
                    tool_calls=msg.tool_calls,
                )
                db.add(new_msg)
                session.msg_count += 1
                # Stop after copying the branch point message
                if payload.branch_from_message_id and msg.id == payload.branch_from_message_id:
                    break

    db.commit()
    return _ok(_serialize_session(session))


@db_router.get("/sessions/{session_id}")
def get_session(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
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
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in s.messages
        ],
    })


@db_router.put("/sessions/{session_id}")
def update_session(session_id: str, payload: SessionUpdate, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
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
    logger.info("会话已更新: id=%s", session_id)
    s.updated_at = datetime.datetime.utcnow()
    db.commit()
    return _ok({"id": session_id, "updated": True})


@db_router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
    """删除会话及其所有消息（级联删除）。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        logger.warning("会话删除失败: id=%s 不存在", session_id)
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
    logger.info("会话已删除: id=%s", session_id)
    db.delete(s)
    db.commit()
    return _ok({"id": session_id, "deleted": True})

# ═══════════════════════════════════════════
# Message CRUD
# ═══════════════════════════════════════════

class MessageCreate(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    sources: Optional[list] = None
    tool_calls: Optional[list] = None


@db_router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
    """获取会话的所有消息。"""
    msgs = db.query(MessageModel).filter(
        MessageModel.session_id == session_id
    ).order_by(MessageModel.created_at).all()
    return _ok({"messages": [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "sources": m.sources or [],
            "tool_calls": m.tool_calls or [],
            "created_at": m.created_at.isoformat() if m.created_at else "",
        }
        for m in msgs
    ]})


@db_router.post("/sessions/{session_id}/messages")
def create_message(session_id: str, payload: MessageCreate, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
    """在会话中添加一条消息。"""
    s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not s:
        raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)

    mid = f"m{uuid.uuid4().hex[:8]}"
    msg = MessageModel(
        id=mid,
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        sources=payload.sources,
        tool_calls=payload.tool_calls,
    )
    s.msg_count = (s.msg_count or 0) + 1
    s.updated_at = datetime.datetime.utcnow()
    if payload.role == "user" and not s.title.startswith("对话"):
        # 用第一条用户消息做标题预览
        s.title = payload.content[:30] + ("..." if len(payload.content) > 30 else "")

    logger.info("消息已保存: session=%s role=%s id=%s", session_id, payload.role, mid)
    db.add(msg)
    db.commit()
    return _ok({
        "id": mid,
        "role": payload.role,
        "content": payload.content,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    })


# ═══════════════════════════════════════════
# Settings CRUD
# ═══════════════════════════════════════════

# 设置键白名单
ALLOWED_SETTINGS_KEYS = {
    "activeProvider", "model", "visionModel", "imageModel",
    "temperature", "topK", "maxTokens", "systemPrompt", "longTermMemory",
    "chatFontSize", "themeMode", "lang", "permissionMode",
    "sandboxEnabled", "sandboxImage",
}


@db_router.get("/settings")
def get_settings(db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
    """获取所有设置键值对。"""
    rows = db.query(SettingsModel).all()
    return _ok({"settings": {r.key: r.value for r in rows}})


@db_router.put("/settings")
def update_settings(payload: dict, db: DBSession = Depends(get_db), user: dict = Depends(current_user)):
    """批量保存设置。只允许白名单中的键。"""
    # 白名单校验
    invalid_keys = set(payload.keys()) - ALLOWED_SETTINGS_KEYS
    if invalid_keys:
        raise _fail(
            "INVALID_SETTINGS_KEY",
            f"不允许的设置键: {', '.join(sorted(invalid_keys))}",
        )
    now = datetime.datetime.utcnow()
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
