import { create } from "zustand";
import { useLogStore } from "./useLogStore";
import { useToastStore } from "./useToastStore";
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
  retrievalSettingsStatus: {
    topK: RetrievalSettingStatus;
    relevanceThreshold: RetrievalSettingStatus;
  };
  retrievalSettingsStorageIssue: boolean;
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
  verifyProvider: (id: string) => Promise<
    | { ok: true; modelCount: number }
    | { ok: false; kind: "local-auth"; code: "AUTH_REQUIRED" | "AUTH_INVALID" }
    | { ok: false; kind: "model-service"; code?: "MODEL_FETCH_FAILED" }
    | { ok: false; kind: "network" | "timeout" | "empty-models" | "unknown" }
  >;
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
  /** 从后端 /api/settings 读取权威的长期记忆值 */
  fetchSettings: () => Promise<void>;
  /** 仅在服务器确认后更新手动维护的长期记忆 */
  saveLongTermMemory: (value: string) => Promise<boolean>;
  updateSetting: <K extends keyof SettingsState>(k: K, v: SettingsState[K]) => Promise<void>;
}

const LONG_TERM_MEMORY_MAX_CHARS = 4000;

type RetrievalSettingKey = "topK" | "relevanceThreshold";
type RetrievalSettingStatus = "idle" | "saving" | "synced" | "local_only" | "read_error";

// 需要持久化的字段
const PERSIST_KEYS: (keyof SettingsState)[] = [
  "providers", "chatProviderId", "chatModel", "imageProviderId", "imageModel",
  "visionProviderId", "visionModel",
  "temperature", "topK", "maxTokens", "relevanceThreshold", "systemPrompt",
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

function readPersistedSettings(): { value: Record<string, unknown> | null; error: boolean } {
  try {
    const raw = localStorage.getItem("hwrag_settings");
    if (!raw) return { value: null, error: false };
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { value: null, error: true };
    return { value: parsed as Record<string, unknown>, error: false };
  } catch {
    return { value: null, error: true };
  }
}

const persistedSettingsAtStartup = readPersistedSettings();

function normalizeTopK(value: unknown): number | null {
  const parsed = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  return typeof parsed === "number" && Number.isInteger(parsed) && parsed >= 1 && parsed <= 20
    ? parsed
    : null;
}

function normalizeRelevanceThreshold(value: unknown): number | null {
  const parsed = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  return typeof parsed === "number" && Number.isFinite(parsed) && parsed >= 0 && parsed <= 100
    ? parsed
    : null;
}

const retrievalLocalExplicit: Record<RetrievalSettingKey, boolean> = {
  topK: normalizeTopK(persistedSettingsAtStartup.value?.topK) !== null,
  relevanceThreshold: normalizeRelevanceThreshold(persistedSettingsAtStartup.value?.relevanceThreshold) !== null,
};
const retrievalWriteVersion: Record<RetrievalSettingKey, number> = { topK: 0, relevanceThreshold: 0 };
const retrievalSaveQueue: Record<RetrievalSettingKey, Promise<void>> = {
  topK: Promise.resolve(),
  relevanceThreshold: Promise.resolve(),
};

// 从 localStorage 加载已保存的值，覆盖默认值
function loadPersistedDefaults(): Partial<typeof DEFAULTS> {
  const saved = persistedSettingsAtStartup.value;
  if (!saved) return {};
  const picked: Record<string, unknown> = {};
  for (const key of PERSIST_KEYS) {
    if (key in saved) {
      picked[key] = saved[key];
    }
  }
  const topK = normalizeTopK(picked.topK);
  if (topK === null) delete picked.topK;
  else picked.topK = topK;
  const relevanceThreshold = normalizeRelevanceThreshold(picked.relevanceThreshold);
  if (relevanceThreshold === null) delete picked.relevanceThreshold;
  else picked.relevanceThreshold = relevanceThreshold;
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
  try {
    localStorage.setItem("hwrag_settings", JSON.stringify(data));
    return true;
  } catch {
    return false;
  }
}

// Prevent a settings GET started before a successful save from overwriting it.
let longTermMemoryWriteVersion = 0;

/** 生成简单唯一 ID */
function genId(): string {
  return `prov_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  ...DEFAULTS,
  ...loadPersistedDefaults(),
  retrievalSettingsStatus: { topK: "idle", relevanceThreshold: "idle" },
  retrievalSettingsStorageIssue: persistedSettingsAtStartup.error,

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
    if (!provider) return { ok: false, kind: "unknown" };
    try {
      // 传入正在验证的 provider 的凭证，覆盖全局 header
      // /api/models 使用 current_user_optional，无需 session_token 即可验证
      const customHeaders: Record<string, string> = {
        "X-API-Key": provider.apiKey,
        "X-Base-URL": provider.baseUrl,
        "X-Provider": id,
      };
      const response = await apiPost<unknown>("models", { base_url: provider.baseUrl }, 60_000, customHeaders);
      const models = response && typeof response === "object" && "models" in response
        ? (response as { models?: unknown }).models
        : undefined;
      if (!Array.isArray(models)) {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (unknown)");
        return { ok: false, kind: "unknown" };
      }
      if (models.length === 0) {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (empty-models)");
        return { ok: false, kind: "empty-models" };
      }
      if (!models.every((model) => typeof model === "string" && model.trim().length > 0)) {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (unknown)");
        return { ok: false, kind: "unknown" };
      }
      const verifiedModels = models as string[];
      set((s) => ({
        providers: s.providers.map((p) =>
          p.id === id ? { ...p, verified: true, models: verifiedModels } : p
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
          } catch {
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
      useLogStore.getState().log("ok", "settings", "服务商验证成功 (" + verifiedModels.length + " 模型)");
      return { ok: true, modelCount: verifiedModels.length };
    } catch (err) {
      const code = err && typeof err === "object" && "code" in err
        ? (err as { code?: unknown }).code
        : undefined;
      if (code === "AUTH_REQUIRED" || code === "AUTH_INVALID") {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (local-auth, " + code + ")");
        return { ok: false, kind: "local-auth", code };
      }
      if (code === "MODEL_FETCH_FAILED") {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (model-service, MODEL_FETCH_FAILED)");
        return { ok: false, kind: "model-service", code };
      }
      if (err && typeof err === "object" && "name" in err && (err as { name?: unknown }).name === "AbortError") {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (timeout)");
        return { ok: false, kind: "timeout" };
      }
      if (err instanceof TypeError) {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (network)");
        return { ok: false, kind: "network" };
      }
      if (err instanceof Error && /^API 5\d{2}:/.test(err.message)) {
        useLogStore.getState().log("error", "settings", "服务商验证失败 (model-service)");
        return { ok: false, kind: "model-service" };
      }
      useLogStore.getState().log("error", "settings", "服务商验证失败 (unknown)");
      return { ok: false, kind: "unknown" };
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
      useToastStore.getState().showError("获取模型列表失败");
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
      useToastStore.getState().showError("切换技能失败");
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
      useToastStore.getState().showError("获取 MCP 服务器列表失败");
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
      useToastStore.getState().showError("删除 MCP 服务器失败");
    }
  },
  updateSetting: async (key, value) => {
    if (key === "topK" || key === "relevanceThreshold") {
      const retrievalKey = key as RetrievalSettingKey;
      const normalized = retrievalKey === "topK" ? normalizeTopK(value) : normalizeRelevanceThreshold(value);
      if (normalized === null) {
        useToastStore.getState().showError(retrievalKey === "topK" ? "Top-K 必须是 1 到 20 的整数" : "相关度阈值必须在 0 到 100 之间");
        return;
      }
      retrievalLocalExplicit[retrievalKey] = true;
      const version = ++retrievalWriteVersion[retrievalKey];
      set((state) => ({
        [retrievalKey]: normalized,
        retrievalSettingsStatus: { ...state.retrievalSettingsStatus, [retrievalKey]: "saving" },
      } as Partial<SettingsState>));
      const localSaved = persist(get());
      set({ retrievalSettingsStorageIssue: !localSaved });

      const previous = retrievalSaveQueue[retrievalKey];
      const operation = previous.catch(() => undefined).then(async () => {
        if (version !== retrievalWriteVersion[retrievalKey]) return;
        const latestValue = get()[retrievalKey];
        try {
          await apiPut("settings", { [retrievalKey]: latestValue });
          if (version !== retrievalWriteVersion[retrievalKey]) return;
          const savedLocally = persist(get());
          set((state) => ({
            retrievalSettingsStatus: { ...state.retrievalSettingsStatus, [retrievalKey]: "synced" },
            retrievalSettingsStorageIssue: !savedLocally,
          }));
          if (!savedLocally) useToastStore.getState().showError("后台已同步，但本机缓存写入失败");
        } catch (err) {
          console.warn(`updateSetting sync failed: ${key}`, err);
          if (version !== retrievalWriteVersion[retrievalKey]) return;
          const savedLocally = persist(get());
          set((state) => ({
            retrievalSettingsStatus: { ...state.retrievalSettingsStatus, [retrievalKey]: "local_only" },
            retrievalSettingsStorageIssue: !savedLocally,
          }));
          useToastStore.getState().showError(
            savedLocally ? "本机已保存，后台未同步" : "本机保存失败，后台也未同步"
          );
        }
      });
      retrievalSaveQueue[retrievalKey] = operation;
      await operation;
      return;
    }
    if (key === "permissionMode") {
      console.info('[SettingsStore] permission_mode=%s', value);
    }
    set({ [key]: value } as Partial<SettingsState>);
    // fire-and-forget backend sync; 不阻塞 UI
    apiPut("settings", { [key]: value }).catch((err) => {
      console.warn(`updateSetting sync failed: ${String(key)}`, err);
    });
  },

  saveLongTermMemory: async (value) => {
    if (typeof value !== "string" || Array.from(value).length > LONG_TERM_MEMORY_MAX_CHARS) {
      return false;
    }
    try {
      await apiPut("settings", { longTermMemory: value });
      longTermMemoryWriteVersion += 1;
      set({ longTermMemory: value });
      return true;
    } catch {
      // Keep the last server-confirmed value unchanged; the editor owns the draft/error UI.
      return false;
    }
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
    const writeVersionAtStart = longTermMemoryWriteVersion;
    const retrievalVersionsAtStart = { ...retrievalWriteVersion };
    try {
      const data = await apiGet<{ settings?: Record<string, unknown> }>("settings");
      const serverSettings = data?.settings;
      if (!serverSettings || typeof serverSettings !== "object" || Array.isArray(serverSettings)) return;
      const serverMemory = typeof serverSettings.longTermMemory === "string"
        ? serverSettings.longTermMemory
        : "";
      const updates: Partial<Pick<SettingsState, RetrievalSettingKey | "longTermMemory">> = {};
      const statuses: Partial<SettingsState["retrievalSettingsStatus"]> = {};
      const localValuesToSync: Array<[RetrievalSettingKey, number]> = [];
      for (const key of ["topK", "relevanceThreshold"] as const) {
        if (retrievalVersionsAtStart[key] !== retrievalWriteVersion[key]) continue;
        const serverValue = key === "topK"
          ? normalizeTopK(serverSettings[key])
          : normalizeRelevanceThreshold(serverSettings[key]);
        const localValue = get()[key];
        if (retrievalLocalExplicit[key]) {
          if (serverValue === localValue) statuses[key] = "synced";
          else {
            statuses[key] = "saving";
            localValuesToSync.push([key, localValue]);
          }
        } else if (serverValue !== null) {
          updates[key] = serverValue;
          statuses[key] = "synced";
        }
      }
      if (writeVersionAtStart === longTermMemoryWriteVersion) updates.longTermMemory = serverMemory;
      set((state) => ({
        ...updates,
        retrievalSettingsStatus: { ...state.retrievalSettingsStatus, ...statuses },
        retrievalSettingsStorageIssue: false,
      }));
      const saved = persist(get());
      if (!saved) set({ retrievalSettingsStorageIssue: true });
      for (const [key, value] of localValuesToSync) void get().updateSetting(key, value);
    } catch (err) {
      console.warn("fetchSettings failed", err);
      set((state) => {
        const statuses = { ...state.retrievalSettingsStatus };
        for (const key of ["topK", "relevanceThreshold"] as const) {
          if (retrievalVersionsAtStart[key] === retrievalWriteVersion[key] && statuses[key] === "idle") {
            statuses[key] = "read_error";
          }
        }
        return { retrievalSettingsStatus: statuses };
      });
      useToastStore.getState().showError("加载设置失败");
    }
  },
}));

// 自动持久化：任何状态变更时写入 localStorage
useSettingsStore.subscribe((state) => {
  persist(state);
});

// 初始化：从后端恢复手动长期记忆；不从浏览器缓存读取记忆内容
useSettingsStore.getState().fetchSettings();
