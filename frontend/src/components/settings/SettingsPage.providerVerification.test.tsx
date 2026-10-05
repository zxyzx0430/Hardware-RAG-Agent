import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPage } from "./SettingsPage";

const mocks = vi.hoisted(() => {
  const settingsState: {
    providers: Array<{
      id: string;
      name: string;
      baseUrl: string;
      apiKey: string;
      verified: boolean;
      models: string[];
    }>;
    [key: string]: unknown;
  } = {
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
    addProvider: undefined,
    removeProvider: vi.fn(),
    updateProvider: vi.fn(),
    verifyProvider: undefined,
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
  const verifyProviderMock = vi.fn();
  const addProviderMock = vi.fn((name: string, baseUrl: string, apiKey: string) => {
    const provider = {
      id: "new-provider",
      name,
      baseUrl,
      apiKey,
      verified: false,
      models: [],
    };
    settingsState.providers = [...settingsState.providers, provider];
    return provider;
  });
  settingsState.addProvider = addProviderMock;
  settingsState.verifyProvider = verifyProviderMock;

  const appState = {
    setActiveNav: vi.fn(),
    themeMode: "light",
    setThemeMode: vi.fn(),
    lang: "en",
    setLang: vi.fn(),
    chatFontSize: 14,
    setChatFontSize: vi.fn(),
  };
  const chatState = { needsApiKey: false, activeSessionId: "session-1" };
  const sessionState = {
    sessions: [{ id: "session-1", contextWindow: 262144 }],
    setContextWindow: vi.fn(),
    updateSessionMeta: vi.fn(),
  };
  const logState = {
    buffer: [],
    filter: { levels: [], timeRange: 0 },
    setFilter: vi.fn(),
    clear: vi.fn(),
    getFiltered: () => [],
  };
  const modalState = { confirmDialog: vi.fn() };
  const useChatStoreMock = Object.assign(
    vi.fn((selector?: (state: typeof chatState) => unknown) => (selector ? selector(chatState) : chatState)),
    { getState: () => chatState },
  );
  const useSessionStoreMock = vi.fn(
    (selector?: (state: typeof sessionState) => unknown) => (selector ? selector(sessionState) : sessionState),
  );

  return {
    settingsState,
    verifyProviderMock,
    addProviderMock,
    appState,
    chatState,
    sessionState,
    logState,
    modalState,
    useSettingsStoreMock: vi.fn(() => settingsState),
    useAppStoreMock: vi.fn(() => appState),
    useChatStoreMock,
    useSessionStoreMock,
    useLogStoreMock: vi.fn(() => logState),
    useModalStoreMock: vi.fn(() => modalState),
  };
});

vi.mock("../../stores/useSettingsStore", () => ({
  useSettingsStore: mocks.useSettingsStoreMock,
}));
vi.mock("../../stores/useAppStore", () => ({
  useAppStore: mocks.useAppStoreMock,
}));
vi.mock("../../stores/useChatStore", () => ({
  useChatStore: mocks.useChatStoreMock,
}));
vi.mock("../../stores/useSessionStore", () => ({
  useSessionStore: mocks.useSessionStoreMock,
  CONTEXT_WINDOW_256K: 262144,
  CONTEXT_WINDOW_1M: 1048576,
}));
vi.mock("../../stores/useLogStore", () => ({
  useLogStore: mocks.useLogStoreMock,
}));
vi.mock("../../stores/useModalStore", () => ({
  useModalStore: mocks.useModalStoreMock,
}));
vi.mock("../../i18n", () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));
vi.mock("./RagSettingsPanel", () => ({ RagSettingsPanel: () => null }));
vi.mock("./MCPManager", () => ({ MCPManager: () => null }));
vi.mock("./LongTermMemoryEditor", () => ({ LongTermMemoryEditor: () => null }));
vi.mock("./SkillsManager", () => ({ SkillsManager: () => null }));
vi.mock("./TokenUsagePanel", () => ({ TokenUsagePanel: () => null }));
vi.mock("./AuditLogPanel", () => ({ AuditLogPanel: () => null }));
vi.mock("../shared/EmptyState", () => ({ EmptyState: () => null }));

describe("SettingsPage provider verification feedback", () => {
  beforeEach(() => {
    mocks.settingsState.providers = [
      {
        id: "provider-one",
        name: "Provider One",
        baseUrl: "https://one.example/v1",
        apiKey: "key-one",
        verified: true,
        models: ["previous-model"],
      },
      {
        id: "provider-two",
        name: "Provider Two",
        baseUrl: "https://two.example/v1",
        apiKey: "key-two",
        verified: false,
        models: [],
      },
    ];
    mocks.verifyProviderMock.mockReset().mockResolvedValue({ ok: false, kind: "network" });
    mocks.addProviderMock.mockClear();
  });

  afterEach(() => cleanup());

  it("shows the last failure for its provider and hides stale success feedback", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: /Provider One/ }));
    fireEvent.click(screen.getByRole("button", { name: "verify" }));

    expect(await screen.findByText("providerVerifyNetwork")).toBeTruthy();
    expect(screen.queryByText(/verifiedKey/)).toBeNull();
    const failedProviderCard = screen.getByRole("button", { name: /Provider One/ });
    expect(failedProviderCard.textContent).toContain("!");
    expect(failedProviderCard.textContent).not.toContain("✓");

    fireEvent.click(screen.getByRole("button", { name: /Provider Two/ }));
    expect(screen.queryByText("providerVerifyNetwork")).toBeNull();
  });

  it("shows automatic verification failures after creating a provider", async () => {
    mocks.settingsState.providers = [];
    mocks.verifyProviderMock.mockResolvedValue({ ok: false, kind: "model-service" });
    render(<SettingsPage />);

    fireEvent.change(screen.getByPlaceholderText("e.g. My OpenAI"), { target: { value: "New provider" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.openai.com/v1"), {
      target: { value: "https://new.example/v1" },
    });
    fireEvent.change(screen.getAllByPlaceholderText("sk-...")[0], { target: { value: "new-provider-key" } });
    fireEvent.click(screen.getByRole("button", { name: "add" }));

    expect(await screen.findByText("providerVerifyModelService")).toBeTruthy();
    expect(mocks.verifyProviderMock).toHaveBeenCalledWith("new-provider");
    expect(screen.queryByText(/verifiedKey/)).toBeNull();
  });
});
