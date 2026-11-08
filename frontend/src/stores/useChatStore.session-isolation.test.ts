import { act, cleanup, render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatSSEEvent, ErrorSSEEvent } from "../types/api";
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

type CapturedSseCallbacks = {
  onEvent: (event: ChatSSEEvent) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
};

const sessionA = "isolation-session-a";
const sessionB = "isolation-session-b";

const sessionStreamError: ErrorSSEEvent = {
  type: "error",
  code: "AGENT_STREAM_ERROR",
  message: "本轮 Agent 执行失败，请重试。",
  detail: "该错误只属于产生它的会话。",
};

const messagesA: Message[] = [
  { id: "a-user", role: "user", content: "A 的问题", timestamp: 1 },
  { id: "a-assistant", role: "assistant", content: "A 的回答", timestamp: 2 },
];
const messagesB: Message[] = [
  { id: "b-user", role: "user", content: "B 的问题", timestamp: 3 },
  { id: "b-assistant", role: "assistant", content: "B 的回答", timestamp: 4 },
];

function makeSession(id: string): Session {
  return {
    id,
    title: id,
    preview: "",
    model: "",
    createdAt: 1,
    project: "",
    pinned: false,
    msgCount: 0,
    contextWindow: 262144,
  };
}

async function importChatStore() {
  return (await import("./useChatStore")).useChatStore;
}

async function startStream(store: Awaited<ReturnType<typeof importChatStore>>, prompt: string) {
  await store.getState().sendMessage(prompt);
  const callbacks = apiSSEMock.mock.calls.at(-1)?.[2] as CapturedSseCallbacks | undefined;
  if (!callbacks) throw new Error("chat SSE callbacks were not captured");
  return callbacks;
}

describe("per-session chat error and confirmation isolation", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    apiGetMock.mockReset().mockImplementation(async (path: string) => {
      if (path === `sessions/${sessionA}/messages`) return { messages: [] };
      if (path === `sessions/${sessionB}/messages`) return { messages: [] };
      throw new Error(`unexpected GET: ${path}`);
    });
    apiPostMock.mockReset().mockResolvedValue({ success: true, data: {} });
    apiPutMock.mockReset().mockResolvedValue({});
    apiPatchMock.mockReset().mockResolvedValue({});
    apiDeleteMock.mockReset().mockResolvedValue({});
    apiSSEMock.mockReset().mockImplementation(() => Promise.resolve());
    localStorage.setItem("hwrag_sessions", JSON.stringify([makeSession(sessionA), makeSession(sessionB)]));
    localStorage.setItem("hwrag_active_session", JSON.stringify(sessionA));
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("does not show session A's terminal error over session B", async () => {
    const store = await importChatStore();
    store.setState({
      activeSessionId: sessionA,
      messages: messagesA,
      sessionMessages: { [sessionA]: messagesA, [sessionB]: messagesB },
    });

    const callbacks = await startStream(store, "触发隔离错误");
    callbacks.onEvent(sessionStreamError);

    expect(store.getState().streamingError).toMatchObject({ code: "AGENT_STREAM_ERROR" });
    expect(store.getState().streamingErrorBySession[sessionA]).toMatchObject({
      code: "AGENT_STREAM_ERROR",
    });

    store.getState().setActiveSession(sessionB);
    expect(store.getState().activeSessionId).toBe(sessionB);
    expect(store.getState().streamingError).toBeNull();
    expect(store.getState().messages.map((message) => message.id)).toEqual(messagesB.map((message) => message.id));

    store.getState().setActiveSession(sessionA);
    expect(store.getState().streamingError).toMatchObject({ code: "AGENT_STREAM_ERROR" });
  });

  it("keeps a background run's error out of the session the user switched to", async () => {
    const store = await importChatStore();
    store.setState({
      activeSessionId: sessionA,
      messages: messagesA,
      sessionMessages: { [sessionA]: messagesA, [sessionB]: messagesB },
    });

    const callbacks = await startStream(store, "后台运行中切走");
    store.getState().setActiveSession(sessionB);
    callbacks.onEvent(sessionStreamError);

    expect(store.getState().activeSessionId).toBe(sessionB);
    expect(store.getState().streamingError).toBeNull();
    expect(store.getState().streamingErrorBySession[sessionA]).toMatchObject({
      code: "AGENT_STREAM_ERROR",
    });

    store.getState().setActiveSession(sessionA);
    expect(store.getState().streamingError).toMatchObject({ code: "AGENT_STREAM_ERROR" });
  });

  it("shows the HITL confirmation only for the session that owns the paused run", async () => {
    const store = await importChatStore();
    store.setState({
      activeSessionId: sessionA,
      messages: messagesA,
      sessionMessages: { [sessionA]: messagesA, [sessionB]: messagesB },
      streamingSessionId: sessionA,
      isStreaming: true,
    });

    store.setState({
      pendingConfirm: {
        count: 1,
        sessionId: sessionA,
        calls: [{ name: "write_file", args: { path: "main.c" }, call_id: "call-a", risk_level: "high" }],
      },
    });

    const ConfirmDialog = (await import("../components/chat/ConfirmDialog")).default;
    const view = render(createElement(ConfirmDialog));
    expect(view.container.querySelector(".confirm-sheet")).not.toBeNull();

    act(() => store.getState().setActiveSession(sessionB));
    expect(view.container.querySelector(".confirm-sheet")).toBeNull();
    expect(store.getState().pendingConfirm?.sessionId).toBe(sessionA);

    act(() => store.getState().setActiveSession(sessionA));
    expect(view.container.querySelector(".confirm-sheet")).not.toBeNull();
  });

  it("applies backend messages even when a stale streaming flag has no live request", async () => {
    const backendMessages: Message[] = [
      { id: "server-user", role: "user", content: "服务端问题", timestamp: 10 },
      { id: "server-assistant", role: "assistant", content: "服务端回答", timestamp: 11 },
    ];
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === `sessions/${sessionA}/messages`) {
        return {
          messages: backendMessages.map((message) => ({
            id: message.id,
            role: message.role,
            content: message.content as string,
            sources: [],
            created_at: "2026-11-08T00:00:00Z",
          })),
        };
      }
      throw new Error(`unexpected GET: ${path}`);
    });

    const store = await importChatStore();
    store.setState({
      activeSessionId: sessionA,
      messages: messagesA,
      sessionMessages: { [sessionA]: messagesA },
      isStreaming: true,
      streamingSessionId: sessionA,
      currentSseRequest: null,
    });

    await store.getState().fetchMessages(sessionA);

    expect(store.getState().messages.map((message) => message.id)).toEqual(["server-user", "server-assistant"]);
    expect(store.getState().messages.map((message) => message.content)).toEqual(["服务端问题", "服务端回答"]);
    expect(store.getState().sessionMessages[sessionA].map((message) => message.id)).toEqual([
      "server-user",
      "server-assistant",
    ]);
  });

  it("still skips the snapshot while a live stream owns the session", async () => {
    apiGetMock.mockImplementation(async () => ({
      messages: [{ id: "server-user", role: "user", content: "服务端问题", sources: [], created_at: "2026-11-08T00:00:00Z" }],
    }));

    const store = await importChatStore();
    const controller = new AbortController();
    store.setState({
      activeSessionId: sessionA,
      messages: messagesA,
      sessionMessages: { [sessionA]: messagesA },
      isStreaming: true,
      streamingSessionId: sessionA,
      currentSseRequest: controller,
    });

    await store.getState().fetchMessages(sessionA);

    expect(store.getState().messages.map((message) => message.id)).toEqual(messagesA.map((message) => message.id));
  });

  it("clears the session's error when its next run completes", async () => {
    const store = await importChatStore();
    store.setState({
      activeSessionId: sessionA,
      messages: messagesA,
      sessionMessages: { [sessionA]: messagesA },
    });

    const firstCallbacks = await startStream(store, "第一次失败");
    firstCallbacks.onEvent(sessionStreamError);
    expect(store.getState().streamingErrorBySession[sessionA]).toBeTruthy();

    const secondCallbacks = await startStream(store, "第二次成功");
    secondCallbacks.onEvent({ type: "text", content: "完成" });
    secondCallbacks.onEvent({ type: "done", success: true, completed: true });

    expect(store.getState().streamingError).toBeNull();
    expect(store.getState().streamingErrorBySession[sessionA]).toBeUndefined();
  });
});
