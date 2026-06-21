import { useState, useMemo, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAppStore } from "../../stores/useAppStore";
import { useSettingsStore, baseUrlByProvider } from "../../stores/useSettingsStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useLogStore } from "../../stores/useLogStore";
import { apiPost } from "../../api/client";
import { useI18n } from "../../i18n";
import type { ContentPart } from "../../types/session";

const TAB_IDS = ["api", "rag", "memory", "appearance", "usage", "logs", "mcp", "skills", "about"] as const;

const PROVIDERS = [
  { id: 'openai', name: 'OpenAI', color: '#16a085' },
  { id: 'anthropic', name: 'Anthropic', color: '#c4793b' },
  { id: 'deepseek', name: 'DeepSeek', color: '#2b4fc7' },
  { id: 'gemini', name: 'Google Gemini', color: '#4f7fff' },
  { id: 'xai', name: 'xAI (Grok)', color: '#111111' },
  { id: 'mistral', name: 'Mistral AI', color: '#ff7a00' },
  { id: 'groq', name: 'Groq', color: '#ff5a3d' },
  { id: 'together', name: 'Together AI', color: '#4f6dff' },
  { id: 'perplexity', name: 'Perplexity', color: '#2aa5a3' },
  { id: 'fireworks', name: 'Fireworks AI', color: '#ff4b1f' },
  { id: 'cohere', name: 'Cohere', color: '#3d5a40' },
  { id: 'glm', name: 'Zhipu GLM', color: '#4f6dff' },
  { id: 'kimi', name: 'Kimi (Moonshot)', color: '#6b4eff' },
  { id: 'qwen', name: 'Alibaba Qwen', color: '#ff7a00' },
  { id: 'baichuan', name: 'Baichuan AI', color: '#4f7fff' },
  { id: 'step', name: 'StepFun Step', color: '#8b5cf6' },
  { id: 'siliconflow', name: 'SiliconFlow', color: '#1e9ce5' },
  { id: 'doubao', name: 'Doubao (ByteDance)', color: '#ff5442' },
  { id: 'azure', name: 'Azure OpenAI', color: '#1685d8' },
  { id: 'ollama', name: 'Ollama (Local)', color: '#666666' },
];

const TOOL_ENTRIES = [
  { id: 'search_docs', label: 'Search Docs', icon: '🔍' },
  { id: 'web_search', label: 'Web Search', icon: '🌐' },
  { id: 'brave_search', label: 'Brave Search', icon: '🦁' },
  { id: 'wolfram', label: 'Wolfram', icon: '🧮' },
  { id: 'weather', label: 'Weather', icon: '🌤️' },
  { id: 'datasheet', label: 'Datasheet', icon: '📋' },
  { id: 'code_executor', label: 'Code Executor', icon: '💻' },
  { id: 'serial_bridge', label: 'Serial Bridge', icon: '🔌' },
];

const FALLBACK_MODEL_OPTIONS = [
  { value: "gpt-4o", label: "gpt-4o" },
  { value: "llama3.3", label: "llama3.3" },
  { value: "deepseek-v3", label: "deepseek-v3" },
];

type ModelItem = { id: string; label: string; provider: string };

export function SettingsPage() {
  const { t } = useI18n();
  const providerNameMap: Record<string, string> = {
    glm: t('providerGlm'), qwen: t('providerQwen'), baichuan: t('providerBaichuan'),
    step: t('providerStep'), siliconflow: t('providerSiliconflow'), doubao: t('providerDoubao'),
    ollama: t('providerOllama'),
  };
  const getProviderName = (id: string) => providerNameMap[id] || PROVIDERS.find(p => p.id === id)?.name || id;
  const { setActiveNav, themeMode, setThemeMode, lang, setLang, chatFontSize, setChatFontSize } = useAppStore();
  const {
    activeProvider, providerKeys, showKeys, model, visionModel, imageModel,
    temperature, topK, maxTokens, systemPrompt, longTermMemory, skills,
    mcpServers, toolKeys, showToolKeys, baseUrls,
    setActiveProvider, setProviderKey, toggleShowKey,
    setModel, setVisionModel, setImageModel, setBaseUrl,
    updateSetting, toggleSkill, toggleMcpServer,
    setToolKey, toggleShowToolKey, addMcpServer,
    fetchMCPServers, startMCPServer, stopMCPServer, addMCPServer, removeMCPServer,
  } = useSettingsStore();
  const { buffer, filter, setFilter, clear, getFiltered } = useLogStore();
  const [tab, setTab] = useState<(typeof TAB_IDS)[number]>("api");
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [verifyStatus, setVerifyStatus] = useState<'idle' | 'verified' | 'failed'>('idle');
  const [fetchedModelCount, setFetchedModelCount] = useState<number>(0);
  const [showMcpForm, setShowMcpForm] = useState(false);
  const [mcpFormName, setMcpFormName] = useState("");
  const [mcpFormCommand, setMcpFormCommand] = useState("");
  const [mcpLoading, setMcpLoading] = useState<string | null>(null);
  const currentKey = providerKeys[activeProvider] || "";
  const queryClient = useQueryClient();

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

  const filteredBuffer = useMemo(() => getFiltered(), [buffer, filter.levels, filter.timeRange]);

  const resolvedBaseUrl = baseUrls[activeProvider] || baseUrlByProvider(activeProvider);

  // Fetch models for the current provider（只走后端代理，避免 CORS）
  const { data: fetchedModels, isError: modelsError } = useQuery<ModelItem[]>({
    queryKey: ["models", resolvedBaseUrl, activeProvider, currentKey],
    queryFn: async () => {
      try {
        const res = await apiPost<{ models: string[] }>("models", { base_url: resolvedBaseUrl });
        if (res.models?.length) {
          return res.models.map((modelId) => ({ id: modelId, label: modelId, provider: activeProvider }));
        }
        return [];
      } catch {
        return [];  // Graceful fallback: API failure returns empty array instead of throwing
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: 1,
    enabled: !!currentKey.trim(),
  });

  const modelOptions = useMemo(() => {
    if (fetchedModels && fetchedModels.length > 0) {
      return fetchedModels.map((m) => ({ value: m.id, label: m.label }));
    }
    return FALLBACK_MODEL_OPTIONS;
  }, [fetchedModels]);

  const handleVerify = useCallback(async () => {
    setVerifyLoading(true);
    setVerifyStatus('idle');
    setFetchedModelCount(0);
    try {
      // 只走后端代理，避免 CORS
      const res = await apiPost<{ models: string[] }>("models", { base_url: resolvedBaseUrl });
      if (res.models && res.models.length > 0) {
        setVerifyStatus('verified');
        setFetchedModelCount(res.models.length);
        // 刷新 useQuery 数据，让模型下拉框同步更新
        queryClient.invalidateQueries({ queryKey: ["models"] });
        return;
      }
      throw new Error("No models returned");
    } catch {
      setVerifyStatus('failed');
    } finally {
      setVerifyLoading(false);
    }
  }, [resolvedBaseUrl, currentKey]);

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

  return (
    <div className="settings-overlay" onClick={() => setActiveNav("chat")}>
      <div className="settings-shell" onClick={(e) => e.stopPropagation()}>
        <div className="settings-page">
          <div className="settings-header" style={{ padding: '42px 40px 18px 40px' }}>
            <h2 style={{ fontSize: 18, fontWeight: 600 }}>{t('settings')}</h2>
            <div className="settings-tabs" style={{ marginTop: 20 }}>
              {TAB_IDS.map((id) => {
                const tabLabelMap: Record<string, string> = {
                  api: t('apiConfig'), rag: t('ragParams'), memory: t('memory'),
                  appearance: t('appearance'), usage: t('usage'), logs: t('logs'),
                  mcp: t('mcpService'), skills: t('skills'), about: t('about'),
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
                <div className="settings-section" style={{ marginBottom: 26 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('provider')}</h3>
                  <div className="provider-grid">
                    {PROVIDERS.map((p) => (
                      <button key={p.id} className={`provider-card${activeProvider === p.id ? ' active' : ''}`} onClick={() => { setActiveProvider(p.id); setVerifyStatus('idle'); setFetchedModelCount(0); }}>
                        <div className="provider-dot" style={{ background: p.color }} />
                        <span className="provider-name">{p.name}</span>
                        {providerKeys[p.id] ? <span className="provider-check">✓</span> : null}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="settings-section provider-detail-card">
                  <div className="provider-detail-title-row">
                    <div className="provider-detail-dot" style={{ background: PROVIDERS.find((p) => p.id === activeProvider)?.color || '#666' }} />
                    <span className="provider-detail-title">{PROVIDERS.find((p) => p.id === activeProvider)?.name || activeProvider}</span>
                  </div>
                  <div className="field-label">Base URL</div>
                  <div className="api-key-row">
                    <input
                      className="form-input"
                      value={baseUrls[activeProvider] || ""}
                      onChange={(e) => setBaseUrl(activeProvider, e.target.value)}
                      placeholder={baseUrlByProvider(activeProvider)}
                      style={{ flex: 1 }}
                    />
                    {baseUrls[activeProvider] && (
                      <button
                        className="verify-btn"
                        onClick={() => setBaseUrl(activeProvider, "")}
                        title={t('reset')}
                        style={{ minWidth: 36, padding: '0 8px', fontSize: 14 }}
                      >
                        ✕
                      </button>
                    )}
                  </div>
                  <div className="field-label" style={{ marginTop: 12 }}>API Key</div>
                  <div className="api-key-row">
                    <input
                      className="form-input"
                      type={showKeys[activeProvider] ? "text" : "password"}
                      placeholder={`${getProviderName(activeProvider)} API Key...`}
                      value={currentKey}
                      onChange={(e) => { setProviderKey(activeProvider, e.target.value); setVerifyStatus('idle'); setFetchedModelCount(0); }}
                    />
                    <button
                      className="verify-btn"
                      onClick={() => toggleShowKey(activeProvider)}
                      title={showKeys[activeProvider] ? t('hide') : t('show')}
                      style={{ minWidth: 36, padding: '0 8px', fontSize: 16 }}
                    >
                      {showKeys[activeProvider] ? '🙈' : '👁️'}
                    </button>
                    <button className="verify-btn" onClick={handleVerify} disabled={verifyLoading || !currentKey.trim()}>
                      {verifyLoading ? t('verifying') : t('verify')}
                    </button>
                  </div>
                  {verifyStatus === 'verified' && (
                    <div style={{ marginTop: 6, fontSize: 12, color: '#16a085' }}>
                      {t('verifiedKey')}{fetchedModelCount > 0 ? ` · ${fetchedModelCount} ${t('model')}` : ''}
                    </div>
                  )}
                  {verifyStatus === 'failed' && <div style={{ marginTop: 6, fontSize: 12, color: '#e74c3c' }}>{t('invalidKey')}</div>}
                  <div className="field-label" style={{ marginTop: 12 }}>{t('defaultModel')}</div>
                  <select className="form-select" value={model} onChange={(e) => setModel(e.target.value)}>
                    {modelOptions.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                  <div className="field-label" style={{ marginTop: 12 }}>{t('visionModel')}</div>
                  <select className="form-select" value={visionModel} onChange={(e) => setVisionModel(e.target.value)}>
                    <option value="auto">auto ({t('followChatModel')})</option>
                    {modelOptions.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                  <div className="field-label" style={{ marginTop: 12 }}>{t('imageModel')}</div>
                  <select className="form-select" value={imageModel} onChange={(e) => setImageModel(e.target.value)}>
                    <option value="auto">auto ({t('followChatModel')})</option>
                    {modelOptions.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                </div>

                <div className="settings-section tool-keys-section" style={{ marginTop: 26 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('toolApiKeys')}</h3>
                  <div className="tool-keys-grid">
                    {TOOL_ENTRIES.map((tool) => (
                      <div key={tool.id} className="tool-key-card">
                        <div className="tool-key-header">
                          <span className="tool-key-icon">{tool.icon}</span>
                          <span className="tool-key-label">{tool.label}</span>
                        </div>
                        <div className="api-key-row">
                          <input
                            className="form-input"
                            type={showToolKeys[tool.id] ? "text" : "password"}
                            placeholder={`${tool.label} API Key...`}
                            value={toolKeys[tool.id] || ""}
                            onChange={(e) => setToolKey(tool.id, e.target.value)}
                            style={{ flex: 1 }}
                          />
                          <button
                            className="verify-btn"
                            onClick={() => toggleShowToolKey(tool.id)}
                            title={showToolKeys[tool.id] ? t('hide') : t('show')}
                            style={{ minWidth: 36, padding: '0 8px', fontSize: 16 }}
                          >
                            {showToolKeys[tool.id] ? '🙈' : '👁️'}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {tab === 'rag' && (
              <div className="settings-section">
                <h3>{t('ragParams')}</h3>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('temperature')}: {temperature}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('tempDesc')}</p><div className="range-wrap"><span className="range-label">0</span><input type="range" min="0" max="1" step="0.05" value={temperature} onChange={(e) => updateSetting('temperature', parseFloat(e.target.value))} className="range-slider" /><span className="range-label">1</span></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('topK')}: {topK}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('topKDesc')}</p><div className="range-wrap"><span className="range-label">1</span><input type="range" min="1" max="20" step="1" value={topK} onChange={(e) => updateSetting('topK', parseInt(e.target.value))} className="range-slider" /><span className="range-label">20</span></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('maxTokens')}: {maxTokens}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('maxTokensDesc')}</p><div className="range-wrap"><span className="range-label">512</span><input type="range" min="512" max="8192" step="256" value={maxTokens} onChange={(e) => updateSetting('maxTokens', parseInt(e.target.value))} className="range-slider" /><span className="range-label">8192</span></div></div>
                <div style={{ borderRadius:6,border:'1px solid var(--border)',background:'var(--thinking-bg)',padding:'12px 16px' }}><p style={{ fontSize:12,color:'var(--muted-fg)' }}>{t('currentConfig')}: Top-{topK}, Temperature {temperature}, {maxTokens.toLocaleString()} tokens</p></div>
              </div>
            )}

            {tab === 'memory' && (
              <div className="settings-section">
                <h3>{t('memory')}</h3>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('systemPrompt')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('systemPromptDesc')}</p><textarea className="settings-textarea" rows={5} value={systemPrompt} onChange={(e) => updateSetting('systemPrompt', e.target.value)} /><div className="char-count">{systemPrompt.length} {t('chars')} · ~{Math.ceil(systemPrompt.length / 4)} tokens</div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('longTermMemory')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('ltmDesc')}</p><textarea className="settings-textarea" rows={5} value={longTermMemory} onChange={(e) => updateSetting('longTermMemory', e.target.value)} /><div className="char-count">{longTermMemory.length} {t('chars')} · ~{Math.ceil(longTermMemory.length / 4)} tokens</div></div>
              </div>
            )}

            {tab === 'appearance' && (
              <div className="settings-section">
                <h3>{t('appearance')}</h3>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('themeMode')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('themeModeDesc')}</p><div className="appearance-group"><button className={`appearance-btn ${themeMode === 'light' ? 'active' : ''}`} onClick={() => setThemeMode('light')}>{t('light')}</button><button className={`appearance-btn ${themeMode === 'dark' ? 'active' : ''}`} onClick={() => setThemeMode('dark')}>{t('dark')}</button><button className={`appearance-btn ${themeMode === 'auto' ? 'active' : ''}`} onClick={() => setThemeMode('auto')}>{t('followSystem')}</button></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('langLabel')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('langDesc')}</p><div className="appearance-group"><button className={`appearance-btn ${lang === 'zh' ? 'active' : ''}`} onClick={() => setLang('zh')}>🇨🇳 中文</button><button className={`appearance-btn ${lang === 'en' ? 'active' : ''}`} onClick={() => setLang('en')}>🇺🇸 English</button></div></div>
                <div style={{ marginBottom: 24 }}><div className="field-label">{t('chatFontSize')}</div><p style={{ fontSize:12,color:'var(--muted-fg)',marginBottom:8 }}>{t('chatFontSizeDesc')} ({chatFontSize}px)</p><div className="font-size-wrap"><span style={{ fontSize:12,color:'var(--muted-fg)' }}>A</span><input type="range" min="12" max="20" step="1" value={chatFontSize} onChange={(e) => setChatFontSize(parseInt(e.target.value))} /><span style={{ fontSize:16,color:'var(--muted-fg)' }}>A</span><span className="font-size-label">{chatFontSize}px</span></div></div>
              </div>
            )}

            {tab === 'usage' && (
              <UsageTab />
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
                <div className="logs-toolbar"><div></div><div className="logs-actions"><button className="verify-btn" onClick={() => {/* refresh */}}>{t('refreshLogs')}</button><button className="verify-btn" onClick={clear}>{t('clearLogs')}</button><button className="verify-btn" onClick={() => { const text = filteredBuffer.map((e) => `[${e.level.toUpperCase()}] [${e.tag}] ${e.msg}`).join('\n'); navigator.clipboard?.writeText(text).catch(() => { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); }); }}>{t('copyLogs')}</button></div></div>
                <div className="logs-box">{filteredBuffer.length ? filteredBuffer.map((e, idx) => <div className={`log-line log-${e.level}`} key={idx}><span className="log-ts">{new Date(e.ts).toLocaleTimeString('zh-CN', { hour12:false })}</span> <span className="log-tag">[{e.tag}]</span> {e.msg}</div>) : <div className="log-empty">{t('noLogs')}</div>}</div>
              </div>
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
                          onClick={() => srv.status === 'running' ? stopMCPServer(srv.id) : startMCPServer(srv.id)}
                          disabled={mcpLoading === srv.id}
                        >
                          {mcpLoading === srv.id ? '...' : srv.status === 'running' ? t('stopServer') : t('start')}
                        </button>
                        <button
                          className="verify-btn danger"
                          onClick={() => removeMCPServer(srv.id)}
                          title={t('delete') || '删除'}
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
              <div className="settings-section"><h3>{t('skills')}</h3><p style={{ fontSize:13,color:'var(--muted-fg)',marginBottom:12 }}>{t('skillsDesc')}</p>{skills.map((skill) => <div key={skill.name} style={{ display:'flex', alignItems:'center', justifyContent:'space-between', padding:'12px 0', borderBottom:'1px solid var(--border)' }}><div><p style={{ fontSize:13,fontFamily:'var(--font-mono)',fontWeight:500 }}>{skill.name}</p><p style={{ fontSize:12,color:'var(--muted-fg)',marginTop:2 }}>{skill.desc}</p></div><button className={`mini-toggle ${skill.enabled ? 'on' : 'off'}`} onClick={() => toggleSkill(skill.name)}><span className="mini-toggle-knob"></span></button></div>)}</div>
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
                  <a className="about-link" href="https://docs.example.com/hardware-rag-agent" target="_blank" rel="noopener noreferrer">{t('viewDocs')}</a>
                  <a className="about-link" href="https://github.com/example/hardware-rag-agent" target="_blank" rel="noopener noreferrer">GitHub</a>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 粗略估算 token 数 */
function estimateTokens(text: string): number {
  const cnChars = [...text].filter(c => '\u4e00' <= c && c <= '\u9fff').length;
  const otherChars = text.length - cnChars;
  return Math.ceil(cnChars * 1.5 + otherChars * 0.5);
}

/** Extract plain text from Message content (string | ContentPart[]) */
function contentToText(content: string | ContentPart[]): string {
  if (typeof content === 'string') return content;
  return content.map(p => 'text' in p ? p.text : '').join('');
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toString();
}

/** Usage 标签页：真实数据 */
function UsageTab() {
  const { t } = useI18n();
  const { sessionMessages } = useChatStore();
  const { sessions } = useSessionStore();
  const { model } = useSettingsStore();

  // 汇总所有会话的消息
  const allMessages = useMemo(() => {
    return Object.values(sessionMessages).flat();
  }, [sessionMessages]);

  const totalSessions = sessions.length;
  const totalMessages = allMessages.filter(m => m.role === 'user' || (m.role === 'assistant' && m.content)).length;

  // 真实 usage 数据
  let realPrompt = 0, realCompletion = 0, realTotal = 0;
  let hasRealUsage = false;
  allMessages.forEach(m => {
    if (m.role === 'assistant' && m.usage) {
      realPrompt += m.usage.promptTokens;
      realCompletion += m.usage.completionTokens;
      realTotal += m.usage.totalTokens;
      hasRealUsage = true;
    }
  });

  // 估算 fallback
  const estInput = allMessages.filter(m => m.role === 'user').reduce((a, m) => a + estimateTokens(contentToText(m.content)), 0);
  const estOutput = allMessages.filter(m => m.role === 'assistant').reduce((a, m) => a + estimateTokens(contentToText(m.content)), 0);

  const totalTokens = hasRealUsage ? realTotal : (estInput + estOutput);
  const todayStart = new Date(); todayStart.setHours(0, 0, 0, 0);
  const todayMessages = allMessages.filter(m => m.timestamp >= todayStart.getTime());
  const dailyTokens = todayMessages.filter(m => m.role === 'assistant').reduce((a, m) =>
    a + (m.usage?.totalTokens || estimateTokens(contentToText(m.content))), 0);

  // 模型分布
  const modelDist = useMemo(() => {
    const map: Record<string, number> = {};
    allMessages.forEach(m => {
      if (m.role === 'assistant') {
        const tokens = m.usage?.totalTokens || estimateTokens(contentToText(m.content));
        map[model] = (map[model] || 0) + tokens;
      }
    });
    const maxTokens = Math.max(...Object.values(map), 1);
    return Object.entries(map).map(([m, tokens]) => ({ model: m, tokens, pct: Math.round(tokens / maxTokens * 100) }));
  }, [allMessages, model]);

  return (
    <div className="settings-section">
      <h3>{t('usageAnalysis')}</h3>
      <div className="stats-grid">
        <div className="stat-card"><div className="stat-label">{t('totalSessions')}</div><div className="stat-value">{totalSessions}</div></div>
        <div className="stat-card"><div className="stat-label">{t('totalMessages')}</div><div className="stat-value">{totalMessages}</div></div>
        <div className="stat-card"><div className="stat-label">{t('totalTokens')}</div><div className="stat-value">{formatTokenCount(totalTokens)}{hasRealUsage ? ' ✓' : ' ~'}</div></div>
        <div className="stat-card"><div className="stat-label">{t('dailyTokens')}</div><div className="stat-value">{formatTokenCount(dailyTokens)}</div></div>
      </div>
      {modelDist.length > 0 && (
        <div className="chart-card">
          <div className="chart-title">{t('modelDistribution')}</div>
          {modelDist.map((row) => (
            <div className="model-bar-row" key={row.model}>
              <span className="model-bar-name">{row.model}</span>
              <div className="model-bar-track"><div className="model-bar-fill" style={{ width: `${row.pct}%` }}></div></div>
              <span className="model-bar-tokens">{formatTokenCount(row.tokens)}</span>
            </div>
          ))}
        </div>
      )}
      {!hasRealUsage && (
        <div style={{ fontSize: 11, color: 'var(--muted-fg)', fontStyle: 'italic', marginTop: 8 }}>
          ~ 为估算值，发送消息后 API 返回 usage 数据将显示真实值
        </div>
      )}
    </div>
  );
}
