"""多模态消息工具 — 统一处理 list[dict] 格式的消息内容。

消除 chat_routes.py 与 client.py 中重复的多模态文本抽取逻辑。
"""


def extract_text_from_multimodal(content) -> str:
    """从多模态消息内容中抽取纯文本。

    支持两种输入：
    - str: 直接返回
    - list[dict]: 拼接所有 {type: "text", text: ...} 的文本部分

    用于：
    - RAG 检索前从 last_user_msg 抽取查询文本
    - 查询改写时从 history 抽取上下文
    - 消息摘要时从多模态消息抽取文本
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content) if content is not None else ""


def estimate_multimodal_tokens(content) -> int:
    """估算多模态消息的 token 数（兼容文本与图片）。

    文本：中文 1.5 token/字，英文 0.5 token/字
    图片：固定 85 token（OpenAI 视觉模型典型值）
    """
    if isinstance(content, str):
        cn_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
        other_chars = len(content) - cn_chars
        return int(cn_chars * 1.5 + other_chars * 0.5)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text = part.get("text", "")
                    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
                    other_chars = len(text) - cn_chars
                    total += int(cn_chars * 1.5 + other_chars * 0.5)
                elif part.get("type") == "image_url":
                    total += 85
        return total
    return 0
