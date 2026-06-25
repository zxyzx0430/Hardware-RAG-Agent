/**
 * KB Collection Manager — modal for managing multiple knowledge bases.
 */
import { useState, useEffect, useRef } from "react";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useI18n } from "../../i18n";
import type { CreateKBRequest, KBCollectionDetail } from "../../types/kb";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function KbCollectionManager({ open, onClose }: Props) {
  const { t } = useI18n();
  const {
    collections, isLoadingCollections, activeKbId,
    fetchCollections, setActiveKb, createCollection, deleteCollection,
    toggleCollection, getCollectionDetail, exportCollection, importCollection,
    renameCollection, updateKbConfig, fetchEmbeddingModels,
  } = useKnowledgeStore();

  const {
    embeddingDefaultModel, embeddingDefaultBaseUrl, embeddingDefaultApiKey,
    agentChunkerDefaultModel, agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey,
    defaultContextWindow,
  } = useSettingsStore();

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [expandedKbId, setExpandedKbId] = useState<string | null>(null);
  // Ref mirror of expandedKbId to guard against async race conditions in handleExpand
  const expandedKbIdRef = useRef<string | null>(null);
  const [kbDetail, setKbDetail] = useState<KBCollectionDetail | null>(null);
  const [detailError, setDetailError] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [editingKbId, setEditingKbId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [importingKbId, setImportingKbId] = useState<string | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);

  // Edit KB config state
  const [editingConfigKbId, setEditingConfigKbId] = useState<string | null>(null);
  const [configForm, setConfigForm] = useState<Partial<CreateKBRequest>>({
    embedding_model: "",
    embedding_base_url: "",
    embedding_api_key: "",
    chunk_method: "hybrid",
    agent_chunker_model: "",
    agent_chunker_base_url: "",
    agent_chunker_api_key: "",
    context_window: 0,
  });
  const [configSaving, setConfigSaving] = useState(false);
  const [configMsg, setConfigMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [testingEmbedding, setTestingEmbedding] = useState(false);

  // Create form state — starts empty, pre-fills from settings when opened
  const [form, setForm] = useState<CreateKBRequest>({
    name: "",
    description: "",
    chunk_method: "hybrid",
    embedding_model: "",
    embedding_base_url: "",
    embedding_api_key: "",
    agent_chunker_model: "",
    agent_chunker_base_url: "",
    agent_chunker_api_key: "",
    context_window: 0,
  });

  useEffect(() => {
    if (open) {
      fetchCollections();
    }
  }, [open, fetchCollections]);

  // Sync form defaults from settings when form is opened
  useEffect(() => {
    if (showCreateForm) {
      setForm((f) => ({
        ...f,
        name: "",
        description: "",
        embedding_model: embeddingDefaultModel,
        embedding_base_url: embeddingDefaultBaseUrl,
        embedding_api_key: embeddingDefaultApiKey,
        agent_chunker_model: agentChunkerDefaultModel,
        agent_chunker_base_url: agentChunkerDefaultBaseUrl,
        agent_chunker_api_key: agentChunkerDefaultApiKey,
        context_window: defaultContextWindow,
      }));
      setErrorMsg("");
    }
  }, [showCreateForm, embeddingDefaultModel, embeddingDefaultBaseUrl, embeddingDefaultApiKey,
      agentChunkerDefaultModel, agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey, defaultContextWindow]);

  if (!open) return null;

  const handleCreate = async () => {
    if (!form.name.trim()) return;
    if (!form.embedding_model.trim()) {
      setErrorMsg(t('enterEmbeddingModel'));
      return;
    }
    setErrorMsg("");
    const result = await createCollection(form);
    if (result) {
      setShowCreateForm(false);
    } else {
      setErrorMsg(t('createKbFailed'));
    }
  };

  const handleDelete = async (kbId: string) => {
    const ok = await deleteCollection(kbId);
    if (!ok) {
      setErrorMsg(t('deleteKbFailed'));
    }
    setDeleteConfirmId(null);
    if (expandedKbId === kbId) {
      expandedKbIdRef.current = null;
      setExpandedKbId(null);
      setKbDetail(null);
    }
  };

  const handleExpand = async (kbId: string) => {
    // Close config editor if open (mutual exclusion)
    setEditingConfigKbId(null);
    setConfigMsg(null);
    if (expandedKbIdRef.current === kbId) {
      expandedKbIdRef.current = null;
      setExpandedKbId(null);
      setKbDetail(null);
      setDetailError(false);
      return;
    }
    expandedKbIdRef.current = kbId;
    setExpandedKbId(kbId);
    setKbDetail(null); // Clear immediately to avoid showing stale data
    setDetailError(false);
    const detail = await getCollectionDetail(kbId);
    // Race condition guard: only apply detail if still expanded on this kbId
    if (expandedKbIdRef.current !== kbId) return;
    if (detail) {
      setKbDetail(detail);
    } else {
      setDetailError(true);
    }
  };

  const handleExport = async (kbId: string) => {
    setErrorMsg("");
    try {
      await exportCollection(kbId);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : t('fetchFailed'));
    }
  };

  const handleImportClick = (kbId: string) => {
    setImportingKbId(kbId);
    importFileRef.current?.click();
  };

  const handleImportFile = async (files: FileList | null) => {
    if (!files?.length || !importingKbId) return;
    const file = files[0];
    setErrorMsg("");
    try {
      const imported = await importCollection(importingKbId, file);
      if (imported === 0) {
        setErrorMsg(t('noChunksImported'));
      }
      // Refresh expanded detail if open
      if (expandedKbId === importingKbId) {
        setKbDetail(null);
        const detail = await getCollectionDetail(importingKbId);
        if (detail) setKbDetail(detail);
      }
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : t('fetchFailed'));
    }
    setImportingKbId(null);
    // Reset input so same file can be selected again
    if (importFileRef.current) importFileRef.current.value = "";
  };

  const startRename = (kbId: string, currentName: string) => {
    setEditingKbId(kbId);
    setEditingName(currentName);
    setErrorMsg("");
  };

  const commitRename = async (kbId: string) => {
    const newName = editingName.trim();
    if (!newName) {
      // Show error and keep the editor open so the user can type a valid name
      setErrorMsg(t('enterNewName'));
      return;
    }
    setErrorMsg("");
    setEditingKbId(null);
    setEditingName("");
    try {
      await renameCollection(kbId, newName);
    } catch {
      // error already logged in store
    }
  };

  const cancelRename = () => {
    setEditingKbId(null);
    setEditingName("");
    setErrorMsg("");
  };

  // ─── Edit KB config ───
  const startEditConfig = (kb: typeof collections[number]) => {
    // Close detail panel if open, switch to config mode
    expandedKbIdRef.current = null;
    setExpandedKbId(null);
    setKbDetail(null);
    setDetailError(false);
    setEditingConfigKbId(kb.id);
    setConfigMsg(null);
    // Prefill: use KB's own config first, then fall back to global defaults
    // so user only needs to click Save to sync global config into the KB.
    const kbUnconfigured = !kb.embedding_base_url;
    setConfigForm({
      embedding_model: kb.embedding_model || embeddingDefaultModel || "",
      embedding_base_url: kb.embedding_base_url || embeddingDefaultBaseUrl || "",
      // Prefill API key from global defaults only if KB appears unconfigured
      embedding_api_key: kbUnconfigured ? (embeddingDefaultApiKey || "") : "",
      chunk_method: kb.chunk_method || "hybrid",
      agent_chunker_model: kb.agent_chunker_model || agentChunkerDefaultModel || "",
      agent_chunker_base_url: kb.agent_chunker_base_url || agentChunkerDefaultBaseUrl || "",
      agent_chunker_api_key: kbUnconfigured ? (agentChunkerDefaultApiKey || "") : "",
      context_window: kb.context_window || defaultContextWindow || 0,
    });
    // Show hint if KB has no embedding base_url (likely unconfigured)
    if (kbUnconfigured) {
      setConfigMsg({ kind: "ok", text: t('configPrefillHint') });
    }
  };

  const cancelEditConfig = () => {
    setEditingConfigKbId(null);
    setConfigMsg(null);
  };

  const handleSaveConfig = async (kbId: string) => {
    setConfigSaving(true);
    setConfigMsg(null);
    try {
      // For api_key fields: omit (undefined) means "keep existing" on backend.
      const payload: Partial<CreateKBRequest> = {
        embedding_model: configForm.embedding_model,
        embedding_base_url: configForm.embedding_base_url,
        chunk_method: configForm.chunk_method as "hybrid" | "agent",
      };
      if (configForm.embedding_api_key) {
        payload.embedding_api_key = configForm.embedding_api_key;
      }
      if (configForm.chunk_method === "agent") {
        payload.agent_chunker_model = configForm.agent_chunker_model;
        payload.agent_chunker_base_url = configForm.agent_chunker_base_url;
        if (configForm.agent_chunker_api_key) {
          payload.agent_chunker_api_key = configForm.agent_chunker_api_key;
        }
        if (configForm.context_window) {
          payload.context_window = configForm.context_window;
        }
      }
      await updateKbConfig(kbId, payload);
      setConfigMsg({ kind: "ok", text: t('configUpdated') });
      // Auto-close after short delay so user sees the success message
      setTimeout(() => {
        setEditingConfigKbId(null);
        setConfigMsg(null);
      }, 1200);
    } catch (e) {
      setConfigMsg({ kind: "err", text: e instanceof Error ? e.message : t('fetchFailed') });
    } finally {
      setConfigSaving(false);
    }
  };

  const handleTestEmbedding = async () => {
    if (!configForm.embedding_base_url || !configForm.embedding_api_key) {
      setConfigMsg({ kind: "err", text: t('enterBaseUrlAndKey') });
      return;
    }
    setTestingEmbedding(true);
    setConfigMsg(null);
    try {
      const models = await fetchEmbeddingModels(
        configForm.embedding_base_url,
        configForm.embedding_api_key,
      );
      if (models.length > 0) {
        setConfigMsg({ kind: "ok", text: t('connectionOk').replace('{n}', String(models.length)) });
        // Auto-fill model name if empty
        if (!configForm.embedding_model && models.includes("text-embedding-3-small")) {
          setConfigForm((f) => ({ ...f, embedding_model: "text-embedding-3-small" }));
        }
      } else {
        setConfigMsg({ kind: "err", text: t('noModelsFound') });
      }
    } catch (e) {
      setConfigMsg({ kind: "err", text: e instanceof Error ? e.message : t('fetchFailed') });
    } finally {
      setTestingEmbedding(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose} style={{
      position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
      background: "rgba(0,0,0,0.5)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <input
        ref={importFileRef}
        type="file"
        accept=".json"
        style={{ display: "none" }}
        onChange={(e) => handleImportFile(e.target.files)}
      />
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{
        background: "var(--bg)", borderRadius: 8, padding: 0,
        width: "90%", maxWidth: 680, maxHeight: "85vh", overflow: "auto",
        border: "1px solid var(--border)",
        boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
      }}>
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "16px 20px", borderBottom: "1px solid var(--border)",
          position: "sticky", top: 0, background: "var(--bg)", zIndex: 1,
        }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{t('kbCollections')}</h2>
          <button className="kb-item-icon-btn" onClick={onClose} style={{ fontSize: 16 }}>✕</button>
        </div>

        <div style={{ padding: "16px 20px" }}>
          {/* Action bar */}
          <div style={{ marginBottom: 16 }}>
            <button className="btn-new" onClick={() => setShowCreateForm(!showCreateForm)}>
              {showCreateForm ? t('escClose') : `+ ${t('createKb')}`}
            </button>
          </div>

          {/* Error message */}
          {errorMsg && (
            <div style={{
              marginBottom: 12, padding: "8px 12px", borderRadius: 4,
              background: "rgba(231,76,60,0.1)", border: "1px solid rgba(231,76,60,0.3)",
              fontSize: 12, color: "#e74c3c",
            }}>
              {errorMsg}
            </div>
          )}

          {/* Create form */}
          {showCreateForm && (
            <div style={{
              marginBottom: 16, padding: 16, borderRadius: 6,
              border: "1px solid var(--border)", background: "var(--thinking-bg)",
            }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12 }}>{t('createKb')}</div>

              <div className="field-label" style={{ marginBottom: 4 }}>{t('kbName')} *</div>
              <input
                className="form-input"
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                style={{ width: "100%", marginBottom: 10 }}
                placeholder={t('kbNamePlaceholder')}
              />

              <div className="field-label" style={{ marginBottom: 4 }}>{t('kbDescription')}</div>
              <input
                className="form-input"
                type="text"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                style={{ width: "100%", marginBottom: 10 }}
                placeholder={t('kbDescriptionPlaceholder')}
              />

              <div className="field-label" style={{ marginBottom: 4 }}>{t('chunkMethod')}</div>
              <select
                className="form-input"
                value={form.chunk_method}
                onChange={(e) => setForm({ ...form, chunk_method: e.target.value as "hybrid" | "agent" })}
                style={{ width: "100%", marginBottom: 4 }}
              >
                <option value="hybrid">{t('hybrid')}</option>
                <option value="agent">{t('agent')}</option>
              </select>
              {form.chunk_method === "agent" && (
                <p style={{ fontSize: 11, color: "var(--muted-fg)", margin: "4px 0 10px" }}>
                  {t('agentChunkHint')}
                </p>
              )}

              {/* Embedding config */}
              <div style={{ fontWeight: 600, fontSize: 12, color: "var(--muted-fg)", margin: "12px 0 6px", textTransform: "uppercase", letterSpacing: 0.5 }}>
                {t('embeddingConfig')}
              </div>
              <div className="field-label" style={{ marginBottom: 4 }}>{t('embeddingModel')} *</div>
              <input
                className="form-input"
                type="text"
                value={form.embedding_model}
                onChange={(e) => setForm({ ...form, embedding_model: e.target.value })}
                style={{ width: "100%", marginBottom: 10 }}
                placeholder="text-embedding-3-small"
              />
              <div className="field-label" style={{ marginBottom: 4 }}>{t('embeddingBaseUrl')}</div>
              <input
                className="form-input"
                type="text"
                value={form.embedding_base_url}
                onChange={(e) => setForm({ ...form, embedding_base_url: e.target.value })}
                style={{ width: "100%", marginBottom: 10 }}
                placeholder="https://api.openai.com/v1"
              />
              <div className="field-label" style={{ marginBottom: 4 }}>{t('embeddingApiKey')}</div>
              <input
                className="form-input"
                type="password"
                value={form.embedding_api_key}
                onChange={(e) => setForm({ ...form, embedding_api_key: e.target.value })}
                style={{ width: "100%", marginBottom: 10 }}
                placeholder={t('useGlobalDefaultIfEmpty')}
              />

              {/* Agent chunker config */}
              {form.chunk_method === "agent" && (
                <>
                  <div style={{ fontWeight: 600, fontSize: 12, color: "var(--muted-fg)", margin: "12px 0 6px", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    {t('agentChunkerConfig')}
                  </div>
                  <div className="field-label" style={{ marginBottom: 4 }}>{t('agentChunkerModel')}</div>
                  <input
                    className="form-input"
                    type="text"
                    value={form.agent_chunker_model}
                    onChange={(e) => setForm({ ...form, agent_chunker_model: e.target.value })}
                    style={{ width: "100%", marginBottom: 10 }}
                    placeholder="gpt-4o-mini"
                  />
                  <div className="field-label" style={{ marginBottom: 4 }}>{t('agentChunkerBaseUrl')}</div>
                  <input
                    className="form-input"
                    type="text"
                    value={form.agent_chunker_base_url}
                    onChange={(e) => setForm({ ...form, agent_chunker_base_url: e.target.value })}
                    style={{ width: "100%", marginBottom: 10 }}
                    placeholder="https://api.openai.com/v1"
                  />
                  <div className="field-label" style={{ marginBottom: 4 }}>{t('agentChunkerApiKey')}</div>
                  <input
                    className="form-input"
                    type="password"
                    value={form.agent_chunker_api_key}
                    onChange={(e) => setForm({ ...form, agent_chunker_api_key: e.target.value })}
                    style={{ width: "100%", marginBottom: 10 }}
                    placeholder={t('useGlobalDefaultIfEmpty')}
                  />
                  <div className="field-label" style={{ marginBottom: 4 }}>{t('contextWindow')}</div>
                  <input
                    className="form-input"
                    type="number"
                    value={form.context_window || ""}
                    min={4096}
                    max={1000000}
                    step={4096}
                    onChange={(e) => setForm({ ...form, context_window: parseInt(e.target.value) || 0 })}
                    style={{ width: "100%", marginBottom: 10 }}
                    placeholder="256000"
                  />
                </>
              )}

              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button className="btn-new" onClick={handleCreate} disabled={!form.name.trim() || !form.embedding_model.trim()}>
                  {t('createKb')}
                </button>
                <button className="kb-item-icon-btn" onClick={() => setShowCreateForm(false)}>
                  {t('escClose')}
                </button>
              </div>
            </div>
          )}

          {/* KB list */}
          {isLoadingCollections ? (
            <div style={{ textAlign: "center", padding: 32, color: "var(--muted-fg)", fontSize: 13 }}>...</div>
          ) : collections.length === 0 ? (
            <div style={{ textAlign: "center", padding: 32, color: "var(--muted-fg)", fontSize: 13 }}>
              {t('noCollections')}
            </div>
          ) : (
            <div>
              {collections.map((kb) => (
                <div key={kb.id} style={{
                  marginBottom: 6, borderRadius: 6,
                  border: `1px solid ${expandedKbId === kb.id ? "var(--primary)" : "var(--border)"}`,
                  background: "var(--bg)", overflow: "hidden",
                  transition: "border-color 0.15s",
                }}>
                  {/* KB row */}
                  <div
                    style={{
                      display: "flex", alignItems: "center", padding: "10px 12px",
                      cursor: "pointer", gap: 10,
                    }}
                    onClick={() => handleExpand(kb.id)}
                  >
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        {editingKbId === kb.id ? (
                          <input
                            className="form-input"
                            type="text"
                            value={editingName}
                            autoFocus
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              setEditingName(e.target.value);
                              if (errorMsg) setErrorMsg("");
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") { e.preventDefault(); commitRename(kb.id); }
                              else if (e.key === "Escape") { e.preventDefault(); cancelRename(); }
                            }}
                            onBlur={() => commitRename(kb.id)}
                            style={{ flex: 1, fontSize: 13, padding: "2px 6px" }}
                          />
                        ) : (
                          <span style={{ fontWeight: 600, fontSize: 13 }}>{kb.name}</span>
                        )}
                        {kb.is_builtin && (
                          <span style={{
                            fontSize: 10, padding: "1px 6px", borderRadius: 3,
                            background: "var(--primary)", color: "white", fontWeight: 500,
                          }}>{t('builtinKb')}</span>
                        )}
                        {activeKbId === kb.id && (
                          <span style={{
                            fontSize: 10, padding: "1px 6px", borderRadius: 3,
                            background: "var(--accent)", color: "var(--bg)",
                          }}>{t('active')}</span>
                        )}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 2 }}>
                        {kb.chunk_method === "agent" ? t('agent') : t('hybrid')}
                        {kb.embedding_model ? ` · ${kb.embedding_model}` : ""}
                        {` · ${t('docCount')}: ${kb.doc_count}`}
                        {` · ${t('chunkCount')}: ${kb.chunk_count}`}
                      </div>
                    </div>

                    {/* Enabled toggle */}
                    <div
                      className={`toggle-switch ${kb.enabled ? "on" : "off"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleCollection(kb.id, !kb.enabled);
                      }}
                    >
                      <span className="toggle-knob"></span>
                    </div>

                    {/* Set active */}
                    {activeKbId !== kb.id && (
                      <button
                        className="kb-item-icon-btn"
                        title={t('setAsTarget')}
                        onClick={(e) => {
                          e.stopPropagation();
                          setActiveKb(kb.id);
                        }}
                      >
                        →
                      </button>
                    )}

                    {/* Export */}
                    <button
                      className="kb-item-icon-btn"
                      title={t('exportKb')}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleExport(kb.id);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    </button>

                    {/* Import */}
                    <button
                      className="kb-item-icon-btn"
                      title={t('importKb')}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleImportClick(kb.id);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    </button>

                    {/* Edit config (all KBs — including builtin, since builtin also needs embedding config) */}
                    <button
                      className="kb-item-icon-btn"
                      title={t('editKbConfig')}
                      onClick={(e) => {
                        e.stopPropagation();
                        startEditConfig(kb);
                      }}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                    </button>

                    {/* Rename (non-builtin only) */}
                    {!kb.is_builtin && (
                      <button
                        className="kb-item-icon-btn"
                        title={t('renameKb')}
                        onClick={(e) => {
                          e.stopPropagation();
                          startRename(kb.id, kb.name);
                        }}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                      </button>
                    )}

                    {/* Delete (non-builtin only) */}
                    {!kb.is_builtin && (
                      <button
                        className="kb-item-icon-btn"
                        title={t('deleteKb')}
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteConfirmId(kb.id);
                        }}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                      </button>
                    )}
                  </div>

                  {/* Delete confirmation */}
                  {deleteConfirmId === kb.id && (
                    <div style={{
                      padding: "8px 12px", background: "rgba(231,76,60,0.05)",
                      borderTop: "1px solid var(--border)",
                    }}>
                      <p style={{ fontSize: 12, margin: "0 0 8px" }}>{t('deleteKbConfirm')}</p>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          className="btn-new"
                          style={{ background: "#e74c3c", color: "white" }}
                          onClick={() => handleDelete(kb.id)}
                        >
                          {t('deleteKb')}
                        </button>
                        <button className="kb-item-icon-btn" onClick={() => setDeleteConfirmId(null)}>
                          {t('escClose')}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Config editor panel (mutually exclusive with detail) */}
                  {editingConfigKbId === kb.id && (
                    <div style={{
                      padding: "12px", background: "var(--thinking-bg)",
                      borderTop: "1px solid var(--border)",
                    }}>
                      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>
                        {t('editKbConfig')} — {kb.name}
                      </div>

                      {/* Config message */}
                      {configMsg && (
                        <div style={{
                          marginBottom: 10, padding: "6px 10px", borderRadius: 4, fontSize: 12,
                          background: configMsg.kind === "ok" ? "rgba(46,204,113,0.1)" : "rgba(231,76,60,0.1)",
                          border: `1px solid ${configMsg.kind === "ok" ? "rgba(46,204,113,0.3)" : "rgba(231,76,60,0.3)"}`,
                          color: configMsg.kind === "ok" ? "#27ae60" : "#e74c3c",
                        }}>
                          {configMsg.text}
                        </div>
                      )}

                      <div className="field-label" style={{ marginBottom: 4 }}>{t('chunkMethod')}</div>
                      <select
                        className="form-input"
                        value={configForm.chunk_method as "hybrid" | "agent"}
                        onChange={(e) => setConfigForm({ ...configForm, chunk_method: e.target.value as "hybrid" | "agent" })}
                        style={{ width: "100%", marginBottom: 10 }}
                      >
                        <option value="hybrid">{t('hybrid')}</option>
                        <option value="agent">{t('agent')}</option>
                      </select>

                      {/* Embedding config */}
                      <div style={{ fontWeight: 600, fontSize: 12, color: "var(--muted-fg)", margin: "8px 0 6px", textTransform: "uppercase", letterSpacing: 0.5 }}>
                        {t('embeddingConfig')}
                      </div>
                      <div className="field-label" style={{ marginBottom: 4 }}>{t('embeddingModel')} *</div>
                      <input
                        className="form-input"
                        type="text"
                        value={configForm.embedding_model || ""}
                        onChange={(e) => setConfigForm({ ...configForm, embedding_model: e.target.value })}
                        style={{ width: "100%", marginBottom: 8 }}
                        placeholder="text-embedding-3-small"
                      />
                      <div className="field-label" style={{ marginBottom: 4 }}>{t('embeddingBaseUrl')}</div>
                      <input
                        className="form-input"
                        type="text"
                        value={configForm.embedding_base_url || ""}
                        onChange={(e) => setConfigForm({ ...configForm, embedding_base_url: e.target.value })}
                        style={{ width: "100%", marginBottom: 8 }}
                        placeholder="https://api.openai.com/v1"
                      />
                      <div className="field-label" style={{ marginBottom: 4 }}>{t('embeddingApiKey')}</div>
                      <input
                        className="form-input"
                        type="password"
                        value={configForm.embedding_api_key || ""}
                        onChange={(e) => setConfigForm({ ...configForm, embedding_api_key: e.target.value })}
                        style={{ width: "100%", marginBottom: 8 }}
                        placeholder={t('keepEmptyToRetain')}
                      />
                      <button
                        className="kb-item-icon-btn"
                        onClick={handleTestEmbedding}
                        disabled={testingEmbedding || !configForm.embedding_base_url || !configForm.embedding_api_key}
                        style={{ fontSize: 11, marginBottom: 10, opacity: (testingEmbedding || !configForm.embedding_base_url || !configForm.embedding_api_key) ? 0.5 : 1 }}
                      >
                        {testingEmbedding ? "..." : t('testConnection')}
                      </button>

                      {/* Agent chunker config */}
                      {configForm.chunk_method === "agent" && (
                        <>
                          <div style={{ fontWeight: 600, fontSize: 12, color: "var(--muted-fg)", margin: "8px 0 6px", textTransform: "uppercase", letterSpacing: 0.5 }}>
                            {t('agentChunkerConfig')}
                          </div>
                          <div className="field-label" style={{ marginBottom: 4 }}>{t('agentChunkerModel')}</div>
                          <input
                            className="form-input"
                            type="text"
                            value={configForm.agent_chunker_model || ""}
                            onChange={(e) => setConfigForm({ ...configForm, agent_chunker_model: e.target.value })}
                            style={{ width: "100%", marginBottom: 8 }}
                            placeholder="gpt-4o-mini"
                          />
                          <div className="field-label" style={{ marginBottom: 4 }}>{t('agentChunkerBaseUrl')}</div>
                          <input
                            className="form-input"
                            type="text"
                            value={configForm.agent_chunker_base_url || ""}
                            onChange={(e) => setConfigForm({ ...configForm, agent_chunker_base_url: e.target.value })}
                            style={{ width: "100%", marginBottom: 8 }}
                            placeholder="https://api.openai.com/v1"
                          />
                          <div className="field-label" style={{ marginBottom: 4 }}>{t('agentChunkerApiKey')}</div>
                          <input
                            className="form-input"
                            type="password"
                            value={configForm.agent_chunker_api_key || ""}
                            onChange={(e) => setConfigForm({ ...configForm, agent_chunker_api_key: e.target.value })}
                            style={{ width: "100%", marginBottom: 8 }}
                            placeholder={t('keepEmptyToRetain')}
                          />
                          <div className="field-label" style={{ marginBottom: 4 }}>{t('contextWindow')}</div>
                          <input
                            className="form-input"
                            type="number"
                            value={configForm.context_window || ""}
                            min={4096}
                            max={1000000}
                            step={4096}
                            onChange={(e) => setConfigForm({ ...configForm, context_window: parseInt(e.target.value) || 0 })}
                            style={{ width: "100%", marginBottom: 8 }}
                            placeholder="256000"
                          />
                        </>
                      )}

                      <div style={{ fontSize: 11, color: "var(--muted-fg)", marginBottom: 10 }}>
                        {t('configUpdateHint')}
                      </div>

                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          className="btn-new"
                          onClick={() => handleSaveConfig(kb.id)}
                          disabled={configSaving || !configForm.embedding_model?.trim()}
                          style={{ opacity: (configSaving || !configForm.embedding_model?.trim()) ? 0.5 : 1 }}
                        >
                          {configSaving ? "..." : t('saveConfig')}
                        </button>
                        <button className="kb-item-icon-btn" onClick={cancelEditConfig}>
                          {t('escClose')}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Expanded detail */}
                  {expandedKbId === kb.id && (
                    <div style={{
                      padding: "8px 12px", background: "var(--thinking-bg)",
                      borderTop: "1px solid var(--border)",
                    }}>
                      {detailError ? (
                        <p style={{ fontSize: 12, color: "#e74c3c", margin: 0 }}>
                          {t('fetchFailed')}
                          <button className="kb-item-icon-btn" style={{ marginLeft: 8, fontSize: 11 }} onClick={() => handleExpand(kb.id)}>
                            ↻
                          </button>
                        </p>
                      ) : !kbDetail ? (
                        <p style={{ fontSize: 12, color: "var(--muted-fg)", margin: 0 }}>...</p>
                      ) : (kbDetail.documents ?? []).length === 0 ? (
                        <p style={{ fontSize: 12, color: "var(--muted-fg)", margin: 0 }}>{t('noDocuments')}</p>
                      ) : (
                        <div>
                          {(kbDetail.documents ?? []).map((doc) => (
                            <div key={doc.doc_id} style={{
                              display: "flex", justifyContent: "space-between",
                              padding: "4px 0", fontSize: 12,
                              borderBottom: "1px solid var(--border)",
                            }}>
                              <span>{doc.title}</span>
                              <span style={{ color: "var(--muted-fg)" }}>
                                {doc.chunk_count} {t('chunks')} · {doc.status}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
