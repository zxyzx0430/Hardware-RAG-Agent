import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { act, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet } from "../../api/client";
import { usePanelResize } from "../../hooks/usePanelResize";
import { useAppStore } from "../../stores/useAppStore";
import {
  CHAT_MIN_WIDTH,
  EXPLORER_MIN_WIDTH,
  EXPLORER_OPEN_KEY,
  EXPLORER_WIDTH_KEY,
  RIGHT_PANEL_MIN_WIDTH,
} from "../../stores/appStore/persistence";
import { ExplorerPanel } from "./ExplorerPanel";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({ children: [] }),
  apiPost: vi.fn().mockResolvedValue({}),
}));

vi.mock("./useExplorerWatch", () => ({ useExplorerWatch: vi.fn() }));

function ExplorerResizeHarness() {
  const [width, setWidth] = useState(280);
  const resize = usePanelResize(width, "right", EXPLORER_MIN_WIDTH, 600, setWidth);

  return (
    <div>
      <button type="button" onMouseDown={resize.onMouseDown}>Drag divider</button>
      <output aria-label="Explorer width">{width}</output>
    </div>
  );
}

describe("Explorer panel layout", () => {
  beforeEach(() => {
    localStorage.clear();
    act(() => {
      useAppStore.setState({
        explorerOpen: true,
        explorerWidth: 280,
        explorerRootPath: null,
        recentFolders: [],
        openFiles: [],
        editorShowTree: true,
      });
    });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("allows the Explorer divider to reach its 220px minimum", () => {
    render(<ExplorerResizeHarness />);

    fireEvent.mouseDown(screen.getByRole("button", { name: "Drag divider" }), { clientX: 300 });
    fireEvent.mouseMove(document, { clientX: 600 });

    expect(EXPLORER_MIN_WIDTH).toBe(220);
    expect(CHAT_MIN_WIDTH).toBe(400);
    expect(RIGHT_PANEL_MIN_WIDTH).toBe(340);
    expect(screen.getByLabelText("Explorer width").textContent).toBe("220");
    fireEvent.mouseUp(document);
  });

  it("keeps the minimum wide enough for all five compact header actions", () => {
    // 5 icon buttons + 4 gaps + header padding + the panel border.
    const requiredWidth = 5 * 32 + 4 * 4 + 16 + 1;

    expect(EXPLORER_MIN_WIDTH).toBeGreaterThanOrEqual(requiredWidth);
  });

  it("keeps every header action available after a project is opened", async () => {
    act(() => useAppStore.setState({ explorerRootPath: "C:\\workspace\\project" }));
    await act(async () => {
      render(<ExplorerPanel />);
      await Promise.resolve();
    });

    expect(document.querySelectorAll(".explorer-header-actions button")).toHaveLength(5);
    expect(screen.getByRole("button", { name: "跟随模式" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "最近" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开文件夹" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "已删除" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "切换资源管理器" })).toBeTruthy();

    expect(vi.mocked(apiGet)).toHaveBeenCalledWith(
      expect.stringContaining("explorer/dir?path="),
    );
  });

  it("collapses from the accessible header button and restores the previous width", () => {
    act(() => useAppStore.getState().setExplorerWidth(248));
    render(<ExplorerPanel />);

    const collapseButton = screen.getByRole("button", { name: "切换资源管理器" });
    expect(collapseButton.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(collapseButton);
    expect(useAppStore.getState().explorerOpen).toBe(false);
    expect(collapseButton.getAttribute("aria-expanded")).toBe("false");
    expect(localStorage.getItem(EXPLORER_OPEN_KEY)).toBe("false");

    act(() => useAppStore.getState().setExplorerOpen(true));
    expect(useAppStore.getState().explorerOpen).toBe(true);
    expect(useAppStore.getState().explorerWidth).toBe(248);
    expect(localStorage.getItem(EXPLORER_WIDTH_KEY)).toBe("248");
  });
});
