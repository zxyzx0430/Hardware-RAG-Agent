import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatSSEEvent } from "../types/api";
import type { Session } from "../types/session";

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
  getAuthHeaders: () => ({}),
}));

type SseMeta = { lastEventId?: string; runId?: string; seq?: number };
type CapturedCallbacks = {
  onEvent: (event: ChatSSEEvent, meta?: SseMeta) => void;
  onDone?: () => void;
  onError?: (error: Error) => void;
};
type CapturedCall = {
  path: string;
  body: unknown;
  callbacks: CapturedCallbacks;
  controller?: AbortController;
  options?: Record<string, unknown>;
};

const sessionId = "run-view-session";
const session: Session = {
  id: sessionId,
  title: "Run 状态测试",
  preview: "",
  model: "",
  createdAt: 1,
  project: "",
  pinned: false,
  msgCount: 0,
  contextWindow: 262144,
};

let sseCalls: CapturedCall[] = [];

function seedRunRecord(runId: string, lastEventSeq: number, status = "running"): void {
  localStorage.setItem("hwrag_active_runs_v1", JSON.stringify({
    [sessionId]: { runId, sessionId, lastEventSeq, status, updatedAt: Date.now() },
  }));
}

function emit(callIndex: number, event: ChatSSEEvent, seq?: number): void {
  const call = sseCalls[callIndex];
  if (!call) throw new Error(`SSE call ${callIndex} was not captured`);
  const runId = event.run_id ?? "r_1";
  call.callbacks.onEvent(event, {
    runId,
    seq,
    lastEventId: seq !== undefined ? `${runId}:${seq}` : undefined,
  });
}

async function importStore() {
  return (await import("./useChatStore")).useChatStore;
}

function installApiGet(handlers: {
  active?: () => Promise<unknown> | unknown;
  run?: (runId: string) => Promise<unknown> | unknown;
  messages?: () => unknown;
}): void {
  apiGetMock.mockImplementation(async (path: string) => {
    if (path === `sessions/${sessionId}/runs/active`) {
      if (handlers.active) return await handlers.active();
      throw new Error("API 404: Not Found");
    }
    if (path === `sessions/${sessionId}/messages`) {
      return handlers.messages
        ? handlers.messages()
        : { messages: [{ id: "u1", role: "user", content: "问题", created_at: "2026-11-05T00:00:00Z" }] };
    }
    if (path.startsWith("runs/")) {
      if (handlers.run) return await handlers.run(path.slice("runs/".length));
      return { status: "running", last_event_seq: 0 };
    }
    throw new Error(`unexpected GET: ${path}`);
  });
}

describe("useChatStore run lifecycle view", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    sseCalls = [];
    apiGetMock.mockReset();
    apiPostMock.mockReset().mockResolvedValue({});
    apiPutMock.mockReset().mockResolvedValue({});
    apiPatchMock.mockReset().mockResolvedValue({});
    apiDeleteMock.mockReset().mockResolvedValue({});
    apiSSEMock.mockReset().mockImplementation((
      path: string,
      body: unknown,
      callbacks: CapturedCallbacks,
      controller?: AbortController,
      options?: Record<string, unknown>,
    ) => {
      sseCalls.push({ path, body, callbacks, controller, options });
      return Promise.resolve({ lastEventId: undefined, runId: undefined, lastSeq: 0, done: false });
    });

    localStorage.setItem("hwrag_sessions", JSON.stringify([session]));
    localStorage.setItem("hwrag_active_session", JSON.stringify(sessionId));
  });

  afterEach(async () => {
    const recovery = await import("../utils/runRecovery");
    recovery.flushRunRecords();
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("adopts the server active run without a local record and never applies a sequence twice", async () => {
    installApiGet({
      active: () => ({
        active: {
          id: "r_9",
          status: "running",
          last_event_seq: 0,
          started_at: "2026-11-09T01:00:00Z",
        },
      }),
      messages: () => ({
        messages: [
          { id: "u1", role: "user", content: "问题", created_at: "2026-11-05T00:00:00Z" },
          { id: "a1", role: "assistant", content: "", created_at: "2026-11-05T00:00:01Z" },
        ],
      }),
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(sseCalls.some((call) => call.path === "runs/r_9/events")).toBe(true);
    });

    const replayIndex = sseCalls.findIndex((call) => call.path === "runs/r_9/events");
    expect(sseCalls[replayIndex].options).toMatchObject({ method: "GET", after: 0 });

    const liveView = store.getState().runViewBySession[sessionId];
    expect(liveView).toMatchObject({
      runId: "r_9",
      status: "running",
      phase: "restoring",
    });
    expect(liveView.startedAt).toBe(Date.parse("2026-11-09T01:00:00Z"));

    emit(replayIndex, { type: "run_started", run_id: "r_9", seq: 1 }, 1);
    emit(replayIndex, { type: "text", content: "A", run_id: "r_9", seq: 2 }, 2);
    emit(replayIndex, { type: "text", content: "DUP", run_id: "r_9", seq: 2 }, 2);
    emit(replayIndex, { type: "done", success: true, run_id: "r_9", seq: 3 }, 3);
    sseCalls[replayIndex].callbacks.onDone?.();

    expect(store.getState().messages.at(-1)?.content).toBe("A");
    expect(store.getState().runViewBySession[sessionId]).toMatchObject({
      runId: "r_9",
      status: "completed",
      phase: "terminal",
    });
    // One row per accepted sequence: the duplicate seq 2 added no row, and the
    // streaming text chunk was coalesced instead of one row per token.
    expect(store.getState().runTimelineBySession[sessionId].map((entry) => entry.type))
      .toEqual(["run_started", "text", "done"]);
  });

  it("restores a pending approval from the active-run route without reopening the stream", async () => {
    installApiGet({
      active: () => ({
        run: {
          id: "r_2",
          status: "awaiting_confirmation",
          last_event_seq: 5,
          pending_approvals: [{
            id: "approval-1",
            run_id: "r_2",
            call_id: "call-1",
            tool_name: "write_file",
            args: { path: "main.c" },
            risk_level: "high",
            status: "pending",
          }],
        },
      }),
      messages: () => ({
        messages: [
          { id: "u1", role: "user", content: "写文件", created_at: "2026-11-05T00:00:00Z" },
          { id: "a1", role: "assistant", content: "需要确认", created_at: "2026-11-05T00:00:01Z" },
        ],
      }),
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(store.getState().pendingConfirm?.calls).toHaveLength(1);
    });

    expect(store.getState().pendingConfirm).toMatchObject({
      sessionId,
      count: 1,
      calls: [{ name: "write_file", call_id: "call-1", risk_level: "high" }],
    });
    expect(store.getState().runViewBySession[sessionId]).toMatchObject({
      runId: "r_2",
      status: "awaiting_confirmation",
      phase: "live",
      pendingApprovals: 1,
    });
    expect(sseCalls).toHaveLength(0);
  });

  it("shows an interrupted hardware run as unknown and never as success", async () => {
    installApiGet({
      active: () => ({
        run: {
          id: "r_3",
          status: "interrupted",
          error_code: "PROCESS_RESTART",
          checkpoint_id: null,
          last_event_seq: 2,
        },
      }),
      messages: () => ({
        messages: [
          { id: "u1", role: "user", content: "烧录", created_at: "2026-11-05T00:00:00Z" },
          {
            id: "a1",
            role: "assistant",
            content: "正在烧录",
            created_at: "2026-11-05T00:00:01Z",
            activity: {
              durationMs: 0,
              status: "running",
              steps: [{ type: "tool", id: "t1", name: "flash_firmware", status: "pending" }],
            },
          },
        ],
      }),
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(sseCalls.some((call) => call.path === "runs/r_3/events")).toBe(true);
    });

    const view = store.getState().runViewBySession[sessionId];
    expect(view).toMatchObject({
      runId: "r_3",
      status: "interrupted",
      phase: "terminal",
      resultUnknown: true,
      hardwareUnknown: true,
    });
    expect(store.getState().runRecoveryNoticeBySession[sessionId]).toMatchObject({
      runId: "r_3",
      status: "interrupted",
      resultUnknown: true,
      hardwareUnknown: true,
    });
    expect(store.getState().runRecoveryNoticeBySession[sessionId].message).toContain("核对设备");

    const replayIndex = sseCalls.findIndex((call) => call.path === "runs/r_3/events");
    emit(replayIndex, {
      type: "error",
      code: "INTERRUPTED",
      message: "运行已中断（进程重启）",
      run_id: "r_3",
      seq: 3,
    }, 3);
    sseCalls[replayIndex].callbacks.onDone?.();

    const final = store.getState();
    expect(final.runViewBySession[sessionId].status).toBe("interrupted");
    expect(final.activeRuns[sessionId]).toBeUndefined();
    expect(final.isStreaming).toBe(false);
  });

  it("converges a cancelled replay to cancelled instead of completed", async () => {
    seedRunRecord("r_4", 2, "running");
    installApiGet({
      run: () => ({ status: "running", last_event_seq: 2 }),
      messages: () => ({
        messages: [
          { id: "u1", role: "user", content: "问题", created_at: "2026-11-05T00:00:00Z" },
          { id: "a1", role: "assistant", content: "A", created_at: "2026-11-05T00:00:01Z" },
        ],
      }),
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(sseCalls.some((call) => call.path === "runs/r_4/events")).toBe(true);
    });

    const replayIndex = sseCalls.findIndex((call) => call.path === "runs/r_4/events");
    emit(replayIndex, {
      type: "done",
      success: false,
      completed: false,
      cancelled: true,
      run_id: "r_4",
      seq: 3,
    }, 3);
    sseCalls[replayIndex].callbacks.onDone?.();

    const state = store.getState();
    expect(state.runViewBySession[sessionId]).toMatchObject({
      runId: "r_4",
      status: "cancelled",
      phase: "terminal",
    });
    expect(state.messages.at(-1)?.content).toBe("A");
    expect(String(state.messages.at(-1)?.content)).not.toContain("❌");
  });

  it("cancels a live run through the backend and converges the bar", async () => {
    installApiGet({});
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, run_id: "r_5", cancelled: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const store = await importStore();
    await store.getState().sendMessage("开始");
    emit(0, { type: "run_started", run_id: "r_5", seq: 1 }, 1);
    expect(store.getState().runViewBySession[sessionId]?.status).toBe("running");

    const cancelled = await store.getState().cancelActiveRun(sessionId);

    expect(cancelled).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runs/r_5/cancel",
      expect.objectContaining({ method: "POST" }),
    );
    expect(store.getState().runViewBySession[sessionId]).toMatchObject({
      status: "cancelled",
      phase: "terminal",
    });
    expect(store.getState().activeRuns[sessionId]).toBeUndefined();
    expect(store.getState().isStreaming).toBe(false);
  });

  it("clears a pending approval when a new turn starts", async () => {
    installApiGet({});
    const store = await importStore();
    store.setState({
      pendingConfirm: {
        sessionId,
        count: 1,
        calls: [{ name: "write_file", args: { path: "old.c" }, call_id: "old-call", risk_level: "high" }],
      },
    });

    await store.getState().sendMessage("新的问题");

    expect(store.getState().pendingConfirm).toBeNull();
    expect(sseCalls[0]?.path).toBe("chat");
  });

  it("keeps the interrupted notice when the backend refuses to resume", async () => {
    installApiGet({});
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        detail: {
          success: false,
          error: { code: "RESUME_UNSUPPORTED", message: "当前版本不自动恢复中断的运行" },
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const store = await importStore();
    const { useToastStore } = await import("./useToastStore");
    const showError = vi.spyOn(useToastStore.getState(), "showError");

    store.setState({
      runViewBySession: {
        [sessionId]: {
          runId: "r_6",
          status: "interrupted",
          phase: "terminal",
          startedAt: null,
          finishedAt: Date.now(),
          lastEventType: "error",
          lastEventSeq: 3,
          lastEventAt: Date.now(),
          currentTool: null,
          currentStepIndex: null,
          checkpointId: "cp_1",
          errorCode: "PROCESS_RESTART",
          errorMessage: "运行已中断，可从检查点继续。",
          pendingApprovals: 0,
          resultUnknown: false,
          hardwareUnknown: false,
          resumable: true,
          actionPending: false,
        },
      },
    });

    const resumed = await store.getState().resumeInterruptedRun(sessionId);

    expect(resumed).toBe(false);
    expect(showError).toHaveBeenCalledWith(expect.stringContaining("当前版本不自动恢复"));
    expect(store.getState().runViewBySession[sessionId]).toMatchObject({
      runId: "r_6",
      status: "interrupted",
      actionPending: false,
    });
  });
});
