"""
测试 /api/chat SSE 路由。

使用 TestClient + unittest.mock 模拟 LLMClient.chat_stream，
验证 SSE 事件序列与错误处理协议。
"""
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import create_app
from src.llm.client import StreamChunk


@pytest.fixture
def client():
    return TestClient(create_app())


def _parse_sse(response_text):
    """将 SSE 响应文本解析为 [{type, ...}, ...]。"""
    events = []
    for block in response_text.strip().split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: "):
                payload = line[len("data: "):]
                events.append(json.loads(payload))
    return events


class TestChatSSE:
    """测试 /api/chat SSE 事件流。"""

    def test_sse_event_sequence_thinking_text_done(self, client):
        """验证 SSE 事件序列：thinking → text → done。"""

        async def fake_stream(*args, **kwargs):
            yield StreamChunk(type="thinking", content="正在思考...")
            yield StreamChunk(type="text", content="你好！")

        with patch("app.api.routes.LLMClient") as mock_llm:
            mock_llm.return_value.chat_stream = fake_stream
            response = client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": "hi"}], "top_k": 0},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]

        assert types[0] == "thinking"
        assert "text" in types
        assert types[-1] == "done"
        assert types.index("thinking") < types.index("text") < types.index("done")

    def test_sse_error_event_followed_by_done(self, client):
        """验证 error 事件后必须跟随 done 事件。"""

        async def fake_stream(*args, **kwargs):
            # 通过 yield 使其成为异步生成器，避免产生未 await 的协程
            yield StreamChunk(type="text", content="")
            raise RuntimeError("模拟 LLM 调用失败")

        with patch("app.api.routes.LLMClient") as mock_llm:
            mock_llm.return_value.chat_stream = fake_stream
            response = client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": "hi"}], "top_k": 0},
            )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        types = [e["type"] for e in events]

        assert "error" in types
        assert types[-1] == "done"
        assert types.index("error") < types.index("done")
