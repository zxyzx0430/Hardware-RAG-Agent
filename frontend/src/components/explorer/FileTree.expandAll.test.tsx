import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "./treeUtils";
import { useAppStore } from "../../stores/useAppStore";
import { FileTree } from "./FileTree";

vi.mock("../../api/client", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: {
    getState: () => ({ showError: vi.fn(), showSuccess: vi.fn(), showInfo: vi.fn() }),
  },
}));
vi.mock("../../stores/useModalStore", () => ({
  useModalStore: { getState: () => ({ promptDialog: vi.fn(), confirmDialog: vi.fn() }) },
}));
vi.mock("../../i18n", () => ({
  t: (_key: string, fallback?: string) => fallback ?? _key,
  useI18n: () => ({ t: (_key: string, fallback?: string) => fallback ?? _key, lang: "zh" }),
}));

const ROOT = "C:/synthetic/root";

function makeTree(): FileNode {
  return {
    name: "root",
    type: "directory",
    path: ROOT,
    lazy: false,
    children: [
      { name: "a", type: "directory", path: `${ROOT}/a`, lazy: true },
      { name: "b", type: "directory", path: `${ROOT}/b`, lazy: true },
      { name: "c", type: "directory", path: `${ROOT}/c`, lazy: true },
    ],
  };
}

function renderTree(options: {
  expanded: Set<string>;
  tree?: FileNode;
  dirErrors?: Map<string, string>;
  onLoadNodeChildren?: (path: string) => Promise<void>;
}) {
  const onLoadNodeChildren = options.onLoadNodeChildren ?? vi.fn().mockResolvedValue(undefined);
  const view = render(
    <FileTree
      tree={options.tree ?? makeTree()}
      searchQuery=""
      expanded={options.expanded}
      setExpanded={vi.fn()}
      selectedPaths={new Set()}
      setSelectedPaths={vi.fn()}
      rootPath={ROOT}
      onRefresh={vi.fn()}
      onLoadNodeChildren={onLoadNodeChildren}
      loadingDirPaths={new Set()}
      dirErrors={options.dirErrors ?? new Map()}
      onMatchCountChange={vi.fn()}
    />,
  );
  return { ...view, onLoadNodeChildren };
}

describe("FileTree expand-all lazy loading", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ openFiles: [], activeFileId: null, pinnedFileIds: [] });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("loads expanded-but-unloaded lazy directories without a second click", async () => {
    const { onLoadNodeChildren } = renderTree({
      expanded: new Set([ROOT, `${ROOT}/a`]),
    });

    await waitFor(() => expect(onLoadNodeChildren).toHaveBeenCalledWith(`${ROOT}/a`));
    expect(onLoadNodeChildren).not.toHaveBeenCalledWith(`${ROOT}/b`);
    expect(onLoadNodeChildren).not.toHaveBeenCalledWith(`${ROOT}/c`);
  });

  it("does not auto-retry directories that already failed to load", async () => {
    const { onLoadNodeChildren } = renderTree({
      expanded: new Set([ROOT, `${ROOT}/a`]),
      dirErrors: new Map([[`${ROOT}/a`, "permission denied"]]),
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(onLoadNodeChildren).not.toHaveBeenCalled();
  });

  it("stops requesting a directory once its children are loaded", async () => {
    const { onLoadNodeChildren, rerender } = renderTree({
      expanded: new Set([ROOT, `${ROOT}/a`]),
    });
    await waitFor(() => expect(onLoadNodeChildren).toHaveBeenCalledTimes(1));

    const loadedTree = makeTree();
    loadedTree.children![0] = {
      name: "a",
      type: "directory",
      path: `${ROOT}/a`,
      lazy: false,
      children: [{ name: "file.txt", type: "file", path: `${ROOT}/a/file.txt` }],
    };
    rerender(
      <FileTree
        tree={loadedTree}
        searchQuery=""
        expanded={new Set([ROOT, `${ROOT}/a`])}
        setExpanded={vi.fn()}
        selectedPaths={new Set()}
        setSelectedPaths={vi.fn()}
        rootPath={ROOT}
        onRefresh={vi.fn()}
        onLoadNodeChildren={onLoadNodeChildren}
        loadingDirPaths={new Set()}
        dirErrors={new Map()}
        onMatchCountChange={vi.fn()}
      />,
    );

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(onLoadNodeChildren).toHaveBeenCalledTimes(1);
  });
});
