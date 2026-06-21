"""FastAPI 依赖注入：认证、数据库会话等共享依赖。"""
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Header, Request, status
from sqlalchemy.orm import Session as DBSession

from app.api.auth import get_provider_key_by_session
from app.db.database import get_db

logger = logging.getLogger(__name__)


def get_session_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Optional[str]:
    """从 Authorization Bearer 或 query 参数 ?token= 提取 session_token。"""
    # 1. Authorization: Bearer <token>
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    # 2. Query 参数（用于 WebSocket / SSE）
    token = request.query_params.get("token")
    if token:
        return token
    # 3. X-API-Key 兼容（旧客户端直传 API Key，无 session_token）
    return None


def current_user(
    token: Optional[str] = Depends(get_session_token),
) -> dict:
    """鉴权依赖：校验 session_token，返回 {provider, api_key}。

    开发态兼容：若未配置任何 Provider Key，则跳过鉴权（返回 None）。
    生产态：必须提供有效 token，否则 401。
    """
    try:
        from app.api.auth import _load_store

        # 开发态兼容：若加密存储为空（未配置任何 Key），跳过鉴权
        store = _load_store()
        if not store.get("providers"):
            return {"provider": None, "api_key": None, "anonymous": True}

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"success": False, "error": {"code": "AUTH_REQUIRED", "message": "未提供认证 token"}},
            )

        result = get_provider_key_by_session(token)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "token 无效或已过期"}},
            )

        provider, api_key = result
        return {"provider": provider, "api_key": api_key, "anonymous": False}
    except HTTPException:
        raise
    except Exception:
        logger.warning("鉴权存储读取失败，跳过鉴权")
        return {"provider": None, "api_key": None, "anonymous": True}


def current_user_optional(
    token: Optional[str] = Depends(get_session_token),
) -> dict:
    """可选鉴权：有 token 则校验，无 token 则匿名访问。"""
    if not token:
        return {"provider": None, "api_key": None, "anonymous": True}
    result = get_provider_key_by_session(token)
    if not result:
        return {"provider": None, "api_key": None, "anonymous": True}
    provider, api_key = result
    return {"provider": provider, "api_key": api_key, "anonymous": False}


def ws_auth(websocket) -> Optional[dict]:
    """WebSocket 鉴权：在 accept() 前调用，失败返回错误码。"""
    try:
        from app.api.auth import _load_store

        store = _load_store()
        # 开发态兼容：未配置 Key 时跳过
        if not store.get("providers"):
            return {"provider": None, "api_key": None, "anonymous": True}

        token = websocket.query_params.get("token") or websocket.headers.get("x-token", "")
        if not token:
            return None  # 调用方负责 close
        result = get_provider_key_by_session(token)
        if not result:
            return None
        provider, api_key = result
        return {"provider": provider, "api_key": api_key, "anonymous": False}
    except Exception:
        logger.warning("WS 鉴权存储读取失败，跳过鉴权")
        return {"provider": None, "api_key": None, "anonymous": True}
