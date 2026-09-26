import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpenFileItem } from "../../types";
import type { AppState } from "./types";
import { saveFileVersion } from "./persistence";
import { createExplorerActions } from "./explorerActions";

const { apiPostMock, apiGetMock, toastMock } = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  apiGetMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({ apiPost: apiPostMock, apiGet: apiGetMock }));
vi.mock("../useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: toastMock, showInfo: vi.fn() }) },
}));
vi.mock("../useModalStore", () => ({
  useModalStore: { getState: () => ({ promptDialog: vi.fn(), confirmDialog: vi.fn() }) },
}));
vi.mock("../../i18n", () => ({ t: (_key: string, fallback?: string) => fallback ?? _key }));

const initialFile: OpenFileItem = {
  id: "C:/workspace/main.py",
  path: "C:/workspace/main.py",
  name: "main.py",
  content: "first draft",
  snapshot: "on disk",
  dirty: true,
  pinned: false,
};

function createHarness(files: OpenFileItem[] = [initialFile]) {
  let state = {
    openFiles: files,
    activeFileId: files[0]?.id ?? null,
    pinnedFileIds: [] as string[],
  } as unknown as AppState;
  const set = (update: (value: AppState) => Partial<AppState> | AppState) => {
    state = { ...state, ...update(state) };
  };
  const get = () => state;
  return { get, actions: createExplorerActions(set, get) };
}

describe("Explorer save concurrency", () => {
  beforeEach(() => {
    localStorage.clear();
    apiPostMock.mockReset();
    apiGetMock.mockReset();
    toastMock.mockReset();
    saveFileVersion(initialFile.path, "version-1");
  });

  afterEach(() => vi.restoreAllMocks());

  it("keeps typing made while a save is pending marked dirty", async () => {
    let finishSave: ((value: { version: string }) => void) | undefined;
    apiPostMock.mockImplementation(
      () => new Promise((resolve) => { finishSave = resolve; }),
    );
    const harness = createHarness();
    const actions = harness.actions;

    const saving = actions.saveFile(initialFile.id);
    actions.setFileContent(initialFile.id, "typed during save");
    actions.markFileDirty(initialFile.id, true);
    finishSave?.({ version: "version-2" });

    expect(await saving).toBe(true);
    expect(apiPostMock).toHaveBeenCalledWith("explorer/write", {
      path: initialFile.path,
      content: "first draft",
      expected_version: "version-1",
    });
    expect(harness.get().openFiles[0]).toMatchObject({
      content: "typed during save",
      snapshot: "first draft",
      dirty: true,
    });
  });

  it("preserves the local draft when the server reports an external-change conflict", async () => {
    apiPostMock.mockRejectedValue(new Error("file changed since it was read"));
    const harness = createHarness();
    const actions = harness.actions;

    expect(await actions.saveFile(initialFile.id)).toBe(false);
    expect(harness.get().openFiles[0]).toMatchObject({
      content: "first draft",
      snapshot: "on disk",
      dirty: true,
    });
    expect(toastMock).toHaveBeenCalled();
  });
});
