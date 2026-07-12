"""错误信息脱敏 — 替换 API key/Bearer token 等敏感字段。"""

import re


def sanitize_error(msg: str) -> str:
    """脱敏错误信息：替换各种 API key 前缀和 URL 中的敏感参数。"""
    # Cover OpenAI (sk-), Anthropic (sk-ant-), and other common key formats
    msg = re.sub(r"sk-(?:ant-)?[a-zA-Z0-9_-]{8,}", "sk-***", msg)
    msg = re.sub(r"([?&](?:api[_-]?key|key|secret|token|authorization)=)[^&\s]+", r"\1***", msg, flags=re.IGNORECASE)
    # Strip Bearer tokens
    msg = re.sub(r"(Bearer\s+)[a-zA-Z0-9_.\-]{8,}", r"\1***", msg, flags=re.IGNORECASE)
    return msg
