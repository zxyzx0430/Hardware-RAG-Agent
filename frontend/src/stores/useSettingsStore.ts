import { create } from "zustand";
import { loadFromStorage, saveToStorage } from "../utils/persistence";
import { useLogStore } from "./useLogStore";
import { apiPost, apiGet, apiDelete } from "../api/client";

interface Skill { name: string; desc: string; enabled: boolean; }
interface MCPServer { id: string; name: string; command: string; status: string; tools: number; }

interface SettingsState {
  activeProvider: string;
  providerKeys: Record<string, string>;
  showKeys: Record<string, boolean>;
  verifyStatus: Record<string, string>;
  toolKeys: Record<string, string>;
  showToolKeys: Record<string, boolean>;
  model: string;
  visionModel: string;
  imageModel: string;
  temperature: number;
  topK: number;
  maxTokens: number;
  systemPrompt: string;
  longTermMemory: string;
  skills: Skill[];
  mcpServers: MCPServer[];
  chatFontSize: number;
  themeMode: string;
  lang: string;
  baseUrls: Record<string, string>;

  // RAG global defaults (for KB creation)
  embeddingDefaultModel: string;
  embeddingDefaultBaseUrl: string;
  embeddingDefaultApiKey: string;
  agentChunkerDefaultModel: string;
  agentChunkerDefaultBaseUrl: string;
  agentChunkerDefaultApiKey: string;
  defaultContextWindow: number;

  setActiveProvider: (p: string) => void;
  setProviderKey: (p: string, k: string) => void;
  toggleShowKey: (p: string) => void;
  setVerifyStatus: (p: string, s: string) => void;
  setToolKey: (t: string, k: string) => void;
  toggleShowToolKey: (t: string) => void;
  setModel: (m: string) => void;
  setVisionModel: (m: string) => void;
  setImageModel: (m: string) => void;
  setBaseUrl: (provider: string, url: string) => void;
  getBaseUrl: (provider: string) => string;
  addMcpServer: (name: string, command: string) => void;
  toggleSkill: (name: string) => void;
  toggleMcpServer: (name: string) => void;
  fetchMCPServers: () => Promise<void>;
  startMCPServer: (id: string) => Promise<void>;
  stopMCPServer: (id: string) => Promise<void>;
  addMCPServer: (config: { id: string; name: string; command: string; args?: string[]; env?: Record<string, string> }) => Promise<void>;
  removeMCPServer: (id: string) => Promise<void>;
  updateSetting: <K extends keyof SettingsState>(k: K, v: SettingsState[K]) => void;
}

// 默认 per-provider base URL 映射
const DEFAULT_BASE_URLS: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  deepseek: 'https://api.deepseek.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta/openai',
  ollama: 'http://localhost:11434/v1',
};

export function baseUrlByProvider(id: string) {
  return DEFAULT_BASE_URLS[id] || 'http://localhost:11434/v1';
}

// 需要持久化的字段
const PERSIST_KEYS: (keyof SettingsState)[] = [
  "activeProvider", "providerKeys", "model", "visionModel", "imageModel",
  "temperature", "topK", "maxTokens", "systemPrompt", "longTermMemory",
  "skills", "mcpServers", "toolKeys", "chatFontSize", "themeMode", "lang", "baseUrls",
  "embeddingDefaultModel", "embeddingDefaultBaseUrl", "embeddingDefaultApiKey",
  "agentChunkerDefaultModel", "agentChunkerDefaultBaseUrl", "agentChunkerDefaultApiKey",
  "defaultContextWindow",
];

// 默认值
const DEFAULTS = {
  activeProvider: "openai",
  providerKeys: { openai: "", deepseek: "" },
  showKeys: {} as Record<string, boolean>,
  verifyStatus: {} as Record<string, string>,
  toolKeys: {} as Record<string, string>,
  showToolKeys: {} as Record<string, boolean>,
  model: "gpt-4o",
  visionModel: "auto",
  imageModel: "auto",
  temperature: 0.2,
  topK: 5,
  maxTokens: 8192,
  systemPrompt: "",
  longTermMemory: "",
  skills: [
    { name: "search_docs", desc: "在向量知识库中检索文档片段", enabled: true },
    { name: "lookup_register", desc: "查询指定芯片的寄存器定义", enabled: true },
    { name: "calculate_timing", desc: "计算 I2C/SPI 等总线时序参数", enabled: true },
    { name: "compile_code", desc: "编译固件代码并返回编译日志", enabled: true },
    { name: "audit_pins", desc: "检查引脚分配是否与 Strapping 引脚冲突", enabled: true },
    { name: "web_search", desc: "搜索互联网获取最新信息", enabled: false },
    { name: "code_executor", desc: "执行 Python/C 代码片段", enabled: false },
    { name: "datasheet_lookup", desc: "通过型号查询元器件数据手册", enabled: true },
  ] as Skill[],
  mcpServers: [
    { id: "filesystem", name: "filesystem", command: "npx @modelcontextprotocol/server-filesystem /workspace", status: "stopped", tools: 0 },
    { id: "brave-search", name: "brave-search", command: "npx @modelcontextprotocol/server-brave-search", status: "stopped", tools: 0 },
    { id: "github", name: "github", command: "npx @modelcontextprotocol/server-github", status: "stopped", tools: 0 },
  ] as MCPServer[],
  chatFontSize: 14,
  themeMode: "light",
  lang: "zh",
  baseUrls: {} as Record<string, string>,
  embeddingDefaultModel: "text-embedding-3-small",
  embeddingDefaultBaseUrl: "https://api.openai.com/v1",
  embeddingDefaultApiKey: "",
  agentChunkerDefaultModel: "gpt-4o-mini",
  agentChunkerDefaultBaseUrl: "https://api.openai.com/v1",
  agentChunkerDefaultApiKey: "",
  defaultContextWindow: 256000,
};

// 从 localStorage 加载已保存的值，覆盖默认值
function loadPersistedDefaults(): Partial<typeof DEFAULTS> {
  const saved = loadFromStorage("settings", null as Record<string, unknown> | null);
  if (!saved) return {};
  const picked: Record<string, unknown> = {};
  for (const key of PERSIST_KEYS) {
    if (key in saved) {
      picked[key] = saved[key];
    }
  }
  // 迁移旧版 baseUrl → baseUrls
  if ("baseUrl" in saved && typeof saved.baseUrl === "string" && saved.baseUrl) {
    const active = (picked.activeProvider as string) || DEFAULTS.activeProvider;
    const baseUrls = (picked.baseUrls as Record<string, string>) || {};
    if (!baseUrls[active]) {
      baseUrls[active] = saved.baseUrl as string;
    }
    picked.baseUrls = baseUrls;
    delete picked.baseUrl;
  }
  // 迁移：maxTokens 太小会导致输出截断，至少 8192
  if (picked.maxTokens && (picked.maxTokens as number) < 8192) {
    picked.maxTokens = 8192;
  }
  return picked;
}

// 将需要持久化的字段序列化到 localStorage
function persist(state: SettingsState) {
  const data: Record<string, unknown> = {};
  for (const key of PERSIST_KEYS) {
    data[key] = state[key];
  }
  saveToStorage("settings", data);
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  ...DEFAULTS,
  ...loadPersistedDefaults(),

  setActiveProvider: (activeProvider) => {
    useLogStore.getState().log("info", "settings", `切换服务商: ${activeProvider}`);
    set({ activeProvider });
  },
  setProviderKey: (p, k) => {
    set((s) => ({ providerKeys: { ...s.providerKeys, [p]: k } }));
    // 将 API Key 加密存储到后端，获取 session_token
    if (k) {
      const baseUrl = get().getBaseUrl(p);
      apiPost<{ session_token: string; provider: string }>("auth/store-key", {
        provider: p,
        api_key: k,
        base_url: baseUrl,
      })
        .then((data) => {
          if (data?.session_token) {
            localStorage.setItem("session_token", data.session_token);
            useLogStore.getState().log("ok", "settings", `API Key 已加密存储: ${p}`);
          }
        })
        .catch((err) => {
          useLogStore.getState().log("error", "settings", `API Key 加密存储失败: ${err instanceof Error ? err.message : String(err)}`);
        });
    }
  },
  toggleShowKey: (p) => set((s) => ({ showKeys: { ...s.showKeys, [p]: !s.showKeys[p] } })),
  setVerifyStatus: (p, s) => set((st) => ({ verifyStatus: { ...st.verifyStatus, [p]: s } })),
  setToolKey: (t, k) => set((s) => ({ toolKeys: { ...s.toolKeys, [t]: k } })),
  toggleShowToolKey: (t) => set((s) => ({ showToolKeys: { ...s.showToolKeys, [t]: !s.showToolKeys[t] } })),
  setModel: (model) => {
    useLogStore.getState().log("info", "settings", `切换模型: ${model}`);
    set({ model });
  },
  setVisionModel: (visionModel) => set({ visionModel }),
  setImageModel: (imageModel) => set({ imageModel }),
  setBaseUrl: (provider, url) => set((s) => ({
    baseUrls: { ...s.baseUrls, [provider]: url },
  })),
  getBaseUrl: (provider) => {
    const state = get();
    return state.baseUrls[provider] || baseUrlByProvider(provider);
  },
  addMcpServer: (name, command) => {
    useLogStore.getState().log("info", "settings", `添加 MCP 服务器: ${name}`);
    set((s) => ({
      mcpServers: [...s.mcpServers, { id: name, name, command, status: "stopped", tools: 0 }],
    }));
  },
  toggleSkill: (name) => {
    useLogStore.getState().log("debug", "settings", `切换技能: ${name}`);
    set((s) => ({
      skills: s.skills.map((sk) => sk.name === name ? { ...sk, enabled: !sk.enabled } : sk),
    }));
  },
  toggleMcpServer: (name) => {
    useLogStore.getState().log("debug", "settings", `切换 MCP: ${name}`);
    set((s) => ({
      mcpServers: s.mcpServers.map((srv) => srv.name === name ? { ...srv, status: srv.status === "running" ? "stopped" : "running" } : srv),
    }));
  },
  fetchMCPServers: async () => {
    try {
      const data = await apiGet<{ servers: Array<{ id: string; name: string; command: string; status: string; tools_count: number }> }>("mcp/servers");
      if (data?.servers) {
        set({
          mcpServers: data.servers.map((s) => ({
            id: s.id,
            name: s.name,
            command: s.command,
            status: s.status,
            tools: s.tools_count,
          })),
        });
        useLogStore.getState().log("ok", "settings", `MCP 服务器列表已刷新: ${data.servers.length} 个`);
      }
    } catch (err) {
      useLogStore.getState().log("warn", "settings", `MCP 服务器列表获取失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  },
  startMCPServer: async (id) => {
    try {
      await apiPost(`mcp/servers/${id}/start`);
      useLogStore.getState().log("ok", "settings", `MCP 服务器已启动: ${id}`);
      // 刷新列表
      await get().fetchMCPServers();
    } catch (err) {
      useLogStore.getState().log("error", "settings", `MCP 服务器启动失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  },
  stopMCPServer: async (id) => {
    try {
      await apiPost(`mcp/servers/${id}/stop`);
      useLogStore.getState().log("ok", "settings", `MCP 服务器已停止: ${id}`);
      await get().fetchMCPServers();
    } catch (err) {
      useLogStore.getState().log("error", "settings", `MCP 服务器停止失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  },
  addMCPServer: async (config) => {
    try {
      await apiPost("mcp/servers", config);
      useLogStore.getState().log("ok", "settings", `MCP 服务器已添加: ${config.name}`);
      await get().fetchMCPServers();
    } catch (err) {
      useLogStore.getState().log("error", "settings", `MCP 服务器添加失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  },
  removeMCPServer: async (id) => {
    try {
      await apiDelete(`mcp/servers/${id}`);
      useLogStore.getState().log("ok", "settings", `MCP 服务器已删除: ${id}`);
      await get().fetchMCPServers();
    } catch (err) {
      useLogStore.getState().log("error", "settings", `MCP 服务器删除失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  },
  updateSetting: (key, value) => set({ [key]: value } as Partial<SettingsState>),
}));

// 自动持久化：任何状态变更时写入 localStorage
useSettingsStore.subscribe((state) => {
  persist(state);
});


