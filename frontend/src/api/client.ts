// API 客户端 — 对齐后端 API 合约
// - 响应解包：{ success: true, data } / { success: false, error }
// - 全局 Header 注入（API-Key / Model / Provider）
// - SSE 支持 event: 行解析 + error 事件处理
// - WS 动态协议检测

import type { ChatSSEEvent, BuildSSEEvent } from "../types/api";
import { useSettingsStore } from "../stores/useSettingsStore";
import { useLogStore } from "../stores/useLogStore";

function getLog() {
  return useLogStore.getState().log;
}

// ─── ApiError ────────────────────────────────────────────────
export class ApiError extends Error {
  code: string;
  details: unknown;
  constructor(code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
  }
}

// ─── Auth Headers ────────────────────────────────────────────
export const AUTH_TOKEN_CHANGED_EVENT = "hwrag:auth-token-changed";

/**
 * Store or clear the session token and notify long-lived consumers (file
 * watcher SSE, WebSocket helpers) that they should reconnect with new auth.
 */
export function setSessionToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem("session_token", token);
    else sessionStorage.removeItem("session_token");
  } catch {
    // Storage may be unavailable in private mode; the in-memory callers can
    // still proceed, and the next request simply lacks a token.
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_TOKEN_CHANGED_EVENT));
  }
}

export function getSessionToken(): string | null {
  try {
    return sessionStorage.getItem("session_token");
  } catch {
    return null;
  }
}

export function getAuthHeaders(): Record<string, string> {
  const { resolveChatCreds } = useSettingsStore.getState();
  const creds = resolveChatCreds();
  const headers: Record<string, string> = {};
  if (creds.apiKey) headers["X-API-Key"] = creds.apiKey;
  // session_token 存于 sessionStorage，用于本地会话鉴权。
  const sessionToken = getSessionToken();
  if (sessionToken) headers["Authorization"] = `Bearer ${sessionToken}`;
  if (creds.model) headers["X-Model"] = creds.model;
  if (creds.providerId) headers["X-Provider"] = creds.providerId;
  if (creds.baseUrl) headers["X-Base-URL"] = creds.baseUrl;
  return headers;
}

// ─── Response Unwrapping ─────────────────────────────────────
async function unwrapResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // Try to read error body for a more informative message
    let detail = "";
    try {
      const body = await res.json();
      if (body?.error?.message) detail = body.error.message;
      else if (body?.message) detail = body.message;
    } catch {
      // body not JSON, fall back to statusText
    }
    throw new Error(detail || `API ${res.status}: ${res.statusText}`);
  }
  const json = await res.json();
  if (!("success" in json)) {
    // 兼容旧格式响应（如 /api/sessions, /api/settings 等不带 success 字段）
    return json as T;
  }
  if (json.success === true) return json.data as T;
  if (json.success === false) {
    const err = json.error ?? {};
    throw new ApiError(err.code ?? "UNKNOWN", err.message ?? "Unknown error", err.details);
  }
  throw new ApiError("INVALID_RESPONSE", "响应格式无效：success 字段值无效", json);
}

// ─── fetch with timeout ──────────────────────────────────────
function fetchWithTimeout(url: string, opts: RequestInit, timeoutMs = 8000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...opts, signal: controller.signal }).finally(() => clearTimeout(timer));
}

// ─── apiGet ──────────────────────────────────────────────────
export async function apiGet<T>(path: string, timeoutMs?: number): Promise<T> {
  const headers = { ...getAuthHeaders() };
  try {
    const res = await fetchWithTimeout(`/api/${path.replace(/^\//, "")}`, { headers }, timeoutMs);
    const result = await unwrapResponse<T>(res);
    getLog()("ok", "api", `GET ${path} → OK`);
    return result;
  } catch (err) {
    getLog()("error", "api", `GET ${path} → ${err instanceof Error ? err.message : String(err)}`);
    throw err;
  }
}

// ─── apiPost ─────────────────────────────────────────────────
export async function apiPost<T>(path: string, body?: unknown, timeoutMs?: number, customHeaders?: Record<string, string>): Promise<T> {
  const isFormData = body instanceof FormData;
  const authHeaders = getAuthHeaders();
  const finalHeaders = customHeaders ? { ...authHeaders, ...customHeaders } : authHeaders;
  const opts: RequestInit = {
    method: "POST",
    ...(isFormData
      ? { body, headers: finalHeaders }
      : { headers: { "Content-Type": "application/json", ...finalHeaders }, body: JSON.stringify(body) }),
  };
  try {
    const res = await fetchWithTimeout(`/api/${path.replace(/^\//, "")}`, opts, timeoutMs);
    const result = await unwrapResponse<T>(res);
    getLog()("ok", "api", `POST ${path} → OK`);
    return result;
  } catch (err) {
    getLog()("error", "api", `POST ${path} → ${err instanceof Error ? err.message : String(err)}`);
    throw err;
  }
}

// ─── apiUploadWithProgress ──────────────────────────────────
// 用 XMLHttpRequest 上传 FormData，支持上传进度回调和取消。
// fetch 无法拿到上传进度，所以需要 XHR。
export function apiUploadWithProgress<T>(
  path: string,
  formData: FormData,
  onProgress?: (percent: number) => void,
): { promise: Promise<T>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const authHeaders = getAuthHeaders();

  const promise = new Promise<T>((resolve, reject) => {
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      try {
        const json = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          // 对齐 unwrapResponse：解包 { success, data } envelope
          if (json && typeof json === "object" && "success" in json) {
            if (json.success === true) {
              getLog()("ok", "api", `POST ${path} → OK`);
              resolve(json.data as T);
            } else if (json.success === false) {
              const err = json.error ?? {};
              const msg = err.message ?? "Unknown error";
              getLog()("error", "api", `POST ${path} → ${msg}`);
              reject(new ApiError(err.code ?? "UNKNOWN", msg, err.details));
            } else {
              reject(new ApiError("INVALID_RESPONSE", "响应格式无效：success 字段值无效", json));
            }
          } else {
            // 旧格式兼容（不带 success 字段）
            getLog()("ok", "api", `POST ${path} → OK`);
            resolve(json as T);
          }
        } else {
          // HTTP 错误：优先从 envelope.error 提取，再回退到裸 message
          const envErr = json && typeof json === "object" ? json.error : null;
          const msg = envErr?.message || json?.message || `HTTP ${xhr.status}`;
          getLog()("error", "api", `POST ${path} → ${msg}`);
          reject(new ApiError(envErr?.code ?? "HTTP_ERROR", msg, envErr?.details));
        }
      } catch (e) {
        reject(new ApiError("PARSE_ERROR", `响应解析失败: ${e instanceof Error ? e.message : String(e)}`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error"));
    xhr.onabort = () => reject(new Error("aborted"));

    xhr.open("POST", `/api/${path.replace(/^\//, "")}`);
    Object.entries(authHeaders).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.send(formData);
  });

  return { promise, abort: () => xhr.abort() };
}

// ─── apiPut ─────────────────────────────────────────────────
export async function apiPut<T>(path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const authHeaders = getAuthHeaders();
  const opts: RequestInit = {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: body ? JSON.stringify(body) : undefined,
  };
  try {
    const res = await fetchWithTimeout(`/api/${path.replace(/^\//, "")}`, opts, timeoutMs);
    const result = await unwrapResponse<T>(res);
    getLog()("ok", "api", `PUT ${path} → OK`);
    return result;
  } catch (err) {
    getLog()("error", "api", `PUT ${path} → ${err instanceof Error ? err.message : String(err)}`);
    throw err;
  }
}

// ─── apiDelete ──────────────────────────────────────────────
export async function apiDelete<T>(path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const authHeaders = getAuthHeaders();
  const opts: RequestInit = {
    method: "DELETE",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: body ? JSON.stringify(body) : undefined,
  };
  try {
    const res = await fetchWithTimeout(`/api/${path.replace(/^\//, "")}`, opts, timeoutMs);
    const result = await unwrapResponse<T>(res);
    getLog()("ok", "api", `DELETE ${path} → OK`);
    return result;
  } catch (err) {
    getLog()("error", "api", `DELETE ${path} → ${err instanceof Error ? err.message : String(err)}`);
    throw err;
  }
}

// ─── apiPatch ───────────────────────────────────────────────
export async function apiPatch<T>(path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const authHeaders = getAuthHeaders();
  const opts: RequestInit = {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: body ? JSON.stringify(body) : undefined,
  };
  try {
    const res = await fetchWithTimeout(`/api/${path.replace(/^\//, "")}`, opts, timeoutMs);
    const result = await unwrapResponse<T>(res);
    getLog()("ok", "api", `PATCH ${path} → OK`);
    return result;
  } catch (err) {
    getLog()("error", "api", `PATCH ${path} → ${err instanceof Error ? err.message : String(err)}`);
    throw err;
  }
}

// ─── SSE Callbacks ───────────────────────────────────────────
/** Metadata parsed from the SSE `id:` line and the event payload. */
export interface SSEEventMeta {
  /** Raw value of the most recent `id:` line (e.g. "r_123:7"). */
  lastEventId?: string;
  /** Run id parsed from the id line or the event payload. */
  runId?: string;
  /** Monotonic per-run sequence parsed from the id line or the event payload. */
  seq?: number;
}

type SSEHandler = (event: ChatSSEEvent | BuildSSEEvent, meta?: SSEEventMeta) => void;
type SSECallback = {
  onEvent: SSEHandler;
  onDone?: () => void;
  onError?: (err: Error) => void;
};

export interface SSEReconnectOptions {
  /** Maximum reconnect attempts after an unexpected disconnect. Default 5. */
  maxAttempts?: number;
  /** First backoff delay in ms (doubles per attempt). Default 500. */
  baseDelayMs?: number;
  /** Backoff cap in ms. Default 8000. */
  maxDelayMs?: number;
}

export interface ApiSSEOptions {
  /** HTTP method; defaults to POST (the chat/build convention). */
  method?: "GET" | "POST";
  /** Resume sequence: sent as `?after=<seq>` and `Last-Event-ID` on GET. */
  after?: number;
  /** Explicit `Last-Event-ID` value (e.g. "r_1:7"). */
  lastEventId?: string;
  /** Reconnect with replay after an unexpected disconnect. */
  reconnect?: SSEReconnectOptions;
  /** Called once the run id is known (X-Run-Id header or run_started event). */
  onRunId?: (runId: string) => void;
}

export interface SSEResult {
  /** Last id the server sent, if any. */
  lastEventId?: string;
  runId?: string;
  lastSeq: number;
  done: boolean;
}

/** Parse an SSE event id of the form `<run_id>:<seq>`. */
export function parseSseEventId(value: string | undefined | null): { runId: string; seq: number } | null {
  if (!value) return null;
  const trimmed = value.trim();
  const idx = trimmed.lastIndexOf(":");
  if (idx <= 0 || idx === trimmed.length - 1) return null;
  const runId = trimmed.slice(0, idx);
  const seq = Number(trimmed.slice(idx + 1));
  if (!runId || !Number.isFinite(seq) || seq < 0) return null;
  return { runId, seq: Math.trunc(seq) };
}

function buildSseUrl(path: string, method: "GET" | "POST", afterSeq: number): string {
  let url = `/api/${path.replace(/^\//, "")}`;
  if (method === "GET") {
    url += `${url.includes("?") ? "&" : "?"}after=${afterSeq}`;
  }
  return url;
}

function buildSseError(status: number, statusText: string, body: unknown): ApiError {
  const envelope = body && typeof body === "object"
    ? ((body as { error?: unknown }).error ?? body)
    : null;
  const code = envelope && typeof envelope === "object" && typeof (envelope as { code?: unknown }).code === "string"
    ? (envelope as { code: string }).code
    : (status === 401 || status === 403 ? "AUTH_REQUIRED" : `HTTP_${status}`);
  const bodyMessage = envelope && typeof envelope === "object" && typeof (envelope as { message?: unknown }).message === "string"
    ? (envelope as { message: string }).message
    : "";
  const message = bodyMessage || (status === 401 || status === 403
    ? `认证失败：请先登录或检查访问令牌（HTTP ${status}）`
    : `SSE ${status}: ${statusText}`);
  return new ApiError(code, message);
}

async function readSseHttpError(res: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await res.clone().json();
  } catch {
    // Non-JSON error body: fall back to the status text.
  }
  return buildSseError(res.status, res.statusText, body);
}

function delayAbortable(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) { resolve(); return; }
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}

// ─── apiSSE ──────────────────────────────────────────────────
/**
 * Stream SSE from the backend.
 *
 * Persisted events carry `id: <run_id>:<seq>`; the parsed id and sequence are
 * exposed through `callbacks.onEvent(event, meta)` and the returned result.
 * When `options.reconnect` is set the stream is re-established with
 * `GET runs/{run_id}/events?after=<lastSeq>` after an unexpected disconnect,
 * using exponential backoff (5 attempts by default).
 */
export async function apiSSE(
  path: string,
  body: unknown,
  callbacks: SSECallback,
  externalController?: AbortController,
  options: ApiSSEOptions = {}
): Promise<SSEResult> {
  // The external controller represents user intent (stop / navigation).
  // Each connection gets its own controller so timeouts can retry without
  // cancelling the whole recovery loop.
  const userController = externalController ?? new AbortController();
  const reconnect = options.reconnect;
  const maxAttempts = reconnect ? Math.max(0, reconnect.maxAttempts ?? 5) : 0;
  const baseDelayMs = reconnect?.baseDelayMs ?? 500;
  const maxDelayMs = reconnect?.maxDelayMs ?? 8000;

  let requestMethod: "GET" | "POST" = options.method ?? "POST";
  let requestPath = path;
  const initialIdInfo = parseSseEventId(options.lastEventId);
  let lastEventId = options.lastEventId;
  let runId = initialIdInfo?.runId;
  let lastSeq = options.after ?? initialIdInfo?.seq ?? 0;
  let doneReceived = false;
  let consecutiveFailures = 0;
  let attempt = 0;
  let userAborted = false;
  let activeConnection: AbortController | null = null;

  const result = (): SSEResult => ({ lastEventId, runId, lastSeq, done: doneReceived });

  const onUserAbort = () => {
    userAborted = true;
    activeConnection?.abort();
  };
  if (userController.signal.aborted) {
    getLog()("info", "sse", `SSE ${path} aborted by user`);
    return result();
  }
  userController.signal.addEventListener("abort", onUserAbort);

  try {
    for (;;) {
      const connection = new AbortController();
      activeConnection = connection;
      const relayUserAbort = () => connection.abort();
      userController.signal.addEventListener("abort", relayUserAbort);

      let abortedByTimeout = false;
      let connTimer: ReturnType<typeof setTimeout> | null = null;
      let idleTimer: ReturnType<typeof setTimeout> | null = null;
      let failure: Error | null = null;
      let parseFailure = false;
      let stopReading = false;

      try {
        const url = buildSseUrl(requestPath, requestMethod, lastSeq);
        const headers: Record<string, string> = { ...getAuthHeaders() };
        if (requestMethod === "POST") {
          headers["Content-Type"] = "application/json";
        } else {
          const resumeId = lastEventId ?? (runId && lastSeq > 0 ? `${runId}:${lastSeq}` : undefined);
          if (resumeId) headers["Last-Event-ID"] = resumeId;
        }

        // Connection timeout 120s (RAG may load reranker/embedding models).
        connTimer = setTimeout(() => {
          abortedByTimeout = true;
          connection.abort();
        }, 120_000);

        const res = await fetch(url, {
          method: requestMethod,
          headers,
          ...(requestMethod === "POST" ? { body: JSON.stringify(body) } : {}),
          signal: connection.signal,
        });
        if (!res.ok) throw await readSseHttpError(res);

        const headerRunId = res.headers?.get("X-Run-Id") ?? res.headers?.get("x-run-id");
        if (headerRunId && !runId) {
          runId = headerRunId;
          options.onRunId?.(headerRunId);
        }
        getLog()("info", "sse", `SSE ${requestPath} connected${runId ? ` (run=${runId})` : ""}`);
        if (connTimer) { clearTimeout(connTimer); connTimer = null; }

        // Chat and run replay streams send heartbeats every 15s; build/upload
        // may be silent for much longer.
        const streamPath = requestPath.split("?")[0];
        const idleTimeoutMs = streamPath === "chat" || streamPath.startsWith("runs/") ? 45_000 : 30 * 60 * 1000;
        const resetIdleTimer = () => {
          if (idleTimer) clearTimeout(idleTimer);
          idleTimer = setTimeout(() => {
            abortedByTimeout = true;
            connection.abort();
          }, idleTimeoutMs);
        };
        resetIdleTimer();

        const reader = res.body?.getReader();
        if (!reader) throw new Error("No response body");
        const decoder = new TextDecoder();
        let buffer = "";
        let currentEvent = "";
        let dataBuffer = "";
        let eventId: string | undefined;

        const handlePayload = (payloadText: string, rawEvent: string, rawId: string | undefined): boolean => {
          try {
            const parsed = JSON.parse(payloadText) as Record<string, unknown>;
            consecutiveFailures = 0;
            if (!parsed.type && rawEvent) parsed.type = rawEvent;
            const event = parsed as unknown as ChatSSEEvent | BuildSSEEvent;

            const idInfo = parseSseEventId(rawId);
            const payloadRunId = typeof parsed.run_id === "string" ? parsed.run_id : undefined;
            const payloadSeq = typeof parsed.seq === "number" ? parsed.seq : undefined;
            const seq = payloadSeq ?? idInfo?.seq;
            const eventRunId = payloadRunId ?? idInfo?.runId;
            if (eventRunId && !runId) {
              runId = eventRunId;
              options.onRunId?.(eventRunId);
            }
            if (rawId) lastEventId = rawId;
            else if (eventRunId && seq !== undefined) lastEventId = `${eventRunId}:${seq}`;

            // Replay overlap: never apply the same sequence twice.
            if (seq !== undefined) {
              if (seq <= lastSeq) return false;
              lastSeq = seq;
            }

            const meta: SSEEventMeta = { lastEventId: rawId ?? lastEventId, runId: eventRunId ?? runId, seq };
            getLog()("debug", "sse", `SSE ${requestPath} event: ${event.type}${seq !== undefined ? ` #${seq}` : ""}`);
            if (meta.lastEventId !== undefined || meta.runId !== undefined || meta.seq !== undefined) {
              callbacks.onEvent(event, meta);
            } else {
              callbacks.onEvent(event);
            }

            if (userController.signal.aborted) {
              if (abortedByTimeout) {
                const errMsg = "连接超时，请检查网络或后端是否运行";
                getLog()("error", "sse", `SSE ${requestPath} read error: ${errMsg}`);
                failure = new Error(`SSE 读取异常: ${errMsg}`);
              } else {
                getLog()("info", "sse", `SSE ${requestPath} read aborted (controller already aborted)`);
              }
              return true;
            }
            if (event.type === "done") {
              getLog()("ok", "sse", `SSE ${requestPath} completed`);
              doneReceived = true;
              return true;
            }
          } catch {
            consecutiveFailures++;
            getLog()("warn", "sse", `SSE JSON 解析失败 (${consecutiveFailures}): ${payloadText.slice(0, 100)}`);
            if (consecutiveFailures >= 3) {
              failure = new Error("SSE 连续解析失败，请检查网络连接");
              parseFailure = true;
              return true;
            }
          }
          return false;
        };

        try {
          while (!doneReceived) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            resetIdleTimer();
            // SSE allows CRLF / CR / LF line endings.
            const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
            const lines = normalized.split("\n");
            buffer = lines.pop() ?? "";
            for (const line of lines) {
              if (line.startsWith("event:")) {
                currentEvent = line.slice(6).trim();
              } else if (line.startsWith("id:")) {
                eventId = line.slice(3).trim();
              } else if (line.startsWith("data:")) {
                const chunk = line.slice(5);
                if (dataBuffer) dataBuffer += "\n";
                dataBuffer += chunk.startsWith(" ") ? chunk.slice(1) : chunk;
              } else if (line === "") {
                const payloadText = dataBuffer;
                const rawEvent = currentEvent;
                const rawId = eventId;
                dataBuffer = "";
                currentEvent = "";
                eventId = undefined;
                if (payloadText && handlePayload(payloadText, rawEvent, rawId)) {
                  stopReading = true;
                  break;
                }
              }
            }
            if (stopReading) break;
          }

          // Defensive: process a trailing event that did not end with a blank line.
          if (!doneReceived && !stopReading && dataBuffer) {
            const payloadText = dataBuffer;
            const rawEvent = currentEvent;
            const rawId = eventId;
            dataBuffer = "";
            currentEvent = "";
            eventId = undefined;
            getLog()("debug", "sse", `SSE ${requestPath} trailing event`);
            handlePayload(payloadText, rawEvent, rawId);
          }
        } catch (readErr) {
          if (userController.signal.aborted && !abortedByTimeout) {
            getLog()("info", "sse", `SSE ${requestPath} read aborted (controller already aborted)`);
          } else if (doneReceived) {
            getLog()("info", "sse", `SSE ${requestPath} read error after done, ignoring`);
          } else if (abortedByTimeout) {
            const errMsg = "连接超时，请检查网络或后端是否运行";
            getLog()("error", "sse", `SSE ${requestPath} read error: ${errMsg}`);
            failure = new Error(`SSE 读取异常: ${errMsg}`);
          } else {
            const errMsg = readErr instanceof Error ? readErr.message : String(readErr);
            getLog()("error", "sse", `SSE ${requestPath} read error: ${errMsg}`);
            failure = new Error(`SSE 读取异常: ${errMsg}`);
          }
        }
      } catch (err) {
        if (userController.signal.aborted && !abortedByTimeout) {
          getLog()("info", "sse", `SSE ${requestPath} aborted by user`);
        } else if (doneReceived) {
          getLog()("info", "sse", `SSE ${requestPath} error after done, ignoring`);
        } else {
          const errMsg = abortedByTimeout
            ? "连接超时，请检查网络或后端是否运行"
            : (err instanceof Error ? err.message : String(err));
          getLog()("error", "sse", `SSE ${requestPath} failed: ${errMsg}`);
          failure = failure ?? new Error(errMsg);
        }
      } finally {
        if (connTimer) clearTimeout(connTimer);
        if (idleTimer) clearTimeout(idleTimer);
        userController.signal.removeEventListener("abort", relayUserAbort);
        if (activeConnection === connection) activeConnection = null;
      }

      if (doneReceived) break;
      if (userAborted || userController.signal.aborted) break;

      // Unexpected disconnect: retry with replay when configured.
      if (parseFailure || !reconnect || attempt >= maxAttempts) {
        if (!failure) failure = new Error("连接断开，点击重试");
        if (!parseFailure && reconnect && attempt >= maxAttempts && attempt > 0) {
          failure = new Error(`连接断开，已重连 ${maxAttempts} 次仍未恢复: ${failure.message}`);
        }
        callbacks.onError?.(failure);
        return result();
      }
      if (!runId) {
        // Keep the concrete HTTP/auth failure instead of masking it with the
        // generic "cannot determine run id" message.
        callbacks.onError?.(failure ?? new Error("连接断开且无法确定运行 ID，点击重试"));
        return result();
      }

      attempt++;
      const delay = Math.min(baseDelayMs * 2 ** (attempt - 1), maxDelayMs);
      getLog()("warn", "sse", `SSE ${requestPath} disconnected, reconnect ${attempt}/${maxAttempts} in ${delay}ms`);
      await delayAbortable(delay, userController.signal);
      if (userAborted || userController.signal.aborted) break;

      requestMethod = "GET";
      requestPath = `runs/${encodeURIComponent(runId)}/events`;
    }
  } finally {
    userController.signal.removeEventListener("abort", onUserAbort);
  }

  if (doneReceived) callbacks.onDone?.();
  return result();
}

// ─── apiWS ───────────────────────────────────────────────────
export function apiWS(
  endpoint: string,
  handlers: {
    onOpen?: () => void;
    onMessage?: (data: string) => void;
    onClose?: () => void;
    onError?: (err: Event) => void;
  }
): WebSocket {
  const explicitWsUrl = import.meta.env.VITE_WS_URL as string | undefined;
  let url: string;
  if (explicitWsUrl) {
    url = explicitWsUrl;
  } else {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const isLocalDev = window.location.port === "5173" || ["127.0.0.1", "localhost"].includes(window.location.hostname);
    const port = isLocalDev ? "58080" : window.location.port || (window.location.protocol === "https:" ? "443" : "80");
    url = `${protocol}//${window.location.hostname}:${port}${endpoint}`;
  }
  // Inject session_token for WebSocket auth — mirrors getAuthHeaders for fetch.
  // Backend ws_auth() reads it from query_params["token"] before accept().
  const sessionToken = getSessionToken();
  if (sessionToken) {
    const sep = url.includes("?") ? "&" : "?";
    url = `${url}${sep}token=${encodeURIComponent(sessionToken)}`;
  }
  const ws = new WebSocket(url);
  ws.addEventListener("open", () => {
    getLog()("ok", "ws", `WS ${endpoint} connected`);
    handlers.onOpen?.();
  });
  ws.addEventListener("message", (e) => handlers.onMessage?.(e.data));
  ws.addEventListener("close", () => {
    getLog()("info", "ws", `WS ${endpoint} closed`);
    handlers.onClose?.();
  });
  ws.addEventListener("error", (e) => {
    getLog()("error", "ws", `WS ${endpoint} error`);
    handlers.onError?.(e);
  });
  return ws;
}

// ─── Big Chunk API ──────────────────────────────────────────
export interface BigChunkData {
  big_chunk_id: string;
  text: string;
  section_title: string;
  page_start: number | null;
  page_end: number | null;
  doc_id: string;
  kb_id: string;
}

export async function fetchBigChunk(bigChunkId: string): Promise<BigChunkData> {
  return apiGet<BigChunkData>(`kb/big-chunks/${encodeURIComponent(bigChunkId)}`);
}
