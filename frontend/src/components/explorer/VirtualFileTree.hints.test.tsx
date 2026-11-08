import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "./treeUtils";
import { useAppStore } from "../../stores/useAppStore";
import { VirtualFileTree } from "./VirtualFileTree";

vi.mock("../../api/client", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: {
    getState: () => ({ showError: vi.fn(), showSuccess: vi.fn(), showInfo: vi.fn() }),
  },
}));
vi.mock("../../i18n", () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }));

const ROOT = "C:/synthetic/root";
const LONG_NAME = `${"V".repeat(180)}.txt`;

const tree: FileNode = {
  name: "root",
  type: "directory",
  path: ROOT,
  lazy: false,
  children: [
    { name: "empty", type: "directory", path: `${ROOT}/empty`, lazy: false, children: [] },
    { name: "denied", type: "directory", path: `${ROOT}/denied`, lazy: false, children: [] },
    { name: LONG_NAME, type: "file", path: `${ROOT}/${LONG_NAME}` },
  ],
};

function renderTree(overrides: Partial<React.ComponentProps<typeof VirtualFileTree>> = {}) {
  const props: React.ComponentProps<typeof VirtualFileTree> = {
    tree,
    expanded: new Set([ROOT, `${ROOT}/empty`, `${ROOT}/denied`]),
    selectedPaths: new Set(),
    renaming: null,
    creating: null,
    dragOverPath: null,
    dirErrors: new Map([[`${ROOT}/denied`, "PermissionError: permission denied"]]),
    newItemPlaceholder: { file: "New file", folder: "New folder" },
    onToggle: vi.fn(),
    onSelect: vi.fn(),
    onContextMenu: vi.fn(),
    onFinishRename: vi.fn(),
    onFinishCreate: vi.fn(),
    onDragStart: vi.fn(),
    onDragEnd: vi.fn(),
    onDragOver: vi.fn(),
    onDragLeave: vi.fn(),
    onDrop: vi.fn(),
    ...overrides,
  };
  return render(<VirtualFileTree {...props} />);
}

describe("VirtualFileTree auxiliary rows", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ openFiles: [], activeFileId: null, pinnedFileIds: [] });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("renders empty-folder hints and load errors as list rows", () => {
    renderTree();

    expect(screen.getAllByText("空文件夹")).toHaveLength(1);
    expect(screen.getByText("无权限读取该文件夹")).toBeTruthy();
  });

  it("keeps the full name as a tooltip in virtual rows", () => {
    const { container } = renderTree();
    const label = container.querySelector(`.explorer-tree-label[title="${LONG_NAME}"]`);
    expect(label).toBeTruthy();
    expect(label?.textContent).toBe(LONG_NAME);
  });

  it("retries a failed directory from its virtual row", () => {
    const onRetry = vi.fn();
    renderTree({ onRetry });

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(onRetry.mock.calls[0][0].path).toBe(`${ROOT}/denied`);
  });

  it("renders the new-item input as a virtual row", () => {
    renderTree({ creating: { path: ROOT, type: "file" } });
    expect(screen.getByPlaceholderText("New file")).toBeTruthy();
  });
});
