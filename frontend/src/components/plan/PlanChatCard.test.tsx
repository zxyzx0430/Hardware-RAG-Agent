import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlanChatCard } from "./PlanChatCard";

const mocks = vi.hoisted(() => {
  const plan = {
    id: "pl_1",
    session_id: "s1",
    version: 1,
    status: "pending" as const,
    title: "Add run report",
    summary: "Collect run events.",
    steps: [{ title: "Inspect RunStore" }, { title: "Render report" }],
    risks: [],
    open_questions: [],
    file_path: "E:/data/plans/s1/plan-v1.md",
    created_at: "2026-11-07T10:00:00Z",
  };
  const chatState = {
    activeSessionId: "s1",
    sessionPlans: { s1: plan },
    isStreaming: false,
    approvePlan: vi.fn(async () => ({ ...plan, status: "approved" })),
    rejectPlan: vi.fn(async () => ({ ...plan, status: "rejected" })),
    executeApprovedPlan: vi.fn(),
    setPlanMode: vi.fn(),
  };
  const appState = {
    setRightPanelOpen: vi.fn(),
    setRightMode: vi.fn(),
    setRightContentTab: vi.fn(),
  };
  const toastState = { showInfo: vi.fn() };
  return { plan, chatState, appState, toastState };
});

vi.mock("../../stores/useChatStore", () => ({
  useChatStore: (selector: (state: typeof mocks.chatState) => unknown) => selector(mocks.chatState),
}));
vi.mock("../../stores/useAppStore", () => ({
  useAppStore: (selector: (state: typeof mocks.appState) => unknown) => selector(mocks.appState),
}));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => mocks.toastState },
}));

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(cleanup);

describe("PlanChatCard", () => {
  it("renders the three plan actions and opens the right plan panel", () => {
    render(<PlanChatCard />);

    expect(screen.getByText("Add run report")).toBeTruthy();
    expect(screen.getByRole("button", { name: "按计划实施" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "补充信息" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "取消" })).toBeTruthy();
    expect(screen.getByText(/plan-v1\.md/)).toBeTruthy();
    expect(mocks.appState.setRightPanelOpen).toHaveBeenCalledWith(true);
    expect(mocks.appState.setRightMode).toHaveBeenCalledWith("content");
    expect(mocks.appState.setRightContentTab).toHaveBeenCalledWith("s1", "plan");
  });

  it("approves and executes the plan", async () => {
    render(<PlanChatCard />);

    fireEvent.click(screen.getByRole("button", { name: "按计划实施" }));

    await waitFor(() => expect(mocks.chatState.approvePlan).toHaveBeenCalledWith("s1", "pl_1"));
    await waitFor(() => expect(mocks.chatState.executeApprovedPlan).toHaveBeenCalled());
  });

  it("keeps the current permission mode when asking for more information", () => {
    render(<PlanChatCard />);

    fireEvent.click(screen.getByRole("button", { name: "补充信息" }));

    expect(mocks.chatState.setPlanMode).not.toHaveBeenCalled();
    expect(mocks.toastState.showInfo).toHaveBeenCalled();
  });

  it("rejects the plan when cancelled", async () => {
    render(<PlanChatCard />);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(mocks.chatState.rejectPlan).toHaveBeenCalledWith("s1", "pl_1"));
  });
});
