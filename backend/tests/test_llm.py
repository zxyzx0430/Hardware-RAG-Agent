"""
测试 LLM 客户端模块。

对应原计划：
- Day 3：验证 chat / chat_stream / 动态参数
- Day 6：验证错误处理与重试
- Day 7：把 LLM 核心能力纳入 pytest
"""
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from openai import APIError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.client import LLMClient, ChatMessage, LLMResponse, StreamChunk, LLMError
from src.config.settings import settings


class TestChatMessage:
    """测试 ChatMessage 数据类。"""

    def test_create_message(self):
        msg = ChatMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_system_message(self):
        msg = ChatMessage(role="system", content="you are a helper")
        assert msg.role == "system"


class TestLLMResponse:
    """测试 LLMResponse 数据类。"""

    def test_create_response(self):
        resp = LLMResponse(content="hello back", model="gpt-4o")
        assert resp.content == "hello back"
        assert resp.model == "gpt-4o"

    def test_response_with_usage(self):
        resp = LLMResponse(
            content="hello", model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        assert resp.usage["total_tokens"] == 15


class TestLLMClient:
    """测试 LLMClient（mock OpenAI API）。"""

    @pytest.fixture
    def client(self):
        return LLMClient(
            api_key="test-key",
            base_url="https://test.api.com/v1",
            model="gpt-4o-mini",
        )

    def _make_chat_create_mock(self, content, model="gpt-4o-mini", usage_tokens=None):
        """辅助：构造 chat.completions.create 的假返回。"""
        mock_usage = MagicMock()
        if usage_tokens:
            mock_usage.prompt_tokens = usage_tokens[0]
            mock_usage.completion_tokens = usage_tokens[1]
            mock_usage.total_tokens = usage_tokens[2]
        else:
            mock_usage.prompt_tokens = 0
            mock_usage.completion_tokens = 0
            mock_usage.total_tokens = 0

        inner = MagicMock()
        inner.choices = [MagicMock()]
        inner.choices[0].message.content = content
        inner.model = model
        inner.usage = mock_usage
        return inner

    @pytest.mark.asyncio
    async def test_chat_success(self, client):
        """测试非流式对话成功。"""
        fake = self._make_chat_create_mock(
            content="这是测试回复",
            model="gpt-4o-mini-2024-07-18",
            usage_tokens=(20, 10, 30),
        )
        with patch.object(client.client.chat.completions, "create", new=AsyncMock(return_value=fake)):
            resp = await client.chat(user_message="你好")
            assert resp.content == "这是测试回复"
            assert resp.model == "gpt-4o-mini-2024-07-18"
            assert resp.usage["total_tokens"] == 30

    @pytest.mark.asyncio
    async def test_chat_with_history(self, client):
        """测试带历史上下文的对话。"""
        fake = self._make_chat_create_mock(
            content="基于历史的回复",
            usage_tokens=(50, 10, 60),
        )
        history = [
            ChatMessage(role="user", content="第一个问题"),
            ChatMessage(role="assistant", content="第一个回答"),
        ]

        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=fake),
        ) as mock_create:
            resp = await client.chat(
                user_message="第二个问题",
                system_prompt="你是助手",
                history=history,
            )
            call_kwargs = mock_create.call_args[1]
            msgs = call_kwargs["messages"]
            assert msgs[0]["role"] == "system"
            assert msgs[1]["role"] == "user"
            assert msgs[1]["content"] == "第一个问题"
            assert msgs[2]["role"] == "assistant"
            assert msgs[2]["content"] == "第一个回答"
            assert msgs[3]["role"] == "user"
            assert msgs[3]["content"] == "第二个问题"

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self, client):
        """测试系统提示词被正确传入。"""
        fake = self._make_chat_create_mock(content="回复")
        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=fake),
        ) as mock_create:
            await client.chat(
                user_message="hi",
                system_prompt="你是硬件专家",
            )
            msgs = mock_create.call_args[1]["messages"]
            assert msgs[0]["role"] == "system"
            assert msgs[0]["content"] == "你是硬件专家"

    @pytest.mark.asyncio
    async def test_chat_stream(self, client):
        """测试流式对话。"""
        chunks = ["hello ", "how ", "are ", "you?"]

        async def _stream_gen():
            for text in chunks:
                chunk = MagicMock()
                chunk.usage = None
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = text
                chunk.choices[0].delta.reasoning_content = None
                chunk.choices[0].delta.reasoning = None
                chunk.choices[0].delta.thinking = None
                yield chunk

        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=_stream_gen()),
        ):
            collected = []
            async for token in client.chat_stream(user_message="hi"):
                collected.append(token.content)
            assert "".join(collected) == "hello how are you?"

    @pytest.mark.asyncio
    async def test_chat_stream_empty_response(self, client):
        """测试流式空响应。"""

        async def _empty_gen():
            chunk = MagicMock()
            chunk.usage = None
            chunk.choices = []
            yield chunk

        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=_empty_gen()),
        ):
            collected = []
            async for token in client.chat_stream(user_message="hi"):
                collected.append(token)
            assert len(collected) == 0

    @pytest.mark.asyncio
    async def test_reload_from_settings(self, client):
        """测试 reload 刷新配置。"""
        settings.llm_base_url = "https://reloaded.api.com/v1"
        settings.llm_api_key = "new-key"
        settings.llm_model = "gpt-4"

        client.reload_from_settings()

        assert client.api_key == "new-key"
        assert client.base_url == "https://reloaded.api.com/v1"
        assert client.model == "gpt-4"
        assert client._client is None  # 客户端被重建

    @pytest.mark.asyncio
    async def test_list_models(self, client):
        """测试获取模型列表。"""
        mock_model_1 = MagicMock()
        mock_model_1.id = "gpt-4o"
        mock_model_2 = MagicMock()
        mock_model_2.id = "gpt-4o-mini"
        mock_response = MagicMock()
        mock_response.data = [mock_model_1, mock_model_2]

        with patch.object(client.client.models, "list", new=AsyncMock(return_value=mock_response)):
            models = await client.list_models()
            assert "gpt-4o" in models
            assert "gpt-4o-mini" in models

    @pytest.mark.asyncio
    async def test_chat_retries_after_api_error(self, client):
        """瞬态 API 错误后应自动重试。"""
        fake = self._make_chat_create_mock(content="第二次成功")
        transient_error = APIError("temporary failure", request=MagicMock(), body={})

        with patch.object(
            client.client.chat.completions,
            "create",
            new=AsyncMock(side_effect=[transient_error, fake]),
        ) as mock_create:
            response = await client.chat(user_message="retry please")
            assert response.content == "第二次成功"
            assert mock_create.await_count == 2

    @pytest.mark.asyncio
    async def test_list_models_uses_runtime_credentials(self, client):
        """模型列表支持运行时传入请求头鉴权。"""
        mock_model = MagicMock()
        mock_model.id = "runtime-model"
        mock_response = MagicMock()
        mock_response.data = [mock_model]

        fake_runtime_client = MagicMock()
        fake_runtime_client.models.list = AsyncMock(return_value=mock_response)

        with patch("src.llm.client.AsyncOpenAI", return_value=fake_runtime_client) as mock_openai:
            models = await client.list_models(
                api_key="runtime-key",
                base_url="https://runtime.example/v1",
            )

        assert models == ["runtime-model"]
        mock_openai.assert_called_once_with(
            api_key="runtime-key",
            base_url="https://runtime.example/v1",
        )

    def test_source_uses_asyncio_wait_for(self):
        """源码评审验证：chat() 使用 asyncio.wait_for 实现超时。"""
        import inspect
        from src.llm import client as client_module
        source = inspect.getsource(client_module.LLMClient.chat)
        assert "asyncio.wait_for" in source, (
            "LLMClient.chat() 必须使用 asyncio.wait_for 实现超时"
        )

    @pytest.mark.asyncio
    async def test_chat_stream_extracts_reasoning_content(self, client):
        """流式 chunk 应能提取 reasoning_content / reasoning / thinking。"""
        async def _stream_gen():
            for attr, content in [
                ("reasoning_content", "步骤1"),
                ("reasoning", "步骤2"),
                ("thinking", "步骤3"),
            ]:
                chunk = MagicMock()
                chunk.usage = None
                chunk.choices = [MagicMock()]
                for a in ("reasoning_content", "reasoning", "thinking", "content"):
                    setattr(chunk.choices[0].delta, a, None)
                setattr(chunk.choices[0].delta, attr, content)
                yield chunk

        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=_stream_gen()),
        ):
            collected = []
            async for token in client.chat_stream(user_message="hi"):
                collected.append(token)

        assert len(collected) == 3
        for token in collected:
            assert token.type == "thinking"
        assert [t.content for t in collected] == ["步骤1", "步骤2", "步骤3"]

    @pytest.mark.asyncio
    async def test_chat_stream_usage_chunk_keeps_text(self, client):
        """usage chunk 不应丢失同 chunk 中的正文内容。"""
        async def _stream_gen():
            chunk = MagicMock()
            usage = MagicMock()
            usage.prompt_tokens = 10
            usage.completion_tokens = 5
            usage.total_tokens = 15
            chunk.usage = usage
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "正文"
            chunk.choices[0].delta.reasoning_content = None
            chunk.choices[0].delta.reasoning = None
            chunk.choices[0].delta.thinking = None
            yield chunk

        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=_stream_gen()),
        ):
            collected = []
            async for token in client.chat_stream(user_message="hi"):
                collected.append(token)

        assert len(collected) == 2
        assert collected[0].type == "usage"
        assert collected[0].usage == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
        assert collected[1].type == "text"
        assert collected[1].content == "正文"

    @pytest.mark.asyncio
    async def test_chat_returns_reasoning_field(self, client):
        """非流式 chat 应返回 reasoning 字段。"""
        fake = self._make_chat_create_mock(content="回复")
        fake.choices[0].message.reasoning_content = "思考过程"
        fake.choices[0].message.reasoning = None
        fake.choices[0].message.thinking = None

        with patch.object(
            client.client.chat.completions, "create",
            new=AsyncMock(return_value=fake),
        ):
            resp = await client.chat(user_message="hi")
            assert resp.reasoning == "思考过程"

    @pytest.mark.asyncio
    async def test_with_retries_raises_llm_error(self, client):
        """_with_retries 最终异常应被抛出为 LLMError。"""
        async def _failing_operation():
            raise ValueError("持续失败")

        with pytest.raises(LLMError) as exc_info:
            await client._with_retries(_failing_operation)

        assert "持续失败" in str(exc_info.value)
