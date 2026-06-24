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
  } = useKnowledgeStore();

  const {
    embeddingDefaultModel, embeddingDefaultBaseUrl, embeddingDefaultApiKey,
    agentChunkerDefaultModel, agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey,
    defaultContextWindow,
  } = useSettingsStore();

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [expandedKbId, setExpandedKbId] = useState<string | null>(null);
  const [kbDetail, setKbDetail] = useState<KBCollectionDetail | null>(null);
  const [detailError, setDetailError] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [importingKbId, setImportingKbId] = useState<string | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);

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
      setExpandedKbId(null);
      setKbDetail(null);
    }
  };

  const handleExpand = async (kbId: string) => {
    if (expandedKbId === kbId) {
      setExpandedKbId(null);
      setKbDetail(null);
      setDetailError(false);
      return;
    }
    setExpandedKbId(kbId);
    setKbDetail(null); // Clear immediately to avoid showing stale data
    setDetailError(false);
    const detail = await getCollectionDetail(kbId);
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
        setErrorMsg(t('noModelsFound'));
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
                        <span style={{ fontWeight: 600, fontSize: 13 }}>{kb.name}</span>
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
