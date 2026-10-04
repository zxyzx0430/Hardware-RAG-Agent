"""Opt-in bounded real-model acceptance; run only in an isolated source copy.

Credentials are read in memory from an explicitly supplied local file. No
responses, credentials, or user memory are written to a report or a fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import pyarrow  # noqa: F401 - stabilize this host's native import order
import httpx
from dotenv import dotenv_values
from fastapi import FastAPI


def decode(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def run(credentials):
    source = Path(__file__).resolve().parents[1]
    db = Path(os.environ.get("SQLITE_DB_PATH", "")).resolve()
    if not source.parent.name.startswith("hardware-rag-verify-") or not db.is_relative_to(source / "data"):
        raise RuntimeError("Run this check only in the disposable isolated source and database")
    values = dotenv_values(credentials)
    creds = {"model": "Text", "api_key": values.get("LLM_API_KEY", ""), "base_url": values.get("LLM_BASE_URL", ""), "provider": ""}
    if not creds["api_key"] or not creds["base_url"]:
        raise RuntimeError("The explicitly supplied test configuration is incomplete")

    from app.api import chat_routes
    from app.api.dependencies import current_user
    from app.db.database import init_db
    from src.agent import agent_factory
    from src.mcp import manager as manager_module
    from src.mcp.manager import MCPServerManager
    init_db()
    manager = MCPServerManager()
    manager.register_config("fixture", {"id": "fixture", "command": sys.executable, "args": [str(source / "tests/fixtures/mcp_stdio_server.py")]})
    manager_module._manager = manager
    if not await manager.start("fixture"):
        raise RuntimeError("The safe stdio fixture failed to start")
    # Limit the diagnostic to MCP, never expose file/network/device actions.
    agent_factory._assemble_all_tools = lambda _payload: []
    chat_routes.resolve_credentials = lambda *_args: creds
    chat_routes._resolve_creds = lambda *_args: creds
    app = FastAPI()
    app.include_router(chat_routes.router)
    app.dependency_overrides[current_user] = lambda: {}
    remote_calls = []
    client = manager.get_client("fixture")
    original = client.call_tool

    async def observe(name, args):
        remote_calls.append(name)
        return await original(name, args)

    client.call_tool = observe
    summaries = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=120) as api:
            for decision in ("deny", "allow"):
                marker = "MCP-SYNTHETIC-ACCEPTANCE-ONLY"
                payload = {"messages": [{"role": "user", "content": f"Use mcp__fixture__echo exactly once with text '{marker}'. Never use other tools. If refused or failed, do not call it again: explain briefly and stop. On success report the exact returned text."}], "session_id": "mcp-live-" + decision, "model": "Text", "use_agent": True, "permission_mode": "bypassPermissions", "long_term_memory": "", "max_tokens": 2048}
                before = len(remote_calls)
                initial = await api.post("/api/chat", json=payload)
                events = decode(initial.text)
                confirmations = [event for event in events if event["type"] == "tool_confirm_required"]
                if initial.status_code != 200 or not confirmations or len(confirmations[-1]["calls"]) != 1:
                    raise RuntimeError("The real model did not reach a single expected confirmation")
                call = confirmations[-1]["calls"][0]
                if call["name"] != "mcp__fixture__echo" or call["args"] != {"text": marker} or len(remote_calls) != before:
                    raise RuntimeError("The expected tool was not safely paused")
                resumed = await api.post("/api/agent-sandbox/resume", json={"payload": payload, "decision": decision, "call_id": call["call_id"]})
                resumed_events = decode(resumed.text)
                answer = "".join(event.get("content", "") for event in resumed_events if event["type"] == "text")
                results = [event for event in resumed_events if event["type"] == "tool_result"]
                success = resumed.status_code == 200 and bool(answer.strip()) and any(event["type"] == "done" and event.get("success") for event in resumed_events) and not any(event["type"] in {"error", "tool_confirm_required"} for event in resumed_events)
                expected_success = decision == "allow"
                success = success and len(remote_calls) - before == int(expected_success) and any(event.get("success") is expected_success for event in results)
                if expected_success:
                    success = success and marker in answer
                summaries.append({"decision": decision, "paused_without_execution": True, "remote_calls": len(remote_calls) - before, "nonempty_answer": bool(answer.strip()), "passed": success})
                if not success:
                    # Fail safely instead of approving any extra tool or retrying.
                    await api.post("/api/agent-sandbox/resume", json={"payload": payload, "decision": "stop"})
                    break
    finally:
        await manager.shutdown()
        for decision in ("deny", "allow"):
            chat_routes._clear_pending_request_snapshot("mcp-live-" + decision)
    print(json.dumps({"model": "Text", "checks": summaries, "passed": len(summaries) == 2 and all(item["passed"] for item in summaries)}, ensure_ascii=False))
    return len(summaries) == 2 and all(item["passed"] for item in summaries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        passed = asyncio.run(run(args.credentials))
    except Exception:
        print(json.dumps({"passed": False, "error": "Real MCP acceptance did not complete; no credentials or remote detail reported"}))
        passed = False
    raise SystemExit(0 if passed else 1)
