"""API Key 加密存储与认证端点"""
import os
import sys
import json
import logging
import hashlib
import secrets
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from cryptography.fernet import Fernet
from src.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# --- 加密存储 ---
ENCRYPTION_KEY_PATH = Path(__file__).parent.parent / "db" / ".enc_key"
STORE_PATH = Path(__file__).parent.parent / "db" / "keys_store.json"

# 可重入锁：保护 _load_store/_save_store 的 read-modify-write 事务。
# 用 RLock 是因为 get_provider_key_by_session 内部调用 get_provider_key，
# delete_key/list_keys 通过 _require_auth 间接调用 get_provider_key_by_session。
_store_lock = threading.RLock()

# Auth store cache: avoid re-reading the file on every _load_store. Invalidated by mtime.
_auth_cache: dict = {"mtime": 0.0, "data": None}

def _get_fernet() -> Fernet:
    """获取或创建加密密钥"""
    if not ENCRYPTION_KEY_PATH.exists():
        ENCRYPTION_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENCRYPTION_KEY_PATH.write_bytes(Fernet.generate_key())
        # Restrict key file permissions to owner only (P1: avoid arbitrary user read)
        try:
            os.chmod(ENCRYPTION_KEY_PATH, 0o600)
        except (OSError, AttributeError) as e:
            logger.debug("chmod skipped on %s: %s", ENCRYPTION_KEY_PATH, e)

        # Windows 上 chmod 限制有限，额外用 icacls 设置 ACL (L-1)
        if sys.platform == "win32":
            try:
                import subprocess
                username = os.environ.get("USERNAME", "")
                if username:
                    # 移除继承，仅保留当前用户完全控制
                    subprocess.run(
                        ["icacls", str(ENCRYPTION_KEY_PATH), "/inheritance:r", "/grant:r", f"{username}:F"],
                        check=False,
                        capture_output=True,
                        timeout=5,
                    )
                    logger.info("Windows ACL applied to %s", ENCRYPTION_KEY_PATH)
            except Exception as e:
                logger.warning("Failed to apply Windows ACL: %s", e)
    return Fernet(ENCRYPTION_KEY_PATH.read_bytes())

def _load_store() -> dict:
    """Load store with mtime-based in-process cache."""
    empty = {"providers": {}, "sessions": {}}
    try:
        mtime = STORE_PATH.stat().st_mtime
    except OSError:
        _auth_cache["data"] = None
        return empty
    if _auth_cache["data"] is not None and _auth_cache["mtime"] == mtime:
        return _auth_cache["data"]
    try:
        data = json.loads(STORE_PATH.read_text())
    except OSError:
        _auth_cache["data"] = None
        return empty
    _auth_cache["mtime"] = mtime
    _auth_cache["data"] = data
    return data

def _save_store(data: dict):
    """原子写入：先写临时文件，再 os.replace 替换原文件，避免写一半崩溃损坏 store。"""
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STORE_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    os.replace(tmp_path, STORE_PATH)
    try:
        _auth_cache["mtime"] = STORE_PATH.stat().st_mtime
        _auth_cache["data"] = data
    except OSError:
        _auth_cache["data"] = None

def encrypt_key(api_key: str) -> str:
    return _get_fernet().encrypt(api_key.encode()).decode()

def decrypt_key(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()

def store_provider_key(provider: str, api_key: str, base_url: str = "") -> str:
    """存储 Provider 的 API Key，返回 session_token"""
    with _store_lock:
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

def get_provider_key(provider: str) -> Optional[tuple[str, str]]:
    """从加密存储读取 Provider 的 (api_key, base_url)"""
    with _store_lock:
        store = _load_store()
        info = store["providers"].get(provider)
        if not info:
            return None
        return (decrypt_key(info["encrypted_key"]), info.get("base_url", ""))

def get_provider_key_by_session(token: str) -> Optional[tuple[str, str]]:
    """通过 session_token 获取 (provider, api_key)。

    若 session 已过期，则从 store 中删除该 session（过期清理），不再保留。
    """
    with _store_lock:
        store = _load_store()
        session = store["sessions"].get(token)
        if not session:
            return None
        expires = datetime.fromisoformat(session["expires_at"])
        if datetime.now() > expires:
            # 过期 session 清理：删除后落盘，避免过期 session 残留
            del store["sessions"][token]
            _save_store(store)
            logger.info("过期 session 已清理: token=%s", token[:8])
            return None
        provider = session["provider"]
        result = get_provider_key(provider)
        if not result:
            return None
        key, _ = result
        return (provider, key)


def resolve_credentials(payload, request) -> dict:
    """统一解析 api_key/base_url/model/provider，优先级 header > payload > stored > settings。空串视为 None。"""
    def _normalize(value):
        return value if value and str(value).strip() else None

    header_key = _normalize(request.headers.get("x-api-key"))
    header_model = _normalize(request.headers.get("x-model"))
    header_provider = _normalize(request.headers.get("x-provider"))
    header_base_url = _normalize(request.headers.get("x-base-url"))

    payload_provider = _normalize(getattr(payload, "provider", None))
    payload_base_url = _normalize(getattr(payload, "base_url", None))
    payload_model = _normalize(getattr(payload, "model", None))

    provider = payload_provider or header_provider or "openai"

    stored = get_provider_key(provider)
    stored_key = stored[0] if stored else None
    stored_base_url = _normalize(stored[1]) if stored else None

    api_key = header_key or stored_key or _normalize(settings.llm_api_key)
    base_url = header_base_url or payload_base_url or stored_base_url or _normalize(settings.llm_base_url)
    model = header_model or payload_model or _normalize(settings.llm_model)

    return {"api_key": api_key, "base_url": base_url, "model": model, "provider": provider}


def _require_auth(authorization: Optional[str], store: dict) -> None:
    """内联鉴权检查(避免与 dependencies.py 循环导入)。

    无 providers 时跳过(首次配置场景);有 providers 时校验 Bearer token。
    """
    if not store.get("providers"):
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_REQUIRED", "message": "未提供认证 token"}})
    token = authorization.split(" ", 1)[1].strip()
    if not get_provider_key_by_session(token):
        raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "token 无效或已过期"}})


class StoreKeyRequest(BaseModel):
    provider: str
    api_key: str
    base_url: str = ""

class LoginRequest(BaseModel):
    """登录请求：用 api_key 换取 session_token，provider 可选。

    用于浏览器 localStorage 被清空但后端已存有 provider 的场景，
    用户重新输入已存储的 API Key 即可恢复会话，无需手动注入 token。
    若指定 provider 则优先匹配该 provider，否则/失败时搜索所有 provider。
    """
    provider: str = ""
    api_key: str

@router.post("/store-key")
async def store_key(req: StoreKeyRequest, authorization: Optional[str] = Header(default=None)):
    """存储 API Key，返回 session_token。

    首次无 Provider 时免鉴权（允许首次配置）；已有 Provider 时必须带有效 Bearer token。
    """
    if not req.api_key or not req.api_key.strip():
        logger.warning("API Key 存储失败: 空值")
        raise HTTPException(400, detail="API Key 不能为空")
    with _store_lock:
        store = _load_store()
        _require_auth(authorization, store)
    token = store_provider_key(req.provider, req.api_key.strip(), req.base_url)
    logger.info("API Key 已存储: provider=%s", req.provider)
    return {"success": True, "data": {"session_token": token, "provider": req.provider}}

@router.post("/login")
async def login(req: LoginRequest):
    """用 api_key 换取 session_token（会话恢复）。

    场景：浏览器 localStorage 被清空但后端已存有 provider。
    匹配策略：优先匹配指定 provider，否则搜索所有 provider。
    匹配失败返回 401（不泄露 provider 是否存在）。
    安全性：知道 API Key 的用户本就可直接通过 X-API-Key 调用，
    session_token 不增加额外权限，因此跨 provider 匹配不是安全降级。
    """
    if not req.api_key or not req.api_key.strip():
        raise HTTPException(400, detail="API Key 不能为空")
    raw_key = req.api_key.strip()
    with _store_lock:
        store = _load_store()
        matched_provider: Optional[str] = None
        # 1. 优先匹配指定 provider
        if req.provider:
            info = store["providers"].get(req.provider)
            if info:
                try:
                    if secrets.compare_digest(decrypt_key(info["encrypted_key"]), raw_key):
                        matched_provider = req.provider
                except Exception:
                    logger.warning("解密失败: provider=%s", req.provider)
        # 2. 指定 provider 未命中 → 搜索所有 provider
        if not matched_provider:
            for name, info in store["providers"].items():
                try:
                    if secrets.compare_digest(decrypt_key(info["encrypted_key"]), raw_key):
                        matched_provider = name
                        break
                except Exception:
                    continue
        if not matched_provider:
            raise HTTPException(401, detail={"success": False, "error": {"code": "AUTH_INVALID", "message": "API Key 未匹配到已存储的 provider"}})
        # 匹配成功 → 发放新 session_token
        token = secrets.token_hex(32)
        store["sessions"][token] = {
            "provider": matched_provider,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
        }
        _save_store(store)
    logger.info("登录成功: provider=%s", matched_provider)
    return {"success": True, "data": {"session_token": token, "provider": matched_provider}}

@router.get("/keys")
async def list_keys(authorization: Optional[str] = Header(default=None)):
    """列出已存储的 Provider（不含明文 Key）"""
    with _store_lock:
        store = _load_store()
        _require_auth(authorization, store)
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
    """删除 Provider 的 API Key，同时清理该 Provider 关联的所有 session。"""
    with _store_lock:
        store = _load_store()
        _require_auth(authorization, store)
        if provider in store["providers"]:
            del store["providers"][provider]
            # 清理该 Provider 关联的所有 session，避免悬挂 session
            store["sessions"] = {
                t: s for t, s in store["sessions"].items()
                if s.get("provider") != provider
            }
            _save_store(store)
            logger.info("API Key 已删除: provider=%s", provider)
    return {"success": True}
