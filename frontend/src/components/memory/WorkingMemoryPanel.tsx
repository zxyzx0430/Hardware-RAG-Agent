import { useCallback, useEffect, useRef, useState } from "react";
import { getWorkingMemory } from "../../api/memory";
import { useI18n } from "../../i18n";
import { useChatStore } from "../../stores/useChatStore";
import { WORKING_MEMORY_FIELDS } from "../../types/memory";
import type { WorkingMemorySnapshot } from "../../types/memory";
import { EmptyState } from "../shared/EmptyState";
import { claimTexts, errorMessage, fieldLabelKey, formatDate } from "./memoryFormat";

export function WorkingMemoryPanel() {
  const { t, lang } = useI18n();
  const sessionId = useChatStore((state) => state.activeSessionId);
  const [snapshot, setSnapshot] = useState<WorkingMemorySnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    if (!sessionId) {
      setSnapshot(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await getWorkingMemory(sessionId);
      if (requestId === requestRef.current) setSnapshot(next);
    } catch (err) {
      if (requestId === requestRef.current) setError(errorMessage(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!sessionId) {
    return (
      <EmptyState
        size="md"
        title={t("workingMemoryNoSession")}
        desc={t("workingMemoryNoSessionDesc")}
      />
    );
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>
          {t("workingMemorySession")}: <span style={{ fontFamily: "var(--font-mono)" }}>{sessionId}</span>
        </span>
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

      {error && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "12px 0" }}>
          {t("workingMemoryLoadFailed")}: {error}
          <button type="button" className="verify-btn" style={{ marginLeft: 8 }} onClick={() => void load()}>
            {t("retry")}
          </button>
        </div>
      )}

      {!error && loading && (
        <div style={{ fontSize: 12, color: "var(--muted-fg)", padding: "12px 0" }}>
          {t("workingMemoryLoading")}
        </div>
      )}

      {!error && !loading && !snapshot && (
        <EmptyState
          size="md"
          title={t("workingMemoryEmpty")}
          desc={t("workingMemoryEmptyDesc")}
        />
      )}

      {!error && !loading && snapshot && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "14px 16px" }}>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 11, color: "var(--muted-fg)", marginBottom: 12 }}>
            <span>
              {t("workingMemoryUpdatedAt")}: {formatDate(snapshot.updated_at || snapshot.created_at, lang)}
            </span>
            <span>{t("workingMemorySeal")}: {snapshot.seal_id || "—"}</span>
            <span>{t("workingMemoryGeneration")}: {snapshot.generation}</span>
          </div>

          <div style={{ display: "grid", gap: 12 }}>
            {WORKING_MEMORY_FIELDS.map((field) => {
              const texts = claimTexts(snapshot.body[field]);
              const emptyText = field === "completed" ? t("workingMemoryNoCompleted") : "—";
              return (
                <div key={field}>
                  <div className="field-label">{t(fieldLabelKey(field))}</div>
                  {texts.length === 0 ? (
                    <p style={{ fontSize: 13, margin: 0, color: "var(--muted-fg)" }}>{emptyText}</p>
                  ) : texts.length === 1 ? (
                    <p style={{ fontSize: 13, margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {texts[0]}
                    </p>
                  ) : (
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                      {texts.map((text, index) => (
                        <li key={`${field}-${index}`} style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                          {text}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
