import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FileNode } from "./treeUtils";
import type { ExplorerReadResponse } from "../../types/api";
import { useAppStore } from "../../stores/useAppStore";
import { FileTreeNode } from "./FileTreeNode";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: toastMock, showInfo: vi.fn() }) },
}));
vi.mock("../../stores/useModalStore", () => ({
  useModalStore: { getState: () => ({ promptDialog: vi.fn(), confirmDialog: vi.fn() }) },
}));
vi.mock("../../i18n", () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }));

const fixturePath = "C:/synthetic/workspace/example.py";
const fileNode: FileNode = {
  name: "example.py",
  type: "file",
  path: fixturePath,
};

function readResult(): ExplorerReadResponse {
  return {
    name: fileNode.name,
    path: fixturePath,
    content: "print('UAT')",
    is_text: true,
    version: "fixture-version",
  };
}

function renderFileNode() {
  return render(
    <FileTreeNode
      node={fileNode}
      depth={0}
      expanded={new Set()}
      selectedPaths={new Set()}
      renaming={null}
      creating={null}
      dragOverPath={null}
      siblings={[fileNode]}
      newItemPlaceholder={{ file: "New file", folder: "New folder" }}
      onToggle={vi.fn()}
      onSelect={vi.fn()}
      onContextMenu={vi.fn()}
      onDragStart={vi.fn()}
      onDragEnd={vi.fn()}
      onDragOver={vi.fn()}
      onDragLeave={vi.fn()}
      onDrop={vi.fn()}
      onFinishRename={vi.fn()}
      onFinishCreate={vi.fn()}
    />,
  );
}

describe("FileTreeNode repeated file opening", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    toastMock.mockReset();
    useAppStore.setState({ openFiles: [], activeFileId: null, pinnedFileIds: [] });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("coalesces the two click events and trailing dblclick from one native double-click", async () => {
    const resolvers: Array<(value: ExplorerReadResponse) => void> = [];
    apiGetMock.mockImplementation(
      () => new Promise<ExplorerReadResponse>((resolve) => resolvers.push(resolve)),
    );
    const { container } = renderFileNode();
    const row = container.querySelector<HTMLElement>(".explorer-tree-node[data-path]")!;

    fireEvent.click(row, { detail: 1 });
    fireEvent.click(row, { detail: 2 });
    fireEvent.doubleClick(row, { detail: 2 });
    const readRequestCount = apiGetMock.mock.calls.length;

    await act(async () => {
      resolvers.forEach((resolve) => resolve(readResult()));
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(useAppStore.getState().openFiles.filter((file) => file.path === fixturePath)).toHaveLength(1);
    });

    const opened = useAppStore.getState().openFiles.filter((file) => file.path === fixturePath);
    expect({
      readRequestCount,
      tabCount: opened.length,
      activeFileId: useAppStore.getState().activeFileId,
    }).toEqual({
      readRequestCount: 1,
      tabCount: 1,
      activeFileId: fixturePath,
    });
  });
});
