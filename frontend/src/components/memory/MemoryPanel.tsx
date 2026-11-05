import { useCallback, useEffect, useRef, useState } from "react";
import { createMemory, deleteMemory, listMemory, updateMemory } from "../../api/memory";
import { useI18n } from "../../i18n";
import { MEMORY_CREATE_TIERS, MEMORY_KINDS, MEMORY_SCOPES, MEMORY_STATUSES, MEMORY_TIERS } from "../../types/memory";
import type {
  MemoryCreateInput,
  MemoryCreateTier,
  MemoryEntry,
  MemoryKind,
  MemoryScope,
  MemoryStatus,
  MemoryTier,
} from "../../types/memory";
import { EmptyState } from "../shared/EmptyState";
import { entryStatus, entryTier, formatEvidence, statusLabelKey, tierLabelKey } from "./memoryFormat";

const DEFAULT_CONFIDENCE = "0.7";

interface MemoryDraft {
  tier: MemoryCreateTier;
  scopeKey: string;
  projectId: string;
  kind: MemoryKind;
  content: string;
  confidence: string;
  expiresAt: string;
}

function scopeForTier(tier: MemoryCreateTier): MemoryScope {
  if (tier === "working") return "session";
  if (tier === "user") return "global";
  return "project";
}

function toCreateTier(tier: MemoryTier): MemoryCreateTier {
  return tier === "working" || tier === "user" ? tier : "project";
}

function defaultDraft(projectId = ""): MemoryDraft {
  return {
    tier: "project",
    scopeKey: "",
    projectId,
    kind: "fact",
    content: "",
    confidence: DEFAULT_CONFIDENCE,
    expiresAt: "",
  };
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function toLocalInput(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function formatDate(value: string | null | undefined, lang: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { hour12: false });
}

function scopeLabelKey(scope: string): string {
  return `memoryScope_${scope}`;
}

function kindLabelKey(kind: string): string {
  return `memoryKind_${kind}`;
}

interface MemoryDraftFieldsProps {
  draft: MemoryDraft;
  onChange: (patch: Partial<MemoryDraft>) => void;
  scopeEditable: boolean;
  idPrefix: string;
  t: (key: string, fallback?: string) => string;
}

function MemoryDraftFields({ draft, onChange, scopeEditable, idPrefix, t }: MemoryDraftFieldsProps) {
  return (
    <>
      {scopeEditable && (
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
          <div style={{ flex: "1 1 140px" }}>
            <label htmlFor={`${idPrefix}-tier`} className="field-label">{t("memoryTier")}</label>
            <select
              id={`${idPrefix}-tier`}
              className="form-select"
              value={draft.tier}
              onChange={(e) => onChange({ tier: e.target.value as MemoryCreateTier })}
            >
              {MEMORY_CREATE_TIERS.map((tier) => (
                <option key={tier} value={tier}>{t(tierLabelKey(tier))}</option>
              ))}
            </select>
          </div>
          <div style={{ flex: "2 1 200px" }}>
            <label htmlFor={`${idPrefix}-scope-key`} className="field-label">
              {t("memoryScopeKey")}{draft.tier === "user" ? ` (${t("memoryOptional")})` : ""}
            </label>
            <input
              id={`${idPrefix}-scope-key`}
              className="form-input"
              value={draft.scopeKey}
              disabled={draft.tier === "user"}
              placeholder={
                draft.tier === "working"
                  ? t("memorySessionKeyPlaceholder")
                  : draft.tier === "project"
                    ? t("memoryProjectKeyPlaceholder")
                    : ""
              }
              onChange={(e) => onChange({ scopeKey: e.target.value })}
            />
          </div>
          <div style={{ flex: "1 1 180px" }}>
            <label htmlFor={`${idPrefix}-project-id`} className="field-label">
              {t("memoryProjectId")} ({t("memoryOptional")})
            </label>
            <input
              id={`${idPrefix}-project-id`}
              className="form-input"
              value={draft.projectId}
              placeholder={t("memoryProjectIdPlaceholder")}
              onChange={(e) => onChange({ projectId: e.target.value })}
            />
          </div>
          <div style={{ flex: "1 1 140px" }}>
            <label htmlFor={`${idPrefix}-kind`} className="field-label">{t("memoryKind")}</label>
            <select
              id={`${idPrefix}-kind`}
              className="form-select"
              value={draft.kind}
              onChange={(e) => onChange({ kind: e.target.value as MemoryKind })}
            >
              {MEMORY_KINDS.map((kind) => (
                <option key={kind} value={kind}>{t(kindLabelKey(kind))}</option>
              ))}
            </select>
          </div>
        </div>
      )}
      <label htmlFor={`${idPrefix}-content`} className="field-label">{t("memoryContent")}</label>
      <textarea
        id={`${idPrefix}-content`}
        className="settings-textarea"
        rows={3}
        value={draft.content}
        onChange={(e) => onChange({ content: e.target.value })}
      />
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
        <div style={{ flex: "0 0 140px" }}>
          <label htmlFor={`${idPrefix}-confidence`} className="field-label">{t("memoryConfidence")}</label>
          <input
            id={`${idPrefix}-confidence`}
            className="form-input"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={draft.confidence}
            onChange={(e) => onChange({ confidence: e.target.value })}
          />
        </div>
        <div style={{ flex: "1 1 220px" }}>
          <label htmlFor={`${idPrefix}-expires`} className="field-label">{t("memoryExpires")}</label>
          <input
            id={`${idPrefix}-expires`}
            className="form-input"
            type="datetime-local"
            value={draft.expiresAt}
            onChange={(e) => onChange({ expiresAt: e.target.value })}
          />
        </div>
      </div>
    </>
  );
}

interface MemoryPanelProps {
  /** Project id seeded from the hub's project context; users can override it. */
  projectId?: string;
}

export function MemoryPanel({ projectId = "" }: MemoryPanelProps) {
  const { t, lang } = useI18n();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [scopeFilter, setScopeFilter] = useState<MemoryScope | "">("");
  const [tierFilter, setTierFilter] = useState<MemoryTier | "">("");
  const [statusFilter, setStatusFilter] = useState<MemoryStatus | "">("active");
  const [kindFilter, setKindFilter] = useState<MemoryKind | "">("");
  const [projectInput, setProjectInput] = useState(projectId);
  const [debouncedProject, setDebouncedProject] = useState(projectId.trim());
  const [showCreate, setShowCreate] = useState(false);
  const [draft, setDraft] = useState<MemoryDraft>(() => defaultDraft());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<MemoryDraft | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestRef = useRef(0);

  useEffect(() => {
    setProjectInput(projectId);
    setDebouncedProject(projectId.trim());
  }, [projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const target = projectInput.trim();
    if (target === debouncedProject) return;
    const timer = window.setTimeout(() => setDebouncedProject(target), 300);
    return () => window.clearTimeout(timer);
  }, [projectInput, debouncedProject]);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const items = await listMemory({
        scope: scopeFilter,
        tier: tierFilter,
        status: statusFilter,
        project_id: debouncedProject,
        kind: kindFilter,
        q: debouncedSearch,
        limit: 200,
      });
      if (requestId === requestRef.current) setEntries(items);
    } catch (err) {
      if (requestId === requestRef.current) setError(errorMessage(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [scopeFilter, tierFilter, statusFilter, debouncedProject, kindFilter, debouncedSearch]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    const content = draft.content.trim();
    if (!content) {
      setFormError(t("memoryContentRequired"));
      return;
    }
    const tier = draft.tier;
    const projectIdValue = draft.projectId.trim();
    const scopeKey = tier === "user"
      ? ""
      : draft.scopeKey.trim() || (tier === "project" ? projectIdValue : "");
    if (tier !== "user" && !scopeKey) {
      setFormError(t("memoryScopeKeyRequired"));
      return;
    }
    const confidence = Number(draft.confidence);
    if (Number.isNaN(confidence) || confidence < 0 || confidence > 1) {
      setFormError(t("memoryConfidenceInvalid"));
      return;
    }
    const payload: MemoryCreateInput = {
      scope: scopeForTier(tier),
      scope_key: scopeKey,
      kind: draft.kind,
      content,
      source: "user",
      confidence,
      expires_at: fromLocalInput(draft.expiresAt),
    };
    // Project is the legacy default; only send tier when it changes the mapping.
    if (tier !== "project") payload.tier = tier;
    if (tier !== "user" && projectIdValue) payload.project_id = projectIdValue;
    setBusy(true);
    setFormError(null);
    try {
      const created = await createMemory(payload);
      setEntries((prev) => [created, ...prev]);
      setDraft(defaultDraft(debouncedProject));
      setShowCreate(false);
      setNotice(t("memoryCreated"));
    } catch (err) {
      setFormError(`${t("createFailed")}: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (entry: MemoryEntry) => {
    setEditingId(entry.id);
    setShowCreate(false);
    setEditDraft({
      tier: toCreateTier(entryTier(entry)),
      scopeKey: entry.scope_key,
      projectId: entry.project_id ?? "",
      kind: entry.kind,
      content: entry.content,
      confidence: String(entry.confidence),
      expiresAt: toLocalInput(entry.expires_at),
    });
    setPendingDeleteId(null);
    setFormError(null);
    setNotice(null);
  };

  const handleUpdate = async () => {
    if (!editingId || !editDraft) return;
    const content = editDraft.content.trim();
    if (!content) {
      setFormError(t("memoryContentRequired"));
      return;
    }
    const confidence = Number(editDraft.confidence);
    if (Number.isNaN(confidence) || confidence < 0 || confidence > 1) {
      setFormError(t("memoryConfidenceInvalid"));
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const updated = await updateMemory(editingId, {
        content,
        confidence,
        expires_at: fromLocalInput(editDraft.expiresAt),
      });
      setEntries((prev) => prev.map((entry) => (entry.id === editingId ? updated : entry)));
      setEditingId(null);
      setEditDraft(null);
      setNotice(t("memoryUpdatedNotice"));
    } catch (err) {
      setFormError(`${t("memoryUpdateFailed")}: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    setBusy(true);
    setFormError(null);
    try {
      await deleteMemory(id);
      setEntries((prev) => prev.filter((entry) => entry.id !== id));
      setPendingDeleteId(null);
      setNotice(t("deleteSuccess"));
    } catch (err) {
      setFormError(`${t("deleteFailed")}: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <p style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 12 }}>
        {t("memoryNoSecrets")}
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <input
          className="form-input"
          style={{ flex: "1 1 220px" }}
          aria-label={t("memorySearchPlaceholder")}
          placeholder={t("memorySearchPlaceholder")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="form-select"
          style={{ flex: "0 0 auto" }}
          aria-label={t("memoryScopeFilter")}
          value={scopeFilter}
          onChange={(e) => setScopeFilter(e.target.value as MemoryScope | "")}
        >
          <option value="">{t("memoryAllScopes")}</option>
          {MEMORY_SCOPES.map((scope) => (
            <option key={scope} value={scope}>{t(scopeLabelKey(scope))}</option>
          ))}
        </select>
        <select
          className="form-select"
          style={{ flex: "0 0 auto" }}
          aria-label={t("memoryTierFilter")}
          value={tierFilter}
          onChange={(e) => setTierFilter(e.target.value as MemoryTier | "")}
        >
          <option value="">{t("memoryAllTiers")}</option>
          {MEMORY_TIERS.map((tier) => (
            <option key={tier} value={tier}>{t(tierLabelKey(tier))}</option>
          ))}
        </select>
        <select
          className="form-select"
          style={{ flex: "0 0 auto" }}
          aria-label={t("memoryStatusFilter")}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as MemoryStatus | "")}
        >
          {MEMORY_STATUSES.map((status) => (
            <option key={status} value={status}>{t(statusLabelKey(status))}</option>
          ))}
        </select>
        <input
          className="form-input"
          style={{ flex: "1 1 160px" }}
          aria-label={t("memoryProjectFilter")}
          placeholder={t("memoryProjectIdPlaceholder")}
          value={projectInput}
          onChange={(e) => setProjectInput(e.target.value)}
        />
        <select
          className="form-select"
          style={{ flex: "0 0 auto" }}
          aria-label={t("memoryKindFilter")}
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value as MemoryKind | "")}
        >
          <option value="">{t("memoryAllKinds")}</option>
          {MEMORY_KINDS.map((kind) => (
            <option key={kind} value={kind}>{t(kindLabelKey(kind))}</option>
          ))}
        </select>
        <button
          type="button"
          className="settings-btn primary"
          onClick={() => {
            const nextOpen = !showCreate;
            setShowCreate(nextOpen);
            setDraft(defaultDraft(debouncedProject));
            setEditingId(null);
            setEditDraft(null);
            setFormError(null);
            setNotice(null);
          }}
        >
          {showCreate ? t("cancel") : t("memoryAdd")}
        </button>
      </div>

      {notice && <div role="status" style={{ fontSize: 12, color: "var(--success)", marginBottom: 10 }}>{notice}</div>}

      {showCreate && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "14px 16px", marginBottom: 16 }}>
          <MemoryDraftFields
            draft={draft}
            onChange={(patch) => setDraft((prev) => ({ ...prev, ...patch }))}
            scopeEditable
            idPrefix="memory-create"
            t={t}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="verify-btn primary" disabled={busy} onClick={() => void handleCreate()}>
              {busy ? t("memorySaving") : t("memorySaveNew")}
            </button>
            <button type="button" className="verify-btn" disabled={busy} onClick={() => { setShowCreate(false); setDraft(defaultDraft(debouncedProject)); setFormError(null); }}>
              {t("cancel")}
            </button>
          </div>
        </div>
      )}

      {formError && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", marginBottom: 10 }}>{formError}</div>
      )}

      {loading && <div style={{ fontSize: 12, color: "var(--muted-fg)", padding: "12px 0" }}>{t("memoryLoading")}</div>}

      {!loading && error && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "12px 0" }}>
          {t("memoryLoadFailed")}: {error}
          <button type="button" className="verify-btn" style={{ marginLeft: 8 }} onClick={() => void load()}>
            {t("retry")}
          </button>
        </div>
      )}

      {!loading && !error && entries.length === 0 && (
        <EmptyState
          size="md"
          title={t("memoryEmpty")}
          desc={t("memoryEmptyDesc")}
        />
      )}

      {!loading && !error && entries.map((entry) => (
        <div
          key={entry.id}
          style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "12px 14px", marginBottom: 10 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span className="memory-badge" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 10, border: "1px solid var(--border)", color: "var(--muted-fg)" }}>
              {t(scopeLabelKey(entry.scope))}
            </span>
            <span className="memory-badge" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 10, border: "1px solid var(--border)", color: "var(--muted-fg)" }}>
              {t(tierLabelKey(entryTier(entry)))}
            </span>
            <span className="memory-badge" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 10, border: "1px solid var(--border)", color: "var(--muted-fg)" }}>
              {t(statusLabelKey(entryStatus(entry)))}
            </span>
            <span className="memory-badge" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 10, border: "1px solid var(--border)", color: "var(--muted-fg)" }}>
              {t(kindLabelKey(entry.kind))}
            </span>
            <span className="memory-badge" style={{ fontSize: 11, padding: "1px 7px", borderRadius: 10, border: "1px solid var(--border)", color: "var(--muted-fg)" }}>
              {t(`memorySource_${entry.source}`, String(entry.source))}
            </span>
            <span style={{ fontSize: 11, color: "var(--muted-fg)" }}>
              {t("memoryConfidence")} {Number.isFinite(entry.confidence) ? entry.confidence.toFixed(2) : "—"}
            </span>
            <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              {editingId !== entry.id && (
                <>
                  <button type="button" className="verify-btn" onClick={() => startEdit(entry)}>{t("edit")}</button>
                  {pendingDeleteId === entry.id ? (
                    <>
                      <button type="button" className="verify-btn danger" disabled={busy} onClick={() => void handleDelete(entry.id)}>
                        {t("confirmDelete")}
                      </button>
                      <button type="button" className="verify-btn" disabled={busy} onClick={() => setPendingDeleteId(null)}>
                        {t("cancel")}
                      </button>
                    </>
                  ) : (
                    <button type="button" className="verify-btn danger" onClick={() => { setPendingDeleteId(entry.id); setFormError(null); }}>
                      {t("delete")}
                    </button>
                  )}
                </>
              )}
            </span>
          </div>

          {editingId === entry.id && editDraft ? (
            <div style={{ marginTop: 12 }}>
              <MemoryDraftFields
                draft={editDraft}
                onChange={(patch) => setEditDraft((prev) => (prev ? { ...prev, ...patch } : prev))}
                scopeEditable={false}
                idPrefix={`memory-edit-${entry.id}`}
                t={t}
              />
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button type="button" className="verify-btn primary" disabled={busy} onClick={() => void handleUpdate()}>
                  {busy ? t("memorySaving") : t("save")}
                </button>
                <button type="button" className="verify-btn" disabled={busy} onClick={() => { setEditingId(null); setEditDraft(null); setFormError(null); }}>
                  {t("cancel")}
                </button>
              </div>
            </div>
          ) : (
            <>
              <p style={{ fontSize: 13, margin: "8px 0", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{entry.content}</p>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 11, color: "var(--muted-fg)" }}>
                {entry.scope_key && <span>{t("memoryScopeKey")}: {entry.scope_key}</span>}
                {entry.project_id && <span>{t("memoryProjectId")}: {entry.project_id}</span>}
                {formatEvidence(entry.evidence) && (
                  <span>{t("memoryEvidence")}: {formatEvidence(entry.evidence)}</span>
                )}
                <span>{t("memoryExpires")}: {entry.expires_at ? formatDate(entry.expires_at, lang) : t("memoryNeverExpires")}</span>
                <span>{t("memoryUpdatedAt")}: {formatDate(entry.updated_at, lang)}</span>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
