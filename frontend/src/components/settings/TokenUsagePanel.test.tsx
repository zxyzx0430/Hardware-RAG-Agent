import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TokenUsagePanel } from "./TokenUsagePanel";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
}));

vi.mock("../../i18n", () => ({
  useI18n: () => ({ t: (key: string) => ({
    tokenUsage: "Token 用量",
    inputTokens: "输入 Token",
    freshInputTokens: "新增输入",
    outputTokens: "输出 Token",
    totalTokens: "总计 Token",
    cachedTokens: "缓存输入",
    cacheHitRate: "缓存命中率",
    tokenUsageNote: "输入已包含系统提示与工具定义；缓存命中是输入的一部分。",
    lineChart: "折线图",
    barChart: "柱状图",
    noTokenData: "暂无 Token 用量数据",
    calls: "次",
    daysUnit: "天",
  } as Record<string, string>)[key] ?? key }),
}));

afterEach(cleanup);

describe("TokenUsagePanel", () => {
  it("shows cached input and cache hit rate from stats", async () => {
    apiGetMock.mockResolvedValueOnce({
      daily: [{
        date: "2026-11-10", input: 1000, output: 100, total: 1100,
        cached: 400, hit_rate: 0.4,
      }],
      by_model: [{
        model: "Text", input: 1000, output: 100, total: 1100,
        cached: 400, hit_rate: 0.4, calls: 1,
      }],
      summary: {
        total_input: 1000, total_output: 100, total_tokens: 1100,
        total_cached: 400, hit_rate: 0.4, days: 30,
      },
    });

    render(<TokenUsagePanel />);

    await waitFor(() => expect(screen.getAllByText("缓存输入").length).toBeGreaterThan(0));
    expect(screen.getByText("缓存命中率")).toBeTruthy();
    expect(screen.getAllByText("40.0%").length).toBeGreaterThan(0);
    expect(screen.getByText("400")).toBeTruthy();
    expect(screen.getByText("新增输入")).toBeTruthy();
    expect(screen.getByText("600")).toBeTruthy();
    expect(screen.getByText("输入已包含系统提示与工具定义；缓存命中是输入的一部分。")).toBeTruthy();
  });

  it("does not fabricate cache data when the provider reports none", async () => {
    apiGetMock.mockResolvedValueOnce({
      daily: [{
        date: "2026-11-10", input: 10, output: 2, total: 12,
        cached: 0, hit_rate: 0,
      }],
      by_model: [],
      summary: {
        total_input: 10, total_output: 2, total_tokens: 12,
        total_cached: 0, hit_rate: 0, days: 30,
      },
    });

    render(<TokenUsagePanel />);

    await waitFor(() => expect(screen.getByText("0.0%")).toBeTruthy());
  });
});
