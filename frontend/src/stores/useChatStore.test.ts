import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ChatSSEEvent } from "../types/api";
import type { Session } from "../types/session";
import { saveSessionMessages, saveToStorage } from "../utils/persistence";

const { apiPostMock, apiGetMock, apiSSEPaths } = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiSSEPaths: [] as string[],
}));

// 捕获 SSE 回调与 controller，便于测试手动驱动事件流
let capturedCallbacks: {
  onEvent: (e: ChatSSEEvent) => void;
  onDone?: () => void;
  onError?: (err: Error) => void;
} | null = null;

vi.mock("../api/client", () => ({
  apiSSE: vi.fn((path, _body, callbacks) => {
    apiSSEPaths.push(path);
    capturedCallbacks = callbacks;
    return Promise.resolve();
  }),
  apiPost: apiPostMock,
  apiGet: apiGetMock,
  apiDelete: vi.fn(),
}));

vi.mock("../utils/broadcast", () => ({
  post: vi.fn(),
  on: vi.fn(() => vi.fn()),
}));

vi.mock("../stores/useSettingsStore", () => ({
  useSettingsStore: {
    getState: () => ({
      topK: 5,
      temperature: 0.2,
      systemPrompt: "",
      longTermMemory: "",
      chatModel: "gpt-4o",
      maxTokens: 8192,
      providers: [],
      chatProviderId: "",
      webSearchConfig: { apiKey: "", baseUrl: "" },
      resolveChatCreds: () => ({ providerId: "", model: "gpt-4o", baseUrl: "https://api.openai.com/v1", apiKey: "" }),
      resolveVisionCreds: () => ({ model: "", baseUrl: "", apiKey: "" }),
      resolveImageCreds: () => ({ model: "", baseUrl: "", apiKey: "" }),
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
        sessions: [{ id: "s1", title: "新对话", model: "gpt-4o" } as Session],
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
    updateSessionMeta.mockClear();
    apiPostMock.mockReset().mockResolvedValue({ success: true, data: {} });
    apiGetMock.mockReset().mockResolvedValue({ messages: [] });
    apiSSEPaths.length = 0;
    localStorage.clear();
    vi.setSystemTime(new Date("2026-06-20T12:00:00.000Z"));
  });

  afterEach(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
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
    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({ msgCount: expect.any(Number), preview: "你好" })
    ));
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

  it("聊天连接超时时停止生成并保留已收到的回答", async () => {
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "disconnect-session",
      isStreaming: false,
    });

    store.getState().sendMessage("测试断线");
    capturedCallbacks!.onEvent({ type: "text", content: "已收到的内容" });
    capturedCallbacks!.onError!(new Error("连接超时，请检查网络或后端是否运行"));

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().streamingSessionId).toBeNull();
    expect(store.getState().currentSseRequest).toBeNull();
    expect(store.getState().sessionMessages["disconnect-session"][1].content).toContain("已收到的内容");
    expect(store.getState().sessionMessages["disconnect-session"][1].content).toContain("连接超时");
  });

  it("stopStreaming 清理全局状态并把内容写回当前会话", async () => {
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "s1",
      isStreaming: false,
    });

    const firstSaveCall = apiPostMock.mock.calls.length;
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
    await vi.waitFor(() => expect(apiPostMock.mock.calls.slice(firstSaveCall)
      .filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2));
    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.s1).toBeUndefined());
    const messageCalls = apiPostMock.mock.calls.slice(firstSaveCall)
      .filter((call) => call[0] === "sessions/s1/messages");
    expect((messageCalls[0][1] as Record<string, unknown>).id).toBe(store.getState().sessionMessages.s1[0].id);
    expect((messageCalls[1][1] as Record<string, unknown>).id).toBe(store.getState().sessionMessages.s1[1].id);
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
    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalledWith(
      "s1",
      expect.any(Object)
    ));
  });

  it("普通回答完成后用本地消息 ID 保存回答和来源", async () => {
    saveToStorage("sessions", [{ id: "s1" }]);
    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "s1",
      isStreaming: false,
    });

    const firstSaveCall = apiPostMock.mock.calls.length;
    store.getState().sendMessage("来源问题");
    capturedCallbacks!.onEvent({ type: "text", content: "回答" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-1",
      title: "芯片手册",
      doc: "chip.pdf",
      page: 4,
      score: 0.9,
      excerpt: "相关段落",
    });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(apiPostMock.mock.calls.slice(firstSaveCall)
      .filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2));
    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.s1).toBeUndefined());
    const messageCalls = apiPostMock.mock.calls.slice(firstSaveCall)
      .filter((call) => call[0] === "sessions/s1/messages");
    const [userRequest, assistantRequest] = messageCalls.map((call) => call[1] as Record<string, unknown>);
    expect(userRequest.id).toBe(store.getState().messages[0].id);
    expect(assistantRequest.id).toBe(store.getState().messages[1].id);
    expect(assistantRequest.sources).toEqual(store.getState().messages[1].sources);
    expect(apiSSEPaths).toEqual(["chat"]);
  });

  it("刷新时识别旧版服务端生成的消息 ID，避免重复插入已有历史", async () => {
    saveToStorage("sessions", [{ id: "s1" }]);
    const source = { id: "src-legacy", title: "手册", doc: "manual.pdf", page: 2, score: 0.9, excerpt: "历史来源" };
    const userMessage = { id: "browser-user-id", role: "user" as const, content: "旧问题", timestamp: 1 };
    const assistantMessage = {
      id: "browser-assistant-id",
      role: "assistant" as const,
      content: "旧回答",
      timestamp: 2,
      sources: [source],
    };
    apiGetMock.mockResolvedValue({
      messages: [
        { id: "server-user-id", role: "user", content: "旧问题", sources: [], activity: null },
        { id: "server-assistant-id", role: "assistant", content: "旧回答", sources: [source], activity: null },
      ],
    });
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      isStreaming: false,
    });

    await store.getState().fetchMessages("s1");

    expect(apiPostMock).not.toHaveBeenCalled();
    expect(store.getState().messages.map((message) => message.id)).toEqual(["server-user-id", "server-assistant-id"]);
    expect(store.getState().messages[1].sources).toEqual([source]);
  });

  it("旧版续跑内容更新同一后端消息，不创建重复助手消息", async () => {
    saveToStorage("sessions", [{ id: "s1" }]);
    const serverMessages = new Map<string, Record<string, unknown>>([
      ["server-user-id", { id: "server-user-id", role: "user", content: "确认问题", sources: [], activity: null }],
      ["server-assistant-id", { id: "server-assistant-id", role: "assistant", content: "旧的部分回答", sources: [], activity: null }],
    ]);
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path === "sessions/s1/messages") serverMessages.set(String(payload.id), payload);
      return { success: true, data: {} };
    });
    const userMessage = { id: "browser-user-id", role: "user" as const, content: "确认问题", timestamp: 1 };
    const assistantMessage = {
      id: "browser-assistant-id",
      role: "assistant" as const,
      content: "续跑后的完整回答",
      timestamp: 2,
      sources: [{ id: "src-final", title: "手册", doc: "manual.pdf", page: 7, score: 0.9, excerpt: "续跑来源" }],
    };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      isStreaming: false,
    });

    await store.getState().fetchMessages("s1");

    expect(apiPostMock).toHaveBeenCalledTimes(1);
    expect(apiPostMock.mock.calls[0][1]).toMatchObject({
      id: "server-assistant-id",
      content: "续跑后的完整回答",
    });
    expect(serverMessages.size).toBe(2);
    expect(store.getState().messages[1].content).toBe("续跑后的完整回答");
  });

  it.each(["allow", "deny", "stop"] as const)(
    "人工确认后以相同消息 ID 保存 %s 续跑结果和来源",
    async (decision) => {
      saveToStorage("sessions", [{ id: "s1" }]);
      const userMessage = { id: "user-stable", role: "user" as const, content: "继续执行", timestamp: 1 };
      const assistantMessage = {
        id: "assistant-stable",
        role: "assistant" as const,
        content: "确认前",
        timestamp: 2,
        sources: [{ id: "src-old", title: "旧来源", doc: "old.pdf", page: 1, score: 0.8, excerpt: "旧摘录" }],
      };
      const store = await importStore();
      store.setState({
        messages: [userMessage, assistantMessage],
        sessionMessages: { s1: [userMessage, assistantMessage] },
        activeSessionId: "s1",
        streamingSessionId: "s1",
        isStreaming: false,
        _lastAgentPayload: { messages: [] },
      });

      const firstSaveCall = apiPostMock.mock.calls.length;
      store.getState().resumeAgent(decision);
      capturedCallbacks!.onEvent({ type: "text", content: "，续跑完成" });
      capturedCallbacks!.onEvent({
        type: "source",
        id: "src-new",
        title: "新来源",
        doc: "new.pdf",
        page: 2,
        score: 0.95,
        excerpt: "新摘录",
      });
      capturedCallbacks!.onDone!();

      await vi.waitFor(() => expect(apiPostMock.mock.calls.slice(firstSaveCall)
        .filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2));
      await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.s1).toBeUndefined());
      const messageCalls = apiPostMock.mock.calls.slice(firstSaveCall)
        .filter((call) => call[0] === "sessions/s1/messages");
      const assistantRequest = messageCalls[1][1] as Record<string, unknown>;
      expect((messageCalls[0][1] as Record<string, unknown>).id).toBe("user-stable");
      expect(assistantRequest.id).toBe("assistant-stable");
      expect(assistantRequest.content).toBe("确认前，续跑完成");
      expect(assistantRequest.sources).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: "src-old" }),
        expect.objectContaining({ id: "src-new" }),
      ]));
      expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);
    }
  );

  it("续跑期间切换会话仍只更新并保存发起续跑的会话", async () => {
    saveToStorage("sessions", [{ id: "s1" }, { id: "s2" }]);
    const userMessage = { id: "user-resume-switch", role: "user" as const, content: "原会话问题", timestamp: 1 };
    const assistantMessage = { id: "assistant-resume-switch", role: "assistant" as const, content: "续跑前", timestamp: 2 };
    const otherUser = { id: "user-other", role: "user" as const, content: "另一个问题", timestamp: 3 };
    const otherAssistant = { id: "assistant-other", role: "assistant" as const, content: "另一个回答", timestamp: 4 };
    apiGetMock.mockImplementation(async (path: string) => path === "sessions/s2/messages"
      ? { messages: [otherUser, otherAssistant].map((message) => ({ ...message, sources: [], activity: null })) }
      : { messages: [] });
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage], s2: [otherUser, otherAssistant] },
      activeSessionId: "s1",
      streamingSessionId: "s1",
      isStreaming: false,
      _lastAgentPayload: { messages: [] },
    });

    store.getState().resumeAgent("allow");
    capturedCallbacks!.onEvent({ type: "text", content: "，续跑首段" });
    store.getState().setActiveSession("s2");
    await vi.waitFor(() => expect(store.getState().isLoadingMessages).toBe(false));
    capturedCallbacks!.onEvent({ type: "text", content: "续跑末段" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-resume-switch",
      title: "续跑来源",
      doc: "resume.pdf",
      page: 5,
      score: 0.9,
      excerpt: "续跑摘录",
    });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(apiPostMock.mock.calls
      .filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2));
    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.s1).toBeUndefined());
    const savedMessages = apiPostMock.mock.calls
      .filter((call) => call[0] === "sessions/s1/messages")
      .map((call) => call[1] as Record<string, unknown>);
    expect(savedMessages.map((message) => message.id)).toEqual([userMessage.id, assistantMessage.id]);
    expect(savedMessages[1].content).toBe("续跑前，续跑首段续跑末段");
    expect(savedMessages[1].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-resume-switch" }),
    ]));
    expect(store.getState().messages[1].content).toBe("另一个回答");
    expect(store.getState().sessionMessages.s1[1].content).toBe("续跑前，续跑首段续跑末段");
    expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);
  });

  it("Agent 恢复流断开时结束流状态并保存已有回答", async () => {
    const userMessage = { id: "user-resume-error", role: "user" as const, content: "确认后继续", timestamp: 1 };
    const assistantMessage = { id: "assistant-resume-error", role: "assistant" as const, content: "恢复前", timestamp: 2 };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      streamingSessionId: "s1",
      isStreaming: false,
      _lastAgentPayload: { messages: [] },
    });

    store.getState().resumeAgent("allow");
    capturedCallbacks!.onEvent({ type: "text", content: "部分续跑回答" });
    capturedCallbacks!.onError!(new Error("恢复流断开"));

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().sessionMessages.s1[1].content).toContain("部分续跑回答");
    await vi.waitFor(() => expect(apiPostMock.mock.calls
      .filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2));
    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.s1).toBeUndefined());
    const messageCalls = apiPostMock.mock.calls.filter((call) => call[0] === "sessions/s1/messages");
    expect((messageCalls[0][1] as Record<string, unknown>).id).toBe(userMessage.id);
    expect((messageCalls[1][1] as Record<string, unknown>).id).toBe(assistantMessage.id);
    expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);
  });

  it("助手消息部分保存失败后，刷新仍可重试保存且不会重放聊天请求", async () => {
    const sessionId = "retry-save-session";
    saveToStorage("sessions", [{ id: sessionId }]);
    const serverMessages = new Map<string, Record<string, unknown>>();
    let assistantFailuresRemaining = 2;
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      if (payload.role === "assistant" && assistantFailuresRemaining > 0) {
        assistantFailuresRemaining -= 1;
        throw new Error("503 simulated write failure");
      }
      serverMessages.set(String(payload.id), payload);
      return { success: true, data: { id: payload.id } };
    });
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));

    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: sessionId,
      isStreaming: false,
    });
    store.getState().sendMessage("需重试的问题");
    capturedCallbacks!.onEvent({ type: "text", content: "需保存的回答" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-retry",
      title: "保留来源",
      doc: "retry.pdf",
      page: 3,
      score: 0.9,
      excerpt: "不能丢失",
    });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending"));
    const [userId, assistantId] = store.getState().sessionMessages[sessionId].map((message) => message.id);
    expect(serverMessages.has(userId)).toBe(true);
    expect(serverMessages.has(assistantId)).toBe(false);

    // Simulate refresh: Zustand is recreated while localStorage remains intact.
    saveSessionMessages(sessionId, store.getState().sessionMessages[sessionId]);
    vi.resetModules();
    capturedCallbacks = null;
    apiSSEPaths.length = 0;
    const refreshedStore = await importStore();
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");

    await refreshedStore.getState().retryPendingSave(sessionId);
    expect([...serverMessages.keys()].sort()).toEqual([userId, assistantId].sort());
    expect(serverMessages.get(assistantId)?.sources).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "src-retry" })])
    );
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    expect(apiSSEPaths).toEqual([]);
  });

  it("续跑助手部分写入失败后可刷新重试并保留来源", async () => {
    const sessionId = "retry-resume-session";
    saveToStorage("sessions", [{ id: sessionId }]);
    const serverMessages = new Map<string, Record<string, unknown>>();
    let assistantFailuresRemaining = 2;
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      if (payload.role === "assistant" && assistantFailuresRemaining > 0) {
        assistantFailuresRemaining -= 1;
        throw new Error("503 simulated resume write failure");
      }
      serverMessages.set(String(payload.id), payload);
      return { success: true, data: { id: payload.id } };
    });
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));
    const userMessage = { id: "user-resume-retry", role: "user" as const, content: "拒绝工具后回答", timestamp: 1 };
    const assistantMessage = {
      id: "assistant-resume-retry",
      role: "assistant" as const,
      content: "确认前的回答",
      timestamp: 2,
      sources: [{ id: "src-before", title: "原来源", doc: "before.pdf", page: 1, score: 0.8, excerpt: "已有来源" }],
    };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { [sessionId]: [userMessage, assistantMessage] },
      activeSessionId: sessionId,
      streamingSessionId: sessionId,
      isStreaming: false,
      _lastAgentPayload: { messages: [] },
    });

    store.getState().resumeAgent("deny");
    capturedCallbacks!.onEvent({ type: "text", content: "，续跑回答" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-resumed",
      title: "续跑来源",
      doc: "resumed.pdf",
      page: 2,
      score: 0.95,
      excerpt: "续跑摘录",
    });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending"));
    expect(serverMessages.has(userMessage.id)).toBe(true);
    expect(serverMessages.has(assistantMessage.id)).toBe(false);
    saveSessionMessages(sessionId, store.getState().sessionMessages[sessionId]);
    vi.resetModules();
    capturedCallbacks = null;
    apiSSEPaths.length = 0;
    const refreshedStore = await importStore();
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");

    await refreshedStore.getState().retryPendingSave(sessionId);

    expect([...serverMessages.keys()].sort()).toEqual([userMessage.id, assistantMessage.id].sort());
    expect(serverMessages.get(assistantMessage.id)?.sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-before" }),
      expect.objectContaining({ id: "src-resumed" }),
    ]));
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    expect(apiSSEPaths).toEqual([]);
  });
});
