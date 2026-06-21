import { useRef, useCallback, useEffect } from "react";
import { apiWS } from "../api/client";

interface UseWebSocketOptions {
  onMessage?: (data: string) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: Event) => void;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);

  const connect = useCallback(
    (endpoint: string, opts: UseWebSocketOptions) => {
      wsRef.current?.close();
      wsRef.current = apiWS(endpoint, opts);
    },
    []
  );

  const send = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  useEffect(() => {
    return () => disconnect();
  }, [disconnect]);

  return { connect, send, disconnect };
}
