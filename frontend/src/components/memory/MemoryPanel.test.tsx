import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryPanel } from "./MemoryPanel";
import { useAppStore } from "../../stores/useAppStore";
import type { MemoryEntry } from "../../types/memory";

const { listMemoryMock, createMemoryMock, updateMemoryMock, deleteMemoryMock } = vi.hoisted(() => ({
  listMemoryMock: vi.fn(),
  createMemoryMock: vi.fn(),
  updateMemoryMock: vi.fn(),
  deleteMemoryMock: vi.fn(),
}));

vi.mock("../../api/memory", () => ({
  listMemory: listMemoryMock,
  createMemory: createMemoryMock,
  updateMemory: updateMemoryMock,
  deleteMemory: deleteMemoryMock,
}));

// useSettingsStore calls fetchSettings at module load; keep the real client
// (and its store cycle) out of these unit tests.
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

function memoryEntry(overrides: Partial<MemoryEntry> = {}): MemoryEntry {
  return {
    id: "mem-1",
    scope: "project",
    scope_key: "E:/projects/board",
    kind: "fact",
    content: "The board uses 3.3V logic.",
    structured: {},
    source: "user",
    confidence: 0.9,
    created_at: "2026-11-05T08:00:00Z",
    updated_at: "2026-11-05T09:00:00Z",
    last_used_at: null,
    expires_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
  listMemoryMock.mockReset().mockResolvedValue([]);
  createMemoryMock.mockReset();
  updateMemoryMock.mockReset();
  deleteMemoryMock.mockReset();
});

afterEach(cleanup);

describe("MemoryPanel", () => {
  it("renders entries with scope/kind/source badges, confidence and TTL", async () => {
    listMemoryMock.mockResolvedValue([
      memoryEntry({ expires_at: "2026-12-01T00:00:00Z" }),
      memoryEntry({ id: "mem-2", scope: "global", scope_key: "", kind: "preference", content: "Prefer concise answers.", confidence: 0.7, source: "agent" }),
    ]);

    render(<MemoryPanel />);

    await screen.findByText("The board uses 3.3V logic.");
    expect(screen.getAllByText("项目").length).toBeGreaterThan(0);
    expect(screen.getAllByText("事实").length).toBeGreaterThan(0);
    expect(screen.getAllByText("用户").length).toBeGreaterThan(0);
    expect(screen.getByText("置信度 0.90")).toBeTruthy();
    expect(screen.getAllByText(/过期时间: /).length).toBeGreaterThan(0);
    expect(screen.getByText("Prefer concise answers.")).toBeTruthy();
    expect(screen.getAllByText("全局").length).toBeGreaterThan(0);
    expect(screen.getAllByText("偏好").length).toBeGreaterThan(0);
    expect(screen.getByText("Agent")).toBeTruthy();
    expect(screen.getByText(/永不过期/)).toBeTruthy();
  });

  it("filters by scope/kind and searches with a debounced query", async () => {
    listMemoryMock.mockResolvedValue([memoryEntry()]);
    render(<MemoryPanel />);
    await screen.findByText("The board uses 3.3V logic.");
    await waitFor(() => expect(listMemoryMock).toHaveBeenCalledWith({
      scope: "",
      tier: "",
      status: "active",
      project_id: "",
      kind: "",
      q: "",
      limit: 200,
    }));

    fireEvent.change(screen.getByLabelText("按范围筛选"), { target: { value: "global" } });
    await waitFor(() => expect(listMemoryMock).toHaveBeenLastCalledWith(expect.objectContaining({ scope: "global" })));

    fireEvent.change(screen.getByLabelText("按类型筛选"), { target: { value: "decision" } });
    await waitFor(() => expect(listMemoryMock).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "decision" })));

    fireEvent.change(screen.getByLabelText("搜索记忆…"), { target: { value: "供电" } });
    await waitFor(
      () => expect(listMemoryMock).toHaveBeenLastCalledWith(expect.objectContaining({ q: "供电" })),
      { timeout: 2000 },
    );
  });

  it("filters by tier, status and project id", async () => {
    listMemoryMock.mockResolvedValue([memoryEntry()]);
    render(<MemoryPanel />);
    await screen.findByText("The board uses 3.3V logic.");

    fireEvent.change(screen.getByLabelText("按层级筛选"), { target: { value: "user" } });
    await waitFor(() => expect(listMemoryMock).toHaveBeenLastCalledWith(expect.objectContaining({ tier: "user" })));

    fireEvent.change(screen.getByLabelText("按状态筛选"), { target: { value: "proposed" } });
    await waitFor(() => expect(listMemoryMock).toHaveBeenLastCalledWith(expect.objectContaining({ status: "proposed" })));

    fireEvent.change(screen.getByLabelText("按项目筛选"), { target: { value: "proj-42" } });
    await waitFor(
      () => expect(listMemoryMock).toHaveBeenLastCalledWith(expect.objectContaining({ project_id: "proj-42" })),
      { timeout: 2000 },
    );
  });

  it("shows tier, status, project and evidence for v2 entries", async () => {
    listMemoryMock.mockResolvedValue([
      memoryEntry({
        tier: "user",
        status: "proposed",
        project_id: "proj-42",
        evidence: { seal_id: "seal-9", field: "decisions" },
      }),
    ]);
    render(<MemoryPanel />);

    await screen.findByText("The board uses 3.3V logic.");
    expect(screen.getAllByText("待审核").length).toBeGreaterThan(0);
    expect(screen.getByText(/项目 ID: proj-42/)).toBeTruthy();
    expect(screen.getByText(/证据: seal_id: seal-9/)).toBeTruthy();
    expect(screen.getByText(/field: decisions/)).toBeTruthy();
  });

  it("creates a memory entry with the selected scope key and confidence", async () => {
    listMemoryMock.mockResolvedValue([]);
    createMemoryMock.mockResolvedValue(memoryEntry({ id: "mem-new", content: "Use SWD for flashing." }));
    render(<MemoryPanel />);
    await screen.findByText("暂无记忆条目");

    fireEvent.click(screen.getByRole("button", { name: "新增记忆" }));
    fireEvent.change(screen.getByLabelText("范围键"), { target: { value: "E:/projects/board" } });
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "Use SWD for flashing." } });
    fireEvent.change(screen.getByLabelText("置信度"), { target: { value: "0.85" } });
    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));

    await screen.findByText("记忆已创建。");
    expect(createMemoryMock).toHaveBeenCalledWith({
      scope: "project",
      scope_key: "E:/projects/board",
      kind: "fact",
      content: "Use SWD for flashing.",
      source: "user",
      confidence: 0.85,
      expires_at: null,
    });
    expect(await screen.findByText("Use SWD for flashing.")).toBeTruthy();
  });

  it("creates a user-tier memory without a scope key", async () => {
    listMemoryMock.mockResolvedValue([]);
    createMemoryMock.mockResolvedValue(memoryEntry({ id: "mem-user", scope: "global", scope_key: "", tier: "user" }));
    render(<MemoryPanel />);
    await screen.findByText("暂无记忆条目");

    fireEvent.click(screen.getByRole("button", { name: "新增记忆" }));
    fireEvent.change(screen.getByLabelText("层级"), { target: { value: "user" } });
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "Prefer metric units." } });
    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));

    await screen.findByText("记忆已创建。");
    expect(createMemoryMock).toHaveBeenCalledWith({
      scope: "global",
      scope_key: "",
      kind: "fact",
      content: "Prefer metric units.",
      source: "user",
      confidence: 0.7,
      expires_at: null,
      tier: "user",
    });
  });

  it("requires a session id when creating working memory", async () => {
    listMemoryMock.mockResolvedValue([]);
    createMemoryMock.mockResolvedValue(memoryEntry({ id: "mem-working", scope: "session", scope_key: "sess-9", tier: "working" }));
    render(<MemoryPanel />);
    await screen.findByText("暂无记忆条目");

    fireEvent.click(screen.getByRole("button", { name: "新增记忆" }));
    fireEvent.change(screen.getByLabelText("层级"), { target: { value: "working" } });
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "Session note." } });
    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));
    expect(screen.getByRole("alert").textContent).toContain("项目或会话记忆需要填写范围键。");
    expect(createMemoryMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("范围键"), { target: { value: "sess-9" } });
    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));

    await screen.findByText("记忆已创建。");
    expect(createMemoryMock).toHaveBeenCalledWith({
      scope: "session",
      scope_key: "sess-9",
      kind: "fact",
      content: "Session note.",
      source: "user",
      confidence: 0.7,
      expires_at: null,
      tier: "working",
    });
  });

  it("rejects a project memory without a scope key", async () => {
    render(<MemoryPanel />);
    await screen.findByText("暂无记忆条目");

    fireEvent.click(screen.getByRole("button", { name: "新增记忆" }));
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "Needs a scope key" } });
    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));

    expect(screen.getByRole("alert").textContent).toContain("项目或会话记忆需要填写范围键。");
    expect(createMemoryMock).not.toHaveBeenCalled();
  });

  it("edits content, confidence and expiry without changing scope or kind", async () => {
    listMemoryMock.mockResolvedValue([memoryEntry()]);
    updateMemoryMock.mockResolvedValue(memoryEntry({ content: "The board uses 5V logic.", confidence: 0.6, expires_at: null }));
    render(<MemoryPanel />);
    await screen.findByText("The board uses 3.3V logic.");

    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    fireEvent.change(screen.getByLabelText("内容"), { target: { value: "The board uses 5V logic." } });
    fireEvent.change(screen.getByLabelText("置信度"), { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await screen.findByText("记忆已更新。");
    expect(updateMemoryMock).toHaveBeenCalledWith("mem-1", {
      content: "The board uses 5V logic.",
      confidence: 0.6,
      expires_at: null,
    });
    expect(await screen.findByText("The board uses 5V logic.")).toBeTruthy();
  });

  it("soft-deletes after an inline confirmation", async () => {
    listMemoryMock.mockResolvedValue([memoryEntry()]);
    deleteMemoryMock.mockResolvedValue(undefined);
    render(<MemoryPanel />);
    await screen.findByText("The board uses 3.3V logic.");

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await screen.findByText("删除成功");
    expect(deleteMemoryMock).toHaveBeenCalledWith("mem-1");
    await screen.findByText("暂无记忆条目");
  });

  it("shows a clear error state with retry", async () => {
    listMemoryMock.mockRejectedValueOnce(new Error("backend offline")).mockResolvedValueOnce([memoryEntry()]);
    render(<MemoryPanel />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("加载记忆失败");
    expect(alert.textContent).toContain("backend offline");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("The board uses 3.3V logic.")).toBeTruthy();
  });
});
