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
function getAuthHeaders(): Record<string, string> {
  const { activeProvider, model, providerKeys } = useSettingsStore.getState();
  const { getBaseUrl } = useSettingsStore.getState();
  const headers: Record<string, string> = {};
  const apiKey = providerKeys[activeProvider];
  if (apiKey) headers["X-API-Key"] = apiKey;
  // 从 localStorage 读取 session_token，添加 Authorization header
  const sessionToken = localStorage.getItem("session_token");
  if (sessionToken) headers["Authorization"] = `Bearer ${sessionToken}`;
  if (model) headers["X-Model"] = model;
  if (activeProvider) headers["X-Provider"] = activeProvider;
  const baseUrl = getBaseUrl(activeProvider);
  if (baseUrl) headers["X-Base-URL"] = baseUrl;
  return headers;
}

// ─── Response Unwrapping ─────────────────────────────────────
async function unwrapResponse<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
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
export async function apiPost<T>(path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const isFormData = body instanceof FormData;
  const authHeaders = getAuthHeaders();
  const opts: RequestInit = {
    method: "POST",
    ...(isFormData
      ? { body, headers: authHeaders }
      : { headers: { "Content-Type": "application/json", ...authHeaders }, body: JSON.stringify(body) }),
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

// ─── SSE Callbacks ───────────────────────────────────────────
type SSEHandler = (event: ChatSSEEvent | BuildSSEEvent) => void;
type SSECallback = {
  onEvent: SSEHandler;
  onDone?: () => void;
  onError?: (err: Error) => void;
};

// ─── apiSSE ──────────────────────────────────────────────────
export async function apiSSE(
  path: string,
  body: unknown,
  callbacks: SSECallback,
  externalController?: AbortController
): Promise<void> {
  // 使用外部传入的 controller（用于用户主动中止），或创建新的
  const controller = externalController ?? new AbortController();
  let abortedByTimeout = false;
  let connTimer: ReturnType<typeof setTimeout> | null = null;
  let consecutiveFailures = 0;
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  let resetIdleTimer: (() => void) | null = null;
  try {
    // 连接超时 60s（LLM 思考可能耗时较长），收到响应头后清除
    connTimer = setTimeout(() => { abortedByTimeout = true; controller.abort(); }, 60_000);

    const authHeaders = getAuthHeaders();
    const res = await fetch(`/api/${path.replace(/^\//, "")}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!res.ok) throw new Error(`SSE ${res.status}: ${res.statusText}`);

    getLog()("info", "sse", `SSE ${path} connected`);
    // 连接超时已过，转为读超时：5 分钟无数据则断开
    if (connTimer) clearTimeout(connTimer);
    idleTimer = null;
    const IDLE_TIMEOUT = 5 * 60 * 1000;
    resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        abortedByTimeout = true;
        controller.abort();
      }, IDLE_TIMEOUT);
    };
    resetIdleTimer();

    const reader = res.body?.getReader();
    if (!reader) throw new Error("No response body");
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEvent = "";
    let dataBuffer = "";

    // Read loop wrapped in try/catch to handle reader.read() exceptions explicitly
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        resetIdleTimer();
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            if (dataBuffer) dataBuffer += "\n";
            dataBuffer += line.slice(6);
          } else if (line === "") {
            if (dataBuffer) {
              try {
                const parsed = JSON.parse(dataBuffer);
                consecutiveFailures = 0;
                // 如果解析出的对象没有 type 字段，用 SSE event 行的值补充
                if (!parsed.type && currentEvent) parsed.type = currentEvent;
                const event = parsed as ChatSSEEvent | BuildSSEEvent;

                getLog()("debug", "sse", `SSE ${path} event: ${event.type}`);
                callbacks.onEvent(event);
                // error 事件后通常跟着 done，不提前 return
                if (event.type === "done") {
                  getLog()("ok", "sse", `SSE ${path} completed`);
                // onDone 在 stream 结束后统一触发
                }
              } catch {
                consecutiveFailures++;
                getLog()("warn", "sse", `SSE JSON 解析失败 (${consecutiveFailures}): ${dataBuffer.slice(0, 100)}`);
                if (consecutiveFailures >= 3) {
                  callbacks.onError?.(new Error("SSE 连续解析失败，请检查网络连接"));
                  consecutiveFailures = 0;
                }
              }
              dataBuffer = "";
              currentEvent = "";
            }
          }
        }
      }
    } catch (readErr) {
      // reader.read() threw (network error, stream aborted by server, etc.)
      const errMsg = readErr instanceof Error ? readErr.message : String(readErr);
      getLog()("error", "sse", `SSE ${path} read error: ${errMsg}`);
      callbacks.onError?.(new Error(`SSE 读取异常: ${errMsg}`));
      return;
    }
    // 流式正常结束，触发 onDone
    callbacks.onDone?.();

  } catch (err) {
    // 用户主动中止（切换会话/停止流式）不应触发错误回调
    if (controller.signal.aborted && !abortedByTimeout) {
      getLog()("info", "sse", `SSE ${path} aborted by user`);
      return;
    }
    // 连接超时或其他错误，触发错误回调
    const errMsg = abortedByTimeout ? "连接超时，请检查网络或后端是否运行" : (err instanceof Error ? err.message : String(err));
    getLog()("error", "sse", `SSE ${path} failed: ${errMsg}`);
    callbacks.onError?.(new Error(errMsg));
  } finally {
    if (connTimer) clearTimeout(connTimer);
    if (typeof idleTimer !== "undefined" && idleTimer) clearTimeout(idleTimer);
  }
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
    const port = window.location.port === "5173" || window.location.hostname === "127.0.0.1"
      ? "58080"
      : window.location.port || (window.location.protocol === "https:" ? "443" : "80");
    url = `${protocol}//${window.location.hostname}:${port}/api${endpoint}`;
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
