import { useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import type { RerankerState, RerankerStatus } from "../../types/session";

/** Reload is in-process; poll until the backend settles on a terminal state. */
const POLL_INTERVAL_MS = 2000;

const STATE_LABELS: Record<RerankerState, string> = {
  loaded: "重排模型已启用",
  unavailable: "重排模型不可用",
  loading: "加载中…",
  not_loaded: "重排模型未加载",
};

function isRerankerState(value: unknown): value is RerankerState {
  return value === "loaded" || value === "unavailable" || value === "loading" || value === "not_loaded";
}

function isRerankerStatus(value: unknown): value is RerankerStatus {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return isRerankerState((value as Record<string, unknown>).state);
}

function isTerminalState(state: RerankerState): boolean {
  return state === "loaded" || state === "unavailable";
}

function hasMemoryNumbers(status: RerankerStatus): boolean {
  return typeof status.available_mb === "number" && typeof status.required_mb === "number";
}

/** Compact reranker availability row shown under a search_docs tool header.
 *  It only renders backend-reported status; it never re-runs the search. */
export default function RerankerStatusRow({ status: initialStatus }: { status: RerankerStatus }) {
  const [status, setStatus] = useState<RerankerStatus>(initialStatus);
  const [busy, setBusy] = useState(false);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // A replayed/restored tool step replaces the snapshot; follow it.
  useEffect(() => { setStatus(initialStatus); }, [initialStatus]);

  const shouldPoll = status.state === "loading";
  useEffect(() => {
    if (!shouldPoll) return;
    let cancelled = false;
    let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) return;
      inFlight = true;
      apiGet<RerankerStatus>("rag/reranker")
        .then((next) => {
          if (cancelled || !isRerankerStatus(next)) return;
          setStatus(next);
          if (isTerminalState(next.state)) window.clearInterval(timer);
        })
        .catch(() => { /* transient failure: keep polling until the row unmounts */ })
        .finally(() => { inFlight = false; });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [shouldPoll]);

  const retryDisabled = busy || status.state === "loading";
  const handleRetry = () => {
    if (retryDisabled) return;
    setBusy(true);
    setReloadError(null);
    apiPost<{ started: boolean; status: RerankerStatus }>("rag/reranker/reload")
      .then(async (result) => {
        if (!mountedRef.current) return;
        if (isRerankerStatus(result?.status)) {
          setStatus(result.status);
          return;
        }
        const next = await apiGet<RerankerStatus>("rag/reranker");
        if (mountedRef.current && isRerankerStatus(next)) setStatus(next);
      })
      .catch(() => {
        if (mountedRef.current) setReloadError("恢复请求失败，请稍后重试");
      })
      .finally(() => {
        if (mountedRef.current) setBusy(false);
      });
  };

  const showMemory = status.state === "unavailable" && hasMemoryNumbers(status);
  const showReason = (status.state === "unavailable" || status.state === "not_loaded") && status.reason;

  return (
    <div className={`reranker-status-row ${status.state}`} role="status">
      <span className="reranker-status-dot" aria-hidden="true" />
      <span className="reranker-status-label">{STATE_LABELS[status.state]}</span>
      {status.state === "loaded" && status.model ? (
        <span className="reranker-status-meta" title={status.model}>{status.model}</span>
      ) : null}
      {showMemory ? (
        <span className="reranker-status-meta">
          当前 {status.available_mb} MB / 需要 {status.required_mb} MB
        </span>
      ) : null}
      {showReason ? (
        <span className="reranker-status-reason" title={status.reason ?? undefined}>{status.reason}</span>
      ) : null}
      {reloadError ? (
        <span className="reranker-status-reason" title={reloadError}>{reloadError}</span>
      ) : null}
      {status.state !== "loaded" ? (
        <button
          type="button"
          className="reranker-retry-btn"
          disabled={retryDisabled}
          onClick={handleRetry}
        >
          尝试恢复
        </button>
      ) : null}
    </div>
  );
}
