import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadSessionMessages, migrateMessagesToShards, saveSessionMessages } from "./persistence";

describe("session message Skill trace persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("writes only safe Skill fields while preserving ordinary message data", () => {
    const ordinaryStep = {
      type: "tool",
      id: "ordinary-call",
      call_id: "ordinary-call",
      name: "write_file",
      args: "{\"path\":\"output.txt\"}",
      result: "ordinary result",
      status: "done",
      file_path: "output.txt",
    };
    const reasoningStep = {
      type: "thinking",
      id: "reasoning-step",
      source: "reasoning",
      content: "reasoning remains unchanged",
    };
    const messages = [{
      id: "assistant-message",
      role: "assistant",
      content: "answer remains unchanged",
      timestamp: 17,
      sources: [{ id: "source-1", title: "source", doc: "board.pdf", page: 1, score: 0.8, excerpt: "kept" }],
      __hwrag_pending_save: true,
      activity: {
        durationMs: 31,
        status: "done",
        steps: [
          reasoningStep,
          {
            type: "tool",
            id: "skill-call",
            call_id: "skill-call",
            name: "load_skill",
            args: "{\"skill_id\":\"fixture-skill\",\"raw_args\":\"ARG_SENTINEL\"}",
            result: "BODY_SENTINEL",
            status: "done",
            skill_trace: {
              skill_id: "fixture-skill",
              content_hash: "d".repeat(64),
              stage: "instructions",
              success: true,
              instructions: "TRACE_SENTINEL",
              error_message: "ERROR_SENTINEL",
            },
          },
          ordinaryStep,
        ],
      },
    }];

    saveSessionMessages("s1", messages);

    const saved = JSON.parse(localStorage.getItem("hwrag_msg_s1") || "null") as typeof messages;
    expect(saved[0]).toMatchObject({
      id: "assistant-message",
      content: "answer remains unchanged",
      sources: messages[0].sources,
      __hwrag_pending_save: true,
      activity: { durationMs: 31, status: "done" },
    });
    expect(saved[0].activity.steps[0]).toEqual(reasoningStep);
    expect(saved[0].activity.steps[1]).toEqual({
      type: "tool",
      id: "skill-call",
      call_id: "skill-call",
      name: "load_skill",
      args: "{\"skill_id\":\"fixture-skill\"}",
      result: "Skill instructions loaded",
      status: "done",
      skill_trace: {
        skill_id: "fixture-skill",
        content_hash: "d".repeat(64),
        stage: "instructions",
        success: true,
      },
    });
    expect(saved[0].activity.steps[2]).toEqual(ordinaryStep);
    expect(JSON.stringify(saved)).not.toContain("SENTINEL");
  });

  it("projects existing shard data on read and fails old untraced Skill steps closed", () => {
    const ordinaryStep = {
      type: "tool",
      id: "ordinary-read-call",
      call_id: "ordinary-read-call",
      name: "write_file",
      args: "{\"path\":\"output.txt\"}",
      result: "ordinary result",
      status: "done",
    };
    const rawMessages = [{
      id: "cached-assistant",
      role: "assistant",
      content: "cached answer",
      timestamp: 21,
      sources: [{ id: "cached-source", title: "source", doc: "board.pdf", page: 2, score: 0.7, excerpt: "kept" }],
      __hwrag_pending_save: true,
      activity: {
        durationMs: 44,
        steps: [
          {
            type: "tool",
            id: "cached-skill-call",
            call_id: "cached-skill-call",
            name: "load_skill",
            args: "{\"skill_id\":\"fixture-skill\",\"body\":\"ARG_SENTINEL\"}",
            result: "RAW_CACHED_BODY_SENTINEL",
            status: "done",
            skill_trace: {
              skill_id: "fixture-skill",
              content_hash: "f".repeat(64),
              stage: "instructions",
              success: true,
              instructions: "TRACE_SENTINEL",
            },
          },
          {
            type: "tool",
            id: "legacy-skill-call",
            call_id: "legacy-skill-call",
            name: "read_skill_resource",
            args: "{\"skill_id\":\"fixture-skill\",\"path\":\"references/old.md\",\"raw\":\"ARG_SENTINEL\"}",
            result: "{\"output\":\"RAW_LEGACY_BODY_SENTINEL\"}",
            status: "done",
          },
          ordinaryStep,
        ],
      },
    }];
    const rawJson = JSON.stringify(rawMessages);
    localStorage.setItem("hwrag_msg_s1", rawJson);

    const loaded = loadSessionMessages<typeof rawMessages>("s1", []);

    expect(loaded[0]).toMatchObject({
      id: "cached-assistant",
      content: "cached answer",
      sources: rawMessages[0].sources,
      __hwrag_pending_save: true,
      activity: { durationMs: 44 },
    });
    expect(loaded[0].activity.steps[0]).toMatchObject({
      args: "{\"skill_id\":\"fixture-skill\"}",
      result: "Skill instructions loaded",
      status: "done",
      skill_trace: {
        skill_id: "fixture-skill",
        content_hash: "f".repeat(64),
        stage: "instructions",
        success: true,
      },
    });
    expect(loaded[0].activity.steps[1]).toMatchObject({
      args: "{\"skill_id\":\"fixture-skill\",\"path\":\"references/old.md\"}",
      result: "Skill operation failed",
      status: "error",
      skill_trace: { skill_id: "fixture-skill", stage: "resource", success: false },
    });
    expect(loaded[0].activity.steps[2]).toEqual(ordinaryStep);
    expect(JSON.stringify(loaded)).not.toContain("SENTINEL");
    expect(localStorage.getItem("hwrag_msg_s1")).toBe(rawJson);
  });

  it("keeps the existing quota-exceeded storage event", () => {
    const error = new DOMException("storage quota exceeded", "QuotaExceededError");
    vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw error; });
    const listener = vi.fn();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    window.addEventListener("storage-quota-exceeded", listener);

    saveSessionMessages("s1", []);

    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0]).toMatchObject({ detail: { key: "msg_s1" } });
    expect(consoleError).toHaveBeenCalledWith("[persistence] 存储空间不足: msg_s1");
    window.removeEventListener("storage-quota-exceeded", listener);
  });

  it("keeps the legacy cache after a shard quota failure and retries both sessions", () => {
    const ordinaryStep = {
      type: "tool",
      id: "ordinary-call",
      call_id: "ordinary-call",
      name: "write_file",
      args: "{\"path\":\"output.txt\"}",
      result: "ordinary result",
      status: "done",
    };
    const legacyMessages = {
      s1: [{
        id: "message-s1",
        role: "assistant",
        content: "answer from the first session",
        sources: [{ id: "source-s1", title: "source", doc: "fixture.pdf", page: 1, content: "kept" }],
        __hwrag_pending_save: true,
        activity: {
          steps: [{
            type: "tool",
            id: "skill-call-s1",
            call_id: "skill-call-s1",
            name: "load_skill",
            args: "{\"skill_id\":\"fixture-skill\",\"body\":\"ARG_SENTINEL\"}",
            result: "RAW_SKILL_BODY_SENTINEL",
            status: "done",
            skill_trace: {
              skill_id: "fixture-skill",
              content_hash: "a".repeat(64),
              stage: "instructions",
              success: true,
              instructions: "TRACE_SENTINEL",
            },
          }, ordinaryStep],
        },
      }],
      s2: [{
        id: "message-s2",
        role: "assistant",
        content: "answer from the second session",
        sources: [{ id: "source-s2", title: "source", doc: "fixture.pdf", page: 2, content: "also kept" }],
        __hwrag_pending_save: true,
        activity: { steps: [{
          type: "tool",
          id: "skill-call-s2",
          call_id: "skill-call-s2",
          name: "read_skill_resource",
          args: "{\"skill_id\":\"fixture-skill\",\"path\":\"references/pins.md\",\"raw\":\"ARG_SENTINEL\"}",
          result: "RAW_RESOURCE_SENTINEL",
          status: "done",
        }, ordinaryStep] },
      }],
    };
    const legacyKey = "hwrag_session_messages";
    const legacyRaw = JSON.stringify(legacyMessages);
    localStorage.setItem(legacyKey, legacyRaw);

    const quotaError = new DOMException("storage quota exceeded", "QuotaExceededError");
    const originalSetItem = Storage.prototype.setItem;
    let shouldFailSecondShard = true;
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key: string, value: string) {
      if (key === "hwrag_msg_s2" && shouldFailSecondShard) {
        shouldFailSecondShard = false;
        throw quotaError;
      }
      return originalSetItem.call(this, key, value);
    });
    const listener = vi.fn();
    window.addEventListener("storage-quota-exceeded", listener);

    try {
      migrateMessagesToShards(legacyMessages);

      expect(localStorage.getItem(legacyKey)).toBe(legacyRaw);
      expect(listener).toHaveBeenCalledOnce();
      expect(listener.mock.calls[0][0]).toMatchObject({ detail: { key: "msg_s2" } });
      const firstFromShard = loadSessionMessages<typeof legacyMessages.s1>("s1", []);
      expect(firstFromShard[0]).toMatchObject({
        id: "message-s1",
        content: "answer from the first session",
        __hwrag_pending_save: true,
        activity: { steps: [{ result: "Skill instructions loaded", status: "done" }, ordinaryStep] },
      });
      const secondFromLegacy = loadSessionMessages<typeof legacyMessages.s2>("s2", []);
      expect(secondFromLegacy[0]).toMatchObject({
        id: "message-s2",
        content: "answer from the second session",
        sources: legacyMessages.s2[0].sources,
        __hwrag_pending_save: true,
        activity: { steps: [{
          args: "{\"skill_id\":\"fixture-skill\",\"path\":\"references/pins.md\"}",
          result: "Skill operation failed",
          status: "error",
          skill_trace: { skill_id: "fixture-skill", stage: "resource", success: false },
        }, ordinaryStep] },
      });
      expect(JSON.stringify([firstFromShard, secondFromLegacy])).not.toContain("SENTINEL");
      expect(localStorage.getItem(legacyKey)).toBe(legacyRaw);
      expect(setItem).toHaveBeenCalledTimes(2);

      setItem.mockImplementation(originalSetItem);
      migrateMessagesToShards(legacyMessages);

      expect(localStorage.getItem(legacyKey)).toBeNull();
      const firstSession = loadSessionMessages<typeof legacyMessages.s1>("s1", []);
      expect(firstSession[0]).toMatchObject({
        id: "message-s1",
        content: "answer from the first session",
        sources: legacyMessages.s1[0].sources,
        __hwrag_pending_save: true,
        activity: { steps: [{
          args: "{\"skill_id\":\"fixture-skill\"}",
          result: "Skill instructions loaded",
          status: "done",
          skill_trace: { skill_id: "fixture-skill", content_hash: "a".repeat(64), success: true },
        }, ordinaryStep] },
      });
      const secondSession = loadSessionMessages<typeof legacyMessages.s2>("s2", []);
      expect(secondSession[0]).toMatchObject({
        id: "message-s2",
        content: "answer from the second session",
        sources: legacyMessages.s2[0].sources,
        __hwrag_pending_save: true,
        activity: { steps: [{
          args: "{\"skill_id\":\"fixture-skill\",\"path\":\"references/pins.md\"}",
          result: "Skill operation failed",
          status: "error",
          skill_trace: { skill_id: "fixture-skill", stage: "resource", success: false },
        }, ordinaryStep] },
      });
      expect(JSON.stringify([firstSession, secondSession])).not.toContain("SENTINEL");
      expect(listener).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener("storage-quota-exceeded", listener);
    }
  });

  it("keeps the legacy cache when a written shard cannot be read back", () => {
    const legacyMessages = {
      s1: [{ id: "message-s1", role: "user", content: "keep the first" }],
      s2: [{ id: "message-s2", role: "user", content: "keep the second" }],
    };
    const legacyRaw = JSON.stringify(legacyMessages);
    localStorage.setItem("hwrag_session_messages", legacyRaw);
    const originalGetItem = Storage.prototype.getItem;
    let secondShardReads = 0;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
      if (key === "hwrag_msg_s2") {
        secondShardReads += 1;
        if (secondShardReads === 2) throw new Error("synthetic read-back failure");
      }
      return originalGetItem.call(this, key);
    });

    migrateMessagesToShards(legacyMessages);

    expect(localStorage.getItem("hwrag_session_messages")).toBe(legacyRaw);
  });

  it("keeps the legacy cache when shard read-back bytes differ", () => {
    const legacyMessages = {
      s1: [{ id: "message-s1", role: "user", content: "keep the first" }],
      s2: [{ id: "message-s2", role: "user", content: "keep the second" }],
    };
    const legacyRaw = JSON.stringify(legacyMessages);
    localStorage.setItem("hwrag_session_messages", legacyRaw);
    const originalGetItem = Storage.prototype.getItem;
    let secondShardReads = 0;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
      if (key === "hwrag_msg_s2") {
        secondShardReads += 1;
        if (secondShardReads === 2) return "[]";
      }
      return originalGetItem.call(this, key);
    });

    migrateMessagesToShards(legacyMessages);

    expect(localStorage.getItem("hwrag_session_messages")).toBe(legacyRaw);
  });

  it("prefers an existing empty shard and does not fall back from a malformed shard", () => {
    const legacy = { s1: [{ id: "legacy-message", role: "user", content: "legacy copy" }] };
    localStorage.setItem("hwrag_session_messages", JSON.stringify(legacy));
    localStorage.setItem("hwrag_msg_s1", "[]");

    expect(loadSessionMessages("s1", [{ id: "fallback" }])).toEqual([]);

    const fallback = [{ id: "fallback" }];
    localStorage.setItem("hwrag_msg_s1", "not-json");
    expect(loadSessionMessages("s1", fallback)).toBe(fallback);
  });

  it("does not replace an existing empty shard with stale legacy messages", () => {
    const legacyMessages = { s1: [{ id: "stale-message", role: "user", content: "do not revive" }] };
    const legacyRaw = JSON.stringify(legacyMessages);
    localStorage.setItem("hwrag_session_messages", legacyRaw);
    localStorage.setItem("hwrag_msg_s1", "[]");

    migrateMessagesToShards(legacyMessages);

    expect(localStorage.getItem("hwrag_msg_s1")).toBe("[]");
    expect(localStorage.getItem("hwrag_session_messages")).toBe(legacyRaw);
    expect(loadSessionMessages("s1", [{ id: "fallback" }])).toEqual([]);
  });

  it("keeps the legacy source when migration has no session shards to verify", () => {
    const legacyRaw = JSON.stringify({ s1: [{ id: "message-s1", role: "user", content: "keep me" }] });
    localStorage.setItem("hwrag_session_messages", legacyRaw);

    migrateMessagesToShards({});

    expect(localStorage.getItem("hwrag_session_messages")).toBe(legacyRaw);
  });

  it.each([
    ["array root", "s1", '[{"s1":[{"id":"legacy"}]}]'],
    ["non-array session value", "s1", '{"s1":{"id":"legacy"}}'],
    ["malformed root", "s1", "{"],
    ["inherited session key", "toString", "{}"],
    ["prototype session key", "__proto__", '{"__proto__":[{"id":"legacy"}]}'],
  ])("returns the caller fallback for a %s in the legacy cache", (_label, sessionId, rawLegacy) => {
    const fallback = [{ id: "fallback" }];
    localStorage.setItem("hwrag_session_messages", rawLegacy);

    expect(loadSessionMessages(sessionId, fallback)).toBe(fallback);
  });
});
