"""
测试 LLMClient.list_models 的错误处理。
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.client import LLMClient, LLMError


@pytest.mark.asyncio
async def test_list_models_raises_on_failure():
    """模型列表获取失败时应抛出 LLMError，而不是返回错误字符串。"""
    client = LLMClient(api_key="test-key", base_url="https://test.api.com/v1")

    with patch.object(
        client.client.models,
        "list",
        new=AsyncMock(side_effect=RuntimeError("连接超时")),
    ):
        with pytest.raises(LLMError, match="无法获取模型列表"):
            await client.list_models()


@pytest.mark.asyncio
async def test_list_models_returns_sorted_ids_on_success():
    """模型列表获取成功时返回排序后的模型 ID 列表。"""
    client = LLMClient(api_key="test-key", base_url="https://test.api.com/v1")

    mock_model_b = MagicMock()
    mock_model_b.id = "gpt-4o-mini"
    mock_model_a = MagicMock()
    mock_model_a.id = "gpt-4o"
    mock_response = MagicMock()
    mock_response.data = [mock_model_b, mock_model_a]

    with patch.object(
        client.client.models,
        "list",
        new=AsyncMock(return_value=mock_response),
    ):
        models = await client.list_models()

    assert models == ["gpt-4o", "gpt-4o-mini"]
