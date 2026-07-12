import { create } from "zustand";
import { loadFromStorage, saveToStorage } from "../utils/persistence";
import { useLogStore } from "./useLogStore";
import { apiPost, apiGet, apiPut, apiPatch, apiDelete } from "../api/client";

interface Skill { name: string; desc: string; enabled: boolean; group?: string; }
interface MCPServer { id: string; name: string; command: string; status: string; tools: number; }

/** 自建服务商配置：用户自行填写 name/baseUrl/apiKey，验证后拉取模型列表 */
export interface ProviderConfig {
  id: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  verified: boolean;
  models: string[];
}

interface SettingsState {
  // 服务商列表（用户自建）
  providers: ProviderConfig[];
  // 三类模型各自选择服务商 + 模型
  chatProviderId: string;
  chatModel: string;
  imageProviderId: string;
  imageModel: string;
  visionProviderId: string;
  visionModel: string;

  temperature: number;
  topK: number;
  maxTokens: number;
  relevanceThreshold: number; // 0-100, 0=no filtering, 60=default recommended
  systemPrompt: string;
  longTermMemory: string;
  skills: Skill[];
  mcpServers: MCPServer[];
  // Web Search 工具配置（Tavily）：key + 可选自定义 baseUrl
  webSearchConfig: { apiKey: string; baseUrl: string };
  showWebSearchKey: boolean;
  // Image Generation 工具独立配置（绕过 provider 验证，例如 inroi /images/generations）
  imageGenerationConfig: { apiKey: string; baseUrl: string; model: string };
  showImageGenerationKey: boolean;
  chatFontSize: number;
  themeMode: string;
  lang: string;

  // RAG global defaults (for KB creation)
  embeddingDefaultModel: string;
  embeddingDefaultBaseUrl: string;
  embeddingDefaultApiKey: string;
  agentChunkerDefaultModel: string;
  agentChunkerDefaultBaseUrl: string;
  agentChunkerDefaultApiKey: string;
  defaultContextWindow: number;
  /** Agent permission mode: bypassPermissions=default bypass, default=ask each tool, acceptEdits=auto-accept edits */
  permissionMode: "bypassPermissions" | "default" | "acceptEdits";

  // 服务商管理
  addProvider: (name: string, baseUrl: string, apiKey: string) => ProviderConfig;
  removeProvider: (id: string) => void;
  updateProvider: (id: string, patch: Partial<Pick<ProviderConfig, "name" | "baseUrl" | "apiKey">>) => void;
  verifyProvider: (id: string) => Promise<boolean>;
  /** 重新从上游拉取模型列表并写入 provider.models */
  fetchProviderModels: (id: string) => Promise<string[]>;
  /** 获取指定用途的服务商+模型+base_url+api_key（用于请求头注入和 /api/chat） */
  resolveChatCreds: () => { providerId: string; model: string; baseUrl: string; apiKey: string };
  resolveImageCreds: () => { providerId: string; model: string; baseUrl: string; apiKey: string };
  resolveVisionCreds: () => { providerId: string; model: string; baseUrl: string; apiKey: string };

  // 模型选择
  setChatModel: (providerId: string, model: string) => void;
  setImageModel: (providerId: string, model: string) => void;
  setVisionModel: (providerId: string, model: string) => void;

  setWebSearchKey: (k: string) => void;
  setWebSearchBaseUrl: (url: string) => void;
  toggleShowWebSearchKey: () => void;
  setImageGenerationKey: (k: string) => void;
  setImageGenerationBaseUrl: (url: string) => void;
  setImageGenerationModel: (model: string) => void;
  toggleShowImageGenerationKey: () => void;
  addMcpServer: (name: string, command: string) => void;
  toggleSkill: (name: string) => Promise<void>;
  toggleMcpServer: (name: string) => void;
  fetchMCPServers: () => Promise<void>;
  startMCPServer: (id: string) => Promise<void>;
  stopMCPServer: (id: string) => Promise<void>;
  addMCPServer: (config: { id: string; name: string; command: string; args?: string[]; env?: Record<string, string> }) => Promise<void>;
  removeMCPServer: (id: string) => Promise<void>;
  /** 从后端 /api/tools 拉取真实工具列表，覆盖本地 skills */
  fetchTools: () => Promise<void>;
  /** 从后端 /api/settings 拉取持久化设置，merge 到本地（不覆盖后端没有的字段） */
  fetchSettings: () => Promise<void>;
  updateSetting: <K extends keyof SettingsState>(k: K, v: SettingsState[K]) => Promise<void>;
}

// 需要持久化的字段
const PERSIST_KEYS: (keyof SettingsState)[] = [
  "providers", "chatProviderId", "chatModel", "imageProviderId", "imageModel",
  "visionProviderId", "visionModel",
  "temperature", "topK", "maxTokens", "relevanceThreshold", "systemPrompt", "longTermMemory",
  "skills", "mcpServers", "webSearchConfig", "imageGenerationConfig", "chatFontSize", "themeMode", "lang",
  "embeddingDefaultModel", "embeddingDefaultBaseUrl", "embeddingDefaultApiKey",
  "agentChunkerDefaultModel", "agentChunkerDefaultBaseUrl", "agentChunkerDefaultApiKey",
  "defaultContextWindow", "permissionMode",
];

// 默认值
const DEFAULTS = {
  providers: [] as ProviderConfig[],
  chatProviderId: "",
  chatModel: "",
  imageProviderId: "",
  imageModel: "",
  visionProviderId: "",
  visionModel: "",
  showWebSearchKey: false,
  webSearchConfig: { apiKey: "", baseUrl: "" },
  showImageGenerationKey: false,
  imageGenerationConfig: { apiKey: "", baseUrl: "", model: "" },
  temperature: 0.2,
  topK: 5,
  maxTokens: 8192,
  relevanceThreshold: 0,
  systemPrompt: "",
  longTermMemory: "",
  skills: [] as Skill[],
  mcpServers: [] as MCPServer[],
  chatFontSize: 14,
  themeMode: "light",
  lang: "zh",
  embeddingDefaultModel: "",
  embeddingDefaultBaseUrl: "",
  embeddingDefaultApiKey: "",
  agentChunkerDefaultModel: "",
  agentChunkerDefaultBaseUrl: "",
  agentChunkerDefaultApiKey: "",
  defaultContextWindow: 0,
  permissionMode: "default" as "bypassPermissions" | "default" | "acceptEdits",
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

/** 生成简单唯一 ID */
function genId(): string {
  return `prov_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  ...DEFAULTS,
  ...loadPersistedDefaults(),

  addProvider: (name, baseUrl, apiKey) => {
    const provider: ProviderConfig = {
      id: genId(),
      name: name.trim() || "未命名服务商",
      baseUrl: baseUrl.trim(),
      apiKey: apiKey.trim(),
      verified: false,
      models: [],
    };
    set((s) => ({ providers: [...s.providers, provider] }));
    useLogStore.getState().log("info", "settings", `新增服务商: ${provider.name}`);
    return provider;
  },

  removeProvider: (id) => {
    set((s) => {
      const providers = s.providers.filter((p) => p.id !== id);
      // 如果删除的是当前选中的，清空选择
      const patch: Partial<SettingsState> = { providers };
      if (s.chatProviderId === id) { patch.chatProviderId = ""; patch.chatModel = ""; }
      if (s.imageProviderId === id) { patch.imageProviderId = ""; patch.imageModel = ""; }
      if (s.visionProviderId === id) { patch.visionProviderId = ""; patch.visionModel = ""; }
      return patch;
    });
    useLogStore.getState().log("info", "settings", `删除服务商: ${id}`);
  },

  updateProvider: (id, patch) => {
    set((s) => ({
      providers: s.providers.map((p) =>
        p.id === id ? { ...p, ...patch, verified: false, models: [] } : p
      ),
    }));
  },

  verifyProvider: async (id) => {
    const provider = get().providers.find((p) => p.id === id);
    if (!provider) return false;
    try {
      // 传入正在验证的 provider 的凭证，覆盖全局 header
      // /api/models 使用 current_user_optional，无需 session_token 即可验证
      const customHeaders: Record<string, string> = {
        "X-API-Key": provider.apiKey,
        "X-Base-URL": provider.baseUrl,
        "X-Provider": id,
      };
      const res = await apiPost<{ models: string[] }>("models", { base_url: provider.baseUrl }, 60_000, customHeaders);
      if (res.models && res.models.length > 0) {
        set((s) => ({
          providers: s.providers.map((p) =>
            p.id === id ? { ...p, verified: true, models: res.models } : p
          ),
        }));
        // 加密存储 API Key 到后端（fire-and-forget）。
        // 流程：store-key → 401 时 fallback 到 /api/auth/login 恢复会话。
        // login 用 provider+api_key 换 session_token（要求 key 与后端已存储的匹配）。
        const persistKey = async () => {
          const hasSessionToken = !!localStorage.getItem("session_token");
          if (hasSessionToken) {
            // 已登录 → 直接 store-key（可能更新 base_url 等元数据）
            try {
              const data = await apiPost<{ session_token?: string }>("auth/store-key", {
                provider: id, api_key: provider.apiKey, base_url: provider.baseUrl,
              });
              if (data?.session_token) {
                localStorage.setItem("session_token", data.session_token);
                useLogStore.getState().log("ok", "settings", `API Key 已加密存储并刷新 session_token`);
              }
              return;
            } catch (err) {
              useLogStore.getState().log("error", "settings", `API Key 加密存储失败: ${err instanceof Error ? err.message : String(err)}`);
              return;
            }
          }
          // 未登录 → 先尝试 store-key（首次场景：后端无 provider 时免鉴权）
          try {
            const data = await apiPost<{ session_token?: string }>("auth/store-key", {
              provider: id, api_key: provider.apiKey, base_url: provider.baseUrl,
            });
            if (data?.session_token) {
              localStorage.setItem("session_token", data.session_token);
              useLogStore.getState().log("ok", "settings", `API Key 已加密存储并获取 session_token`);
            }
          } catch (storeKeyErr) {
            // store-key 401 → 后端已有 provider 但本机无 token，尝试 login 恢复会话
            try {
              const loginData = await apiPost<{ session_token?: string }>("auth/login", {
                provider: id, api_key: provider.apiKey,
              });
              if (loginData?.session_token) {
                localStorage.setItem("session_token", loginData.session_token);
                useLogStore.getState().log("ok", "settings", `会话已恢复（login 成功），session_token 已写入 localStorage`);
                // 拿到 token 后再补一次 store-key 以更新元数据
                apiPost("auth/store-key", {
                  provider: id, api_key: provider.apiKey, base_url: provider.baseUrl,
                }).catch(() => { /* 元数据更新失败不阻塞 */ });
              }
            } catch (loginErr) {
              useLogStore.getState().log("warn", "settings",
                `API Key 加密存储失败且 login 未匹配到已存储的 provider（${loginErr instanceof Error ? loginErr.message : String(loginErr)}）。验证已通过，但未持久化。若需持久化，请输入之前已存储过的 API Key 后重新点击验证。`);
            }
          }
        };
        void persistKey();
        useLogStore.getState().log("ok", "settings", `服务商验证成功: ${provider.name} (${res.models.length} 模型)`);
        return true;
      }
      return false;
    } catch (err) {
      useLogStore.getState().log("error", "settings", `服务商验证失败: ${provider.name} - ${err instanceof Error ? err.message : String(err)}`);
      return false;
    }
  },

  fetchProviderModels: async (id) => {
    const provider = get().providers.find((p) => p.id === id);
    if (!provider) return [];
    try {
      const customHeaders: Record<string, string> = {
        "X-API-Key": provider.apiKey,
        "X-Base-URL": provider.baseUrl,
        "X-Provider": id,
      };
      const res = await apiPost<{ models: string[] }>("models", { base_url: provider.baseUrl }, 60_000, customHeaders);
      if (res.models && res.models.length > 0) {
        set((s) => ({
          providers: s.providers.map((p) =>
            p.id === id ? { ...p, models: res.models, verified: true } : p
          ),
        }));
        return res.models;
      }
      return [];
    } catch {
      return [];
    }
  },

  resolveChatCreds: () => {
    const s = get();
    const p = s.providers.find((pv) => pv.id === s.chatProviderId);
    return {
      providerId: s.chatProviderId,
      model: s.chatModel,
      baseUrl: p?.baseUrl || "",
      apiKey: p?.apiKey || "",
    };
  },

  resolveImageCreds: () => {
    const s = get();
    // Manual tool-level config takes precedence over provider selection, so services
    // like inroi that fail /models validation can still be used for image generation.
    const manual = s.imageGenerationConfig;
    if (manual.apiKey && manual.baseUrl) {
      return {
        providerId: "image_tool_manual",
        model: manual.model || s.imageModel || s.chatModel,
        baseUrl: manual.baseUrl,
        apiKey: manual.apiKey,
      };
    }
    // imageProviderId 为空时回退到 chatProviderId
    const pid = s.imageProviderId || s.chatProviderId;
    const p = s.providers.find((pv) => pv.id === pid);
    return {
      providerId: pid,
      model: s.imageModel || s.chatModel,
      baseUrl: p?.baseUrl || "",
      apiKey: p?.apiKey || "",
    };
  },

  resolveVisionCreds: () => {
    const s = get();
    const pid = s.visionProviderId || s.chatProviderId;
    const p = s.providers.find((pv) => pv.id === pid);
    return {
      providerId: pid,
      model: s.visionModel || s.chatModel,
      baseUrl: p?.baseUrl || "",
      apiKey: p?.apiKey || "",
    };
  },

  setChatModel: (providerId, model) => {
    useLogStore.getState().log("info", "settings", `切换对话模型: ${model}`);
    set({ chatProviderId: providerId, chatModel: model });
  },
  setImageModel: (providerId, model) => {
    set({ imageProviderId: providerId, imageModel: model });
  },
  setVisionModel: (providerId, model) => {
    set({ visionProviderId: providerId, visionModel: model });
  },

  setWebSearchKey: (k) => set((s) => ({ webSearchConfig: { ...s.webSearchConfig, apiKey: k } })),
  setWebSearchBaseUrl: (url) => set((s) => ({ webSearchConfig: { ...s.webSearchConfig, baseUrl: url } })),
  toggleShowWebSearchKey: () => set((s) => ({ showWebSearchKey: !s.showWebSearchKey })),
  setImageGenerationKey: (k) => set((s) => ({ imageGenerationConfig: { ...s.imageGenerationConfig, apiKey: k } })),
  setImageGenerationBaseUrl: (url) => set((s) => ({ imageGenerationConfig: { ...s.imageGenerationConfig, baseUrl: url } })),
  setImageGenerationModel: (model) => set((s) => ({ imageGenerationConfig: { ...s.imageGenerationConfig, model } })),
  toggleShowImageGenerationKey: () => set((s) => ({ showImageGenerationKey: !s.showImageGenerationKey })),

  addMcpServer: (name, command) => {
    useLogStore.getState().log("info", "settings", `添加 MCP 服务器: ${name}`);
    set((s) => ({
      mcpServers: [...s.mcpServers, { id: name, name, command, status: "stopped", tools: 0 }],
    }));
  },
  toggleSkill: async (name) => {
    useLogStore.getState().log("debug", "settings", `切换技能: ${name}`);
    try {
      const res = await apiPatch<{ name: string; enabled: boolean }>(`tools/${name}/toggle`);
      if (res?.name) {
        set((s) => ({
          skills: s.skills.map((sk) => sk.name === name ? { ...sk, enabled: res.enabled } : sk),
        }));
      }
    } catch (err) {
      // 后端没改，本地状态保持不变
      console.warn(`toggleSkill failed: ${name}`, err);
      useLogStore.getState().log("warn", "settings", `切换技能失败: ${name} - ${err instanceof Error ? err.message : String(err)}`);
    }
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
  updateSetting: async (key, value) => {
    if (key === "permissionMode") {
      console.info('[SettingsStore] permission_mode=%s', value);
    } else if (key === "topK") {
      console.info('[SettingsStore] retrieval topK=%d', value);
    } else if (key === "relevanceThreshold") {
      console.info('[SettingsStore] retrieval threshold=%f', value);
    }
    set({ [key]: value } as Partial<SettingsState>);
    // fire-and-forget backend sync; 不阻塞 UI
    apiPut("settings", { [key]: value }).catch((err) => {
      console.warn(`updateSetting sync failed: ${String(key)}`, err);
    });
  },

  fetchTools: async () => {
    try {
      const data = await apiGet<{
        tools: Array<{ name: string; description: string; group: string; enabled: boolean }>;
        total: number;
      }>("tools");
      if (data?.tools) {
        set({
          skills: data.tools.map((t) => ({
            name: t.name,
            desc: t.description,
            group: t.group,
            enabled: t.enabled,
          })),
        });
        useLogStore.getState().log("ok", "settings", `工具列表已刷新: ${data.tools.length} 个`);
      }
    } catch (err) {
      useLogStore.getState().log("warn", "settings", `工具列表获取失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  },

  fetchSettings: async () => {
    try {
      const data = await apiGet<Record<string, unknown>>("settings");
      if (!data) return;
      set((s) => {
        const patch: Partial<SettingsState> = {};
        for (const key of PERSIST_KEYS) {
          if (key in data) {
            patch[key] = data[key] as SettingsState[typeof key];
          }
        }
        // 后端没有的字段（如 providers）保持本地值，不覆盖
        return { ...s, ...patch };
      });
    } catch (err) {
      console.warn("fetchSettings failed", err);
    }
  },
}));

// 自动持久化：任何状态变更时写入 localStorage
useSettingsStore.subscribe((state) => {
  persist(state);
});

// 初始化：拉取后端持久化的 settings，merge 到本地（后端没有的字段保持本地值）
useSettingsStore.getState().fetchSettings();
