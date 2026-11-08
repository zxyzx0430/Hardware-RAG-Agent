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
vi.mock("../../stores/useAppStore", () => {
  const state = () => ({ ...mocks.appState, lang: "zh", themeMode: "light" });
  const useAppStore = Object.assign(
    (selector: (value: ReturnType<typeof state>) => unknown) => selector(state()),
    { getState: state, subscribe: () => () => {} },
  );
  return { useAppStore };
});
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => mocks.toastState },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.chatState.sessionPlans = { s1: mocks.plan };
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

  it("renders summary markdown without leaking source syntax", () => {
    const summary = [
      "**结论**：先读 `0x68`。",
      "",
      "| 引脚 | 电平 |",
      "| --- | --- |",
      "| SDA | 3.3V |",
      "",
      "```bash",
      "i2cdetect -y 1",
      "```",
    ].join("\n");
    mocks.chatState.sessionPlans = { s1: { ...mocks.plan, summary } };

    const { container } = render(<PlanChatCard />);

    expect(container.querySelector(".plan-chat-markdown strong")?.textContent).toBe("结论");
    expect(container.querySelector(".plan-chat-markdown table")).toBeTruthy();
    expect(container.querySelector(".plan-chat-markdown pre")).toBeTruthy();
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("```");
  });

  it("does not expose source citations in the plan card", () => {
    mocks.chatState.sessionPlans = {
      s1: {
        ...mocks.plan,
        title: "Add run report[src1]",
        summary: "Collect run events.[src6]",
        steps: [{ title: "Inspect RunStore[src9]" }],
      },
    };

    const { container } = render(<PlanChatCard />);

    expect(container.textContent).not.toContain("[src");
    expect(screen.getByText("Add run report")).toBeTruthy();
    expect(screen.getByText("Collect run events.")).toBeTruthy();
    expect(screen.getByText("Inspect RunStore")).toBeTruthy();
  });

  it("rejects the plan when cancelled", async () => {
    render(<PlanChatCard />);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(mocks.chatState.rejectPlan).toHaveBeenCalledWith("s1", "pl_1"));
  });
});
