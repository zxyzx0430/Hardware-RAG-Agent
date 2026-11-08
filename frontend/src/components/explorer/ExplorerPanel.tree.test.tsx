import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet } from "../../api/client";
import { useAppStore } from "../../stores/useAppStore";
import { useToastStore } from "../../stores/useToastStore";
import { ExplorerPanel } from "./ExplorerPanel";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("./useExplorerWatch", () => ({ useExplorerWatch: vi.fn() }));
vi.mock("./FolderPickerDialog", () => ({ FolderPickerDialog: () => null }));

const get = vi.mocked(apiGet);

beforeEach(() => {
  localStorage.clear();
  useToastStore.setState({ toasts: [] });
  useAppStore.setState({
    explorerOpen: true,
    explorerRootPath: "C:\\workspace\\project",
    recentFolders: [],
    openFiles: [],
    activeFileId: null,
    pinnedFileIds: [],
    editorShowTree: true,
  });
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  useToastStore.setState({ toasts: [] });
});

describe("ExplorerPanel path normalization", () => {
  it("normalizes the persisted root and child paths, and expands the root", async () => {
    get.mockResolvedValue({
      children: [
        { name: "a.txt", type: "file", path: "C:\\workspace\\project\\a.txt" },
      ],
    });

    render(<ExplorerPanel />);

    await waitFor(() => {
      expect(document.querySelector('[data-path="C:/workspace/project"]')).toBeTruthy();
    });
    const root = document.querySelector('[data-path="C:/workspace/project"]')!;
    expect(root.classList.contains("is-expanded")).toBe(true);
    expect(document.querySelector('[data-path="C:/workspace/project/a.txt"]')).toBeTruthy();
  });

  it("shows the unauthorized placeholder without a startup toast", async () => {
    get.mockRejectedValue(
      new Error("security check failed: directory has not been explicitly opened"),
    );

    render(<ExplorerPanel />);

    await waitFor(() => {
      expect(screen.getByText("该项目尚未授权，请明确重新打开")).toBeTruthy();
    });
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});

describe("ExplorerPanel refresh", () => {
  it("reloads children of an expanded directory on refresh", async () => {
    let docsVersion = 1;
    get.mockImplementation(async (url: string) => {
      const decoded = decodeURIComponent(url);
      if (decoded.includes("/docs")) {
        return {
          children: [
            {
              name: docsVersion === 1 ? "old.txt" : "new.txt",
              type: "file",
              path: `C:/workspace/project/docs/${docsVersion === 1 ? "old.txt" : "new.txt"}`,
            },
          ],
        };
      }
      return {
        children: [
          { name: "docs", type: "directory", path: "C:/workspace/project/docs", lazy: true },
          { name: "a.txt", type: "file", path: "C:/workspace/project/a.txt" },
        ],
      };
    });

    render(<ExplorerPanel />);
    await waitFor(() => {
      expect(document.querySelector('[data-path="C:/workspace/project/docs"]')).toBeTruthy();
    });

    fireEvent.click(document.querySelector('[data-path="C:/workspace/project/docs"]')!);
    await waitFor(() => {
      expect(document.querySelector('[data-path="C:/workspace/project/docs/old.txt"]')).toBeTruthy();
    });

    docsVersion = 2;
    fireEvent.click(screen.getByTitle("刷新文件树"));

    await waitFor(() => {
      expect(document.querySelector('[data-path="C:/workspace/project/docs/new.txt"]')).toBeTruthy();
    });
    expect(document.querySelector('[data-path="C:/workspace/project/docs/old.txt"]')).toBeNull();
  });
});
