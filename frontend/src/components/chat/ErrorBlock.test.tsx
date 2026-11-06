import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ErrorBlock from "./ErrorBlock";
import { useAppStore } from "../../stores/useAppStore";

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
});

afterEach(cleanup);

describe("ErrorBlock error actions", () => {
  it("does not offer a dead retry for context-limit errors and points at a new session", () => {
    const onRetry = vi.fn();
    const onNewSession = vi.fn();
    render(
      <ErrorBlock
        code="CONTEXT_LIMIT"
        message="上下文超过当前模型可处理上限"
        detail="累计上下文估算为 235894 tokens，限制为 206233 tokens。"
        onRetry={onRetry}
        onNewSession={onNewSession}
      />,
    );

    expect(screen.queryByText("重试")).toBeNull();
    expect(screen.getByText(/新建会话或缩短上下文/)).toBeTruthy();
    fireEvent.click(screen.getByText("新建会话"));
    expect(onNewSession).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("keeps retry for ordinary network errors and preserves their message", () => {
    const onRetry = vi.fn();
    render(
      <ErrorBlock
        code="NETWORK_ERROR"
        message="连接断开，点击重试"
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText("连接断开，点击重试")).toBeTruthy();
    fireEvent.click(screen.getByText("重试"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("新建会话")).toBeNull();
  });

  it("shows an auth hint for 401 errors", () => {
    render(
      <ErrorBlock
        code="AUTH_FAILED"
        message="认证失败：请先登录或检查访问令牌后重试。"
        detail="SSE 401: Unauthorized"
      />,
    );

    expect(screen.getByText(/登录状态或访问令牌/)).toBeTruthy();
  });
});
