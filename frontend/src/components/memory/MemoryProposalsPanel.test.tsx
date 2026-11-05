import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryProposalsPanel } from "./MemoryProposalsPanel";
import { useAppStore } from "../../stores/useAppStore";
import type { MemoryEntry } from "../../types/memory";

const { listProposalsMock, approveProposalMock, rejectProposalMock } = vi.hoisted(() => ({
  listProposalsMock: vi.fn(),
  approveProposalMock: vi.fn(),
  rejectProposalMock: vi.fn(),
}));

vi.mock("../../api/memory", () => ({
  listProposals: listProposalsMock,
  approveProposal: approveProposalMock,
  rejectProposal: rejectProposalMock,
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

function proposal(overrides: Partial<MemoryEntry> = {}): MemoryEntry {
  return {
    id: "prop-1",
    tier: "project",
    status: "proposed",
    scope: "project",
    scope_key: "proj-42",
    project_id: "proj-42",
    kind: "decision",
    content: "Use SPI mode 0 for the display.",
    structured: {},
    evidence: { seal_id: "seal-1", generation: 2, field: "decisions", source_ref: { kind: "tool" } },
    source: "agent",
    confidence: 0.8,
    created_at: "2026-11-06T01:00:00Z",
    updated_at: "2026-11-06T01:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
  listProposalsMock.mockReset().mockResolvedValue([]);
  approveProposalMock.mockReset();
  rejectProposalMock.mockReset();
});

afterEach(cleanup);

describe("MemoryProposalsPanel", () => {
  it("renders proposed entries with tier, kind, source, project and evidence", async () => {
    listProposalsMock.mockResolvedValue([proposal()]);

    render(<MemoryProposalsPanel />);

    expect(await screen.findByText("Use SPI mode 0 for the display.")).toBeTruthy();
    expect(screen.getByText("项目")).toBeTruthy();
    expect(screen.getByText("决策")).toBeTruthy();
    expect(screen.getByText("待审核")).toBeTruthy();
    expect(screen.getByText("Agent")).toBeTruthy();
    expect(screen.getByText(/proj-42/)).toBeTruthy();
    expect(screen.getByText(/seal_id: seal-1/)).toBeTruthy();
    expect(screen.getByText(/field: decisions/)).toBeTruthy();
  });

  it("approves a proposal and removes it from the review list", async () => {
    listProposalsMock.mockResolvedValue([proposal()]);
    approveProposalMock.mockResolvedValue(proposal({ status: "active" }));

    render(<MemoryProposalsPanel />);
    const content = await screen.findByText("Use SPI mode 0 for the display.");
    const card = content.closest("div[style]") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "批准" }));

    await screen.findByText("已批准。");
    expect(approveProposalMock).toHaveBeenCalledWith("prop-1");
    expect(screen.queryByText("Use SPI mode 0 for the display.")).toBeNull();
    expect(await screen.findByText("暂无待审核候选")).toBeTruthy();
  });

  it("rejects a proposal and removes it from the review list", async () => {
    listProposalsMock.mockResolvedValue([proposal({ id: "prop-2", content: "Prefer UART logging." })]);
    rejectProposalMock.mockResolvedValue(proposal({ id: "prop-2", status: "rejected" }));

    render(<MemoryProposalsPanel />);
    await screen.findByText("Prefer UART logging.");
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));

    await screen.findByText("已拒绝。");
    expect(rejectProposalMock).toHaveBeenCalledWith("prop-2");
    expect(await screen.findByText("暂无待审核候选")).toBeTruthy();
  });

  it("keeps the proposal and reports an action error", async () => {
    listProposalsMock.mockResolvedValue([proposal()]);
    approveProposalMock.mockRejectedValue(new Error("review conflict"));

    render(<MemoryProposalsPanel />);
    await screen.findByText("Use SPI mode 0 for the display.");
    fireEvent.click(screen.getByRole("button", { name: "批准" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("批准失败");
    expect(alert.textContent).toContain("review conflict");
    expect(screen.getByText("Use SPI mode 0 for the display.")).toBeTruthy();
  });

  it("shows the empty and load-error states", async () => {
    render(<MemoryProposalsPanel />);
    expect(await screen.findByText("暂无待审核候选")).toBeTruthy();

    cleanup();
    listProposalsMock
      .mockRejectedValueOnce(new Error("proposals offline"))
      .mockResolvedValueOnce([proposal()]);

    render(<MemoryProposalsPanel />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("加载待审核候选失败");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(screen.getByText("Use SPI mode 0 for the display.")).toBeTruthy());
  });
});
