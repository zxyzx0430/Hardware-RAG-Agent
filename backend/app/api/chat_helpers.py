"""Chat 路由辅助函数 — 从 chat_sse god function 抽出的职责。

包含：
- 附件处理（_process_attachments）
- 历史消息构建（_build_chat_history）
- system_prompt 构建（_build_system_prompt）
- Token 用量持久化（_record_token_usage）
- 相关度分级（_classify_relevance）

已迁移/废弃（spec rag-to-agent-tool-trigger）：
- _build_source_event / _build_citation → 迁移到
  src/agent/tools/groups/retrieval/search_docs.py (build_source_event_from_dict)
- _build_rag_context → 废弃（Agent 走工具路径，不再注入 RAG context 到 system prompt）
- pre-RAG 函数（_run_rag_retrieval / _rewrite_query_for_rag / _get_cached_rewrite /
  _set_cached_rewrite / _QUERY_REWRITE_SYSTEM / _REWRITE_CACHE*）已废弃删除
"""

import logging
from typing import Any

from app.api.common import get_db_ctx, DEFAULT_SYSTEM_PROMPT
from app.api.attachments import extract_attachment_text
from app.db.models import TokenUsage
from src.config.settings import settings
from src.llm.client import ChatMessage

logger = logging.getLogger(__name__)

# 附件文本最大字符数（超过则截断）
_MAX_ATTACHMENT_CHARS_DEFAULT = 8000


def _classify_relevance(score: float) -> str:
    """根据相关度分数返回等级标签。"""
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


async def _process_attachments(payload: Any) -> tuple[list[str], list[dict]]:
    """提取附件文本和图片，返回 (attachment_texts, image_parts)。

    - 图片附件 → image_parts（用于 multimodal LLM 输入）
    - 文本附件 → attachment_texts（拼入 system_prompt）
    - 文本超过 max_attachment_chars 则截断
    """
    attachment_texts: list[str] = []
    image_parts: list[dict] = []
    if not payload.attachments:
        return attachment_texts, image_parts

    logger.info(f"收到 {len(payload.attachments)} 个附件: {[a.get('name') for a in payload.attachments]}")
    for att in payload.attachments:
        att_name = att.get("name", "未知文件")
        att_type = att.get("type", "")
        att_content = att.get("content", "")
        if att_type.startswith("image/"):
            image_parts.append({
                "type": "image_url",
                "image_url": {"url": att_content},
            })
            continue
        try:
            text = await extract_attachment_text(att_name, att_type, att_content)
            if text:
                max_chars = settings.max_attachment_chars
                if len(text) > max_chars:
                    text = text[:max_chars] + f"\n\n[...内容已截断，共 {len(text)} 字符]"
                attachment_texts.append(f"[附件: {att_name}]\n{text}")
        except Exception as e:
            logger.warning(f"附件文本提取失败 {att_name}: {e}")
    return attachment_texts, image_parts


def _build_chat_history(msgs: Any) -> tuple[list[ChatMessage], str | list[dict]]:
    """从请求消息构建 (history, last_user_msg)。

    history = 最后一条 user 消息之前的所有消息。
    last_user_msg = 最后一条 user 消息的 content（str 或 multimodal list）。
    """
    last_user_idx = -1
    for i, m in enumerate(msgs):
        if m.role == "user":
            last_user_idx = i

    if last_user_idx < 0:
        return [], ""

    last_user_msg = msgs[last_user_idx].content if msgs[last_user_idx].content is not None else ""
    history = [
        ChatMessage(role=m.role, content=m.content if m.content is not None else "")
        for m in msgs[:last_user_idx]
    ]
    return history, last_user_msg


def _build_system_prompt(payload: Any, attachment_texts: list[str], rag_context: str) -> str:
    """构建 system_prompt：用户自定义 or 默认 + 附件 + RAG context。"""
    system_prompt = payload.system_prompt if payload.system_prompt is not None else DEFAULT_SYSTEM_PROMPT
    prompt_len = len(payload.system_prompt) if payload.system_prompt else 0
    logger.info(f"system_prompt received: length={prompt_len}, using_default={payload.system_prompt is None}")
    if attachment_texts:
        system_prompt += "\n\n## 用户附件\n以下内容来自用户上传的附件：\n" + "\n---\n".join(attachment_texts)
    if rag_context:
        system_prompt += f"\n\n## 参考文档片段\n以下内容来自知识库检索，请优先引用：\n{rag_context}"
    return system_prompt


def _build_source_event(r: Any, i: int) -> dict:
    """[DEPRECATED] 构建 RAG source 的 SSE payload。

    已迁移到 src.agent.tools.groups.retrieval.search_docs.build_source_event_from_dict
    （接受 dict 而非 FusedResult，适配 Agent 工具返回值格式）。

    保留此函数仅为向后兼容；如需调用，请改用迁移后的版本。
    """
    from src.agent.tools.groups.retrieval.search_docs import build_source_event_from_dict
    # Convert FusedResult to dict shape expected by build_source_event_from_dict.
    meta = getattr(r, "metadata", {}) or {}
    res_dict = {
        "id": f"src{i + 1}",
        "doc": getattr(r, "doc_id", "") or meta.get("doc_id", ""),
        "score": float(getattr(r, "score", 0.0) or 0.0),
        "content": getattr(r, "content", "") or "",
        "title": meta.get("title", "未知来源"),
        "chunk_index": meta.get("chunk_index", 0),
        "page_start": meta.get("page_start"),
        "page_end": meta.get("page_end"),
        "section_title": meta.get("section_title", ""),
        "source_url": meta.get("source_url", meta.get("source", "")),
        "category": meta.get("category", ""),
        "chunk_method": meta.get("chunk_method", ""),
        "kb_id": getattr(r, "kb_id", "") or "",
        "kb_name": getattr(r, "kb_name", "") or "",
        "small_chunk_id": meta.get("small_chunk_id", ""),
    }
    return build_source_event_from_dict(res_dict, i)


def _build_rag_context(results: list[Any]) -> str:
    """拼接 RAG 检索结果为 LLM context 文本。

    格式：
        [srcN] 标题 / 章节 (相关度: xx%, kb: xxx)
        <chunk content>

    [srcN] 编号与 SSE source 事件的 sid 对齐，供 LLM 引用。
    """
    return (
        "以下参考文档片段的 [srcN] 编号即为你答案中要标注的引用编号。每引用一处知识库内容，就在该句句尾标注对应的 [srcN]，不要整段只标一次。\n\n"
        + "\n\n".join(
            f"[src{i + 1}] {r.metadata.get('title', '未知')}"
            f"{' / ' + r.metadata.get('section_title', '') if r.metadata.get('section_title') else ''}"
            f" (相关度: {float(r.score):.1%}, kb: {r.kb_name})\n{r.content}"
            for i, r in enumerate(results)
        )
    )


def _record_token_usage(
    model: str,
    provider: str,
    session_id: str | None,
    usage_data: dict,
) -> None:
    """记录 Token 用量到数据库（失败不抛异常，仅 warning）。

    参数：
        usage_data: {prompt_tokens, completion_tokens, total_tokens}
    """
    try:
        with get_db_ctx() as db:
            record = TokenUsage(
                model=model,
                provider=provider or "",
                session_id=session_id or "",
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )
            db.add(record)
    except Exception as db_err:
        logger.warning(f"Failed to record token usage: {db_err}")
