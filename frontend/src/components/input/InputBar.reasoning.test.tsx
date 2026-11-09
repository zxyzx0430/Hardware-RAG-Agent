import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InputBar } from "./InputBar";

const { chatState, appState, settingsState, sessionState } = vi.hoisted(() => ({
  chatState: {
    sendMessage: vi.fn(), stopStreaming: vi.fn(), isStreaming: false,
    activeSessionId: "session-a", drafts: { "session-a": "" },
    setDraft: vi.fn(), clearDraft: vi.fn(), sessionAttachments: {},
    setSessionAttachments: vi.fn(), clearSessionAttachments: vi.fn(),
    planMode: false, setPlanMode: vi.fn(), leaveAgentPlanMode: vi.fn().mockResolvedValue(true),
  },
  appState: {
    quotedMsg: null, setQuotedMsg: vi.fn(),
    templatePanelOpen: false, setTemplatePanelOpen: vi.fn(),
  },
  settingsState: {
    providers: [] as Array<Record<string, unknown>>,
    chatProviderId: "provider-a",
    chatModel: "glm",
    setChatModel: vi.fn(),
    permissionMode: "default",
    updateSetting: vi.fn(),
  },
  sessionState: {
    sessions: [{ id: "session-a", model: "glm", reasoningMode: "high" }],
    updateSessionMeta: vi.fn(),
    setContextWindow: vi.fn(),
  },
}));

vi.mock("../../stores/useChatStore", () => ({ useChatStore: () => chatState }));
vi.mock("../../stores/useAppStore", () => ({
  useAppStore: Object.assign(() => appState, { getState: () => appState }),
}));
vi.mock("../../stores/useSettingsStore", () => ({ useSettingsStore: () => settingsState }));
vi.mock("../../stores/useSessionStore", () => ({
  useSessionStore: () => sessionState,
  CONTEXT_WINDOW_256K: 262144,
  CONTEXT_WINDOW_1M: 1048576,
}));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => ({ showWarning: vi.fn() }) },
}));
vi.mock("../../stores/useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));
vi.mock("../../i18n", () => ({ useI18n: () => ({ t: (key: string) => key, lang: "zh" }) }));
vi.mock("../shared/TemplatePanel", () => ({ TemplatePanel: () => null }));

function openReasoningDropdown() {
  fireEvent.click(document.getElementById("reasoningSelector")!);
}

beforeEach(() => {
  sessionState.updateSessionMeta.mockReset();
  sessionState.sessions = [{ id: "session-a", model: "glm", reasoningMode: "high" }];
  settingsState.providers = [{
    id: "provider-a",
    name: "Provider",
    verified: true,
    models: ["glm"],
    modelCapabilities: {
      glm: {
        reasoning: true,
        canDisable: true,
        effortSupported: true,
        efforts: ["low", "medium", "high"],
        format: "zai",
      },
    },
  }];
});

afterEach(cleanup);

describe("InputBar reasoning selector", () => {
  it("shows the session mode and saves a per-session change", () => {
    render(<InputBar />);

    expect(document.getElementById("reasoningSelector")?.textContent).toContain("思考: 高");
    openReasoningDropdown();
    const option = screen.getAllByText("低").find(
      (element) => element.closest("#reasoningDropdown"),
    );
    fireEvent.click(option!.closest("button")!);

    expect(sessionState.updateSessionMeta).toHaveBeenCalledWith(
      "session-a",
      { reasoningMode: "low" },
    );
  });

  it("keeps auto-only for a combo model with no declared capability", () => {
    sessionState.sessions = [{ id: "session-a", model: "Text", reasoningMode: "high" }];
    settingsState.providers = [{
      id: "provider-a",
      name: "Provider",
      verified: true,
      models: ["Text"],
      modelCapabilities: {},
    }];
    render(<InputBar />);

    expect(document.getElementById("reasoningSelector")?.textContent).toContain("思考: 自动");
    openReasoningDropdown();
    expect(document.getElementById("reasoningDropdown")?.textContent).toContain("组合模型未声明思考能力");
    expect(screen.queryAllByText("高").filter((el) => el.closest("#reasoningDropdown"))).toHaveLength(0);
  });

  it("marks a known non-reasoning model as unsupported", () => {
    settingsState.providers = [{
      id: "provider-a",
      name: "Provider",
      verified: true,
      models: ["glm"],
      modelCapabilities: {
        glm: {
          reasoning: false,
          canDisable: false,
          effortSupported: false,
          efforts: [],
          format: null,
        },
      },
    }];
    render(<InputBar />);

    openReasoningDropdown();
    expect(document.getElementById("reasoningDropdown")?.textContent).toContain("该模型不支持思考");
  });
});
