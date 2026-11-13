import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet } from "../../api/client";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import type { BackendKBDoc } from "../../types/api";
import { KnowledgePanel } from "./KnowledgePanel";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  fetchBigChunk: vi.fn(),
}));

const get = vi.mocked(apiGet);

const documents: BackendKBDoc[] = [
  { doc_id: "doc-1", title: "stm32-datasheet.pdf", file_type: "PDF", file_size: 4096, chunk_count: 12, status: "indexed", created_at: "2026-11-01T08:00:00", kb_id: "kb-1" },
  { doc_id: "doc-2", title: "esp32-wifi.md", file_type: "Markdown", file_size: 512, chunk_count: 0, status: "error", created_at: "2026-11-02T08:00:00", kb_id: "kb-2", error_message: "parse failed" },
];

function resetKnowledgeStore() {
  useKnowledgeStore.setState({
    items: [],
    collections: [],
    activeKbId: "builtin-001",
    uploadProgress: {},
    isUploading: false,
  });
}

beforeEach(() => {
  localStorage.clear();
  resetKnowledgeStore();
  get.mockReset();
  get.mockImplementation(((path: string) => {
    if (path === "kb/collections") return Promise.resolve({ collections: [] });
    if (path === "kb/list") return Promise.resolve({ documents });
    return Promise.resolve({});
  }) as never);
});

afterEach(() => {
  cleanup();
  resetKnowledgeStore();
});

describe("KnowledgePanel all-documents view", () => {
  it("loads every document without a kb_id filter and titles the page 全部文档", async () => {
    render(<KnowledgePanel />);

    expect(await screen.findByRole("heading", { name: "全部文档" })).toBeTruthy();
    await waitFor(() => expect(get).toHaveBeenCalledWith("kb/list"));
    expect(get.mock.calls.some(([path]) => String(path).includes("kb_id="))).toBe(false);
    expect(await screen.findByText("stm32-datasheet.pdf")).toBeTruthy();
    expect(screen.getByText("esp32-wifi.md")).toBeTruthy();
  });

  it("filters rows by document name", async () => {
    render(<KnowledgePanel />);
    const search = await screen.findByPlaceholderText("搜索文档名、类型或标签…");
    expect(await screen.findByText("stm32-datasheet.pdf")).toBeTruthy();

    fireEvent.change(search, { target: { value: "esp32" } });

    expect(screen.queryByText("stm32-datasheet.pdf")).toBeNull();
    expect(screen.getByText("esp32-wifi.md")).toBeTruthy();
  });

  it("filters rows by status chip", async () => {
    render(<KnowledgePanel />);
    expect(await screen.findByText("stm32-datasheet.pdf")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "失败" }));

    expect(screen.queryByText("stm32-datasheet.pdf")).toBeNull();
    expect(screen.getByText("esp32-wifi.md")).toBeTruthy();
  });
});
