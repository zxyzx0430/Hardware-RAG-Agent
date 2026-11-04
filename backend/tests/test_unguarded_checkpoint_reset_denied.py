import pytest

from src.agent import agent_factory


@pytest.mark.asyncio
async def test_unguarded_checkpoint_reset_fails_before_saver_access(monkeypatch):
    touched = []

    async def fail_async(name, *_args, **_kwargs):
        touched.append(name)
        raise AssertionError(f"unexpected saver-path call: {name}")

    def fail_sync(name, *_args, **_kwargs):
        touched.append(name)
        raise AssertionError(f"unexpected saver-path call: {name}")

    monkeypatch.setattr(
        agent_factory,
        "_get_checkpointer",
        lambda: fail_async("_get_checkpointer"),
    )
    monkeypatch.setattr(
        agent_factory,
        "_try_delete_thread",
        lambda *_args, **_kwargs: fail_async("_try_delete_thread"),
    )
    monkeypatch.setattr(
        agent_factory,
        "_clear_storage",
        lambda *_args, **_kwargs: fail_sync("_clear_storage"),
    )
    monkeypatch.setattr(
        agent_factory,
        "_clear_writes",
        lambda *_args, **_kwargs: fail_sync("_clear_writes"),
    )

    with pytest.raises(
        agent_factory.WriterLifecycleError,
        match="unguarded checkpoint reset is disabled",
    ):
        await agent_factory.reset_thread_checkpoint("thread-1")

    assert touched == []
