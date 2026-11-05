import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryHubPanel } from "./MemoryHubPanel";
import { apiPut } from "../../api/client";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSettingsStore } from "../../stores/useSettingsStore";

const {
  listMemoryMock,
  getWorkingMemoryMock,
  listProposalsMock,
  listHardwareProfilesMock,
} = vi.hoisted(() => ({
  listMemoryMock: vi.fn(),
  getWorkingMemoryMock: vi.fn(),
  listProposalsMock: vi.fn(),
  listHardwareProfilesMock: vi.fn(),
}));

vi.mock("../../api/memory", () => ({
  listMemory: listMemoryMock,
  createMemory: vi.fn(),
  updateMemory: vi.fn(),
  deleteMemory: vi.fn(),
  getWorkingMemory: getWorkingMemoryMock,
  listProposals: listProposalsMock,
  approveProposal: vi.fn(),
  rejectProposal: vi.fn(),
  listHardwareProfiles: listHardwareProfilesMock,
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
  useAppStore.setState({ lang: "zh", explorerRootPath: "" });
  useChatStore.setState({ activeSessionId: "" });
  useSettingsStore.setState({ autoMemoryEnabled: false });
  listMemoryMock.mockReset().mockResolvedValue([]);
  getWorkingMemoryMock.mockReset().mockResolvedValue(null);
  listProposalsMock.mockReset().mockResolvedValue([]);
  listHardwareProfilesMock.mockReset().mockResolvedValue([]);
  vi.mocked(apiPut).mockClear();
});

afterEach(cleanup);

describe("MemoryHubPanel", () => {
  it("switches between working memory, entries, proposals and hardware profiles", async () => {
    render(<MemoryHubPanel />);

    expect(screen.getByRole("heading", { level: 3, name: "记忆与硬件档案" })).toBeTruthy();
    expect(await screen.findByText("没有活动会话")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "记忆条目" }));
    expect(await screen.findByText("暂无记忆条目")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "待审核" }));
    expect(await screen.findByText("暂无待审核候选")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "硬件档案" }));
    expect(await screen.findByText("暂无硬件档案")).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("不会自动注入");
  });

  it("persists the auto-memory toggle through /api/settings", async () => {
    render(<MemoryHubPanel />);
    const toggle = screen.getByRole("switch", { name: "自动提取记忆" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    fireEvent.click(toggle);

    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
    expect(apiPut).toHaveBeenCalledWith("settings", { autoMemoryEnabled: true });
  });

  it("shows the explorer project and passes a manual project id to the entry list", async () => {
    useAppStore.setState({ explorerRootPath: "E:\\projects\\board" });
    const { unmount } = render(<MemoryHubPanel />);
    expect(screen.getByText("board")).toBeTruthy();
    expect(screen.getByText("E:\\projects\\board")).toBeTruthy();
    unmount();

    useAppStore.setState({ explorerRootPath: "" });
    render(<MemoryHubPanel />);
    expect(screen.getByText("未检测到打开的项目")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("项目 ID"), { target: { value: "proj-42" } });
    fireEvent.click(screen.getByRole("button", { name: "记忆条目" }));

    await waitFor(() => expect(listMemoryMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ project_id: "proj-42" }),
    ));
  });
});
