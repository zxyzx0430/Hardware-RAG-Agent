import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import type { ReactNode } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore, CONTEXT_WINDOW_256K, CONTEXT_WINDOW_1M } from "../../stores/useSessionStore";
import { useLogStore } from "../../stores/useLogStore";
import { useModalStore } from "../../stores/useModalStore";
import { useI18n } from "../../i18n";
import { RagSettingsPanel } from "./RagSettingsPanel";
import { MCPManager } from "./MCPManager";
import { LongTermMemoryEditor } from "./LongTermMemoryEditor";
import { SkillsManager } from "./SkillsManager";
import { TokenUsagePanel } from "./TokenUsagePanel";
import { AuditLogPanel } from "./AuditLogPanel";
import { MemoryHubPanel } from "../memory/MemoryHubPanel";
import { EmptyState } from "../shared/EmptyState";
import "../../styles/settings-layout.css";

const TAB_IDS = ["api", "rag", "memory", "memory-hub", "appearance", "usage", "logs", "audit", "mcp", "tools", "skills", "about"] as const;

type SettingsTabId = (typeof TAB_IDS)[number];

interface SettingsNavItem {
  id: SettingsTabId;
  labelKey?: string;
  label?: { zh: string; en: string };
  desc: { zh: string; en: string };
  icon: ReactNode;
}

interface SettingsNavGroup {
  key: string;
  title: { zh: string; en: string };
  items: SettingsNavItem[];
}

function NavIcon({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

const SETTINGS_NAV_GROUPS: SettingsNavGroup[] = [
  {
    key: "general",
    title: { zh: "通用", en: "General" },
    items: [
      {
        id: "appearance",
        labelKey: "appearance",
        desc: { zh: "主题、语言与聊天字体大小", en: "Theme, language and chat font size" },
        icon: <NavIcon><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" /></NavIcon>,
      },
      {
        id: "api",
        labelKey: "apiConfig",
        desc: { zh: "服务商、模型分配与 API Key", en: "Providers, model routing and API keys" },
        icon: <NavIcon><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" /></NavIcon>,
      },
      {
        id: "rag",
        labelKey: "ragParams",
        desc: { zh: "温度、检索数量与知识库范围", en: "Temperature, retrieval size and knowledge base scope" },
        icon: <NavIcon><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" /><path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" /></NavIcon>,
      },
    ],
  },
  {
    key: "assistant",
    title: { zh: "助手", en: "Assistant" },
    items: [
      {
        id: "memory",
        labelKey: "memory",
        desc: { zh: "系统提示词与长期记忆", en: "System prompt and long-term memory" },
        icon: <NavIcon><path d="M12 5a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V8a3 3 0 0 0-3-3z" /><path d="M9 8H6a2 2 0 0 0 0 4h3M15 8h3a2 2 0 0 1 0 4h-3M9 16H7a2 2 0 0 0 0 4h2M15 16h2a2 2 0 0 1 0 4h-2" /></NavIcon>,
      },
      {
        id: "memory-hub",
        labelKey: "memoryHub",
        desc: { zh: "记忆条目、硬件档案与项目记忆", en: "Memory entries, hardware profiles and project memory" },
        icon: <NavIcon><rect x="5" y="5" width="14" height="14" rx="2" /><rect x="9" y="9" width="6" height="6" rx="1" /><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4" /></NavIcon>,
      },
      {
        id: "tools",
        labelKey: "tools",
        desc: { zh: "Agent 当前可调用的工具开关", en: "Tools currently available to the Agent" },
        icon: <NavIcon><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.3-2.3 2.5-2.5z" /></NavIcon>,
      },
    ],
  },
  {
    key: "capability",
    title: { zh: "能力", en: "Capabilities" },
    items: [
      {
        id: "skills",
        labelKey: "skills",
        desc: { zh: "已安装的 Markdown 技能", en: "Installed Markdown skills" },
        icon: <NavIcon><path d="M10 3a2 2 0 0 1 4 0v2h3a2 2 0 0 1 2 2v3h-2a2 2 0 0 0 0 4h2v3a2 2 0 0 1-2 2h-3v-2a2 2 0 0 0-4 0v2H7a2 2 0 0 1-2-2v-3H3a2 2 0 0 1 0-4h2V7a2 2 0 0 1 2-2h3V3z" /></NavIcon>,
      },
      {
        id: "mcp",
        labelKey: "mcpService",
        desc: { zh: "Model Context Protocol 服务连接", en: "Model Context Protocol server connections" },
        icon: <NavIcon><rect x="2" y="3" width="20" height="7" rx="2" /><rect x="2" y="14" width="20" height="7" rx="2" /><path d="M6 6.5h.01M6 17.5h.01" /></NavIcon>,
      },
    ],
  },
  {
    key: "runtime",
    title: { zh: "运行", en: "Runtime" },
    items: [
      {
        id: "usage",
        labelKey: "usage",
        desc: { zh: "Token 用量、缓存与模型分布", en: "Token usage, cache and model breakdown" },
        icon: <NavIcon><line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" /></NavIcon>,
      },
      {
        id: "logs",
        labelKey: "logs",
        desc: { zh: "前端运行日志与级别过滤", en: "Frontend runtime logs and level filters" },
        icon: <NavIcon><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9h10M7 13h6" /></NavIcon>,
      },
      {
        id: "audit",
        label: { zh: "工具审计", en: "Tool Audit" },
        desc: { zh: "工具调用与权限决策记录", en: "Tool calls and permission decisions" },
        icon: <NavIcon><path d="M12 2l8 4v6c0 5-3.4 9.4-8 10-4.6-.6-8-5-8-10V6l8-4z" /><path d="M9 12l2 2 4-4" /></NavIcon>,
      },
      {
        id: "about",
        labelKey: "about",
        desc: { zh: "版本、构建与运行环境", en: "Version, build and runtime environment" },
        icon: <NavIcon><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></NavIcon>,
      },
    ],
  },
];

const NAV_ITEMS: SettingsNavItem[] = SETTINGS_NAV_GROUPS.flatMap((group) => group.items);

const TOOL_ENTRIES = [
  { id: 'web_search', label: 'Web Search', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg> },
  { id: 'image_generation', label: 'Image Generation', icon: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg> },
];

export function SettingsPage() {
  const { t } = useI18n();
  const { setActiveNav, themeMode, setThemeMode, lang, setLang, chatFontSize, setChatFontSize } = useAppStore();
  const {
    providers, chatProviderId, chatModel, imageProviderId, imageModel, visionProviderId, visionModel,
    temperature, topK, maxTokens, relevanceThreshold, retrievalSettingsStatus, retrievalSettingsStorageIssue,
    systemPrompt, longTermMemory, skills: tools,
    webSearchConfig, showWebSearchKey, imageGenerationConfig, showImageGenerationKey,
    addProvider, removeProvider, updateProvider, verifyProvider, fetchProviderModels,
    setChatModel, setImageModel, setVisionModel,
    updateSetting, saveLongTermMemory, toggleSkill,
    setupCode, setSetupCode,
    setWebSearchKey, setWebSearchBaseUrl, toggleShowWebSearchKey,
    setImageGenerationKey, setImageGenerationBaseUrl, setImageGenerationModel, toggleShowImageGenerationKey,
    fetchTools,
  } = useSettingsStore();
  const { buffer, filter, setFilter, clear, getFiltered } = useLogStore();
  const { confirmDialog } = useModalStore();
  // Context window: reactively track current session's value + setter
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setContextWindow = useSessionStore((s) => s.setContextWindow);
  const currentContextWindow = useSessionStore((s) =>
    s.sessions.find((x) => x.id === activeSessionId)?.contextWindow ?? CONTEXT_WINDOW_1M
  );
  const [tab, setTab] = useState<SettingsTabId>("api");
  const [searchQuery, setSearchQuery] = useState("");
  const contentScrollRef = useRef<HTMLDivElement | null>(null);
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
  // 工具开关来自 /api/tools；Markdown Skills 有独立接口和状态。
  useEffect(() => {
    if (tab === 'tools') {
      fetchTools();
    }
  }, [tab, fetchTools]);
  useEffect(() => {
    if (contentScrollRef.current) contentScrollRef.current.scrollTop = 0;
  }, [tab]);
  const savedLabel = lang === 'zh' ? '✓ 已保存' : '✓ Saved';
  const retrievalFeedback = (key: 'topK' | 'relevanceThreshold'): string => {
    if (retrievalSettingsStorageIssue) {
      return retrievalSettingsStatus[key] === 'synced' ? t('retrievalSyncedStorageError') : t('retrievalStorageError');
    }
    switch (retrievalSettingsStatus[key]) {
      case 'saving': return t('retrievalSaving');
      case 'synced': return t('retrievalSynced');
      case 'local_only': return t('retrievalLocalOnly');
      case 'read_error': return t('retrievalReadError');
      default: return '';
    }
  };

  // 服务商详情 + 新建表单相关状态
  const [selectedProviderId, setSelectedProviderId] = useState<string>("");
  const [newProviderName, setNewProviderName] = useState("");
  const [newProviderBaseUrl, setNewProviderBaseUrl] = useState("");
  const [newProviderApiKey, setNewProviderApiKey] = useState("");
  const [showNewProviderKey, setShowNewProviderKey] = useState(false);
  const [showProvKey, setShowProvKey] = useState(false);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [providerVerificationFeedback, setProviderVerificationFeedback] = useState<{
    providerId: string;
    result: Awaited<ReturnType<typeof verifyProvider>>;
  } | null>(null);
  const [fetchModelsLoading, setFetchModelsLoading] = useState(false);

  const selectedProvider = useMemo(
    () => providers.find((p) => p.id === selectedProviderId) || null,
    [providers, selectedProviderId]
  );
  const selectedProviderVerificationResult =
    selectedProvider && providerVerificationFeedback?.providerId === selectedProvider.id
      ? providerVerificationFeedback.result
      : null;
  const providerVerificationMessageKey = selectedProviderVerificationResult?.ok === false
    ? selectedProviderVerificationResult.kind === "local-auth"
      ? "providerVerifyLocalAuth"
      : selectedProviderVerificationResult.kind === "model-service"
        ? "providerVerifyModelService"
        : selectedProviderVerificationResult.kind === "network"
          ? "providerVerifyNetwork"
          : selectedProviderVerificationResult.kind === "timeout"
            ? "providerVerifyTimeout"
            : selectedProviderVerificationResult.kind === "empty-models"
              ? "providerVerifyEmptyModels"
              : "providerVerifyUnknown"
    : null;
  const verifiedProviders = useMemo(
    () => providers.filter((p) => p.verified),
    [providers]
  );

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
    setProviderVerificationFeedback(null);
    try {
      const result = await verifyProvider(selectedProvider.id);
      if (!result.ok) setProviderVerificationFeedback({ providerId: selectedProvider.id, result });
      else setProviderVerificationFeedback(null);
    } catch {
      setProviderVerificationFeedback({
        providerId: selectedProvider.id,
        result: { ok: false, kind: "unknown" },
      });
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
    try {
      const result = await verifyProvider(created.id);
      if (!result.ok) setProviderVerificationFeedback({ providerId: created.id, result });
      else setProviderVerificationFeedback(null);
    } catch {
      setProviderVerificationFeedback({
        providerId: created.id,
        result: { ok: false, kind: "unknown" },
      });
    }
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

  // Close settings on Esc and expose a real close button instead of a giant clickable overlay
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActiveNav("chat");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [setActiveNav]);

  const labelOf = (item: SettingsNavItem): string =>
    item.labelKey ? t(item.labelKey) : item.label ? item.label[lang] : item.id;
  const query = searchQuery.trim().toLowerCase();
  const navGroups = SETTINGS_NAV_GROUPS
    .map((group) => ({
      key: group.key,
      title: group.title[lang],
      items: group.items.filter((item) => !query || labelOf(item).toLowerCase().includes(query)),
    }))
    .filter((group) => group.items.length > 0);
  const activeItem = NAV_ITEMS.find((item) => item.id === tab) ?? NAV_ITEMS[0];
  const activeLabel = labelOf(activeItem);
  const activeDesc = activeItem.desc[lang];

  return (
    <div className="settings-overlay">
      <div className="settings-shell settings-layout">
        <aside className="settings-sidebar">
          <div className="settings-brand">
            <span className="settings-brand-mark" aria-hidden="true">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9" /></svg>
            </span>
            <div className="settings-brand-text">
              <div className="settings-brand-title">{t('settings')}</div>
              <div className="settings-brand-sub">{t('productName')}</div>
            </div>
          </div>

          <label className="settings-search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={lang === 'zh' ? '搜索设置' : 'Search settings'}
              aria-label={lang === 'zh' ? '搜索设置' : 'Search settings'}
              autoComplete="off"
            />
          </label>

          <nav className="settings-nav" aria-label={t('settings')}>
            {navGroups.map((group) => (
              <div className="settings-nav-group" key={group.key}>
                <div className="settings-nav-label">{group.title}</div>
                {group.items.map((item) => {
                  const label = labelOf(item);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`settings-nav-item${tab === item.id ? ' active' : ''}`}
                      onClick={() => setTab(item.id)}
                      title={label}
                      aria-current={tab === item.id ? 'page' : undefined}
                    >
                      {item.icon}
                      <span className="settings-nav-item-label">{label}</span>
                    </button>
                  );
                })}
              </div>
            ))}
            {navGroups.length === 0 && (
              <div className="settings-nav-empty">{lang === 'zh' ? '没有匹配的设置项' : 'No matching settings'}</div>
            )}
          </nav>
        </aside>

        <main className="settings-content">
          <header className="settings-content-head">
            <div className="settings-content-head-main">
              <h2 className="settings-content-title">{activeLabel}</h2>
              <p className="settings-content-desc">{activeDesc}</p>
            </div>
            <button
              className="settings-close-btn"
              onClick={() => setActiveNav("chat")}
              aria-label={lang === 'zh' ? '关闭设置' : 'Close settings'}
              title={lang === 'zh' ? '关闭设置' : 'Close settings'}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
            </button>
          </header>

          <div ref={contentScrollRef} className="settings-scroll settings-content-scroll">
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
                        {selectedProviderId === p.id &&
                        providerVerificationFeedback?.providerId === p.id &&
                        !providerVerificationFeedback.result.ok
                          ? <span className="provider-check pending">!</span>
                          : p.verified
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
                  <div className="field-label">{lang === 'zh' ? '首次配置确认码（仅首次）' : 'First-run setup code (first time only)'}</div>
                  <input
                    className="form-input"
                    value={setupCode ?? ''}
                    onChange={(e) => setSetupCode?.(e.target.value)}
                    placeholder={lang === 'zh' ? '后端控制台打印的一次性确认码' : 'One-time code printed by the backend console'}
                    style={{ marginBottom: 4 }}
                  />
                  <div style={{ fontSize: 11, color: 'var(--muted-fg)', marginBottom: 8 }}>
                    {lang === 'zh'
                      ? '首次启动后端时打印，也保存在 backend/data/.setup_code；已配置过服务商后不需要填写。'
                      : 'Printed when the backend first starts (also in backend/data/.setup_code); not needed after a provider is stored.'}
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
                    {providerVerificationMessageKey && (
                      <div style={{ marginTop: 6, fontSize: 12, color: "var(--danger)" }}>
                        {t(providerVerificationMessageKey)}
                      </div>
                    )}
                    {selectedProvider.verified && selectedProvider.models.length > 0 && !providerVerificationMessageKey && (
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
                    <option value={65536}>64K</option>
                    <option value={131072}>128K</option>
                    <option value={CONTEXT_WINDOW_256K}>256K</option>
                    <option value={524288}>512K</option>
                    <option value={CONTEXT_WINDOW_1M}>1M</option>
                  </select>
                  <div style={{ fontSize: 12, color: 'var(--muted-fg)' }}>
                    {lang === 'zh'
                      ? '上下文窗口决定 AI 能记住的对话长度，上限为 1M。系统提示、工具 schema、输出和对话内容都占用同一窗口；超出所选窗口时系统会自动压缩历史。'
                      : 'Context window determines how much conversation the AI can remember. The maximum is 1M. System prompts, tool schemas, output and conversation share the same window; the system compacts history when it is exceeded.'}
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
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('topK')}: {topK}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('topKDesc')}</p><div className="range-wrap"><span className="range-label">1</span><input type="range" min="1" max="20" step="1" value={topK} onChange={(e) => void updateSetting('topK', parseInt(e.target.value))} className="range-slider" /><span className="range-label">20</span></div>{retrievalFeedback('topK') && <span className="saved-feedback" role="status" aria-live="polite">{retrievalFeedback('topK')}</span>}</div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('maxTokens')}: {maxTokens}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('maxTokensDesc')}</p><div className="range-wrap"><span className="range-label">512</span><input type="range" min="512" max="8192" step="256" value={maxTokens} onChange={(e) => updateSetting('maxTokens', parseInt(e.target.value))} onPointerUp={() => triggerSaved('maxTokens')} onKeyUp={() => triggerSaved('maxTokens')} className="range-slider" /><span className="range-label">8192</span></div>{savedField === 'maxTokens' && <span className="saved-feedback">{savedLabel}</span>}</div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('relevanceThreshold')}: {relevanceThreshold === 0 ? (lang === 'zh' ? '关闭' : 'OFF') : `${relevanceThreshold}%`}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('relevanceThresholdDesc')}</p><div className="range-wrap"><span className="range-label">0</span><input type="range" min="0" max="100" step="5" value={relevanceThreshold} onChange={(e) => void updateSetting('relevanceThreshold', parseInt(e.target.value))} className="range-slider" /><span className="range-label">100</span></div>{retrievalFeedback('relevanceThreshold') && <span className="saved-feedback" role="status" aria-live="polite">{retrievalFeedback('relevanceThreshold')}</span>}</div>
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
                <LongTermMemoryEditor
                  value={longTermMemory}
                  lang={lang}
                  label={t('longTermMemory')}
                  description={t('ltmDesc')}
                  onSave={saveLongTermMemory}
                />
              </div>
            )}

            {tab === 'memory-hub' && <MemoryHubPanel />}

            {tab === 'appearance' && (
              <div className="settings-section">
                <h3>{t('appearance')}</h3>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('themeMode')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('themeModeDesc')}</p><div className="appearance-group"><button className={`appearance-btn ${themeMode === 'light' ? 'active' : ''}`} onClick={() => setThemeMode('light')}>{t('light')}</button><button className={`appearance-btn ${themeMode === 'dark' ? 'active' : ''}`} onClick={() => setThemeMode('dark')}>{t('dark')}</button><button className={`appearance-btn ${themeMode === 'auto' ? 'active' : ''}`} onClick={() => setThemeMode('auto')}>{t('followSystem')}</button></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('langLabel')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('langDesc')}</p><div className="appearance-group"><button className={`appearance-btn ${lang === 'zh' ? 'active' : ''}`} onClick={() => setLang('zh')}>🇨🇳 中文</button><button className={`appearance-btn ${lang === 'en' ? 'active' : ''}`} onClick={() => setLang('en')}>🇺🇸 English</button></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('chatFontSize')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('chatFontSizeDesc')} ({chatFontSize}px)</p><div className="font-size-wrap"><span style={{ fontSize:12,color:'var(--muted-fg)' }}>A</span><input type="range" min="12" max="20" step="1" value={chatFontSize} onChange={(e) => setChatFontSize(parseInt(e.target.value))} onPointerUp={() => triggerSaved('chatFontSize')} onKeyUp={() => triggerSaved('chatFontSize')} /><span style={{ fontSize:16,color:'var(--muted-fg)' }}>A</span><span className="font-size-label">{chatFontSize}px</span></div>{savedField === 'chatFontSize' && <span className="saved-feedback">{savedLabel}</span>}</div>
              </div>
            )}

            {tab === 'usage' && (
              providers.length === 0 ? (
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

            {tab === 'mcp' && <MCPManager lang={lang} />}

            {tab === 'tools' && (
              <div className="settings-section">
                <h3>{t('tools')}</h3>
                <p style={{ fontSize:13,color:'var(--muted-fg)',marginBottom:12 }}>{lang === 'zh' ? '启用或停用 Agent 当前可调用的工具。' : 'Enable or disable tools currently available to the Agent.'}</p>
                {tools.length === 0 ? (
                  <EmptyState size="md" title={lang === 'zh' ? '暂无可用工具' : 'No tools available'} desc={lang === 'zh' ? '后端工具列表为空或暂时无法读取。' : 'The backend tool list is empty or temporarily unavailable.'} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M13 2L3 14h9l-1 8 10-12h-9z" strokeLinejoin="round" /></svg>} />
                ) : tools.map((tool) => (
                  <div key={tool.name} style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'12px 0', borderBottom:'1px solid var(--border)' }}>
                    <div>
                      <p style={{ fontSize:13,fontFamily:'var(--font-mono)',fontWeight:500 }}>{tool.name}</p>
                      <p style={{ fontSize:12,color:'var(--muted-fg)',marginTop:2 }}>{tool.desc}</p>
                    </div>
                    <button className={`mini-toggle ${tool.enabled ? 'on' : 'off'}`} aria-label={`${tool.enabled ? (lang === 'zh' ? '停用工具' : 'Disable tool') : (lang === 'zh' ? '启用工具' : 'Enable tool')}: ${tool.name}`} aria-pressed={tool.enabled} onClick={() => toggleSkill(tool.name)}>
                      <span className="mini-toggle-knob"></span>
                    </button>
                  </div>
                ))}
              </div>
            )}

            {tab === 'skills' && <SkillsManager lang={lang} />}

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
        </main>
      </div>
    </div>
  );
}
