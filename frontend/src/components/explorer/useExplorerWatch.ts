import { useEffect, useRef } from "react";
import { useLogStore } from "../../stores/useLogStore";
import { getAuthHeaders } from "../../api/client";

export interface WatchEvent {
  type: "change" | "create" | "delete" | "rename" | "heartbeat";
  path?: string;
  src_path?: string;
  dest_path?: string;
  is_directory?: boolean;
}

const IDLE_TIMEOUT_MS = 5 * 60 * 1000;
// Exponential backoff reconnect: 1s, 2s, 4s, 8s, 16s, 30s (capped), max 10 attempts.
const MAX_RETRIES = 10;
const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1000;

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
) {
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const onStatusRef = useRef(onStatusChange);

  useEffect(() => {
    onEventRef.current = onEvent;
    onErrorRef.current = onError;
    onStatusRef.current = onStatusChange;
  }, [onEvent, onError, onStatusChange]);

  useEffect(() => {
    if (!rootPath) return;
    // stopped=true means manual teardown (unmount / rootPath change): do NOT reconnect.
    let stopped = false;
    let retryCount = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    let attemptController: AbortController | null = null;
    let idleAborted = false;

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

    const connect = async () => {
      idleAborted = false;
      attemptController = new AbortController();
      const controller = attemptController;
      try {
        const url = `/api/explorer/watch?path=${encodeURIComponent(rootPath)}`;
        const res = await fetch(url, { headers: getAuthHeaders(), signal: controller.signal });
        if (!res.ok) throw new Error(`SSE ${res.status}: ${res.statusText}`);
        if (!res.body) throw new Error("No response body");
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        resetIdleTimer();
        // Connection established: reset retry counter and notify connected.
        retryCount = 0;
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
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (idleTimer) clearTimeout(idleTimer);
      attemptController?.abort();
      setStatus(false);
    };
  }, [rootPath]);
}
