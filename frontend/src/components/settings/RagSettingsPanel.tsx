/**
 * RAG Settings Panel — Embedding & Agent Chunker global defaults.
 *
 * These values are used as defaults when creating a new KB.
 * Individual KBs can override them.
 */
import { useState } from "react";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import { useI18n } from "../../i18n";

export function RagSettingsPanel() {
  const { t } = useI18n();
  const {
    embeddingDefaultModel, embeddingDefaultBaseUrl, embeddingDefaultApiKey,
    agentChunkerDefaultModel, agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey,
    defaultContextWindow,
    updateSetting,
  } = useSettingsStore();

  const { fetchEmbeddingModels } = useKnowledgeStore();
  const [showEmbKey, setShowEmbKey] = useState(false);
  const [showAgentKey, setShowAgentKey] = useState(false);
  const [embeddingModels, setEmbeddingModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);

  const handleFetchModels = async () => {
    if (!embeddingDefaultApiKey) return;
    setFetchingModels(true);
    try {
      const models = await fetchEmbeddingModels(embeddingDefaultBaseUrl, embeddingDefaultApiKey);
      setEmbeddingModels(models);
    } finally {
      setFetchingModels(false);
    }
  };

  return (
    <div className="settings-section">
      <h3>{t('ragSettings')}</h3>

      {/* ─── Embedding Config ─── */}
      <div style={{ marginBottom: 24 }}>
        <div className="field-label" style={{ fontWeight: 600, marginBottom: 8 }}>
          {t('embeddingConfig')}
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('embeddingModel')}</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="settings-input"
              type="text"
              value={embeddingDefaultModel}
              onChange={(e) => updateSetting("embeddingDefaultModel", e.target.value)}
              list="embedding-model-list"
              style={{ flex: 1 }}
            />
            <button
              className="btn-new"
              onClick={handleFetchModels}
              disabled={fetchingModels || !embeddingDefaultApiKey}
              style={{ whiteSpace: "nowrap" }}
            >
              {fetchingModels ? "..." : t('fetchEmbeddingModels')}
            </button>
          </div>
          <datalist id="embedding-model-list">
            {embeddingModels.map((m) => <option key={m} value={m} />)}
          </datalist>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('embeddingBaseUrl')}</label>
          <input
            className="settings-input"
            type="text"
            value={embeddingDefaultBaseUrl}
            onChange={(e) => updateSetting("embeddingDefaultBaseUrl", e.target.value)}
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('embeddingApiKey')}</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="settings-input"
              type={showEmbKey ? "text" : "password"}
              value={embeddingDefaultApiKey}
              onChange={(e) => updateSetting("embeddingDefaultApiKey", e.target.value)}
              style={{ flex: 1 }}
            />
            <button className="kb-item-icon-btn" onClick={() => setShowEmbKey(!showEmbKey)}>
              {showEmbKey ? t('hide') : t('show')}
            </button>
          </div>
        </div>
      </div>

      {/* ─── Agent Chunker Config ─── */}
      <div style={{ marginBottom: 24 }}>
        <div className="field-label" style={{ fontWeight: 600, marginBottom: 8 }}>
          {t('agentChunkerConfig')}
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('agentChunkerModel')}</label>
          <input
            className="settings-input"
            type="text"
            value={agentChunkerDefaultModel}
            onChange={(e) => updateSetting("agentChunkerDefaultModel", e.target.value)}
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('agentChunkerBaseUrl')}</label>
          <input
            className="settings-input"
            type="text"
            value={agentChunkerDefaultBaseUrl}
            onChange={(e) => updateSetting("agentChunkerDefaultBaseUrl", e.target.value)}
          />
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('agentChunkerApiKey')}</label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="settings-input"
              type={showAgentKey ? "text" : "password"}
              value={agentChunkerDefaultApiKey}
              onChange={(e) => updateSetting("agentChunkerDefaultApiKey", e.target.value)}
              style={{ flex: 1 }}
            />
            <button className="kb-item-icon-btn" onClick={() => setShowAgentKey(!showAgentKey)}>
              {showAgentKey ? t('hide') : t('show')}
            </button>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label className="field-label">{t('contextWindow')}</label>
          <input
            className="settings-input"
            type="number"
            value={defaultContextWindow}
            min={4096}
            max={1000000}
            step={4096}
            onChange={(e) => updateSetting("defaultContextWindow", parseInt(e.target.value) || 256000)}
          />
        </div>
      </div>

      <div style={{ borderRadius: 6, border: "1px solid var(--border)", background: "var(--thinking-bg)", padding: "12px 16px" }}>
        <p style={{ fontSize: 12, color: "var(--muted-fg)" }}>
          {t('globalDefaults')}: {embeddingDefaultModel} / {agentChunkerDefaultModel} / {defaultContextWindow.toLocaleString()} tokens
        </p>
      </div>
    </div>
  );
}
