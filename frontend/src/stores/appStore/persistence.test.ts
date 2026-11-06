import { beforeEach, describe, expect, it } from "vitest";
import type { OpenFileItem } from "../../types";
import {
  loadInputBarAuto,
  loadOpenFiles,
  loadRightMode,
  saveInputBarAuto,
  saveOpenFiles,
  saveRightMode,
} from "./persistence";

describe("Explorer editor draft persistence", () => {
  beforeEach(() => localStorage.clear());

  it("restores a dirty file draft after the store is recreated", () => {
    const draft: OpenFileItem = {
      id: "C:/workspace/main.py",
      path: "C:/workspace/main.py",
      name: "main.py",
      content: "unsaved edit",
      snapshot: "on disk",
      dirty: true,
      pinned: true,
    };

    saveOpenFiles([draft]);
    const restored = loadOpenFiles();

    expect(restored).toHaveLength(1);
    expect(restored[0]).toMatchObject({
      id: draft.id,
      path: draft.path,
      content: "unsaved edit",
      snapshot: "on disk",
      dirty: true,
      pinned: true,
    });
  });

  it("retains a dirty buffer draft across refresh", () => {
    const buffer: OpenFileItem = {
      id: "buffer-1",
      path: "buffer://snippet.ino",
      name: "snippet.ino",
      content: "void setup() {}",
      snapshot: "",
      dirty: true,
      isBuffer: true,
      language: "cpp",
    };

    saveOpenFiles([buffer]);

    expect(loadOpenFiles()[0]).toMatchObject({
      id: "buffer-1",
      path: "buffer://snippet.ino",
      content: "void setup() {}",
      dirty: true,
      isBuffer: true,
      language: "cpp",
    });
  });
});

describe("composer auto width persistence", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to auto and remembers an explicit manual mode", () => {
    expect(loadInputBarAuto()).toBe(true);

    saveInputBarAuto(false);
    expect(loadInputBarAuto()).toBe(false);

    saveInputBarAuto(true);
    expect(loadInputBarAuto()).toBe(true);
  });
});

describe("right panel mode persistence", () => {
  beforeEach(() => localStorage.clear());

  it("restores the content mode after a refresh", () => {
    expect(loadRightMode()).toBe("workbench");

    saveRightMode("content");
    expect(loadRightMode()).toBe("content");

    saveRightMode("workbench");
    expect(loadRightMode()).toBe("workbench");
  });
});
