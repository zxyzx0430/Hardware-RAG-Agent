import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpenFileItem } from "../../types";
import type { ExplorerReadResponse } from "../../types/api";
import type { AppState } from "./types";
import { createExplorerActions } from "./explorerActions";

const { apiGetMock, apiPostMock, toastMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({ apiGet: apiGetMock, apiPost: apiPostMock }));
vi.mock("../useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: toastMock, showInfo: vi.fn() }) },
}));
vi.mock("../useModalStore", () => ({
  useModalStore: { getState: () => ({ promptDialog: vi.fn(), confirmDialog: vi.fn() }) },
}));
vi.mock("../../i18n", () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }));

const fixturePath = "C:/synthetic/workspace/example.py";

function readResult(path: string, content: string): ExplorerReadResponse {
  return {
    name: path.split(/[\\/]/).pop() ?? path,
    path,
    content,
    is_text: true,
    version: "fixture-version",
  };
}

function createHarness(files: OpenFileItem[] = []) {
  let state = {
    openFiles: files,
    activeFileId: files[0]?.id ?? null,
    pinnedFileIds: [],
  } as unknown as AppState;
  const set = (update: (value: AppState) => Partial<AppState> | AppState) => {
    state = { ...state, ...update(state) };
  };
  const get = () => state;
  return { get, actions: createExplorerActions(set, get) };
}

describe("Explorer open-file concurrency", () => {
  beforeEach(() => {
    localStorage.clear();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    toastMock.mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

  it("creates one tab when two rapid clicks open the same path during an unresolved read", async () => {
    const resolvers: Array<(value: ExplorerReadResponse) => void> = [];
    apiGetMock.mockImplementation(
      () => new Promise<ExplorerReadResponse>((resolve) => resolvers.push(resolve)),
    );
    const harness = createHarness();

    // FileTreeNode opens files on every click; a native double-click emits two clicks.
    const firstClickOpen = harness.actions.openFile(fixturePath);
    const secondClickOpen = harness.actions.openFile(fixturePath);
    const readRequestCount = apiGetMock.mock.calls.length;
    resolvers.forEach((resolve) => resolve(readResult(fixturePath, "print('UAT')")));

    await Promise.all([firstClickOpen, secondClickOpen]);

    const opened = harness.get().openFiles.filter((file) => file.path === fixturePath);
    expect({
      readRequestCount,
      tabCount: opened.length,
      activeFileId: harness.get().activeFileId,
    }).toEqual({
      readRequestCount: 1,
      tabCount: 1,
      activeFileId: fixturePath,
    });
  });

  it("opens two different paths independently", async () => {
    const secondPath = "C:/synthetic/workspace/notes.txt";
    apiGetMock
      .mockResolvedValueOnce(readResult(fixturePath, "print('UAT')"))
      .mockResolvedValueOnce(readResult(secondPath, "UAT notes"));
    const harness = createHarness();

    await Promise.all([
      harness.actions.openFile(fixturePath),
      harness.actions.openFile(secondPath),
    ]);

    expect(apiGetMock).toHaveBeenCalledTimes(2);
    expect(harness.get().openFiles.map((file) => file.path).sort()).toEqual(
      [fixturePath, secondPath].sort(),
    );
  });

  it("activates an already-open dirty draft without rereading or replacing it", async () => {
    const dirtyFile: OpenFileItem = {
      id: fixturePath,
      path: fixturePath,
      name: "example.py",
      content: "unsaved draft",
      snapshot: "saved content",
      dirty: true,
      pinned: false,
    };
    const harness = createHarness([dirtyFile]);
    harness.actions.setActiveFile(null);

    await harness.actions.openFile(fixturePath);

    expect(apiGetMock).not.toHaveBeenCalled();
    expect(harness.get().openFiles).toEqual([dirtyFile]);
    expect(harness.get().activeFileId).toBe(fixturePath);
  });

  it("does not create an empty tab after a failed shared read and permits a later retry", async () => {
    apiGetMock
      .mockRejectedValueOnce(new Error("synthetic read failure"))
      .mockResolvedValueOnce(readResult(fixturePath, "print('UAT retry')"));
    const harness = createHarness();

    await Promise.all([
      harness.actions.openFile(fixturePath),
      harness.actions.openFile(fixturePath),
    ]);

    expect(apiGetMock).toHaveBeenCalledTimes(1);
    expect(harness.get().openFiles).toEqual([]);
    expect(harness.get().activeFileId).toBeNull();
    expect(toastMock).toHaveBeenCalledTimes(1);

    await harness.actions.openFile(fixturePath);

    expect(apiGetMock).toHaveBeenCalledTimes(2);
    expect(harness.get().openFiles.filter((file) => file.path === fixturePath)).toHaveLength(1);
  });
});
