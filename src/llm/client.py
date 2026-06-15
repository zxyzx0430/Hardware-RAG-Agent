"""
LLM 调用模块 — OpenAI-compatible API 封装。
支持同步/流式调用，多轮对话历史。
"""
from typing import Optional, AsyncGenerator, List, Dict, Any
from dataclasses import dataclass, field

from openai import AsyncOpenAI, APIError, RateLimitError
from src.config.settings import settings


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None


@dataclass
class LLMClient:
    """
    OpenAI-compatible LLM 客户端。
    通过 .env 配置 api_key / base_url / model，运行时可通过 reload() 刷新。
    """

    api_key: str = field(default_factory=lambda: settings.llm_api_key)
    base_url: str = field(default_factory=lambda: settings.llm_base_url)
    model: str = field(default_factory=lambda: settings.llm_model)
    temperature: float = field(default_factory=lambda: settings.llm_temperature)
    max_tokens: int = field(default_factory=lambda: settings.llm_max_tokens)

    def __post_init__(self):
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
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

    def _build_messages(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_message})
        return messages

    async def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
        timeout: float = 60.0,
    ) -> LLMResponse:
        """
        非流式对话调用。
        超时通过 asyncio.wait_for 实现（符合核心架构约束）。
        """
        import asyncio

        messages = self._build_messages(user_message, system_prompt, history)

        async def _call() -> LLMResponse:
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    stream=False,
                )
                return LLMResponse(
                    content=resp.choices[0].message.content or "",
                    model=resp.model,
                    usage={
                        "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                        "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                        "total_tokens": resp.usage.total_tokens if resp.usage else 0,
                    } if resp.usage else None,
                )
            except RateLimitError as e:
                return LLMResponse(
                    content=f"⚠️ 请求频率超限：{e.message}",
                    model=self.model,
                )
            except APIError as e:
                return LLMResponse(
                    content=f"⚠️ API 错误：{e.message}",
                    model=self.model,
                )

        return await asyncio.wait_for(_call(), timeout=timeout)

    async def chat_stream(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
        timeout: float = 60.0,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话调用，逐 token 产出内容。
        """
        import asyncio

        messages = self._build_messages(user_message, system_prompt, history)

        async def _stream():
            try:
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield delta.content
            except Exception as e:
                yield f"\n⚠️ 流式请求出错：{e}"

        async def _timeout_wrapper():
            gen = _stream()
            while True:
                try:
                    val = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
                    yield val
                except StopAsyncIteration:
                    break

        async for token in _timeout_wrapper():
            yield token

    async def list_models(self, timeout: float = 10.0) -> List[str]:
        """获取可用的模型列表（用于前端模型选择）。"""
        import asyncio

        async def _list():
            try:
                resp = await self.client.models.list()
                return sorted([m.id for m in resp.data])
            except Exception as e:
                return [f"无法获取模型列表：{e}"]

        return await asyncio.wait_for(_list(), timeout=timeout)
