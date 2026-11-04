import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ChatRequest, ChatSSEEvent } from "../types/api";
import type { Message } from "../types/session";

const {
  apiSSEPaths,
  apiSSEBodies,
  apiPostMock,
  apiGetMock,
  newSessionMock,
  historyModeMock,
  sessionState,
  appState,
  setQuotedMsgMock,
} = vi.hoisted(() => {
  const appState: { quotedMsg: unknown } = { quotedMsg: null };
  return {
    apiSSEPaths: [] as string[],
    apiSSEBodies: [] as unknown[],
    apiPostMock: vi.fn(),
    apiGetMock: vi.fn(),
    newSessionMock: vi.fn(),
    historyModeMock: vi.fn(),
    sessionState: {
      creatingSession: false,
      sessions: [{ id: "s1", title: "新对话", model: "gpt-4o" }],
    },
    appState,
    setQuotedMsgMock: vi.fn((message: unknown) => { appState.quotedMsg = message; }),
  };
});

let capturedCallbacks: {
  onEvent: (event: ChatSSEEvent) => void;
  onDone?: () => void;
  onError?: (err: Error) => void;
} | null = null;

vi.mock("../api/client", () => ({
  apiSSE: vi.fn((path, body, callbacks) => {
    apiSSEPaths.push(path);
    apiSSEBodies.push(body);
    capturedCallbacks = callbacks;
    return Promise.resolve();
  }),
  apiPost: apiPostMock,
  apiGet: apiGetMock,
  apiDelete: vi.fn(),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
}));

vi.mock("../utils/broadcast", () => ({
  post: vi.fn(),
  on: vi.fn(() => vi.fn()),
}));

vi.mock("../utils/rollingHistoryClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../utils/rollingHistoryClient")>();
  return {
    ...actual,
    getSessionHistoryMode: historyModeMock,
  };
});

vi.mock("../stores/useSettingsStore", () => ({
  useSettingsStore: {
    getState: () => ({
      topK: 5,
      relevanceThreshold: 0,
      temperature: 0.2,
      systemPrompt: "",
      longTermMemory: "",
      chatModel: "gpt-4o",
      maxTokens: 8192,
      providers: [],
      chatProviderId: "",
      webSearchConfig: { apiKey: "", baseUrl: "" },
      resolveChatCreds: () => ({ providerId: "", model: "gpt-4o", baseUrl: "https://api.openai.com/v1", apiKey: "" }),
      resolveVisionCreds: () => ({ model: "", baseUrl: "", apiKey: "" }),
      resolveImageCreds: () => ({ model: "", baseUrl: "", apiKey: "" }),
    }),
  },
}));

vi.mock("../stores/useLogStore", () => ({
  useLogStore: {
    getState: () => ({ log: vi.fn() }),
  },
}));

vi.mock("../stores/useSessionStore", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("./useSessionStore");
  return {
    ...actual,
    useSessionStore: {
      getState: () => ({
        sessions: sessionState.sessions,
        creatingSession: sessionState.creatingSession,
        newSession: newSessionMock,
        updateSessionMeta: vi.fn(),
      }),
      setState: vi.fn(),
    },
  };
});

vi.mock("../stores/useAppStore", () => ({
  useAppStore: {
    getState: () => ({
      setRightPanelOpen: vi.fn(),
      setRightMode: vi.fn(),
      setWbTab: vi.fn(),
      addPreviewTab: vi.fn(),
      quotedMsg: appState.quotedMsg as Message | null,
      setQuotedMsg: setQuotedMsgMock,
      setActiveSession: vi.fn(),
      resetWorkbenchOverride: vi.fn(),
    }),
  },
}));

const OWNER_INCARNATION_ID = "0123456789abcdef0123456789abcdef";

function ownerMode(sessionId = "s1") {
  return { session_id: sessionId, mode: "owner_v1" as const, incarnation_id: OWNER_INCARNATION_ID };
}

function legacyMode(sessionId = "s1") {
  return { session_id: sessionId, mode: "legacy" as const };
}

async function importStores() {
  const [{ useChatStore }, { useToastStore }] = await Promise.all([
    import("./useChatStore"),
    import("./useToastStore"),
  ]);
  return { useChatStore, useToastStore };
}

function makeHistory(count: number): Message[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `history-${index}`,
    role: (index % 2 === 0 ? "user" : "assistant") as Message["role"],
    content: `history message ${index}`,
    timestamp: index + 1,
  }));
}

function lastRequestBody(): ChatRequest {
  return apiSSEBodies.at(-1) as ChatRequest;
}

function toastMessages(store: {
  getState: () => { toasts: Array<{ message: string }> };
}): string[] {
  return store.getState().toasts.map((toast) => toast.message);
}

describe("useChatStore server-owned history", () => {
  beforeEach(() => {
    vi.resetModules();
    capturedCallbacks = null;
    sessionState.creatingSession = false;
    sessionState.sessions = [{ id: "s1", title: "新对话", model: "gpt-4o" }];
    appState.quotedMsg = null;
    setQuotedMsgMock.mockClear();
    newSessionMock.mockReset();
    historyModeMock.mockReset().mockResolvedValue(ownerMode());
    apiPostMock.mockReset().mockResolvedValue({ success: true, data: {} });
    apiGetMock.mockReset().mockResolvedValue({ messages: [] });
    apiSSEPaths.length = 0;
    apiSSEBodies.length = 0;
    localStorage.clear();
  });

  afterEach(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("owner_v1 can send while an older history page is still available", async () => {
    const { useChatStore } = await importStores();
    const history = makeHistory(6);
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 1, hasMore: true, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("current question");

    expect(apiSSEPaths).toEqual(["chat"]);
    const body = lastRequestBody();
    expect(body.history_mode).toBe("server");
    expect(body.messages).toHaveLength(1);
    expect(body.messages[0]).toMatchObject({ role: "user", content: "current question" });
  });

  it("owner_v1 can send with more than the client cap and only sends this turn plus quote", async () => {
    const { useChatStore } = await importStores();
    const history = makeHistory(210);
    const quoted: Message = { id: "quote-1", role: "user", content: "quoted earlier text", timestamp: 1 };
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 2, hasMore: false, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("next question", undefined, quoted);

    expect(apiSSEPaths).toEqual(["chat"]);
    const body = lastRequestBody();
    expect(body.history_mode).toBe("server");
    expect(body.messages).toHaveLength(2);
    expect(body.messages[0]).toMatchObject({ role: "system" });
    expect(body.messages[0].content).toContain("quoted earlier text");
    expect(body.messages[1]).toMatchObject({ role: "user", content: "next question" });
  });

  it("legacy still blocks while an older history page is not loaded", async () => {
    historyModeMock.mockResolvedValue(legacyMode());
    const { useChatStore, useToastStore } = await importStores();
    const history = makeHistory(4);
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 1, hasMore: true, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("blocked question");

    expect(apiSSEPaths).toEqual([]);
    expect(useChatStore.getState().messages).toHaveLength(history.length);
    expect(toastMessages(useToastStore)).toEqual(
      expect.arrayContaining([expect.stringContaining("更早的分页历史")]),
    );
  });

  it("legacy still enforces the per-request message cap", async () => {
    historyModeMock.mockResolvedValue(legacyMode());
    const { useChatStore, useToastStore } = await importStores();
    const history = makeHistory(210);
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 2, hasMore: false, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("blocked question");

    expect(apiSSEPaths).toEqual([]);
    expect(useChatStore.getState().messages).toHaveLength(history.length);
    expect(toastMessages(useToastStore)).toEqual(
      expect.arrayContaining([expect.stringContaining("超过本次请求可容纳的")]),
    );
  });

  it("legacy still sends the full known history without history_mode", async () => {
    historyModeMock.mockResolvedValue(legacyMode());
    const { useChatStore } = await importStores();
    const history = makeHistory(4);
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 2, hasMore: false, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("legacy question");

    expect(apiSSEPaths).toEqual(["chat"]);
    const body = lastRequestBody();
    expect(body.history_mode).toBeUndefined();
    expect(body.messages).toHaveLength(history.length + 1);
    expect(body.messages.slice(0, history.length)).toEqual(
      history.map((message) => ({ role: message.role, content: message.content, message_id: message.id })),
    );
  });

  it("owner_v1 still blocks when the displayed history and local cache disagree", async () => {
    const { useChatStore, useToastStore } = await importStores();
    const shown = makeHistory(4);
    const cached = makeHistory(4).map((message, index) =>
      index === 3 ? { ...message, content: "different cached content" } : message
    );
    useChatStore.setState({
      activeSessionId: "s1",
      messages: shown,
      sessionMessages: { s1: cached },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 2, hasMore: true, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("blocked question");

    expect(apiSSEPaths).toEqual([]);
    expect(useChatStore.getState().messages).toHaveLength(shown.length);
    expect(toastMessages(useToastStore)).toEqual(
      expect.arrayContaining([expect.stringContaining("显示内容与本地历史缓存不一致")]),
    );
  });

  it("owner_v1 still blocks while history is loading", async () => {
    const { useChatStore, useToastStore } = await importStores();
    const history = makeHistory(2);
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: true, pagesLoaded: 0, hasMore: null, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("blocked question");

    expect(apiSSEPaths).toEqual([]);
    expect(toastMessages(useToastStore)).toEqual(
      expect.arrayContaining([expect.stringContaining("仍在恢复或分页加载")]),
    );
  });

  it("owner_v1 still blocks when pending saves pause history paging", async () => {
    const { useChatStore, useToastStore } = await importStores();
    const history = makeHistory(2);
    useChatStore.setState({
      activeSessionId: "s1",
      messages: history,
      sessionMessages: { s1: history },
      isStreaming: false,
      pendingSaveBySession: { s1: "pending" },
      pagedHistoryBySession: {
        s1: {
          loading: false,
          pagesLoaded: 1,
          hasMore: false,
          error: "会话存在待确认的本地消息，分页已暂停",
          persistenceError: null,
        },
      },
    });

    await useChatStore.getState().sendMessage("blocked question");

    expect(apiSSEPaths).toEqual([]);
    expect(toastMessages(useToastStore)).toEqual(
      expect.arrayContaining([expect.stringContaining("无法确认当前会话历史是否完整")]),
    );
  });

  it("rolls back the staged pair and restores input when the history-mode read fails", async () => {
    historyModeMock.mockRejectedValueOnce(new Error("history-mode unavailable"));
    const { useChatStore, useToastStore } = await importStores();
    useChatStore.setState({
      activeSessionId: "s1",
      messages: [],
      sessionMessages: { s1: [] },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 1, hasMore: false, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("keep this input");

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiSSEPaths).toEqual([]);
    expect(useChatStore.getState().messages).toEqual([]);
    expect(useChatStore.getState().sessionMessages.s1).toEqual([]);
    expect(useChatStore.getState().drafts.s1).toBe("keep this input");
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(toastMessages(useToastStore)).toEqual(
      expect.arrayContaining([expect.stringContaining("无法确认会话历史版本")]),
    );
  });

  it("keeps the existing streaming error rollback for a failed server-history request", async () => {
    const { useChatStore } = await importStores();
    useChatStore.setState({
      activeSessionId: "s1",
      messages: [],
      sessionMessages: { s1: [] },
      isStreaming: false,
      pagedHistoryBySession: {
        s1: { loading: false, pagesLoaded: 1, hasMore: true, error: null, persistenceError: null },
      },
    });

    await useChatStore.getState().sendMessage("server history failure");

    expect(apiSSEPaths).toEqual(["chat"]);
    expect(lastRequestBody().history_mode).toBe("server");
    expect(useChatStore.getState().isStreaming).toBe(true);

    capturedCallbacks!.onError!(new Error("network down"));

    const messages = useChatStore.getState().messages;
    expect(useChatStore.getState().isStreaming).toBe(false);
    expect(messages).toHaveLength(2);
    expect(messages[1].content).toContain("network down");
    expect((messages[1].activity as { status?: string } | undefined)?.status).toBe("error");
  });
});
