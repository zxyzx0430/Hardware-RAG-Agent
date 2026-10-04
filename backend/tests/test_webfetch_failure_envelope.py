"""Integration tests for WebFetchTool failures through ToolRouter."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from src.agent.core.toolkit.tool_router import ToolRouter
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.retrieval import webfetch as webfetch_module
from src.agent.tools.groups.retrieval.webfetch import (
    WebFetchTool,
    _WEBFETCH_HTTPX_URL_FILTER,
)


class _NoopAuditRecorder:
    def record(self, _record: object) -> None:
        pass


class _DictionaryPayloadTool(WebFetchTool):
    name: str = "dictionary_payload"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        return {"error": "application payload", "value": "preserved"}


def _mock_http_responses(
    monkeypatch: pytest.MonkeyPatch,
    response_for: Callable[[httpx.Request], httpx.Response],
) -> list[str]:
    original_client = httpx.AsyncClient
    requested_urls: list[str] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return response_for(request)

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle_request)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return requested_urls


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 503])
async def test_http_status_failures_use_tool_router_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status_code: int,
) -> None:
    secret_url = "https://user:pass@example.test/page?token=query-secret"
    caplog.set_level(logging.INFO, logger="httpx")
    _mock_http_responses(
        monkeypatch,
        lambda request: httpx.Response(status_code, request=request),
    )
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-http-error",
        tool_name="webfetch",
        args={"url": secret_url, "prompt": "Find the value"},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    assert result["success"] is False
    assert result["data"] is None
    assert result["error"]["error_type"] == "EXEC_ERROR"
    assert f"HTTP {status_code}" in result["error"]["error_message"]
    assert "user:pass" not in str(result)
    assert "query-secret" not in str(result)
    assert secret_url not in str(result)
    assert "user:pass" not in caplog.text
    assert secret_url not in caplog.text
    assert "query-secret" not in caplog.text


@pytest.mark.asyncio
async def test_redirect_policy_failure_is_distinct_and_does_not_follow_redirect(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "https://user:pass@example.test/page?token=query-secret"
    caplog.set_level(logging.INFO, logger="httpx")
    requested_urls = _mock_http_responses(
        monkeypatch,
        lambda request: httpx.Response(
            301,
            headers={"Location": "https://other.test/final"},
            request=request,
        ),
    )
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-redirect-error",
        tool_name="webfetch",
        args={"url": secret_url, "prompt": "Find the value"},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    message = result["error"]["error_message"]
    assert result["success"] is False
    assert result["error"]["error_type"] == "EXEC_ERROR"
    assert "redirect" in message.lower()
    assert "HTTP 301" in message
    assert "network" not in message.lower()
    assert requested_urls == [secret_url]
    assert "user:pass" not in str(result)
    assert "query-secret" not in str(result)
    assert secret_url not in str(result)
    assert "user:pass" not in caplog.text
    assert "query-secret" not in caplog.text
    assert secret_url not in caplog.text


@pytest.mark.asyncio
async def test_httpx_info_log_redaction_is_scoped_to_webfetch(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "https://user:pass@example.test/page?token=query-secret"
    unrelated_url = "https://other.test/status?trace=keep-visible"
    caplog.set_level(logging.INFO, logger="httpx")

    def respond_while_other_request_logs(request: httpx.Request) -> httpx.Response:
        thread_errors: list[BaseException] = []

        def emit_unrelated_log() -> None:
            try:
                contextvars.Context().run(
                    logging.getLogger("httpx").info,
                    "HTTP Request: GET %s HTTP/1.1 200 OK",
                    unrelated_url,
                )
            except BaseException as exc:
                thread_errors.append(exc)

        thread = threading.Thread(target=emit_unrelated_log)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert thread_errors == []
        return httpx.Response(404, request=request)

    _mock_http_responses(monkeypatch, respond_while_other_request_logs)
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-httpx-log-redaction",
        tool_name="webfetch",
        args={"url": secret_url, "prompt": "Find the value"},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    assert result["success"] is False
    assert "HTTP 404" in result["error"]["error_message"]
    assert "HTTP Request: GET [URL redacted]" in caplog.text
    assert "HTTP/1.1 404 Not Found" in caplog.text
    assert unrelated_url in caplog.text
    assert "user:pass" not in caplog.text
    assert "query-secret" not in caplog.text
    assert secret_url not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["http_status", "cancelled"])
async def test_httpx_url_filter_is_removed_after_exception_or_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    exit_kind: str,
) -> None:
    secret_url = "https://user:pass@example.test/page?token=query-secret"
    caplog.set_level(logging.INFO, logger="httpx")
    tool = WebFetchTool()

    if exit_kind == "http_status":
        _mock_http_responses(
            monkeypatch,
            lambda request: httpx.Response(404, request=request),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await tool._fetch_html(secret_url)
    else:
        original_client = httpx.AsyncClient
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, request=request)
            )
            client = original_client(*args, **kwargs)
            actual_get = client.get

            async def blocked_get(
                *get_args: Any,
                **get_kwargs: Any,
            ) -> httpx.Response:
                request_started.set()
                await release_request.wait()
                return await actual_get(*get_args, **get_kwargs)

            client.get = blocked_get
            return client

        monkeypatch.setattr(httpx, "AsyncClient", client_factory)
        fetch_task = asyncio.create_task(tool._fetch_html(secret_url))
        await asyncio.wait_for(request_started.wait(), timeout=2)
        fetch_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fetch_task

    assert _WEBFETCH_HTTPX_URL_FILTER not in logging.getLogger("httpx").filters
    caplog.clear()
    unrelated_url = "https://other.test/after-exit?trace=still-visible"
    logging.getLogger("httpx").info(
        "HTTP Request: GET %s HTTP/1.1 200 OK",
        unrelated_url,
    )
    assert unrelated_url in caplog.text
    assert "[URL redacted]" not in caplog.text


@pytest.mark.asyncio
async def test_overlapping_webfetches_keep_filter_until_last_request_exits(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    httpx_logger = logging.getLogger("httpx")
    baseline_scopes = webfetch_module._HTTPX_LOG_FILTER_ACTIVE_SCOPES
    baseline_filter_installed = _WEBFETCH_HTTPX_URL_FILTER in httpx_logger.filters
    assert baseline_scopes == 0
    assert baseline_filter_installed is False

    a_body_started = asyncio.Event()
    a_body_release = asyncio.Event()
    b_transport_waiting = asyncio.Event()
    b_response_release = asyncio.Event()
    b_body_started = asyncio.Event()
    b_body_release = asyncio.Event()

    class _GatedBody(httpx.AsyncByteStream):
        def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
            self._started = started
            self._release = release

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self._started.set()
            await self._release.wait()
            yield b"<html><body>GPIO value is 3.3 V.</body></html>"

        async def aclose(self) -> None:
            return None

    def response_for(request: httpx.Request) -> httpx.Response:
        request_id = request.url.params.get("request")
        if request_id == "failure":
            return httpx.Response(404, request=request)
        started, release = {
            "A": (a_body_started, a_body_release),
            "B": (b_body_started, b_body_release),
        }[request_id]
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            stream=_GatedBody(started, release),
            request=request,
        )

    class _EventGatedMockTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self._mock = httpx.MockTransport(response_for)

        async def handle_async_request(
            self,
            request: httpx.Request,
        ) -> httpx.Response:
            if request.url.params.get("request") == "B":
                b_transport_waiting.set()
                await b_response_release.wait()
            return await self._mock.handle_async_request(request)

        async def aclose(self) -> None:
            await self._mock.aclose()

    original_client = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = _EventGatedMockTransport()
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    tool = WebFetchTool()
    url_a = "https://user:pass@example.test/page?request=A&token=secret-A"
    url_b = "https://user:pass@example.test/page?request=B&token=secret-B"
    task_a = asyncio.create_task(tool._fetch_html(url_a))
    task_b = asyncio.create_task(tool._fetch_html(url_b))

    try:
        await asyncio.wait_for(a_body_started.wait(), timeout=2)
        await asyncio.wait_for(b_transport_waiting.wait(), timeout=2)
        assert webfetch_module._HTTPX_LOG_FILTER_ACTIVE_SCOPES == baseline_scopes + 2
        assert _WEBFETCH_HTTPX_URL_FILTER in httpx_logger.filters

        a_body_release.set()
        assert "GPIO" in await asyncio.wait_for(task_a, timeout=2)
        assert not task_b.done()
        assert webfetch_module._HTTPX_LOG_FILTER_ACTIVE_SCOPES == baseline_scopes + 1
        assert _WEBFETCH_HTTPX_URL_FILTER in httpx_logger.filters

        def httpx_messages() -> list[str]:
            return [
                record.getMessage()
                for record in caplog.records
                if record.name == "httpx"
            ]
        messages_after_a = httpx_messages()
        assert sum("[URL redacted]" in message for message in messages_after_a) == 1
        assert url_a not in "\n".join(messages_after_a)

        parent_url = "https://parent.test/health?trace=keep-parent"
        async with original_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"ok", request=request)
            )
        ) as parent_client:
            response = await parent_client.get(parent_url)
        assert response.status_code == 200
        assert parent_url in "\n".join(httpx_messages())
        assert not task_b.done()

        b_response_release.set()
        await asyncio.wait_for(b_body_started.wait(), timeout=2)
        messages_after_b_starts = httpx_messages()
        assert sum("[URL redacted]" in message for message in messages_after_b_starts) == 2
        assert url_b not in "\n".join(messages_after_b_starts)
        assert parent_url in "\n".join(messages_after_b_starts)
        assert not task_b.done()

        b_body_release.set()
        assert "GPIO" in await asyncio.wait_for(task_b, timeout=2)
        assert webfetch_module._HTTPX_LOG_FILTER_ACTIVE_SCOPES == baseline_scopes
        assert (_WEBFETCH_HTTPX_URL_FILTER in httpx_logger.filters) is baseline_filter_installed

        failure_url = "https://user:pass@example.test/failure?request=failure&token=secret-failure"
        with pytest.raises(httpx.HTTPStatusError):
            await tool._fetch_html(failure_url)
        assert webfetch_module._WEBFETCH_HTTPX_URL_REDACTION.get() is False
        assert webfetch_module._HTTPX_LOG_FILTER_ACTIVE_SCOPES == baseline_scopes
        assert (_WEBFETCH_HTTPX_URL_FILTER in httpx_logger.filters) is baseline_filter_installed
        assert failure_url not in "\n".join(httpx_messages())
    finally:
        a_body_release.set()
        b_response_release.set()
        b_body_release.set()
        pending_tasks = [task for task in (task_a, task_b) if not task.done()]
        for task in pending_tasks:
            task.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_request_timeout_uses_router_timeout_envelope_without_url_leak(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "https://user:pass@example.test/page?token=query-secret"

    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            f"timed out fetching {secret_url}",
            request=request,
        )

    _mock_http_responses(monkeypatch, time_out)
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-timeout",
        tool_name="webfetch",
        args={"url": secret_url, "prompt": "Find the value"},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    assert result["success"] is False
    assert result["error"]["error_type"] == "TIMEOUT"
    assert "timed out" in result["error"]["error_message"].lower()
    assert "user:pass" not in str(result)
    assert "query-secret" not in str(result)
    assert secret_url not in caplog.text


@pytest.mark.asyncio
async def test_network_failure_is_distinct_from_redirect_policy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "https://user:pass@example.test/page?token=query-secret"

    def fail_to_connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"connection failed for {secret_url}",
            request=request,
        )

    _mock_http_responses(monkeypatch, fail_to_connect)
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-network-error",
        tool_name="webfetch",
        args={"url": secret_url, "prompt": "Find the value"},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    message = result["error"]["error_message"]
    assert result["success"] is False
    assert result["error"]["error_type"] == "EXEC_ERROR"
    assert "network request failed" in message.lower()
    assert "redirect" not in message.lower()
    assert "user:pass" not in str(result)
    assert "query-secret" not in str(result)
    assert secret_url not in caplog.text


@pytest.mark.asyncio
async def test_empty_response_body_uses_tool_router_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_http_responses(
        monkeypatch,
        lambda request: httpx.Response(200, content=b"", request=request),
    )
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-empty-body",
        tool_name="webfetch",
        args={"url": "https://example.test/empty", "prompt": "Find the value"},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    assert result["success"] is False
    assert result["data"] is None
    assert result["error"]["error_type"] == "EXEC_ERROR"
    assert "body is empty" in result["error"]["error_message"].lower()


@pytest.mark.asyncio
async def test_valid_html_keeps_existing_success_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/page"
    prompt = "Find the GPIO voltage"
    _mock_http_responses(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body><h1>GPIO</h1><p>Voltage is 3.3 V.</p></body></html>",
            request=request,
        ),
    )
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())

    result = await router.dispatch(
        call_id="webfetch-valid-html",
        tool_name="webfetch",
        args={"url": url, "prompt": prompt},
        ctx=ToolContext(),
        tool_spec=WebFetchTool(),
    )

    assert result["success"] is True
    assert result["error"] is None
    assert set(result["data"]) == {"url", "content", "prompt", "truncated"}
    assert result["data"]["url"] == url
    assert result["data"]["prompt"] == prompt
    assert "GPIO" in result["data"]["content"]
    assert result["data"]["truncated"] is False


@pytest.mark.asyncio
async def test_other_dict_payloads_with_error_key_remain_successful() -> None:
    router = ToolRouter(audit_recorder=_NoopAuditRecorder())
    tool = _DictionaryPayloadTool()

    result = await router.dispatch(
        call_id="dictionary-error-field",
        tool_name=tool.name,
        args={"url": "https://example.test", "prompt": "Read"},
        ctx=ToolContext(),
        tool_spec=tool,
    )

    assert result["success"] is True
    assert result["error"] is None
    assert result["data"] == {"error": "application payload", "value": "preserved"}
