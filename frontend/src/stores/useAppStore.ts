import { create } from "zustand";
import { useLogStore } from "./useLogStore";
import {
  saveRightPanelOpen,
  getDefaultRightPanelOpen,
  loadRightPanelWidth,
  saveRightPanelWidth,
  loadBoolean,
  loadNumber,
  loadStringArray,
  loadOpenFiles,
  loadActiveFileId,
  loadExplorerRootPath,
  loadEditorShowTree,
  saveEditorShowTree,
  EXPLORER_OPEN_KEY,
  EXPLORER_WIDTH_KEY,
  RECENT_FOLDERS_KEY,
  DEFAULT_EXPLORER_WIDTH,
} from "./appStore/persistence";
import type { Theme, Lang, NavView, RightMode, WbTab, PreviewTab } from "../types";
import type { AppState } from "./appStore/types";
import { createExplorerActions } from "./appStore/explorerActions";

export const useAppStore = create<AppState>((set, get) => ({
  themeMode: "light",
  lang: "zh",
  activeNav: "chat",
  leftPanelOpen: true,
  rightPanelOpen: getDefaultRightPanelOpen(),
  leftPanelWidth: 280,
  rightPanelWidth: loadRightPanelWidth(),
  rightMode: "workbench",
  wbTab: "serial",
  workbenchUserOverride: false,
  flashCode: "",
  quotedMsg: null,
  searchOpen: false,
  searchQuery: "",
  sessionGroupsCollapsed: {},
  chatFontSize: 14,
  skipScroll: false,
  flashChip: "ESP32-S3",
  flashPlatform: "espressif32",
  flashBoard: "esp32-s3-devkitc-1",
  previewTabs: [],
  activePreviewTabId: null,
  templatePanelOpen: false,
  snapshotPanelOpen: false,
  shortcutHelpOpen: false,
  branchTreeOpen: false,
  explorerOpen: loadBoolean(EXPLORER_OPEN_KEY) ?? true,
  explorerWidth: loadNumber(EXPLORER_WIDTH_KEY) ?? DEFAULT_EXPLORER_WIDTH,
  explorerRootPath: loadExplorerRootPath(),
  openFiles: loadOpenFiles(),
  activeFileId: loadActiveFileId(),
  pinnedFileIds: loadOpenFiles()
    .filter((f) => f.pinned)
    .map((f) => f.id),
  followMode: false,
  recentFolders: loadStringArray(RECENT_FOLDERS_KEY),
  editorShowTree: loadEditorShowTree(),
  diffViewOpen: false,
  diffViewFileId: null,

  setThemeMode: (themeMode) => {
      useLogStore.getState().log("debug", "ui", "主题切换: " + themeMode);
      set({ themeMode });
    },
  setLang: (lang) => {
      useLogStore.getState().log("debug", "ui", "语言切换: " + lang);
      set({ lang });
    },
  setActiveNav: (activeNav) => {
      useLogStore.getState().log("debug", "ui", "导航切换: " + activeNav);
      set({ activeNav });
    },
  setLeftPanelOpen: (leftPanelOpen) => set({ leftPanelOpen }),
  setRightPanelOpen: (rightPanelOpen) => {
    saveRightPanelOpen(rightPanelOpen);
    set({ rightPanelOpen });
  },
  setLeftPanelWidth: (leftPanelWidth) => set({ leftPanelWidth }),
  setRightPanelWidth: (rightPanelWidth) => {
    saveRightPanelWidth(rightPanelWidth);
    set({ rightPanelWidth });
  },
  setRightMode: (rightMode) => set({ rightMode }),
  setWbTab: (wbTab, source = "user") =>
    set(source === "user" ? { wbTab, workbenchUserOverride: true } : { wbTab }),
  setWorkbenchUserOverride: (workbenchUserOverride) => set({ workbenchUserOverride }),
  resetWorkbenchOverride: () => set({ workbenchUserOverride: false }),
  setFlashCode: (flashCode) => set({ flashCode }),
  setQuotedMsg: (quotedMsg) => set({ quotedMsg }),
  setSearchOpen: (searchOpen) => set({ searchOpen }),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  toggleSessionGroupCollapsed: (group) =>
    set((s) => ({
      sessionGroupsCollapsed: {
        ...s.sessionGroupsCollapsed,
        [group]: !s.sessionGroupsCollapsed[group],
      },
    })),
  setChatFontSize: (chatFontSize) => set({ chatFontSize }),
  setSkipScroll: (skipScroll) => set({ skipScroll }),
  setFlashChip: (flashChip) => set({ flashChip }),
  setFlashPlatform: (flashPlatform) => set({ flashPlatform }),
  setFlashBoard: (flashBoard) => set({ flashBoard }),
  addPreviewTab: (tab) =>
    set((s) => {
      const exists = s.previewTabs.find((item) => item.id === tab.id);
      if (exists) {
        return {
          previewTabs: s.previewTabs.map((item) =>
            item.id === tab.id ? tab : item,
          ),
          activePreviewTabId: tab.id,
        };
      }

      return {
        previewTabs: [...s.previewTabs, tab],
        activePreviewTabId: tab.id,
      };
    }),
  removePreviewTab: (id) =>
    set((s) => {
      const nextTabs = s.previewTabs.filter((t) => t.id !== id);
      return {
        previewTabs: nextTabs,
        activePreviewTabId:
          s.activePreviewTabId === id ? nextTabs.at(-1)?.id ?? null : s.activePreviewTabId,
      };
    }),
  setActivePreviewTabId: (activePreviewTabId) => set({ activePreviewTabId }),
  updatePreviewTabCode: (id, code) =>
    set((s) => ({
      previewTabs: s.previewTabs.map((t) =>
        t.id === id ? { ...t, code } : t,
      ),
    })),
  setTemplatePanelOpen: (templatePanelOpen) => set({ templatePanelOpen }),
  setSnapshotPanelOpen: (snapshotPanelOpen) => set({ snapshotPanelOpen }),
  setShortcutHelpOpen: (shortcutHelpOpen) => set({ shortcutHelpOpen }),
  setBranchTreeOpen: (branchTreeOpen) => set({ branchTreeOpen }),
  setEditorShowTree: (editorShowTree) => {
    saveEditorShowTree(editorShowTree);
    set({ editorShowTree });
  },

  ...createExplorerActions(set, get),
}));
