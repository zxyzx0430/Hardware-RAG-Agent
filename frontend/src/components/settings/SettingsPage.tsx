import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAppStore } from "../../stores/useAppStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore, CONTEXT_WINDOW_256K, CONTEXT_WINDOW_1M } from "../../stores/useSessionStore";
import { useLogStore } from "../../stores/useLogStore";
import { useModalStore } from "../../stores/useModalStore";
import { useI18n } from "../../i18n";
import { RagSettingsPanel } from "./RagSettingsPanel";
import { TokenUsagePanel } from "./TokenUsagePanel";
import { AuditLogPanel } from "./AuditLogPanel";
import { EmptyState } from "../shared/EmptyState";

function isApiKeyMissing(providers: { apiKey?: string }[]): boolean {
  return !providers.some((p) => p.apiKey?.trim());
}

const TAB_IDS = ["api", "rag", "memory", "appearance", "usage", "logs", "audit", "mcp", "skills", "about"] as const;

const TOOL_ENTRIES = [
  { id: 'web_search', label: 'Web Search', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg> },
  { id: 'image_generation', label: 'Image Generation', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg> },
];

export function SettingsPage() {
  const { t } = useI18n();
  const needsApiKey = useChatStore((s) => s.needsApiKey);
  const { setActiveNav, themeMode, setThemeMode, lang, setLang, chatFontSize, setChatFontSize } = useAppStore();
  const {
    providers, chatProviderId, chatModel, imageProviderId, imageModel, visionProviderId, visionModel,
    temperature, topK, maxTokens, relevanceThreshold, systemPrompt, longTermMemory, skills,
    mcpServers, webSearchConfig, showWebSearchKey, imageGenerationConfig, showImageGenerationKey,
    addProvider, removeProvider, updateProvider, verifyProvider, fetchProviderModels,
    setChatModel, setImageModel, setVisionModel,
    updateSetting, toggleSkill, toggleMcpServer,
    setWebSearchKey, setWebSearchBaseUrl, toggleShowWebSearchKey,
    setImageGenerationKey, setImageGenerationBaseUrl, setImageGenerationModel, toggleShowImageGenerationKey, addMcpServer,
    fetchMCPServers, startMCPServer, stopMCPServer, addMCPServer, removeMCPServer,
    fetchTools,
  } = useSettingsStore();
  const { buffer, filter, setFilter, clear, getFiltered } = useLogStore();
  const { confirmDialog } = useModalStore();
  // Context window: reactively track current session's value + setter
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setContextWindow = useSessionStore((s) => s.setContextWindow);
  const currentContextWindow = useSessionStore((s) =>
    s.sessions.find((x) => x.id === activeSessionId)?.contextWindow ?? CONTEXT_WINDOW_256K
  );
  const [tab, setTab] = useState<(typeof TAB_IDS)[number]>("api");
  const [showMcpForm, setShowMcpForm] = useState(false);
  const [mcpFormName, setMcpFormName] = useState("");
  const [mcpFormCommand, setMcpFormCommand] = useState("");
  const [mcpLoading, setMcpLoading] = useState<string | null>(null);
  const [logRefreshKey, setLogRefreshKey] = useState(0);
  const [savedField, setSavedField] = useState<string | null>(null);
  const savedTimerRef = useRef<number | null>(null);
  const triggerSaved = useCallback((field: string) => {
    setSavedField(field);
    if (savedTimerRef.current !== null) {
      window.clearTimeout(savedTimerRef.current);
    }
    savedTimerRef.current = window.setTimeout(() => {
      setSavedField(null);
      savedTimerRef.current = null;
    }, 2000);
  }, []);
  useEffect(() => {
    return () => {
      if (savedTimerRef.current !== null) {
        window.clearTimeout(savedTimerRef.current);
      }
    };
  }, []);
  // 切换到 skills tab 时拉取后端真实工具列表
  useEffect(() => {
    if (tab === 'skills') {
      fetchTools();
    }
  }, [tab, fetchTools]);
  const savedLabel = lang === 'zh' ? '✓ 已保存' : '✓ Saved';

  // 服务商详情 + 新建表单相关状态
  const [selectedProviderId, setSelectedProviderId] = useState<string>("");
  const [newProviderName, setNewProviderName] = useState("");
  const [newProviderBaseUrl, setNewProviderBaseUrl] = useState("");
  const [newProviderApiKey, setNewProviderApiKey] = useState("");
  const [showNewProviderKey, setShowNewProviderKey] = useState(false);
  const [showProvKey, setShowProvKey] = useState(false);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [verifyError, setVerifyError] = useState(false);
  const [fetchModelsLoading, setFetchModelsLoading] = useState(false);

  const selectedProvider = useMemo(
    () => providers.find((p) => p.id === selectedProviderId) || null,
    [providers, selectedProviderId]
  );
  const verifiedProviders = useMemo(
    () => providers.filter((p) => p.verified),
    [providers]
  );

  // MCP 服务器列表从 API 拉取
  const { data: mcpServersData } = useQuery({
    queryKey: ["mcpServers"],
    queryFn: async () => {
      try {
        await fetchMCPServers();
        return true;
      } catch {
        return false;
      }
    },
    staleTime: 10 * 1000,
    refetchInterval: 15000,
  });

  const TIME_RANGE_OPTIONS = [
    { value: 0, label: t('allTime') },
    { value: 60, label: t('last1Hour') },
    { value: 360, label: t('last6Hours') },
    { value: 1440, label: t('last24Hours') },
    { value: 10080, label: t('last7Days') },
  ];

  const filteredBuffer = useMemo(() => getFiltered(), [buffer, filter.levels, filter.timeRange, logRefreshKey]);

  const handleVerifyProvider = useCallback(async () => {
    if (!selectedProvider) return;
    setVerifyLoading(true);
    setVerifyError(false);
    try {
      const ok = await verifyProvider(selectedProvider.id);
      if (!ok) setVerifyError(true);
    } catch {
      setVerifyError(true);
    } finally {
      setVerifyLoading(false);
    }
  }, [selectedProvider, verifyProvider]);

  const handleFetchModels = useCallback(async () => {
    if (!selectedProvider) return;
    setFetchModelsLoading(true);
    try {
      await fetchProviderModels(selectedProvider.id);
    } finally {
      setFetchModelsLoading(false);
    }
  }, [selectedProvider, fetchProviderModels]);

  const handleCreateProvider = useCallback(async () => {
    if (!newProviderName.trim() || !newProviderBaseUrl.trim()) return;
    const created = addProvider(newProviderName, newProviderBaseUrl, newProviderApiKey);
    setNewProviderName("");
    setNewProviderBaseUrl("");
    setNewProviderApiKey("");
    setSelectedProviderId(created.id);
    triggerSaved('newProvider');
    // FIX-8: 自动验证新添加的服务商（store 内部已捕获异常并记录日志，不阻塞 UI）
    await verifyProvider(created.id);
  }, [newProviderName, newProviderBaseUrl, newProviderApiKey, addProvider, verifyProvider, triggerSaved]);

  const handleDeleteProvider = useCallback(async () => {
    if (!selectedProvider) return;
    const name = selectedProvider.name;
    const ok = await confirmDialog({
      title: lang === 'zh' ? '删除服务商' : 'Delete Provider',
      message: lang === 'zh' ? `确定删除服务商「${name}」？该操作不可撤销。` : `Delete provider "${name}"? This cannot be undone.`,
      danger: true,
    });
    if (!ok) return;
    removeProvider(selectedProvider.id);
    setSelectedProviderId("");
  }, [selectedProvider, removeProvider, lang, confirmDialog]);

  const handleAddMcpServer = async () => {
    if (!mcpFormName.trim() || !mcpFormCommand.trim()) return;
    const id = mcpFormName.trim().toLowerCase().replace(/\s+/g, '-');
    await addMCPServer({
      id,
      name: mcpFormName.trim(),
      command: mcpFormCommand.trim(),
    });
    setMcpFormName("");
    setMcpFormCommand("");
    setShowMcpForm(false);
  };

  // Close settings on Esc and expose a real close button instead of a giant clickable overlay
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActiveNav("chat");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [setActiveNav]);

  return (
    <div className="settings-overlay">
      <div className="settings-shell">
        <div className="settings-page">
          <div className="settings-header" style={{ padding: '42px 40px 18px 40px' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
              <h2 style={{ fontSize: 18, fontWeight: 600 }}>{t('settings')}</h2>
              <button
                className="kb-item-icon-btn"
                onClick={() => setActiveNav("chat")}
                aria-label={lang === 'zh' ? '关闭设置' : 'Close settings'}
                title={lang === 'zh' ? '关闭设置' : 'Close settings'}
                style={{ padding: 6, borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--fg)', cursor: 'pointer' }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
              </button>
            </div>
            <div className="settings-tabs" style={{ marginTop: 20 }}>
              {TAB_IDS.map((id) => {
                const tabLabelMap: Record<string, string> = {
                  api: t('apiConfig'), rag: t('ragParams'), memory: t('memory'),
                  appearance: t('appearance'), usage: t('usage'), logs: t('logs'),
                  audit: '工具审计', mcp: t('mcpService'), skills: t('skills'), about: t('about'),
                };
                return (
                  <button key={id} className={`settings-tab${tab === id ? " active" : ""}`} onClick={() => setTab(id)}>{tabLabelMap[id]}</button>
                );
              })}
            </div>
          </div>

          <div className="settings-scroll" style={{ padding: '28px 40px 40px 40px' }}>
            {tab === 'api' && (
              <>
                {/* 服务商列表 */}
                <div className="settings-section" style={{ marginBottom: 26 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('provider')}</h3>
                  <div className="provider-grid">
                    {providers.map((p) => (
                      <button
                        key={p.id}
                        className={`provider-card${selectedProviderId === p.id ? ' active' : ''}`}
                        onClick={() => setSelectedProviderId(p.id)}
                        title={p.verified ? `${p.models.length} ${t('model')}` : (lang === 'zh' ? '未验证' : 'Unverified')}
                      >
                        <span className="provider-name">{p.name}</span>
                        {p.verified
                          ? <span className="provider-check ok">✓</span>
                          : <span className="provider-check pending">?</span>}
                      </button>
                    ))}
                    {providers.length === 0 && (
                      <EmptyState size="sm" title={lang === 'zh' ? '暂无服务商，请在下方新建' : 'No providers yet. Create one below.'} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="3" width="20" height="6" rx="2" /><rect x="2" y="11" width="20" height="6" rx="2" /><rect x="2" y="19" width="20" height="4" rx="1" /></svg>} />
                    )}
                  </div>
                </div>

                {/* 新建服务商表单 */}
                <div className="settings-section" style={{ marginBottom: 26 }}>
                  <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 12 }}>
                    {lang === 'zh' ? '新建服务商' : 'New Provider'}
                  </h3>
                  <div className="field-label">{t('nameLabel')}</div>
                  <input
                    className="form-input"
                    value={newProviderName}
                    onChange={(e) => setNewProviderName(e.target.value)}
                    placeholder={lang === 'zh' ? '例如：我的 OpenAI' : 'e.g. My OpenAI'}
                    style={{ marginBottom: 8 }}
                  />
                  <div className="field-label">{t('baseUrl')}</div>
                  <input
                    className="form-input"
                    value={newProviderBaseUrl}
                    onChange={(e) => setNewProviderBaseUrl(e.target.value)}
                    placeholder="https://api.openai.com/v1"
                    style={{ marginBottom: 8 }}
                  />
                  <div className="field-label">{t('apiKey')}</div>
                  <div className="api-key-row">
                    <input
                      className="form-input"
                      type={showNewProviderKey ? 'text' : 'password'}
                      value={newProviderApiKey}
                      onChange={(e) => setNewProviderApiKey(e.target.value)}
                      placeholder="sk-..."
                      style={{ flex: 1 }}
                    />
                    <button
                      className="verify-btn"
                      onClick={() => setShowNewProviderKey((v) => !v)}
                      title={showNewProviderKey ? t('hide') : t('show')}
                      style={{ minWidth: 36, padding: '0 8px', fontSize: 16 }}
                    >
                      {showNewProviderKey ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg> : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>}
                    </button>
                    <button
                      className="verify-btn primary"
                      onClick={handleCreateProvider}
                      disabled={!newProviderName.trim() || !newProviderBaseUrl.trim()}
                    >
                      {t('add')}
                    </button>
                  </div>
                  {savedField === 'newProvider' && <span className="saved-feedback">{savedLabel}</span>}
                </div>

                {/* 服务商详情 */}
                {selectedProvider && (
                  <div className="settings-section provider-detail-card" style={{ marginBottom: 26 }}>
                    <div className="provider-detail-title-row">
                      <span className="provider-detail-title">{selectedProvider.name}</span>
                      {selectedProvider.verified
                        ? <span style={{ marginLeft: 8, fontSize: 12, color: "var(--success)" }}>✓ {selectedProvider.models.length} {t('model')}</span>
                        : <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--muted-fg)' }}>{lang === 'zh' ? '未验证' : 'Unverified'}</span>}
                      <button
                        className="verify-btn danger"
                        onClick={handleDeleteProvider}
                        title={t('delete')}
                        style={{ marginLeft: 'auto', minWidth: 36, padding: '0 8px', fontSize: 14 }}
                      >
                        ✕
                      </button>
                    </div>
                    <div className="field-label">{t('nameLabel')}</div>
                    <input
                      className="form-input"
                      value={selectedProvider.name}
                      onChange={(e) => updateProvider(selectedProvider.id, { name: e.target.value })}
                      onBlur={() => triggerSaved('provName')}
                      style={{ marginBottom: 8 }}
                    />
                    <div className="field-label">{t('baseUrl')}</div>
                    <input
                      className="form-input"
                      value={selectedProvider.baseUrl}
                      onChange={(e) => updateProvider(selectedProvider.id, { baseUrl: e.target.value })}
                      onBlur={() => triggerSaved('provBaseUrl')}
                      style={{ marginBottom: 8 }}
                    />
                    <div className="field-label">{t('apiKey')}</div>
                    <div className="api-key-row">
                      <input
                        className="form-input"
                        type={showProvKey ? 'text' : 'password'}
                        value={selectedProvider.apiKey}
                        onChange={(e) => updateProvider(selectedProvider.id, { apiKey: e.target.value })}
                        onBlur={() => triggerSaved('provApiKey')}
                        style={{ flex: 1 }}
                      />
                      <button
                        className="verify-btn"
                        onClick={() => setShowProvKey((v) => !v)}
                        title={showProvKey ? t('hide') : t('show')}
                        style={{ minWidth: 36, padding: '0 8px', fontSize: 16 }}
                      >
                        {showProvKey ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg> : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>}
                      </button>
                      <button
                        className="settings-btn"
                        onClick={handleVerifyProvider}
                        disabled={verifyLoading || !selectedProvider.apiKey.trim() || !selectedProvider.baseUrl.trim()}
                      >
                        {verifyLoading ? t('verifying') : t('verify')}
                      </button>
                      <button
                        className="settings-btn primary"
                        onClick={handleFetchModels}
                        disabled={fetchModelsLoading || !selectedProvider.baseUrl.trim()}
                        title={t('fetchEmbeddingModels')}
                      >
                        {fetchModelsLoading ? '...' : t('fetchEmbeddingModels')}
                      </button>
                    </div>
                    {savedField === 'provName' && <span className="saved-feedback">{savedLabel}</span>}
                    {savedField === 'provBaseUrl' && <span className="saved-feedback">{savedLabel}</span>}
                    {savedField === 'provApiKey' && !verifyLoading && <span className="saved-feedback">{savedLabel}</span>}
                    {verifyError && <div style={{ marginTop: 6, fontSize: 12, color: "var(--danger)" }}>{t('invalidKey')}</div>}
                    {selectedProvider.verified && selectedProvider.models.length > 0 && (
                      <div style={{ marginTop: 6, fontSize: 12, color: "var(--success)" }}>
                        {t('verifiedKey')} · {selectedProvider.models.length} {t('model')}
                      </div>
                    )}
                  </div>
                )}

                {/* 三类模型配置 */}
                <div className="settings-section" style={{ marginBottom: 26 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>
                    {lang === 'zh' ? '模型选择' : 'Model Selection'}
                  </h3>
                  {verifiedProviders.length === 0 && (
                    <div style={{ fontSize: 12, color: 'var(--muted-fg)', marginBottom: 12 }}>
                      {lang === 'zh' ? '请先验证至少一个服务商' : 'Please verify at least one provider first'}
                    </div>
                  )}
                  {/* 对话模型 */}
                  <div style={{ marginBottom: 16 }}>
                    <div className="field-label">{t('defaultModel')}</div>
                    <select
                      className="form-select"
                      value={chatProviderId}
                      onChange={(e) => {
                        const pid = e.target.value;
                        const p = verifiedProviders.find((pp) => pp.id === pid);
                        const newModel = p?.models[0] || "";
                        setChatModel(pid, newModel);
                        const sid = useChatStore.getState().activeSessionId;
                        if (sid) useSessionStore.getState().updateSessionMeta(sid, { model: newModel });
                        triggerSaved('chatModel');
                      }}
                      style={{ marginBottom: 6 }}
                    >
                      <option value="">{lang === 'zh' ? '— 选择服务商 —' : '— Select provider —'}</option>
                      {verifiedProviders.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <select
                      className="form-select"
                      value={chatModel}
                      onChange={(e) => {
                        const newModel = e.target.value;
                        setChatModel(chatProviderId, newModel);
                        const sid = useChatStore.getState().activeSessionId;
                        if (sid) useSessionStore.getState().updateSessionMeta(sid, { model: newModel });
                        triggerSaved('chatModel');
                      }}
                      disabled={!chatProviderId}
                    >
                      <option value="">{lang === 'zh' ? '— 选择模型 —' : '— Select model —'}</option>
                      {(verifiedProviders.find((p) => p.id === chatProviderId)?.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                    {savedField === 'chatModel' && <span className="saved-feedback">{savedLabel}</span>}
                  </div>
                  {/* 视觉分析模型 */}
                  <div style={{ marginBottom: 16 }}>
                    <div className="field-label">{t('visionModel')}</div>
                    <select
                      className="form-select"
                      value={visionProviderId}
                      onChange={(e) => {
                        const pid = e.target.value;
                        const p = verifiedProviders.find((pp) => pp.id === pid);
                        setVisionModel(pid, pid ? (p?.models[0] || "") : "");
                        triggerSaved('visionModel');
                      }}
                      style={{ marginBottom: 6 }}
                    >
                      <option value="">{`auto (${t('followChatModel')})`}</option>
                      {verifiedProviders.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                    <select
                      className="form-select"
                      value={visionModel}
                      onChange={(e) => { setVisionModel(visionProviderId, e.target.value); triggerSaved('visionModel'); }}
                      disabled={!visionProviderId}
                    >
                      <option value="">{lang === 'zh' ? '— 选择模型 —' : '— Select model —'}</option>
                      {(verifiedProviders.find((p) => p.id === visionProviderId)?.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                    {savedField === 'visionModel' && <span className="saved-feedback">{savedLabel}</span>}
                  </div>
                  {/* 图像生成模型 */}
                  <div style={{ marginBottom: 16 }}>
                    <div className="field-label">{t('imageModel')}</div>
                    <select
                      className="form-select"
                      value={imageProviderId}
                      onChange={(e) => {
                        const pid = e.target.value;
                        const p = providers.find((pp) => pp.id === pid);
                        // Image provider supports unverified providers (e.g. inroi /images/generations only).
                        // Pre-fill first model if available, otherwise keep existing or empty.
                        const firstModel = p?.models?.[0] || "";
                        setImageModel(pid, pid ? firstModel : "");
                        triggerSaved('imageModel');
                      }}
                      style={{ marginBottom: 6 }}
                    >
                      <option value="">{`auto (${t('followChatModel')})`}</option>
                      {providers.map((p) => <option key={p.id} value={p.id}>{p.name} {p.verified ? '' : '(未验证)'}</option>)}
                    </select>
                    {/* Model selector: dropdown when models are known, free-text input otherwise (e.g. inroi). */}
                    {(() => {
                      const selectedImageProvider = providers.find((p) => p.id === imageProviderId);
                      const availableModels = selectedImageProvider?.models || [];
                      if (availableModels.length > 0) {
                        return (
                          <select
                            className="form-select"
                            value={imageModel}
                            onChange={(e) => { setImageModel(imageProviderId, e.target.value); triggerSaved('imageModel'); }}
                            disabled={!imageProviderId}
                          >
                            <option value="">{lang === 'zh' ? '— 选择模型 —' : '— Select model —'}</option>
                            {availableModels.map((m) => <option key={m} value={m}>{m}</option>)}
                          </select>
                        );
                      }
                      return (
                        <input
                          type="text"
                          className="form-input"
                          value={imageModel}
                          placeholder={lang === 'zh' ? '直接输入模型名，例如 gpt-image-2' : 'Enter model name, e.g. gpt-image-2'}
                          onChange={(e) => { setImageModel(imageProviderId, e.target.value); triggerSaved('imageModel'); }}
                          disabled={!imageProviderId}
                          style={{ marginTop: 6 }}
                        />
                      );
                    })()}
                    {savedField === 'imageModel' && <span className="saved-feedback">{savedLabel}</span>}
                  </div>
                </div>

                {/* Context Window config */}
                <div className="settings-section" style={{ marginBottom: 26 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>
                    {lang === 'zh' ? '上下文窗口' : 'Context Window'}
                  </h3>
                  <div className="field-label">
                    {lang === 'zh' ? '窗口大小' : 'Window Size'}
                  </div>
                  <select
                    className="form-select"
                    value={currentContextWindow}
                    onChange={(e) => setContextWindow(Number(e.target.value))}
                    style={{ marginBottom: 8 }}
                  >
                    <option value={CONTEXT_WINDOW_256K}>256K</option>
                    <option value={CONTEXT_WINDOW_1M}>1M</option>
                  </select>
                  <div style={{ fontSize: 12, color: 'var(--muted-fg)' }}>
                    {lang === 'zh'
                      ? '上下文窗口决定 AI 能记住的对话长度。256K 适合大多数场景，1M 适合超长对话。切换到更小窗口时，如果当前对话已超出，系统会自动压缩历史。'
                      : 'Context window determines how much conversation the AI can remember. 256K suits most scenarios, 1M for very long conversations. When switching to a smaller window, if the current conversation exceeds it, the system will auto-compress history.'}
                  </div>
                </div>

                {/* 工具 API Key 配置 */}
                <div className="settings-section tool-keys-section" style={{ marginTop: 26 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('toolApiKeys')}</h3>
                  <div className="tool-keys-grid">
                    {TOOL_ENTRIES.map((tool) => (
                      <div key={tool.id} className="tool-key-card">
                        <div className="tool-key-header">
                          <span className="tool-key-icon">{tool.icon}</span>
                          <span className="tool-key-label">{tool.label}</span>
                        </div>
                        {tool.id === 'web_search' ? (
                          <>
                            <div style={{ marginBottom: 10 }}>
                              <div className="field-label">API Key</div>
                              <div className="api-key-row">
                                <input
                                  className="form-input"
                                  type={showWebSearchKey ? "text" : "password"}
                                  placeholder="tvly-..."
                                  value={webSearchConfig.apiKey}
                                  onChange={(e) => setWebSearchKey(e.target.value)}
                                  onBlur={() => triggerSaved('webSearchApiKey')}
                                  style={{ flex: 1 }}
                                />
                                <button
                                  className="verify-btn"
                                  onClick={toggleShowWebSearchKey}
                                  title={showWebSearchKey ? t('hide') : t('show')}
                                  style={{ minWidth: 36, padding: '0 8px', fontSize: 16 }}
                                >
                                  {showWebSearchKey ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg> : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>}
                                </button>
                              </div>
                              {savedField === 'webSearchApiKey' && <span className="saved-feedback">{savedLabel}</span>}
                            </div>
                            <div>
                              <div className="field-label">{t('baseUrl')} <span style={{ fontSize: 11, color: 'var(--muted-fg)' }}>({lang === 'zh' ? '可选，用于 Tavily 代理或兼容端点' : 'optional, for Tavily proxy/compatible endpoint'})</span></div>
                              <input
                                className="form-input"
                                placeholder="https://api.tavily.com"
                                value={webSearchConfig.baseUrl}
                                onChange={(e) => setWebSearchBaseUrl(e.target.value)}
                                onBlur={() => triggerSaved('webSearchBaseUrl')}
                              />
                              {savedField === 'webSearchBaseUrl' && <span className="saved-feedback">{savedLabel}</span>}
                            </div>
                          </>
                        ) : (
                          <>
                            <div style={{ marginBottom: 10 }}>
                              <div className="field-label">API Key</div>
                              <div className="api-key-row">
                                <input
                                  className="form-input"
                                  type={showImageGenerationKey ? "text" : "password"}
                                  placeholder="sk-..."
                                  value={imageGenerationConfig.apiKey}
                                  onChange={(e) => setImageGenerationKey(e.target.value)}
                                  onBlur={() => triggerSaved('imageGenerationApiKey')}
                                  style={{ flex: 1 }}
                                />
                                <button
                                  className="verify-btn"
                                  onClick={toggleShowImageGenerationKey}
                                  title={showImageGenerationKey ? t('hide') : t('show')}
                                  style={{ minWidth: 36, padding: '0 8px', fontSize: 16 }}
                                >
                                  {showImageGenerationKey ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg> : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>}
                                </button>
                              </div>
                              {savedField === 'imageGenerationApiKey' && <span className="saved-feedback">{savedLabel}</span>}
                            </div>
                            <div style={{ marginBottom: 10 }}>
                              <div className="field-label">{t('baseUrl')}</div>
                              <input
                                className="form-input"
                                placeholder=""
                                value={imageGenerationConfig.baseUrl}
                                onChange={(e) => setImageGenerationBaseUrl(e.target.value)}
                                onBlur={() => triggerSaved('imageGenerationBaseUrl')}
                              />
                              {savedField === 'imageGenerationBaseUrl' && <span className="saved-feedback">{savedLabel}</span>}
                            </div>
                            <div>
                              <div className="field-label">{lang === 'zh' ? '模型' : 'Model'}</div>
                              <input
                                className="form-input"
                                placeholder=""
                                value={imageGenerationConfig.model}
                                onChange={(e) => setImageGenerationModel(e.target.value)}
                                onBlur={() => triggerSaved('imageGenerationModel')}
                              />
                              {savedField === 'imageGenerationModel' && <span className="saved-feedback">{savedLabel}</span>}
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {tab === 'rag' && (
              <div className="settings-section">
                <h3>{t('ragParams')}</h3>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('temperature')}: {temperature}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('tempDesc')}</p><div className="range-wrap"><span className="range-label">0</span><input type="range" min="0" max="1" step="0.05" value={temperature} onChange={(e) => updateSetting('temperature', parseFloat(e.target.value))} onPointerUp={() => triggerSaved('temperature')} onKeyUp={() => triggerSaved('temperature')} className="range-slider" /><span className="range-label">1</span></div>{savedField === 'temperature' && <span className="saved-feedback">{savedLabel}</span>}</div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('topK')}: {topK}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('topKDesc')}</p><div className="range-wrap"><span className="range-label">1</span><input type="range" min="1" max="20" step="1" value={topK} onChange={(e) => updateSetting('topK', parseInt(e.target.value))} onPointerUp={() => triggerSaved('topK')} onKeyUp={() => triggerSaved('topK')} className="range-slider" /><span className="range-label">20</span></div>{savedField === 'topK' && <span className="saved-feedback">{savedLabel}</span>}</div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('maxTokens')}: {maxTokens}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('maxTokensDesc')}</p><div className="range-wrap"><span className="range-label">512</span><input type="range" min="512" max="8192" step="256" value={maxTokens} onChange={(e) => updateSetting('maxTokens', parseInt(e.target.value))} onPointerUp={() => triggerSaved('maxTokens')} onKeyUp={() => triggerSaved('maxTokens')} className="range-slider" /><span className="range-label">8192</span></div>{savedField === 'maxTokens' && <span className="saved-feedback">{savedLabel}</span>}</div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('relevanceThreshold')}: {relevanceThreshold === 0 ? (lang === 'zh' ? '关闭' : 'OFF') : `${relevanceThreshold}%`}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('relevanceThresholdDesc')}</p><div className="range-wrap"><span className="range-label">0</span><input type="range" min="0" max="100" step="5" value={relevanceThreshold} onChange={(e) => updateSetting('relevanceThreshold', parseInt(e.target.value))} onPointerUp={() => triggerSaved('relevanceThreshold')} onKeyUp={() => triggerSaved('relevanceThreshold')} className="range-slider" /><span className="range-label">100</span></div>{savedField === 'relevanceThreshold' && <span className="saved-feedback">{savedLabel}</span>}</div>
                <div style={{ borderRadius:6,border:'1px solid var(--border)',background:'var(--thinking-bg)',padding:'12px 16px',marginBottom:24 }}><p style={{ fontSize:12,color:'var(--muted-fg)' }}>{t('currentConfig')}: Top-{topK}, Temperature {temperature}, {maxTokens.toLocaleString()} tokens, {t('relevanceThreshold')} {relevanceThreshold === 0 ? (lang === 'zh' ? '关闭' : 'OFF') : `${relevanceThreshold}%`}</p></div>

                <RagSettingsPanel />
              </div>
            )}

            {tab === 'memory' && (
              <div className="settings-section">
                <h3>{t('memory')}</h3>
                <div style={{ marginBottom: 24 }}>
                  <label htmlFor="systemPrompt" className="field-label">{t('systemPrompt')}</label>
                  <p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('systemPromptDesc')}</p>
                  <textarea id="systemPrompt" className="settings-textarea" rows={5} value={systemPrompt} onChange={(e) => updateSetting('systemPrompt', e.target.value)} onBlur={() => triggerSaved('systemPrompt')} />
                  <div className="char-count">{systemPrompt.length} {t('chars')} · ~{Math.ceil(systemPrompt.length / 4)} tokens</div>
                  {savedField === 'systemPrompt' && <span className="saved-feedback">{savedLabel}</span>}
                </div>
                <div style={{ marginBottom: 24 }}>
                  <label htmlFor="longTermMemory" className="field-label">{t('longTermMemory')}</label>
                  <p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('ltmDesc')}</p>
                  <textarea id="longTermMemory" className="settings-textarea" rows={5} value={longTermMemory} onChange={(e) => updateSetting('longTermMemory', e.target.value)} onBlur={() => triggerSaved('longTermMemory')} />
                  <div className="char-count">{longTermMemory.length} {t('chars')} · ~{Math.ceil(longTermMemory.length / 4)} tokens</div>
                  {savedField === 'longTermMemory' && <span className="saved-feedback">{savedLabel}</span>}
                </div>
              </div>
            )}

            {tab === 'appearance' && (
              <div className="settings-section">
                <h3>{t('appearance')}</h3>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('themeMode')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('themeModeDesc')}</p><div className="appearance-group"><button className={`appearance-btn ${themeMode === 'light' ? 'active' : ''}`} onClick={() => setThemeMode('light')}>{t('light')}</button><button className={`appearance-btn ${themeMode === 'dark' ? 'active' : ''}`} onClick={() => setThemeMode('dark')}>{t('dark')}</button><button className={`appearance-btn ${themeMode === 'auto' ? 'active' : ''}`} onClick={() => setThemeMode('auto')}>{t('followSystem')}</button></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('langLabel')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('langDesc')}</p><div className="appearance-group"><button className={`appearance-btn ${lang === 'zh' ? 'active' : ''}`} onClick={() => setLang('zh')}>🇨🇳 中文</button><button className={`appearance-btn ${lang === 'en' ? 'active' : ''}`} onClick={() => setLang('en')}>🇺🇸 English</button></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('chatFontSize')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('chatFontSizeDesc')} ({chatFontSize}px)</p><div className="font-size-wrap"><span style={{ fontSize:12,color:'var(--muted-fg)' }}>A</span><input type="range" min="12" max="20" step="1" value={chatFontSize} onChange={(e) => setChatFontSize(parseInt(e.target.value))} onPointerUp={() => triggerSaved('chatFontSize')} onKeyUp={() => triggerSaved('chatFontSize')} /><span style={{ fontSize:16,color:'var(--muted-fg)' }}>A</span><span className="font-size-label">{chatFontSize}px</span></div>{savedField === 'chatFontSize' && <span className="saved-feedback">{savedLabel}</span>}</div>
              </div>
            )}

            {tab === 'usage' && (
              (needsApiKey || isApiKeyMissing(providers)) ? (
                <div className="settings-section">
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('tokenUsage')}</h3>
                  <div style={{ textAlign: "center", padding: 40, color: "var(--muted-fg)", fontSize: 13 }}>
                    {lang === 'zh' ? '配置 API Key 后查看用量' : 'Configure an API Key to view usage'}
                  </div>
                </div>
              ) : <TokenUsagePanel />
            )}

            {tab === 'logs' && (
              <div className="settings-section">
                <h3>{t('logs')}</h3>
                <div className="log-filter-bar">{['error','warn','info','ok','debug'].map((lv) => <button key={lv} className={`log-filter-btn log-filter-${lv}${filter.levels[lv] ? ' active' : ''}`} onClick={() => setFilter({ levels: { ...filter.levels, [lv]: !filter.levels[lv] } })}>{lv.toUpperCase()}</button>)}</div>
                <div style={{ marginTop: 10, marginBottom: 10 }}>
                  <div className="field-label" style={{ marginBottom: 4 }}>{t('timeRange')}</div>
                  <select className="form-select" value={filter.timeRange} onChange={(e) => setFilter({ timeRange: parseInt(e.target.value) })}>
                    {TIME_RANGE_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </div>
                <div className="logs-toolbar"><div></div><div className="logs-actions"><button className="verify-btn" onClick={() => setLogRefreshKey(k => k + 1)}>{t('refreshLogs')}</button><button className="verify-btn" onClick={clear}>{t('clearLogs')}</button><button className="verify-btn" onClick={() => { const text = filteredBuffer.map((e) => `[${e.level.toUpperCase()}] [${e.tag}] ${e.msg}`).join('\n'); navigator.clipboard?.writeText(text).catch(() => { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); }); }}>{t('copyLogs')}</button></div></div>
                <div className="logs-box">{filteredBuffer.length ? filteredBuffer.map((e, idx) => <div className={`log-line log-${e.level}`} key={idx}><span className="log-ts">{new Date(e.ts).toLocaleTimeString('zh-CN', { hour12:false })}</span> <span className="log-tag">[{e.tag}]</span> {e.msg}</div>) : <div className="log-empty"><EmptyState size="sm" title={t('noLogs')} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9h10M7 13h6" strokeLinecap="round" /></svg>} /></div>}</div>
              </div>
            )}

            {tab === 'audit' && (
              <AuditLogPanel />
            )}

            {tab === 'mcp' && (
              <div className="settings-section">
                <h3>{t('mcpService')}</h3>
                <p style={{ fontSize:13,color:'var(--muted-fg)',marginBottom:12 }}>{t('mcpDesc')}</p>
                {mcpServers.map((srv) => (
                  <div className="mcp-card" key={srv.id}>
                    <div className="mcp-row">
                      <div className="mcp-left">
                        <span className={`mcp-dot ${srv.status}`}></span>
                        <span className="mcp-name">{srv.name}</span>
                        <span className="mcp-badge">{srv.tools} tools</span>
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button
                          className={`verify-btn ${srv.status === 'running' ? 'danger' : 'primary'}`}
                          onClick={async () => {
                            setMcpLoading(srv.id);
                            try {
                              if (srv.status === 'running') { await stopMCPServer(srv.id); }
                              else { await startMCPServer(srv.id); }
                            } finally { setMcpLoading(null); }
                          }}
                          disabled={mcpLoading === srv.id}
                        >
                          {mcpLoading === srv.id ? '...' : srv.status === 'running' ? t('stopServer') : t('start')}
                        </button>
                        <button
                          className="verify-btn danger"
                          onClick={() => removeMCPServer(srv.id)}
                          title={t('delete')}
                          style={{ minWidth: 36, padding: '0 8px', fontSize: 14 }}
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                    <div className="mcp-command">{srv.command}</div>
                  </div>
                ))}
                {showMcpForm ? (
                  <div style={{ marginTop: 12, padding: '16px', border: '1px solid var(--border)', borderRadius: 8, background: 'var(--thinking-bg)' }}>
                    <div className="field-label">{t('nameLabel')}</div>
                    <input className="form-input" value={mcpFormName} onChange={(e) => setMcpFormName(e.target.value)} placeholder={t('serverNamePlaceholder')} />
                    <div className="field-label" style={{ marginTop: 8 }}>{t('commandLabel')}</div>
                    <input className="form-input" value={mcpFormCommand} onChange={(e) => setMcpFormCommand(e.target.value)} placeholder="npx @modelcontextprotocol/server-xxx" />
                    <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                      <button className="verify-btn primary" onClick={handleAddMcpServer} disabled={!mcpFormName.trim() || !mcpFormCommand.trim()}>{t('add')}</button>
                      <button className="verify-btn" onClick={() => { setShowMcpForm(false); setMcpFormName(""); setMcpFormCommand(""); }}>{t('cancel')}</button>
                    </div>
                  </div>
                ) : (
                  <div className="mcp-add" style={{ cursor: 'pointer' }} onClick={() => setShowMcpForm(true)}>+ {t('addMcpServer')}</div>
                )}
              </div>
            )}

            {tab === 'skills' && (
              <div className="settings-section">
                <h3>{t('skills')}</h3>
                <p style={{ fontSize:13,color:'var(--muted-fg)',marginBottom:12 }}>{t('skillsDesc')}</p>
                {skills.length === 0 ? (
                  <EmptyState size="md" title={lang === 'zh' ? '暂无技能' : 'No skills yet'} desc={lang === 'zh' ? '请在 API 配置页添加并验证服务商后刷新' : 'Add and verify a provider on the API tab, then refresh'} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2L3 14h9l-1 8 10-12h-9z" strokeLinejoin="round" /></svg>} />
                ) : skills.map((skill) => (
                  <div key={skill.name} style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'12px 0', borderBottom:'1px solid var(--border)' }}>
                    <div>
                      <p style={{ fontSize:13,fontFamily:'var(--font-mono)',fontWeight:500 }}>{skill.name}</p>
                      <p style={{ fontSize:12,color:'var(--muted-fg)',marginTop:2 }}>{skill.desc}</p>
                    </div>
                    <button className={`mini-toggle ${skill.enabled ? 'on' : 'off'}`} onClick={() => toggleSkill(skill.name)}>
                      <span className="mini-toggle-knob"></span>
                    </button>
                  </div>
                ))}
              </div>
            )}

            {tab === 'about' && (
              <div className="settings-section">
                <h3>{t('about')}</h3>
                <div className="about-row"><span className="about-key">{t('productInfo')}</span><span className="about-val">{t('productName')}</span></div>
                <div className="about-row"><span className="about-key">{t('version')}</span><span className="about-val">{t('appVersion')}</span></div>
                <div className="about-row"><span className="about-key">{t('buildDate')}</span><span className="about-val">{t('buildDateVal')}</span></div>
                <div className="about-row"><span className="about-key">{t('license')}</span><span className="about-val">{t('licenseVal')}</span></div>
                <div className="about-row"><span className="about-key">{t('runtime')}</span><span className="about-val">{t('runtimeVal')}</span></div>
                <div style={{ display: 'flex', gap: 16, marginTop: 16 }}>
                  <span className="about-val">文档与开源仓库即将上线</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
