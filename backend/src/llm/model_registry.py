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
    # Qwen
    "qwen3-235b": 256000,
    "qwen-plus": 131072,
    "qwen-max": 32768,
    # Claude
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    # Others / fallback
}

DEFAULT_CONTEXT_WINDOW = 128000


def get_context_window(model: str | None) -> int:
    """Get context window size for a model. Returns DEFAULT_CONTEXT_WINDOW if unknown."""
    if not model:
        return DEFAULT_CONTEXT_WINDOW
    # Try exact match first
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]
    # Try prefix match (e.g. "oc/deepseek-v4-flash" -> "deepseek-v4-flash")
    # Strip provider prefix like "oc/", "openai/", "anthropic/"
    stripped = model.split("/")[-1] if "/" in model else model
    if stripped in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[stripped]
    # Try partial match (model name contains a known key)
    for key, val in MODEL_CONTEXT_WINDOWS.items():
        if key in stripped or stripped in key:
            return val
    logger.debug(f"[model_registry] Unknown model '{model}', using default {DEFAULT_CONTEXT_WINDOW}")
    return DEFAULT_CONTEXT_WINDOW


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
    # Qwen
    "qwen3-235b": 8192,
    "qwen-plus": 8192,
    "qwen-max": 8192,
    # Claude
    "claude-3-5-sonnet": 8192,
    "claude-3-5-haiku": 8192,
}

DEFAULT_MAX_TOKENS = 4096

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
