import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ChatSSEEvent } from "../types/api";
import type { Plan } from "../types/plan";

const {
  fetchPlanMock,
  approvePlanMock,
  setAgentPlanModeMock,
  updateSessionMetaMock,
  toastInfoMock,
  toastErrorMock,
  sessionState,
} = vi.hoisted(() => {
  const updateSessionMetaMock = vi.fn();
  return {
    fetchPlanMock: vi.fn(),
    approvePlanMock: vi.fn(),
    setAgentPlanModeMock: vi.fn(),
    updateSessionMetaMock,
    toastInfoMock: vi.fn(),
    toastErrorMock: vi.fn(),
    sessionState: {
      sessions: [
        { id: "s1", title: "Session 1", model: "gpt-4o", planModeRequested: false },
        { id: "s2", title: "Session 2", model: "gpt-4o", planModeRequested: false },
      ],
      updateSessionMeta: updateSessionMetaMock,
    },
  };
});

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
  apiGet: vi.fn().mockResolvedValue({ messages: [] }),
  apiDelete: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
}));

vi.mock("../api/plan", () => ({
  fetchLatestPlan: fetchPlanMock,
  approveSessionPlan: approvePlanMock,
  rejectSessionPlan: vi.fn(),
  listSessionPlans: vi.fn().mockResolvedValue([]),
  setAgentPlanMode: setAgentPlanModeMock,
}));

vi.mock("../api/todo", () => ({
  fetchSessionTodos: vi.fn().mockResolvedValue({ todos: [], updatedAt: null }),
  clearSessionTodos: vi.fn().mockResolvedValue(undefined),
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
      permissionMode: "yolo",
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
    getState: () => ({ showError: toastErrorMock, showWarning: vi.fn(), showInfo: toastInfoMock }),
  },
}));

vi.mock("../stores/useSessionStore", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("./useSessionStore");
  return {
    ...actual,
    useSessionStore: { getState: () => sessionState },
  };
});

vi.mock("../stores/useAppStore", () => ({
  useAppStore: {
    getState: () => ({
      lang: "zh",
      quotedMsg: null,
      setQuotedMsg: vi.fn(),
      setRightPanelOpen: vi.fn(),
      setRightMode: vi.fn(),
      setRightContentTab: vi.fn(),
    }),
  },
}));

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: "pl_1",
    session_id: "s1",
    version: 1,
    status: "pending",
    title: "Plan",
    summary: "Summary",
    steps: [{ title: "Inspect", detail: "", verification: "" }],
    risks: [],
    open_questions: [],
    run_id: "r1",
    executed_run_id: null,
    created_at: "2026-11-08T10:00:00Z",
    decided_at: null,
    ...overrides,
  };
}

async function importStore() {
  return (await import("./useChatStore")).useChatStore;
}

async function startStream(store: Awaited<ReturnType<typeof importStore>>) {
  store.setState({
    activeSessionId: "s1",
    messages: [],
    sessionMessages: { s1: [] },
    isStreaming: false,
    planMode: false,
    planModeBySession: {},
  });
  store.getState().sendMessage("hello");
  await Promise.resolve();
  if (!capturedCallbacks) throw new Error("SSE callbacks not captured");
  return capturedCallbacks;
}

beforeEach(() => {
  vi.resetModules();
  capturedCallbacks = null;
  sessionState.sessions = [
    { id: "s1", title: "Session 1", model: "gpt-4o", planModeRequested: false },
    { id: "s2", title: "Session 2", model: "gpt-4o", planModeRequested: false },
  ];
  updateSessionMetaMock.mockReset();
  setAgentPlanModeMock.mockReset();
  approvePlanMock.mockReset();
  fetchPlanMock.mockReset().mockResolvedValue(null);
  toastInfoMock.mockReset();
  toastErrorMock.mockReset();
  localStorage.clear();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe("Agent plan mode", () => {
  it("applies permission_mode_changed to the active session", async () => {
    const store = await importStore();
    const callbacks = await startStream(store);

    callbacks.onEvent({
      type: "permission_mode_changed",
      permission_mode: "plan",
      source: "agent",
      reason: "research first",
    } as ChatSSEEvent);

    expect(store.getState().planMode).toBe(true);
    expect(store.getState().planModeBySession.s1).toBe(true);
    expect(updateSessionMetaMock).toHaveBeenCalledWith("s1", { planModeRequested: true });
    expect(toastInfoMock).toHaveBeenCalled();
  });

  it("keeps a background session's marker without changing the visible mode", async () => {
    const store = await importStore();
    const callbacks = await startStream(store);
    store.getState().setActiveSession("s2");

    callbacks.onEvent({
      type: "permission_mode_changed",
      permission_mode: "plan",
      source: "agent",
    } as ChatSSEEvent);

    expect(store.getState().planModeBySession.s1).toBe(true);
    expect(store.getState().planMode).toBe(false);
    expect(updateSessionMetaMock).toHaveBeenCalledWith("s1", { planModeRequested: true });
  });

  it("restores the persisted marker after a refresh", async () => {
    sessionState.sessions = [
      { id: "s1", title: "Session 1", model: "gpt-4o", planModeRequested: true },
      { id: "s2", title: "Session 2", model: "gpt-4o", planModeRequested: false },
    ];
    const store = await importStore();
    store.setState({ activeSessionId: "s1" });

    store.getState().restorePlanModeForSession("s1");

    expect(store.getState().planMode).toBe(true);
  });

  it("switches plan mode per session", async () => {
    const store = await importStore();
    store.setState({
      activeSessionId: "s2",
      planMode: false,
      planModeBySession: { s1: true },
    });

    store.getState().setActiveSession("s1");
    expect(store.getState().planMode).toBe(true);

    store.getState().setActiveSession("s2");
    expect(store.getState().planMode).toBe(false);
  });

  it("clears the server marker when the user leaves plan mode", async () => {
    setAgentPlanModeMock.mockResolvedValue(false);
    const store = await importStore();
    store.setState({ activeSessionId: "s1", planMode: true, planModeBySession: { s1: true } });

    const cleared = await store.getState().leaveAgentPlanMode();

    expect(cleared).toBe(true);
    expect(setAgentPlanModeMock).toHaveBeenCalledWith("s1", false);
    expect(store.getState().planMode).toBe(false);
    expect(store.getState().planModeBySession.s1).toBe(false);
    expect(updateSessionMetaMock).toHaveBeenCalledWith("s1", { planModeRequested: false });
  });

  it("keeps plan mode when clearing the marker fails", async () => {
    setAgentPlanModeMock.mockRejectedValue(new Error("network down"));
    const store = await importStore();
    store.setState({ activeSessionId: "s1", planMode: true, planModeBySession: { s1: true } });

    const cleared = await store.getState().leaveAgentPlanMode();

    expect(cleared).toBe(false);
    expect(store.getState().planMode).toBe(true);
    expect(store.getState().planModeBySession.s1).toBe(true);
    expect(toastErrorMock).toHaveBeenCalledWith(expect.stringContaining("network down"));
  });

  it("approving a plan clears plan mode for that session", async () => {
    const approved = makePlan({ status: "approved" });
    approvePlanMock.mockResolvedValue(approved);
    const store = await importStore();
    store.setState({
      activeSessionId: "s1",
      planMode: true,
      planModeBySession: { s1: true },
      sessionPlans: { s1: makePlan() },
      sessionPlanLoaded: { s1: true },
    });

    await store.getState().approvePlan("s1", "pl_1");

    expect(store.getState().planMode).toBe(false);
    expect(store.getState().planModeBySession.s1).toBe(false);
    expect(updateSessionMetaMock).toHaveBeenCalledWith("s1", { planModeRequested: false });
  });
});
