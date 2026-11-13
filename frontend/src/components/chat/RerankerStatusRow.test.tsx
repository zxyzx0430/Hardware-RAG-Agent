import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RerankerStatus } from "../../types/session";
import RerankerStatusRow from "./RerankerStatusRow";
import { apiGet, apiPost } from "../../api/client";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

const mockApiGet = vi.mocked(apiGet);
const mockApiPost = vi.mocked(apiPost);

const UNAVAILABLE: RerankerStatus = {
  state: "unavailable",
  available_mb: 512,
  required_mb: 2048,
  reason: "insufficient_memory",
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("RerankerStatusRow", () => {
  it("shows the loaded state without a retry button", () => {
    render(<RerankerStatusRow status={{ state: "loaded", model: "BAAI/bge-reranker-base" }} />);

    expect(screen.getByText("重排模型已启用")).toBeTruthy();
    expect(screen.getByText("BAAI/bge-reranker-base")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "尝试恢复" })).toBeNull();
    expect(screen.queryByText(/当前/)).toBeNull();
  });

  it("shows memory numbers and the failure reason when unavailable", () => {
    render(<RerankerStatusRow status={UNAVAILABLE} />);

    expect(screen.getByText("重排模型不可用")).toBeTruthy();
    expect(screen.getByText("当前 512 MB / 需要 2048 MB")).toBeTruthy();
    expect(screen.getByText("insufficient_memory")).toBeTruthy();
    const button = screen.getByRole("button", { name: "尝试恢复" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it("reloads on click, then polls until loaded and stops", async () => {
    vi.useFakeTimers();
    mockApiPost.mockResolvedValue({
      started: true,
      status: { state: "loading", required_mb: 2048 },
    });
    mockApiGet.mockResolvedValue({ state: "loaded", model: "BAAI/bge-reranker-base" });
    render(<RerankerStatusRow status={UNAVAILABLE} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "尝试恢复" }));
    });

    expect(mockApiPost).toHaveBeenCalledWith("rag/reranker/reload");
    expect(screen.getByText("加载中…")).toBeTruthy();
    const button = screen.getByRole("button", { name: "尝试恢复" });
    expect((button as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });

    expect(mockApiGet).toHaveBeenCalledWith("rag/reranker");
    expect(screen.getByText("重排模型已启用")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "尝试恢复" })).toBeNull();

    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });
    expect(mockApiGet).toHaveBeenCalledTimes(1);
  });

  it("polls a restored loading status until it becomes unavailable", async () => {
    vi.useFakeTimers();
    mockApiGet.mockResolvedValue({
      state: "unavailable",
      available_mb: 128,
      required_mb: 2048,
      reason: "oom",
    });
    render(<RerankerStatusRow status={{ state: "loading", required_mb: 2048 }} />);

    expect(screen.getByText("加载中…")).toBeTruthy();
    expect((screen.getByRole("button", { name: "尝试恢复" }) as HTMLButtonElement).disabled).toBe(true);
    expect(mockApiGet).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });

    expect(mockApiGet).toHaveBeenCalledTimes(1);
    expect(screen.getByText("重排模型不可用")).toBeTruthy();
    expect(screen.getByText("当前 128 MB / 需要 2048 MB")).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(6000);
      await Promise.resolve();
    });
    expect(mockApiGet).toHaveBeenCalledTimes(1);
  });

  it("keeps the current status and shows an inline error when reload fails", async () => {
    mockApiPost.mockRejectedValue(new Error("network down"));
    render(<RerankerStatusRow status={UNAVAILABLE} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "尝试恢复" }));
    });

    expect(screen.getByText("恢复请求失败，请稍后重试")).toBeTruthy();
    expect(screen.getByText("重排模型不可用")).toBeTruthy();
    expect((screen.getByRole("button", { name: "尝试恢复" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
