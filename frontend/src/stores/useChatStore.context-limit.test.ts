import { createElement } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BuildSSEEvent, ChatRequest, ChatSSEEvent, ErrorSSEEvent } from "../types/api";
import type { Message, Session } from "../types/session";

const { apiGetMock, apiPostMock, apiPutMock, apiPatchMock, apiDeleteMock, apiSSEMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
  apiSSEMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  apiSSE: apiSSEMock,
}));

vi.mock("../components/chat/UserMessageRow", () => ({ UserMessageRow: () => null }));
vi.mock("../components/chat/ActivityBlock", () => ({ default: () => null }));
vi.mock("../components/chat/ConfirmDialog", () => ({ default: () => null }));
vi.mock("../components/chat/LoadingState", () => ({ LoadingState: () => null }));
vi.mock("../components/chat/EmptyState", () => ({ EmptyState: () => null }));

type CapturedSseCallbacks = {
  onEvent: (event: ChatSSEEvent | BuildSSEEvent) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
};

const sessionId = "context-limit-256k-session";
const session: Session = {
  id: sessionId,
  title: "256K 上下文会话",
  preview: "",
  model: "",
  createdAt: 1,
  project: "",
  pinned: false,
  msgCount: 0,
  contextWindow: 262144,
};
const contextLimitError: ErrorSSEEvent = {
  type: "error",
  code: "CONTEXT_LIMIT",
  message: "上下文超过当前模型可处理上限，本轮请求已停止。请缩短对话历史或开启新会话后重试。",
  detail: "本次请求已停止，未自动缩减上下文或重试。",
};

let capturedCallbacks: CapturedSseCallbacks | null = null;

async function importChatModules() {
  const { useChatStore } = await import("./useChatStore");
  const { useSessionStore } = await import("./useSessionStore");
  const { ChatArea } = await import("../components/chat/ChatArea");
  return { useChatStore, useSessionStore, ChatArea };
}

describe("typed 256K context-limit error", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    capturedCallbacks = null;
    apiGetMock.mockReset().mockResolvedValue({ messages: [] });
    apiPostMock.mockReset().mockResolvedValue({ success: true, data: {} });
    apiPutMock.mockReset().mockResolvedValue({});
    apiPatchMock.mockReset().mockResolvedValue({});
    apiDeleteMock.mockReset().mockResolvedValue({});
    apiSSEMock.mockReset().mockImplementation((
      _path: string,
      _body: ChatRequest,
      callbacks: CapturedSseCallbacks,
    ) => {
      capturedCallbacks = callbacks;
      return Promise.resolve();
    });

    localStorage.setItem("hwrag_sessions", JSON.stringify([session]));
    localStorage.setItem("hwrag_active_session", JSON.stringify(sessionId));
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the typed limit as an explicit error without replaying chat", async () => {
    const { useChatStore, useSessionStore, ChatArea } = await importChatModules();
    const activeSession = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
    expect(activeSession?.contextWindow).toBe(262144);

    const { container } = render(createElement(ChatArea));
    await act(async () => {
      await useChatStore.getState().sendMessage("在 256K 会话上限内提交问题");
    });

    expect(apiSSEMock).toHaveBeenCalledTimes(1);
    expect(apiSSEMock.mock.calls[0][0]).toBe("chat");
    expect(apiSSEMock.mock.calls[0][1]).toMatchObject({ session_id: sessionId });
    const callbacks = capturedCallbacks;
    if (!callbacks) throw new Error("chat SSE callbacks were not captured");

    await act(async () => {
      callbacks.onEvent(contextLimitError);
    });

    await waitFor(() => {
      expect(useChatStore.getState().pendingSaveBySession[sessionId]).toBeUndefined();
      expect(apiPostMock.mock.calls.filter(([path]) => path === `sessions/${sessionId}/messages`)).toHaveLength(2);
    });

    const assistantMessage = useChatStore.getState().messages.at(-1);
    expect(useChatStore.getState().streamingError).toEqual({
      code: "CONTEXT_LIMIT",
      message: contextLimitError.message,
      detail: contextLimitError.detail,
    });
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(assistantMessage?.content).toBe(`❌ ${contextLimitError.message}`);
    expect(container.querySelector(".error-block")?.textContent).toContain(contextLimitError.message);
    expect(screen.getByText(contextLimitError.detail!)).toBeTruthy();
    expect(screen.queryByText("普通助手回答")).toBeNull();
    // No unconditional retry: it would replay the same oversized history.
    expect(screen.queryByText("重试")).toBeNull();
    expect(screen.getByText(/新建会话或缩短上下文/)).toBeTruthy();
    expect(apiSSEMock).toHaveBeenCalledTimes(1);
    expect(apiPostMock.mock.calls.every(([path]) => path === `sessions/${sessionId}/messages`)).toBe(true);
  });

  it("finalizes a manual HITL resume on context limit and clears a pending confirmation", async () => {
    const { useChatStore, ChatArea } = await importChatModules();
    const history: Message[] = [
      { id: "old-user", role: "user", content: "此前问题", timestamp: 1 },
      { id: "old-assistant", role: "assistant", content: "此前回答", timestamp: 2 },
      { id: "resume-user", role: "user", content: "继续当前任务", timestamp: 3 },
      {
        id: "resume-assistant",
        role: "assistant",
        content: "此前部分回答",
        timestamp: 4,
        sources: [{ id: "resume-source", title: "既有来源", doc: "manual.pdf", page: 2, score: 0.9, excerpt: "保留来源" }],
      },
    ];
    const confirmCall = {
      name: "write_file",
      args: { path: "output.txt" },
      call_id: "initial-confirm",
      risk_level: "high" as const,
    };
    useChatStore.setState({
      messages: history,
      sessionMessages: { [sessionId]: history },
      activeSessionId: sessionId,
      streamingSessionId: sessionId,
      _lastAgentPayload: { messages: [] } as ChatRequest,
      pendingConfirm: { calls: [confirmCall], count: 1, sessionId },
    });

    const { container } = render(createElement(ChatArea));
    await act(async () => {
      useChatStore.getState().resumeAgent("allow");
    });
    expect(apiSSEMock).toHaveBeenCalledTimes(1);
    expect(apiSSEMock.mock.calls[0][0]).toBe("agent-sandbox/resume");
    expect(apiSSEMock.mock.calls[0][1]).toMatchObject({ decision: "allow", call_id: "initial-confirm" });
    expect(useChatStore.getState().pendingConfirm).toBeNull();

    const callbacks = capturedCallbacks;
    if (!callbacks) throw new Error("resume SSE callbacks were not captured");
    await act(async () => {
      callbacks.onEvent({ type: "text", content: "，续跑中已生成" });
      callbacks.onEvent({
        type: "tool_confirm_required",
        calls: [{ ...confirmCall, call_id: "second-confirm" }],
        count: 1,
      });
      expect(useChatStore.getState().pendingConfirm?.calls[0]?.call_id).toBe("second-confirm");
      callbacks.onEvent(contextLimitError);
      callbacks.onEvent({ type: "done", success: false, completed: false });
      callbacks.onDone?.();
    });

    await waitFor(() => {
      expect(apiPostMock.mock.calls.filter(([path]) => path === `sessions/${sessionId}/messages`)).toHaveLength(2);
    });

    const state = useChatStore.getState();
    expect(state.messages.map((message) => message.id)).toEqual(history.map((message) => message.id));
    expect(state.messages[0].content).toBe("此前问题");
    expect(state.messages[1].content).toBe("此前回答");
    expect(state.messages[3].content).toBe(`此前部分回答，续跑中已生成\n\n❌ ${contextLimitError.message}`);
    expect(state.messages[3].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "resume-source" }),
    ]));
    expect(state.messages[3].activity?.status).toBe("error");
    expect(state.streamingError).toEqual({
      code: "CONTEXT_LIMIT",
      message: contextLimitError.message,
      detail: contextLimitError.detail,
    });
    expect(container.querySelector(".error-block")?.textContent).toContain(contextLimitError.message);
    expect(state.isStreaming).toBe(false);
    expect(state.isCompressing).toBe(false);
    expect(state.streamingSessionId).toBeNull();
    expect(state.streamingStartTime).toBeNull();
    expect(state.currentSseRequest).toBeNull();
    expect(state.pendingConfirm).toBeNull();
    expect(state._lastAgentPayload).toBeNull();
    expect(apiSSEMock.mock.calls.map(([path]) => path)).toEqual(["agent-sandbox/resume"]);
    expect(apiPostMock.mock.calls.every(([path]) => path === `sessions/${sessionId}/messages`)).toBe(true);
  });
});
