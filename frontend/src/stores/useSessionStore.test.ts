import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiPostMock, setActiveSessionMock, setChatStateMock } = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  setActiveSessionMock: vi.fn(),
  setChatStateMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: vi.fn(),
  apiPost: apiPostMock,
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));

vi.mock("../utils/broadcast", () => ({
  post: vi.fn(),
  on: vi.fn(() => vi.fn()),
}));

vi.mock("./useChatStore", () => ({
  useChatStore: {
    getState: () => ({ setActiveSession: setActiveSessionMock }),
    setState: setChatStateMock,
  },
}));

vi.mock("./useSettingsStore", () => ({
  useSettingsStore: { getState: () => ({ chatModel: "gpt-4o" }) },
}));

vi.mock("./useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: vi.fn() }) },
}));

vi.mock("./useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));

async function loadSessionStore() {
  vi.resetModules();
  const { useSessionStore } = await import("./useSessionStore");
  return useSessionStore;
}

describe("useSessionStore.newSession", () => {
  beforeEach(() => {
    localStorage.clear();
    apiPostMock.mockReset().mockResolvedValue({ id: "server-session" });
    setActiveSessionMock.mockReset();
    setChatStateMock.mockReset();
  });

  it("strict 创建失败时拒绝请求且不留下本地幽灵会话", async () => {
    const store = await loadSessionStore();
    apiPostMock.mockRejectedValueOnce(new Error("模拟创建失败"));

    await expect(store.getState().newSession({ requirePersisted: true }))
      .rejects.toThrow("模拟创建失败");

    expect(apiPostMock).toHaveBeenCalledOnce();
    expect(store.getState().sessions).toEqual([]);
    expect(store.getState().creatingSession).toBe(false);
    expect(setActiveSessionMock).not.toHaveBeenCalled();
  });

  it("strict 创建返回 401 时仍设置 API Key 提示状态", async () => {
    const store = await loadSessionStore();
    apiPostMock.mockRejectedValueOnce(new Error("401 Unauthorized"));

    await expect(store.getState().newSession({ requirePersisted: true }))
      .rejects.toThrow("401 Unauthorized");

    expect(store.getState().sessions).toEqual([]);
    expect(store.getState().creatingSession).toBe(false);
    expect(setActiveSessionMock).not.toHaveBeenCalled();
    expect(setChatStateMock).toHaveBeenCalledWith({ needsApiKey: true });
  });

  it("strict 创建响应缺少有效 ID 时拒绝且不留下本地幽灵会话", async () => {
    const store = await loadSessionStore();
    apiPostMock.mockResolvedValueOnce({ id: "   " });

    await expect(store.getState().newSession({ requirePersisted: true }))
      .rejects.toThrow("后端未返回有效会话 ID");

    expect(store.getState().sessions).toEqual([]);
    expect(store.getState().creatingSession).toBe(false);
    expect(setActiveSessionMock).not.toHaveBeenCalled();
  });

  it("默认创建继续兼容服务端失败时的本地回退", async () => {
    const store = await loadSessionStore();
    apiPostMock.mockRejectedValueOnce(new Error("模拟离线"));

    const sessionId = await store.getState().newSession();

    expect(sessionId).toMatch(/^s\d+$/);
    expect(store.getState().sessions).toHaveLength(1);
    expect(store.getState().sessions[0].id).toBe(sessionId);
    expect(setActiveSessionMock).toHaveBeenCalledWith(sessionId);
  });
});
