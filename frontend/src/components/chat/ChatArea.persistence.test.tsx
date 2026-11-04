import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ActivityStep, Message, SourceRef } from "../../types/session";
import { ChatArea } from "./ChatArea";
import { useChatStore } from "../../stores/useChatStore";

type MockChatStoreState = {
  activeSessionId: string;
  messages: Message[];
  isStreaming: boolean;
  isLoadingMessages: boolean;
  pendingSaveBySession: Record<string, "saving" | "pending">;
  streamingContent: string;
  streamingSteps: ActivityStep[];
  streamingStartTime: number | null;
  streamingError: unknown;
  isCompressing: boolean;
  compressingMessage: string;
  streamingSources: SourceRef[];
  quoteMessage: (...args: unknown[]) => void;
  retryMessage: (...args: unknown[]) => void;
  editAndResend: (...args: unknown[]) => void;
  sessionHighlightSourceId: Record<string, string | null>;
  setSessionHighlightSourceId: (...args: unknown[]) => void;
  setSessionFileViewerSource: (...args: unknown[]) => void;
  retryPendingSave: (sessionId: string) => Promise<void>;
};

const { retryPendingSaveMock } = vi.hoisted(() => ({ retryPendingSaveMock: vi.fn() }));

vi.mock("../../stores/useChatStore", async () => {
  const { create } = await import("zustand");
  const store = create<MockChatStoreState>((set) => ({
    activeSessionId: "pending-save-session",
    messages: [],
    isStreaming: false,
    isLoadingMessages: false,
    pendingSaveBySession: {},
    streamingContent: "",
    streamingSteps: [],
    streamingStartTime: null,
    streamingError: null,
    isCompressing: false,
    compressingMessage: "",
    streamingSources: [],
    quoteMessage: vi.fn(),
    retryMessage: vi.fn(),
    editAndResend: vi.fn(),
    sessionHighlightSourceId: {},
    setSessionHighlightSourceId: vi.fn(),
    setSessionFileViewerSource: vi.fn(),
    retryPendingSave: async (sessionId: string) => {
      retryPendingSaveMock(sessionId);
      set((state) => {
        const pending = { ...state.pendingSaveBySession };
        delete pending[sessionId];
        return { pendingSaveBySession: pending };
      });
    },
  }));
  return { useChatStore: store };
});

vi.mock("../../stores/useBookmarkStore", () => ({
  useBookmarkStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    toggleBookmark: vi.fn(),
    isBookmarked: () => false,
    bookmarkFolders: [],
    addBookmarkToFolder: vi.fn(),
    addBookmarkFolder: vi.fn(),
  }),
}));

vi.mock("../../stores/useAppStore", () => ({
  useAppStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    chatFontSize: 14,
    setRightPanelOpen: vi.fn(),
    setRightMode: vi.fn(),
    setWbTab: vi.fn(),
    addPreviewTab: vi.fn(),
  }),
}));

vi.mock("../../stores/useSessionStore", () => ({
  useSessionStore: (selector: (state: Record<string, unknown>) => unknown) => selector({ sessions: [] }),
}));

vi.mock("../../i18n", () => ({ useI18n: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }) }));
vi.mock("../shared/MarkdownRenderer", () => ({ MarkdownRenderer: () => null }));
vi.mock("./ActivityBlock", () => ({ default: () => null }));
vi.mock("./ConfirmDialog", () => ({ default: () => null }));
vi.mock("./UserMessageRow", () => ({ UserMessageRow: () => null }));
vi.mock("./AssistantMessageRow", () => ({ AssistantMessageRow: () => null }));
vi.mock("./LoadingState", () => ({ LoadingState: () => null }));
vi.mock("./EmptyState", () => ({ EmptyState: () => null }));

const sessionId = "pending-save-session";
const userMessage: Message = {
  id: "pending-save-user-message",
  role: "user",
  content: "已生成的回答",
  timestamp: 1,
};

beforeEach(() => {
  retryPendingSaveMock.mockClear();
  useChatStore.setState({
    activeSessionId: sessionId,
    messages: [userMessage],
    isStreaming: false,
    isLoadingMessages: false,
    pendingSaveBySession: { [sessionId]: "pending" },
  });
  vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChatArea save status", () => {
  it("shows unsaved feedback and retry, then hides the prompt after the store clears pending", async () => {
    render(<ChatArea />);

    expect(screen.getByRole("status").textContent).toContain("消息尚未保存到会话记录。");
    const retryButton = screen.getByRole("button", { name: "重试保存" });

    await act(async () => {
      fireEvent.click(retryButton);
    });

    expect(retryPendingSaveMock).toHaveBeenCalledTimes(1);
    expect(retryPendingSaveMock).toHaveBeenCalledWith(sessionId);
    expect(useChatStore.getState().pendingSaveBySession[sessionId]).toBeUndefined();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
