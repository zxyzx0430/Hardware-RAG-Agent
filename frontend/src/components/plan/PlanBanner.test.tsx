import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlanBanner } from "./PlanBanner";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import type { Plan } from "../../types/plan";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({ settings: {} }),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  parseSseEventId: vi.fn(() => null),
  ApiError: class ApiError extends Error {},
  fetchBigChunk: vi.fn(),
}));

const { approvePlanMock, rejectPlanMock } = vi.hoisted(() => ({
  approvePlanMock: vi.fn(),
  rejectPlanMock: vi.fn(),
}));

vi.mock("../../api/plan", () => ({
  approveSessionPlan: approvePlanMock,
  rejectSessionPlan: rejectPlanMock,
  fetchLatestPlan: vi.fn().mockResolvedValue(null),
  listSessionPlans: vi.fn().mockResolvedValue([]),
}));

vi.mock("../../api/todo", () => ({
  fetchSessionTodos: vi.fn().mockResolvedValue({ todos: [], updatedAt: null }),
  clearSessionTodos: vi.fn().mockResolvedValue(undefined),
}));

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: "pl_1",
    session_id: "s1",
    version: 2,
    status: "pending",
    title: "Bring up the I2C sensor",
    summary: "Plan summary",
    steps: [{ title: "Inspect datasheet" }],
    risks: [],
    open_questions: [],
    run_id: "r_1",
    executed_run_id: null,
    created_at: "2026-11-06T10:00:00Z",
    decided_at: null,
    ...overrides,
  };
}

function seed(plan: Plan | null, isStreaming = false) {
  useChatStore.setState({
    activeSessionId: "s1",
    sessionPlans: { s1: plan },
    sessionPlanLoaded: { s1: true },
    isStreaming,
  });
}

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
  approvePlanMock.mockReset();
  rejectPlanMock.mockReset();
  useChatStore.setState({
    activeSessionId: "s1",
    sessionPlans: {},
    sessionPlanLoaded: {},
    isStreaming: false,
    planMode: false,
  });
});

afterEach(cleanup);

describe("PlanBanner", () => {
  it("shows a pending plan with view, approve and reject actions", () => {
    seed(makePlan());

    render(<PlanBanner />);

    expect(screen.getByText("计划 v2 待批准")).toBeTruthy();
    expect(screen.getByText("Bring up the I2C sensor")).toBeTruthy();
    expect(screen.getByRole("button", { name: "查看" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "批准并执行" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeTruthy();
  });

  it("approves the plan, then sends the approved-plan execution turn", async () => {
    const pending = makePlan();
    const approved = makePlan({ status: "approved", decided_at: "2026-11-06T10:05:00Z" });
    approvePlanMock.mockResolvedValue(approved);
    seed(pending);
    const sendMessage = vi.fn();
    useChatStore.setState({ sendMessage });

    render(<PlanBanner />);
    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));

    await waitFor(() => expect(approvePlanMock).toHaveBeenCalledWith("s1", "pl_1"));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith(
      "按照已批准的计划开始执行。",
      undefined,
      undefined,
      undefined,
      { approvedPlanId: "pl_1" },
    ));
    expect(useChatStore.getState().planMode).toBe(false);
    expect(useChatStore.getState().sessionPlans.s1).toEqual(approved);
  });

  it("shows 开始执行 for an approved plan without an executed run and resends on click", () => {
    seed(makePlan({ status: "approved", executed_run_id: null }));
    const sendMessage = vi.fn();
    useChatStore.setState({ sendMessage });

    render(<PlanBanner />);
    const button = screen.getByRole("button", { name: "开始执行" });
    expect(button).toBeTruthy();
    fireEvent.click(button);

    expect(sendMessage).toHaveBeenCalledWith(
      "按照已批准的计划开始执行。",
      undefined,
      undefined,
      undefined,
      { approvedPlanId: "pl_1" },
    );
  });

  it("disables plan actions while streaming", () => {
    seed(makePlan(), true);

    render(<PlanBanner />);

    expect(screen.getByRole("button", { name: "批准并执行" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "拒绝" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "查看" }).hasAttribute("disabled")).toBe(false);
  });

  it("rejects the plan and hides the banner after the status changes", async () => {
    const pending = makePlan();
    rejectPlanMock.mockResolvedValue(makePlan({ status: "rejected", decided_at: "2026-11-06T10:06:00Z" }));
    seed(pending);

    render(<PlanBanner />);
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));

    await waitFor(() => expect(rejectPlanMock).toHaveBeenCalledWith("s1", "pl_1"));
    await waitFor(() => expect(screen.queryByText("计划 v2 待批准")).toBeNull());
    // Rejecting keeps plan mode so the user can revise instead of silently
    // falling back to the normal permission mode.
    expect(useChatStore.getState().planMode).toBe(true);
  });

  it("renders nothing without a pending or approved plan", () => {
    seed(makePlan({ status: "rejected" }));
    const { container } = render(<PlanBanner />);
    expect(container.firstChild).toBeNull();
  });
});
