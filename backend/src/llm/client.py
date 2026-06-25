"""
LLM 调用模块 — OpenAI-compatible API 封装。
支持同步/流式调用，多轮对话历史。

Week 1 对照：
- Day 2：HTTP 请求的抽象入口（真正发请求给模型）
- Day 3：LLM API 调通 + 动态 API Key + 流式输出
- Day 6：错误处理 + 重试
- Day 7：这一文件需要被 pytest 覆盖，见 `tests/test_llm.py`
"""
from typing import Optional, AsyncGenerator, List, Dict, Any
from dataclasses import dataclass, field
import asyncio
import logging

from openai import AsyncOpenAI, APIError, RateLimitError, AuthenticationError
from src.config.settings import settings


logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用级别的业务异常"""
    pass


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str | list[dict]  # str=纯文本, list=[{type,text|image_url},...]


@dataclass
class StreamChunk:
    """流式输出的单个 chunk，区分思考内容和正文内容。"""
    type: str  # "thinking" | "text" | "usage"
    content: str
    usage: Optional[Dict[str, int]] = None  # {"prompt_tokens", "completion_tokens", "total_tokens"}


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None
    reasoning: Optional[str] = None


@dataclass
class LLMClient:
    """
    OpenAI-compatible LLM 客户端。
    通过 .env 配置 api_key / base_url / model，运行时可通过 reload() 刷新。

    如果把项目比作一家公司，这个类就是"外联接口人"：
    它负责把我们内部的消息，翻译成模型 API 能听懂的请求。
    """

    api_key: str = field(default_factory=lambda: settings.llm_api_key)
    base_url: str = field(default_factory=lambda: settings.llm_base_url)
    model: str = field(default_factory=lambda: settings.llm_model)
    temperature: float = field(default_factory=lambda: settings.llm_temperature)
    max_tokens: int = field(default_factory=lambda: settings.llm_max_tokens)
    max_retries: int = 3
    retry_backoff: float = 0.5

    def __post_init__(self):
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120.0,
            )
        return self._client

    def reload_from_settings(self) -> None:
        """从全局配置刷新参数并重建客户端。"""
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        self._client = None  # 强制重建

    # 粗略估算：1 个中文字符 ≈ 1.5 token，1 个英文单词 ≈ 1.3 token
    # 这里用简单的字符数 / 2 作为粗略 token 估算
    @staticmethod
    def _estimate_tokens(content) -> int:
        """粗略估算文本的 token 数，兼容多模态消息格式。"""
        if isinstance(content, str):
            cn_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
            other_chars = len(content) - cn_chars
            return int(cn_chars * 1.5 + other_chars * 0.5)
        elif isinstance(content, list):
            # 多模态消息，估算文本部分
            total = 0
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text = part.get("text", "")
                        cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
                        other_chars = len(text) - cn_chars
                        total += int(cn_chars * 1.5 + other_chars * 0.5)
                    elif part.get("type") == "image_url":
                        total += 85  # image token estimate
            return total
        return 0

    async def _summarize_messages(self, messages: list[dict]) -> str:
        """将被截断的消息生成摘要，替代直接丢弃。"""
        if not messages:
            return ""
        summary_prompt = "请用2-3句话总结以下对话的关键信息，保留重要的技术细节、用户偏好和决策：\n\n"
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
            summary_prompt += f"[{role}]: {content[:500]}\n"

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个对话摘要助手。"},
                    {"role": "user", "content": summary_prompt},
                ],
                max_tokens=300,
                temperature=0.3,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"摘要生成失败: {e}")
            return f"[早期对话摘要不可用，共 {len(messages)} 条消息被截断]"

    def _build_messages(
        self,
        user_message: str | list[dict],
        system_prompt: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
    ) -> tuple[List[Dict[str, str]], list[dict]]:
        """构建消息列表，包含上下文压缩：保留最近的消息，截断过长的早期历史。

        Returns:
            (messages, truncated_raw) — 构建好的消息列表和被截断的原始消息（用于异步摘要生成）。
        """
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        truncated_raw: list[dict] = []

        if history:
            # 上下文窗口管理：保留 system_prompt + 最近的历史消息 + 用户消息
            # 预留 max_tokens 给模型回复，剩余空间分配给历史
            system_tokens = self._estimate_tokens(system_prompt) if system_prompt else 0
            user_tokens = self._estimate_tokens(user_message)
            reply_budget = self.max_tokens  # 预留给回复
            # 总预算 = 128K（大多数现代模型支持 128K），减去 system + user + reply
            context_window = 128000
            history_budget = context_window - system_tokens - user_tokens - reply_budget

            # 从最新消息开始倒序累加，直到超出预算
            # Use index-based tracking to avoid value-equality bugs with duplicate messages
            history_messages: List[Dict[str, str]] = []
            used_tokens = 0
            included_indices: set[int] = set()
            for i in range(len(history) - 1, -1, -1):
                msg = history[i]
                msg_tokens = self._estimate_tokens(msg.content) + 4  # +4 for role overhead
                if used_tokens + msg_tokens > history_budget:
                    # Collect truncated early messages by index (not value equality)
                    truncated_raw = [
                        {"role": history[j].role, "content": history[j].content}
                        for j in range(len(history)) if j not in included_indices
                    ]
                    # 插入截断提示占位，后续由摘要替换
                    history_messages.insert(0, {
                        "role": "system",
                        "content": "[早期对话已省略以节省上下文空间]"
                    })
                    break
                history_messages.insert(0, {"role": msg.role, "content": msg.content})
                included_indices.add(i)
                used_tokens += msg_tokens

            messages.extend(history_messages)

        messages.append({"role": "user", "content": user_message})
        return messages, truncated_raw

    async def _with_retries(self, operation, *, stream: bool = False):
        """
        为瞬态错误提供最小重试能力，满足 Week 1 的错误处理目标。

        对应 Week 1 Day 6：
        这里解决的是"网络抖一下就全挂掉"的问题。
        仅重试可恢复的异常（网络、速率限制、5xx），编程错误直接抛出。
        """
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return await operation()
            except (KeyError, AttributeError, TypeError, ValueError) as error:
                # 编程错误，直接抛出
                raise
            except AuthenticationError as error:
                logger.warning(f"AuthenticationError (attempt {attempt + 1}): {error.message}")
                raise LLMError(f"API Key 无效：{error.message}") from error
            except RateLimitError as error:
                last_error = error
                logger.warning(f"RateLimitError (attempt {attempt + 1}): {error.message}")
                if attempt >= self.max_retries:
                    raise LLMError(f"请求频率超限：{error.message}") from error
                wait = min(2 ** attempt, 8)
                logger.warning(f"重试 {attempt + 1}/{self.max_retries}: {error}, 等待 {wait}s")
                await asyncio.sleep(wait)
            except APIError as error:
                last_error = error
                # Use status_code attribute instead of parsing error string
                status_code = getattr(error, 'status_code', None) or getattr(error, 'code', None)
                is_5xx = isinstance(status_code, int) and 500 <= status_code < 600
                is_timeout = isinstance(status_code, int) and status_code == 408
                # Also check error message for timeout keywords (some providers return 400/500 with timeout message)
                err_msg_lower = (getattr(error, 'message', '') or str(error)).lower()
                has_timeout_msg = 'timeout' in err_msg_lower or 'timed out' in err_msg_lower
                if not (is_5xx or is_timeout or has_timeout_msg):
                    raise LLMError(f"API 错误：{error.message}") from error
                logger.warning(f"APIError {status_code} (attempt {attempt + 1}): {error.message}")
                if attempt >= self.max_retries:
                    raise LLMError(f"API 错误：{error.message}") from error
                wait = min(2 ** attempt, 8)
                logger.warning(f"重试 {attempt + 1}/{self.max_retries}: {error}, 等待 {wait}s")
                await asyncio.sleep(wait)
            except (ConnectionError, TimeoutError, OSError) as error:
                last_error = error
                logger.warning(f"网络错误 (attempt {attempt + 1}): {error}")
                if attempt >= self.max_retries:
                    raise LLMError(f"网络错误：{error}") from error
                wait = min(2 ** attempt, 8)
                logger.warning(f"重试 {attempt + 1}/{self.max_retries}: {error}, 等待 {wait}s")
                await asyncio.sleep(wait)
            except Exception as error:
                # 其他未知异常：检查是否可重试
                error_str = str(error).lower()
                is_retryable = (
                    "rate" in error_str or "429" in str(error) or
                    "timeout" in error_str or "connection" in error_str or
                    ("5" in str(error)[:1] and any(c.isdigit() and int(c) >= 5 for c in str(error)[:4] if c.isdigit()))
                )
                if not is_retryable:
                    raise
                last_error = error
                logger.warning(f"可重试异常 (attempt {attempt + 1}): {error}")
                if attempt >= self.max_retries:
                    break
                wait = min(2 ** attempt, 8)
                logger.warning(f"重试 {attempt + 1}/{self.max_retries}: {error}, 等待 {wait}s")
                await asyncio.sleep(wait)
        raise LLMError(f"重试 {self.max_retries} 次后仍失败: {last_error}") from last_error

    async def chat(
        self,
        user_message: str | list[dict],
        system_prompt: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
        timeout: float = 60.0,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> LLMResponse:
        """
        非流式对话调用。
        超时通过 asyncio.wait_for 实现（符合核心架构约束）。

        对应 Week 1 Day 3：
        最基础的目标是"先拿到完整回复"，哪怕还不是流式。
        """
        messages, truncated_raw = self._build_messages(user_message, system_prompt, history)
        # 异步生成摘要并替换占位符（不阻塞主流程）
        if truncated_raw:
            try:
                summary = await self._summarize_messages(truncated_raw)
                if summary:
                    for i, m in enumerate(messages):
                        if m.get("role") == "system" and m.get("content") == "[早期对话已省略以节省上下文空间]":
                            messages[i] = {"role": "system", "content": f"[早期对话摘要] {summary}"}
                            break
            except Exception:
                pass  # 保留原始占位符

        # Web 场景下允许每个请求临时覆盖鉴权信息；CLI 场景则复用全局 settings。
        runtime_client = self.client if not (api_key or base_url) else AsyncOpenAI(
            api_key=api_key or self.api_key,
            base_url=base_url or self.base_url,
            timeout=120.0,
        )

        async def _call() -> LLMResponse:
            # 真正的模型调用都收口在这里，外层负责超时和重试。
            resp = await runtime_client.chat.completions.create(
                    model=model or self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    stream=False,
                )
            message = resp.choices[0].message
            reasoning = None
            if hasattr(message, "reasoning_content") and message.reasoning_content:
                reasoning = message.reasoning_content
            elif hasattr(message, "reasoning") and message.reasoning:
                reasoning = message.reasoning
            elif hasattr(message, "thinking") and message.thinking:
                reasoning = message.thinking
            return LLMResponse(
                content=message.content or "",
                model=resp.model,
                usage={
                    "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                    "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                    "total_tokens": resp.usage.total_tokens if resp.usage else 0,
                } if resp.usage else None,
                reasoning=reasoning,
            )

        return await asyncio.wait_for(self._with_retries(_call), timeout=timeout)

    async def chat_stream(
        self,
        user_message: str | list[dict],
        system_prompt: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
        timeout: float = 300.0,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        流式对话调用，逐 token 产出内容，区分思考(reasoning)和正文(text)。

        支持 DeepSeek-R1/QwQ 等模型的 reasoning_content 字段，
        以及 OpenAI o1/o3 系列的 reasoning 字段。
        """
        messages, truncated_raw = self._build_messages(user_message, system_prompt, history)
        # 异步生成摘要并替换占位符（不阻塞主流程）
        if truncated_raw:
            try:
                summary = await self._summarize_messages(truncated_raw)
                if summary:
                    for i, m in enumerate(messages):
                        if m.get("role") == "system" and m.get("content") == "[早期对话已省略以节省上下文空间]":
                            messages[i] = {"role": "system", "content": f"[早期对话摘要] {summary}"}
                            break
            except Exception:
                pass  # 保留原始占位符

        # SSE / CLI 流式输出也支持运行时覆盖请求头，避免把用户 key 写死在服务端。
        runtime_client = self.client if not (api_key or base_url) else AsyncOpenAI(
            api_key=api_key or self.api_key,
            base_url=base_url or self.base_url,
            timeout=120.0,
        )

        async def _stream():
            try:
                async def _create_stream():
                    # 构建请求参数
                    create_kwargs = {
                        "model": model or self.model,
                        "messages": messages,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    }
                    # Ollama 兼容：启用 think 模式（推理模型需要）
                    # 检测方式：显式 provider 优先，再兜底 base_url
                    resolved_provider = (provider or "").lower()
                    base = (base_url or self.base_url or "").lower()
                    if resolved_provider == "ollama" or "ollama" in base or "11434" in base:
                        create_kwargs["extra_body"] = {"think": True}
                    return await runtime_client.chat.completions.create(**create_kwargs)

                stream = await self._with_retries(_create_stream, stream=True)
                logger.info(f"Stream created successfully, consuming chunks...")
                async for chunk in stream:
                    # 先处理 usage（可能和 choices 同时存在，不要直接跳过）
                    if chunk.usage:
                        yield StreamChunk(
                            type="usage",
                            content="",
                            usage={
                                "prompt_tokens": chunk.usage.prompt_tokens or 0,
                                "completion_tokens": chunk.usage.completion_tokens or 0,
                                "total_tokens": chunk.usage.total_tokens or 0,
                            },
                        )

                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue
                    # 1. 优先处理 reasoning_content（DeepSeek-R1 / QwQ / Ollama OpenAI 兼容）
                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        yield StreamChunk(type="thinking", content=delta.reasoning_content)
                    # 2. 处理 reasoning 字段（部分 OpenAI 兼容 API）
                    elif hasattr(delta, 'reasoning') and delta.reasoning:
                        yield StreamChunk(type="thinking", content=delta.reasoning)
                    # 3. 处理 thinking 字段（Ollama OpenAI 兼容 API）
                    elif hasattr(delta, 'thinking') and delta.thinking:
                        yield StreamChunk(type="thinking", content=delta.thinking)
                    # 4. 正文内容
                    if delta.content:
                        yield StreamChunk(type="text", content=delta.content)
            except RateLimitError as error:
                logger.warning(f"chat_stream RateLimitError: {error.message}")
                raise LLMError(f"请求频率超限：{error.message}")
            except AuthenticationError as error:
                logger.warning(f"chat_stream AuthenticationError: {error.message}")
                raise LLMError(f"API Key 无效：{error.message}")
            except APIError as error:
                logger.warning(f"chat_stream APIError: {error.message}")
                raise LLMError(f"API 错误：{error.message}")
            except Exception as e:
                logger.exception(f"chat_stream unexpected error: {e}")
                raise LLMError(f"流式请求出错：{e}")

        async def _timeout_wrapper():
            gen = _stream()
            try:
                while True:
                    try:
                        val = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
                        yield val
                    except StopAsyncIteration:
                        break
            finally:
                # Ensure the inner generator is properly closed to release HTTP resources
                await gen.aclose()

        async for chunk in _timeout_wrapper():
            yield chunk

    async def list_models(
        self,
        timeout: float = 10.0,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> List[str]:
        """
        获取可用的模型列表（用于前端模型选择）。

        这个能力是 Day 3 动态鉴权的延伸：不只是聊天能换 Key，
        连模型列表也必须跟着用户自己的 Key 走。
        """
        # 模型列表和聊天请求一样，也要允许前端带自己的 key / base url。
        runtime_client = self.client if not (api_key or base_url) else AsyncOpenAI(
            api_key=api_key or self.api_key,
            base_url=base_url or self.base_url,
        )

        async def _list():
            resp = await runtime_client.models.list()
            return sorted([m.id for m in resp.data])

        try:
            return await asyncio.wait_for(self._with_retries(_list), timeout=timeout)
        except Exception as e:
            raise LLMError(f"无法获取模型列表：{e}") from e
