import { useRef, useCallback, useEffect } from "react";
import { apiSSE } from "../api/client";
import type { ChatSSEEvent, BuildSSEEvent } from "../types/api";

type SSEEvent = ChatSSEEvent | BuildSSEEvent;

interface UseSSEOptions {
  onEvent: (evt: SSEEvent) => void;
  onDone?: () => void;
  onError?: (err: Error) => void;
}

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(
    async (path: string, body: unknown, opts: UseSSEOptions) => {
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      await apiSSE(path, body, opts);
    },
    []
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => {
    return () => cancel();
  }, [cancel]);

  return { start, cancel };
}
