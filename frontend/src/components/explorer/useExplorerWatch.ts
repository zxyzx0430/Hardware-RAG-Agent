import { useEffect, useRef } from "react";
import { useLogStore } from "../../stores/useLogStore";
import { AUTH_TOKEN_CHANGED_EVENT, getAuthHeaders, getSessionToken } from "../../api/client";

export interface WatchEvent {
  type: "change" | "create" | "delete" | "rename" | "heartbeat";
  path?: string;
  src_path?: string;
  dest_path?: string;
  is_directory?: boolean;
}

/** Marker error: the watcher needs a login/session token before it can connect. */
export class WatchAuthRequiredError extends Error {
  constructor(message = "SSE 401: Unauthorized") {
    super(message);
    this.name = "WatchAuthRequiredError";
  }
}

const IDLE_TIMEOUT_MS = 5 * 60 * 1000;
// Exponential backoff reconnect: 1s, 2s, 4s, 8s, 16s, 30s (capped), max 10 attempts.
const MAX_RETRIES = 10;
const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1000;
const AUTH_POLL_MS = 2000;

function backoffDelay(attempt: number): number {
  return Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
}

function parseEvent(buffer: string): WatchEvent | null {
  const lines = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let event = "";
  let data = "";
  for (const line of lines) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data = line.slice(6);
  }
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as WatchEvent;
    if (!parsed.type && event) parsed.type = event as WatchEvent["type"];
    return parsed;
  } catch {
    return null;
  }
}

export function useExplorerWatch(
  rootPath: string | null,
  onEvent?: (event: WatchEvent) => void,
  onError?: (err: Error) => void,
  onStatusChange?: (connected: boolean) => void,
  onAuthRequired?: () => void,
) {
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const onStatusRef = useRef(onStatusChange);
  const onAuthRequiredRef = useRef(onAuthRequired);

  useEffect(() => {
    onEventRef.current = onEvent;
    onErrorRef.current = onError;
    onStatusRef.current = onStatusChange;
    onAuthRequiredRef.current = onAuthRequired;
  }, [onEvent, onError, onStatusChange, onAuthRequired]);

  useEffect(() => {
    if (!rootPath) return;
    // stopped=true means manual teardown (unmount / rootPath change): do NOT reconnect.
    let stopped = false;
    let retryCount = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    let attemptController: AbortController | null = null;
    let idleAborted = false;
    // Auth retry state: after a 401 wait for a new session token instead of
    // burning the retry budget and surfacing a toast per attempt.
    let knownToken = getSessionToken();
    let waitingForAuth = false;
    let authNotified = false;
    let authPollTimer: ReturnType<typeof setInterval> | null = null;

    const setStatus = (connected: boolean) => onStatusRef.current?.(connected);

    const resetIdleTimer = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        // No heartbeat for IDLE_TIMEOUT_MS: abort current attempt to trigger reconnect.
        idleAborted = true;
        attemptController?.abort();
      }, IDLE_TIMEOUT_MS);
    };

    const scheduleReconnect = () => {
      if (stopped) return;
      if (retryCount >= MAX_RETRIES) {
        useLogStore.getState().log("error", "sse", `explorer/watch gave up after ${MAX_RETRIES} retries`);
        return;
      }
      const delay = backoffDelay(retryCount);
      retryCount += 1;
      console.warn(`[explorer/watch] reconnecting in ${delay}ms (attempt ${retryCount}/${MAX_RETRIES})`);
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!stopped) void connect();
      }, delay);
    };

    const stopAuthWait = () => {
      waitingForAuth = false;
      if (authPollTimer) {
        clearInterval(authPollTimer);
        authPollTimer = null;
      }
    };

    const waitForAuth = () => {
      waitingForAuth = true;
      if (!authNotified) {
        authNotified = true;
        onAuthRequiredRef.current?.();
      }
      if (authPollTimer) return;
      // Fallback for login flows that write sessionStorage without using
      // setSessionToken(); the custom event handles the normal path instantly.
      authPollTimer = setInterval(() => {
        const token = getSessionToken();
        if (token && token !== knownToken) {
          knownToken = token;
          stopAuthWait();
          authNotified = false;
          retryCount = 0;
          void connect();
        }
      }, AUTH_POLL_MS);
    };

    const tryTokenReconnect = () => {
      const token = getSessionToken();
      if (!token || token === knownToken) return;
      knownToken = token;
      if (!waitingForAuth) return;
      stopAuthWait();
      authNotified = false;
      retryCount = 0;
      void connect();
    };

    const onAuthTokenChanged = () => tryTokenReconnect();
    window.addEventListener(AUTH_TOKEN_CHANGED_EVENT, onAuthTokenChanged);

    const connect = async () => {
      idleAborted = false;
      attemptController = new AbortController();
      const controller = attemptController;
      try {
        const url = `/api/explorer/watch?path=${encodeURIComponent(rootPath)}`;
        const res = await fetch(url, { headers: getAuthHeaders(), signal: controller.signal });
        if (!res.ok) {
          if (res.status === 401 || res.status === 403) {
            throw new WatchAuthRequiredError(`SSE ${res.status}: ${res.statusText || "Unauthorized"}`);
          }
          throw new Error(`SSE ${res.status}: ${res.statusText}`);
        }
        if (!res.body) throw new Error("No response body");
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        resetIdleTimer();
        // Connection established: reset retry counter and notify connected.
        retryCount = 0;
        knownToken = getSessionToken();
        authNotified = false;
        stopAuthWait();
        setStatus(true);
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
          const lines = normalized.split("\n");
          buffer = lines.pop() ?? "";
          let chunk = "";
          for (const line of lines) {
            if (line === "") {
              const event = parseEvent(chunk);
              if (event) onEventRef.current?.(event);
              chunk = "";
            } else {
              chunk += (chunk ? "\n" : "") + line;
            }
          }
          resetIdleTimer();
        }
        // Server closed the stream gracefully: reconnect unless manually stopped.
        if (!stopped) scheduleReconnect();
      } catch (err) {
        if (stopped) return; // manual teardown: no reconnect, no error toast
        if (err instanceof WatchAuthRequiredError) {
          useLogStore.getState().log("warn", "sse", `explorer/watch requires auth: ${err.message}`);
          waitForAuth();
          return;
        }
        if (idleAborted) {
          // Idle timeout: reconnect silently without surfacing an error.
          idleAborted = false;
          scheduleReconnect();
          return;
        }
        const message = err instanceof Error ? err.message : String(err);
        useLogStore.getState().log("error", "sse", `explorer/watch failed: ${message}`);
        onErrorRef.current?.(new Error(message));
        scheduleReconnect();
      } finally {
        if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
        setStatus(false);
      }
    };

    void connect();

    return () => {
      stopped = true;
      window.removeEventListener(AUTH_TOKEN_CHANGED_EVENT, onAuthTokenChanged);
      stopAuthWait();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (idleTimer) clearTimeout(idleTimer);
      attemptController?.abort();
      setStatus(false);
    };
  }, [rootPath]);
}
