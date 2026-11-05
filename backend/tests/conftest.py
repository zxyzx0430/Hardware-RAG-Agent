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


@pytest.fixture
async def writer_bound_saver(monkeypatch):
    """Give factory graphs a supported, isolated AsyncSqliteSaver."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from src.agent import agent_factory

    conn = await aiosqlite.connect(":memory:")
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    monkeypatch.setattr(agent_factory, "_global_checkpointer", saver)
    monkeypatch.setattr(agent_factory, "_global_checkpointer_cm", None)
    monkeypatch.setattr(agent_factory, "_writer_lifecycle_saver", None)
    monkeypatch.setattr(agent_factory, "_writer_lifecycle_registry", None)
    monkeypatch.setattr(agent_factory, "_factory_graph_savers", {})
    try:
        yield saver
    finally:
        await conn.close()


@pytest.fixture(autouse=True, scope="session")
def _ensure_global_schema():
    """Create the process-wide schema so direct route calls can read epochs."""
    from app.db.database import init_db

    init_db()


@pytest.fixture(autouse=True)
def _bypass_auth():
    """所有测试绕过 auth —— 测试不应依赖 keys_store.json 磁盘状态。

    current_user() 内部调用 _load_store()，当 store["providers"] 为空时
    跳过鉴权返回 anonymous。patch _load_store 返回空 store 即可。
    """
    _empty_store = {"providers": {}, "sessions": {}}
    with patch("app.api.auth._load_store", return_value=_empty_store):
        yield


@pytest.fixture
def context_archive_scope(tmp_path, monkeypatch):
    """Opt-in real archive capability for compaction tests, not global auth."""
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.api import common
    from app.db import database
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.context_events import ContextEventScope
    from app.db.database import Base
    from app.db.models import Session

    path = tmp_path / "compaction-events.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(common, "SessionLocal", factory)
    monkeypatch.setattr(database, "SessionLocal", factory)
    with factory.begin() as db:
        db.add(Session(id="archive-compaction"))
    scope = ContextEventScope("archive-compaction", "a" * 32, "b" * 64, "")
    with bind_compaction_archive(scope):
        yield SimpleNamespace(scope=scope, factory=factory, engine=engine, path=path)
    engine.dispose()


@pytest.fixture
def compaction_checkpoint():
    """Seed an actual graph, replacing the old async-update recording fake."""
    async def seed(messages, context_window):
        from langgraph.graph import StateGraph, MessagesState, START, END
        from langgraph.checkpoint.memory import InMemorySaver
        builder = StateGraph(MessagesState)
        builder.add_node("agent", lambda _state: {})
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "compaction-test", "context_window": context_window}}
        await graph.ainvoke({"messages": messages}, config)
        return graph, config, (await graph.aget_state(config)).values["messages"]
    return seed
