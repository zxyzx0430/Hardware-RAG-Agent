import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KBDoc } from "../types/api";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
  apiPatch: vi.fn(),
}));
vi.mock("./useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));
vi.mock("./useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: vi.fn() }) },
}));

describe("knowledge indexing state", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    apiGetMock.mockReset();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("shows the server failure and stops polling", async () => {
    const { useKnowledgeStore } = await import("./useKnowledgeStore");
    const pending = {
      id: "doc-fixed", name: "fixed.md", status: "indexing", chunks: 0,
      enabled: true, size: "1 KB", updatedAt: "2026-10-02", docType: "Markdown", tags: [],
    } as KBDoc;
    useKnowledgeStore.setState({ items: [pending], activeKbId: "kb-fixed", uploadProgress: {
      fixed: { phase: "indexing", percent: 100, chunks: 0 },
    } });
    apiGetMock.mockResolvedValue({ documents: [{
      doc_id: "doc-fixed", kb_id: "kb-fixed", title: "fixed.md",
      status: "error", error_message: "未写入任何可检索向量", chunk_count: 1,
    }] });

    useKnowledgeStore.getState().pollIndexingStatus("doc-fixed", "fixed");
    await vi.advanceTimersByTimeAsync(2000);
    expect(useKnowledgeStore.getState().items[0]).toMatchObject({
      status: "error", errorMessage: "未写入任何可检索向量",
    });
    expect(useKnowledgeStore.getState().uploadProgress.fixed).toBeUndefined();
    await vi.advanceTimersByTimeAsync(6000);
    expect(apiGetMock).toHaveBeenCalledTimes(1);
  });
});
