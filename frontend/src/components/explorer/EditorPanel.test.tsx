import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../../stores/useAppStore";
import { EditorPanel } from "./EditorPanel";

const monacoMock = vi.hoisted(() => ({
  command: null as null | (() => void),
}));

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({}),
  apiPost: vi.fn().mockResolvedValue({}),
  AUTH_TOKEN_CHANGED_EVENT: "hwrag:auth-token-changed",
  getSessionToken: () => null,
}));

vi.mock("@monaco-editor/react", async () => {
  const React = await import("react");
  const MockMonaco = (props: { onMount?: (editor: unknown, monaco: unknown) => void }) => {
    const { onMount } = props;
    React.useEffect(() => {
      onMount?.(
        {
          addCommand: (_keys: number, callback: () => void) => {
            monacoMock.command = callback;
          },
        },
        {
          KeyMod: { CtrlCmd: 1 },
          KeyCode: { KeyS: 2 },
        },
      );
    }, [onMount]);
    return React.createElement("div", { "data-testid": "monaco" });
  };
  return { default: MockMonaco };
});

const file = (id: string) => ({
  id,
  path: `C:/project/${id}.txt`,
  name: `${id}.txt`,
  content: "content",
  snapshot: "content",
  dirty: true,
  pinned: false,
  is_text: true,
});

beforeEach(() => {
  monacoMock.command = null;
  useAppStore.setState({
    openFiles: [file("a")],
    activeFileId: "a",
    pinnedFileIds: [],
    explorerRootPath: "C:/project",
    editorShowTree: true,
    themeMode: "light",
  });
});

afterEach(() => {
  cleanup();
});

describe("EditorPanel save shortcut", () => {
  it("registers Ctrl/Cmd+S on the Monaco editor and saves the active file", async () => {
    const saveFile = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ saveFile });

    render(<EditorPanel />);

    await waitFor(() => expect(monacoMock.command).not.toBeNull());
    act(() => {
      monacoMock.command?.();
    });
    await waitFor(() => expect(saveFile).toHaveBeenCalledWith("a"));
  });
});

describe("EditorPanel tab visibility", () => {
  it("scrolls the active tab into view when several tabs are open", async () => {
    useAppStore.setState({
      openFiles: [file("a"), file("b"), file("c"), file("d")],
      activeFileId: "d",
    });
    const scrollIntoView = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollIntoView;

    try {
      render(<EditorPanel />);
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
      const active = document.querySelector(".editor-tab.active");
      expect(active?.textContent).toContain("d.txt");
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});
