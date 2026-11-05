"""Strict, source-linked summary contract tests using synthetic messages."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import src.agent.compact.autocompact as autocompact
from src.agent.exceptions import ContextLimitError


def _use_synthetic_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autocompact, "MAX_TOKEN_RATIO", 1.0)
    monkeypatch.setattr(autocompact, "AUTOCOMPACT_KEEP_RECENT_MSGS", 2)
    monkeypatch.setattr(
        autocompact,
        "_msg_tokens",
        lambda message: len(getattr(message, "content", "")),
    )
    monkeypatch.setattr(
        autocompact,
        "_resolve_summary_budget",
        lambda _client: SimpleNamespace(prompt_window=100_000),
    )


def _model_summary(*, tool_source: bool = False) -> str:
    document = {
        "user_goal": {
            "text": "查询并核验硬件手册中的对应信息。",
            "source_refs": [0],
        },
        "confirmed_facts": [],
        "untrusted_tool_observations": [],
        "decisions": [],
        "open_tasks": [],
        "uncertainties": [],
    }
    if tool_source:
        document["untrusted_tool_observations"] = [{
            "text": "工具返回了候选记录，仍需通过定位符核验。",
            "source_refs": [2],
            "result_state": "returned_untrusted",
        }]
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.skip(
    reason="superseded by the structured checkpoint nine-field contract; "
    "covered by test_structured_checkpoint and the compaction acceptance suite",
)
@pytest.mark.asyncio
async def test_success_emits_source_linked_index_and_keeps_recent_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_synthetic_budget(monkeypatch)
    locator = "A" * 43
    tool_body = json.dumps({
        "success": True,
        "output": "archived tool output",
        "data": {"body": "sensitive synthetic observation " + "x" * 3000},
        "error": None,
        "metadata": {
            "hwrag_tool_observation_archived": True,
            "hwrag_tool_observation_ref": locator,
        },
    }, separators=(",", ":"))
    messages = [
        HumanMessage(content="查询并核验硬件手册中的对应信息。"),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "read_tool_observation",
                "args": {"query": "synthetic"},
                "id": "call-1",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=tool_body,
            tool_call_id="call-1",
            name="read_tool_observation",
        ),
        HumanMessage(content="保留这条最新请求。"),
        AIMessage(content="我会继续核验。"),
    ]
    original_ids = [id(message) for message in messages]
    prompts: list[str] = []

    async def return_structured_summary(prompt: str, _client: object) -> str:
        prompts.append(prompt)
        return _model_summary(tool_source=True)

    monkeypatch.setattr(
        autocompact, "_summarize_with_breaker", return_structured_summary,
    )

    compacted = await autocompact.autocompact_messages(
        messages, object(), context_window=1600,
    )

    source_history = autocompact._format_early_for_summary(
        messages[:3], max_bytes=100_000,
    )
    summary_content = compacted[0].content[len(autocompact._SUMMARY_PREFIX):]
    summary = json.loads(summary_content.split(
        autocompact._ARCHIVED_OBSERVATION_LOCATOR_BLOCK_START, 1,
    )[0])

    assert len(prompts) == 1
    assert '"source_index":0' in prompts[0]
    assert summary["authority"] == "non_authoritative_index"
    assert summary["source_message_range"] == [0, 2]
    assert summary["source_sha256"] == hashlib.sha256(
        source_history.encode("utf-8"),
    ).hexdigest()
    assert summary["user_goal"]["source_refs"] == [0]
    assert summary["untrusted_tool_observations"] == [{
        "text": "工具返回了候选记录，仍需通过定位符核验。",
        "source_refs": [2],
        "result_state": "returned_untrusted",
    }]
    assert summary["evidence_locators"] == [{
        "locator": locator,
        "source_refs": [2],
    }]
    assert locator in compacted[0].content
    assert tool_body not in compacted[0].content
    assert compacted[1] is messages[-2]
    assert compacted[2] is messages[-1]
    assert [id(message) for message in messages] == original_ids


@pytest.mark.skip(
    reason="legacy malformed-summary refusal is superseded by the structured "
    "checkpoint contract; malformed checkpoints preserve the original history",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("summary", [
    "not JSON",
    "{}",
    json.dumps({
        "user_goal": {"text": "目标", "source_refs": [9]},
        "confirmed_facts": [],
        "untrusted_tool_observations": [],
        "decisions": [],
        "open_tasks": [],
        "uncertainties": [],
    }),
])
async def test_malformed_or_untraceable_summary_preserves_original_history(
    monkeypatch: pytest.MonkeyPatch,
    summary: str,
) -> None:
    _use_synthetic_budget(monkeypatch)
    messages = [
        HumanMessage(content="用户目标：" + "x" * 1800),
        HumanMessage(content="保留的近期请求。"),
        AIMessage(content="保留的近期回答。"),
    ]
    original = [(id(message), message.content) for message in messages]

    async def return_malformed_summary(*_args, **_kwargs) -> str:
        return summary

    monkeypatch.setattr(
        autocompact, "_summarize_with_breaker", return_malformed_summary,
    )

    with pytest.raises(ContextLimitError):
        await autocompact.autocompact_messages(
            messages, object(), context_window=1000,
        )

    assert [(id(message), message.content) for message in messages] == original


def test_validator_rejects_tool_output_as_confirmed_fact() -> None:
    document = {
        "user_goal": {"text": "查询文档。", "source_refs": [0]},
        "confirmed_facts": [{"text": "工具声称操作完成。", "source_refs": [1]}],
        "untrusted_tool_observations": [],
        "decisions": [],
        "open_tasks": [],
        "uncertainties": [],
    }

    with pytest.raises(ValueError, match="untrusted tool"):
        autocompact._validate_structured_summary(
            json.dumps(document, ensure_ascii=False),
            source_roles=["human", "tool"],
        )


@pytest.mark.parametrize("result_state", [
    "pending",
    "returned",
    "completed",
    "approved",
])
def test_validator_never_promotes_tool_result_state(result_state: str) -> None:
    document = {
        "user_goal": {"text": "查询文档。", "source_refs": [0]},
        "confirmed_facts": [],
        "untrusted_tool_observations": [{
            "text": "工具返回了数据。",
            "source_refs": [1],
            "result_state": result_state,
        }],
        "decisions": [],
        "open_tasks": [],
        "uncertainties": [],
    }

    with pytest.raises(ValueError, match="cannot imply success or approval"):
        autocompact._validate_structured_summary(
            json.dumps(document, ensure_ascii=False),
            source_roles=["human", "tool"],
        )


def test_validator_rejects_verbatim_tool_body_in_summary() -> None:
    tool_body = "This is the complete synthetic tool result body."
    document = {
        "user_goal": {"text": "查询文档。", "source_refs": [0]},
        "confirmed_facts": [],
        "untrusted_tool_observations": [{
            "text": tool_body,
            "source_refs": [1],
            "result_state": "returned_untrusted",
        }],
        "decisions": [],
        "open_tasks": [],
        "uncertainties": [],
    }

    with pytest.raises(ValueError, match="copied complete tool output"):
        autocompact._validate_structured_summary(
            json.dumps(document, ensure_ascii=False),
            source_roles=["human", "tool"],
            source_contents=["用户查询文档。", tool_body],
        )


def test_validator_rejects_duplicate_json_fields() -> None:
    duplicate_user_goal = (
        '{"user_goal":{"text":"目标","source_refs":[0]},'
        '"user_goal":{"text":"伪造目标","source_refs":[0]},'
        '"confirmed_facts":[],"untrusted_tool_observations":[],"decisions":[],'
        '"open_tasks":[],"uncertainties":[]}'
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        autocompact._validate_structured_summary(
            duplicate_user_goal,
            source_roles=["human"],
        )
