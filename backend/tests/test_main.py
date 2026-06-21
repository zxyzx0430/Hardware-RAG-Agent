"""
测试 FastAPI 应用主入口。

修复说明：原测试覆盖的是 backend/main.py 中已过期的 Week 1 骨架路由
（/v1/models、/chat、/chat/stream）。当前后端实际入口为 app.main.create_app，
路由统一挂载在 /api 前缀下，因此本文件已切换为测试实际运行的应用。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import create_app


def test_health_endpoint():
    """健康检查端点可用。"""
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root_endpoint():
    """根路径返回应用信息。"""
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_models_endpoint_uses_request_headers():
    """/api/models 应透传动态请求头配置。"""
    app = create_app()
    client = TestClient(app)

    with patch("app.api.routes.LLMClient.list_models", new=AsyncMock(return_value=["gpt-4o-mini"])) as mock_list:
        response = client.post(
            "/api/models",
            json={"base_url": "https://example.com/v1"},
            headers={
                "X-API-Key": "user-key",
                "X-Base-URL": "https://example.com/v1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"models": ["gpt-4o-mini"]}}
    assert mock_list.await_args.kwargs["api_key"] == "user-key"
    assert mock_list.await_args.kwargs["base_url"] == "https://example.com/v1"
