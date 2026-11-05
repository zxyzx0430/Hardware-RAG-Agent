import { useCallback, useEffect, useRef, useState } from "react";
import { approveProposal, listProposals, rejectProposal } from "../../api/memory";
import { useI18n } from "../../i18n";
import type { MemoryEntry } from "../../types/memory";
import { EmptyState } from "../shared/EmptyState";
import {
  entryStatus,
  entryTier,
  errorMessage,
  formatDate,
  formatEvidence,
  statusLabelKey,
  tierLabelKey,
} from "./memoryFormat";

type ReviewAction = "approve" | "reject";

const badgeStyle = {
  fontSize: 11,
  padding: "1px 7px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  color: "var(--muted-fg)",
} as const;

export function MemoryProposalsPanel() {
  const { t, lang } = useI18n();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const items = await listProposals();
      if (requestId === requestRef.current) setEntries(items);
    } catch (err) {
      if (requestId === requestRef.current) setError(errorMessage(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const review = async (entry: MemoryEntry, action: ReviewAction) => {
    const key = `${action}:${entry.id}`;
    setBusyKey(key);
    setActionError(null);
    setNotice(null);
    try {
      if (action === "approve") await approveProposal(entry.id);
      else await rejectProposal(entry.id);
      setEntries((prev) => prev.filter((item) => item.id !== entry.id));
      setNotice(action === "approve" ? t("proposalApproved") : t("proposalRejected"));
    } catch (err) {
      setActionError(
        `${action === "approve" ? t("proposalApproveFailed") : t("proposalRejectFailed")}: ${errorMessage(err)}`,
      );
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <p style={{ fontSize: 12, color: "var(--muted-fg)", margin: 0 }}>
          {t("proposalsHint")}
        </p>
        <button
          type="button"
          className="verify-btn"
          style={{ marginLeft: "auto" }}
          disabled={loading}
          onClick={() => void load()}
        >
          {loading ? t("memoryLoading") : t("workingMemoryRefresh")}
        </button>
      </div>

      {notice && (
        <div role="status" style={{ fontSize: 12, color: "var(--success)", marginBottom: 10 }}>
          {notice}
        </div>
      )}
      {actionError && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", marginBottom: 10 }}>
          {actionError}
        </div>
      )}

      {loading && (
        <div style={{ fontSize: 12, color: "var(--muted-fg)", padding: "12px 0" }}>{t("memoryLoading")}</div>
      )}

      {!loading && error && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "12px 0" }}>
          {t("proposalsLoadFailed")}: {error}
          <button type="button" className="verify-btn" style={{ marginLeft: 8 }} onClick={() => void load()}>
            {t("retry")}
          </button>
        </div>
      )}

      {!loading && !error && entries.length === 0 && (
        <EmptyState
          size="md"
          title={t("proposalsEmpty")}
          desc={t("proposalsEmptyDesc")}
        />
      )}

      {!loading && !error && entries.map((entry) => {
        const evidence = formatEvidence(entry.evidence);
        const approving = busyKey === `approve:${entry.id}`;
        const rejecting = busyKey === `reject:${entry.id}`;
        const busy = approving || rejecting;
        return (
          <div
            key={entry.id}
            style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "12px 14px", marginBottom: 10 }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
              <span className="memory-badge" style={badgeStyle}>{t(tierLabelKey(entryTier(entry)))}</span>
              <span className="memory-badge" style={badgeStyle}>{t(`memoryKind_${entry.kind}`, String(entry.kind))}</span>
              <span className="memory-badge" style={badgeStyle}>{t(statusLabelKey(entryStatus(entry)))}</span>
              <span style={badgeStyle}>{t(`memorySource_${entry.source}`, String(entry.source))}</span>
              <span style={{ fontSize: 11, color: "var(--muted-fg)" }}>
                {t("memoryConfidence")} {Number.isFinite(entry.confidence) ? entry.confidence.toFixed(2) : "—"}
              </span>
            </div>

            <p style={{ fontSize: 13, margin: "6px 0", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {entry.content}
            </p>

            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 11, color: "var(--muted-fg)" }}>
              {entry.project_id && <span>{t("memoryProjectId")}: {entry.project_id}</span>}
              {evidence && <span>{t("memoryEvidence")}: {evidence}</span>}
              <span>{t("proposalCreatedAt")}: {formatDate(entry.created_at, lang)}</span>
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button
                type="button"
                className="verify-btn primary"
                disabled={busy}
                onClick={() => void review(entry, "approve")}
              >
                {approving ? t("memorySaving") : t("proposalApprove")}
              </button>
              <button
                type="button"
                className="verify-btn danger"
                disabled={busy}
                onClick={() => void review(entry, "reject")}
              >
                {rejecting ? t("memorySaving") : t("proposalReject")}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
