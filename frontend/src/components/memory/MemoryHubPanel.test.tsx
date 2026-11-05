import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryHubPanel } from "./MemoryHubPanel";
import { useAppStore } from "../../stores/useAppStore";

vi.mock("../../api/memory", () => ({
  listMemory: vi.fn().mockResolvedValue([]),
  createMemory: vi.fn(),
  updateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  listHardwareProfiles: vi.fn().mockResolvedValue([]),
  getHardwareProfile: vi.fn(),
  upsertHardwareProfile: vi.fn(),
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
});

afterEach(cleanup);

describe("MemoryHubPanel", () => {
  it("switches between memory entries and hardware profiles", async () => {
    render(<MemoryHubPanel />);

    expect(screen.getByRole("heading", { level: 3, name: "记忆与硬件档案" })).toBeTruthy();
    expect(await screen.findByText("暂无记忆条目")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "硬件档案" }));
    expect(await screen.findByText("暂无硬件档案")).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("不会自动注入");

    fireEvent.click(screen.getByRole("button", { name: "记忆条目" }));
    expect(await screen.findByText("暂无记忆条目")).toBeTruthy();
  });
});
