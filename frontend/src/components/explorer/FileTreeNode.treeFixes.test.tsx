import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "./treeUtils";
import { useAppStore } from "../../stores/useAppStore";
import { FileTreeNode } from "./FileTreeNode";

vi.mock("../../api/client", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: vi.fn(), showInfo: vi.fn() }) },
}));
vi.mock("../../stores/useModalStore", () => ({
  useModalStore: { getState: () => ({ promptDialog: vi.fn(), confirmDialog: vi.fn() }) },
}));
vi.mock("../../i18n", () => ({
  t: (_key: string, fallback?: string) => fallback ?? _key,
  useI18n: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key, lang: "zh" }),
}));

function renderNode(node: FileNode, overrides: Partial<React.ComponentProps<typeof FileTreeNode>> = {}) {
  const props: React.ComponentProps<typeof FileTreeNode> = {
    node,
    depth: 0,
    expanded: new Set(),
    selectedPaths: new Set(),
    renaming: null,
    creating: null,
    dragOverPath: null,
    siblings: [node],
    newItemPlaceholder: { file: "New file", folder: "New folder" },
    onToggle: vi.fn(),
    onSelect: vi.fn(),
    onContextMenu: vi.fn(),
    onDragStart: vi.fn(),
    onDragEnd: vi.fn(),
    onDragOver: vi.fn(),
    onDragLeave: vi.fn(),
    onDrop: vi.fn(),
    onFinishRename: vi.fn(),
    onFinishCreate: vi.fn(),
    ...overrides,
  };
  return render(<FileTreeNode {...props} />);
}

describe("FileTreeNode tree interaction fixes", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ openFiles: [], activeFileId: null, pinnedFileIds: [] });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("exposes the full file name as a tooltip on the truncated label", () => {
    const longName = `${"L".repeat(180)}.txt`;
    const node: FileNode = { name: longName, type: "file", path: `C:/synthetic/${longName}` };
    const { container } = renderNode(node);

    const label = container.querySelector(".explorer-tree-label");
    expect(label?.getAttribute("title")).toBe(longName);
  });

  it("shows a permission-specific error with retry instead of an empty-folder hint", () => {
    const node: FileNode = {
      name: "denied-dir",
      type: "directory",
      path: "C:/synthetic/denied-dir",
      lazy: false,
      children: [],
    };
    const onRetry = vi.fn();
    renderNode(node, {
      expanded: new Set([node.path]),
      // Exact message the backend returns for EXPLORER_ACCESS_DENIED; the
      // API client surfaces only error.message, not the error code.
      dirErrors: new Map([[node.path, "没有权限读取该文件夹"]]),
      onRetry,
    });

    expect(screen.getByText("无权限读取该文件夹")).toBeTruthy();
    expect(screen.queryByText("空文件夹")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledWith(node);
  });

  it("keeps the raw message for non-permission load failures", () => {
    const node: FileNode = {
      name: "broken-dir",
      type: "directory",
      path: "C:/synthetic/broken-dir",
      lazy: false,
      children: [],
    };
    renderNode(node, {
      expanded: new Set([node.path]),
      dirErrors: new Map([[node.path, "network unreachable"]]),
    });

    expect(screen.getByText("加载失败: network unreachable")).toBeTruthy();
  });

  it("does not clear the drag highlight when the pointer moves inside the row", () => {
    const node: FileNode = { name: "drag.txt", type: "file", path: "C:/synthetic/drag.txt" };
    const onDragLeave = vi.fn();
    const { container } = renderNode(node, { onDragLeave });

    const row = container.querySelector(".explorer-tree-node[data-path]")!;
    const label = container.querySelector(".explorer-tree-label")!;
    // jsdom's fireEvent does not carry relatedTarget for drag events; dispatch a
    // MouseEvent so React sees the pointer target.
    row.dispatchEvent(new MouseEvent("dragleave", { bubbles: true, relatedTarget: label }));
    expect(onDragLeave).not.toHaveBeenCalled();

    row.dispatchEvent(new MouseEvent("dragleave", { bubbles: true, relatedTarget: document.body }));
    expect(onDragLeave).toHaveBeenCalledTimes(1);
  });
});
