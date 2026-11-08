"""System-prompt blocks: global plan authoring and plan-mode read-only rules."""

from src.agent.plan_prompt import build_plan_authoring_prompt, build_plan_mode_prompt


def test_plan_mode_prompt_is_read_only():
    prompt = build_plan_mode_prompt()

    assert "read-only" in prompt.lower()
    assert "submit_plan" in prompt
    assert "not a permission grant" in prompt.lower()


def test_plan_authoring_prompt_guides_every_mode():
    prompt = build_plan_authoring_prompt()

    assert "every permission mode" in prompt
    assert "submit_plan" in prompt
    assert "enter_plan_mode" in prompt
    assert "stop and wait" in prompt
    assert "never start executing" in prompt


def test_append_plan_prompts_includes_authoring_without_plan_mode():
    from src.agent.agent_factory import _append_plan_prompts

    prompt = _append_plan_prompts("base prompt", False)

    assert "Plan authoring" in prompt
    assert "submit_plan" in prompt
    assert "Plan mode (read-only)" not in prompt


def test_append_plan_prompts_stacks_plan_mode_rules():
    from src.agent.agent_factory import _append_plan_prompts

    prompt = _append_plan_prompts("base prompt", True)

    assert "Plan authoring" in prompt
    assert "Plan mode (read-only)" in prompt
