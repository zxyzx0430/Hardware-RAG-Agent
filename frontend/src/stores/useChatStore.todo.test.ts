import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ChatSSEEvent } from "../types/api";
import type { Message, TodoItem } from "../types/session";

const {
  apiGetMock,
  fetchTodosMock,
  clearTodosMock,
  sessionState,
  toastErrorMock,
} = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  fetchTodosMock: vi.fn(),
  clearTodosMock: vi.fn(),
  sessionState: {
    sessions: [{ id: "s1", title: "Session 1", model: "gpt-4o" }],
  },
  toastErrorMock: vi.fn(),
}));

let capturedCallbacks: {
  onEvent: (event: ChatSSEEvent) => void;
  onDone?: () => void;
  onError?: (err: Error) => void;
} | null = null;

vi.mock("../api/client", () => ({
  apiSSE: vi.fn((_path, _body, callbacks) => {
    capturedCallbacks = callbacks;
    return Promise.resolve();
  }),
  apiPost: vi.fn().mockResolvedValue({}),
  apiGet: apiGetMock,
  apiDelete: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
}));

vi.mock("../api/todo", () => ({
  fetchSessionTodos: fetchTodosMock,
  clearSessionTodos: clearTodosMock,
}));

vi.mock("../api/plan", () => ({
  fetchLatestPlan: vi.fn().mockResolvedValue(null),
  approveSessionPlan: vi.fn(),
  rejectSessionPlan: vi.fn(),
  listSessionPlans: vi.fn().mockResolvedValue([]),
}));

vi.mock("../utils/broadcast", () => ({ post: vi.fn(), on: vi.fn(() => vi.fn()) }));

vi.mock("../utils/rollingHistoryClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../utils/rollingHistoryClient")>();
  return { ...actual, getSessionHistoryMode: vi.fn().mockResolvedValue({ mode: "legacy" }) };
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
      permissionMode: "default",
      webSearchConfig: { apiKey: "", baseUrl: "" },
      resolveVisionCreds: () => ({ model: "", baseUrl: "", apiKey: "" }),
      resolveImageCreds: () => ({ model: "", baseUrl: "", apiKey: "" }),
    }),
  },
}));

vi.mock("../stores/useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));

vi.mock("../stores/useToastStore", () => ({
  useToastStore: {
    getState: () => ({ showError: toastErrorMock, showWarning: vi.fn() }),
  },
}));

vi.mock("../stores/useSessionStore", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("./useSessionStore");
  return {
    ...actual,
    useSessionStore: {
      getState: () => ({
        sessions: sessionState.sessions,
        creatingSession: false,
        newSession: vi.fn(),
        updateSessionMeta: vi.fn(),
      }),
    },
  };
});

vi.mock("../stores/useAppStore", () => ({
  useAppStore: {
    getState: () => ({
      quotedMsg: null,
      setQuotedMsg: vi.fn(),
      setRightPanelOpen: vi.fn(),
      setRightMode: vi.fn(),
      setRightContentTab: vi.fn(),
    }),
  },
}));

async function importStore() {
  return (await import("./useChatStore")).useChatStore;
}

function assistantWithTodos(todos: Message["todos"]): Message {
  return { id: "a1", role: "assistant", content: "", timestamp: 1, todos };
}

async function startStream(store: Awaited<ReturnType<typeof importStore>>) {
  store.setState({
    activeSessionId: "s1",
    messages: [assistantWithTodos([])],
    sessionMessages: { s1: [assistantWithTodos([])] },
    isStreaming: false,
    planMode: false,
  });
  store.getState().sendMessage("hello");
  // sendMessage reaches apiSSE synchronously when no validation awaits run.
  await Promise.resolve();
  if (!capturedCallbacks) throw new Error("SSE callbacks not captured");
  return capturedCallbacks;
}

beforeEach(() => {
  vi.resetModules();
  capturedCallbacks = null;
  sessionState.sessions = [{ id: "s1", title: "Session 1", model: "gpt-4o" }];
  apiGetMock.mockReset().mockResolvedValue({ messages: [] });
  fetchTodosMock.mockReset().mockResolvedValue({ todos: [], updatedAt: null });
  clearTodosMock.mockReset().mockResolvedValue(undefined);
  toastErrorMock.mockReset();
  localStorage.clear();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe("useChatStore todos", () => {
  it("loads the authoritative snapshot and marks it loaded", async () => {
    fetchTodosMock.mockResolvedValue({
      todos: [
        { id: "t1", content: "Inspect datasheet", status: "completed" },
        { content: "Flash firmware", status: "in_progress" },
      ],
      updatedAt: "2026-11-06T10:00:00Z",
    });
    const store = await importStore();

    await store.getState().loadSessionTodos("s1");

    const state = store.getState();
    expect(fetchTodosMock).toHaveBeenCalledWith("s1");
    expect(state.sessionTodosLoaded.s1).toBe(true);
    expect(state.sessionTodos.s1).toEqual([
      { id: "t1", content: "Inspect datasheet", status: "completed" },
      { id: "todo-1", content: "Flash firmware", status: "in_progress" },
    ]);
  });

  it("keeps a loaded empty list empty instead of falling back to message todos", async () => {
    fetchTodosMock.mockResolvedValue({ todos: [], updatedAt: null });
    const store = await importStore();
    store.setState({
      activeSessionId: "s1",
      messages: [assistantWithTodos([{ id: "old", content: "Old task", status: "pending" }])],
    });

    await store.getState().loadSessionTodos("s1");

    expect(store.getState().sessionTodosLoaded.s1).toBe(true);
    expect(store.getState().sessionTodos.s1).toEqual([]);
  });

  it("clears the server list and the current assistant message", async () => {
    const store = await importStore();
    const message = assistantWithTodos([{ id: "old", content: "Old task", status: "pending" }]);
    store.setState({
      activeSessionId: "s1",
      messages: [message],
      sessionMessages: { s1: [message] },
      sessionTodos: { s1: [{ id: "old", content: "Old task", status: "pending" }] },
      sessionTodosLoaded: { s1: true },
      streamingTodos: [{ id: "old", content: "Old task", status: "pending" }],
    });

    const cleared = await store.getState().clearSessionTodos("s1");

    expect(cleared).toBe(true);
    expect(clearTodosMock).toHaveBeenCalledWith("s1");
    expect(store.getState().sessionTodos.s1).toEqual([]);
    expect(store.getState().streamingTodos).toEqual([]);
    expect(store.getState().messages[0].todos).toEqual([]);
    expect(store.getState().sessionMessages.s1[0].todos).toEqual([]);
  });

  it("keeps todo snapshots isolated per session and clears planMode on switch", async () => {
    fetchTodosMock.mockImplementation(async (sessionId: string) => ({
      todos: [{ id: `${sessionId}-todo`, content: `Todo ${sessionId}`, status: "pending" }],
      updatedAt: null,
    }));
    const store = await importStore();
    store.setState({ planMode: true });

    await store.getState().loadSessionTodos("s1");
    await store.getState().loadSessionTodos("s2");
    store.getState().setActiveSession("s2");

    expect(store.getState().planMode).toBe(false);
    expect(store.getState().sessionTodos.s1[0].content).toBe("Todo s1");
    expect(store.getState().sessionTodos.s2[0].content).toBe("Todo s2");
  });

  it("discards a GET response that raced a newer SSE todo_update", async () => {
    let resolveStale: ((value: { todos: TodoItem[]; updatedAt: string | null }) => void) | null = null;
    fetchTodosMock.mockReturnValue(new Promise<{ todos: TodoItem[]; updatedAt: string | null }>((resolve) => {
      resolveStale = resolve;
    }));
    const store = await importStore();
    const callbacks = await startStream(store);

    const pendingLoad = store.getState().loadSessionTodos("s1");
    callbacks.onEvent({
      type: "todo_update",
      todos: [{ content: "From SSE", status: "in_progress" }],
    } as ChatSSEEvent);

    resolveStale!({
      todos: [{ id: "stale", content: "Stale GET", status: "pending" }],
      updatedAt: null,
    });
    await pendingLoad;

    const state = store.getState();
    expect(state.streamingTodosSeen.s1).toBe(true);
    expect(state.sessionTodos.s1.map((todo) => todo.content)).toEqual(["From SSE"]);
    expect(state.streamingTodos.map((todo) => todo.content)).toEqual(["From SSE"]);
  });

  it("treats an empty todo_update as an authoritative clear", async () => {
    const store = await importStore();
    const callbacks = await startStream(store);

    callbacks.onEvent({
      type: "todo_update",
      todos: [{ content: "First", status: "pending" }],
    } as ChatSSEEvent);
    expect(store.getState().sessionTodos.s1).toHaveLength(1);

    callbacks.onEvent({ type: "todo_update", todos: [] } as ChatSSEEvent);

    const state = store.getState();
    expect(state.sessionTodos.s1).toEqual([]);
    expect(state.streamingTodos).toEqual([]);
    expect(state.messages[state.messages.length - 1].todos).toEqual([]);
  });
});
