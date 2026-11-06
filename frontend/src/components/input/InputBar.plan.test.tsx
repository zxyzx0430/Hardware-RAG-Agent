import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InputBar } from "./InputBar";

const { chatState, appState, settingsState, sessionState } = vi.hoisted(() => ({
  chatState: {
    sendMessage: vi.fn(), stopStreaming: vi.fn(), isStreaming: false, activeSessionId: "session-a",
    drafts: { "session-a": "" }, setDraft: vi.fn(), clearDraft: vi.fn(),
    sessionAttachments: {}, setSessionAttachments: vi.fn(), clearSessionAttachments: vi.fn(),
    planMode: false, setPlanMode: vi.fn(),
  },
  appState: {
    quotedMsg: null,
    setQuotedMsg: vi.fn(),
    templatePanelOpen: false,
    setTemplatePanelOpen: vi.fn(),
  },
  settingsState: {
    providers: [],
    chatProviderId: "",
    chatModel: "model",
    setChatModel: vi.fn(),
    permissionMode: "default",
    updateSetting: vi.fn(),
  },
  sessionState: {
    sessions: [{ id: "session-a", model: "model" }],
    updateSessionMeta: vi.fn(),
  },
}));

vi.mock("../../stores/useChatStore", () => ({ useChatStore: () => chatState }));
vi.mock("../../stores/useAppStore", () => ({
  useAppStore: Object.assign(() => appState, { getState: () => appState }),
}));
vi.mock("../../stores/useSettingsStore", () => ({ useSettingsStore: () => settingsState }));
vi.mock("../../stores/useSessionStore", () => ({ useSessionStore: () => sessionState }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => ({ showWarning: vi.fn() }) },
}));
vi.mock("../../stores/useLogStore", () => ({ useLogStore: { getState: () => ({ log: vi.fn() }) } }));
vi.mock("../../i18n", () => ({ useI18n: () => ({ t: (key: string) => key, lang: "zh" }) }));
vi.mock("../shared/TemplatePanel", () => ({ TemplatePanel: () => null }));

function openPermissionDropdown() {
  fireEvent.click(document.getElementById("permissionSelector")!);
}

beforeEach(() => {
  chatState.planMode = false;
  chatState.setPlanMode.mockReset();
  settingsState.permissionMode = "default";
  settingsState.updateSetting.mockReset();
});

afterEach(cleanup);

describe("InputBar plan mode", () => {
  it("selects plan mode without writing the persisted permission setting", () => {
    render(<InputBar />);
    openPermissionDropdown();

    fireEvent.click(screen.getByRole("button", { name: "permissionPlan" }));

    expect(chatState.setPlanMode).toHaveBeenCalledWith(true);
    expect(settingsState.updateSetting).not.toHaveBeenCalled();
    expect(chatState.setPlanMode).toHaveBeenCalledTimes(1);
  });

  it("selecting a normal mode clears plan mode and persists the mode", () => {
    render(<InputBar />);
    openPermissionDropdown();

    fireEvent.click(screen.getByRole("button", { name: "permissionAsk" }));

    expect(chatState.setPlanMode).toHaveBeenCalledWith(false);
    expect(settingsState.updateSetting).toHaveBeenCalledWith("permissionMode", "default");
  });

  it("shows the plan label when plan mode is active", () => {
    chatState.planMode = true;
    render(<InputBar />);

    expect(document.getElementById("permissionLabel")?.textContent).toBe("permissionPlan");
  });
});
