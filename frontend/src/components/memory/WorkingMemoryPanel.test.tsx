import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkingMemoryPanel } from "./WorkingMemoryPanel";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";

const { getWorkingMemoryMock } = vi.hoisted(() => ({
  getWorkingMemoryMock: vi.fn(),
}));

vi.mock("../../api/memory", () => ({
  getWorkingMemory: getWorkingMemoryMock,
}));

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

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
  useChatStore.setState({ activeSessionId: "" });
  getWorkingMemoryMock.mockReset();
});

afterEach(cleanup);

describe("WorkingMemoryPanel", () => {
  it("shows an empty state when there is no active session", () => {
    render(<WorkingMemoryPanel />);

    expect(screen.getByText("没有活动会话")).toBeTruthy();
    expect(getWorkingMemoryMock).not.toHaveBeenCalled();
  });

  it("renders the nine fields read-only with snapshot metadata", async () => {
    useChatStore.setState({ activeSessionId: "sess-1" });
    getWorkingMemoryMock.mockResolvedValue({
      body: {
        goal: [{ text: "Fix the I2C NACK", source: { kind: "message" } }],
        constraints: ["3.3V logic", "Do not change pinout"],
        completed: [],
        current_state: "Bus scan fails at address 0x68",
        decisions: [{ text: "Use 100kHz" }, { text: "Enable pull-ups" }],
        failed_attempts: [],
        open_questions: ["Is the sensor powered?"],
        artifacts_and_evidence: [{ text: "scope capture i2c.png" }],
        next_action: "Probe SDA/SCL",
      },
      seal_id: "seal-1",
      generation: 3,
      created_at: "2026-11-06T01:00:00Z",
      updated_at: "2026-11-06T02:00:00Z",
    });

    render(<WorkingMemoryPanel />);

    expect(await screen.findByText("Fix the I2C NACK")).toBeTruthy();
    for (const label of [
      "目标", "约束", "已完成", "当前状态", "决策",
      "失败尝试", "待解决问题", "产物与证据", "下一步",
    ]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByText("3.3V logic")).toBeTruthy();
    expect(screen.getByText("Enable pull-ups")).toBeTruthy();
    expect(screen.getByText("无已验证完成项")).toBeTruthy();
    expect(screen.getByText(/seal-1/)).toBeTruthy();
    expect(screen.getByText(/代次: 3/)).toBeTruthy();
    expect(screen.getByText(/更新时间/)).toBeTruthy();
  });

  it("shows an empty state when the session has no snapshot", async () => {
    useChatStore.setState({ activeSessionId: "sess-2" });
    getWorkingMemoryMock.mockResolvedValue(null);

    render(<WorkingMemoryPanel />);

    expect(await screen.findByText("暂无工作记忆")).toBeTruthy();
  });

  it("shows an error state and retries loading", async () => {
    useChatStore.setState({ activeSessionId: "sess-3" });
    getWorkingMemoryMock
      .mockRejectedValueOnce(new Error("snapshot backend down"))
      .mockResolvedValueOnce({
        body: { goal: "Recovered goal" },
        seal_id: "seal-2",
        generation: 1,
        created_at: "2026-11-06T01:00:00Z",
        updated_at: "2026-11-06T01:00:00Z",
      });

    render(<WorkingMemoryPanel />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("加载工作记忆失败");
    expect(alert.textContent).toContain("snapshot backend down");

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("Recovered goal")).toBeTruthy();
  });
});
