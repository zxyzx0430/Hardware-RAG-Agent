import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BuildSSEEvent, ChatRequest, ChatSSEEvent } from "../../types/api";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useSkillsStore } from "../../stores/useSkillsStore";
import { useToastStore } from "../../stores/useToastStore";
import { ToastContainer } from "../shared/Toast";
import { InputBar } from "./InputBar";

const { apiGetMock, apiPostMock, apiPutMock, apiPatchMock, apiDeleteMock, apiSSEMock, broadcastPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
  apiSSEMock: vi.fn(),
  broadcastPostMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  apiSSE: apiSSEMock,
}));

vi.mock("../../utils/broadcast", () => ({
  post: broadcastPostMock,
  on: vi.fn(() => vi.fn()),
}));

vi.mock("../../stores/useWorkbenchBridge", () => ({
  handleToolCallEvent: vi.fn(),
  handleToolResultEvent: vi.fn(),
  handleCompileLogEvent: vi.fn(),
  handleProgressEvent: vi.fn(),
}));

vi.mock("../../i18n", () => ({
  useI18n: () => ({
    t: (key: string) => {
      const labels: Record<string, string> = { send: "发送", attachBtn: "附件", templateBtn: "模板" };
      return labels[key] ?? key;
    },
    lang: "zh",
  }),
}));

vi.mock("../shared/TemplatePanel", () => ({ TemplatePanel: () => null }));
vi.mock("./SkillModeSelector", () => ({ SkillModeSelector: () => null }));

type CapturedChatCallbacks = {
  onEvent: (event: ChatSSEEvent | BuildSSEEvent) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
};

const createdSessionId = "inputbar-first-send-session";
const sessionCreateError = "新建会话失败，输入已保留，请重试";
let createAttempts = 0;
let capturedCallbacks: CapturedChatCallbacks | null = null;
let persistedMessages: Array<Record<string, unknown>> = [];

beforeEach(() => {
  localStorage.clear();
  createAttempts = 0;
  capturedCallbacks = null;
  persistedMessages = [];

  apiGetMock.mockReset().mockResolvedValue({ messages: [] });
  apiPostMock.mockReset().mockImplementation(async (path: string, body?: Record<string, unknown>) => {
    if (path === "sessions") {
      createAttempts += 1;
      if (createAttempts === 1) throw new Error("simulated session create failure");
      return { id: createdSessionId };
    }
    if (path === `sessions/${createdSessionId}/messages`) {
      persistedMessages.push(body ?? {});
      return { success: true, data: { id: body?.id } };
    }
    return { success: true, data: {} };
  });
  apiPutMock.mockReset().mockResolvedValue({});
  apiPatchMock.mockReset().mockResolvedValue({});
  apiDeleteMock.mockReset().mockResolvedValue({});
  broadcastPostMock.mockReset();
  apiSSEMock.mockReset().mockImplementation((
    _path: string,
    _body: ChatRequest,
    callbacks: CapturedChatCallbacks,
  ) => {
    capturedCallbacks = callbacks;
    return Promise.resolve();
  });

  useSessionStore.setState({ sessions: [], activeProject: "all", initialized: true, creatingSession: false });
  useChatStore.setState({
    activeSessionId: "",
    messages: [],
    sessionMessages: {},
    isStreaming: false,
    streamingSessionId: null,
    currentSseRequest: null,
    drafts: {},
    sessionAttachments: {},
    pendingSaveBySession: {},
    selectedKbIds: [],
    kbScopeIssue: null,
  });
  useSettingsStore.setState({ providers: [], chatProviderId: "", chatModel: "", permissionMode: "default" });
  useSkillsStore.setState({ skills: [], chatSkillSelections: {} });
  useAppStore.setState({ quotedMsg: null, templatePanelOpen: false });
  useToastStore.setState({ toasts: [] });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});

afterEach(() => {
  cleanup();
  useToastStore.setState({ toasts: [] });
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("InputBar first-send recovery", () => {
  it("keeps the real composer draft and attachment visible after session creation fails, then sends once on retry", async () => {
    const draft = "首次创建失败后仍应保留的问题";
    const attachment = new File(["synthetic attachment"], "notes.txt", { type: "text/plain" });
    const { container } = render(<><InputBar /><ToastContainer /></>);
    const textarea = container.querySelector<HTMLTextAreaElement>("#inputArea");
    const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
    if (!textarea || !fileInput) throw new Error("InputBar composer controls were not rendered");

    fireEvent.change(textarea, { target: { value: draft } });
    fireEvent.change(fileInput, { target: { files: [attachment] } });
    await screen.findByText("notes.txt");

    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain(sessionCreateError);
      expect(textarea.value).toBe(draft);
      expect(screen.getByText("notes.txt")).toBeTruthy();
    });
    expect(useChatStore.getState().drafts[""]).toBe(draft);
    expect(useChatStore.getState().sessionAttachments[""]).toHaveLength(1);
    expect(useSessionStore.getState().sessions).toHaveLength(0);
    expect(createAttempts).toBe(1);
    expect(apiSSEMock).not.toHaveBeenCalled();
    expect(persistedMessages).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(apiSSEMock).toHaveBeenCalledTimes(1));
    expect(createAttempts).toBe(2);
    expect(useSessionStore.getState().sessions.map((session) => session.id)).toEqual([createdSessionId]);
    expect(useChatStore.getState().activeSessionId).toBe(createdSessionId);
    expect(apiSSEMock.mock.calls[0][0]).toBe("chat");
    expect(apiSSEMock.mock.calls[0][1]).toMatchObject({
      session_id: createdSessionId,
      attachments: [expect.objectContaining({ name: "notes.txt", type: "text/plain" })],
    });

    const callbacks = capturedCallbacks;
    if (!callbacks) throw new Error("chat SSE callbacks were not captured");
    await act(async () => {
      callbacks.onEvent({ type: "text", content: "模拟回答" });
      callbacks.onDone?.();
    });

    await waitFor(() => expect(persistedMessages).toHaveLength(2));
    expect(persistedMessages.map((message) => message.role)).toEqual(["user", "assistant"]);
    expect(apiSSEMock).toHaveBeenCalledTimes(1);
    expect(useChatStore.getState().activeSessionId).toBe(createdSessionId);
    expect(textarea.value).toBe("");
    expect(screen.queryByText("notes.txt")).toBeNull();
  });
});
