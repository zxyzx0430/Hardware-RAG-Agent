import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { Attachment, ChatSSEEvent } from "../types/api";
import type { Message, Session } from "../types/session";
import { saveSessionMessages, saveToStorage } from "../utils/persistence";

const {
  apiPostMock,
  apiGetMock,
  apiSSEPaths,
  apiSSEBodies,
  operationOrder,
  settingsValues,
  newSessionMock,
  sessionState,
  appState,
  setQuotedMsgMock,
} = vi.hoisted(() => {
  const appState: { quotedMsg: unknown } = { quotedMsg: null };
  const sessionState: { creatingSession: boolean; sessions: Array<Record<string, unknown>> } = {
    creatingSession: false,
    sessions: [{ id: "s1", title: "新对话", model: "gpt-4o" }],
  };
  const setQuotedMsgMock = vi.fn((message: unknown) => { appState.quotedMsg = message; });
  return {
    apiPostMock: vi.fn(),
    apiGetMock: vi.fn(),
    apiSSEPaths: [] as string[],
    apiSSEBodies: [] as unknown[],
    operationOrder: [] as string[],
    settingsValues: { topK: 5, relevanceThreshold: 0 },
    newSessionMock: vi.fn(),
    sessionState,
    appState,
    setQuotedMsgMock,
  };
});

// 捕获 SSE 回调与 controller，便于测试手动驱动事件流
let capturedCallbacks: {
  onEvent: (e: ChatSSEEvent) => void;
  onDone?: () => void;
  onError?: (err: Error) => void;
} | null = null;

vi.mock("../api/client", () => ({
  apiSSE: vi.fn((path, body, callbacks) => {
    apiSSEPaths.push(path);
    apiSSEBodies.push(body);
    operationOrder.push(`sse:${path}`);
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
      ...settingsValues,
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
        sessions: sessionState.sessions as unknown as Session[],
        creatingSession: sessionState.creatingSession,
        newSession: newSessionMock,
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
      quotedMsg: appState.quotedMsg as Message | null,
      setQuotedMsg: setQuotedMsgMock,
      setActiveSession: vi.fn(),
      resetWorkbenchOverride: vi.fn(),
    }),
  },
}));

async function importStore() {
  const mod = await import("./useChatStore");
  return mod.useChatStore;
}

describe("useChatStore", () => {
  it("sends the exact pending call ID before clearing the confirmation", async () => {
    const store = await importStore();
    store.setState({ activeSessionId: "s1", _lastAgentPayload: { messages: [] }, pendingConfirm: { count: 1, sessionId: "s1", calls: [{ name: "mcp__fixture__echo", args: { text: "synthetic" }, call_id: "exact-call", risk_level: "high" }] } });
    store.getState().resumeAgent("allow");
    expect(apiSSEBodies.at(-1)).toMatchObject({ decision: "allow", call_id: "exact-call" });
    expect(store.getState().pendingConfirm).toBeNull();
  });
  beforeEach(() => {
    vi.resetModules();
    capturedCallbacks = null;
    updateSessionMeta.mockClear();
    newSessionMock.mockReset();
    sessionState.creatingSession = false;
    sessionState.sessions = [{ id: "s1", title: "新对话", model: "gpt-4o" }];
    appState.quotedMsg = null;
    setQuotedMsgMock.mockClear();
    apiPostMock.mockReset().mockResolvedValue({ success: true, data: {} });
    apiGetMock.mockReset().mockResolvedValue({ messages: [] });
    settingsValues.topK = 5;
    settingsValues.relevanceThreshold = 0;
    apiSSEPaths.length = 0;
    apiSSEBodies.length = 0;
    operationOrder.length = 0;
    localStorage.clear();
    vi.setSystemTime(new Date("2026-06-20T12:00:00.000Z"));
  });

  afterEach(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    vi.useRealTimers();
  });

  it("sends the per-request Top-K and normalized vector threshold from settings", async () => {
    settingsValues.topK = 7;
    settingsValues.relevanceThreshold = 50;
    const store = await importStore();
    store.setState({ activeSessionId: "s1", messages: [], sessionMessages: {} });

    await store.getState().sendMessage("配置请求参数");

    expect(apiSSEBodies.at(-1)).toMatchObject({ top_k: 7, relevance_threshold: 0.5 });
  });

  it("restores knowledge-base scope independently for each session and after store reload", async () => {
    const store = await importStore();

    store.getState().setActiveSession("scope-session-a");
    store.getState().setSelectedKbIds(["kb-a"]);
    store.getState().setActiveSession("scope-session-b");
    expect(store.getState().selectedKbIds).toEqual([]);
    store.getState().setSelectedKbIds(["kb-b"]);
    store.getState().setActiveSession("scope-session-a");
    expect(store.getState().selectedKbIds).toEqual(["kb-a"]);

    vi.resetModules();
    const refreshedStore = await importStore();
    expect(refreshedStore.getState().activeSessionId).toBe("scope-session-a");
    expect(refreshedStore.getState().selectedKbIds).toEqual(["kb-a"]);
  });

  it("blocks a corrupt saved scope until the user explicitly chooses all enabled KBs", async () => {
    const sessionId = "corrupt-scope-session";
    localStorage.setItem("hwrag_active_session", JSON.stringify(sessionId));
    localStorage.setItem(`hwrag_chat_kb_scope_v1_${encodeURIComponent(sessionId)}`, "{broken");
    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {} });

    expect(store.getState().kbScopeIssue).toBe("corrupt");
    await store.getState().sendMessage("不能默默扩大范围");
    expect(apiSSEPaths).toEqual([]);

    store.getState().setSelectedKbIds([]);
    expect(store.getState().kbScopeIssue).toBeNull();
    await store.getState().sendMessage("明确选择全部");
    expect(apiSSEPaths).toEqual(["chat"]);
    const allEnabledScope = (apiSSEBodies.at(-1) as { kb_ids?: string[] }).kb_ids;
    expect(allEnabledScope === undefined || allEnabledScope.length === 0).toBe(true);
  });

  it("blocks deleted or disabled KB IDs and permits an explicit scope repair", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "kb/collections") {
        return { collections: [{ id: "kb-a", enabled: true }, { id: "kb-b", enabled: false }] };
      }
      return { messages: [] };
    });
    const store = await importStore();
    store.setState({ activeSessionId: "scope-validation-session", messages: [], sessionMessages: {} });
    store.getState().setSelectedKbIds(["kb-a", "kb-b", "kb-deleted"]);

    await store.getState().sendMessage("范围含有不可用 KB");

    expect(store.getState().kbScopeIssue).toBe("unavailable");
    expect(apiSSEPaths).toEqual([]);

    store.getState().beginKbScopeRepair();
    expect(store.getState().kbScopeIssue).toBe("repair");
    store.getState().toggleKbSelection("kb-a");
    expect(store.getState().kbScopeIssue).toBeNull();
    await store.getState().sendMessage("重新选择可用 KB");

    await vi.waitFor(() => expect(apiSSEPaths).toEqual(["chat"]));
    expect(apiSSEBodies.at(-1)).toMatchObject({ kb_ids: ["kb-a"] });
  });

  it("同一知识库验证等待期间的重复发送只发起一个聊天请求", async () => {
    const resolvers: Array<(value: { collections: Array<{ id: string; enabled: boolean }> }) => void> = [];
    apiGetMock.mockImplementation((path: string) => path === "kb/collections"
      ? new Promise<{ collections: Array<{ id: string; enabled: boolean }> }>((resolve) => resolvers.push(resolve))
      : Promise.resolve({ messages: [] }));
    const store = await importStore();
    store.setState({ activeSessionId: "race-session", messages: [], sessionMessages: {}, selectedKbIds: ["kb-a"], isStreaming: false });

    const first = store.getState().sendMessage("第一条模拟发送");
    const second = store.getState().sendMessage("重复的模拟发送");
    expect(resolvers).toHaveLength(1);
    resolvers[0]({ collections: [{ id: "kb-a", enabled: true }] });
    await Promise.all([first, second]);

    expect(apiSSEPaths).toEqual(["chat"]);
    expect(apiSSEBodies).toHaveLength(1);
  });

  it("空会话首发先创建一次会话，再用同一 ID 保存用户、回答和来源", async () => {
    const store = await importStore();
    const sessionId = "first-send-session";
    const persistedMessages: Array<Record<string, unknown>> = [];
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path === "sessions") {
        operationOrder.push("post:sessions");
        return { id: sessionId };
      }
      if (path === `sessions/${sessionId}/messages`) {
        operationOrder.push("post:message");
        persistedMessages.push(payload);
        return { success: true, data: { id: payload.id } };
      }
      return { success: true, data: {} };
    });
    newSessionMock.mockImplementationOnce(async (options: { requirePersisted?: boolean }) => {
      expect(options).toEqual({ requirePersisted: true });
      sessionState.creatingSession = true;
      try {
        await apiPostMock("sessions", { id: sessionId });
        sessionState.sessions.unshift({ id: sessionId, title: "新对话", model: "gpt-4o" });
        store.getState().setActiveSession(sessionId);
        return sessionId;
      } finally {
        sessionState.creatingSession = false;
      }
    });
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "", isStreaming: false });
    const sending = store.getState().sendMessage("空会话首发问题");
    await sending;

    expect(newSessionMock).toHaveBeenCalledOnce();
    expect(apiSSEPaths).toEqual(["chat"]);
    expect(apiSSEBodies[0]).toMatchObject({ session_id: sessionId });
    expect(operationOrder.slice(0, 2)).toEqual(["post:sessions", "sse:chat"]);

    capturedCallbacks!.onEvent({ type: "text", content: "首发回答" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "first-send-source",
      title: "首发来源",
      doc: "manual.pdf",
      page: 6,
      score: 0.93,
      excerpt: "保留的来源摘录",
    });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(persistedMessages).toHaveLength(2));
    expect(operationOrder).toEqual(["post:sessions", "sse:chat", "post:message", "post:message"]);
    expect(persistedMessages.map((message) => message.role)).toEqual(["user", "assistant"]);
    expect(persistedMessages[1].sources).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "first-send-source" })]),
    );
    expect(store.getState().sessionMessages[sessionId]).toHaveLength(2);
  });

  it("空会话创建失败时不发聊天、保留草稿附件和引用，并允许重试", async () => {
    const store = await importStore();
    const attachment = {
      id: "retry-attachment",
      name: "notes.txt",
      type: "text/plain",
      content: "data:text/plain;base64,bm90ZXM=",
    } as Attachment;
    const quoted: Message = { id: "retry-quote", role: "user", content: "引用内容", timestamp: 1 };
    const draft = "  创建失败后保留原文  ";
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "",
      isStreaming: false,
      drafts: { "": draft },
      sessionAttachments: { "": [attachment] },
    });
    appState.quotedMsg = quoted;
    newSessionMock.mockRejectedValueOnce(new Error("模拟网络失败"));

    const failedSend = store.getState().sendMessage("创建失败后保留原文", [attachment], quoted);
    store.getState().clearDraft("");
    store.getState().clearSessionAttachments("");
    setQuotedMsgMock(null);
    await failedSend;

    expect(apiSSEPaths).toEqual([]);
    expect(sessionState.sessions).toHaveLength(1);
    expect(store.getState().drafts[""]).toBe(draft);
    expect(store.getState().sessionAttachments[""]).toEqual([attachment]);
    expect(appState.quotedMsg).toEqual(quoted);

    const retrySessionId = "retry-after-create-failure";
    newSessionMock.mockImplementationOnce(async () => {
      sessionState.sessions.unshift({ id: retrySessionId, title: "新对话", model: "gpt-4o" });
      store.getState().setActiveSession(retrySessionId);
      return retrySessionId;
    });
    await store.getState().sendMessage(
      store.getState().drafts[""],
      store.getState().sessionAttachments[""],
      appState.quotedMsg as Message,
    );

    expect(newSessionMock).toHaveBeenCalledTimes(2);
    expect(apiSSEPaths).toEqual(["chat"]);
    expect(apiSSEBodies[0]).toMatchObject({ session_id: retrySessionId });
  });

  it("并发空会话首发只创建一次会话并保留第二条草稿", async () => {
    const store = await importStore();
    const sessionId = "concurrent-first-session";
    let finishCreate: (() => void) | undefined;
    newSessionMock.mockImplementationOnce(() => {
      sessionState.creatingSession = true;
      return new Promise<string>((resolve) => {
        finishCreate = () => {
          sessionState.sessions.unshift({ id: sessionId, title: "新对话", model: "gpt-4o" });
          sessionState.creatingSession = false;
          store.getState().setActiveSession(sessionId);
          resolve(sessionId);
        };
      });
    });
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "", isStreaming: false });

    const firstSend = store.getState().sendMessage("第一条问题");
    await vi.waitFor(() => expect(newSessionMock).toHaveBeenCalledOnce());
    await store.getState().sendMessage("并发第二条问题");

    expect(newSessionMock).toHaveBeenCalledOnce();
    expect(apiSSEPaths).toEqual([]);
    finishCreate!();
    await firstSend;

    expect(apiSSEPaths).toEqual(["chat"]);
    expect(apiSSEBodies[0]).toMatchObject({ session_id: sessionId });
    expect(store.getState().drafts[sessionId]).toBe("并发第二条问题");
  });

  it("空会话建会失败时合并恢复并发首发的两条输入、附件和引用", async () => {
    const store = await importStore();
    const attachmentA = {
      id: "initial-attachment-a",
      name: "a.txt",
      type: "text/plain",
      content: "data:text/plain;base64,YQ==",
    } as Attachment;
    const attachmentB = {
      id: "initial-attachment-b",
      name: "b.txt",
      type: "text/plain",
      content: "data:text/plain;base64,Yg==",
    } as Attachment;
    const quoteA: Message = { id: "initial-quote-a", role: "user", content: "引用 A", timestamp: 1 };
    const quoteB: Message = { id: "initial-quote-b", role: "assistant", content: "引用 B", timestamp: 2 };
    let rejectCreate: ((reason?: unknown) => void) | undefined;
    newSessionMock.mockImplementationOnce(() => {
      sessionState.creatingSession = true;
      return new Promise<string>((_resolve, reject) => { rejectCreate = reject; });
    });
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: "",
      isStreaming: false,
      drafts: { "": "首发问题 A" },
      sessionAttachments: { "": [attachmentA] },
    });
    appState.quotedMsg = quoteA;

    const firstSend = store.getState().sendMessage("首发问题 A", [attachmentA], quoteA);
    await vi.waitFor(() => expect(newSessionMock).toHaveBeenCalledOnce());
    store.getState().clearDraft("");
    store.getState().clearSessionAttachments("");
    setQuotedMsgMock(null);

    store.setState({ drafts: { "": "首发问题 B" }, sessionAttachments: { "": [attachmentB] } });
    setQuotedMsgMock(quoteB);
    const secondSend = store.getState().sendMessage("首发问题 B", [attachmentB], quoteB);
    store.getState().clearDraft("");
    store.getState().clearSessionAttachments("");
    setQuotedMsgMock(null);

    rejectCreate!(new Error("模拟建会失败"));
    await Promise.all([firstSend, secondSend]);

    expect(apiSSEPaths).toEqual([]);
    expect(newSessionMock).toHaveBeenCalledOnce();
    expect(store.getState().drafts[""]).toBe("首发问题 A\n\n首发问题 B");
    expect(store.getState().sessionAttachments[""]).toEqual([attachmentA, attachmentB]);
    expect((appState.quotedMsg as Message).content).toContain("引用 1");
    expect((appState.quotedMsg as Message).content).toContain("引用 A");
    expect((appState.quotedMsg as Message).content).toContain("引用 B");
  });

  it("知识库验证等待期间切换会话后，请求仍归属原会话并按后台流恢复", async () => {
    let resolveScope: ((value: { collections: Array<{ id: string; enabled: boolean }> }) => void) | undefined;
    apiGetMock.mockImplementation((path: string) => path === "kb/collections"
      ? new Promise<{ collections: Array<{ id: string; enabled: boolean }> }>((resolve) => { resolveScope = resolve; })
      : Promise.resolve({ messages: [] }));
    const store = await importStore();
    store.setState({ activeSessionId: "race-old-session", messages: [], sessionMessages: {}, selectedKbIds: ["kb-a"], isStreaming: false });

    const sending = store.getState().sendMessage("原会话模拟提问");
    await vi.waitFor(() => expect(resolveScope).toBeTypeOf("function"));
    store.getState().setActiveSession("race-new-session");
    resolveScope?.({ collections: [{ id: "kb-a", enabled: true }] });
    await sending;
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(store.getState().activeSessionId).toBe("race-new-session");
    expect(store.getState().messages).toEqual([]);
    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().backgroundSseRequests.has("race-old-session")).toBe(true);
    expect(apiSSEBodies[0]).toMatchObject({ session_id: "race-old-session", kb_ids: ["kb-a"] });

    store.getState().setActiveSession("race-old-session");
    expect(store.getState().isStreaming).toBe(true);
    expect(store.getState().streamingSessionId).toBe("race-old-session");
    expect(store.getState().messages[0].content).toBe("原会话模拟提问");
  });

  it("验证等待期间知识库选择变化时仍只发送已验证的范围快照", async () => {
    let resolveScope: ((value: { collections: Array<{ id: string; enabled: boolean }> }) => void) | undefined;
    apiGetMock.mockImplementation((path: string) => path === "kb/collections"
      ? new Promise<{ collections: Array<{ id: string; enabled: boolean }> }>((resolve) => { resolveScope = resolve; })
      : Promise.resolve({ messages: [] }));
    const store = await importStore();
    store.setState({ activeSessionId: "race-scope-session", messages: [], sessionMessages: {}, selectedKbIds: ["kb-a"], isStreaming: false });

    const sending = store.getState().sendMessage("知识库范围模拟提问");
    await vi.waitFor(() => expect(resolveScope).toBeTypeOf("function"));
    store.getState().setSelectedKbIds(["kb-b"]);
    resolveScope?.({ collections: [{ id: "kb-a", enabled: true }, { id: "kb-b", enabled: true }] });
    await sending;

    expect(store.getState().selectedKbIds).toEqual(["kb-b"]);
    expect(apiSSEBodies[0]).toMatchObject({ kb_ids: ["kb-a"] });
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
    expect(apiSSEBodies[0]).toMatchObject({ skills_mode: "auto" });
    expect((apiSSEBodies[0] as Record<string, unknown>).skill_ids).toBeUndefined();
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

  it("会话摘要更新失败不回滚已确认写入的消息状态", async () => {
    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "s1", isStreaming: false });
    updateSessionMeta.mockImplementationOnce(() => { throw new Error("模拟摘要同步失败"); });

    store.getState().sendMessage("摘要失败不影响保存");
    capturedCallbacks!.onEvent({ type: "text", content: "已保存回答" });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalled());
    expect(apiPostMock.mock.calls.filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2);
    expect(store.getState().pendingSaveBySession?.s1).toBeUndefined();
    expect(apiSSEPaths).toEqual(["chat"]);
  });

  it("always requests automatic Skills discovery and keeps it through HITL resume", async () => {
    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "s1", isStreaming: false });

    store.getState().sendMessage("请检查", undefined, undefined, {
      mode: "manual",
      skillIds: ["review", "datasheet", "review", "checklist", "ignored"],
    });
    const request = apiSSEBodies[0] as Record<string, unknown>;
    expect(request.skills_mode).toBe("auto");
    expect(request.skill_ids).toBeUndefined();
    expect(store.getState()._lastAgentPayload).toMatchObject({
      skills_mode: "auto",
    });
    expect(store.getState()._lastAgentPayload).not.toHaveProperty("skill_ids");

    capturedCallbacks!.onEvent({ type: "tool_confirm_required", calls: [], count: 0 });
    store.getState().resumeAgent("allow");
    expect(apiSSEPaths).toEqual(["chat", "agent-sandbox/resume"]);
    expect(apiSSEBodies[1]).toMatchObject({
      decision: "allow",
      payload: { skills_mode: "auto" },
    });
    expect((apiSSEBodies[1] as { payload: Record<string, unknown> }).payload)
      .not.toHaveProperty("skill_ids");
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

  it("同一流中的 error 后 done(false) 不会把部分回答标为完成", async () => {
    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "s1", isStreaming: false });

    store.getState().sendMessage("部分回答失败");
    capturedCallbacks!.onEvent({ type: "thinking", content: "正在处理", source: "reasoning" });
    capturedCallbacks!.onEvent({ type: "text", content: "已输出部分" });
    capturedCallbacks!.onEvent({ type: "error", code: "INTERNAL_ERROR", message: "生成失败" });
    capturedCallbacks!.onEvent({ type: "done", success: false });
    capturedCallbacks!.onDone!();

    expect(store.getState().messages[1].content).toContain("已输出部分");
    expect(store.getState().messages[1].content).toContain("生成失败");
    expect(store.getState().messages[1].activity?.status).toBe("error");
    expect(store.getState().isStreaming).toBe(false);
  });

  it("done(false) 单独结束流时保留部分回答并标记失败", async () => {
    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "s1", isStreaming: false });

    store.getState().sendMessage("无终态错误事件");
    capturedCallbacks!.onEvent({ type: "thinking", content: "正在处理", source: "reasoning" });
    capturedCallbacks!.onEvent({ type: "text", content: "仍需保留" });
    capturedCallbacks!.onEvent({ type: "done", success: false });
    capturedCallbacks!.onDone!();

    expect(store.getState().messages[1].content).toContain("仍需保留");
    expect(store.getState().messages[1].content).toContain("回答未能完成");
    expect(store.getState().messages[1].activity?.status).toBe("error");
    expect(store.getState().isStreaming).toBe(false);
  });

  it("HITL 确认后的 done 保留待确认 activity 和续跑快照", async () => {
    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: "s1", isStreaming: false });

    store.getState().sendMessage("需授权的操作");
    capturedCallbacks!.onEvent({
      type: "tool_call", call_id: "confirm-me", tool: "write_file", args: { path: "out.txt" },
    });
    capturedCallbacks!.onEvent({
      type: "tool_confirm_required",
      calls: [{ name: "write_file", args: { path: "out.txt" }, call_id: "confirm-me", risk_level: "high" }],
      count: 1,
    });
    capturedCallbacks!.onEvent({
      type: "done", success: true, completed: false, awaiting_confirmation: true,
    } as ChatSSEEvent);
    capturedCallbacks!.onDone!();

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().messages[1].activity?.status).toBe("running");
    expect(store.getState().messages[1].activity?.steps).toEqual(
      expect.arrayContaining([expect.objectContaining({ call_id: "confirm-me", status: "pending" })]),
    );
    expect(store.getState().pendingConfirm?.calls[0]?.call_id).toBe("confirm-me");
    expect(store.getState()._lastAgentPayload?.messages).toEqual(
      expect.arrayContaining([expect.objectContaining({ content: "需授权的操作" })]),
    );
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

  it("resume error 后即使同流 done(true) 也保留错误且不把待执行步骤标为完成", async () => {
    const userMessage = { id: "user-resume-terminal-error", role: "user" as const, content: "继续", timestamp: 1 };
    const assistantMessage = {
      id: "assistant-resume-terminal-error",
      role: "assistant" as const,
      content: "续跑前",
      timestamp: 2,
      activity: {
        durationMs: 0,
        status: "running" as const,
        steps: [{ type: "tool" as const, id: "pending-tool", name: "write_file", status: "pending" as const }],
      },
    };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      streamingSessionId: "s1",
      isStreaming: true,
      _lastAgentPayload: { messages: [] },
    });

    store.getState().resumeAgent("allow");
    capturedCallbacks!.onEvent({ type: "text", content: "部分续跑" });
    capturedCallbacks!.onEvent({ type: "error", code: "AGENT_RESUME_FAILED", message: "续跑失败" });
    capturedCallbacks!.onEvent({ type: "done", success: true, completed: true });
    capturedCallbacks!.onDone!();

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState().streamingError).toMatchObject({ code: "AGENT_RESUME_FAILED", message: "续跑失败" });
    expect(store.getState().messages[1].content).toContain("续跑失败");
    expect(store.getState().messages[1].activity?.status).toBe("error");
    expect(store.getState().messages[1].activity?.steps[0].status).toBe("error");
  });

  it("resume done(false) 单独到达时也作为失败终态保留已有回答", async () => {
    const userMessage = { id: "user-resume-done-false", role: "user" as const, content: "继续", timestamp: 1 };
    const assistantMessage = { id: "assistant-resume-done-false", role: "assistant" as const, content: "恢复前" , timestamp: 2 };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      streamingSessionId: "s1",
      isStreaming: true,
      _lastAgentPayload: { messages: [] },
    });

    store.getState().resumeAgent("deny");
    capturedCallbacks!.onEvent({ type: "text", content: "部分续跑回答" });
    capturedCallbacks!.onEvent({ type: "done", success: false, completed: false });
    capturedCallbacks!.onDone!();

    expect(store.getState().messages[1].content).toContain("部分续跑回答");
    expect(store.getState().messages[1].activity?.status).toBe("error");
    expect(store.getState().streamingError?.message).toContain("未能完成");
    expect(store.getState().isStreaming).toBe(false);
  });

  it("resume 二次确认的 done 保留 payload、pendingConfirm 和未完成工具步骤", async () => {
    const userMessage = { id: "user-resume-confirm-again", role: "user" as const, content: "继续确认", timestamp: 1 };
    const assistantMessage = { id: "assistant-resume-confirm-again", role: "assistant" as const, content: "确认前", timestamp: 2 };
    const payload = { messages: [] };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      streamingSessionId: "s1",
      isStreaming: true,
      _lastAgentPayload: payload,
    });

    store.getState().resumeAgent("allow");
    capturedCallbacks!.onEvent({
      type: "tool_call", call_id: "second-confirm", tool: "write_file", args: { path: "out.txt" },
    });
    capturedCallbacks!.onEvent({
      type: "tool_confirm_required",
      calls: [{ name: "write_file", args: { path: "out.txt" }, call_id: "second-confirm", risk_level: "high" }],
      count: 1,
    });
    capturedCallbacks!.onEvent({
      type: "done", success: true, completed: false, awaiting_confirmation: true,
    });
    capturedCallbacks!.onDone!();

    expect(store.getState().isStreaming).toBe(false);
    expect(store.getState()._lastAgentPayload).toBe(payload);
    expect(store.getState().pendingConfirm?.calls[0]?.call_id).toBe("second-confirm");
    expect(store.getState().messages[1].activity?.status).toBe("running");
    expect(store.getState().messages[1].activity?.steps).toEqual(
      expect.arrayContaining([expect.objectContaining({ call_id: "second-confirm", status: "pending" })]),
    );
  });

  it("resume stop 的既有 user-stopped 终态不报异常并清理续跑状态", async () => {
    const userMessage = { id: "user-resume-stop", role: "user" as const, content: "停止", timestamp: 1 };
    const assistantMessage = {
      id: "assistant-resume-stop",
      role: "assistant" as const,
      content: "停止前的回答",
      timestamp: 2,
      sources: [{ id: "src-stop", title: "停止前来源", doc: "stop.pdf", page: 1, score: 0.8, excerpt: "保留引用" }],
    };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { s1: [userMessage, assistantMessage] },
      activeSessionId: "s1",
      streamingSessionId: "s1",
      isStreaming: false,
      pendingConfirm: { count: 1, sessionId: "s1", calls: [{ name: "write_file", args: {}, call_id: "stop-call", risk_level: "high" }] },
      _lastAgentPayload: { messages: [{ role: "user", content: "停止" }] },
    });

    store.getState().resumeAgent("stop");
    capturedCallbacks!.onEvent({
      type: "done", success: false, reason: "user stopped",
    } as ChatSSEEvent);
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(apiPostMock.mock.calls
      .filter((call) => call[0] === "sessions/s1/messages")).toHaveLength(2));
    const persistedMessages = apiPostMock.mock.calls
      .filter((call) => call[0] === "sessions/s1/messages")
      .map((call) => call[1] as Record<string, unknown>);
    expect(persistedMessages.map((message) => message.id)).toEqual([userMessage.id, assistantMessage.id]);
    expect(persistedMessages[1].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-stop" }),
    ]));

    expect(store.getState().messages[1].content).toBe("停止前的回答");
    expect(store.getState().messages[1].activity?.status).toBe("done");
    expect(store.getState().streamingError).toBeNull();
    expect(store.getState().pendingConfirm).toBeNull();
    expect(store.getState()._lastAgentPayload).toBeNull();
    expect(store.getState().isStreaming).toBe(false);
    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalledWith(
      "s1",
      expect.objectContaining({ msgCount: 2, preview: "停止" }),
    ));
    expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);
  });

  it("停止续跑的助手保存失败后，刷新只重试 CRUD 并保留消息和来源", async () => {
    const sessionId = "retry-resume-stop-session";
    saveToStorage("sessions", [{ id: sessionId }]);
    const serverMessages = new Map<string, Record<string, unknown>>();
    let assistantFailuresRemaining = 2;
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      if (payload.role === "assistant" && assistantFailuresRemaining > 0) {
        assistantFailuresRemaining -= 1;
        throw new Error("503 simulated stopped-resume write failure");
      }
      serverMessages.set(String(payload.id), payload);
      return { success: true, data: { id: payload.id } };
    });
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));

    const userMessage = {
      id: "user-resume-stop-retry",
      role: "user" as const,
      content: "停止续跑后仍保留本轮",
      timestamp: 1,
    };
    const assistantMessage = {
      id: "assistant-resume-stop-retry",
      role: "assistant" as const,
      content: "停止前已生成的回答",
      timestamp: 2,
      sources: [{
        id: "src-stop-before",
        title: "停止前来源",
        doc: "before-stop.pdf",
        page: 1,
        score: 0.8,
        excerpt: "停止前摘录",
      }],
    };
    const store = await importStore();
    store.setState({
      messages: [userMessage, assistantMessage],
      sessionMessages: { [sessionId]: [userMessage, assistantMessage] },
      activeSessionId: sessionId,
      streamingSessionId: sessionId,
      isStreaming: false,
      pendingConfirm: {
        count: 1,
        sessionId,
        calls: [{ name: "write_file", args: {}, call_id: "stop-retry-call", risk_level: "high" }],
      },
      _lastAgentPayload: { messages: [{ role: "user", content: userMessage.content }] },
    });

    store.getState().resumeAgent("stop");
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-stop-during-resume",
      title: "续跑期间来源",
      doc: "during-stop.pdf",
      page: 2,
      score: 0.95,
      excerpt: "停止后仍须保存的来源",
    });
    capturedCallbacks!.onEvent({
      type: "done",
      success: false,
      reason: "user stopped",
    } as ChatSSEEvent);
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending"));
    expect(serverMessages.has(userMessage.id)).toBe(true);
    expect(serverMessages.has(assistantMessage.id)).toBe(false);
    expect(store.getState().sessionMessages[sessionId][1].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-stop-before" }),
      expect.objectContaining({ id: "src-stop-during-resume" }),
    ]));
    expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);

    // Simulate refresh while the assistant CRUD write is still pending.
    vi.resetModules();
    capturedCallbacks = null;
    apiSSEPaths.length = 0;
    const refreshedStore = await importStore();
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);

    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");
    expect(refreshedStore.getState().sessionMessages[sessionId][1].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-stop-before" }),
      expect.objectContaining({ id: "src-stop-during-resume" }),
    ]));

    await refreshedStore.getState().retryPendingSave(sessionId);

    expect([...serverMessages.keys()].sort()).toEqual([userMessage.id, assistantMessage.id].sort());
    expect(serverMessages.get(assistantMessage.id)?.sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-stop-before" }),
      expect.objectContaining({ id: "src-stop-during-resume" }),
    ]));
    expect(apiPostMock.mock.calls.filter((call) =>
      call[0] === `sessions/${sessionId}/messages`
      && (call[1] as Record<string, unknown>).role === "user",
    )).toHaveLength(1);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    expect(apiSSEPaths).toEqual([]);
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
    expect(updateSessionMeta).not.toHaveBeenCalled();
    const [userId, assistantId] = store.getState().sessionMessages[sessionId].map((message) => message.id);
    expect(serverMessages.has(userId)).toBe(true);
    expect(serverMessages.has(assistantId)).toBe(false);

    // Simulate refresh: Zustand is recreated while localStorage remains intact.
    vi.resetModules();
    capturedCallbacks = null;
    apiSSEPaths.length = 0;
    const refreshedStore = await importStore();
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");

    updateSessionMeta.mockClear();
    await refreshedStore.getState().retryPendingSave(sessionId);
    expect([...serverMessages.keys()].sort()).toEqual([userId, assistantId].sort());
    expect(serverMessages.get(assistantId)?.sources).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "src-retry" })])
    );
    expect(apiPostMock.mock.calls.filter((call) =>
      call[0] === `sessions/${sessionId}/messages` && (call[1] as Record<string, unknown>).role === "user",
    )).toHaveLength(1);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalledWith(
      sessionId,
      expect.objectContaining({ msgCount: 2, preview: "需重试的问题" }),
    ));
    expect(apiSSEPaths).toEqual([]);
  });

  it("续跑更新失败且待保存标记不可写时，刷新从本地分片恢复同 ID 助手消息", async () => {
    const sessionId = "resume-save-marker-unavailable";
    saveToStorage("sessions", [{ id: sessionId }]);
    const userId = "resume-marker-user";
    const assistantId = "resume-marker-assistant";
    const serverMessages = new Map<string, Record<string, unknown>>([
      [userId, {
        id: userId,
        role: "user",
        content: "确认前的问题",
        sources: [],
        tool_calls: [],
        activity: null,
        created_at: "2026-06-20T12:00:00.000Z",
      }],
      [assistantId, {
        id: assistantId,
        role: "assistant",
        content: "确认前的回答",
        sources: [{ id: "src-before-resume", title: "确认前来源", doc: "before.pdf", page: 1, score: 0.8, excerpt: "原来源" }],
        tool_calls: [],
        activity: { durationMs: 0, steps: [], status: "running" },
        created_at: "2026-06-20T12:00:00.001Z",
      }],
    ]);
    let failResumedAssistantUpdate = true;
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      if (
        payload.role === "assistant"
        && payload.content === "确认前的回答，续跑完成"
        && failResumedAssistantUpdate
      ) {
        failResumedAssistantUpdate = false;
        throw new Error("503 simulated resumed message update failure");
      }
      serverMessages.set(String(payload.id), { ...payload });
      return { success: true, data: { id: payload.id } };
    });

    const originalSetItem = Storage.prototype.setItem;
    const pendingMarkerWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (key.startsWith("hwrag_pending_save_")) {
        throw new DOMException("simulated pending marker storage failure", "QuotaExceededError");
      }
      originalSetItem.call(this, key, value);
    });

    try {
      const store = await importStore();
      store.setState({ activeSessionId: sessionId, messages: [], sessionMessages: {} });
      await store.getState().fetchMessages(sessionId);
      store.setState({
        streamingSessionId: sessionId,
        isStreaming: false,
        pendingConfirm: {
          count: 1,
          sessionId,
          calls: [{ name: "write_file", args: { path: "out.txt" }, call_id: "resume-marker-call", risk_level: "high" }],
        },
        _lastAgentPayload: { messages: [{ role: "user", content: "确认前的问题" }] },
      });

      store.getState().resumeAgent("allow");
      capturedCallbacks!.onEvent({ type: "text", content: "，续跑完成" });
      capturedCallbacks!.onEvent({
        type: "source",
        id: "src-after-resume",
        title: "续跑来源",
        doc: "after.pdf",
        page: 2,
        score: 0.95,
        excerpt: "续跑期间的来源",
      });
      capturedCallbacks!.onEvent({ type: "done", success: true, completed: true });
      capturedCallbacks!.onDone!();

      await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending"));
      expect(serverMessages.get(assistantId)?.content).toBe("确认前的回答");
      expect(store.getState().sessionMessages[sessionId][1].content).toBe("确认前的回答，续跑完成");

      vi.resetModules();
      capturedCallbacks = null;
      apiSSEPaths.length = 0;
      const refreshedStore = await importStore();
      expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");
      refreshedStore.setState({ activeSessionId: sessionId });
      await refreshedStore.getState().fetchMessages(sessionId);

      expect(serverMessages.size).toBe(2);
      expect(serverMessages.get(assistantId)?.content).toBe("确认前的回答，续跑完成");
      expect(serverMessages.get(assistantId)?.sources).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: "src-before-resume" }),
        expect.objectContaining({ id: "src-after-resume" }),
      ]));
      expect(refreshedStore.getState().sessionMessages[sessionId].map((message) => message.id)).toEqual([userId, assistantId]);
      expect(refreshedStore.getState().sessionMessages[sessionId][1].content).toBe("确认前的回答，续跑完成");
      expect(refreshedStore.getState().sessionMessages[sessionId][1].sources).toEqual(expect.arrayContaining([
        expect.objectContaining({ id: "src-before-resume" }),
        expect.objectContaining({ id: "src-after-resume" }),
      ]));
      expect(apiSSEPaths).toEqual([]);
      expect(apiPostMock.mock.calls.filter((call) =>
        call[0] === `sessions/${sessionId}/messages` && (call[1] as Record<string, unknown>).role === "user",
      )).toHaveLength(0);
    } finally {
      pendingMarkerWrite.mockRestore();
    }
  });

  it("续跑未终态刷新时，将同 ID 的本地新回答和来源保存到旧后端消息", async () => {
    const sessionId = "resume-refresh-before-terminal";
    const userId = "resume-refresh-user";
    const assistantId = "resume-refresh-assistant";
    saveToStorage("sessions", [{ id: sessionId }]);
    const serverMessages = new Map<string, Record<string, unknown>>([
      [userId, {
        id: userId,
        role: "user",
        content: "等待确认的问题",
        sources: [],
        tool_calls: [],
        activity: null,
        created_at: "2026-06-20T12:00:00.000Z",
      }],
      [assistantId, {
        id: assistantId,
        role: "assistant",
        content: "确认前的回答",
        sources: [{ id: "src-before-refresh", title: "原来源", doc: "before.pdf", page: 1, score: 0.8, excerpt: "原摘录", messageId: assistantId }],
        tool_calls: [],
        activity: { durationMs: 0, steps: [], status: "running" },
        created_at: "2026-06-20T12:00:00.001Z",
      }],
    ]);
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      serverMessages.set(String(payload.id), { ...payload });
      return { success: true, data: { id: payload.id } };
    });

    const store = await importStore();
    store.setState({ activeSessionId: sessionId, messages: [], sessionMessages: {} });
    await store.getState().fetchMessages(sessionId);
    store.setState({
      streamingSessionId: sessionId,
      isStreaming: false,
      pendingConfirm: {
        count: 1,
        sessionId,
        calls: [{ name: "write_file", args: { path: "out.txt" }, call_id: "refresh-call", risk_level: "high" }],
      },
      _lastAgentPayload: { messages: [{ role: "user", content: "等待确认的问题" }] },
    });

    store.getState().resumeAgent("allow");
    capturedCallbacks!.onEvent({ type: "text", content: "，续跑完成" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-during-refresh",
      title: "续跑来源",
      doc: "after.pdf",
      page: 2,
      score: 0.95,
      excerpt: "续跑期间的摘录",
    });

    expect(store.getState().isStreaming).toBe(true);
    expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending");
    expect(store.getState().sessionMessages[sessionId].map((message) => message.id)).toEqual([userId, assistantId]);
    expect(store.getState().sessionMessages[sessionId][1].content).toBe("确认前的回答，续跑完成");
    expect(store.getState().sessionMessages[sessionId][1].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-before-refresh" }),
      expect.objectContaining({ id: "src-during-refresh" }),
    ]));
    expect(serverMessages.get(assistantId)?.content).toBe("确认前的回答");
    expect((serverMessages.get(assistantId)?.sources as Array<{ id: string }>).map((source) => source.id))
      .toEqual(["src-before-refresh"]);
    expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);

    // Simulate refresh before done/onDone; recovery updates the existing assistant row via CRUD.
    vi.resetModules();
    capturedCallbacks = null;
    const refreshedStore = await importStore();
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);

    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    expect(refreshedStore.getState().sessionMessages[sessionId].map((message) => message.id)).toEqual([userId, assistantId]);
    expect(refreshedStore.getState().sessionMessages[sessionId][1].content).toBe("确认前的回答，续跑完成");
    expect([...serverMessages.keys()].sort()).toEqual([userId, assistantId].sort());
    expect(serverMessages.get(assistantId)?.content).toBe("确认前的回答，续跑完成");
    const savedSources = serverMessages.get(assistantId)?.sources as Array<{ id: string }>;
    expect(savedSources.map((source) => source.id).sort()).toEqual(["src-before-refresh", "src-during-refresh"].sort());
    expect(apiPostMock.mock.calls
      .filter((call) => call[0] === `sessions/${sessionId}/messages`)
      .map((call) => ({ id: (call[1] as Record<string, unknown>).id, role: (call[1] as Record<string, unknown>).role })))
      .toEqual([{ id: assistantId, role: "assistant" }]);

    // A second fetch confirms the stored snapshot without another CRUD or SSE request.
    await refreshedStore.getState().fetchMessages(sessionId);
    expect([...serverMessages.keys()].sort()).toEqual([userId, assistantId].sort());
    expect((serverMessages.get(assistantId)?.sources as Array<{ id: string }>).map((source) => source.id))
      .toEqual(["src-before-refresh", "src-during-refresh"]);
    expect(apiPostMock.mock.calls.filter((call) => call[0] === `sessions/${sessionId}/messages`)).toHaveLength(1);
    expect(apiSSEPaths).toEqual(["agent-sandbox/resume"]);
  });

  it("助手消息已落库但写入响应丢失时，刷新确认后不重复写入", async () => {
    const sessionId = "lost-save-response-session";
    saveToStorage("sessions", [{ id: sessionId }]);
    const serverMessages = new Map<string, Record<string, unknown>>();
    let loseAssistantResponse = true;
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      // 模拟服务端已提交消息，但成功响应在网络中丢失。
      serverMessages.set(String(payload.id), { ...payload });
      if (payload.role === "assistant" && loseAssistantResponse) {
        loseAssistantResponse = false;
        throw new Error("模拟写入成功后的响应丢失");
      }
      return { success: true, data: { id: payload.id } };
    });
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));

    const store = await importStore();
    store.setState({ messages: [], sessionMessages: {}, activeSessionId: sessionId, isStreaming: false });
    store.getState().sendMessage("服务端已保存的问题");
    capturedCallbacks!.onEvent({ type: "text", content: "服务端已保存的回答" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-lost-response",
      title: "确认来源",
      doc: "response-loss.pdf",
      page: 4,
      score: 0.91,
      excerpt: "来源仍需保留",
    });
    capturedCallbacks!.onDone!();

    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending"));
    const [userId, assistantId] = store.getState().sessionMessages[sessionId].map((message) => message.id);
    expect(serverMessages.size).toBe(2);
    expect(serverMessages.get(assistantId)?.sources).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "src-lost-response" })])
    );

    // 刷新后 GET 返回的确切稳定 ID 与内容可确认两条消息都已提交；无需重发保存请求。
    vi.resetModules();
    capturedCallbacks = null;
    apiSSEPaths.length = 0;
    const refreshedStore = await importStore();
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);

    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    expect(refreshedStore.getState().sessionMessages[sessionId].map((message) => message.id)).toEqual([userId, assistantId]);
    expect(refreshedStore.getState().sessionMessages[sessionId][1].sources).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "src-lost-response" })])
    );
    expect([...serverMessages.keys()].sort()).toEqual([userId, assistantId].sort());
    expect(apiPostMock.mock.calls.filter((call) => call[0] === `sessions/${sessionId}/messages`)).toHaveLength(2);
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
    expect(updateSessionMeta).not.toHaveBeenCalled();
    expect(serverMessages.has(userMessage.id)).toBe(true);
    expect(serverMessages.has(assistantMessage.id)).toBe(false);
    vi.resetModules();
    capturedCallbacks = null;
    apiSSEPaths.length = 0;
    const refreshedStore = await importStore();
    refreshedStore.setState({ activeSessionId: sessionId });
    await refreshedStore.getState().fetchMessages(sessionId);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBe("pending");

    updateSessionMeta.mockClear();
    await refreshedStore.getState().retryPendingSave(sessionId);

    expect([...serverMessages.keys()].sort()).toEqual([userMessage.id, assistantMessage.id].sort());
    expect(serverMessages.get(assistantMessage.id)?.sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-before" }),
      expect.objectContaining({ id: "src-resumed" }),
    ]));
    expect(apiPostMock.mock.calls.filter((call) =>
      call[0] === `sessions/${sessionId}/messages` && (call[1] as Record<string, unknown>).role === "user",
    )).toHaveLength(1);
    expect(refreshedStore.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalledWith(
      sessionId,
      expect.objectContaining({ msgCount: 2, preview: "拒绝工具后回答" }),
    ));
    expect(apiSSEPaths).toEqual([]);
  });

  it("旧消息 ID 在部分写入失败后刷新和手动重试仍更新同一行", async () => {
    const sessionId = "legacy-id-retry-session";
    const localUserId = "browser-user-legacy";
    const localAssistantId = "browser-assistant-legacy";
    const serverUserId = "server-user-legacy";
    const serverAssistantId = "server-assistant-legacy";
    saveToStorage("sessions", [{ id: sessionId }]);
    const beforeSource = {
      id: "src-before-legacy-resume",
      title: "旧来源",
      doc: "legacy.pdf",
      page: 1,
      score: 0.8,
      excerpt: "确认前来源",
      messageId: localAssistantId,
    };
    const resumedSource = {
      id: "src-after-legacy-resume",
      title: "续跑来源",
      doc: "legacy.pdf",
      page: 2,
      score: 0.95,
      excerpt: "续跑期间来源",
      messageId: localAssistantId,
    };
    const userMessage: Message = {
      id: localUserId,
      role: "user",
      content: "确认问题",
      timestamp: 1,
    };
    const assistantMessage: Message = {
      id: localAssistantId,
      role: "assistant",
      content: "确认前回答，续跑完成",
      timestamp: 2,
      sources: [beforeSource, resumedSource],
    };
    const serverMessages = new Map<string, Record<string, unknown>>([
      [serverUserId, {
        id: serverUserId,
        role: "user",
        content: "确认问题",
        sources: [],
        tool_calls: [],
        activity: null,
        created_at: "2026-06-20T12:00:00.000Z",
      }],
      [serverAssistantId, {
        id: serverAssistantId,
        role: "assistant",
        content: "确认前回答",
        sources: [{ ...beforeSource, messageId: serverAssistantId }],
        tool_calls: [],
        activity: { durationMs: 0, steps: [], status: "running" },
        created_at: "2026-06-20T12:00:00.001Z",
      }],
    ]);
    let failFirstAssistantUpdate = true;
    apiGetMock.mockImplementation(async () => ({ messages: [...serverMessages.values()] }));
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      if (
        payload.role === "assistant"
        && payload.id === serverAssistantId
        && failFirstAssistantUpdate
      ) {
        failFirstAssistantUpdate = false;
        throw new Error("503 simulated legacy assistant update failure");
      }
      serverMessages.set(String(payload.id), { ...payload });
      return { success: true, data: { id: payload.id } };
    });
    saveSessionMessages(sessionId, [userMessage, assistantMessage]);
    localStorage.setItem(
      `hwrag_pending_save_${encodeURIComponent(sessionId)}`,
      JSON.stringify([localAssistantId]),
    );

    // Hydrate a fresh store from the same local shards before the refresh GET.
    const store = await importStore();
    store.setState({ activeSessionId: sessionId });
    await store.getState().fetchMessages(sessionId);

    expect(store.getState().pendingSaveBySession?.[sessionId]).toBe("pending");
    expect(store.getState().sessionMessages[sessionId].map((message) => message.id))
      .toEqual([serverUserId, serverAssistantId]);
    expect(store.getState().sessionMessages[sessionId][1].sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: beforeSource.id, messageId: serverAssistantId }),
      expect.objectContaining({ id: resumedSource.id, messageId: serverAssistantId }),
    ]));
    expect(serverMessages.size).toBe(2);

    await store.getState().retryPendingSave(sessionId);

    expect(serverMessages.size).toBe(2);
    expect([...serverMessages.keys()].sort()).toEqual([serverUserId, serverAssistantId].sort());
    expect(serverMessages.get(serverAssistantId)?.content).toBe("确认前回答，续跑完成");
    expect(serverMessages.get(serverAssistantId)?.sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: beforeSource.id, messageId: serverAssistantId }),
      expect.objectContaining({ id: resumedSource.id, messageId: serverAssistantId }),
    ]));
    expect(apiPostMock.mock.calls
      .filter((call) => call[0] === `sessions/${sessionId}/messages`)
      .map((call) => (call[1] as Record<string, unknown>).id))
      .toEqual([serverAssistantId, serverAssistantId]);
    expect(apiPostMock.mock.calls.some((call) => call[0] === "chat")).toBe(false);
    expect(apiPostMock.mock.calls.some((call) => call[0] === "agent-sandbox/resume")).toBe(false);
    expect(apiSSEPaths).toEqual([]);
    expect(store.getState().pendingSaveBySession?.[sessionId]).toBeUndefined();
  });

  it("确认续跑的保存等待首轮写入完成，避免旧回答覆盖新回答", async () => {
    const sessionId = "racing-confirmation-save-session";
    saveToStorage("sessions", [{ id: sessionId }]);
    const serverMessages = new Map<string, Record<string, unknown>>();
    let assistantWriteCount = 0;
    let releaseInitialAssistant: (() => void) | undefined;
    let notifyInitialAssistantStarted: (() => void) | undefined;
    const initialAssistantStarted = new Promise<void>((resolve) => {
      notifyInitialAssistantStarted = resolve;
    });
    const initialAssistantGate = new Promise<void>((resolve) => {
      releaseInitialAssistant = resolve;
    });
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path !== `sessions/${sessionId}/messages`) return { success: true, data: {} };
      if (payload.role === "assistant" && assistantWriteCount++ === 0) {
        notifyInitialAssistantStarted?.();
        await initialAssistantGate;
      }
      serverMessages.set(String(payload.id), { ...payload });
      return { success: true, data: { id: payload.id } };
    });

    const store = await importStore();
    store.setState({
      messages: [],
      sessionMessages: {},
      activeSessionId: sessionId,
      isStreaming: false,
    });
    store.getState().sendMessage("需要确认的操作");
    capturedCallbacks!.onEvent({ type: "text", content: "确认前回答" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-before-confirm",
      title: "确认前来源",
      doc: "before.pdf",
      page: 1,
      score: 0.8,
      excerpt: "首轮来源",
    });
    capturedCallbacks!.onEvent({
      type: "tool_confirm_required",
      calls: [{ name: "write_file", args: { path: "out.txt" }, call_id: "confirm-save", risk_level: "high" }],
      count: 1,
    });
    capturedCallbacks!.onEvent({
      type: "done",
      success: true,
      completed: false,
      awaiting_confirmation: true,
    } as ChatSSEEvent);
    capturedCallbacks!.onDone!();
    await initialAssistantStarted;

    const [userId, assistantId] = store.getState().sessionMessages[sessionId].map((message) => message.id);
    store.getState().resumeAgent("allow");
    capturedCallbacks!.onEvent({ type: "text", content: "，续跑完成" });
    capturedCallbacks!.onEvent({
      type: "source",
      id: "src-after-confirm",
      title: "续跑来源",
      doc: "after.pdf",
      page: 2,
      score: 0.95,
      excerpt: "续跑来源",
    });
    capturedCallbacks!.onEvent({ type: "done", success: true, completed: true });
    capturedCallbacks!.onDone!();

    await new Promise((resolve) => setTimeout(resolve, 0));
    const writesBeforeInitialSaveSettles = apiPostMock.mock.calls
      .filter((call) => call[0] === `sessions/${sessionId}/messages`).length;
    releaseInitialAssistant?.();
    await vi.waitFor(() => expect(store.getState().pendingSaveBySession?.[sessionId]).toBeUndefined());

    expect([...serverMessages.keys()].sort()).toEqual([userId, assistantId].sort());
    expect(serverMessages.get(assistantId)?.content).toBe("确认前回答，续跑完成");
    expect(writesBeforeInitialSaveSettles).toBe(2);
    expect(serverMessages.get(assistantId)?.sources).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "src-before-confirm" }),
      expect.objectContaining({ id: "src-after-confirm" }),
    ]));
    expect(apiPostMock.mock.calls.filter((call) =>
      call[0] === `sessions/${sessionId}/messages` && (call[1] as Record<string, unknown>).role === "user",
    )).toHaveLength(1);
    await vi.waitFor(() => expect(updateSessionMeta).toHaveBeenCalledWith(
      sessionId,
      expect.objectContaining({ msgCount: 2, preview: "需要确认的操作" }),
    ));
    expect(apiSSEPaths).toEqual(["chat", "agent-sandbox/resume"]);
  });
});
