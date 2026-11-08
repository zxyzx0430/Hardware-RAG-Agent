import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlanPanel } from "./PlanPanel";
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

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: "pl_1",
    session_id: "s1",
    version: 3,
    status: "approved",
    title: "Bring up the I2C sensor",
    summary: "Read the datasheet, wire the bus, then validate the address.",
    steps: [
      { title: "Inspect datasheet", detail: "Confirm the 7-bit address.", verification: "Address is 0x68" },
      { title: "Wire the bus" },
    ],
    risks: ["Bus contention with the EEPROM"],
    open_questions: ["Is the sensor externally powered?"],
    run_id: "r_1",
    executed_run_id: "run_42",
    created_at: "2026-11-06T10:00:00Z",
    decided_at: "2026-11-06T10:05:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
  useChatStore.setState({
    activeSessionId: "s1",
    sessionPlans: { s1: makePlan() },
    sessionPlanLoaded: { s1: true },
  });
});

afterEach(cleanup);

describe("PlanPanel", () => {
  it("renders the plan as markdown headings, lists and bold step titles", () => {
    const plan = makePlan({ title: "<script>alert(1)</script>" });
    useChatStore.setState({ sessionPlans: { s1: plan } });

    const { container } = render(<PlanPanel />);

    expect(screen.getByRole("heading", { level: 2, name: "<script>alert(1)</script>" })).toBeTruthy();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(plan.summary)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "步骤" })).toBeTruthy();
    const steps = container.querySelectorAll(".plan-panel-markdown ol > li");
    expect(steps).toHaveLength(2);
    expect(steps[0].textContent).toContain("Inspect datasheet");
    expect(steps[0].textContent).toContain("Confirm the 7-bit address.");
    expect(steps[0].textContent).toContain("Address is 0x68");
    expect(screen.getByText("Bus contention with the EEPROM")).toBeTruthy();
    expect(screen.getByText("Is the sensor externally powered?")).toBeTruthy();
    expect(screen.getByText("已批准")).toBeTruthy();
    expect(screen.getAllByText(/v3/).length).toBeGreaterThan(0);
    expect(screen.getByText(/run_42/)).toBeTruthy();
  });

  it("renders inline formatting, tables and inline code from plan fields", () => {
    const plan = makePlan({
      summary: "**结论**：先读 `0x68` 手册。\n\n| 引脚 | 电平 |\n| --- | --- |\n| SDA | 3.3V |",
      steps: [{
        title: "Inspect datasheet",
        detail: "确认地址 `0x68`",
        verification: "运行 `i2cdetect`",
      }],
    });
    useChatStore.setState({ sessionPlans: { s1: plan } });

    const { container } = render(<PlanPanel />);

    expect(container.querySelector(".plan-panel-markdown h2")).toBeTruthy();
    expect(container.querySelectorAll(".plan-panel-markdown h3")).toHaveLength(4);
    expect(container.querySelector(".plan-panel-markdown strong")?.textContent).toBe("结论");
    expect(container.querySelector(".plan-panel-markdown table")).toBeTruthy();
    expect(container.querySelectorAll(".plan-panel-markdown code").length).toBeGreaterThanOrEqual(3);
  });

  it("shows the backend execution outcome and note when present", () => {
    const plan = makePlan({
      status: "approved",
      execution_state: "failed",
      execution_note: "write_file was denied at step 2",
    });
    useChatStore.setState({ sessionPlans: { s1: plan } });

    render(<PlanPanel />);

    expect(screen.getByText("执行失败")).toBeTruthy();
    expect(screen.getByText(/write_file was denied at step 2/)).toBeTruthy();
  });

  it("keeps the legacy approved/executed display when execution fields are absent", () => {
    const plan = makePlan();
    useChatStore.setState({ sessionPlans: { s1: plan } });

    const { container } = render(<PlanPanel />);

    expect(screen.getByText("已批准")).toBeTruthy();
    expect(screen.getByText(/run_42/)).toBeTruthy();
    expect(container.querySelector(".plan-execution")).toBeNull();
  });

  it("shows only the rejected status when the plan was never executed", () => {
    const plan = makePlan({
      status: "rejected",
      executed_run_id: null,
      execution_state: "rejected",
      execution_note: "计划未获批准，未执行",
    });
    useChatStore.setState({ sessionPlans: { s1: plan } });

    const { container } = render(<PlanPanel />);

    expect(screen.getByText("已拒绝")).toBeTruthy();
    expect(screen.queryByText("执行被拒绝")).toBeNull();
    expect(container.querySelector(".plan-execution")).toBeNull();
    expect(screen.queryByText(/计划未获批准，未执行/)).toBeNull();
  });

  it("shows the execution rejection when an approved run's tools were denied", () => {
    const plan = makePlan({
      status: "approved",
      execution_state: "rejected",
      execution_note: "执行中部分工具调用被拒绝，计划未完整执行",
    });
    useChatStore.setState({ sessionPlans: { s1: plan } });

    render(<PlanPanel />);

    expect(screen.getByText("执行被拒绝")).toBeTruthy();
    expect(screen.getByText(/部分工具调用被拒绝/)).toBeTruthy();
  });

  it("shows the empty state when the session has no plan", () => {
    useChatStore.setState({
      activeSessionId: "s2",
      sessionPlans: { s2: null },
      sessionPlanLoaded: { s2: true },
    });

    render(<PlanPanel />);

    expect(screen.getByText("暂无计划")).toBeTruthy();
  });
});
