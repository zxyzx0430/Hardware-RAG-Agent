import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RunStatusBar from "./RunStatusBar";
import { useChatStore, type RunView } from "../../stores/useChatStore";
import { useAppStore } from "../../stores/useAppStore";

const sessionId = "s1";
const originalActions = {
  cancelActiveRun: useChatStore.getState().cancelActiveRun,
  resumeInterruptedRun: useChatStore.getState().resumeInterruptedRun,
  dismissRunView: useChatStore.getState().dismissRunView,
};

function makeView(patch: Partial<RunView> = {}): RunView {
  return {
    runId: "r_abcdef123456789",
    status: "running",
    phase: "live",
    startedAt: Date.now() - 12_000,
    finishedAt: null,
    lastEventType: "tool_call",
    lastEventSeq: 7,
    lastEventAt: Date.now() - 3_000,
    currentTool: "search_datasheet",
    currentStepIndex: 2,
    checkpointId: null,
    errorCode: null,
    errorMessage: null,
    pendingApprovals: 0,
    resultUnknown: false,
    hardwareUnknown: false,
    resumable: false,
    actionPending: false,
    ...patch,
  };
}

function resetStore(view?: RunView, extra: Partial<ReturnType<typeof useChatStore.getState>> = {}): void {
  useChatStore.setState({
    activeSessionId: sessionId,
    messages: [],
    streamingSteps: [],
    runViewBySession: view ? { [sessionId]: view } : {},
    runTimelineBySession: {},
    runRecoveryNoticeBySession: {},
    ...originalActions,
    ...extra,
  });
}

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
});

afterEach(() => {
  cleanup();
  resetStore();
  vi.restoreAllMocks();
});

describe("RunStatusBar", () => {
  it("renders a live run with state, run id, current tool and elapsed time", () => {
    resetStore(makeView());

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);

    expect(screen.getByText("运行中")).toBeTruthy();
    expect(screen.getByText(/r_abcdef123456/)).toBeTruthy();
    expect(screen.getByText(/当前工具: search_datasheet/)).toBeTruthy();
    expect(screen.getByText(/步骤 2/)).toBeTruthy();
    expect(screen.getByText(/耗时/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "取消执行" })).toBeTruthy();
  });

  it("uses the client send clock for a live run so it matches Activity", () => {
    const now = 1_000_000;
    vi.spyOn(Date, "now").mockReturnValue(now);
    resetStore(
      makeView({ startedAt: now - 10_000 }),
      { streamingStartTime: now - 30_000 },
    );

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);

    expect(screen.getByText(/30\.00秒/)).toBeTruthy();
    expect(screen.queryByText(/10\.00秒/)).toBeNull();
  });

  it("falls back to backend started_at when no client start exists", () => {
    const now = 1_000_000;
    vi.spyOn(Date, "now").mockReturnValue(now);
    resetStore(makeView({ startedAt: now - 12_000 }), { streamingStartTime: null });

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);

    expect(screen.getByText(/12\.00秒/)).toBeTruthy();
  });

  it("shows the restoring banner while reattaching", () => {
    resetStore(makeView({ phase: "restoring", lastEventType: null }));

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);

    expect(screen.getByText("正在恢复这次执行…")).toBeTruthy();
  });

  it("renders the approval state with the pending count", () => {
    resetStore(makeView({
      status: "awaiting_confirmation",
      pendingApprovals: 2,
      currentTool: "write_file",
    }));

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);

    expect(screen.getByText("等待审批")).toBeTruthy();
    expect(screen.getByText(/2 个操作待确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "取消执行" })).toBeTruthy();
  });

  it("warns that a hardware side effect is unknown and offers no blind resume", () => {
    resetStore(makeView({
      status: "interrupted",
      phase: "terminal",
      finishedAt: Date.now(),
      currentTool: "flash_firmware",
      resultUnknown: true,
      hardwareUnknown: true,
      resumable: false,
      errorMessage: "运行已中断，硬件操作结果未知，请核对设备状态后再继续。",
    }));

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);

    expect(screen.getByText("已中断")).toBeTruthy();
    expect(screen.getByText(/结果未知，请核对设备状态/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "从检查点继续" })).toBeNull();
    expect(screen.getByRole("button", { name: "关闭执行状态" })).toBeTruthy();
  });

  it("offers resume for an interrupted run with a checkpoint and calls the store action", () => {
    const resumeInterruptedRun = vi.fn().mockResolvedValue(true);
    resetStore(
      makeView({
        status: "interrupted",
        phase: "terminal",
        finishedAt: Date.now(),
        resumable: true,
        checkpointId: "cp_1",
        errorMessage: "运行已中断，可从检查点继续。",
      }),
      { resumeInterruptedRun },
    );

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);
    expect(screen.getByText("运行已中断，可从检查点继续。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从检查点继续" }));

    expect(resumeInterruptedRun).toHaveBeenCalledWith(sessionId);
  });

  it("calls cancel for a live run", () => {
    const cancelActiveRun = vi.fn().mockResolvedValue(true);
    resetStore(makeView(), { cancelActiveRun });

    render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "取消执行" }));
    expect(cancelActiveRun).toHaveBeenCalledWith(sessionId);
  });

  it("expands a compact timeline and opens a source in the existing panel", () => {
    const onOpenSource = vi.fn();
    resetStore(makeView());
    useChatStore.setState({
      runTimelineBySession: {
        [sessionId]: [
          { seq: 1, type: "run_started", at: Date.now() - 5000 },
          { seq: 2, type: "tool_call", at: Date.now() - 4000, tool: "search_datasheet", callId: "c1" },
          {
            seq: 3,
            type: "source",
            at: Date.now() - 3000,
            sourceId: "src-1",
            sourceTitle: "ESP32 datasheet",
            messageId: "a1",
          },
        ],
      },
    });

    render(<RunStatusBar sessionId={sessionId} onOpenSource={onOpenSource} />);
    fireEvent.click(screen.getByRole("button", { name: "事件时间线" }));

    expect(screen.getByText("开始执行")).toBeTruthy();
    expect(screen.getByText("调用工具")).toBeTruthy();
    expect(screen.getByText("检索来源")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "ESP32 datasheet" }));
    expect(onOpenSource).toHaveBeenCalledWith("src-1", "a1");
  });

  it("renders nothing when the session has no run", () => {
    resetStore();
    const { container } = render(<RunStatusBar sessionId={sessionId} onOpenSource={vi.fn()} />);
    expect(container.querySelector("[data-testid='run-status-bar']")).toBeNull();
  });
});
