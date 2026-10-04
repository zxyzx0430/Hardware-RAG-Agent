"""Regression coverage for the local MCP stdio communication boundary."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.responses import JSONResponse

from src.agent.core.toolkit.mcp_tool_adapter import MCPToolSpec
from src.mcp import client as client_module
from src.mcp.client import MCPCallError, MCPClient, MCPConfigError
from src.mcp.manager import MCPServerManager

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
ECHO_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "maxLength": 4000}},
    "required": ["text"],
    "additionalProperties": False,
}
ADD_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}
TOOLS = [
    {"name": "echo", "description": "Echo test text", "inputSchema": ECHO_SCHEMA},
    {"name": "add", "description": "Add test numbers", "inputSchema": ADD_SCHEMA},
]
_END_OF_STREAM = object()


def _config(server_id: str = "fixture", *, args: list[str] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": server_id,
        "name": server_id,
        "command": sys.executable,
        "args": [str(FIXTURE)] if args is None else args,
        "env": {} if env is None else env,
    }


def _rpc_response(request: dict[str, Any], result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def _default_handler(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    if method == "initialize":
        return _rpc_response(request, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "test-server", "version": "1"},
        })
    if method == "tools/list":
        return _rpc_response(request, {"tools": TOOLS})
    if method == "tools/call":
        name = request["params"]["name"]
        if name == "fail":
            result = {"content": [{"type": "text", "text": "private remote text"}], "isError": True}
        else:
            result = {"content": [{"type": "text", "text": "ok"}]}
        return _rpc_response(request, result)
    return None


class _FakeStdin:
    def __init__(self, process: "_FakeProcess") -> None:
        self.process = process
        self.pending = bytearray()

    def write(self, data: bytes) -> None:
        self.pending.extend(data)

    async def drain(self) -> None:
        data = bytes(self.pending)
        self.pending.clear()
        for line in data.splitlines():
            request = json.loads(line.decode("utf-8"))
            self.process.requests.append(request)
            if "id" not in request:
                continue
            responses = self.process.handler(request)
            if responses is _END_OF_STREAM:
                self.process.stdout.feed_eof()
                continue
            if responses is None:
                continue
            if not isinstance(responses, list):
                responses = [responses]
            for response in responses:
                self.process.stdout.feed_data(
                    json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
                )


class _FakeProcess:
    def __init__(self, handler: Callable[[dict[str, Any]], Any], limit: int) -> None:
        self.stdin = _FakeStdin(self)
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.handler = handler
        self.requests: list[dict[str, Any]] = []
        self.child_env: dict[str, str] = {}

    def terminate(self) -> None:
        self.returncode = 0
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        return self.returncode


def _patch_fake_process(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[dict[str, Any]], Any] = _default_handler,
) -> list[_FakeProcess]:
    processes: list[_FakeProcess] = []

    async def create_subprocess_exec(*command: str, **kwargs: Any) -> _FakeProcess:
        process = _FakeProcess(handler, kwargs["limit"])
        process.child_env = kwargs["env"]
        processes.append(process)
        return process

    monkeypatch.setattr(client_module.asyncio, "create_subprocess_exec", create_subprocess_exec)
    return processes


@pytest.mark.asyncio
async def test_real_stdio_echo_add_concurrency_and_strict_schema_validation() -> None:
    client = MCPClient(_config())
    assert await client.connect() is True
    try:
        calls = [client.call_tool("echo", {"text": f"synthetic-{index}"}) for index in range(8)]
        results = await asyncio.gather(*calls)
        assert [item["output"] for item in results] == [f"synthetic-{index}" for index in range(8)]
        assert (await client.call_tool("add", {"a": 2, "b": 3})) ["output"] == "5"
        with pytest.raises(MCPCallError) as error:
            await client.call_tool("echo", {"text": 7})
        assert error.value.code == "invalid_arguments"
        assert error.value.retryable is False
        assert error.value.unknown_result is False
        assert client.connected is True
    finally:
        await client.disconnect()
    assert client.connected is False


@pytest.mark.asyncio
async def test_manager_snapshot_is_fresh_and_old_specs_stay_bound_after_restart() -> None:
    manager = MCPServerManager()
    manager.register_config("fixture", _config())
    assert manager.list_servers()[0]["status"] == "stopped"
    assert await manager.start("fixture") is True
    first = manager.connected_tool_specs_snapshot()
    fresh = manager.connected_tool_specs_snapshot()
    assert len(first) == 2
    assert first[0] is not fresh[0]
    assert first[0]._client is fresh[0]._client
    echo = next(item for item in first if item.mcp_info["tool_name"] == "echo")
    assert isinstance(echo, MCPToolSpec)
    assert echo.args_schema == ECHO_SCHEMA
    assert echo.risk_level.value == "high"
    assert echo.max_retries == 0
    assert echo.mcp_info == {"server_name": "fixture", "tool_name": "echo"}
    assert (await echo.execute({"text": "before stop"}, None))["output"] == "before stop"

    old_client = echo._client
    await manager.stop("fixture")
    with pytest.raises(MCPCallError) as error:
        await echo.execute({"text": "must not reach replacement"}, None)
    assert error.value.code == "disconnected"
    assert await manager.start("fixture") is True
    second = manager.connected_tool_specs_snapshot()
    new_echo = next(item for item in second if item.mcp_info["tool_name"] == "echo")
    assert new_echo is not echo
    assert new_echo._client is not old_client
    assert (await new_echo.execute({"text": "after restart"}, None))["output"] == "after restart"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_concurrent_manager_start_spawns_only_one_process(monkeypatch: pytest.MonkeyPatch) -> None:
    processes = _patch_fake_process(monkeypatch)
    manager = MCPServerManager()
    manager.register_config("single-start", _config("single-start", args=[]))
    assert await asyncio.gather(manager.start("single-start"), manager.start("single-start")) == [True, True]
    assert len(processes) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_request_ids_and_bounded_server_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, Any]) -> Any:
        response = _default_handler(request)
        if response is None:
            return None
        notification = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
        return [notification, response]

    processes = _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("fake"))
    assert await client.connect() is True
    assert (await client.call_tool("echo", {"text": "safe"}))["output"] == "ok"
    process = processes[0]
    request_ids = [request["id"] for request in process.requests if "id" in request]
    assert request_ids == [1, 2, 3]
    await client.disconnect()


@pytest.mark.asyncio
async def test_wrong_jsonrpc_id_is_a_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, Any]) -> Any:
        response = _default_handler(request)
        if request.get("method") == "tools/call" and response is not None:
            response["id"] += 1
        return response

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("wrong-id"))
    assert await client.connect() is True
    with pytest.raises(MCPCallError) as error:
        await client.call_tool("echo", {"text": "safe"})
    assert error.value.code == "protocol_error"
    assert error.value.unknown_result is True
    assert client.connected is False


@pytest.mark.asyncio
async def test_remote_rpc_error_is_safe_and_does_not_become_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, Any]) -> Any:
        if request.get("method") == "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32602, "message": "private remote details"},
            }
        return _default_handler(request)

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("rpc-error"))
    assert await client.connect() is True
    with pytest.raises(MCPCallError) as error:
        await client.call_tool("echo", {"text": "safe"})
    assert error.value.code == "rpc_error"
    assert error.value.retryable is False
    assert error.value.unknown_result is False
    assert "private remote details" not in str(error.value)
    assert client.connected is True
    await client.disconnect()


@pytest.mark.asyncio
async def test_disconnect_during_call_reports_unknown_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, Any]) -> Any:
        if request.get("method") == "tools/call":
            return _END_OF_STREAM
        return _default_handler(request)

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("disconnected-call"))
    assert await client.connect() is True
    with pytest.raises(MCPCallError) as error:
        await client.call_tool("echo", {"text": "may have executed"})
    assert error.value.code == "disconnected"
    assert error.value.retryable is False
    assert error.value.unknown_result is True
    assert client.connected is False


@pytest.mark.asyncio
async def test_notification_flood_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, Any]) -> Any:
        response = _default_handler(request)
        if response is None:
            return None
        noise = [
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
            for _ in range(client_module.MAX_IGNORED_NOTIFICATIONS + 1)
        ]
        return [*noise, response]

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("notification-flood"))
    assert await client.connect() is False
    assert client.connected is False


@pytest.mark.asyncio
async def test_timeout_is_not_replayed_and_marks_result_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: dict[str, Any]) -> Any:
        calls.append(request.get("method", ""))
        if request.get("method") == "tools/call":
            return None
        return _default_handler(request)

    _patch_fake_process(monkeypatch, handler)
    monkeypatch.setattr(client_module, "REQUEST_TIMEOUT_SECONDS", 0.01)
    client = MCPClient(_config("timeout"))
    assert await client.connect() is True
    with pytest.raises(MCPCallError) as error:
        await client.call_tool("echo", {"text": "one attempt"})
    assert error.value.code == "timeout"
    assert error.value.retryable is False
    assert error.value.unknown_result is True
    assert calls.count("tools/call") == 1
    with pytest.raises(MCPCallError) as disconnected:
        await client.call_tool("echo", {"text": "must not replay"})
    assert disconnected.value.code == "disconnected"
    assert calls.count("tools/call") == 1


@pytest.mark.asyncio
async def test_is_error_is_a_real_safe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, Any]) -> Any:
        if request.get("method") == "tools/list":
            tools = [{"name": "fail", "description": "", "inputSchema": {"type": "object"}}]
            return _rpc_response(request, {"tools": tools})
        return _default_handler(request)

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("remote-error"))
    assert await client.connect() is True
    with pytest.raises(MCPCallError) as error:
        await client.call_tool("fail", {})
    assert error.value.code == "tool_error"
    assert error.value.unknown_result is False
    assert "private remote text" not in str(error.value)
    assert client.connected is True
    await client.disconnect()


@pytest.mark.asyncio
async def test_tools_list_pagination_is_bounded_and_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_cursors: list[str | None] = []

    def handler(request: dict[str, Any]) -> Any:
        if request.get("method") != "tools/list":
            return _default_handler(request)
        cursor = request["params"].get("cursor")
        requested_cursors.append(cursor)
        tool = TOOLS[0] if cursor is None else TOOLS[1]
        result: dict[str, Any] = {"tools": [tool]}
        if cursor is None:
            result["nextCursor"] = "page-two"
        return _rpc_response(request, result)

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("paged"))
    assert await client.connect() is True
    assert [tool.name for tool in client.tools] == ["echo", "add"]
    assert requested_cursors == [None, "page-two"]
    await client.disconnect()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"x": {"$ref": "https://invalid.example/schema"}}},
        {"type": "object", "properties": {"x": {"pattern": "^x+$"}}},
    ],
)
@pytest.mark.asyncio
async def test_remote_refs_and_unsupported_schema_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    schema: dict[str, Any],
) -> None:
    def handler(request: dict[str, Any]) -> Any:
        if request.get("method") == "tools/list":
            return _rpc_response(request, {"tools": [{"name": "unsafe", "inputSchema": schema}]})
        return _default_handler(request)

    _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("bad-schema"))
    assert await client.connect() is False
    assert client.connected is False


@pytest.mark.asyncio
async def test_child_environment_is_allowlisted_and_explicit_values_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    processes = _patch_fake_process(monkeypatch)
    client = MCPClient(_config("env", args=[], env={"MCP_TEST_VALUE": "explicit"}))
    assert await client.connect() is True
    child_env = processes[0].child_env
    assert child_env["MCP_TEST_VALUE"] == "explicit"
    assert "OPENAI_API_KEY" not in child_env
    assert processes[0].returncode is None
    await client.disconnect()


def test_config_rejects_non_path_commands_and_coercion() -> None:
    with pytest.raises(MCPConfigError):
        MCPClient({**_config("bad-command"), "command": "python"})
    with pytest.raises(MCPConfigError):
        MCPClient({**_config("bad-args"), "args": [7]})
    with pytest.raises(MCPConfigError):
        MCPClient({**_config("bad-env"), "env": {"VALUE": 7}})


@pytest.mark.asyncio
async def test_stop_blocks_a_call_already_queued_behind_an_active_request(monkeypatch):
    entered = asyncio.Event()

    def handler(request):
        if request.get("method") == "tools/call":
            entered.set()
            return None
        return _default_handler(request)

    processes = _patch_fake_process(monkeypatch, handler)
    client = MCPClient(_config("queued-stop"))
    assert await client.connect()
    first = asyncio.create_task(client.call_tool("echo", {"text": "first"}))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(client.call_tool("echo", {"text": "must-not-send"}))
    await asyncio.sleep(0)
    stopped = asyncio.create_task(client.disconnect())
    await asyncio.sleep(0)
    request = next(item for item in processes[0].requests if item.get("method") == "tools/call")
    processes[0].stdout.feed_data(json.dumps(_rpc_response(request, {"content": [{"type": "text", "text": "first"}]})).encode() + b"\n")
    assert (await first)["output"] == "first"
    with pytest.raises(MCPCallError, match="disconnected"):
        await second
    await stopped
    assert len([item for item in processes[0].requests if item.get("method") == "tools/call"]) == 1


@pytest.mark.asyncio
async def test_manager_limits_the_entire_connection_not_only_individual_pages(monkeypatch):
    from src.mcp import manager as module
    cancelled = []

    async def slow_connect(self):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(MCPClient, "connect", slow_connect)
    monkeypatch.setattr(module, "CONNECTION_DEADLINE_SECONDS", 0.01)
    manager = MCPServerManager()
    manager.register_config("slow", _config("slow"))
    assert await manager.start("slow") is False
    assert cancelled == [True] and manager.get_client("slow") is None


def test_external_names_are_bounded_and_unambiguous():
    from src.agent.core.toolkit.mcp_tool_adapter import mcp_tool_name
    assert mcp_tool_name("fixture", "echo") == "mcp__fixture__echo"
    assert len(mcp_tool_name("s" * 64, "t" * 64)) <= 64
    assert mcp_tool_name("a__b", "c") != mcp_tool_name("a", "b__c")


@pytest.mark.asyncio
async def test_api_config_is_ram_only_and_duplicate_does_not_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import mcp_routes

    manager = MCPServerManager()
    monkeypatch.setattr(mcp_routes, "get_mcp_manager", lambda: manager)
    payload = {"id": "api-test", "command": sys.executable, "args": [], "env": {}}
    first = await mcp_routes.add_server(payload, user={})
    assert first == {"success": True, "data": {"id": "api-test"}}
    assert manager.list_servers()[0]["status"] == "stopped"

    duplicate = await mcp_routes.add_server(
        {**payload, "name": "replacement"}, user={}
    )
    assert isinstance(duplicate, JSONResponse)
    assert duplicate.status_code == 409
    assert json.loads(duplicate.body)["data"]["error"]["code"] == "duplicate_config"
    assert len(manager.list_servers()) == 1
    assert manager.list_servers()[0]["name"] == "api-test"

    invalid = await mcp_routes.add_server({**payload, "args": [3]}, user={})
    assert isinstance(invalid, JSONResponse)
    assert invalid.status_code == 422
    assert json.loads(invalid.body)["success"] is False


@pytest.mark.asyncio
async def test_start_failure_returns_generic_error_without_claiming_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import mcp_routes

    async def failed_spawn(*command: str, **kwargs: Any) -> Any:
        raise OSError("private process detail")

    monkeypatch.setattr(client_module.asyncio, "create_subprocess_exec", failed_spawn)
    manager = MCPServerManager()
    manager.register_config("start-failure", _config("start-failure", args=[]))
    monkeypatch.setattr(mcp_routes, "get_mcp_manager", lambda: manager)

    response = await mcp_routes.start_server("start-failure", user={})
    assert isinstance(response, JSONResponse)
    assert response.status_code == 502
    body = json.loads(response.body)
    assert body["success"] is False
    assert body["data"]["error"]["code"] == "start_failed"
    assert "private process detail" not in response.body.decode("utf-8")
