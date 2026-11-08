import { describe, expect, it } from "vitest";
import {
  collectRefreshDirPaths,
  friendlyExplorerError,
  normalizePathSeparators,
  normalizeTreePaths,
  pathKey,
  type FileNode,
} from "./treeUtils";

describe("path normalization", () => {
  it("normalizes separators and trailing slashes for identity", () => {
    expect(normalizePathSeparators("E:\\project\\src\\main.ts")).toBe("E:/project/src/main.ts");
    expect(pathKey("E:\\project\\src\\")).toBe("E:/project/src");
    expect(pathKey("E:/project/src")).toBe("E:/project/src");
  });

  it("normalizes every path in a tree", () => {
    const tree: FileNode = {
      name: "project",
      type: "directory",
      path: "E:\\project",
      children: [
        { name: "src", type: "directory", path: "E:\\project\\src", lazy: true },
        { name: "a.txt", type: "file", path: "E:\\project\\a.txt" },
      ],
    };

    const normalized = normalizeTreePaths(tree);
    expect(normalized.path).toBe("E:/project");
    expect(normalized.children?.map((child) => child.path)).toEqual([
      "E:/project/src",
      "E:/project/a.txt",
    ]);
  });
});

describe("collectRefreshDirPaths", () => {
  it("refreshes expanded and already-loaded dirs, but not the root", () => {
    const tree: FileNode = {
      name: "project",
      type: "directory",
      path: "E:/project",
      lazy: false,
      children: [
        {
          name: "docs",
          type: "directory",
          path: "E:/project/docs",
          lazy: true,
        },
        {
          name: "src",
          type: "directory",
          path: "E:/project/src",
          lazy: false,
          children: [{ name: "main.ts", type: "file", path: "E:/project/src/main.ts" }],
        },
        {
          name: "empty",
          type: "directory",
          path: "E:/project/empty",
          lazy: true,
        },
        { name: "a.txt", type: "file", path: "E:/project/a.txt" },
      ],
    };

    const paths = collectRefreshDirPaths(tree, new Set(["E:/project/docs"]));
    expect(paths.sort()).toEqual(["E:/project/docs", "E:/project/src"]);
  });

  it("refreshes a loaded directory even when it is collapsed", () => {
    const tree: FileNode = {
      name: "project",
      type: "directory",
      path: "E:/project",
      lazy: false,
      children: [
        {
          name: "docs",
          type: "directory",
          path: "E:/project/docs",
          lazy: false,
          children: [],
        },
      ],
    };

    expect(collectRefreshDirPaths(tree, new Set())).toEqual(["E:/project/docs"]);
  });
});

describe("friendlyExplorerError", () => {
  it.each([
    ["security check failed: path does not exist", "文件或目录不存在，可能已被移动或删除"],
    ["write failed: [WinError 5] 拒绝访问。", "没有权限访问该文件或目录"],
    ["already exists: already exists: E:\\p\\a.txt", "目标已存在"],
    ["file too large: max 5242880 bytes", "文件过大，无法在编辑器中打开"],
    ["security check failed: root path is not a directory", "目标不是有效文件夹"],
    ["security check failed: path could not be resolved", "无法解析该路径"],
    ["file changed since it was read: E:\\p\\a.txt", "文件已在外部修改，请重新加载后再保存"],
    ["security check failed: path is outside authorized directories", "该项目尚未授权，请重新打开项目"],
    ["SSE 403: Forbidden", "该项目尚未授权，请重新打开项目"],
  ])("maps %s", (raw, expected) => {
    expect(friendlyExplorerError(new Error(raw))).toBe(expected);
  });

  it("strips the internal security prefix for unknown errors", () => {
    expect(friendlyExplorerError(new Error("security check failed: something odd"))).toBe("something odd");
  });
});
