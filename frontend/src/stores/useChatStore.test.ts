import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ChatSSEEvent } from "../types/api";
import { useSessionStore } from "./useSessionStore";

// 捕获 SSE 回调与 controller，便于测试手动驱动事件流
let capturedCallbacks: {
  onEvent: (e: ChatSSEEvent) => void;
  onDone?: () => void;
  onError?: (err: Error) => void;
} | null = null;
let capturedController: AbortController | null = null;

vi.mock("../api/client", () => ({
  apiSSE: vi.fn((_path, _body, callbacks, controller) => {
    capturedCallbacks = callbacks;
    capturedController = controller ?? null;
    return Promise.resolve();
  }),
}));

vi.mock("../stores/useSettingsStore", () => ({
  useSettingsStore: {
    getState: () => ({
      topK: 5,
      temperature: 0.2,
      systemPrompt: "",
      longTermMemory: "",
      model: "gpt-4o",
      maxTokens: 8192,
      activeProvider: "openai",
      providerKeys: {},
      getBaseUrl: () => "https://api.openai.com/v1",
    }),
  },
}));

vi.mock("../stores/useLogStore", () => ({
  useLogStore: {
    getState: () => ({ log: vi.fn() }),
  },
}));

const updateSessionMeta = vi.fn();
vi.mock("../stores/useSessionStore", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof import("./useSessionStore");
  return {
    ...actual,
    useSessionStore: {
      getState: () => ({
        sessions: [{ id: "s1", title: "新对话", model: "gpt-4o" } as any],
        updateSessionMeta,
      }),
      setState: vi.fn(),
    },
  };
});

vi.mock("../stores/useAppStore", () => ({
  useAppStore: {
    getState: () => ({
      setRightPanelOpen: vi.fn(),
      setRightMode: vi.fn(),
      setWbTab: vi.fn(),
      addPreviewTab: vi.fn(),
      setQuotedMsg: vi.fn(),
      setActiveSession: vi.fn(),
    }),
  },
}));

async function importStore() {
  const mod = await import("./useChatStore");
  return mod.useChatStore;
}

describe("useChatStore", () => {
  beforeEach(() => {
    vi.resetModules();
    capturedCallbacks = null;
    capturedController = null;
    updateSessionMeta.mockClear();
    localStorage.clear();
    vi.setSystemTime(new Date("2026-06-20T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("处理 thinking / text / source / done 事件并更新消息", async () => {
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "s1",
      isStreaming: false,
    });

    store.getState().sendMessage("你好");
    expect(store.getState().isStreaming).toBe(true);
    expect(store.getState().streamingSessionId).toBe("s1");
    expect(capturedCallbacks).toBeTruthy();

    capturedCallbacks!.onEvent({
      type: "thinking",
      content: "检索知识库...",
      source: "rag",
    });
    expect(store.getState().streamingSteps).toHaveLength(1);
    expect(store.getState().streamingSteps[0]).toMatchObject({
      type: "thinking",
      content: "检索知识库...",
      source: "rag",
    });

    capturedCallbacks!.onEvent({ type: "text", content: "Hello" });
    expect(store.getState().streamingContent).toBe("Hello");
    expect(store.getState().messages).toHaveLength(2);
    expect(store.getState().messages[1].content).toBe("Hello");

    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-1",
      title: "STM32 手册",
      doc: "stm32.pdf",
      page: 12,
      score: 0.95,
      excerpt: "...",
    });
    expect(store.getState().streamingSources).toHaveLength(1);
    expect(store.getState().sources[0].title).toBe("STM32 手册");
    expect(store.getState().messages[1].sources).toHaveLength(1);

    capturedCallbacks!.onEvent({
      type: "done",
      success: true,
      usage: {
        prompt_tokens: 10,
        completion_tokens: 5,
        total_tokens: 15,
      },
    });
    capturedCallbacks!.onDone!();

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().streamingSessionId).toBeNull();
    expect(store.getState().messages[1].usage).toEqual({
      promptTokens: 10,
      completionTokens: 5,
      totalTokens: 15,
    });
    expect(store.getState().messages[1].activity).toBeTruthy();
    expect(updateSessionMeta).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({ msgCount: expect.any(Number), preview: "你好" })
    );
  });

  it("error 事件触发 stopStreaming 并在消息中追加错误", async () => {
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "s1",
      isStreaming: false,
    });

    store.getState().sendMessage("test");
    capturedCallbacks!.onEvent({ type: "text", content: "partial" });
    capturedCallbacks!.onEvent({ type: "error", message: "模型调用失败" });

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().messages[1].content).toContain("模型调用失败");
    expect(store.getState().streamingContent).toBe("");
    expect(store.getState().currentSseRequest).toBeNull();
  });

  it("stopStreaming 清理全局状态并把内容写回当前会话", async () => {
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "s1",
      isStreaming: false,
    });

    store.getState().sendMessage("hi");
    capturedCallbacks!.onEvent({ type: "text", content: "stop" });
    store.getState().stopStreaming();

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().streamingContent).toBe("");
    expect(store.getState().streamingSteps).toEqual([]);
    expect(store.getState().streamingSources).toEqual([]);
    expect(store.getState().streamingSessionId).toBeNull();
    expect(store.getState().currentSseRequest).toBeNull();
    expect(store.getState().messages[1].content).toBe("stop");
    expect(store.getState().sessionMessages["s1"][1].content).toBe("stop");
  });

  it("切换会话后，SSE 内容仍写入发起请求的会话", async () => {
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: { s2: [] },
      activeSessionId: "s1",
      isStreaming: false,
    });

    store.getState().sendMessage("切换测试");
    const requestSessionId = store.getState().streamingSessionId;
    expect(requestSessionId).toBe("s1");

    // 模拟用户切换到 s2
    store.getState().setActiveSession("s2");
    expect(store.getState().activeSessionId).toBe("s2");
    expect(store.getState().isStreaming).toBe(false);

    // 后台 SSE 继续返回 text，应写入 s1 而非当前 messages
    capturedCallbacks!.onEvent({ type: "text", content: "后台" });
    expect(store.getState().messages).toEqual([]);
    expect(store.getState().sessionMessages["s1"]).toHaveLength(2);
    expect(store.getState().sessionMessages["s1"][1].content).toBe("后台");

    // done 后元数据更新也应针对 s1
    capturedCallbacks!.onEvent({
      type: "done",
      success: true,
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    });
    capturedCallbacks!.onDone!();
    expect(updateSessionMeta).toHaveBeenCalledWith(
      "s1",
      expect.any(Object)
    );
  });
});
