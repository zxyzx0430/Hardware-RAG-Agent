import type { OpenFileItem } from "../../types";
import { apiGet, apiPost } from "../../api/client";
import { useToastStore } from "../useToastStore";
import { useModalStore } from "../useModalStore";
import { t } from "../../i18n";
import {
  saveBoolean,
  saveNumber,
  saveStringArray,
  saveOpenFiles,
  saveActiveFileId,
  saveExplorerRootPath,
  clearExplorerRootPath,
  EXPLORER_OPEN_KEY,
  EXPLORER_WIDTH_KEY,
  RECENT_FOLDERS_KEY,
  MAX_RECENT_FOLDERS,
} from "./persistence";
import type { AppState } from "./types";

export interface ExplorerActions {
  setExplorerOpen: (o: boolean) => void;
  setExplorerWidth: (w: number) => void;
  setExplorerRootPath: (p: string | null) => void;
  openFile: (path: string) => Promise<void>;
  closeFile: (id: string, action?: "save" | "discard" | "cancel") => Promise<boolean>;
  setActiveFile: (id: string | null) => void;
  pinFile: (id: string) => void;
  unpinFile: (id: string) => void;
  markFileDirty: (id: string, dirty: boolean) => void;
  setFileContent: (id: string, content: string) => void;
  saveFile: (id: string) => Promise<boolean>;
  openCodeBuffer: (opts: { name: string; content: string; language?: string }) => void;
  saveBufferAsFile: (id: string, targetPath: string) => Promise<boolean>;
  handleExternalFileChange: (path: string) => Promise<void>;
  toggleFollowMode: () => void;
  addRecentFolder: (path: string) => void;
  setDiffViewOpen: (open: boolean) => void;
  openDiffForFile: (id: string) => void;
  closeDiffView: () => void;
}

interface ReadFileResponse {
  name: string;
  path: string;
  content?: string;
  is_text?: boolean;
  data_url?: string;
}

type SetFn = (fn: (state: AppState) => Partial<AppState> | AppState) => void;
type GetFn = () => AppState;

function fileNameFromPath(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function buildOpenFileItem(data: ReadFileResponse): OpenFileItem {
  return {
    id: data.path,
    path: data.path,
    name: data.name || fileNameFromPath(data.path),
    content: data.content ?? "",
    snapshot: data.content,
    dirty: false,
    pinned: false,
    is_text: data.is_text ?? true,
    data_url: data.data_url,
  };
}

function showToastError(message: string): void {
  useToastStore.getState().showError(message);
}

export function createExplorerActions(set: SetFn, get: GetFn): ExplorerActions {
  const saveFileImpl = async (id: string): Promise<boolean> => {
    const file = get().openFiles.find((f) => f.id === id);
    if (!file || !file.dirty) return true;

    const isBuffer = file.isBuffer === true || file.path.startsWith("buffer://");
    if (isBuffer) {
      const targetPath = await useModalStore.getState().promptDialog({
        title: t("saveBufferPromptTitle", "保存未命名文件"),
        placeholder: t("saveBufferPromptPlaceholder", "例如 E:\\project\\agent\\sketch.ino"),
        defaultValue: file.name,
      });
      if (!targetPath) return false;
      return saveBufferAsFile(id, targetPath);
    }

    try {
      await apiPost("explorer/write", { path: file.path, content: file.content ?? "" });
      // Update snapshot to current content so diff indicator works correctly
      set((s) => ({
        openFiles: s.openFiles.map((f) =>
          f.id === id ? { ...f, dirty: false, snapshot: f.content } : f,
        ),
      }));
      saveOpenFiles(get().openFiles);
      return true;
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      showToastError(`${t("saveFailed", "保存失败")}: ${detail}`);
      return false;
    }
  };

  const reloadFileFromDisk = async (path: string): Promise<void> => {
    const data = await apiGet<ReadFileResponse>(
      `explorer/read?path=${encodeURIComponent(path)}`,
    );
    set((s) => ({
      openFiles: s.openFiles.map((f) =>
        f.path === path ? { ...f, content: data.content ?? "" } : f,
      ),
    }));
  };

  const openCodeBuffer = (opts: { name: string; content: string; language?: string }): void => {
    const id = `buffer-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const item: OpenFileItem = {
      id,
      path: `buffer://${opts.name}`,
      name: opts.name,
      content: opts.content,
      snapshot: opts.content,
      dirty: true,
      pinned: false,
      isBuffer: true,
      language: opts.language,
    };
    set((s) => {
      const nextFiles = [...s.openFiles, item];
      return { openFiles: nextFiles, activeFileId: id, explorerOpen: true };
    });
    saveActiveFileId(id);
  };

  const saveBufferAsFile = async (id: string, targetPath: string): Promise<boolean> => {
    const file = get().openFiles.find((f) => f.id === id);
    if (!file) return false;
    try {
      await apiPost("explorer/write", { path: targetPath, content: file.content ?? "" });
      const newId = targetPath;
      const newName = fileNameFromPath(targetPath);
      set((s) => {
        const nextFiles = s.openFiles.map((f) =>
          f.id === id
            ? {
                ...f,
                id: newId,
                path: targetPath,
                name: newName,
                isBuffer: false,
                dirty: false,
              }
            : f,
        );
        const nextActive = s.activeFileId === id ? newId : s.activeFileId;
        const nextPinned = s.pinnedFileIds.map((pid) => (pid === id ? newId : pid));
        saveOpenFiles(nextFiles);
        saveActiveFileId(nextActive);
        return {
          openFiles: nextFiles,
          activeFileId: nextActive,
          pinnedFileIds: nextPinned,
        };
      });
      return true;
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      showToastError(`${t("saveFailed", "保存失败")}: ${detail}`);
      return false;
    }
  };

  return {
    setExplorerOpen: (explorerOpen) => {
      saveBoolean(EXPLORER_OPEN_KEY, explorerOpen);
      set(() => ({ explorerOpen }));
    },
    setExplorerWidth: (explorerWidth) => {
      saveNumber(EXPLORER_WIDTH_KEY, explorerWidth);
      set(() => ({ explorerWidth }));
    },
    setExplorerRootPath: (explorerRootPath) => {
      if (explorerRootPath) {
        saveExplorerRootPath(explorerRootPath);
      } else {
        clearExplorerRootPath();
      }
      set(() => ({ explorerRootPath }));
    },
    openFile: async (path) => {
      // Skip API call if file already open — just activate the tab
      const existing = get().openFiles.find((f) => f.path === path);
      if (existing) {
        saveActiveFileId(existing.id);
        set(() => ({ activeFileId: existing.id }));
        return;
      }
      try {
        const data = await apiGet<ReadFileResponse>(
          `explorer/read?path=${encodeURIComponent(path)}`,
        );
        const item = buildOpenFileItem(data);
        // Even binary files are added to openFiles so EditorPanel can decide
        // how to render them (text editor vs image preview).
        set((s) => {
          const nextFiles = [...s.openFiles, item];
          saveOpenFiles(nextFiles);
          saveActiveFileId(item.id);
          return { openFiles: nextFiles, activeFileId: item.id };
        });
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        showToastError(`${t("openFileFailed", "打开文件失败")}: ${detail}`);
      }
    },
    closeFile: async (id, action) => {
      const file = get().openFiles.find((f) => f.id === id);
      if (!file) return false;
      if (file.dirty && !action) return false;
      if (file.dirty && action === "cancel") return false;
      if (file.dirty && action === "save") {
        const saved = await saveFileImpl(id);
        if (!saved) return false;
      }
      set((s) => {
        const nextFiles = s.openFiles.filter((f) => f.id !== id);
        const nextPinned = s.pinnedFileIds.filter((pid) => pid !== id);
        saveOpenFiles(nextFiles);
        // Activate adjacent tab (previous or next), not the last tab
        const closedIdx = s.openFiles.findIndex((f) => f.id === id);
        const nextActive = s.activeFileId === id
          ? (nextFiles[closedIdx]?.id ?? nextFiles[closedIdx - 1]?.id ?? null)
          : s.activeFileId;
        saveActiveFileId(nextActive);
        return {
          openFiles: nextFiles,
          pinnedFileIds: nextPinned,
          activeFileId: nextActive,
        };
      });
      return true;
    },
    setActiveFile: (id) => {
      saveActiveFileId(id);
      set(() => ({ activeFileId: id }));
    },
    pinFile: (id) =>
      set((s) => {
        if (s.pinnedFileIds.includes(id)) return s;
        const nextFiles = s.openFiles.map((f) =>
          f.id === id ? { ...f, pinned: true } : f,
        );
        saveOpenFiles(nextFiles);
        return { openFiles: nextFiles, pinnedFileIds: [...s.pinnedFileIds, id] };
      }),
    unpinFile: (id) =>
      set((s) => {
        const nextFiles = s.openFiles.map((f) =>
          f.id === id ? { ...f, pinned: false } : f,
        );
        saveOpenFiles(nextFiles);
        return {
          openFiles: nextFiles,
          pinnedFileIds: s.pinnedFileIds.filter((pid) => pid !== id),
        };
      }),
    markFileDirty: (id, dirty) =>
      set((s) => ({
        openFiles: s.openFiles.map((f) =>
          f.id === id ? { ...f, dirty } : f,
        ),
      })),
    setFileContent: (id, content) =>
      set((s) => ({
        openFiles: s.openFiles.map((f) =>
          f.id === id ? { ...f, content } : f,
        ),
      })),
    saveFile: saveFileImpl,
    openCodeBuffer,
    saveBufferAsFile,
    handleExternalFileChange: async (path) => {
      const state = get();
      const file = state.openFiles.find((f) => f.path === path);
      if (!file) return;
      const isActive = state.activeFileId === file.id;
      if (!isActive) {
        if (!file.dirty) {
          try {
            await reloadFileFromDisk(path);
          } catch {
            // ignore silent refresh failures for inactive clean files
          }
          return;
        }
        // Inactive dirty file: notify user about external change
        useToastStore.getState().showInfo(
          t("externalChangeInactive", "{name} 在外部被修改，切回标签页后可选择保留或重新加载")
            .replace("{name}", file.name),
        );
        return;
      }
      if (!file.dirty) {
        try {
          await reloadFileFromDisk(path);
        } catch (err) {
          const detail = err instanceof Error ? err.message : String(err);
          showToastError(`${t("reloadFailed", "重新加载失败")}: ${detail}`);
        }
        return;
      }
      const reload = await useModalStore.getState().confirmDialog({
        title: t("externalChangeTitle", "文件已在外部修改"),
        message: t("externalChangeMessage", "{name} 已在外部修改，是否重新加载？")
          .replace("{name}", file.name),
        confirmText: t("reload", "重新加载"),
        cancelText: t("keepLocal", "保留本地修改"),
      });
      if (!reload) return;
      try {
        await reloadFileFromDisk(path);
        set((s) => ({
          openFiles: s.openFiles.map((f) =>
            f.path === path ? { ...f, dirty: false, snapshot: f.content } : f,
          ),
        }));
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        showToastError(`${t("reloadFailed", "重新加载失败")}: ${detail}`);
      }
    },
    toggleFollowMode: () => set((s) => ({ followMode: !s.followMode })),
    addRecentFolder: (path) =>
      set((s) => {
        const next = [path, ...s.recentFolders.filter((p) => p !== path)].slice(
          0,
          MAX_RECENT_FOLDERS,
        );
        saveStringArray(RECENT_FOLDERS_KEY, next);
        return { recentFolders: next };
      }),
    setDiffViewOpen: (diffViewOpen) => set(() => ({ diffViewOpen })),
    openDiffForFile: (id) => set(() => ({ diffViewFileId: id, diffViewOpen: true })),
    closeDiffView: () => set(() => ({ diffViewOpen: false, diffViewFileId: null })),
  };
}
