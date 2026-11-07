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
vi.mock("../../stores/useLogStore", () => ({ useLogStore: { getState: () => ({ log: vi.fn() }) } }));
vi.mock("../../i18n", () => ({ useI18n: () => ({ t: (key: string) => key, lang: "zh" }) }));
vi.mock("../shared/TemplatePanel", () => ({ TemplatePanel: () => null }));

function openPermissionDropdown() {
  fireEvent.click(document.getElementById("permissionSelector")!);
}

function clickPermission(label: string) {
  const option = screen.getAllByText(label).find(
    (element) => element.closest("#permissionDropdown"),
  );
  fireEvent.click(option!.closest("button")!);
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

    clickPermission("plan");

    expect(chatState.setPlanMode).toHaveBeenCalledWith(true);
    expect(settingsState.updateSetting).not.toHaveBeenCalled();
    expect(chatState.setPlanMode).toHaveBeenCalledTimes(1);
  });

  it("manual clears plan mode and persists the ask mode", () => {
    render(<InputBar />);
    openPermissionDropdown();

    clickPermission("manual");

    expect(chatState.setPlanMode).toHaveBeenCalledWith(false);
    expect(settingsState.updateSetting).toHaveBeenCalledWith("permissionMode", "default");
  });

  it("auto persists the acceptEdits mode", () => {
    render(<InputBar />);
    openPermissionDropdown();

    clickPermission("auto");

    expect(chatState.setPlanMode).toHaveBeenCalledWith(false);
    expect(settingsState.updateSetting).toHaveBeenCalledWith("permissionMode", "acceptEdits");
  });

  it("YOLO persists the no-confirmation mode", () => {
    render(<InputBar />);
    openPermissionDropdown();

    clickPermission("YOLO");

    expect(chatState.setPlanMode).toHaveBeenCalledWith(false);
    expect(settingsState.updateSetting).toHaveBeenCalledWith("permissionMode", "yolo");
  });

  it("shows the plan label when plan mode is active", () => {
    chatState.planMode = true;
    render(<InputBar />);

    expect(document.getElementById("permissionLabel")?.textContent).toBe("plan");
  });
});
