"""Safe stdio MCP fixture: echo/add only; no filesystem, network or devices.

Run with an explicitly selected Python executable. This process never imports
project settings, installs packages, or writes received arguments to logs.
"""

import json
import math
import sys


TOOLS = [
    {
        "name": "echo",
        "description": "Return synthetic text unchanged; use only test data.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 4000}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add",
        "description": "Add two finite synthetic numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
]


def response_result(request):
    method = request.get("method")
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("Invalid params")
    if method == "initialize":
        requested = params.get("protocolVersion")
        supported = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
        return {
            "protocolVersion": requested if requested in supported else "2025-11-25",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "hardware-rag-safe-fixture", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method != "tools/call":
        raise ValueError("Unsupported method")
    name, args = params.get("name"), params.get("arguments", {})
    valid = isinstance(args, dict)
    if name == "echo":
        valid = valid and set(args) == {"text"} and isinstance(args["text"], str) and len(args["text"]) <= 4000
        output = args["text"] if valid else "Invalid echo arguments"
    elif name == "add":
        valid = valid and set(args) == {"a", "b"} and all(
            type(value) in (int, float) and math.isfinite(value) for value in args.values()
        )
        total = args["a"] + args["b"] if valid else None
        valid = valid and math.isfinite(total)
        output = str(total) if valid else "Invalid add arguments"
    else:
        raise ValueError("Unknown tool")
    return {"content": [{"type": "text", "text": output}], "isError": not valid}


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        request_id = None
        try:
            if len(line.encode("utf-8")) > 65536:
                raise ValueError("Message too large")
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError("Invalid request")
            if "id" not in request:
                continue
            request_id = request["id"]
            result = response_result(request)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, TypeError, KeyError, OverflowError):
            response = {
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32602, "message": "Invalid or unsupported request"},
            }
        print(json.dumps(response, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
