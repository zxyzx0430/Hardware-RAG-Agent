/**
 * RAG Settings Panel — Embedding & Agent Chunker global defaults.
 *
 * These values are used as defaults when creating a new KB.
 * Individual KBs can override them.
 */
import { useState, useEffect } from "react";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import { useI18n } from "../../i18n";

type FetchState = "idle" | "ok" | "fail";

export function RagSettingsPanel() {
  const { t } = useI18n();
  const {
    embeddingDefaultModel, embeddingDefaultBaseUrl, embeddingDefaultApiKey,
    agentChunkerDefaultModel, agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey,
    defaultContextWindow,
    updateSetting,
  } = useSettingsStore();

  const { fetchEmbeddingModels } = useKnowledgeStore();

  // ─── Embedding model fetch state ───
  const [showEmbKey, setShowEmbKey] = useState(false);
  const [embModels, setEmbModels] = useState<string[]>([]);
  const [fetchingEmb, setFetchingEmb] = useState(false);
  const [embStatus, setEmbStatus] = useState<FetchState>("idle");
  const [embMsg, setEmbMsg] = useState("");
  const [embCustom, setEmbCustom] = useState(false);

  // ─── Agent chunker model fetch state ───
  const [showAgentKey, setShowAgentKey] = useState(false);
  const [agentModels, setAgentModels] = useState<string[]>([]);
  const [fetchingAgent, setFetchingAgent] = useState(false);
  const [agentStatus, setAgentStatus] = useState<FetchState>("idle");
  const [agentMsg, setAgentMsg] = useState("");
  const [agentCustom, setAgentCustom] = useState(false);

  const handleFetchEmbModels = async () => {
    if (!embeddingDefaultApiKey.trim()) {
      setEmbStatus("fail"); setEmbMsg(t('enterApiKeyFirst')); return;
    }
    if (!embeddingDefaultBaseUrl.trim()) {
      setEmbStatus("fail"); setEmbMsg(t('enterBaseUrlFirst')); return;
    }
    setFetchingEmb(true); setEmbStatus("idle");
    try {
      const models = await fetchEmbeddingModels(embeddingDefaultBaseUrl, embeddingDefaultApiKey);
      setEmbModels(models);
      if (models.length > 0) {
        setEmbStatus("ok"); setEmbMsg(`${models.length} ${t('modelsFound')}`);
        setEmbCustom(false);
      } else {
        setEmbStatus("fail"); setEmbMsg(t('noModelsFound'));
      }
    } catch (e) {
      setEmbStatus("fail"); setEmbMsg(e instanceof Error ? e.message : t('fetchFailed'));
    } finally {
      setFetchingEmb(false);
    }
  };

  const handleFetchAgentModels = async () => {
    if (!agentChunkerDefaultApiKey.trim()) {
      setAgentStatus("fail"); setAgentMsg(t('enterApiKeyFirst')); return;
    }
    if (!agentChunkerDefaultBaseUrl.trim()) {
      setAgentStatus("fail"); setAgentMsg(t('enterBaseUrlFirst')); return;
    }
    setFetchingAgent(true); setAgentStatus("idle");
    try {
      const models = await fetchEmbeddingModels(agentChunkerDefaultBaseUrl, agentChunkerDefaultApiKey);
      setAgentModels(models);
      if (models.length > 0) {
        setAgentStatus("ok"); setAgentMsg(`${models.length} ${t('modelsFound')}`);
        setAgentCustom(false);
      } else {
        setAgentStatus("fail"); setAgentMsg(t('noModelsFound'));
      }
    } catch (e) {
      setAgentStatus("fail"); setAgentMsg(e instanceof Error ? e.message : t('fetchFailed'));
    } finally {
      setFetchingAgent(false);
    }
  };

  const renderStatus = (status: FetchState, msg: string) => {
    if (status === "ok") return <div style={{ marginTop: 4, fontSize: 12, color: "#16a085" }}>✓ {msg}</div>;
    if (status === "fail") return <div style={{ marginTop: 4, fontSize: 12, color: "#e74c3c" }}>✕ {msg}</div>;
    return null;
  };

  // Reusable model selector: select when models available, input for custom
  const renderModelField = (
    value: string,
    onChange: (v: string) => void,
    models: string[],
    customMode: boolean,
    setCustomMode: (v: boolean) => void,
    placeholder: string,
  ) => {
    if (models.length > 0 && !customMode) {
      return (
        <select
          className="form-input"
          value={value}
          onChange={(e) => {
            if (e.target.value === "__custom__") {
              setCustomMode(true);
              onChange("");
            } else {
              onChange(e.target.value);
            }
          }}
          style={{ flex: 1 }}
        >
          <option value="">{t('selectModel')}</option>
          {models.map((m) => <option key={m} value={m}>{m}</option>)}
          <option value="__custom__">✎ {t('customInput')}</option>
        </select>
      );
    }
    return (
      <input
        className="form-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{ flex: 1 }}
      />
    );
  };

  return (
    <div className="settings-section provider-detail-card" style={{ marginTop: 26 }}>
      <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('ragSettings')}</h3>

      {/* ─── Embedding Config ─── */}
      <div style={{ marginBottom: 20 }}>
        <div className="field-label" style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)", marginBottom: 10 }}>
          {t('embeddingConfig')}
        </div>

        <div className="field-label" style={{ marginTop: 8 }}>{t('embeddingModel')}</div>
        <div className="api-key-row">
          {renderModelField(
            embeddingDefaultModel,
            (v) => updateSetting("embeddingDefaultModel", v),
            embModels, embCustom, setEmbCustom,
            "text-embedding-3-small",
          )}
          <button className="verify-btn" onClick={handleFetchEmbModels} disabled={fetchingEmb}>
            {fetchingEmb ? t('verifying') : t('fetchEmbeddingModels')}
          </button>
        </div>
        {renderStatus(embStatus, embMsg)}

        <div className="field-label" style={{ marginTop: 12 }}>{t('embeddingBaseUrl')}</div>
        <input
          className="form-input"
          type="text"
          value={embeddingDefaultBaseUrl}
          onChange={(e) => updateSetting("embeddingDefaultBaseUrl", e.target.value)}
          placeholder="https://api.openai.com/v1"
        />

        <div className="field-label" style={{ marginTop: 12 }}>{t('embeddingApiKey')}</div>
        <div className="api-key-row">
          <input
            className="form-input"
            type={showEmbKey ? "text" : "password"}
            value={embeddingDefaultApiKey}
            onChange={(e) => updateSetting("embeddingDefaultApiKey", e.target.value)}
            placeholder="sk-..."
            style={{ flex: 1 }}
          />
          <button
            className="verify-btn"
            onClick={() => setShowEmbKey(!showEmbKey)}
            title={showEmbKey ? t('hide') : t('show')}
            style={{ minWidth: 36, padding: "0 8px", fontSize: 16 }}
          >
            {showEmbKey ? "🙈" : "👁️"}
          </button>
        </div>
      </div>

      {/* ─── Agent Chunker Config ─── */}
      <div style={{ marginBottom: 20 }}>
        <div className="field-label" style={{ fontWeight: 600, fontSize: 13, color: "var(--fg)", marginBottom: 10 }}>
          {t('agentChunkerConfig')}
        </div>

        <div className="field-label">{t('agentChunkerModel')}</div>
        <div className="api-key-row">
          {renderModelField(
            agentChunkerDefaultModel,
            (v) => updateSetting("agentChunkerDefaultModel", v),
            agentModels, agentCustom, setAgentCustom,
            "gpt-4o-mini",
          )}
          <button className="verify-btn" onClick={handleFetchAgentModels} disabled={fetchingAgent}>
            {fetchingAgent ? t('verifying') : t('fetchEmbeddingModels')}
          </button>
        </div>
        {renderStatus(agentStatus, agentMsg)}

        <div className="field-label" style={{ marginTop: 12 }}>{t('agentChunkerBaseUrl')}</div>
        <input
          className="form-input"
          type="text"
          value={agentChunkerDefaultBaseUrl}
          onChange={(e) => updateSetting("agentChunkerDefaultBaseUrl", e.target.value)}
          placeholder="https://api.openai.com/v1"
        />

        <div className="field-label" style={{ marginTop: 12 }}>{t('agentChunkerApiKey')}</div>
        <div className="api-key-row">
          <input
            className="form-input"
            type={showAgentKey ? "text" : "password"}
            value={agentChunkerDefaultApiKey}
            onChange={(e) => updateSetting("agentChunkerDefaultApiKey", e.target.value)}
            placeholder="sk-..."
            style={{ flex: 1 }}
          />
          <button
            className="verify-btn"
            onClick={() => setShowAgentKey(!showAgentKey)}
            title={showAgentKey ? t('hide') : t('show')}
            style={{ minWidth: 36, padding: "0 8px", fontSize: 16 }}
          >
            {showAgentKey ? "🙈" : "👁️"}
          </button>
        </div>

        <div className="field-label" style={{ marginTop: 12 }}>{t('contextWindow')}</div>
        <input
          className="form-input"
          type="number"
          value={defaultContextWindow || ""}
          min={4096}
          max={1000000}
          step={4096}
          onChange={(e) => updateSetting("defaultContextWindow", Math.max(4096, parseInt(e.target.value) || 4096))}
          placeholder="256000"
        />
      </div>
    </div>
  );
}
