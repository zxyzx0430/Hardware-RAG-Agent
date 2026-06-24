"""API Key 加密存储与认证端点"""
import os
import json
import logging
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# --- 加密存储 ---
ENCRYPTION_KEY_PATH = Path(__file__).parent.parent / "db" / ".enc_key"
STORE_PATH = Path(__file__).parent.parent / "db" / "keys_store.json"

def _get_fernet() -> Fernet:
    """获取或创建加密密钥"""
    if not ENCRYPTION_KEY_PATH.exists():
        ENCRYPTION_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENCRYPTION_KEY_PATH.write_bytes(Fernet.generate_key())
        # Restrict key file permissions to owner only (P1: avoid arbitrary user read)
        try:
            os.chmod(ENCRYPTION_KEY_PATH, 0o600)
        except (OSError, AttributeError):
            pass  # Windows does not support Unix file permissions
    return Fernet(ENCRYPTION_KEY_PATH.read_bytes())

def _load_store() -> dict:
    if not STORE_PATH.exists():
        return {"providers": {}, "sessions": {}}
    return json.loads(STORE_PATH.read_text())

def _save_store(data: dict):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, indent=2))

def encrypt_key(api_key: str) -> str:
    return _get_fernet().encrypt(api_key.encode()).decode()

def decrypt_key(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()

def store_provider_key(provider: str, api_key: str, base_url: str = "") -> str:
    """存储 Provider 的 API Key，返回 session_token"""
    store = _load_store()
    encrypted = encrypt_key(api_key)
    store["providers"][provider] = {
        "encrypted_key": encrypted,
        "base_url": base_url,
        "updated_at": datetime.now().isoformat(),
    }
    # 生成 session_token
    token = secrets.token_hex(32)
    store["sessions"][token] = {
        "provider": provider,
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
    }
    _save_store(store)
    return token

def get_provider_key(provider: str) -> Optional[str]:
    """从加密存储读取 Provider 的明文 API Key"""
    store = _load_store()
    info = store["providers"].get(provider)
    if not info:
        return None
    return decrypt_key(info["encrypted_key"])

def get_provider_key_by_session(token: str) -> Optional[tuple[str, str]]:
    """通过 session_token 获取 (provider, api_key)"""
    store = _load_store()
    session = store["sessions"].get(token)
    if not session:
        return None
    expires = datetime.fromisoformat(session["expires_at"])
    if datetime.now() > expires:
        return None
    provider = session["provider"]
    key = get_provider_key(provider)
    if not key:
        return None
    return (provider, key)

class StoreKeyRequest(BaseModel):
    provider: str
    api_key: str
    base_url: str = ""

@router.post("/store-key")
async def store_key(req: StoreKeyRequest):
    """存储 API Key，返回 session_token"""
    if not req.api_key or not req.api_key.strip():
        logger.warning("API Key 存储失败: 空值")
        raise HTTPException(400, detail="API Key 不能为空")
    token = store_provider_key(req.provider, req.api_key.strip(), req.base_url)
    logger.info("API Key 已存储: provider=%s", req.provider)
    return {"success": True, "data": {"session_token": token, "provider": req.provider}}

@router.get("/keys")
async def list_keys(authorization: Optional[str] = Header(default=None)):
    """列出已存储的 Provider（不含明文 Key）"""
    store = _load_store()
    # 内联鉴权检查（避免与 dependencies.py 循环导入）
    if store.get("providers"):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_REQUIRED", "message": "未提供认证 token"}})
        token = authorization.split(" ", 1)[1].strip()
        if not get_provider_key_by_session(token):
            raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "token 无效或已过期"}})
    providers = []
    for name, info in store["providers"].items():
        providers.append({
            "provider": name,
            "base_url": info.get("base_url", ""),
            "updated_at": info.get("updated_at", ""),
            "has_key": True,
        })
    return {"success": True, "data": {"providers": providers}}

@router.delete("/keys/{provider}")
async def delete_key(provider: str, authorization: Optional[str] = Header(default=None)):
    """删除 Provider 的 API Key"""
    # 内联鉴权检查（避免与 dependencies.py 循环导入）
    store = _load_store()
    if store.get("providers"):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_REQUIRED", "message": "未提供认证 token"}})
        token = authorization.split(" ", 1)[1].strip()
        if not get_provider_key_by_session(token):
            raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "token 无效或已过期"}})
    if provider in store["providers"]:
        del store["providers"][provider]
        _save_store(store)
        logger.info("API Key 已删除: provider=%s", provider)
    return {"success": True}
