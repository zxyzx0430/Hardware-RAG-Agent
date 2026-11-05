"""Public ordinary-client wire checks; only the external provider is synthetic."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.llm.client import ChatMessage, LLMClient, LLMError
from src.llm.model_registry import MODEL_CONTEXT_WINDOWS, MODEL_MAX_TOKENS


async def _ordinary_stream():
    yield SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(
        content="Synthetic answer", reasoning_content=None, reasoning=None, thinking=None))])


def _ordinary_client():
    create = AsyncMock(return_value=SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content="Synthetic answer"))],
        model="gpt-4o-mini", usage=None))
    client = LLMClient(api_key="synthetic-only", base_url="https://synthetic.invalid",
                       model="gpt-4o-mini", max_tokens=512, max_retries=0)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, create


async def _ordinary_call(client, mode, **kwargs):
    if mode == "chat":
        return (await client.chat(**kwargs)).content
    return "".join([chunk.content async for chunk in client.chat_stream(**kwargs)])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
async def test_ordinary_fixed_input_refuses_before_send(mode):
    create = AsyncMock(return_value=SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content="Synthetic answer"))],
        model="gpt-4o-mini", usage=None))
    client = LLMClient(api_key="synthetic-only", base_url="https://synthetic.invalid",
                       model="gpt-4o-mini", max_tokens=1024, max_retries=0)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMError):
        await _ordinary_call(client, mode, user_message="Continue safely.", system_prompt="x" * 384000)
    assert create.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
async def test_ordinary_summary_keeps_complete_early_text(monkeypatch, mode):
    import copy

    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o-mini", 12000)
    client, create = _ordinary_client()
    constraint = " DO_NOT_FLASH ESP32-S3 3.3V /protected-path; ask before connecting."
    history = [ChatMessage("user" if i % 2 == 0 else "assistant",
                           f"record-{i}:" + "x" * 620 + constraint) for i in range(20)]
    before = copy.deepcopy(history)
    summary = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=constraint))],
                              model="gpt-4o-mini", usage=None)
    answer = create.return_value if mode == "chat" else _ordinary_stream()
    create.side_effect = [summary, answer]
    assert await _ordinary_call(client, mode, user_message="Continue safely.", history=history) == "Synthetic answer"
    assert create.await_count == 2
    summary_input = create.call_args_list[0].kwargs["messages"][-1]["content"]
    assert history[0].content in summary_input
    assert "record-0:" in summary_input and constraint in summary_input
    final_messages = create.call_args_list[1].kwargs["messages"]
    assert all(any(item["content"] == message.content for item in final_messages) for message in history[-10:])
    assert constraint in final_messages[0]["content"]
    assert history == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
@pytest.mark.parametrize("fault", ["error", "empty", "none", "space", "oversized", "timeout", "cancel", "prefix_budget"])
async def test_ordinary_summary_failure_never_sends_partial_context(monkeypatch, mode, fault):
    import asyncio
    import copy

    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o-mini", 12000)
    client, create = _ordinary_client()
    history = [ChatMessage("user", f"record-{i}:" + "x" * 620 + " DO_NOT_FLASH")
               for i in range(200 if fault == "prefix_budget" else 20)]
    before = copy.deepcopy(history)
    if fault in ("error", "timeout", "cancel"):
        create.side_effect = {"error": RuntimeError("synthetic failure"),
                              "timeout": TimeoutError(), "cancel": asyncio.CancelledError()}[fault]
    else:
        create.return_value.choices[0].message.content = {
            "empty": "", "none": None, "space": " \n", "oversized": "约" * 3000,
            "prefix_budget": "Synthetic summary",
        }[fault]
    with pytest.raises(asyncio.CancelledError if fault == "cancel" else LLMError):
        await _ordinary_call(client, mode, user_message="Continue safely.", history=history)
    assert create.await_count == (0 if fault == "prefix_budget" else 1)
    assert history == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
async def test_ordinary_summary_expansion_is_rechecked(monkeypatch, mode):
    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o-mini", 12000)
    client, create = _ordinary_client()
    history = [ChatMessage("user", "old record " + "x" * 1000) for _ in range(3)]
    create.return_value.choices[0].message.content = "s" * 7000
    with pytest.raises(LLMError):
        await _ordinary_call(client, mode, user_message="u" * 9000, history=history)
    assert create.await_count == 1
    assert len(create.call_args.kwargs["messages"][-1]["content"]) < 32768


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
@pytest.mark.parametrize("model,output", [("unknown-model", 512), ("gpt-4o-mini-similar", 512),
                                         (None, 512), ("gpt-4o-mini", True),
                                         ("gpt-4o-mini", 0), ("gpt-4o-mini", -1), ("gpt-4o-mini", 1.5)])
async def test_ordinary_unknown_and_invalid_capability_refuses(mode, model, output):
    client, create = _ordinary_client()
    client.model, client.max_tokens = model, output
    with pytest.raises(LLMError):
        await _ordinary_call(client, mode, user_message="Keep all context.")
    assert create.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
@pytest.mark.parametrize("kind", ["opaque_image", "text_blocks"])
async def test_ordinary_multimodal_is_not_lossily_summarized(mode, kind):
    client, create = _ordinary_client()
    content = ([{"type": "image_url", "image_url": {"url": "https://synthetic.invalid/image.png"}}]
               if kind == "opaque_image" else [{"type": "text", "text": "Observe; do not flash ESP32-S3."}])
    if kind == "opaque_image":
        with pytest.raises(LLMError):
            await _ordinary_call(client, mode, user_message=content)
        assert create.await_count == 0
    else:
        if mode == "stream":
            create.return_value = _ordinary_stream()
        assert await _ordinary_call(client, mode, user_message=content) == "Synthetic answer"
        assert create.call_args.kwargs["messages"] == [{"role": "user", "content": content}]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
@pytest.mark.parametrize("large_output", [False, True])
async def test_ordinary_request_uses_runtime_model_and_credentials(monkeypatch, mode, large_output):
    from unittest.mock import patch

    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "qwen-max", 12000)
    client, global_create = _ordinary_client()
    runtime, create = _ordinary_client()
    client.max_tokens = 100000 if large_output else 512
    history = None if large_output else [ChatMessage("user", f"record-{i}:" + "x" * 620 + " DO_NOT_FLASH") for i in range(20)]
    if mode == "stream":
        answer = _ordinary_stream()
    else:
        answer = create.return_value
    if history:
        summary = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="DO_NOT_FLASH"))])
        create.side_effect = [summary, answer]
    else:
        create.return_value = answer
    with patch("src.llm.client.AsyncOpenAI", return_value=runtime._client) as constructor:
        assert await _ordinary_call(client, mode, user_message="Continue safely.", history=history,
                                    model="provider/qwen-max", api_key="runtime-synthetic-only",
                                    base_url="https://runtime.synthetic.invalid") == "Synthetic answer"
    constructor.assert_called_once_with(api_key="runtime-synthetic-only",
                                        base_url="https://runtime.synthetic.invalid", timeout=120.0)
    assert global_create.await_count == 0
    assert create.await_count == (1 if large_output else 2)
    assert all(call.kwargs["model"] == "provider/qwen-max" for call in create.call_args_list)
    assert create.call_args.kwargs["max_tokens"] == (8192 if large_output else 512)
    if history:
        assert history[0].content in create.call_args_list[0].kwargs["messages"][-1]["content"]
        assert create.call_args_list[0].kwargs["max_tokens"] == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["chat", "stream", "uncapped_stream"])
@pytest.mark.parametrize("payload", ["small", "costly", "framing"])
async def test_ordinary_wire_budget_includes_framing_and_uncapped_output(monkeypatch, transport, payload):
    import json

    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o-mini", 12000)
    monkeypatch.setitem(MODEL_MAX_TOKENS, "gpt-4o-mini", 8192)
    client, create = _ordinary_client()
    if transport == "uncapped_stream":
        client.base_url = "https://opencode.synthetic.invalid"
    mode = "chat" if transport == "chat" else "stream"
    if mode == "stream":
        create.return_value = _ordinary_stream()
    text = "u" * {"small": 100, "costly": 5000, "framing": 11240}[payload]
    refuses = payload == "framing" or (transport == "uncapped_stream" and payload == "costly")
    if refuses:
        with pytest.raises(LLMError):
            await _ordinary_call(client, mode, user_message=text)
        assert create.await_count == 0
        if mode == "stream":
            await create.return_value.aclose()
    else:
        assert await _ordinary_call(client, mode, user_message=text) == "Synthetic answer"
        wire = create.call_args.kwargs
        reserve = 8192 if transport == "uncapped_stream" else wire["max_tokens"]
        assert ("max_tokens" not in wire) is (transport == "uncapped_stream")
        assert len(json.dumps(wire["messages"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 256 + reserve <= 12000


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "stream"])
@pytest.mark.parametrize("blocks", [False, True])
async def test_ordinary_summary_preserves_duplicates_and_roles(monkeypatch, mode, blocks):
    import copy
    import json

    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "gpt-4o-mini", 12000)
    client, create = _ordinary_client()
    content = "x" * 620 + " DO_NOT_FLASH /protected-path"
    history = [ChatMessage("user" if i % 2 == 0 else "assistant",
                           [{"type": "text", "text": content}] if blocks else content) for i in range(20)]
    before = copy.deepcopy(history)
    summary = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Unverified historical constraints."))])
    create.side_effect = [summary, create.return_value if mode == "chat" else _ordinary_stream()]
    await _ordinary_call(client, mode, user_message="next", system_prompt="HOST_SYSTEM", history=history)
    summary_wire, final = [call.kwargs["messages"] for call in create.call_args_list]
    prefix = [json.loads(line) for line in summary_wire[-1]["content"].splitlines() if line.startswith('{"role":')]
    assert prefix + final[2:-1] == [{"role": message.role, "content": message.content} for message in history]
    assert final[0] == {"role": "system", "content": "HOST_SYSTEM"}
    assert "untrusted history data" in final[1]["content"]
    assert history == before
