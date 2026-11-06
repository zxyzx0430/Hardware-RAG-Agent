import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useToastStore } from "./useToastStore";

describe("toast coalescing", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
    useToastStore.setState({ toasts: [] });
  });

  it("does not stack identical messages while one is visible", () => {
    useToastStore.getState().showInfo("hardware_rag.db-wal 已更新");
    useToastStore.getState().showInfo("hardware_rag.db-wal 已更新");
    useToastStore.getState().showInfo("hardware_rag.db-wal 已更新");

    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it("allows distinct messages and caps the visible stack at five", () => {
    for (let i = 0; i < 7; i += 1) {
      useToastStore.getState().showInfo(`文件 ${i} 已更新`);
    }

    const messages = useToastStore.getState().toasts.map((toast) => toast.message);
    expect(messages).toEqual([
      "文件 2 已更新",
      "文件 3 已更新",
      "文件 4 已更新",
      "文件 5 已更新",
      "文件 6 已更新",
    ]);
  });

  it("removes a toast after its duration", () => {
    useToastStore.getState().showSuccess("done", 1000);
    expect(useToastStore.getState().toasts).toHaveLength(1);

    vi.advanceTimersByTime(1000);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
