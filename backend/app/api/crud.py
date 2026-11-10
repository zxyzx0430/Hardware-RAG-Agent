"""
会话/消息/设置的 CRUD 路由。
通过 FastAPI 依赖注入获取数据库会话。
所有响应统一 {success, data} 格式（对齐 api-contract.md §2.3）。
"""

import datetime
import hashlib
import json
import math
import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.time_utils import to_utc_iso_z, utc_now
from app.api.dependencies import current_user_optional
from app.api.skill_activity import project_skill_activity
from app.api.memory_epoch import MEMORY_CLEAR_EPOCH_KEY, stage_memory_clear_epoch
from app.api.session_epoch import (
    SESSION_DELETE_EPOCH_PREFIX,
    read_session_delete_epoch,
    stage_session_delete_epoch,
)
from app.api.context_events import CONTEXT_EVENT_PREFIX, ContextEventScope, _session_prefix
from app.api.compaction_archive import bind_compaction_archive
from app.api.tool_observations import TOOL_OBSERVATION_SETTINGS_PREFIX
from app.api.rolling_history import (
    MAX_BODY_EVENT_METADATA_UTF8_BYTES,
    RollingHistoryBodyEventInput,
    RollingHistorySourceMessageWriteReceipt,
    rolling_history_session_prefix,
    validate_rolling_history_body_event_content,
)
from app.api.rolling_history_owner_scope import (
    MaterializedRows,
    RollingHistoryOwnerScopeError,
    RollingHistorySqlHandle,
    ScopeOrigin,
    get_rolling_history_owner_identity,
    run_chat_append_owner_write_operation,
)
from app.api.rolling_revision_store import (
    RollingHistoryV2ActiveBranchPointerResult,
    RollingHistoryV2Conflict,
    RollingHistoryV2MigrationRequired,
    RollingHistoryV2StoreResult,
    append_rolling_history_v2_body_events_in_transaction,
    create_rolling_history_v2_active_branch_pointer,
    create_rolling_history_v2_root,
    read_rolling_history_v2_active_branch_pointer,
    rolling_history_v2_owner_scope,
)
from app.api.session_owner import (
    bind_new_session_owner,
    principal_digest_for_user,
    require_session_owner,
    retire_session_owner,
)

logger = logging.getLogger(__name__)
from app.db.models import Session as SessionModel
from app.db.models import Message as MessageModel
from app.db.models import Settings as SettingsModel
from app.db.models import SessionOwnerBinding
from app.db.models import SessionIncarnation
from app.db.models import CONTEXT_WINDOW_256K
from app.db.models import Feedback as FeedbackModel
from app.db.models import TokenUsage as TokenUsageModel
from app.db.models import ToolAudit as ToolAuditModel
from app.db.models import Todo as TodoModel
from app.db.models import Plan as PlanModel
from app.db.models import SessionTombstone
from app.api.tool_observations import purge_session_observations

db_router = APIRouter(prefix="/api")

# 会话标题预览长度（首条用户消息截取字符数）
TITLE_PREVIEW_LENGTH = 30

# Titles that mean "the user has not named this session yet"; auto-titling
# may replace only these.
_PLACEHOLDER_TITLES: frozenset[str] = frozenset({"", "新对话"})
# The frontend names fresh sessions "新对话 · HH:MM"; those still count as
# placeholders so the first real user message can name the session.
_PLACEHOLDER_TITLE_PREFIXES: tuple[str, ...] = ("对话", "新对话 ·")

# Synthetic message sent after plan approval. It must never become a session
# title; the title of the real task has to survive plan execution.
PLAN_APPROVAL_USER_MESSAGE = "按照已批准的计划开始执行。"


def _is_placeholder_title(title: str | None) -> bool:
    """True when a session still has its default (never user-named) title."""
    if title is None:
        return True
    stripped = title.strip()
    return (
        not stripped
        or stripped in _PLACEHOLDER_TITLES
        or stripped.startswith(_PLACEHOLDER_TITLE_PREFIXES)
    )


def _title_from_user_message(content: str) -> str:
    """Build the title preview from a user message."""
    return content[:TITLE_PREVIEW_LENGTH] + (
        "..." if len(content) > TITLE_PREVIEW_LENGTH else ""
    )


def _should_set_title_from_message(title: str | None, content: str) -> bool:
    """Only name a session from its first real user task.

    A meaningful existing title is never overwritten by a later user message,
    and the synthetic plan-approval message is not a task title.
    """
    return (
        _is_placeholder_title(title)
        and content.strip() != PLAN_APPROVAL_USER_MESSAGE
    )


def _ok(data: dict | None = None) -> dict:
    """统一成功响应包装。"""
    return {"success": True, "data": data}


def _fail(code: str, message: str, details: dict | None = None, status_code: int = 400) -> dict:
    """统一失败响应包装（抛 HTTPException，detail 为契约格式）。"""
    return HTTPException(
        status_code=status_code,
        detail={"success": False, "error": {"code": code, "message": message, "details": details}},
    )


def _same_json_payload(left, right) -> bool:
    """Compare JSON payloads independent of object-key order, preserving JSON types."""
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _message_payload_matches(
    message: MessageModel,
    *,
    role: str,
    content: str,
    sources: list | None,
    tool_calls: list | None,
    activity: dict | None,
) -> bool:
    return (
        message.role == role
        and message.content == content
        and _same_json_payload(message.sources, sources)
        and _same_json_payload(message.tool_calls, tool_calls)
        and _same_json_payload(message.activity, activity)
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


def _serialize_session(
    s: SessionModel,
    db: DBSession | None = None,
    plan_mode_requested: bool | None = None,
) -> dict:
    if plan_mode_requested is None:
        try:
            from app.db import agent_mode_store

            plan_mode_requested = (
                agent_mode_store.plan_mode_requested_in_db(db, s.id)
                if db is not None
                else agent_mode_store.plan_mode_requested(s.id)
            )
        except Exception:
            logger.warning("agent plan mode flag unavailable for %s", s.id)
            plan_mode_requested = False
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
        "workspace_root": getattr(s, "workspace_root", None),
        "version": getattr(s, "version", 0) or 0,
        "plan_mode_requested": bool(plan_mode_requested),
        "created_at": to_utc_iso_z(s.created_at),
        "updated_at": to_utc_iso_z(s.updated_at),
    }


def _begin_session_write(db: DBSession) -> None:
    """Acquire SQLite's write lock before this request performs any ORM query."""
    if db.in_transaction():
        raise RuntimeError("Session transaction started before the session write lock")
    if db.get_bind().dialect.name != "sqlite":
        raise RuntimeError("Session owner lifecycle requires SQLite BEGIN IMMEDIATE")
    db.execute(text("BEGIN IMMEDIATE"))


def _begin_session_read_snapshot(db: DBSession) -> None:
    """Start a SQLite read snapshot before this request performs any ORM query."""
    if db.in_transaction():
        raise RuntimeError("Session transaction started before the session read snapshot")
    if db.get_bind().dialect.name != "sqlite":
        raise RuntimeError("Session owner reads require a SQLite read snapshot")
    db.execute(text("BEGIN"))


def _require_session_owner_if_bound(db: DBSession, session_id: str, user: dict) -> None:
    """Preserve shared legacy sessions while enforcing an existing owner binding."""
    binding = db.query(SessionOwnerBinding.session_id).filter(
        SessionOwnerBinding.session_id == session_id
    ).first()
    if binding is None:
        return

    try:
        require_session_owner(db, session_id, user)
    except PermissionError as exc:
        raise _fail("FORBIDDEN", "无权访问该会话", status_code=403) from exc


def _require_active_session_incarnation(
    db: DBSession,
    session_id: str,
    user: dict,
    expected_incarnation_id: str | None = None,
) -> str:
    """Require the caller's active owner binding and optionally its incarnation."""
    try:
        binding = require_session_owner(db, session_id, user)
    except PermissionError as exc:
        raise _fail(
            "SESSION_INCARNATION_CONFLICT",
            "会话生命周期已变化或不可用，请重新获取会话状态",
            status_code=409,
        ) from exc

    incarnation_id = binding.incarnation_id
    if expected_incarnation_id is not None and expected_incarnation_id != incarnation_id:
        raise _fail(
            "SESSION_INCARNATION_CONFLICT",
            "会话生命周期已变化，请重新获取会话状态",
            status_code=409,
        )
    return incarnation_id


def _owner_history_v2_bootstrap_identity(
    session_id: str,
    binding: SessionOwnerBinding,
) -> tuple[ContextEventScope, str]:
    """Build a stable bootstrap identity from the verified owner lifecycle."""
    delete_epoch = read_session_delete_epoch(session_id)
    if delete_epoch is None:
        raise ValueError("Session deletion epoch is unavailable")
    identity = json.dumps(
        {
            "domain": "owner-history-v2-empty-root-bootstrap-v1",
            "session_id": session_id,
            "principal_hash": binding.owner_principal_hash,
            "incarnation_id": binding.incarnation_id,
            "delete_epoch": delete_epoch,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return (
        ContextEventScope(
            session_id=session_id,
            request_id=digest[:32],
            principal_hash=binding.owner_principal_hash,
            session_epoch=delete_epoch,
        ),
        f"owner-history-v2-empty-root-bootstrap-v1:{digest}",
    )


def _confirmed_owner_history_v2_branch(
    result: RollingHistoryV2ActiveBranchPointerResult,
) -> str | None:
    """Return an active branch only from a structurally complete readback."""
    if (type(result) is not RollingHistoryV2ActiveBranchPointerResult
            or result.outcome != "readback_confirmed"
            or result.pointer is None):
        raise ValueError("Rolling history active pointer readback is unavailable")

    pointer = result.pointer
    if pointer.state == "unset":
        if (pointer.pointer_revision != 0 or pointer.branch_id is not None
                or pointer.operation_id is not None or result.branch is not None
                or result.head is not None):
            raise ValueError("Rolling history unset pointer is inconsistent")
        return None

    if (pointer.state != "active" or type(pointer.branch_id) is not str
            or not pointer.branch_id):
        raise ValueError("Rolling history active pointer is invalid")
    branch_id = pointer.branch_id
    head = getattr(result.head, "head", None)
    if (result.branch is None
            or getattr(result.branch, "branch_id", None) != branch_id
            or getattr(head, "branch_id", None) != branch_id):
        raise ValueError("Rolling history active branch readback is inconsistent")
    return branch_id


def _ensure_owner_history_v2_ready(
    session_id: str,
    binding: SessionOwnerBinding,
    user: dict,
    message_count: int,
) -> bool:
    """Initialize only an empty owner history; preserve populated legacy rows."""
    try:
        archive_scope, idempotency_key = _owner_history_v2_bootstrap_identity(
            session_id, binding,
        )
        with bind_compaction_archive(archive_scope):
            with rolling_history_v2_owner_scope(user, ScopeOrigin.COMPACTION):
                owner_identity = get_rolling_history_owner_identity()
                if (owner_identity.session_id != session_id
                        or owner_identity.principal_hash != binding.owner_principal_hash
                        or owner_identity.incarnation_id != binding.incarnation_id
                        or owner_identity.delete_epoch != archive_scope.session_epoch):
                    raise ValueError("Session owner lifecycle changed during bootstrap")
                pointer_read = read_rolling_history_v2_active_branch_pointer()
                active_branch_id = _confirmed_owner_history_v2_branch(pointer_read)
                if active_branch_id is not None:
                    return True
                if type(message_count) is not int or message_count < 0:
                    raise ValueError("Session message count is unavailable")
                if message_count > 0:
                    return False

                root = create_rolling_history_v2_root(
                    idempotency_key=idempotency_key,
                )
                if type(root) is not RollingHistoryV2StoreResult:
                    raise ValueError("Rolling history empty root is unavailable")
                root_branch_id = getattr(root.branch, "branch_id", None)
                root_head = getattr(root.head, "head", None)
                if (type(root_branch_id) is not str or not root_branch_id
                        or getattr(root_head, "branch_id", None) != root_branch_id):
                    raise ValueError("Rolling history empty root readback is inconsistent")

                # The store performs the unset-pointer and bound-head CAS. Its
                # return can be unknown after a committed write, so always make
                # a separate public read before declaring owner_v1 ready.
                try:
                    create_rolling_history_v2_active_branch_pointer(
                        expected_pointer=pointer_read.pointer,
                        target_expected_head=root.head,
                    )
                except Exception:
                    # A lost response is recoverable only if the independent
                    # owner-bound read below confirms this exact root branch.
                    pass
                pointer_readback = read_rolling_history_v2_active_branch_pointer()
                confirmed_branch_id = _confirmed_owner_history_v2_branch(
                    pointer_readback,
                )
                if confirmed_branch_id != root_branch_id:
                    raise ValueError("Rolling history active pointer was not confirmed")
                return True
    except Exception as exc:
        logger.warning("Owner rolling-history bootstrap failed closed: session=%s", session_id)
        raise _fail(
            "MESSAGE_CONFLICT",
            "会话历史状态不可用，请重新获取会话状态",
            status_code=409,
        ) from exc


def _session_listing_access_filter(db: DBSession, user: dict):
    """Build a SQL filter that runs before session ordering and pagination."""
    unbound = ~db.query(SessionOwnerBinding.session_id).filter(
        SessionOwnerBinding.session_id == SessionModel.id
    ).exists()
    if user.get("anonymous") is not False:
        return unbound

    try:
        owner_digest = principal_digest_for_user(user)
    except PermissionError as exc:
        raise _fail("FORBIDDEN", "无权列出会话", status_code=403) from exc

    active_incarnations = db.query(SessionIncarnation.id).filter(
        SessionIncarnation.retired_at.is_(None),
        func.length(SessionIncarnation.id) == 32,
        SessionIncarnation.id.op("GLOB")("[0-9a-f]" * 32),
    )
    owned_active = db.query(SessionOwnerBinding.session_id).filter(
        SessionOwnerBinding.session_id == SessionModel.id,
        SessionOwnerBinding.owner_principal_hash == owner_digest,
        SessionOwnerBinding.incarnation_id.in_(active_incarnations),
    ).exists()
    return or_(unbound, owned_active)


@db_router.get("/sessions")
def list_sessions(offset: int = 0, limit: int = 50, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取所有会话列表，按更新时间倒序（分页）。"""
    sessions = (
        db.query(SessionModel)
        .filter(_session_listing_access_filter(db, user))
        .order_by(SessionModel.updated_at.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )
    from app.db import agent_mode_store

    flags = agent_mode_store.plan_mode_requested_flags(
        [s.id for s in sessions], db=db,
    )
    return _ok({
        "sessions": [
            _serialize_session(s, db=db, plan_mode_requested=flags.get(s.id, False))
            for s in sessions
        ],
    })


@db_router.post("/sessions")
def create_session(payload: SessionCreate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """创建新会话。"""
    if user is None:
        user = {"anonymous": True}
    try:
        _begin_session_write(db)

        # If the client supplied an ID, preserve the existing retry behavior.
        # The write lock makes this check and any new owner binding serializable.
        if payload.id:
            if _session_is_tombstoned(db, payload.id):
                raise _fail("SESSION_DELETED", f"会话已删除: {payload.id}", status_code=410)
            existing = db.query(SessionModel).filter(SessionModel.id == payload.id).first()
            if existing:
                existing_binding = db.query(SessionOwnerBinding).filter(
                    SessionOwnerBinding.session_id == payload.id
                ).first()
                if existing_binding is not None:
                    try:
                        require_session_owner(db, payload.id, user)
                    except PermissionError as exc:
                        raise _fail("FORBIDDEN", "无权访问该会话", status_code=403) from exc
                result = _ok(_serialize_session(existing, db=db))
                db.rollback()
                return result
            sid = payload.id
        else:
            sid = f"s{uuid.uuid4().hex[:8]}"

        source_session = None
        if payload.branch_from_session_id:
            if user.get("anonymous") is False:
                source_exists = db.query(SessionModel.id).filter(
                    SessionModel.id == payload.branch_from_session_id
                ).first()
                if source_exists is None:
                    raise _fail("NOT_FOUND", "分支源会话不存在", status_code=404)

                source_binding = db.query(SessionOwnerBinding).filter(
                    SessionOwnerBinding.session_id == payload.branch_from_session_id
                ).first()
                if source_binding is None:
                    raise _fail("FORBIDDEN", "未绑定的会话不能作为已认证分支来源", status_code=403)
                try:
                    require_session_owner(db, payload.branch_from_session_id, user)
                except PermissionError as exc:
                    raise _fail("FORBIDDEN", "无权从该会话创建分支", status_code=403) from exc

                source_session = db.query(SessionModel).filter(
                    SessionModel.id == payload.branch_from_session_id
                ).first()
                if source_session is None:
                    raise _fail("NOT_FOUND", "分支源会话不存在", status_code=404)
            else:
                # Preserve anonymous branching from unbound legacy sessions.
                # A bound source still requires owner validation and therefore
                # remains unavailable to anonymous callers.
                source_binding = db.query(SessionOwnerBinding).filter(
                    SessionOwnerBinding.session_id == payload.branch_from_session_id
                ).first()
                if source_binding is not None:
                    try:
                        require_session_owner(db, payload.branch_from_session_id, user)
                    except PermissionError as exc:
                        raise _fail("FORBIDDEN", "无权从该会话创建分支", status_code=403) from exc
                source_session = db.query(SessionModel).filter(
                    SessionModel.id == payload.branch_from_session_id
                ).first()

        session = SessionModel(
            id=sid,
            title=payload.title,
            model=payload.model,
            project=payload.project,
            msg_count=0,
            message_revision=0,
            branch_from_session_id=payload.branch_from_session_id,
            branch_from_message_id=payload.branch_from_message_id,
            context_window=_cap_agent_session_context_window(payload.context_window),
        )
        logger.info("创建会话: id=%s title=%s", sid, payload.title)
        db.add(session)

        if user.get("anonymous") is False:
            try:
                bind_new_session_owner(db, session, user, datetime.datetime.utcnow())
            except PermissionError as exc:
                raise _fail("CONFLICT", "会话 ID 已存在或不可用", status_code=409) from exc
        else:
            db.flush([session])

        copied_message_count = 0
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
                copied_message_count += 1
                # Stop after copying the branch point message (inclusive)
                if payload.branch_from_message_id and msg.id == payload.branch_from_message_id:
                    break

        if copied_message_count:
            # The copied rows and the new session revision commit atomically.
            session.message_revision += 1

        result = _ok(_serialize_session(session, db=db))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@db_router.get("/sessions/{session_id}")
def get_session(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取单个会话（含消息）。"""
    try:
        _begin_session_read_snapshot(db)
        _require_session_owner_if_bound(db, session_id, user)
        s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not s:
            raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
        return _ok({
            **_serialize_session(s, db=db),
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "sources": m.sources or [],
                    "tool_calls": m.tool_calls or [],
                    "activity": project_skill_activity(m.activity),
                    "created_at": m.created_at.isoformat() if m.created_at else "",
                }
                for m in s.messages
            ],
        })
    except Exception:
        db.rollback()
        raise


@db_router.get("/sessions/{session_id}/lifecycle")
def get_session_lifecycle(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    """Return lifecycle identity only to the current authenticated session owner."""
    try:
        _begin_session_read_snapshot(db)
        incarnation_id = _require_active_session_incarnation(db, session_id, user)
        return _ok({"session_id": session_id, "incarnation_id": incarnation_id})
    except Exception:
        db.rollback()
        raise


@db_router.get("/sessions/{session_id}/history-mode")
def get_session_history_mode(
    session_id: str,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    """Classify a session's history mode from one SQLite read snapshot."""
    try:
        _begin_session_read_snapshot(db)
        session_exists = db.query(SessionModel.id).filter(
            SessionModel.id == session_id
        ).first()
        if session_exists is None:
            raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)

        binding_exists = db.query(SessionOwnerBinding.session_id).filter(
            SessionOwnerBinding.session_id == session_id
        ).first()
        if binding_exists is None:
            return _ok({"session_id": session_id, "mode": "legacy"})

        try:
            binding = require_session_owner(db, session_id, user)
        except PermissionError as exc:
            raise _fail("FORBIDDEN", "无权访问该会话", status_code=403) from exc

        message_count = db.query(func.count(MessageModel.id)).filter(
            MessageModel.session_id == session_id
        ).scalar()
        if type(message_count) is not int or message_count < 0:
            raise _fail(
                "MESSAGE_CONFLICT",
                "会话消息状态不可用，请重新获取会话状态",
                status_code=409,
            )
        # The public rolling-history owner API opens its own transactions. End
        # this read snapshot first, then let its verified owner scope recheck
        # the principal, incarnation, and delete epoch against current state.
        db.rollback()
        if not _ensure_owner_history_v2_ready(
            session_id, binding, user, message_count,
        ):
            # Owner-verified legacy rows can still use server-side history and
            # graph-only compaction; their saves stay on the legacy endpoint.
            return _ok({
                "session_id": session_id,
                "mode": "legacy",
                "server_history": True,
            })

        return _ok({
            "session_id": session_id,
            "mode": "owner_v1",
            "incarnation_id": binding.incarnation_id,
        })
    except Exception:
        db.rollback()
        raise


@db_router.put("/sessions/{session_id}")
def update_session(session_id: str, payload: SessionUpdate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """更新会话属性。"""
    try:
        _begin_session_write(db)
        _require_session_owner_if_bound(db, session_id, user)
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
            s.context_window = _cap_agent_session_context_window(
                payload.context_window,
            )
        logger.info("会话已更新: id=%s", session_id)
        s.updated_at = datetime.datetime.utcnow()
        db.commit()
        return _ok({"id": session_id, "updated": True})
    except Exception:
        db.rollback()
        raise


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
    try:
        _begin_session_write(db)
        _require_session_owner_if_bound(db, session_id, user)
        s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not s:
            logger.warning("会话 PATCH 失败: id=%s 不存在", session_id)
            raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
        _apply_session_update(s, payload)
        s.updated_at = datetime.datetime.utcnow()
        result = _ok(_serialize_session(s, db=db))
        db.commit()
        logger.info("会话已 PATCH: id=%s", session_id)
        return result
    except Exception:
        db.rollback()
        raise


@db_router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """删除会话及其所有派生数据（级联 + tombstone）。"""
    try:
        s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not s:
            if _session_is_tombstoned(db, session_id):
                return _ok({"id": session_id, "deleted": True})
            logger.warning("会话删除失败: id=%s 不存在", session_id)
            raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)

        owner_binding = db.query(SessionOwnerBinding).filter(
            SessionOwnerBinding.session_id == session_id
        ).first()
        if owner_binding is not None and user is not None:
            try:
                retire_session_owner(db, session_id, user, datetime.datetime.utcnow())
            except PermissionError as exc:
                raise _fail("FORBIDDEN", "无权删除该会话", status_code=403) from exc

        _purge_session_rows(db, session_id)
        db.delete(s)
        db.commit()

        _purge_session_runtime(session_id)
        logger.info("会话已删除: id=%s", session_id)
        return _ok({"id": session_id, "deleted": True})
    except Exception:
        db.rollback()
        raise


def _session_is_tombstoned(db: DBSession, session_id: str) -> bool:
    return db.query(SessionTombstone.session_id).filter(
        SessionTombstone.session_id == session_id
    ).first() is not None


def _write_tombstone(db: DBSession, session_id: str, now, epoch: str) -> None:
    row = db.query(SessionTombstone).filter(
        SessionTombstone.session_id == session_id
    ).first()
    if row is None:
        db.add(SessionTombstone(session_id=session_id, deleted_at=now, epoch=epoch))
        return
    row.deleted_at = now
    row.epoch = epoch


def _purge_session_rows(db: DBSession, session_id: str) -> None:
    """Delete session-scoped rows in the caller's transaction."""
    now = utc_now()
    epoch = stage_session_delete_epoch(db, session_id, now)
    _write_tombstone(db, session_id, now, epoch)
    for model in (FeedbackModel, TokenUsageModel, ToolAuditModel, TodoModel, PlanModel):
        db.query(model).filter(model.session_id == session_id).delete(
            synchronize_session=False,
        )
    purge_session_observations(db, session_id)
    rolling_prefix = rolling_history_session_prefix(session_id)
    db.query(SettingsModel).filter(
        func.substr(SettingsModel.key, 1, len(rolling_prefix)) == rolling_prefix,
    ).delete(synchronize_session=False)
    db.query(SettingsModel).filter(
        SettingsModel.key.startswith(_session_prefix(session_id), autoescape=True),
    ).delete(synchronize_session=False)
    from app.db import agent_mode_store

    agent_mode_store.clear_plan_mode_requested_in_db(db, session_id)
    # Working memory and its checkpoint snapshots are session-scoped. Project
    # and user memory must survive a session deletion.
    from app.db.memory_store import purge_session_memory

    purge_session_memory(db, session_id)


def _purge_session_runtime(session_id: str) -> None:
    """Best-effort cleanup outside the database transaction.

    Runs synchronously in the endpoint's worker thread; it must never create
    the async checkpointer there (see delete_thread_checkpoint_direct).
    """
    try:
        from src.agent.agent_factory import delete_thread_checkpoint_direct

        delete_thread_checkpoint_direct(session_id)
    except Exception:
        logger.warning("checkpoint cleanup deferred: %s", session_id)
    try:
        from app.db.todo_store import clear_todos

        clear_todos(session_id)
    except Exception:
        logger.warning("todo store cleanup failed: %s", session_id)
    try:
        from src.agent.tools.groups.execution.todo_write import clear_session_todos

        clear_session_todos(session_id)
    except Exception:
        logger.warning("todo cleanup failed: %s", session_id)
    try:
        from app.api.chat_routes import _clear_pending_request_snapshot

        _clear_pending_request_snapshot(session_id)
    except Exception:
        logger.warning("pending cleanup failed: %s", session_id)

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


class MessageAppendV1(BaseModel):
    id: str = Field(min_length=1)
    expected_incarnation_id: str = Field(min_length=1)
    role: str
    content: str
    sources: Optional[list] = None
    tool_calls: Optional[list] = None
    activity: Optional[dict] = None


class MessageTruncateV1(BaseModel):
    keep_through_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    expected_incarnation_id: str = Field(min_length=1)


@db_router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: str,
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    before_id: Optional[str] = Query(default=None, min_length=1),
    expected_revision: Optional[int] = Query(default=None, ge=0),
    expected_incarnation_id: Optional[str] = Query(default=None, min_length=1),
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    """获取会话的所有消息，或按稳定游标读取消息页。"""
    try:
        _begin_session_read_snapshot(db)
        if expected_incarnation_id is None:
            _require_session_owner_if_bound(db, session_id, user)
        else:
            _require_active_session_incarnation(
                db,
                session_id,
                user,
                expected_incarnation_id,
            )
        query = db.query(MessageModel).filter(MessageModel.session_id == session_id)

        def _serialize(messages: list[MessageModel]) -> list[dict]:
            return [
                {
                    "id": message.id,
                    "seq": message.seq,
                    "role": message.role,
                    "content": message.content,
                    "status": message.status,
                    "sources": message.sources or [],
                    "tool_calls": message.tool_calls or [],
                    "activity": project_skill_activity(message.activity),
                    "created_at": to_utc_iso_z(message.created_at) if message.created_at else "",
                }
                for message in messages
            ]

        # Keep the legacy full-history shape and ordering when neither pagination
        # parameter is present. Existing clients use this response for recovery.
        if (limit is None and before_id is None and expected_revision is None
                and expected_incarnation_id is None):
            msgs = query.order_by(MessageModel.created_at).all()
            session_version = db.query(SessionModel.version).filter(
                SessionModel.id == session_id,
            ).scalar()
            return _ok({
                "messages": _serialize(msgs),
                "version": int(session_version or 0),
            })

        current_revision = db.query(SessionModel.message_revision).filter(
            SessionModel.id == session_id
        ).scalar()
        if current_revision is None:
            raise _fail("NOT_FOUND", "会话不存在", status_code=404)
        current_revision = int(current_revision)
        if expected_revision is not None and expected_revision != current_revision:
            raise _fail(
                "SNAPSHOT_STALE",
                "会话消息已变化，请重新读取分页",
                status_code=409,
            )

        page_limit = 50 if limit is None else limit
        if not 1 <= page_limit <= 200:
            raise _fail("INVALID_REQUEST", "limit 必须在 1 到 200 之间", status_code=422)

        if before_id is not None:
            anchor = db.query(MessageModel).filter(
                MessageModel.session_id == session_id,
                MessageModel.id == before_id,
            ).first()
            if anchor is None:
                raise _fail("NOT_FOUND", "分页锚点不存在或不属于该会话", status_code=404)

            if anchor.created_at is None:
                query = query.filter(
                    and_(MessageModel.created_at.is_(None), MessageModel.id < anchor.id)
                )
            else:
                query = query.filter(or_(
                    MessageModel.created_at < anchor.created_at,
                    and_(MessageModel.created_at == anchor.created_at, MessageModel.id < anchor.id),
                    MessageModel.created_at.is_(None),
                ))

        # Fetch newest-first so before_id walks toward older rows, then return this
        # page in the chronological order expected by the chat transcript.
        page_rows = query.order_by(
            MessageModel.created_at.desc(), MessageModel.id.desc()
        ).limit(page_limit + 1).all()
        has_more = len(page_rows) > page_limit
        page_rows = list(reversed(page_rows[:page_limit]))
        next_before_id = page_rows[0].id if has_more and page_rows else None
        return _ok({
            "messages": _serialize(page_rows),
            "next_before_id": next_before_id,
            "has_more": has_more,
            "message_revision": current_revision,
        })
    except Exception:
        db.rollback()
        raise


@db_router.post("/sessions/{session_id}/messages")
def create_message(session_id: str, payload: MessageCreate, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """在会话中添加或幂等更新一条消息。"""
    try:
        _begin_session_write(db)
        if _session_is_tombstoned(db, session_id):
            raise _fail("SESSION_DELETED", f"会话已删除: {session_id}", status_code=410)
        _require_session_owner_if_bound(db, session_id, user)
        s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not s:
            raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)

        mid = payload.id or f"m{uuid.uuid4().hex[:8]}"
        msg = None
        if payload.id:
            existing_message_session = db.query(MessageModel.session_id).filter(
                MessageModel.id == mid
            ).first()
            if existing_message_session is not None:
                existing_session_id = existing_message_session[0]
                if existing_session_id != session_id:
                    if existing_session_id is not None:
                        _require_session_owner_if_bound(db, existing_session_id, user)
                    raise _fail("CONFLICT", "消息 ID 已属于其他会话", status_code=409)
                msg = db.query(MessageModel).filter(MessageModel.id == mid).first()

        is_new = msg is None
        normalized_activity = project_skill_activity(payload.activity)
        if is_new:
            next_seq = db.query(func.max(MessageModel.seq)).filter(
                MessageModel.session_id == session_id,
            ).scalar()
            msg = MessageModel(
                id=mid, session_id=session_id,
                seq=(int(next_seq) + 1) if type(next_seq) is int else 1,
            )
            s.msg_count = (s.msg_count or 0) + 1

        payload_changed = is_new or not _message_payload_matches(
            msg,
            role=payload.role,
            content=payload.content,
            sources=payload.sources,
            tool_calls=payload.tool_calls,
            activity=normalized_activity,
        )
        if payload_changed:
            s.version = (getattr(s, "version", 0) or 0) + 1
            msg.role = payload.role
            msg.content = payload.content
            msg.sources = payload.sources
            msg.tool_calls = payload.tool_calls
            msg.activity = normalized_activity
            s.message_revision = int(s.message_revision or 0) + 1
        s.updated_at = datetime.datetime.utcnow()
        if is_new and payload.role == "user" and _should_set_title_from_message(s.title, payload.content):
            # 用第一条真实用户消息做标题预览
            s.title = _title_from_user_message(payload.content)

        logger.info("消息已%s: session=%s role=%s id=%s", "保存" if is_new else "更新", session_id, payload.role, mid)
        if is_new:
            db.add(msg)
        db.flush()
        result = _ok({
            "id": mid,
            "seq": msg.seq,
            "role": payload.role,
            "content": payload.content,
            "created_at": to_utc_iso_z(msg.created_at) if msg.created_at else "",
        })
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@db_router.post("/sessions/{session_id}/messages/append-v1")
def append_message_v1(
    session_id: str,
    payload: MessageAppendV1,
    user: dict = Depends(current_user_optional),
) -> dict:
    """Append one stable-ID message without invoking chat, agent, or tools."""
    if not payload.id.strip():
        raise _fail("INVALID_REQUEST", "消息 ID 不能为空", status_code=422)
    if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", payload.id, re.ASCII) is None
            or payload.role not in {"user", "assistant"}):
        raise _fail("INVALID_REQUEST", "消息 ID 或角色不受支持", status_code=422)

    try:
        validate_rolling_history_body_event_content(payload.content)
    except ValueError as exc:
        raise _fail("INVALID_REQUEST", "消息正文无效或超过大小限制", status_code=422) from exc

    normalized_activity = project_skill_activity(payload.activity)
    message_event = {
        "message_id": payload.id,
        "expected_incarnation_id": payload.expected_incarnation_id,
        "role": payload.role,
        "content": payload.content,
        "sources": payload.sources,
        "tool_calls": payload.tool_calls,
        "activity": normalized_activity,
    }

    event_type = "user_text" if payload.role == "user" else "assistant_text"

    def read_rows(
        handle: RollingHistorySqlHandle,
        statement: str,
        parameters: tuple = (),
    ) -> tuple[tuple[object, ...], ...]:
        result = handle.execute(statement, parameters)
        if type(result) is not MaterializedRows:
            raise RuntimeError("Owner SQL query did not return materialized rows")
        return result.rows

    def decode_json_column(value: object) -> object:
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("Message JSON column is unavailable")
        return json.loads(value)

    def canonical_source_payload_sha256(event: RollingHistoryBodyEventInput) -> str:
        encoded = json.dumps(
            {"message_event": event.message_event, "created_at": event.created_at},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validate_event_metadata(event: RollingHistoryBodyEventInput) -> None:
        metadata_projection = {
            "event_type": event.event_type,
            "attachments": [],
            "created_at": event.created_at,
            "message_event": {
                key: value for key, value in event.message_event.items()
                if key != "content"
            },
        }
        try:
            encoded_metadata = json.dumps(
                metadata_projection,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise _fail("INVALID_REQUEST", "消息元数据无法保存", status_code=422) from exc
        if len(encoded_metadata) > MAX_BODY_EVENT_METADATA_UTF8_BYTES:
            raise _fail("INVALID_REQUEST", "消息元数据超过大小限制", status_code=422)

    def owner_callback(handle: RollingHistorySqlHandle, scope) -> dict:
        session_rows = read_rows(
            handle,
            "SELECT title, message_revision FROM sessions WHERE id = ?",
            (session_id,),
        )
        if not session_rows:
            raise _fail("NOT_FOUND", "会话不存在", status_code=404)
        session_title, _current_revision = session_rows[0]

        def require_existing_session_owner(existing_session_id: str) -> None:
            bindings = read_rows(
                handle,
                "SELECT owner_principal_hash, incarnation_id "
                "FROM session_owner_bindings WHERE session_id = ?",
                (existing_session_id,),
            )
            if not bindings:
                return
            try:
                principal_hash = principal_digest_for_user(user)
            except PermissionError as exc:
                raise _fail("FORBIDDEN", "无权访问该会话", status_code=403) from exc
            session_exists = read_rows(
                handle,
                "SELECT 1 FROM sessions WHERE id = ?",
                (existing_session_id,),
            )
            binding_owner, incarnation_id = bindings[0]
            if (type(incarnation_id) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", incarnation_id, re.ASCII) is None):
                raise _fail("FORBIDDEN", "无权访问该会话", status_code=403)
            incarnation_rows = read_rows(
                handle,
                "SELECT retired_at FROM session_incarnations WHERE id = ?",
                (incarnation_id,),
            )
            if (not session_exists or binding_owner != principal_hash
                    or not incarnation_rows or incarnation_rows[0][0] is not None):
                raise _fail("FORBIDDEN", "无权访问该会话", status_code=403)

        def existing_message_rows() -> tuple[tuple[object, ...], ...]:
            return read_rows(
                handle,
                "SELECT id, session_id, role, content, sources, tool_calls, activity, created_at "
                "FROM messages WHERE id = ?",
                (payload.id,),
            )

        def message_matches(row: tuple[object, ...]) -> bool:
            return (
                row[2] == payload.role
                and row[3] == payload.content
                and _same_json_payload(decode_json_column(row[4]), payload.sources)
                and _same_json_payload(decode_json_column(row[5]), payload.tool_calls)
                and _same_json_payload(decode_json_column(row[6]), normalized_activity)
            )

        existing_rows = existing_message_rows()
        already_saved = bool(existing_rows)
        if existing_rows:
            existing = existing_rows[0]
            if existing[1] != session_id:
                if existing[1] is not None:
                    require_existing_session_owner(existing[1])
                raise _fail("MESSAGE_ID_IN_USE", "消息 ID 已属于其他会话", status_code=409)
            if not message_matches(existing):
                raise _fail("MESSAGE_CONFLICT", "消息 ID 已存在且内容不同", status_code=409)
            created_at = existing[7]
            if type(created_at) is not str:
                raise _fail("MESSAGE_CONFLICT", "消息时间戳不可用", status_code=409)
        else:
            created_at = datetime.datetime.utcnow().isoformat(sep=" ")

        event = RollingHistoryBodyEventInput(
            message_event=message_event,
            body_text=payload.content,
            created_at=created_at,
            event_type=event_type,
        )
        validate_event_metadata(event)

        def write_source_message(
            callback_handle: RollingHistorySqlHandle,
            callback_session_id: str,
            callback_event: RollingHistoryBodyEventInput,
        ) -> RollingHistorySourceMessageWriteReceipt:
            if (callback_handle is not handle or callback_session_id != session_id
                    or callback_event is not event):
                raise RollingHistoryV2Conflict("Message writer callback binding changed")
            current_rows = existing_message_rows()
            payload_sha256 = canonical_source_payload_sha256(callback_event)
            if current_rows:
                current = current_rows[0]
                if current[1] != session_id:
                    if current[1] is not None:
                        require_existing_session_owner(current[1])
                    raise _fail("MESSAGE_ID_IN_USE", "消息 ID 已属于其他会话", status_code=409)
                if (not message_matches(current) or current[7] != callback_event.created_at):
                    raise _fail("MESSAGE_CONFLICT", "消息 ID 已存在且内容不同", status_code=409)
                return RollingHistorySourceMessageWriteReceipt(
                    message_id=payload.id,
                    outcome="existing_exact",
                    payload_sha256=payload_sha256,
                )

            json_values = tuple(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                )
                for value in (payload.sources, payload.tool_calls, normalized_activity)
            )
            inserted = callback_handle.execute(
                "INSERT INTO messages "
                "(id, session_id, role, content, sources, tool_calls, activity, seq, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, "
                "(SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?), "
                "'complete', ?)",
                (
                    payload.id, session_id, payload.role, payload.content,
                    *json_values, session_id, created_at,
                ),
            )
            if type(inserted) is not int or inserted != 1:
                raise RollingHistoryV2Conflict("Message source insert did not affect one row")

            updated_at = datetime.datetime.utcnow().isoformat(sep=" ")
            next_title = session_title
            if payload.role == "user" and _should_set_title_from_message(session_title, payload.content):
                next_title = _title_from_user_message(payload.content)
            updated = callback_handle.execute(
                "UPDATE sessions SET msg_count = COALESCE(msg_count, 0) + 1, "
                "message_revision = COALESCE(message_revision, 0) + 1, title = ?, updated_at = ? "
                "WHERE id = ?",
                (next_title, updated_at, session_id),
            )
            if type(updated) is not int or updated != 1:
                raise RollingHistoryV2Conflict("Session counters did not update")
            inserted_rows = existing_message_rows()
            if (not inserted_rows or not message_matches(inserted_rows[0])
                    or inserted_rows[0][7] != callback_event.created_at):
                raise RollingHistoryV2Conflict("Inserted Message readback mismatch")
            return RollingHistorySourceMessageWriteReceipt(
                message_id=payload.id,
                outcome="inserted",
                payload_sha256=payload_sha256,
            )

        append_rolling_history_v2_body_events_in_transaction(
            handle,
            session_id,
            scope,
            event,
            idempotency_key=payload.id,
            write_source_message=write_source_message,
        )
        current_rows = read_rows(
            handle,
            "SELECT message_revision FROM sessions WHERE id = ?",
            (session_id,),
        )
        if not current_rows:
            raise RollingHistoryV2Conflict("Session disappeared during message append")
        try:
            created_at_response = datetime.datetime.fromisoformat(created_at).isoformat()
        except ValueError as exc:
            raise RollingHistoryV2Conflict("Message timestamp is unavailable") from exc
        return {
            "id": payload.id,
            "status": "already_saved" if already_saved else "saved",
            "message_revision": int(current_rows[0][0] or 0),
            "created_at": created_at_response,
        }

    try:
        result = run_chat_append_owner_write_operation(
            user,
            session_id,
            payload.expected_incarnation_id,
            owner_callback,
        )
    except RollingHistoryOwnerScopeError as exc:
        raise _fail(
            "SESSION_INCARNATION_CONFLICT",
            "会话生命周期已变化或不可用，请重新获取会话状态",
            status_code=409,
        ) from exc
    except RollingHistoryV2MigrationRequired as exc:
        raise _fail(
            "MESSAGE_CONFLICT",
            "会话历史尚未迁移，当前无法安全追加消息",
            status_code=409,
        ) from exc
    except RollingHistoryV2Conflict as exc:
        raise _fail(
            "MESSAGE_CONFLICT",
            "消息 ID 已存在且内容不同，或会话历史状态冲突",
            status_code=409,
        ) from exc
    return _ok(result)


@db_router.delete("/sessions/{session_id}/messages")
def truncate_messages(session_id: str, keep_count: int = 0, db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """截断会话消息：保留前 keep_count 条，删除其余。keep_count=0 清空。
    用于前端 retry/editAndResend 时同步删除后端旧消息，防止刷新后重复。"""
    try:
        _begin_session_write(db)
        _require_session_owner_if_bound(db, session_id, user)
        s = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not s:
            raise _fail("NOT_FOUND", f"会话不存在: {session_id}", status_code=404)
        msgs = db.query(MessageModel).filter(
            MessageModel.session_id == session_id
        ).order_by(MessageModel.created_at).all()
        keep_count = max(0, min(keep_count, len(msgs)))
        to_delete = msgs[keep_count:]
        for m in to_delete:
            db.delete(m)
        s.msg_count = keep_count
        if to_delete:
            s.message_revision = int(s.message_revision or 0) + 1
        s.updated_at = datetime.datetime.utcnow()
        db.commit()
        logger.info("消息已截断: session=%s kept=%d deleted=%d", session_id, keep_count, len(to_delete))
        return _ok({"kept": keep_count, "deleted": len(to_delete)})
    except Exception:
        db.rollback()
        raise


@db_router.post("/sessions/{session_id}/messages/truncate-v1")
def truncate_messages_through_v1(
    session_id: str,
    payload: MessageTruncateV1,
    db: DBSession = Depends(get_db),
    user: dict = Depends(current_user_optional),
) -> dict:
    """Keep the anchor and older rows from the supplied message snapshot."""
    if not payload.keep_through_id.strip():
        raise _fail("INVALID_REQUEST", "截断锚点 ID 不能为空", status_code=422)

    try:
        _begin_session_write(db)
        _require_active_session_incarnation(
            db,
            session_id,
            user,
            payload.expected_incarnation_id,
        )
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if session is None:
            raise _fail("NOT_FOUND", "会话不存在", status_code=404)

        current_revision = int(session.message_revision or 0)
        if payload.expected_revision != current_revision:
            raise _fail(
                "SNAPSHOT_STALE",
                "会话消息已变化，请重新读取后再截断",
                status_code=409,
            )

        anchor = db.query(MessageModel).filter(
            MessageModel.session_id == session_id,
            MessageModel.id == payload.keep_through_id,
        ).first()
        if anchor is None:
            raise _fail("ANCHOR_NOT_FOUND", "截断锚点不存在或不属于该会话", status_code=404)

        if anchor.created_at is None:
            later_than_anchor = or_(
                MessageModel.created_at.is_not(None),
                and_(
                    MessageModel.created_at.is_(None),
                    MessageModel.id > anchor.id,
                ),
            )
        else:
            later_than_anchor = or_(
                MessageModel.created_at > anchor.created_at,
                and_(
                    MessageModel.created_at == anchor.created_at,
                    MessageModel.id > anchor.id,
                ),
            )

        to_delete = db.query(MessageModel).filter(
            MessageModel.session_id == session_id,
            later_than_anchor,
        ).all()
        for message in to_delete:
            db.delete(message)

        if to_delete:
            session.message_revision = current_revision + 1
            db.flush()
            session.msg_count = int(db.query(func.count(MessageModel.id)).filter(
                MessageModel.session_id == session_id
            ).scalar() or 0)
            session.updated_at = datetime.datetime.utcnow()

        kept_count = int(db.query(func.count(MessageModel.id)).filter(
            MessageModel.session_id == session_id
        ).scalar() or 0)
        result = _ok({
            "kept": kept_count,
            "deleted": len(to_delete),
            "message_revision": int(session.message_revision or 0),
        })
        db.commit()
        logger.info(
            "锚点截断完成: session=%s kept=%d deleted=%d",
            session_id,
            kept_count,
            len(to_delete),
        )
        return result
    except Exception:
        db.rollback()
        raise


# ═══════════════════════════════════════════
# Settings CRUD
# ═══════════════════════════════════════════

# 设置键白名单
ALLOWED_SETTINGS_KEYS = {
    "activeProvider", "model", "visionModel", "imageModel",
    "temperature", "topK", "relevanceThreshold", "maxTokens", "systemPrompt", "longTermMemory",
    "autoMemoryEnabled",
    "chatFontSize", "themeMode", "lang", "permissionMode",
}

# permissionMode is persisted only in these three app-wide modes. "plan" is a
# request-level mode and must never become a stored setting.
PERSISTED_PERMISSION_MODES = frozenset({"bypassPermissions", "default", "acceptEdits", "yolo"})


@db_router.get("/settings")
def get_settings(db: DBSession = Depends(get_db), user: dict = Depends(current_user_optional)) -> dict:
    """获取所有设置键值对。"""
    rows = db.query(SettingsModel).filter(
        SettingsModel.key != MEMORY_CLEAR_EPOCH_KEY,
        SettingsModel.key != "memoryV2LongTermMemoryImported",
        func.substr(SettingsModel.key, 1, len(SESSION_DELETE_EPOCH_PREFIX)) != SESSION_DELETE_EPOCH_PREFIX,
        func.substr(SettingsModel.key, 1, len(CONTEXT_EVENT_PREFIX)) != CONTEXT_EVENT_PREFIX,
        func.substr(
            SettingsModel.key,
            1,
            len(f"{TOOL_OBSERVATION_SETTINGS_PREFIX}:"),
        ) != f"{TOOL_OBSERVATION_SETTINGS_PREFIX}:",
    ).all()
    return _ok({"settings": {r.key: r.value for r in rows}})


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
    if "autoMemoryEnabled" in payload:
        raw_value = payload["autoMemoryEnabled"]
        if isinstance(raw_value, bool):
            normalized_auto = "true" if raw_value else "false"
        elif isinstance(raw_value, str) and raw_value.strip().lower() in ("true", "false"):
            normalized_auto = raw_value.strip().lower()
        else:
            raise _fail(
                "INVALID_AUTO_MEMORY_SETTING",
                "autoMemoryEnabled must be a boolean.",
                status_code=422,
            )
        payload = {**payload, "autoMemoryEnabled": normalized_auto}
    if "permissionMode" in payload:
        mode = payload["permissionMode"]
        if not isinstance(mode, str) or mode not in PERSISTED_PERMISSION_MODES:
            raise _fail(
                "INVALID_PERMISSION_MODE",
                "permissionMode must be one of bypassPermissions, default, acceptEdits, yolo.",
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
    now = datetime.datetime.utcnow()
    if payload.get("longTermMemory") == "":
        previous = db.query(SettingsModel).filter(
            SettingsModel.key == "longTermMemory",
        ).first()
        if previous is not None and previous.value:
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
