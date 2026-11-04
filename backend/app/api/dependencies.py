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
    """从 Authorization Bearer 或 monitor WebSocket 的 ?token= 提取 token."""
    # 1. Authorization: Bearer <token>
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    # 2. Query 参数只用于无法设置 header 的 WebSocket 串口监视器
    if request.url.path.startswith("/api/monitor/"):
        token = request.query_params.get("token")
        if token:
            return token
    return None


def require_session(
    authorization: Optional[str] = Header(default=None),
) -> str:
    """Require a configured provider and a valid Bearer token.

    Unlike current_user this has no first-run anonymous branch and does not
    read ``?token=``, so it is safe for process-spawning endpoints.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"success": False, "error": {"code": "AUTH_REQUIRED", "message": "未提供认证 token"}},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token or not get_provider_key_by_session(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "token 无效或已过期"}},
        )
    return token


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
        logger.warning("鉴权存储读取失败，拒绝访问")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"success": False, "error": {"code": "AUTH_STORE_ERROR", "message": "鉴权服务暂时不可用"}},
        )


def current_user_optional(
    token: Optional[str] = Depends(get_session_token),
) -> dict:
    """可选鉴权：有 token 则校验，无 token 则匿名访问。"""
    if not token:
        return {"provider": None, "api_key": None, "anonymous": True}
    result = get_provider_key_by_session(token)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "token 无效或已过期"}},
        )
    provider, api_key = result
    return {"provider": provider, "api_key": api_key, "anonymous": False}


def ws_auth(websocket) -> Optional[dict]:
    """Require a valid token once a provider key has been configured."""
    try:
        from app.api.auth import _load_store

        store = _load_store()
        # 开发态兼容：未配置 Key 时跳过
        if not store.get("providers"):
            return {"provider": None, "api_key": None, "anonymous": True}

        token = websocket.query_params.get("token") or websocket.headers.get("x-token", "")
        if not token:
            return None
        result = get_provider_key_by_session(token)
        if not result:
            return None
        provider, api_key = result
        return {"provider": provider, "api_key": api_key, "anonymous": False}
    except Exception:
        logger.warning("WS 鉴权存储读取失败，拒绝访问")
        return None
