import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "./treeUtils";
import { useAppStore } from "../../stores/useAppStore";
import { FileTree } from "./FileTree";

const { apiPostMock, toastErrorMock } = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  toastErrorMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({ apiGet: vi.fn(), apiPost: apiPostMock }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: {
    getState: () => ({ showError: toastErrorMock, showSuccess: vi.fn(), showInfo: vi.fn() }),
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
const HIDDEN_FILE = "C:\\synthetic\\root\\src\\.hidden-dir\\inside.txt";

const tree: FileNode = {
  name: "root",
  type: "directory",
  path: ROOT,
  lazy: false,
  children: [
    { name: "src", type: "directory", path: `${ROOT}/src`, lazy: true },
    { name: "readme.md", type: "file", path: `${ROOT}/readme.md` },
  ],
};

function renderTree(searchQuery: string, onMatchCountChange = vi.fn()) {
  return {
    onMatchCountChange,
    ...render(
      <FileTree
        tree={tree}
        searchQuery={searchQuery}
        expanded={new Set()}
        setExpanded={vi.fn()}
        selectedPaths={new Set()}
        setSelectedPaths={vi.fn()}
        rootPath={ROOT}
        onRefresh={vi.fn()}
        onLoadNodeChildren={vi.fn().mockResolvedValue(undefined)}
        loadingDirPaths={new Set()}
        dirErrors={new Map()}
        onMatchCountChange={onMatchCountChange}
      />,
    ),
  };
}

const hit = {
  path: HIDDEN_FILE,
  name: "inside.txt",
  line_number: 3,
  line_text: "inside content",
  match_count: 1,
};

describe("FileTree project search", () => {
  beforeEach(() => {
    localStorage.clear();
    apiPostMock.mockReset();
    toastErrorMock.mockReset();
    useAppStore.setState({ openFiles: [], activeFileId: null, pinnedFileIds: [] });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("queries the backend for matches outside the loaded tree and reports the combined count", async () => {
    apiPostMock.mockResolvedValue({ results: [hit] });
    const { onMatchCountChange } = renderTree("inside");

    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith(
        "explorer/search",
        { root_path: ROOT, query: "inside", max_results: 100 },
        35_000,
      ),
    );
    expect(await screen.findByText("src/.hidden-dir/inside.txt")).toBeTruthy();
    // "inside" only exists under the unloaded src directory, so the in-memory
    // name filter finds nothing; the project search still yields one file.
    expect(screen.getByText("已加载目录中无名称匹配")).toBeTruthy();
    await waitFor(() => expect(onMatchCountChange).toHaveBeenLastCalledWith(1));
  });

  it("shows a loading state until the project search resolves", async () => {
    let resolveSearch!: (value: unknown) => void;
    apiPostMock.mockImplementation(
      () => new Promise((resolve) => { resolveSearch = resolve; }),
    );
    renderTree("inside");

    expect(screen.getByText("正在搜索项目内容…")).toBeTruthy();
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledTimes(1));
    expect(screen.getByText("正在搜索项目内容…")).toBeTruthy();

    await act(async () => {
      resolveSearch({ results: [] });
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByText("未找到匹配结果")).toBeTruthy());
  });

  it("shows an error with a retry that reissues the search", async () => {
    apiPostMock
      .mockRejectedValueOnce(new Error("search backend down"))
      .mockResolvedValueOnce({ results: [] });
    renderTree("inside");

    expect(await screen.findByText(/搜索失败: search backend down/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(apiPostMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText("未找到匹配结果")).toBeTruthy());
  });

  it("opens a content match from an unloaded directory when clicked", async () => {
    apiPostMock.mockResolvedValue({ results: [hit] });
    const openFileSpy = vi.fn();
    useAppStore.setState({ openFile: openFileSpy });
    renderTree("inside");

    fireEvent.click(await screen.findByText("src/.hidden-dir/inside.txt"));
    expect(openFileSpy).toHaveBeenCalledWith(HIDDEN_FILE);
  });

  it("does not call the backend for an empty query", async () => {
    renderTree("");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
    });
    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
