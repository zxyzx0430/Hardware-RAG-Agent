import { describe, expect, it } from "vitest";
import { createFollowToastThrottle, isIgnoredWatchPath } from "./watchEvents";

describe("file watcher event filtering", () => {
  it.each([
    "E:/project/backend/data/hardware_rag.db-wal",
    "E:\\project\\backend\\data\\hardware_rag.db-shm",
    "C:/tmp/cache.sqlite-wal",
    "C:/tmp/cache.sqlite-shm",
    "C:/tmp/notes.db-journal",
  ])("ignores SQLite sidecar writes: %s", (path) => {
    expect(isIgnoredWatchPath(path)).toBe(true);
  });

  it.each([
    "src/main.ts",
    "README.md",
    "backend/data/hardware_rag.db",
    "frontend/src/styles/chat.css",
    null,
    undefined,
  ])("keeps normal project paths: %s", (path) => {
    expect(isIgnoredWatchPath(path)).toBe(false);
  });
});

describe("follow-mode toast throttle", () => {
  it("allows one toast per interval and coalesces the rest", () => {
    const throttle = createFollowToastThrottle(2000);
    expect(throttle.shouldShow(0)).toBe(true);
    expect(throttle.shouldShow(500)).toBe(false);
    expect(throttle.shouldShow(1999)).toBe(false);
    expect(throttle.shouldShow(2000)).toBe(true);
    expect(throttle.shouldShow(2200)).toBe(false);
    expect(throttle.shouldShow(4500)).toBe(true);
  });
});
