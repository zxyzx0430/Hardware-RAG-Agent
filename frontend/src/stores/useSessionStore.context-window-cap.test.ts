import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CONTEXT_WINDOW_CAP = 1048576;
const DEFAULT_WINDOW = 1048576;

const mocks = vi.hoisted(() => {
  const apiGet = vi.fn();
  const apiPost = vi.fn();
  const apiPatch = vi.fn();
  const chatState = {
    needsApiKey: false,
    activeSessionId: "",
    setActiveSession: vi.fn(),
  };
  const useChatStore = Object.assign(
    vi.fn((selector?: (state: typeof chatState) => unknown) => (selector ? selector(chatState) : chatState)),
    {
      getState: () => chatState,
      setState: vi.fn(),
    },
  );
  const settingsState = {
    providers: [],
    chatProviderId: "",
    chatModel: "",
    imageProviderId: "",
    imageModel: "",
    visionProviderId: "",
    visionModel: "",
    temperature: 0.2,
    topK: 5,
    maxTokens: 8192,
    relevanceThreshold: 0,
    retrievalSettingsStatus: { topK: "idle", relevanceThreshold: "idle" },
    retrievalSettingsStorageIssue: false,
    systemPrompt: "",
    longTermMemory: "",
    skills: [],
    webSearchConfig: { apiKey: "", baseUrl: "" },
    showWebSearchKey: false,
    imageGenerationConfig: { apiKey: "", baseUrl: "", model: "" },
    showImageGenerationKey: false,
    addProvider: vi.fn(),
    removeProvider: vi.fn(),
    updateProvider: vi.fn(),
    verifyProvider: vi.fn(),
    fetchProviderModels: vi.fn(),
    setChatModel: vi.fn(),
    setImageModel: vi.fn(),
    setVisionModel: vi.fn(),
    updateSetting: vi.fn(),
    saveLongTermMemory: vi.fn(),
    toggleSkill: vi.fn(),
    setWebSearchKey: vi.fn(),
    setWebSearchBaseUrl: vi.fn(),
    toggleShowWebSearchKey: vi.fn(),
    setImageGenerationKey: vi.fn(),
    setImageGenerationBaseUrl: vi.fn(),
    setImageGenerationModel: vi.fn(),
    toggleShowImageGenerationKey: vi.fn(),
    fetchTools: vi.fn(),
  };
  const appState = {
    setActiveNav: vi.fn(),
    themeMode: "light",
    setThemeMode: vi.fn(),
    lang: "en",
    setLang: vi.fn(),
    chatFontSize: 14,
    setChatFontSize: vi.fn(),
  };
  const logState = {
    buffer: [],
    filter: { levels: {}, timeRange: 0 },
    setFilter: vi.fn(),
    clear: vi.fn(),
    getFiltered: () => [],
  };
  return {
    apiGet,
    apiPost,
    apiPatch,
    chatState,
    useChatStore,
    settingsState,
    appState,
    logState,
  };
});

vi.mock("../api/client", () => ({
  apiGet: mocks.apiGet,
  apiPost: mocks.apiPost,
  apiPut: vi.fn(),
  apiPatch: mocks.apiPatch,
  apiDelete: vi.fn(),
}));
vi.mock("../utils/broadcast", () => ({ post: vi.fn(), on: vi.fn(() => vi.fn()) }));
vi.mock("./useChatStore", () => ({ useChatStore: mocks.useChatStore }));
vi.mock("./useSettingsStore", () => ({
  useSettingsStore: Object.assign(vi.fn(() => mocks.settingsState), {
    getState: () => mocks.settingsState,
  }),
}));
vi.mock("./useToastStore", () => ({ useToastStore: { getState: () => ({ showError: vi.fn() }) } }));
vi.mock("./useLogStore", () => ({
  useLogStore: Object.assign(vi.fn(() => mocks.logState), { getState: () => ({ log: vi.fn() }) }),
}));
vi.mock("../stores/useAppStore", () => ({ useAppStore: vi.fn(() => mocks.appState) }));
vi.mock("../stores/useModalStore", () => ({ useModalStore: vi.fn(() => ({ confirmDialog: vi.fn() })) }));
vi.mock("../i18n", () => ({ useI18n: () => ({ t: (key: string) => key }) }));
vi.mock("../components/settings/RagSettingsPanel", () => ({ RagSettingsPanel: () => null }));
vi.mock("../components/settings/MCPManager", () => ({ MCPManager: () => null }));
vi.mock("../components/settings/LongTermMemoryEditor", () => ({ LongTermMemoryEditor: () => null }));
vi.mock("../components/settings/SkillsManager", () => ({ SkillsManager: () => null }));
vi.mock("../components/settings/TokenUsagePanel", () => ({ TokenUsagePanel: () => null }));
vi.mock("../components/settings/AuditLogPanel", () => ({ AuditLogPanel: () => null }));
vi.mock("../components/shared/EmptyState", () => ({ EmptyState: () => null }));

async function importSessionStore() {
  vi.resetModules();
  return (await import("./useSessionStore")).useSessionStore;
}

let cleanupRenderedPage: (() => void) | null = null;

function makeSession(overrides: Record<string, unknown> = {}) {
  return {
    id: "context-window-session",
    title: "上下文上限测试",
    preview: "",
    model: "",
    createdAt: 1,
    project: "",
    pinned: false,
    msgCount: 0,
    contextWindow: DEFAULT_WINDOW,
    ...overrides,
  };
}

describe("session context window up to 1M", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    mocks.apiGet.mockReset().mockResolvedValue({ sessions: [] });
    mocks.apiPost.mockReset().mockResolvedValue({ id: "server-context-window-session" });
    mocks.apiPatch.mockReset().mockResolvedValue({});
    mocks.chatState.activeSessionId = "";
  });

  afterEach(() => {
    cleanupRenderedPage?.();
    cleanupRenderedPage = null;
  });

  it("keeps a 1M window during hydration and clamps above 1M", async () => {
    localStorage.setItem("hwrag_sessions", JSON.stringify([
      makeSession({ contextWindow: 1048576 }),
      makeSession({ id: "oversized", contextWindow: 2097152 }),
    ]));

    const store = await importSessionStore();

    expect(store.getState().sessions[0].contextWindow).toBe(CONTEXT_WINDOW_CAP);
    expect(store.getState().sessions[1].contextWindow).toBe(CONTEXT_WINDOW_CAP);
  });

  it("stores and sends API window values up to 1M", async () => {
    mocks.apiGet.mockResolvedValueOnce({
      sessions: [{
        id: "context-window-session",
        title: "上下文上限测试",
        context_window: 1048576,
      }],
    });
    const store = await importSessionStore();
    await store.getState().initSessions();

    expect(store.getState().sessions[0].contextWindow).toBe(CONTEXT_WINDOW_CAP);
    expect(JSON.parse(localStorage.getItem("hwrag_sessions") || "[]")[0].contextWindow)
      .toBe(CONTEXT_WINDOW_CAP);

    mocks.chatState.activeSessionId = "context-window-session";
    store.getState().setContextWindow(1048576);
    expect(mocks.apiPatch).toHaveBeenCalledWith("sessions/context-window-session", {
      context_window: CONTEXT_WINDOW_CAP,
    });

    store.getState().setContextWindow(2097152);
    expect(mocks.apiPatch).toHaveBeenLastCalledWith("sessions/context-window-session", {
      context_window: CONTEXT_WINDOW_CAP,
    });

    store.getState().setContextWindow(32768);
    expect(mocks.apiPatch).toHaveBeenLastCalledWith("sessions/context-window-session", {
      context_window: 32768,
    });
  });

  it("creates new sessions at the default 1M window", async () => {
    const store = await importSessionStore();
    const sessionId = await store.getState().newSession();

    expect(mocks.apiPost).toHaveBeenCalledWith("sessions", expect.objectContaining({
      context_window: DEFAULT_WINDOW,
    }));
    expect(sessionId).toBe("server-context-window-session");
    expect(store.getState().sessions[0].contextWindow).toBe(DEFAULT_WINDOW);
  });

  it("exposes 64K through 1M in SettingsPage", async () => {
    localStorage.setItem("hwrag_sessions", JSON.stringify([makeSession()]));
    const store = await importSessionStore();
    mocks.chatState.activeSessionId = "context-window-session";
    store.setState({ sessions: [makeSession()] });

    const React = await import("react");
    const { cleanup, render, screen } = await import("@testing-library/react");
    cleanupRenderedPage = cleanup;
    const { SettingsPage } = await import("../components/settings/SettingsPage");
    render(React.createElement(SettingsPage));

    const select = screen.getAllByRole("combobox").find((element) =>
      Array.from((element as HTMLSelectElement).options).some((option) => option.value === String(DEFAULT_WINDOW)),
    ) as HTMLSelectElement | undefined;
    expect(select).toBeDefined();
    expect(Array.from(select!.options).map((option) => option.textContent))
      .toEqual(["64K", "128K", "256K", "512K", "1M"]);
    expect(screen.getByRole("option", { name: "1M" })).toBeTruthy();
  });
});
