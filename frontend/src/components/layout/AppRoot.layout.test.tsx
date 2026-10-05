import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { AppRoot } from "./AppRoot";

const { showInfoMock } = vi.hoisted(() => ({ showInfoMock: vi.fn() }));

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({}),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  fetchBigChunk: vi.fn(),
}));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => ({ showInfo: showInfoMock, showError: vi.fn() }) },
}));
vi.mock("./IconNav", () => ({ IconNav: () => null }));
vi.mock("./LeftPanel", () => ({ LeftPanel: () => <div className="left-panel-wrap" /> }));
vi.mock("../topbar/TopBar", () => ({ TopBar: () => null }));
vi.mock("../chat/ChatArea", () => ({ ChatArea: () => null }));
vi.mock("../input/InputBar", () => ({ InputBar: () => <div data-testid="input-bar" /> }));
vi.mock("./RightPanel", () => ({ RightPanel: () => null }));
vi.mock("../explorer", () => ({ ExplorerPanel: () => null }));
vi.mock("../settings/SettingsPage", () => ({ SettingsPage: () => null }));
vi.mock("../knowledge/KnowledgePanel", () => ({ KnowledgePanel: () => null }));
vi.mock("../bookmarks/BookmarkPanel", () => ({ BookmarkPanel: () => null }));
vi.mock("../shared/StatsPanel", () => ({ StatsPanel: () => null }));
vi.mock("../shared/SearchModal", () => ({ SearchModal: () => null }));
vi.mock("../shared/SnapshotPanel", () => ({ SnapshotPanel: () => null }));
vi.mock("../shared/Modal", () => ({ ModalContainer: () => null }));
vi.mock("../shared/Toast", () => ({ ToastContainer: () => null }));
vi.mock("../chat/ShortcutHelp", () => ({ ShortcutHelp: () => null }));

const openFile = {
  id: "layout-file",
  path: "C:\\fixture\\draft.py",
  name: "draft.py",
  content: "unsaved file draft",
  snapshot: "saved file version",
  dirty: true,
};

function setViewportWidth(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
}

function setElementWidth(element: HTMLElement, width: number) {
  Object.defineProperty(element, "offsetWidth", { configurable: true, value: width });
}

function resetLayoutState(state: Partial<ReturnType<typeof useAppStore.getState>> = {}) {
  localStorage.clear();
  useAppStore.setState({
    activeNav: "chat",
    leftPanelOpen: true,
    rightPanelOpen: true,
    leftPanelWidth: 500,
    rightPanelWidth: 560,
    explorerOpen: true,
    explorerWidth: 450,
    inputBarWidth: 720,
    openFiles: [openFile],
    activeFileId: openFile.id,
    ...state,
  });
  useSessionStore.setState({ initialized: false, sessions: [] });
  useChatStore.setState({
    activeSessionId: "layout-session",
    drafts: { "layout-session": "unsent chat draft" },
  });
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});

beforeEach(() => {
  setViewportWidth(1536);
  showInfoMock.mockReset();
  resetLayoutState();
});

describe("AppRoot responsive layout", () => {
  it("folds lower-priority panes at 1024px and preserves drafts and width preferences", () => {
    setViewportWidth(1024);
    resetLayoutState();

    render(<AppRoot />);

    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: false,
      explorerOpen: true,
      leftPanelWidth: 500,
      rightPanelWidth: 560,
      explorerWidth: 450,
      openFiles: [openFile],
      activeFileId: openFile.id,
    });
    expect(useChatStore.getState().drafts["layout-session"]).toBe("unsent chat draft");
    expect(showInfoMock).toHaveBeenCalledTimes(1);
    expect(String(showInfoMock.mock.calls[0][0])).toMatch(/空间不足|space/i);

    act(() => window.dispatchEvent(new Event("resize")));
    expect(showInfoMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the just-opened right panel and folds other panes first", () => {
    setViewportWidth(1024);
    resetLayoutState({ leftPanelOpen: true, rightPanelOpen: false, explorerOpen: true });
    render(<AppRoot />);

    fireEvent.click(document.getElementById("rightToggleBtn")!);

    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: true,
      explorerOpen: false,
    });
    expect(showInfoMock).toHaveBeenCalledTimes(1);

    setViewportWidth(1536);
    act(() => window.dispatchEvent(new Event("resize")));
    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: true,
      explorerOpen: false,
    });
  });

  it("keeps the just-opened left panel and only folds the right panel when needed", () => {
    setViewportWidth(1280);
    resetLayoutState({ leftPanelOpen: false, rightPanelOpen: true, explorerOpen: true });
    render(<AppRoot />);

    fireEvent.click(document.getElementById("leftToggleBtn")!);

    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: true,
      rightPanelOpen: false,
      explorerOpen: true,
    });
    expect(showInfoMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the just-opened Explorer and folds the session list first", () => {
    setViewportWidth(1280);
    resetLayoutState({ leftPanelOpen: true, rightPanelOpen: true, explorerOpen: false });
    render(<AppRoot />);

    fireEvent.click(document.getElementById("explorerToggleBtn")!);

    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: true,
      explorerOpen: true,
    });
    expect(showInfoMock).toHaveBeenCalledTimes(1);
  });

  it("re-budgets on window narrowing without changing preferred widths or dirty drafts", () => {
    setViewportWidth(1536);
    resetLayoutState();
    render(<AppRoot />);
    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: true,
      rightPanelOpen: true,
      explorerOpen: true,
    });

    setViewportWidth(1024);
    act(() => window.dispatchEvent(new Event("resize")));

    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: false,
      explorerOpen: true,
      leftPanelWidth: 500,
      rightPanelWidth: 560,
      explorerWidth: 450,
      openFiles: [openFile],
      activeFileId: openFile.id,
    });
    expect(useChatStore.getState().drafts["layout-session"]).toBe("unsent chat draft");
    expect(showInfoMock).toHaveBeenCalledTimes(1);

    setViewportWidth(1536);
    act(() => window.dispatchEvent(new Event("resize")));
    expect(useAppStore.getState()).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: false,
      explorerOpen: true,
      leftPanelWidth: 500,
      rightPanelWidth: 560,
      explorerWidth: 450,
    });
    expect(showInfoMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      panel: "left",
      handle: "#sidebarResizer",
      parentWidth: 1000,
      startX: 100,
      endX: 1000,
      widthKey: "leftPanelWidth" as const,
      expectedWidth: 396,
      state: { leftPanelOpen: true, rightPanelOpen: false, explorerOpen: false },
    },
    {
      panel: "right",
      handle: "#chatResizer",
      parentWidth: 1000,
      startX: 900,
      endX: 0,
      widthKey: "rightPanelWidth" as const,
      expectedWidth: 462,
      state: { leftPanelOpen: false, rightPanelOpen: true, explorerOpen: false },
    },
    {
      panel: "Explorer",
      handle: "#explorerResizer",
      parentWidth: 1000,
      startX: 900,
      endX: 0,
      widthKey: "explorerWidth" as const,
      expectedWidth: 486,
      state: { leftPanelOpen: false, rightPanelOpen: false, explorerOpen: true },
    },
    {
      panel: "input bar",
      handle: ".inputbar-resizer",
      parentWidth: 600,
      startX: 600,
      endX: 0,
      widthKey: "inputBarWidth" as const,
      expectedWidth: 588,
      state: { leftPanelOpen: false, rightPanelOpen: false, explorerOpen: false },
    },
  ])("caps the $panel drag to the currently visible layout budget", ({ handle, parentWidth, startX, endX, widthKey, expectedWidth, state }) => {
    setViewportWidth(1536);
    resetLayoutState(state);
    render(<AppRoot />);

    const resizeHandle = document.querySelector<HTMLElement>(handle)!;
    setElementWidth(resizeHandle.parentElement!, parentWidth);
    fireEvent.mouseDown(resizeHandle, { clientX: startX });
    fireEvent.mouseMove(document, { clientX: endX });

    expect(useAppStore.getState()[widthKey]).toBe(expectedWidth);
    fireEvent.mouseUp(document);
  });
});
