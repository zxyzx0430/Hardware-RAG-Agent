import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Message, Session, SourceRef } from "../../types/session";
import { ChatArea } from "./ChatArea";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore } from "../../stores/useSessionStore";

const { apiGetMock, apiPostMock, apiPutMock, apiPatchMock, apiDeleteMock, apiSSEMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
  apiSSEMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  apiSSE: apiSSEMock,
}));

vi.mock("../shared/MarkdownRenderer", () => ({ MarkdownRenderer: () => null }));
vi.mock("./ActivityBlock", () => ({ default: () => null }));
vi.mock("./ConfirmDialog", () => ({ default: () => null }));
vi.mock("./UserMessageRow", () => ({ UserMessageRow: () => null }));
vi.mock("./AssistantMessageRow", () => ({ AssistantMessageRow: () => null }));
vi.mock("./LoadingState", () => ({ LoadingState: () => null }));
vi.mock("./EmptyState", () => ({ EmptyState: () => null }));

const sessionId = "chat-save-integration-session";
const source: SourceRef = {
  id: "source-1",
  title: "ESP32-S3 技术参考手册",
  doc: "esp32-s3-reference.pdf",
  page: 12,
  score: 0.94,
  excerpt: "GPIO 配置说明",
};
const messages: Message[] = [
  { id: "user-message-1", role: "user", content: "GPIO 如何配置？", timestamp: 1 },
  {
    id: "assistant-message-1",
    role: "assistant",
    content: "请先确认引脚复用配置。",
    timestamp: 2,
    sources: [source],
  },
];
const session: Session = {
  id: sessionId,
  title: "GPIO 配置",
  preview: "",
  model: "",
  createdAt: 1,
  project: "",
  pinned: false,
  msgCount: 0,
  contextWindow: 262144,
};

beforeEach(() => {
  localStorage.clear();
  apiGetMock.mockReset().mockResolvedValue({ sessions: [], messages: [] });
  apiPostMock.mockReset().mockImplementation(async (_path: string, body?: Record<string, unknown>) => ({
    id: body?.id,
  }));
  apiPutMock.mockReset().mockResolvedValue({});
  apiPatchMock.mockReset().mockResolvedValue({});
  apiDeleteMock.mockReset().mockResolvedValue({});
  apiSSEMock.mockReset();

  useSessionStore.setState({ sessions: [session], initialized: true });
  useChatStore.setState({
    activeSessionId: sessionId,
    messages,
    sessionMessages: { [sessionId]: messages },
    isStreaming: false,
    isLoadingMessages: false,
    pendingSaveBySession: {},
  });

  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ChatArea with real chat persistence", () => {
  it("shows a real pending-save state and retries locally without reposting messages", async () => {
    render(<ChatArea />);

    const originalSetItem = Storage.prototype.setItem;
    const shardWriteFailure = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (key === `hwrag_msg_${sessionId}`) {
        throw new DOMException("simulated quota failure", "QuotaExceededError");
      }
      originalSetItem.call(this, key, value);
    });

    let saved = true;
    await act(async () => {
      saved = await useChatStore.getState().persistLastTurn(sessionId);
    });

    expect(saved).toBe(false);
    expect(useChatStore.getState().pendingSaveBySession[sessionId]).toBe("pending");
    expect(screen.getByRole("status").textContent).toContain("消息尚未保存到会话记录。");
    const retryButton = screen.getByRole("button", { name: "重试保存" });
    const messagePostCount = apiPostMock.mock.calls.filter(
      ([path]) => path === `sessions/${sessionId}/messages`,
    ).length;
    expect(messagePostCount).toBe(2);
    expect(apiSSEMock).not.toHaveBeenCalled();

    shardWriteFailure.mockRestore();
    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(useChatStore.getState().pendingSaveBySession[sessionId]).toBeUndefined();
      expect(screen.queryByRole("status")).toBeNull();
    });

    const messagePostCountAfterRetry = apiPostMock.mock.calls.filter(
      ([path]) => path === `sessions/${sessionId}/messages`,
    ).length;
    expect(messagePostCountAfterRetry).toBe(messagePostCount);
    expect(apiSSEMock).not.toHaveBeenCalled();
    const persisted = JSON.parse(localStorage.getItem(`hwrag_msg_${sessionId}`) || "null") as Message[];
    expect(persisted.map(({ id }) => id)).toEqual(messages.map(({ id }) => id));
    expect(persisted[1].sources).toEqual([source]);
  });
});
