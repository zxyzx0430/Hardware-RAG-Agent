import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Message } from "../types/session";

const {
  apiPostMock,
  apiSSEMock,
  apiGetMock,
  apiDeleteMock,
  broadcastPostMock,
  updateSessionMetaMock,
  sessionList,
} = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  apiSSEMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiDeleteMock: vi.fn(),
  broadcastPostMock: vi.fn(),
  updateSessionMetaMock: vi.fn(),
  sessionList: [] as Array<Record<string, unknown>>,
}));

vi.mock("../api/client", () => ({
  apiPost: apiPostMock,
  apiSSE: apiSSEMock,
  apiGet: apiGetMock,
  apiDelete: apiDeleteMock,
}));

vi.mock("../utils/broadcast", () => ({
  post: broadcastPostMock,
  on: vi.fn(() => vi.fn()),
}));

vi.mock("./useSessionStore", () => ({
  CONTEXT_WINDOW_256K: 262144,
  useSessionStore: {
    getState: () => ({ sessions: sessionList, updateSessionMeta: updateSessionMetaMock }),
  },
}));

vi.mock("./useAppStore", () => ({ useAppStore: { getState: () => ({}) } }));
vi.mock("./useSettingsStore", () => ({ useSettingsStore: { getState: () => ({}) } }));
vi.mock("./useLogStore", () => ({ useLogStore: { getState: () => ({ log: vi.fn() }) } }));
vi.mock("./useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: vi.fn(), showWarning: vi.fn() }) },
}));
vi.mock("./useWorkbenchBridge", () => ({
  handleToolCallEvent: vi.fn(),
  handleToolResultEvent: vi.fn(),
  handleCompileLogEvent: vi.fn(),
  handleProgressEvent: vi.fn(),
}));

async function importStore() {
  return (await import("./useChatStore")).useChatStore;
}

function makeTurn(): Message[] {
  return [
    { id: "local-user", role: "user", content: "本地保存失败问题", timestamp: 1 },
    { id: "local-assistant", role: "assistant", content: "回答与来源", timestamp: 2 },
  ];
}

describe("useChatStore local persistence failures", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    apiPostMock.mockReset().mockResolvedValue({ success: true, data: {} });
    apiSSEMock.mockReset();
    apiGetMock.mockReset().mockResolvedValue({ messages: [] });
    apiDeleteMock.mockReset();
    broadcastPostMock.mockReset();
    updateSessionMetaMock.mockReset();
    sessionList.splice(0, sessionList.length, { id: "persistence-session", title: "测试会话" });
  });

  afterEach(() => vi.restoreAllMocks());

  it("本地分片写入失败时显示待保存；恢复后重试只写本地快照", async () => {
    const sessionId = "persistence-session";
    const store = await importStore();
    const messages = makeTurn();
    store.setState({
      activeSessionId: sessionId,
      messages,
      sessionMessages: { [sessionId]: messages },
      isStreaming: false,
    });

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

    const saved = await store.getState().persistLastTurn(sessionId);

    expect(saved).toBe(false);
    expect(store.getState().pendingSaveBySession[sessionId]).toBe("pending");
    expect(apiPostMock.mock.calls.filter(([path]) => path === `sessions/${sessionId}/messages`)).toHaveLength(2);
    expect(apiSSEMock).not.toHaveBeenCalled();

    const postCountBeforeRetry = apiPostMock.mock.calls.length;
    shardWriteFailure.mockRestore();
    await store.getState().retryPendingSave(sessionId);

    expect(apiPostMock).toHaveBeenCalledTimes(postCountBeforeRetry);
    expect(apiSSEMock).not.toHaveBeenCalled();
    expect(store.getState().pendingSaveBySession[sessionId]).toBeUndefined();
    const persisted = JSON.parse(localStorage.getItem(`hwrag_msg_${sessionId}`) || "null") as Message[];
    expect(persisted.map(({ id, content }) => ({ id, content }))).toEqual(
      messages.map(({ id, content }) => ({ id, content })),
    );
  });

  it("本地写入成功但保存期间快照变化时仍保留待保存状态", async () => {
    const sessionId = "persistence-session";
    const store = await importStore();
    const messages = makeTurn();
    store.setState({
      activeSessionId: sessionId,
      messages,
      sessionMessages: { [sessionId]: messages },
      isStreaming: false,
    });

    let notifyUserPostStarted!: () => void;
    const userPostStarted = new Promise<void>((resolve) => { notifyUserPostStarted = resolve; });
    let releaseUserPost!: () => void;
    apiPostMock.mockImplementation(async (path: string, payload: Record<string, unknown>) => {
      if (path === `sessions/${sessionId}/messages` && payload.role === "user") {
        notifyUserPostStarted();
        await new Promise<void>((resolve) => { releaseUserPost = resolve; });
      }
      return { success: true, data: { id: payload.id } };
    });

    const savePromise = store.getState().persistLastTurn(sessionId);
    await userPostStarted;
    const current = store.getState().sessionMessages[sessionId];
    const changed = current.map((message) => message.id === "local-assistant"
      ? { ...message, content: "更新中的新回答" }
      : message);
    store.setState({
      messages: changed,
      sessionMessages: { ...store.getState().sessionMessages, [sessionId]: changed },
    });
    releaseUserPost();

    const saved = await savePromise;

    expect(saved).toBe(false);
    expect(store.getState().sessionMessages[sessionId][1].content).toBe("更新中的新回答");
    expect(store.getState().pendingSaveBySession[sessionId]).toBe("pending");
    const persisted = JSON.parse(localStorage.getItem(`hwrag_msg_${sessionId}`) || "null") as Message[];
    expect(persisted[1].content).toBe("更新中的新回答");
    expect(apiPostMock.mock.calls.filter(([path]) => path === `sessions/${sessionId}/messages`)).toHaveLength(2);
    expect(apiSSEMock).not.toHaveBeenCalled();
  });
});
