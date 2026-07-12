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
export function getAuthHeaders(): Record<string, string> {
  const { resolveChatCreds } = useSettingsStore.getState();
  const creds = resolveChatCreds();
  const headers: Record<string, string> = {};
  if (creds.apiKey) headers["X-API-Key"] = creds.apiKey;
  // 从 localStorage 读取 session_token，添加 Authorization header
  const sessionToken = localStorage.getItem("session_token");
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
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) {
          getLog()("ok", "api", `POST ${path} → OK`);
          resolve(data as T);
        } else {
          const msg = data?.error?.message || data?.message || `HTTP ${xhr.status}`;
          getLog()("error", "api", `POST ${path} → ${msg}`);
          reject(new Error(msg));
        }
      } catch (e) {
        reject(e);
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
  let doneReceived = false;
  try {
    // 连接超时 120s（RAG 首次加载 reranker/embedding 模型可能较慢），收到响应头后清除
    connTimer = setTimeout(() => { abortedByTimeout = true; controller.abort(); }, 120_000);

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
      while (!doneReceived) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        resetIdleTimer();
        // Normalize line endings to support CRLF / CR / LF (SSE spec allows all)
        const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        const lines = normalized.split("\n");
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
                // done 事件后立即触发 onDone 并退出循环，不依赖流关闭
                if (event.type === "done") {
                  getLog()("ok", "sse", `SSE ${path} completed`);
                  doneReceived = true;
                  break;
                }
              } catch {
                consecutiveFailures++;
                getLog()("warn", "sse", `SSE JSON 解析失败 (${consecutiveFailures}): ${dataBuffer.slice(0, 100)}`);
                if (consecutiveFailures >= 3) {
                  callbacks.onError?.(new Error("SSE 连续解析失败，请检查网络连接"));
                  consecutiveFailures = 0;
                  controller.abort();
                  return;
                }
              }
              dataBuffer = "";
              currentEvent = "";
            }
          }
        }
      }

      // Process any trailing event that didn't end with an empty line (defensive)
      if (!doneReceived && dataBuffer) {
        try {
          const parsed = JSON.parse(dataBuffer);
          consecutiveFailures = 0;
          if (!parsed.type && currentEvent) parsed.type = currentEvent;
          const event = parsed as ChatSSEEvent | BuildSSEEvent;
          getLog()("debug", "sse", `SSE ${path} trailing event: ${event.type}`);
          callbacks.onEvent(event);
          if (event.type === "done") {
            getLog()("ok", "sse", `SSE ${path} completed (trailing)`);
            doneReceived = true;
          }
        } catch {
          consecutiveFailures++;
          getLog()("warn", "sse", `SSE trailing JSON 解析失败 (${consecutiveFailures}): ${dataBuffer.slice(0, 100)}`);
        }
        dataBuffer = "";
        currentEvent = "";
      }
    } catch (readErr) {
      // 如果 controller 已被中止（如 onEvent 处理 error 事件后调用了 stopStreaming），
      // 不再触发 onError，避免重复处理
      if (controller.signal.aborted && !abortedByTimeout) {
        getLog()("info", "sse", `SSE ${path} read aborted (controller already aborted)`);
        return;
      }
      // 已收到 done 事件后流关闭产生的异常，视为正常结束的副作用，不报错
      if (doneReceived) {
        getLog()("info", "sse", `SSE ${path} read error after done, ignoring`);
        return;
      }
      // reader.read() threw (network error, stream aborted by server, etc.)
      const errMsg = readErr instanceof Error ? readErr.message : String(readErr);
      getLog()("error", "sse", `SSE ${path} read error: ${errMsg}`);
      callbacks.onError?.(new Error(`SSE 读取异常: ${errMsg}`));
      return;
    }
    // 收到 done 事件视为正常结束触发 onDone；流关闭但未收到 done 视为中途断开
    if (doneReceived) {
      callbacks.onDone?.();
    } else {
      getLog()("warn", "sse", `SSE ${path} stream closed without done event`);
      callbacks.onError?.(new Error("连接断开，点击重试"));
    }

  } catch (err) {
    // 用户主动中止（切换会话/停止流式）不应触发错误回调
    if (controller.signal.aborted && !abortedByTimeout) {
      getLog()("info", "sse", `SSE ${path} aborted by user`);
      return;
    }
    // 已收到 done 事件后的连接关闭异常，视为正常结束的副作用，不报错
    if (doneReceived) {
      getLog()("info", "sse", `SSE ${path} error after done, ignoring`);
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
    url = `${protocol}//${window.location.hostname}:${port}${endpoint}`;
  }
  // Inject session_token for WebSocket auth — mirrors getAuthHeaders for fetch.
  // Backend ws_auth() reads it from query_params["token"] before accept().
  const sessionToken = localStorage.getItem("session_token");
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
