"""Bounded JSON-RPC stdio client for local MCP servers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

logger = logging.getLogger(__name__)

MAX_CONFIG_BYTES = 32_768
MAX_ARGUMENTS = 128
MAX_ARGUMENT_BYTES = 8_192
MAX_ENVIRONMENT_ITEMS = 64
MAX_ENVIRONMENT_VALUE_BYTES = 8_192
MAX_MESSAGE_BYTES = 65_536
MAX_SCHEMA_BYTES = 32_768
MAX_SCHEMA_DEPTH = 32
MAX_TOOL_LIST_PAGES = 32
MAX_TOOLS = 128
MAX_IGNORED_NOTIFICATIONS = 16
MAX_TOOL_NAME_LENGTH = 64
MAX_TOOL_DESCRIPTION_LENGTH = 2_048
REQUEST_TIMEOUT_SECONDS = 30.0
STARTUP_TIMEOUT_SECONDS = 5.0
TERMINATE_TIMEOUT_SECONDS = 2.0
SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25",
})

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEMA_KEYWORDS = frozenset({
    "$schema", "$id", "$ref", "$defs", "definitions", "$comment",
    "title", "description", "default", "examples", "deprecated",
    "type", "enum", "const", "properties", "required",
    "additionalProperties", "items", "prefixItems", "minItems", "maxItems",
    "uniqueItems", "minProperties", "maxProperties", "minLength", "maxLength",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
    "contains", "minContains", "maxContains", "dependentRequired",
    "dependentSchemas", "propertyNames", "unevaluatedProperties",
})
_SCHEMA_MAP_CHILDREN = frozenset({"$defs", "definitions", "properties", "dependentSchemas"})
_SCHEMA_SINGLE_CHILDREN = frozenset({
    "additionalProperties", "items", "not", "if", "then", "else", "contains",
    "propertyNames", "unevaluatedProperties",
})
_SCHEMA_LIST_CHILDREN = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})


class MCPConfigError(ValueError):
    """Safe configuration error; it never includes the supplied values."""

    def __init__(self, code: str = "invalid_config") -> None:
        self.code = code
        super().__init__("MCP server configuration is invalid")


class MCPCallError(RuntimeError):
    """Safe, non-retryable failure from an MCP call."""

    _MESSAGES = {
        "disconnected": "MCP server is disconnected",
        "timeout": "MCP request timed out",
        "transport_error": "MCP transport failed",
        "protocol_error": "MCP server returned an invalid protocol message",
        "rpc_error": "MCP server returned an RPC error",
        "tool_error": "MCP tool reported an error",
        "invalid_arguments": "MCP tool arguments failed schema validation",
        "unsupported_schema": "MCP tool schema is unsupported",
        "unknown_tool": "MCP tool is unavailable",
    }

    def __init__(self, code: str, *, unknown_result: bool = False) -> None:
        self.code = code
        self.retryable = False
        self.unknown_result = unknown_result
        super().__init__(self._MESSAGES.get(code, "MCP request failed"))


@dataclass(frozen=True)
class MCPTool:
    """Validated description of one remote MCP tool."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    validator: Any = field(default=None, repr=False, compare=False)


def normalize_server_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy a stdio server config without coercing its values."""
    if type(config) is not dict or set(config) - {"id", "name", "command", "args", "env"}:
        raise MCPConfigError()

    server_id = config.get("id")
    name = config.get("name", "")
    command = config.get("command")
    args = config.get("args", [])
    env = config.get("env", {})
    if type(server_id) is not str or not _NAME_PATTERN.fullmatch(server_id):
        raise MCPConfigError()
    if type(name) is not str or len(name) > 128:
        raise MCPConfigError()
    if type(command) is not str or not command or len(command) > 4_096 or "\x00" in command:
        raise MCPConfigError()
    if type(args) is not list or len(args) > MAX_ARGUMENTS:
        raise MCPConfigError()
    try:
        if any(type(arg) is not str or "\x00" in arg or len(arg.encode("utf-8")) > MAX_ARGUMENT_BYTES for arg in args):
            raise MCPConfigError()
    except UnicodeError:
        raise MCPConfigError() from None
    if type(env) is not dict or len(env) > MAX_ENVIRONMENT_ITEMS:
        raise MCPConfigError()
    for key, value in env.items():
        try:
            if (
                type(key) is not str
                or not _ENV_KEY_PATTERN.fullmatch(key)
                or type(value) is not str
                or "\x00" in value
                or len(value.encode("utf-8")) > MAX_ENVIRONMENT_VALUE_BYTES
            ):
                raise MCPConfigError()
        except UnicodeError:
            raise MCPConfigError() from None

    try:
        command_path = Path(command)
        if not command_path.is_absolute():
            raise MCPConfigError()
        resolved_command = command_path.resolve(strict=True)
        if not resolved_command.is_file() or not os.access(resolved_command, os.X_OK):
            raise MCPConfigError()
        if os.name == "nt" and resolved_command.suffix.lower() not in {".exe", ".com"}:
            raise MCPConfigError()
        normalized = {
            "id": server_id,
            "name": name or server_id,
            "command": str(resolved_command),
            "args": list(args),
            "env": dict(env),
        }
        encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise MCPConfigError()
    except MCPConfigError:
        raise
    except (OSError, RuntimeError, UnicodeError, TypeError, ValueError):
        raise MCPConfigError() from None
    return normalized


def build_child_environment(explicit: dict[str, str]) -> dict[str, str]:
    """Build a small child environment instead of inheriting process secrets."""
    if os.name == "nt":
        allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT"}
    else:
        allowed = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
    child_env = {
        key: value for key, value in os.environ.items()
        if key.upper() in allowed
    }
    for key, value in explicit.items():
        for inherited_key in tuple(child_env):
            if inherited_key.casefold() == key.casefold():
                child_env.pop(inherited_key)
        child_env[key] = value
    return child_env


def compile_input_schema(schema: dict[str, Any]) -> Any:
    """Compile the supported JSON Schema subset without enabling remote refs."""
    if type(schema) is not dict or schema.get("type") != "object":
        raise MCPCallError("unsupported_schema")
    try:
        encoded = json.dumps(schema, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise MCPCallError("unsupported_schema")
        _check_schema_subset(schema, depth=0)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except MCPCallError:
        raise
    except (SchemaError, TypeError, ValueError, UnicodeError, RecursionError):
        raise MCPCallError("unsupported_schema") from None


def validate_tool_arguments(validator: Any, args: Any) -> None:
    """Validate JSON arguments strictly; Python values are never coerced."""
    if type(args) is not dict:
        raise MCPCallError("invalid_arguments")
    try:
        _check_json_value(args, depth=0)
        encoded = json.dumps(args, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise MCPCallError("invalid_arguments")
        if not validator.is_valid(args):
            raise MCPCallError("invalid_arguments")
    except MCPCallError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MCPCallError("invalid_arguments") from None


def _check_schema_subset(schema: dict[str, Any], *, depth: int) -> None:
    if depth > MAX_SCHEMA_DEPTH or type(schema) is not dict:
        raise MCPCallError("unsupported_schema")
    if set(schema) - _SCHEMA_KEYWORDS:
        raise MCPCallError("unsupported_schema")
    ref = schema.get("$ref")
    if ref is not None and (type(ref) is not str or not ref.startswith("#")):
        raise MCPCallError("unsupported_schema")
    schema_type = schema.get("type")
    allowed_types = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if schema_type is not None:
        types = schema_type if type(schema_type) is list else [schema_type]
        if any(type(value) is not str or value not in allowed_types for value in types):
            raise MCPCallError("unsupported_schema")

    for keyword in _SCHEMA_MAP_CHILDREN:
        child_map = schema.get(keyword)
        if child_map is not None:
            if type(child_map) is not dict:
                raise MCPCallError("unsupported_schema")
            for child in child_map.values():
                _check_schema_child(child, depth + 1)
    for keyword in _SCHEMA_SINGLE_CHILDREN:
        child = schema.get(keyword)
        if child is not None and type(child) is not bool:
            _check_schema_child(child, depth + 1)
    for keyword in _SCHEMA_LIST_CHILDREN:
        children = schema.get(keyword)
        if children is not None:
            if type(children) is not list:
                raise MCPCallError("unsupported_schema")
            for child in children:
                _check_schema_child(child, depth + 1)


def _check_schema_child(child: Any, depth: int) -> None:
    if type(child) is bool:
        return
    if type(child) is not dict:
        raise MCPCallError("unsupported_schema")
    _check_schema_subset(child, depth=depth)


def _check_json_value(value: Any, *, depth: int) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise MCPCallError("invalid_arguments")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not (float("-inf") < value < float("inf")):
            raise MCPCallError("invalid_arguments")
        return
    if type(value) is list:
        for item in value:
            _check_json_value(item, depth=depth + 1)
        return
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise MCPCallError("invalid_arguments")
        for item in value.values():
            _check_json_value(item, depth=depth + 1)
        return
    raise MCPCallError("invalid_arguments")


class MCPClient:
    """One stdio JSON-RPC process; failures never trigger reconnect or replay."""

    def __init__(self, server_config: dict[str, Any]):
        self.config = normalize_server_config(server_config)
        self.command = self.config["command"]
        self.args = tuple(self.config["args"])
        self.env = dict(self.config["env"])
        self.name = self.config["name"]
        self.server_id = self.config["id"]
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._tools: list[MCPTool] = []
        self._request_id = 0
        self._connected = False
        self._request_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        process = self._process
        return bool(self._connected and process is not None and process.returncode is None)

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools)

    async def connect(self) -> bool:
        """Start, initialize and fully discover tools before becoming connected."""
        if self.connected:
            return True
        await self.disconnect()
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": build_child_environment(self.env),
            "limit": MAX_MESSAGE_BYTES + 1,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            self._process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(self.command, *self.args, **kwargs),
                timeout=STARTUP_TIMEOUT_SECONDS,
            )
            if self._process.stderr is not None:
                self._stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))
            result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hardware-rag-agent", "version": "1.0.0"},
            })
            if not self._valid_initialize_result(result):
                raise MCPCallError("protocol_error")
            await self._send_notification("notifications/initialized", {})
            self._tools = await self._discover_tools()
            self._connected = True
            logger.info("MCP server connected id=%s tool_count=%s", self.server_id, len(self._tools))
            return True
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        except Exception:
            await self.disconnect()
            logger.warning("MCP server start failed id=%s", self.server_id)
            return False

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call a discovered tool and raise safe failures instead of returning error text."""
        if not self.connected:
            raise MCPCallError("disconnected")
        tool = next((item for item in self._tools if item.name == tool_name), None)
        if tool is None:
            raise MCPCallError("unknown_tool")
        validate_tool_arguments(tool.validator, args)
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": args,
        }, unknown_result=True)
        if type(result) is not dict:
            await self.disconnect()
            raise MCPCallError("protocol_error", unknown_result=True)
        if "isError" in result and type(result["isError"]) is not bool:
            await self.disconnect()
            raise MCPCallError("protocol_error", unknown_result=True)
        if result.get("isError") is True:
            raise MCPCallError("tool_error")
        content = result.get("content", [])
        if type(content) is not list:
            await self.disconnect()
            raise MCPCallError("protocol_error", unknown_result=True)
        texts: list[str] = []
        for item in content:
            if type(item) is not dict:
                await self.disconnect()
                raise MCPCallError("protocol_error", unknown_result=True)
            if item.get("type") == "text":
                text_value = item.get("text")
                if type(text_value) is not str:
                    await self.disconnect()
                    raise MCPCallError("protocol_error", unknown_result=True)
                texts.append(text_value)
        return {"output": "\n".join(texts), "raw": result}

    async def disconnect(self) -> None:
        """Terminate the process, escalating to kill after a bounded grace period."""
        self._connected = False
        async with self._request_lock:
            await self._terminate_process_locked()
        self._tools = []

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        unknown_result: bool = False,
    ) -> Any:
        async with self._request_lock:
            process = self._process
            if method == "tools/call" and not self.connected:
                raise MCPCallError("disconnected")
            if process is None or process.returncode is not None or process.stdin is None or process.stdout is None:
                raise MCPCallError("disconnected", unknown_result=unknown_result)
            self._request_id += 1
            request_id = self._request_id
            request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            try:
                payload = _encode_message(request)
                response = await asyncio.wait_for(
                    self._exchange(process, payload, request_id),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                if "error" in response:
                    error = response["error"]
                    if type(error) is not dict or type(error.get("code")) is not int:
                        raise MCPCallError("protocol_error", unknown_result=unknown_result)
                    raise MCPCallError("rpc_error", unknown_result=False)
                if "result" not in response:
                    raise MCPCallError("protocol_error", unknown_result=unknown_result)
                return response["result"]
            except asyncio.TimeoutError:
                self._connected = False
                await self._terminate_process_locked()
                raise MCPCallError("timeout", unknown_result=unknown_result) from None
            except asyncio.CancelledError:
                self._connected = False
                await self._terminate_process_locked()
                raise
            except MCPCallError as exc:
                if exc.code in {"transport_error", "protocol_error", "disconnected"}:
                    self._connected = False
                    await self._terminate_process_locked()
                    raise MCPCallError(exc.code, unknown_result=unknown_result) from None
                raise
            except Exception:
                self._connected = False
                await self._terminate_process_locked()
                raise MCPCallError("transport_error", unknown_result=unknown_result) from None

    async def _exchange(self, process: asyncio.subprocess.Process, payload: bytes, request_id: int) -> dict[str, Any]:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(payload)
        await process.stdin.drain()
        ignored = 0
        while True:
            line = await process.stdout.readline()
            if not line:
                raise MCPCallError("disconnected")
            if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                raise MCPCallError("protocol_error")
            try:
                message = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeError, ValueError, RecursionError):
                raise MCPCallError("protocol_error") from None
            if type(message) is not dict or message.get("jsonrpc") != "2.0":
                raise MCPCallError("protocol_error")
            if "id" not in message:
                if (
                    set(message) - {"jsonrpc", "method", "params"}
                    or type(message.get("method")) is not str
                    or (
                        "params" in message and type(message["params"]) is not dict
                    )
                ):
                    raise MCPCallError("protocol_error")
                ignored += 1
                if ignored > MAX_IGNORED_NOTIFICATIONS:
                    raise MCPCallError("protocol_error")
                continue
            response_id = message["id"]
            if type(response_id) is not int or response_id != request_id:
                raise MCPCallError("protocol_error")
            if ("result" in message) == ("error" in message):
                raise MCPCallError("protocol_error")
            return message

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        async with self._request_lock:
            process = self._process
            if process is None or process.returncode is not None or process.stdin is None:
                raise MCPCallError("disconnected")
            try:
                await asyncio.wait_for(
                    _write_message(process.stdin, _encode_message({"jsonrpc": "2.0", "method": method, "params": params})),
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                self._connected = False
                await self._terminate_process_locked()
                raise
            except Exception:
                self._connected = False
                await self._terminate_process_locked()
                raise MCPCallError("transport_error") from None

    async def _discover_tools(self) -> list[MCPTool]:
        discovered: list[MCPTool] = []
        seen_names: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_TOOL_LIST_PAGES):
            params = {"cursor": cursor} if cursor is not None else {}
            result = await self._send_request("tools/list", params)
            if type(result) is not dict or type(result.get("tools")) is not list:
                raise MCPCallError("protocol_error")
            for item in result["tools"]:
                if len(discovered) >= MAX_TOOLS:
                    raise MCPCallError("protocol_error")
                tool = _parse_tool(item)
                if tool.name in seen_names:
                    raise MCPCallError("protocol_error")
                seen_names.add(tool.name)
                discovered.append(tool)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return discovered
            if (
                type(next_cursor) is not str
                or not next_cursor
                or len(next_cursor.encode("utf-8")) > 512
                or next_cursor in seen_cursors
            ):
                raise MCPCallError("protocol_error")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise MCPCallError("protocol_error")

    @staticmethod
    def _valid_initialize_result(result: Any) -> bool:
        return (
            type(result) is dict
            and type(result.get("protocolVersion")) is str
            and result["protocolVersion"] in SUPPORTED_PROTOCOL_VERSIONS
            and type(result.get("capabilities")) is dict
            and type(result.get("serverInfo")) is dict
            and type(result["serverInfo"].get("name")) is str
            and len(result["serverInfo"]["name"]) <= 128
            and type(result["serverInfo"].get("version")) is str
            and len(result["serverInfo"]["version"]) <= 128
        )

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while await stream.read(4_096):
                pass
        except (asyncio.CancelledError, Exception):
            return

    async def _terminate_process_locked(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            try:
                if process.returncode is None:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=TERMINATE_TIMEOUT_SECONDS)
                except (ProcessLookupError, OSError, asyncio.TimeoutError):
                    pass
            except (ProcessLookupError, OSError):
                pass
        task = self._stderr_task
        self._stderr_task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=0.25)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()


def _parse_tool(item: Any) -> MCPTool:
    if type(item) is not dict:
        raise MCPCallError("protocol_error")
    name = item.get("name")
    description = item.get("description", "")
    schema = item.get("inputSchema")
    if (
        type(name) is not str
        or len(name) > MAX_TOOL_NAME_LENGTH
        or not _NAME_PATTERN.fullmatch(name)
        or type(description) is not str
        or len(description) > MAX_TOOL_DESCRIPTION_LENGTH
        or type(schema) is not dict
    ):
        raise MCPCallError("protocol_error")
    validator = compile_input_schema(schema)
    return MCPTool(name=name, description=description, input_schema=schema, validator=validator)


def _encode_message(message: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError):
        raise MCPCallError("protocol_error") from None
    if len(payload) > MAX_MESSAGE_BYTES:
        raise MCPCallError("protocol_error")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-standard JSON constant")


async def _write_message(writer: asyncio.StreamWriter, payload: bytes) -> None:
    writer.write(payload)
    await writer.drain()
