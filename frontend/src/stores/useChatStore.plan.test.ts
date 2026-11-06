import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ChatSSEEvent } from "../types/api";
import type { Plan } from "../types/plan";

const {
  apiGetMock,
  fetchPlanMock,
  approvePlanMock,
  rejectPlanMock,
  sessionState,
  toastErrorMock,
} = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  fetchPlanMock: vi.fn(),
  approvePlanMock: vi.fn(),
  rejectPlanMock: vi.fn(),
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

vi.mock("../api/plan", () => ({
  fetchLatestPlan: fetchPlanMock,
  approveSessionPlan: approvePlanMock,
  rejectSessionPlan: rejectPlanMock,
  listSessionPlans: vi.fn().mockResolvedValue([]),
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

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: "pl_1",
    session_id: "s1",
    version: 1,
    status: "pending",
    title: "Bring up I2C sensor",
    summary: "Read the datasheet and validate the bus.",
    steps: [{ title: "Inspect datasheet", detail: "Confirm address", verification: "Address 0x68" }],
    risks: ["Bus contention"],
    open_questions: ["Is the sensor powered?"],
    run_id: "r_1",
    executed_run_id: null,
    created_at: "2026-11-06T10:00:00Z",
    decided_at: null,
    ...overrides,
  };
}

async function importStore() {
  return (await import("./useChatStore")).useChatStore;
}

async function importExecutionMessage() {
  return (await import("./useChatStore")).APPROVED_PLAN_EXECUTION_MESSAGE;
}

async function startStream(store: Awaited<ReturnType<typeof importStore>>) {
  store.setState({
    activeSessionId: "s1",
    messages: [],
    sessionMessages: { s1: [] },
    isStreaming: false,
    planMode: false,
  });
  store.getState().sendMessage("hello");
  await Promise.resolve();
  if (!capturedCallbacks) throw new Error("SSE callbacks not captured");
  return capturedCallbacks;
}

beforeEach(() => {
  vi.resetModules();
  capturedCallbacks = null;
  sessionState.sessions = [{ id: "s1", title: "Session 1", model: "gpt-4o" }];
  apiGetMock.mockReset().mockResolvedValue({ messages: [] });
  fetchPlanMock.mockReset().mockResolvedValue(null);
  approvePlanMock.mockReset();
  rejectPlanMock.mockReset();
  toastErrorMock.mockReset();
  localStorage.clear();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe("useChatStore plans", () => {
  it("applies plan_update and marks the session plan loaded", async () => {
    const store = await importStore();
    const callbacks = await startStream(store);
    const plan = makePlan();

    callbacks.onEvent({ type: "plan_update", plan } as ChatSSEEvent);

    const state = store.getState();
    expect(state.sessionPlans.s1).toEqual(plan);
    expect(state.sessionPlanLoaded.s1).toBe(true);
    expect(state.sessionPlanRevision.s1).toBe(1);
  });

  it("loads the latest plan and records null as loaded", async () => {
    const plan = makePlan({ status: "approved" });
    fetchPlanMock.mockResolvedValue(plan);
    const store = await importStore();

    await store.getState().loadSessionPlan("s1");
    expect(store.getState().sessionPlans.s1).toEqual(plan);
    expect(store.getState().sessionPlanLoaded.s1).toBe(true);

    fetchPlanMock.mockResolvedValue(null);
    await store.getState().loadSessionPlan("s2");
    expect(store.getState().sessionPlans.s2).toBeNull();
    expect(store.getState().sessionPlanLoaded.s2).toBe(true);
  });

  it("discards a plan GET that raced a newer plan_update", async () => {
    let resolveStale: ((value: Plan | null) => void) | null = null;
    fetchPlanMock.mockReturnValue(new Promise<Plan | null>((resolve) => { resolveStale = resolve; }));
    const store = await importStore();
    const callbacks = await startStream(store);
    const fresh = makePlan({ status: "approved", version: 2 });

    const pendingLoad = store.getState().loadSessionPlan("s1");
    callbacks.onEvent({ type: "plan_update", plan: fresh } as ChatSSEEvent);
    resolveStale!(makePlan({ title: "Stale plan", version: 1 }));
    await pendingLoad;

    expect(store.getState().sessionPlans.s1).toEqual(fresh);
  });

  it("approves a pending plan and closes planMode", async () => {
    const pending = makePlan();
    const approved = makePlan({ status: "approved", decided_at: "2026-11-06T10:05:00Z" });
    approvePlanMock.mockResolvedValue(approved);
    const store = await importStore();
    store.setState({ planMode: true, sessionPlans: { s1: pending }, sessionPlanLoaded: { s1: true } });

    const result = await store.getState().approvePlan("s1", pending.id);

    expect(approvePlanMock).toHaveBeenCalledWith("s1", "pl_1");
    expect(result).toEqual(approved);
    expect(store.getState().sessionPlans.s1).toEqual(approved);
    expect(store.getState().planMode).toBe(false);
  });

  it("keeps the pending plan and reports failure when approval fails", async () => {
    const pending = makePlan();
    approvePlanMock.mockRejectedValue(new Error("PLAN_SUPERSEDED"));
    const store = await importStore();
    store.setState({ planMode: true, sessionPlans: { s1: pending }, sessionPlanLoaded: { s1: true } });

    const result = await store.getState().approvePlan("s1", pending.id);

    expect(result).toBeNull();
    expect(store.getState().sessionPlans.s1).toEqual(pending);
    expect(store.getState().planMode).toBe(true);
    expect(toastErrorMock).toHaveBeenCalledWith(expect.stringContaining("PLAN_SUPERSEDED"));
  });

  it("rejects a pending plan, updates its status and closes planMode", async () => {
    const pending = makePlan();
    const rejected = makePlan({ status: "rejected", decided_at: "2026-11-06T10:06:00Z" });
    rejectPlanMock.mockResolvedValue(rejected);
    const store = await importStore();
    store.setState({ planMode: true, sessionPlans: { s1: pending }, sessionPlanLoaded: { s1: true } });

    const ok = await store.getState().rejectPlan("s1", pending.id);

    expect(ok).toBe(true);
    expect(store.getState().sessionPlans.s1).toEqual(rejected);
    expect(store.getState().planMode).toBe(false);
  });

  it("sends the approved-plan execution turn through sendMessage", async () => {
    const store = await importStore();
    const executionMessage = await importExecutionMessage();
    const sendMessage = vi.fn();
    const approved = makePlan({ status: "approved" });
    store.setState({
      activeSessionId: "s1",
      isStreaming: false,
      planMode: true,
      sessionPlans: { s1: approved },
      sendMessage,
    });

    const started = store.getState().executeApprovedPlan(approved);

    expect(started).toBe(true);
    expect(store.getState().planMode).toBe(false);
    expect(sendMessage).toHaveBeenCalledWith(
      executionMessage,
      undefined,
      undefined,
      undefined,
      { approvedPlanId: approved.id },
    );
  });

  it("refuses to execute while streaming or when the plan is not approved", async () => {
    const store = await importStore();
    const sendMessage = vi.fn();
    const approved = makePlan({ status: "approved" });
    store.setState({
      activeSessionId: "s1",
      isStreaming: true,
      sessionPlans: { s1: approved },
      sendMessage,
    });

    expect(store.getState().executeApprovedPlan(approved)).toBe(false);
    expect(sendMessage).not.toHaveBeenCalled();

    store.setState({ isStreaming: false });
    expect(store.getState().executeApprovedPlan(makePlan({ status: "pending" }))).toBe(false);
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
