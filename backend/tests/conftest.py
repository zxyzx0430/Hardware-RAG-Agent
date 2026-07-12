"""Pytest 全局 fixture：测试隔离 + auth 依赖绕过。

解决的问题：
1. test_routes_*.py 用 TestClient(create_app()) 不带 auth header，依赖磁盘
   keys_store.json 恰好为空才通过。开发者本地曾存过 API Key → 全线 401。
2. 不修改 keys_store.json 状态——测试不应依赖磁盘状态。

方案：autouse fixture patch app.api.auth._load_store 返回空 providers dict，
让 current_user 走"开发态兼容"分支（providers 为空时跳过鉴权返回 anonymous）。
这是 current_user 内部运行时调用的函数，patch 生效可靠。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 确保 backend/ 在 sys.path 最前
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _bypass_auth():
    """所有测试绕过 auth —— 测试不应依赖 keys_store.json 磁盘状态。

    current_user() 内部调用 _load_store()，当 store["providers"] 为空时
    跳过鉴权返回 anonymous。patch _load_store 返回空 store 即可。
    """
    _empty_store = {"providers": {}, "sessions": {}}
    with patch("app.api.auth._load_store", return_value=_empty_store):
        yield
