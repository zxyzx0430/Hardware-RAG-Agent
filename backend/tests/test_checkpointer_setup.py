"""The sqlite checkpointer must be set up before any writer binding uses it.

Regression: ``_build_sqlite_checkpointer`` entered the ``from_conn_string``
context manager but never called ``setup()``, so ``is_setup`` stayed false and
every guarded checkpoint reset rejected the real server's saver with
"exact supported binding". Unit fixtures had always prepared their own saver,
so nothing caught it.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.agent import agent_factory


@pytest.fixture(autouse=True)
async def _isolated_checkpointer(monkeypatch, tmp_path):
    """Point the singleton at a temp database and leave no saver behind."""
    monkeypatch.setattr(
        agent_factory, "_SQLITE_CHECKPOINT_PATH", tmp_path / "agent_checkpoints.sqlite",
    )
    # The suite runs with a memory checkpointer in the environment; these cases
    # are about the sqlite path, so force it unless a test overrides it again.
    monkeypatch.setattr(agent_factory, "_read_checkpointer_type", lambda: "sqlite")
    monkeypatch.setattr(agent_factory, "_global_checkpointer", None)
    monkeypatch.setattr(agent_factory, "_global_checkpointer_cm", None)
    yield
    cm = agent_factory._global_checkpointer_cm
    if cm is not None:
        # Close the aiosqlite connection on this loop so tmp_path can be removed.
        await cm.__aexit__(None, None, None)
    agent_factory._global_checkpointer_cm = None
    agent_factory._global_checkpointer = None


async def test_sqlite_checkpointer_is_set_up_after_creation():
    checkpointer = await agent_factory._get_checkpointer()

    assert type(checkpointer) is AsyncSqliteSaver
    assert checkpointer.is_setup is True


async def test_checkpointer_setup_failure_fails_closed(monkeypatch):
    async def fail_setup(_self):
        raise RuntimeError("schema bootstrap failed")

    monkeypatch.setattr(AsyncSqliteSaver, "setup", fail_setup)

    with pytest.raises(RuntimeError, match="schema bootstrap failed"):
        await agent_factory._get_checkpointer()

    # Fail closed: no in-memory fallback, and no half-initialized state left.
    assert agent_factory._global_checkpointer is None
    assert agent_factory._global_checkpointer_cm is None


async def test_fresh_checkpointer_passes_writer_binding_validation():
    checkpointer = await agent_factory._get_checkpointer()

    # This is the exact gate that rejected the real server's saver.
    agent_factory._validate_bound_async_sqlite_saver(checkpointer)


async def test_memory_checkpointer_mode_never_touches_sqlite(monkeypatch):
    async def unexpected_sqlite_build():
        raise AssertionError("memory mode must not build a sqlite checkpointer")

    monkeypatch.setattr(agent_factory, "_read_checkpointer_type", lambda: "memory")
    monkeypatch.setattr(agent_factory, "_build_sqlite_checkpointer", unexpected_sqlite_build)

    checkpointer = await agent_factory._get_checkpointer()

    assert type(checkpointer) is InMemorySaver
