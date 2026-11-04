"""Model registry: model name -> context window mapping."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# Known model context windows (in tokens)
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    # DeepSeek
    "deepseek-v4": 256000,
    "deepseek-v4-flash": 256000,
    "deepseek-chat": 65536,
    "deepseek-reasoner": 65536,
    "deepseek-r1": 65536,
    # Qwen
    "qwen3-235b": 256000,
    "qwen-plus": 131072,
    "qwen-max": 32768,
    # Claude
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    # Others / fallback
}

DEFAULT_CONTEXT_WINDOW = 32768
CONSERVATIVE_CONTEXT_WINDOW = 32768
CONTEXT_WINDOW_HARD_CAP = 1048576


def get_registered_context_window(model: str | None) -> int | None:
    """Return a registered context window, or None for an unknown model."""
    if not model:
        return None
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]
    stripped = model.split("/")[-1] if "/" in model else model
    if stripped in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[stripped]
    for key, value in MODEL_CONTEXT_WINDOWS.items():
        if key in stripped or stripped in key:
            return value
    return None


def resolve_context_budget(
    model: str | None,
    context_window_override: int | None = None,
    *,
    hard_cap: int = CONTEXT_WINDOW_HARD_CAP,
) -> tuple[int, str]:
    """Resolve a context window and whether it came from a trusted source.

    A registered model uses its real window. An explicit session override may
    tighten that window. An unknown model is still usable: an explicit override
    is honored up to the hard cap, otherwise a conservative default is used
    instead of silently assuming a large provider window or failing closed.
    """
    registered = get_registered_context_window(model)
    override = (
        context_window_override
        if type(context_window_override) is int and context_window_override > 0
        else None
    )
    if registered is not None:
        window = registered
        source = "registered"
        if override is not None:
            window = min(window, override)
            source = "explicit_override"
    elif override is not None:
        window = override
        source = "explicit_override"
    else:
        window = CONSERVATIVE_CONTEXT_WINDOW
        source = "conservative_default"
    if type(hard_cap) is int and hard_cap > 0:
        window = min(window, hard_cap)
    return window, source


def get_context_window(model: str | None) -> int:
    """Get a conservative context window for a model.

    Unknown models use the conservative default rather than an optimistic
    provider window. Callers that need to distinguish the source should use
    ``resolve_context_budget``.
    """
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    registered = get_registered_context_window(model)
    if registered is not None:
        return registered
    logger.info(
        "[model_registry] Unknown model '%s', using conservative window %s",
        model, CONSERVATIVE_CONTEXT_WINDOW,
    )
    return CONSERVATIVE_CONTEXT_WINDOW


# Known model max output tokens (completion tokens)
MODEL_MAX_TOKENS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "gpt-4.1": 65536,
    "gpt-4.1-mini": 65536,
    # DeepSeek
    "deepseek-v4": 8192,
    "deepseek-v4-flash": 8192,
    "deepseek-chat": 8192,
    "deepseek-reasoner": 8192,
    "deepseek-r1": 8192,
    # Qwen
    "qwen3-235b": 8192,
    "qwen-plus": 8192,
    "qwen-max": 8192,
    # Claude
    "claude-3-5-sonnet": 8192,
    "claude-3-5-haiku": 8192,
}

DEFAULT_MAX_TOKENS = 4096


def _registered_model_value(model: str | None, values: dict[str, int]) -> int | None:
    """Return a registry value for an exact model name or exact provider suffix."""
    if type(model) is not str or not model:
        return None
    registered_name = model.rsplit("/", 1)[-1]
    return values.get(registered_name)


def get_registered_context_window(model: str | None) -> int | None:
    """Return only an exactly registered context window, without a default/fuzzy match."""
    return _registered_model_value(model, MODEL_CONTEXT_WINDOWS)


def get_registered_max_tokens(model: str | None) -> int | None:
    """Return only an exactly registered output-token cap, without a default/fuzzy match."""
    return _registered_model_value(model, MODEL_MAX_TOKENS)

# Known model types: "chat" | "embedding" | "reranker"
MODEL_TYPES: dict[str, str] = {
    # Chat models
    "gpt-4o": "chat",
    "gpt-4o-mini": "chat",
    "gpt-4.1": "chat",
    "gpt-4.1-mini": "chat",
    "deepseek-v4": "chat",
    "deepseek-v4-flash": "chat",
    "deepseek-chat": "chat",
    "deepseek-reasoner": "chat",
    "deepseek-r1": "chat",
    "qwen3-235b": "chat",
    "qwen-plus": "chat",
    "qwen-max": "chat",
    "claude-3-5-sonnet": "chat",
    "claude-3-5-haiku": "chat",
    # Embedding models
    "text-embedding-3-small": "embedding",
    "text-embedding-3-large": "embedding",
    "text-embedding-v4": "embedding",
    # Reranker models
    "bge-reranker-base": "reranker",
    "bge-reranker-large": "reranker",
}

DEFAULT_MODEL_TYPE = "chat"


def get_max_tokens(model: str | None) -> int:
    """Get max output tokens for a model. Returns DEFAULT_MAX_TOKENS if unknown."""
    if not model:
        return DEFAULT_MAX_TOKENS
    # Try exact match first
    if model in MODEL_MAX_TOKENS:
        return MODEL_MAX_TOKENS[model]
    # Try prefix match (e.g. "oc/deepseek-v4-flash" -> "deepseek-v4-flash")
    # Strip provider prefix like "oc/", "openai/", "anthropic/"
    stripped = model.split("/")[-1] if "/" in model else model
    if stripped in MODEL_MAX_TOKENS:
        return MODEL_MAX_TOKENS[stripped]
    # Try partial match (model name contains a known key)
    for key, val in MODEL_MAX_TOKENS.items():
        if key in stripped or stripped in key:
            return val
    logger.debug(f"[model_registry] Unknown model '{model}', using default max_tokens {DEFAULT_MAX_TOKENS}")
    return DEFAULT_MAX_TOKENS


def get_model_type(model: str | None) -> str:
    """Get model type ("chat" | "embedding" | "reranker"). Returns DEFAULT_MODEL_TYPE if unknown."""
    if not model:
        return DEFAULT_MODEL_TYPE
    # Try exact match first
    if model in MODEL_TYPES:
        return MODEL_TYPES[model]
    # Try prefix match (e.g. "oc/deepseek-v4-flash" -> "deepseek-v4-flash")
    stripped = model.split("/")[-1] if "/" in model else model
    if stripped in MODEL_TYPES:
        return MODEL_TYPES[stripped]
    # Try partial match (model name contains a known key)
    for key, val in MODEL_TYPES.items():
        if key in stripped or stripped in key:
            return val
    logger.debug(f"[model_registry] Unknown model '{model}', using default type '{DEFAULT_MODEL_TYPE}'")
    return DEFAULT_MODEL_TYPE
