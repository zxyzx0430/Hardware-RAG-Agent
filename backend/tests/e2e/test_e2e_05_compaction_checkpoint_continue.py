"""E2E 5: long-session compaction replaces the checkpoint and continues."""

import pytest


def test_checkpoint_publish_entrypoint_exists():
    from src.agent.compact import checkpoint_publish

    assert callable(checkpoint_publish.publish_compacted_checkpoint)


@pytest.mark.skip(
    reason=(
        "W5 target: real compaction/restart E2E requires W3 checkpoint "
        "persistence and a deterministic agent harness"
    )
)
def test_compaction_replaces_checkpoint_and_continues():
    """Seed long history, trigger mid-run compaction, assert exact checkpoint
    replacement with no tool replay and token reset, restart, then continue."""
    pass
