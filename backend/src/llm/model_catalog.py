"""Provider model catalog and reasoning capability resolution.

The provider exposes reasoning capabilities on ``GET /v1/models``.  We keep a
short-lived cache so a chat turn never has to fetch the whole catalog just to
decide whether a requested reasoning effort can be applied.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Mapping

from src.llm.client import LLMClient, LLMError

logger = logging.getLogger(__name__)

ReasoningMode = str
EFFORT_MODES: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_REASONING_MODE = "auto"
CACHE_TTL_SECONDS = 600.0

_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def normalize_capability(raw: Any) -> dict[str, Any] | None:
    """Return a safe, frontend-friendly capability snapshot."""
    if type(raw) is not dict:
        return None
    reasoning = raw.get("reasoning") is True
    effort_supported = reasoning and raw.get("thinkingEffortSupported") is True
    efforts: list[str] = []
    if effort_supported:
        configured = raw.get("thinkingRange")
        if type(configured) is list:
            efforts = [item for item in configured if item in EFFORT_MODES]
        if not efforts:
            efforts = list(EFFORT_MODES)
    thinking_format = raw.get("thinkingFormat")
    return {
        "reasoning": reasoning,
        "can_disable": raw.get("thinkingCanDisable") is True,
        "effort_supported": effort_supported,
        "efforts": efforts,
        "format": thinking_format if type(thinking_format) is str else None,
    }


def normalize_catalog(models: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalize provider model payloads into ``model id -> capability``."""
    catalog: dict[str, dict[str, Any]] = {}
    for model in models:
        if type(model) is not dict:
            continue
        model_id = model.get("id")
        if type(model_id) is not str or not model_id:
            continue
        capability = normalize_capability(model.get("capabilities"))
        if capability is not None:
            catalog[model_id] = capability
    return catalog


def _cache_key(provider: str | None, base_url: str | None, api_key: str | None) -> str:
    material = f"{provider or ''}|{base_url or ''}|{api_key or ''}".encode()
    return hashlib.sha256(material).hexdigest()


def remember_catalog(
    *,
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
    catalog: dict[str, dict[str, Any]],
) -> None:
    """Seed the cache from a catalog response already fetched by the caller."""
    _CACHE[_cache_key(provider, base_url, api_key)] = (
        time.monotonic() + CACHE_TTL_SECONDS,
        catalog,
    )


async def load_catalog(
    *,
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
) -> dict[str, dict[str, Any]]:
    """Load and cache the provider catalog for one credential scope."""
    key = _cache_key(provider, base_url, api_key)
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]

    client = LLMClient(api_key=api_key or None, base_url=base_url or None)
    models = await client.list_model_catalog(
        api_key=api_key,
        base_url=base_url,
        provider=provider,
    )
    catalog = normalize_catalog(models)
    _CACHE[key] = (now + CACHE_TTL_SECONDS, catalog)
    return catalog


async def capability_for(
    *,
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
    model: str,
) -> dict[str, Any] | None:
    try:
        return (await load_catalog(
            provider=provider, base_url=base_url, api_key=api_key,
        )).get(model)
    except Exception:
        logger.warning("model capability lookup failed model=%s", model, exc_info=True)
        return None


async def resolve_reasoning_effort(
    *,
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
    model: str,
    requested: str | None,
) -> str | None:
    """Return the provider-supported effort, or ``None`` for auto.

    ``off`` is intentionally not translated yet: the catalog declares which
    models can disable thinking, but the provider request field is not part of
    the stable OpenAI-compatible contract.  Sending a guessed field would make
    the UI claim something the provider may ignore.
    """
    if requested not in EFFORT_MODES:
        return None
    capability = await capability_for(
        provider=provider, base_url=base_url, api_key=api_key, model=model,
    )
    if capability is None or not capability.get("effort_supported"):
        return None
    efforts = capability.get("efforts") or []
    return requested if requested in efforts else None


def clear_cache() -> None:
    """Drop cached catalogs (tests or explicit provider refresh)."""
    _CACHE.clear()
