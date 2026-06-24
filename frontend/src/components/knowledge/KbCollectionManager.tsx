/**
 * KB Collection Manager — modal for managing multiple knowledge bases.
 *
 * Features:
 * - List all KBs with stats (doc_count, chunk_count, enabled toggle)
 * - Create new KB (form with embedding + agent chunker config)
 * - Delete KB (with confirmation, builtin KB cannot be deleted)
 * - Click KB to expand document list
 */
import { useState, useEffect } from "react";
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
    toggleCollection, getCollectionDetail,
  } = useKnowledgeStore();

  const {
    embeddingDefaultModel, embeddingDefaultBaseUrl, embeddingDefaultApiKey,
    agentChunkerDefaultModel, agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey,
    defaultContextWindow,
  } = useSettingsStore();

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [expandedKbId, setExpandedKbId] = useState<string | null>(null);
  const [kbDetail, setKbDetail] = useState<KBCollectionDetail | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);

  // Create form state
  const [form, setForm] = useState<CreateKBRequest>({
    name: "",
    description: "",
    chunk_method: "hybrid",
    embedding_model: embeddingDefaultModel,
    embedding_base_url: embeddingDefaultBaseUrl,
    embedding_api_key: embeddingDefaultApiKey,
    agent_chunker_model: agentChunkerDefaultModel,
    agent_chunker_base_url: agentChunkerDefaultBaseUrl,
    agent_chunker_api_key: agentChunkerDefaultApiKey,
    context_window: defaultContextWindow,
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
        embedding_model: embeddingDefaultModel,
        embedding_base_url: embeddingDefaultBaseUrl,
        embedding_api_key: embeddingDefaultApiKey,
        agent_chunker_model: agentChunkerDefaultModel,
        agent_chunker_base_url: agentChunkerDefaultBaseUrl,
        agent_chunker_api_key: agentChunkerDefaultApiKey,
        context_window: defaultContextWindow,
      }));
    }
  }, [showCreateForm]);

  if (!open) return null;

  const handleCreate = async () => {
    if (!form.name.trim()) return;
    const result = await createCollection(form);
    if (result) {
      setShowCreateForm(false);
      setForm({ ...form, name: "", description: "" });
    }
  };

  const handleDelete = async (kbId: string) => {
    await deleteCollection(kbId);
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
      return;
    }
    setExpandedKbId(kbId);
    const detail = await getCollectionDetail(kbId);
    setKbDetail(detail);
  };

  return (
    <div className="modal-overlay" onClick={onClose} style={{
      position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
      background: "rgba(0,0,0,0.5)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{
        background: "var(--bg)", borderRadius: 8, padding: 24,
        width: "90%", maxWidth: 720, maxHeight: "85vh", overflow: "auto",
        border: "1px solid var(--border)",
      }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{t('kbCollections')}</h2>
          <button className="kb-item-icon-btn" onClick={onClose} style={{ fontSize: 18 }}>✕</button>
        </div>

        {/* Action bar */}
        <div style={{ marginBottom: 16, display: "flex", gap: 8 }}>
          <button className="btn-new" onClick={() => setShowCreateForm(!showCreateForm)}>
            + {t('createKb')}
          </button>
        </div>

        {/* Create form */}
        {showCreateForm && (
          <div style={{
            marginBottom: 16, padding: 16, borderRadius: 6,
            border: "1px solid var(--border)", background: "var(--thinking-bg)",
          }}>
            <div style={{ marginBottom: 12 }}>
              <label className="field-label">{t('kbName')} *</label>
              <input
                className="settings-input"
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ marginBottom: 12 }}>
              <label className="field-label">{t('kbDescription')}</label>
              <input
                className="settings-input"
                type="text"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ marginBottom: 12 }}>
              <label className="field-label">{t('chunkMethod')}</label>
              <select
                className="settings-input"
                value={form.chunk_method}
                onChange={(e) => setForm({ ...form, chunk_method: e.target.value as "hybrid" | "agent" })}
                style={{ width: "100%" }}
              >
                <option value="hybrid">{t('hybrid')}</option>
                <option value="agent">{t('agent')}</option>
              </select>
              {form.chunk_method === "agent" && (
                <p style={{ fontSize: 12, color: "var(--muted-fg)", marginTop: 4 }}>
                  💡 {t('agentChunkHint')}
                </p>
              )}
            </div>

            {/* Embedding config */}
            <div style={{ marginBottom: 8, fontWeight: 600, fontSize: 13 }}>{t('embeddingConfig')}</div>
            <div style={{ marginBottom: 12 }}>
              <label className="field-label">{t('embeddingModel')}</label>
              <input
                className="settings-input"
                type="text"
                value={form.embedding_model}
                onChange={(e) => setForm({ ...form, embedding_model: e.target.value })}
                style={{ width: "100%" }}
              />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label className="field-label">{t('embeddingBaseUrl')}</label>
              <input
                className="settings-input"
                type="text"
                value={form.embedding_base_url}
                onChange={(e) => setForm({ ...form, embedding_base_url: e.target.value })}
                style={{ width: "100%" }}
              />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label className="field-label">{t('embeddingApiKey')}</label>
              <input
                className="settings-input"
                type="password"
                value={form.embedding_api_key}
                onChange={(e) => setForm({ ...form, embedding_api_key: e.target.value })}
                style={{ width: "100%" }}
                placeholder="(optional, uses global default if empty)"
              />
            </div>

            {/* Agent chunker config (only relevant for agent method) */}
            {form.chunk_method === "agent" && (
              <>
                <div style={{ marginBottom: 8, fontWeight: 600, fontSize: 13 }}>{t('agentChunkerConfig')}</div>
                <div style={{ marginBottom: 12 }}>
                  <label className="field-label">{t('agentChunkerModel')}</label>
                  <input
                    className="settings-input"
                    type="text"
                    value={form.agent_chunker_model}
                    onChange={(e) => setForm({ ...form, agent_chunker_model: e.target.value })}
                    style={{ width: "100%" }}
                  />
                </div>
                <div style={{ marginBottom: 12 }}>
                  <label className="field-label">{t('agentChunkerBaseUrl')}</label>
                  <input
                    className="settings-input"
                    type="text"
                    value={form.agent_chunker_base_url}
                    onChange={(e) => setForm({ ...form, agent_chunker_base_url: e.target.value })}
                    style={{ width: "100%" }}
                  />
                </div>
                <div style={{ marginBottom: 12 }}>
                  <label className="field-label">{t('agentChunkerApiKey')}</label>
                  <input
                    className="settings-input"
                    type="password"
                    value={form.agent_chunker_api_key}
                    onChange={(e) => setForm({ ...form, agent_chunker_api_key: e.target.value })}
                    style={{ width: "100%" }}
                    placeholder="(optional, uses global default if empty)"
                  />
                </div>
                <div style={{ marginBottom: 12 }}>
                  <label className="field-label">{t('contextWindow')}</label>
                  <input
                    className="settings-input"
                    type="number"
                    value={form.context_window}
                    min={4096}
                    max={1000000}
                    step={4096}
                    onChange={(e) => setForm({ ...form, context_window: parseInt(e.target.value) || 256000 })}
                    style={{ width: "100%" }}
                  />
                </div>
              </>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button className="btn-new" onClick={handleCreate} disabled={!form.name.trim()}>
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
          <div style={{ textAlign: "center", padding: 24, color: "var(--muted-fg)" }}>...</div>
        ) : collections.length === 0 ? (
          <div style={{ textAlign: "center", padding: 24, color: "var(--muted-fg)" }}>
            {t('noCollections')}
          </div>
        ) : (
          <div>
            {collections.map((kb) => (
              <div key={kb.id} style={{
                marginBottom: 8, borderRadius: 6,
                border: `1px solid ${expandedKbId === kb.id ? "var(--primary)" : "var(--border)"}`,
                background: "var(--bg)", overflow: "hidden",
              }}>
                {/* KB row */}
                <div
                  style={{
                    display: "flex", alignItems: "center", padding: "10px 12px",
                    cursor: "pointer", gap: 12,
                  }}
                  onClick={() => handleExpand(kb.id)}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ fontWeight: 600, fontSize: 14 }}>{kb.name}</span>
                      {kb.is_builtin && (
                        <span style={{
                          fontSize: 10, padding: "1px 6px", borderRadius: 3,
                          background: "var(--primary)", color: "white",
                        }}>{t('builtinKb')}</span>
                      )}
                      {activeKbId === kb.id && (
                        <span style={{
                          fontSize: 10, padding: "1px 6px", borderRadius: 3,
                          background: "var(--accent)", color: "var(--bg)",
                        }}>●</span>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: "var(--muted-fg)", marginTop: 2 }}>
                      {kb.chunk_method === "agent" ? t('agent') : t('hybrid')} · {kb.embedding_model} · {t('docCount')}: {kb.doc_count} · {t('chunkCount')}: {kb.chunk_count}
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
                      title={t('targetKb')}
                      onClick={(e) => {
                        e.stopPropagation();
                        setActiveKb(kb.id);
                      }}
                    >
                      →
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
                    padding: "8px 12px", background: "var(--danger-bg, rgba(255,0,0,0.05))",
                    borderTop: "1px solid var(--border)",
                  }}>
                    <p style={{ fontSize: 12, margin: "0 0 8px" }}>{t('deleteKbConfirm')}</p>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        className="btn-new"
                        style={{ background: "var(--danger)", color: "white" }}
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
                {expandedKbId === kb.id && kbDetail && (
                  <div style={{
                    padding: "8px 12px", background: "var(--thinking-bg)",
                    borderTop: "1px solid var(--border)",
                  }}>
                    {kbDetail.documents.length === 0 ? (
                      <p style={{ fontSize: 12, color: "var(--muted-fg)", margin: 0 }}>—</p>
                    ) : (
                      <div>
                        {kbDetail.documents.map((doc) => (
                          <div key={doc.doc_id} style={{
                            display: "flex", justifyContent: "space-between",
                            padding: "4px 0", fontSize: 12,
                            borderBottom: "1px solid var(--border)",
                          }}>
                            <span>{doc.title}</span>
                            <span style={{ color: "var(--muted-fg)" }}>
                              {doc.chunk_count} chunks · {doc.status}
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
  );
}
