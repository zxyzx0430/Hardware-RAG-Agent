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

const sessionId = "recovery-session";
const session: Session = {
  id: sessionId,
  title: "恢复测试会话",
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

async function importRecovery() {
  return await import("../utils/runRecovery");
}

describe("useChatStore run recovery", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    sseCalls = [];
    apiGetMock.mockReset().mockImplementation(async (path: string) => {
      if (path === `sessions/${sessionId}/messages`) return { messages: [] };
      if (path.startsWith("runs/")) return { status: "running", last_event_seq: 0 };
      throw new Error(`unexpected GET: ${path}`);
    });
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
    // Cancel the run-record debounce so a late write from this test cannot
    // leak into the next test's localStorage seed.
    const recovery = await import("../utils/runRecovery");
    recovery.flushRunRecords();
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("drops duplicate sequences and never duplicates streamed text", async () => {
    const store = await importStore();
    await store.getState().sendMessage("开始恢复测试");
    expect(sseCalls).toHaveLength(1);
    expect(sseCalls[0].path).toBe("chat");

    emit(0, { type: "run_started", run_id: "r_1", seq: 1 }, 1);
    emit(0, { type: "text", content: "A", run_id: "r_1", seq: 2 }, 2);
    emit(0, { type: "text", content: "DUPLICATE", run_id: "r_1", seq: 2 }, 2);
    emit(0, { type: "text", content: "C", run_id: "r_1", seq: 3 }, 3);

    const state = store.getState();
    expect(state.messages.at(-1)?.content).toBe("AC");
    expect(state.activeRuns[sessionId]).toMatchObject({ runId: "r_1", lastEventSeq: 3, status: "running" });
  });

  it("requests a replay when a sequence gap is observed", async () => {
    const store = await importStore();
    await store.getState().sendMessage("缺口测试");

    emit(0, { type: "run_started", run_id: "r_1", seq: 1 }, 1);
    emit(0, { type: "text", content: "A", run_id: "r_1", seq: 2 }, 2);
    // seq 3 and 4 are missing; this event is held back for the replay.
    emit(0, { type: "text", content: "E", run_id: "r_1", seq: 5 }, 5);

    expect(sseCalls).toHaveLength(2);
    expect(sseCalls[1].path).toBe("runs/r_1/events");
    expect(sseCalls[1].options).toMatchObject({ method: "GET", after: 2 });
    expect(store.getState().messages.at(-1)?.content).toBe("A");

    // The replay delivers the missing events (including the held-back seq 5).
    emit(1, { type: "text", content: "B", run_id: "r_1", seq: 3 }, 3);
    emit(1, { type: "text", content: "C", run_id: "r_1", seq: 4 }, 4);
    emit(1, { type: "text", content: "E", run_id: "r_1", seq: 5 }, 5);
    emit(1, { type: "done", success: true, run_id: "r_1", seq: 6 }, 6);
    sseCalls[1].callbacks.onDone?.();

    expect(store.getState().messages.at(-1)?.content).toBe("ABCE");
  });

  it("clears the active run after a terminal done event", async () => {
    const store = await importStore();
    const recovery = await importRecovery();
    await store.getState().sendMessage("完成测试");

    emit(0, { type: "run_started", run_id: "r_1", seq: 1 }, 1);
    emit(0, { type: "text", content: "完成", run_id: "r_1", seq: 2 }, 2);
    expect(store.getState().activeRuns[sessionId]).toBeDefined();

    emit(0, { type: "done", success: true, run_id: "r_1", seq: 3 }, 3);
    sseCalls[0].callbacks.onDone?.();

    expect(store.getState().activeRuns[sessionId]).toBeUndefined();
    expect(recovery.getActiveRun(sessionId)).toBeUndefined();
    expect(localStorage.getItem("hwrag_active_runs_v1")).toBeNull();
    expect(store.getState().isStreaming).toBe(false);
  });

  it("reattaches to an unfinished run on refresh without duplicating text", async () => {
    seedRunRecord("r_1", 2, "running");
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === `sessions/${sessionId}/messages`) {
        return {
          messages: [
            { id: "u1", role: "user", content: "问题", created_at: "2026-11-05T00:00:00Z" },
            { id: "a1", role: "assistant", content: "A", created_at: "2026-11-05T00:00:01Z" },
          ],
        };
      }
      if (path === "runs/r_1") return { status: "running", last_event_seq: 2 };
      throw new Error(`unexpected GET: ${path}`);
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(sseCalls.some((call) => call.path === "runs/r_1/events")).toBe(true);
    });

    const replayCall = sseCalls.findIndex((call) => call.path === "runs/r_1/events");
    expect(sseCalls[replayCall].options).toMatchObject({ method: "GET", after: 2, reconnect: { maxAttempts: 5 } });

    emit(replayCall, { type: "text", content: "B", run_id: "r_1", seq: 3 }, 3);
    emit(replayCall, { type: "done", success: true, run_id: "r_1", seq: 4 }, 4);
    sseCalls[replayCall].callbacks.onDone?.();

    const messages = store.getState().messages;
    expect(messages.at(-1)?.content).toBe("AB");
    expect(messages.at(-1)?.content).not.toBe("AAB");
    expect(store.getState().activeRuns[sessionId]).toBeUndefined();
  });

  it("surfaces an interrupted run as a retryable notice instead of a fake success", async () => {
    seedRunRecord("r_1", 1, "running");
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === `sessions/${sessionId}/messages`) {
        return {
          messages: [
            { id: "u1", role: "user", content: "问题", created_at: "2026-11-05T00:00:00Z" },
            { id: "a1", role: "assistant", content: "A", created_at: "2026-11-05T00:00:01Z" },
          ],
        };
      }
      if (path === "runs/r_1") return { status: "interrupted", last_event_seq: 2 };
      throw new Error(`unexpected GET: ${path}`);
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(sseCalls.some((call) => call.path === "runs/r_1/events")).toBe(true);
    });

    const replayCall = sseCalls.findIndex((call) => call.path === "runs/r_1/events");
    emit(replayCall, {
      type: "error",
      code: "INTERRUPTED",
      message: "运行已中断（进程重启）",
      run_id: "r_1",
      seq: 2,
    }, 2);

    const state = store.getState();
    expect(state.activeRuns[sessionId]).toBeUndefined();
    expect(state.runRecoveryNoticeBySession[sessionId]).toMatchObject({
      runId: "r_1",
      status: "interrupted",
    });
    expect(state.isStreaming).toBe(false);
    expect(state.messages.at(-1)?.content).toContain("❌ 运行已中断（进程重启）");
    // Text already rendered before the interruption is not replayed twice.
    expect((state.messages.at(-1)?.content as string).match(/A/g)).toHaveLength(1);
  });

  it("restores a pending approval after refresh and resumes by run id", async () => {
    seedRunRecord("r_1", 5, "awaiting_confirmation");
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === `sessions/${sessionId}/messages`) {
        return {
          messages: [
            { id: "u1", role: "user", content: "写文件", created_at: "2026-11-05T00:00:00Z" },
            { id: "a1", role: "assistant", content: "需要确认", created_at: "2026-11-05T00:00:01Z" },
          ],
        };
      }
      if (path === "runs/r_1") {
        return {
          status: "awaiting_confirmation",
          last_event_seq: 5,
          pending_approvals: [{
            id: "approval-1",
            run_id: "r_1",
            call_id: "call-1",
            tool_name: "write_file",
            args: { path: "main.c" },
            risk_level: "high",
            status: "pending",
          }],
        };
      }
      throw new Error(`unexpected GET: ${path}`);
    });

    const store = await importStore();
    await store.getState().fetchMessages(sessionId);
    await vi.waitFor(() => {
      expect(store.getState().pendingConfirm?.calls).toHaveLength(1);
    });

    expect(store.getState().pendingConfirm).toEqual({
      calls: [{ name: "write_file", args: { path: "main.c" }, call_id: "call-1", risk_level: "high" }],
      count: 1,
      sessionId,
    });
    expect(store.getState()._lastAgentPayload).toBeNull();

    store.getState().resumeAgent("allow");
    expect(apiSSEMock).toHaveBeenCalledTimes(1);
    const [path, body] = apiSSEMock.mock.calls[0];
    expect(path).toBe("agent-sandbox/resume");
    expect(body).toEqual({ decision: "allow", call_id: "call-1", run_id: "r_1" });
  });
});
