import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "./treeUtils";
import { useAppStore } from "../../stores/useAppStore";
import { FileTree } from "./FileTree";

const { apiPostMock, toastErrorMock, confirmMock } = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  toastErrorMock: vi.fn(),
  confirmMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({ apiGet: vi.fn(), apiPost: apiPostMock }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: {
    getState: () => ({ showError: toastErrorMock, showSuccess: vi.fn(), showInfo: vi.fn() }),
  },
}));
vi.mock("../../stores/useModalStore", () => ({
  useModalStore: { getState: () => ({ promptDialog: vi.fn(), confirmDialog: confirmMock }) },
}));
vi.mock("../../i18n", () => ({
  t: (_key: string, fallback?: string) => fallback ?? _key,
  useI18n: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key, lang: "zh" }),
}));

const ROOT = "C:/synthetic/root";
const ONE = `${ROOT}/one.txt`;
const TWO = `${ROOT}/two.txt`;

const tree: FileNode = {
  name: "root",
  type: "directory",
  path: ROOT,
  lazy: false,
  children: [
    { name: "one.txt", type: "file", path: ONE },
    { name: "two.txt", type: "file", path: TWO },
  ],
};

describe("FileTree multi-delete partial failure", () => {
  beforeEach(() => {
    localStorage.clear();
    apiPostMock.mockReset();
    toastErrorMock.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    useAppStore.setState({ openFiles: [], activeFileId: null, pinnedFileIds: [] });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("refreshes the tree and keeps failed entries selected when some deletions fail", async () => {
    apiPostMock.mockImplementation((path: string, body?: { path?: string }) => {
      if (path === "explorer/delete" && body?.path === ONE) return Promise.resolve({});
      if (path === "explorer/delete" && body?.path === TWO) return Promise.reject(new Error("file is locked"));
      return Promise.resolve({});
    });
    const onRefresh = vi.fn();
    const setSelectedPaths = vi.fn();
    const { container } = render(
      <FileTree
        tree={tree}
        searchQuery=""
        expanded={new Set([ROOT])}
        setExpanded={vi.fn()}
        selectedPaths={new Set([ONE, TWO])}
        setSelectedPaths={setSelectedPaths}
        rootPath={ROOT}
        onRefresh={onRefresh}
        onLoadNodeChildren={vi.fn().mockResolvedValue(undefined)}
        loadingDirPaths={new Set()}
        dirErrors={new Map()}
        onMatchCountChange={vi.fn()}
      />,
    );

    const row = container.querySelector(`.explorer-tree-node[data-path="${ONE}"]`)!;
    fireEvent.contextMenu(row, { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("button", { name: "删除选中 (2)" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(apiPostMock).toHaveBeenCalledWith("explorer/delete", { path: ONE });
    expect(apiPostMock).toHaveBeenCalledWith("explorer/delete", { path: TWO });
    expect(toastErrorMock).toHaveBeenCalledWith(expect.stringContaining("已删除 1/2"));

    const updater = setSelectedPaths.mock.calls
      .map((call) => call[0])
      .find((value) => typeof value === "function") as ((prev: Set<string>) => Set<string>) | undefined;
    expect(updater).toBeTruthy();
    expect(updater!(new Set([ONE, TWO]))).toEqual(new Set([TWO]));
  });

  it("does not refresh when every deletion fails", async () => {
    apiPostMock.mockRejectedValue(new Error("denied"));
    const onRefresh = vi.fn();
    const { container } = render(
      <FileTree
        tree={tree}
        searchQuery=""
        expanded={new Set([ROOT])}
        setExpanded={vi.fn()}
        selectedPaths={new Set([ONE, TWO])}
        setSelectedPaths={vi.fn()}
        rootPath={ROOT}
        onRefresh={onRefresh}
        onLoadNodeChildren={vi.fn().mockResolvedValue(undefined)}
        loadingDirPaths={new Set()}
        dirErrors={new Map()}
        onMatchCountChange={vi.fn()}
      />,
    );

    const row = container.querySelector(`.explorer-tree-node[data-path="${ONE}"]`)!;
    fireEvent.contextMenu(row, { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("button", { name: "删除选中 (2)" }));

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalled());
    expect(onRefresh).not.toHaveBeenCalled();
  });
});
