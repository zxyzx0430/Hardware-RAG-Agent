import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BackendMessage } from "../types/api";
import type { Message, Session, SourceRef } from "../types/session";

const { apiGetMock, apiPostMock, apiPutMock, apiPatchMock, apiDeleteMock, apiSSEMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
  apiSSEMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPut: apiPutMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
  apiSSE: apiSSEMock,
}));

const confirmedSessionId = "reload-confirmed-session";
const unavailableSessionId = "reload-unavailable-session";
const source: SourceRef = {
  id: "source-reload-1",
  title: "ESP32-S3 技术参考手册",
  doc: "esp32-s3-reference.pdf",
  page: 12,
  score: 0.94,
  excerpt: "GPIO 配置说明",
};

function makeMessages(prefix: string): Message[] {
  return [
    { id: `${prefix}-user`, role: "user", content: "GPIO 如何配置？", timestamp: 1 },
    {
      id: `${prefix}-assistant`,
      role: "assistant",
      content: "请先确认引脚复用配置。",
      timestamp: 2,
      sources: [source],
    },
  ];
}

function asBackendMessages(messages: Message[]): BackendMessage[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content as string,
    sources: message.sources || [],
    created_at: "2026-10-24T00:00:00Z",
  }));
}

function makeSession(id: string): Session {
  return {
    id,
    title: "回载测试会话",
    preview: "",
    model: "",
    createdAt: 1,
    project: "",
    pinned: false,
    msgCount: 0,
    contextWindow: 262144,
  };
}

function seedPendingSnapshot(sessionId: string, messages: Message[]): void {
  const pendingMessages = messages.map((message) => ({
    ...message,
    __hwrag_pending_save: true,
  }));
  localStorage.setItem(`hwrag_msg_${sessionId}`, JSON.stringify(pendingMessages));
  localStorage.setItem(
    `hwrag_pending_save_${encodeURIComponent(sessionId)}`,
    JSON.stringify(messages.map((message) => message.id)),
  );
}

async function importChatStore() {
  return (await import("./useChatStore")).useChatStore;
}

describe("useChatStore persistence after reload", () => {
  const confirmedMessages = makeMessages("confirmed");
  const unavailableMessages = makeMessages("unavailable");

  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    apiGetMock.mockReset().mockImplementation(async (path: string) => {
      if (path === `sessions/${confirmedSessionId}/messages`) {
        return { messages: asBackendMessages(confirmedMessages) };
      }
      if (path === `sessions/${unavailableSessionId}/messages`) {
        throw new Error("simulated session GET failure");
      }
      throw new Error(`unexpected GET: ${path}`);
    });
    apiPostMock.mockReset().mockResolvedValue({});
    apiPutMock.mockReset().mockResolvedValue({});
    apiPatchMock.mockReset().mockResolvedValue({});
    apiDeleteMock.mockReset().mockResolvedValue({});
    apiSSEMock.mockReset();

    localStorage.setItem(
      "hwrag_sessions",
      JSON.stringify([makeSession(confirmedSessionId), makeSession(unavailableSessionId)]),
    );
    localStorage.setItem("hwrag_active_session", confirmedSessionId);
    seedPendingSnapshot(confirmedSessionId, confirmedMessages);
    seedPendingSnapshot(unavailableSessionId, unavailableMessages);
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("hydrates pending markers, clears an exact backend confirmation, and keeps local data when GET fails", async () => {
    const store = await importChatStore();

    expect(store.getState().pendingSaveBySession[confirmedSessionId]).toBe("pending");
    expect(store.getState().pendingSaveBySession[unavailableSessionId]).toBe("pending");
    const confirmedHydratedMessages = store.getState().sessionMessages[confirmedSessionId] as Array<
      Message & { __hwrag_pending_save?: true }
    >;
    expect(confirmedHydratedMessages.map(({ id }) => id)).toEqual(confirmedMessages.map(({ id }) => id));
    expect(confirmedHydratedMessages.every((message) => message.__hwrag_pending_save === true)).toBe(true);

    await store.getState().fetchMessages(confirmedSessionId);

    expect(apiGetMock).toHaveBeenCalledWith(`sessions/${confirmedSessionId}/messages`);
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(apiSSEMock).not.toHaveBeenCalled();
    expect(store.getState().pendingSaveBySession[confirmedSessionId]).toBeUndefined();
    expect(localStorage.getItem(`hwrag_pending_save_${encodeURIComponent(confirmedSessionId)}`)).toBeNull();
    const confirmedShard = JSON.parse(
      localStorage.getItem(`hwrag_msg_${confirmedSessionId}`) || "null",
    ) as Message[];
    expect(confirmedShard.map(({ id, content }) => ({ id, content }))).toEqual(
      confirmedMessages.map(({ id, content }) => ({ id, content })),
    );
    expect(confirmedShard[1].sources).toEqual([source]);
    expect(confirmedShard.every((message) => !("__hwrag_pending_save" in message))).toBe(true);

    await store.getState().fetchMessages(unavailableSessionId);

    expect(apiGetMock).toHaveBeenCalledWith(`sessions/${unavailableSessionId}/messages`);
    expect(apiPostMock).not.toHaveBeenCalled();
    expect(apiSSEMock).not.toHaveBeenCalled();
    expect(store.getState().pendingSaveBySession[unavailableSessionId]).toBe("pending");
    expect(store.getState().sessionMessages[unavailableSessionId].map(({ id, content }) => ({ id, content }))).toEqual(
      unavailableMessages.map(({ id, content }) => ({ id, content })),
    );
    expect(store.getState().sessionMessages[unavailableSessionId][1].sources).toEqual([source]);
    expect(localStorage.getItem(`hwrag_pending_save_${encodeURIComponent(unavailableSessionId)}`)).not.toBeNull();
    const unavailableShard = JSON.parse(
      localStorage.getItem(`hwrag_msg_${unavailableSessionId}`) || "null",
    ) as Array<Message & { __hwrag_pending_save?: true }>;
    expect(unavailableShard.map(({ id }) => id)).toEqual(unavailableMessages.map(({ id }) => id));
    expect(unavailableShard.every((message) => message.__hwrag_pending_save === true)).toBe(true);
  });
});
