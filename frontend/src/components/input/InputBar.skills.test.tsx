import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InputBar } from "./InputBar";
import { useSkillsStore } from "../../stores/useSkillsStore";

const { chatState, appState, settingsState, sessionState, warningMock } = vi.hoisted(() => ({
  chatState: {
    sendMessage: vi.fn(), stopStreaming: vi.fn(), isStreaming: false, activeSessionId: "session-a",
    drafts: { "session-a": "check this" }, setDraft: vi.fn(), clearDraft: vi.fn(),
    sessionAttachments: {}, setSessionAttachments: vi.fn(), clearSessionAttachments: vi.fn(),
  },
  appState: { quotedMsg: null as null, setQuotedMsg: vi.fn(), templatePanelOpen: false, setTemplatePanelOpen: vi.fn() },
  settingsState: { providers: [], chatProviderId: "", chatModel: "model", setChatModel: vi.fn(), permissionMode: "default", updateSetting: vi.fn() },
  sessionState: { sessions: [{ id: "session-a", model: "model" }], updateSessionMeta: vi.fn() },
  warningMock: vi.fn(),
}));

vi.mock("../../stores/useChatStore", () => ({ useChatStore: () => chatState }));
vi.mock("../../stores/useAppStore", () => ({ useAppStore: Object.assign(() => appState, { getState: () => appState }) }));
vi.mock("../../stores/useSettingsStore", () => ({ useSettingsStore: () => settingsState }));
vi.mock("../../stores/useSessionStore", () => ({ useSessionStore: () => sessionState }));
vi.mock("../../stores/useToastStore", () => ({ useToastStore: { getState: () => ({ showWarning: warningMock }) } }));
vi.mock("../../stores/useLogStore", () => ({ useLogStore: { getState: () => ({ log: vi.fn() }) } }));
vi.mock("../../i18n", () => ({ useI18n: () => ({ t: (key: string) => key, lang: "zh" }) }));
vi.mock("../shared/TemplatePanel", () => ({ TemplatePanel: () => null }));

afterEach(cleanup);

beforeEach(() => {
  chatState.sendMessage.mockReset();
  chatState.clearDraft.mockReset();
  chatState.clearSessionAttachments.mockReset();
  appState.setTemplatePanelOpen.mockReset();
  appState.templatePanelOpen = false;
  warningMock.mockReset();
  useSkillsStore.setState({
    skills: [
      { id: "review", name: "Review", description: "Review", content: "# Review", enabled: true },
      { id: "disabled", name: "Disabled", description: "Disabled", content: "# Disabled", enabled: false },
    ],
    loading: false, loaded: true, error: null, preview: null, previewLoading: false,
    importing: false, chatSkillSelections: { "session-a": { mode: "manual", skillIds: ["review", "disabled"] } },
  });
});

describe("InputBar Skills request wiring", () => {
  it("passes only currently enabled manual Skills to the chat request", () => {
    render(<InputBar />);

    fireEvent.click(screen.getByRole("button", { name: "send" }));

    expect(chatState.sendMessage).toHaveBeenCalledWith("check this", undefined, undefined, {
      mode: "manual",
      skillIds: ["review"],
    });
  });

  it("keeps the draft and blocks sending when manual selection has no enabled Skills", () => {
    useSkillsStore.setState({
      skills: [{ id: "disabled", name: "Disabled", description: "Disabled", content: "# Disabled", enabled: false }],
    });
    render(<InputBar />);

    fireEvent.click(screen.getByRole("button", { name: "send" }));

    expect(chatState.sendMessage).not.toHaveBeenCalled();
    expect(chatState.clearDraft).not.toHaveBeenCalled();
    expect(warningMock).toHaveBeenCalledWith("手动 Skills 模式至少需要一个仍处于启用状态的技能");
  });

  it("shows template and attachment names while preserving their button actions", () => {
    render(<InputBar />);

    const templateButton = screen.getByTitle("templateBtn");
    const attachmentButton = screen.getByTitle("attachBtn");
    expect(templateButton.textContent).toContain("templateBtn");
    expect(attachmentButton.textContent).toContain("attachBtn");

    fireEvent.click(templateButton);
    expect(appState.setTemplatePanelOpen).toHaveBeenCalledWith(true);

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    const fileInputClick = vi.spyOn(fileInput, "click");
    fireEvent.click(attachmentButton);
    expect(fileInputClick).toHaveBeenCalledOnce();
  });
});
