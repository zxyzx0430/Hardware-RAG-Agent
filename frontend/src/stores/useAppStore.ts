import { create } from "zustand";
import { useLogStore } from "./useLogStore";
import type {
  Theme,
  Lang,
  NavView,
  RightMode,
  WbTab,
  FlashState,
  BuildState,
  PreviewTab,
} from "../types";
import type { Message } from "../types/session";

interface AppState {
  // UI 主题与语言
  themeMode: Theme;
  lang: Lang;
  // 导航
  activeNav: NavView;
  activeSession: string;
  // 面板
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  leftPanelWidth: number;
  rightPanelWidth: number;
  rightMode: RightMode;
  // 工作台
  wbTab: WbTab;
  flashCode: string;
  // 流式状态
  isStreaming: boolean;
  // 聊天辅助
  highlightSourceId: string | null;
  quotedMsg: Message | null;
  searchOpen: boolean;
  searchQuery: string;
  sessionGroupsCollapsed: Record<string, boolean>;
  chatFontSize: number;
  skipScroll: boolean;
  fileViewerSource: string | null;
  // 硬件工作台
  serialConnected: boolean;
  flashState: FlashState;
  buildState: BuildState;
  flashChip: string;
  previewTabs: PreviewTab[];
  activePreviewTabId: string | null;
  // 模板与快照面板
  templatePanelOpen: boolean;
  snapshotPanelOpen: boolean;
  // 快捷键帮助
  shortcutHelpOpen: boolean;
  // 分支图面板
  branchTreeOpen: boolean;

  // Actions
  setThemeMode: (t: Theme) => void;
  setLang: (l: Lang) => void;
  setActiveNav: (v: NavView) => void;
  setActiveSession: (id: string) => void;
  setLeftPanelOpen: (o: boolean) => void;
  setRightPanelOpen: (o: boolean) => void;
  setLeftPanelWidth: (w: number) => void;
  setRightPanelWidth: (w: number) => void;
  setRightMode: (m: RightMode) => void;
  setWbTab: (t: WbTab) => void;
  setFlashCode: (code: string) => void;
  setIsStreaming: (s: boolean) => void;
  setHighlightSourceId: (id: string | null) => void;
  setQuotedMsg: (msg: Message | null) => void;
  setSearchOpen: (open: boolean) => void;
  setSearchQuery: (q: string) => void;
  toggleSessionGroupCollapsed: (group: string) => void;
  setChatFontSize: (s: number) => void;
  setSkipScroll: (s: boolean) => void;
  setFileViewerSource: (src: string | null) => void;
  setSerialConnected: (c: boolean) => void;
  setFlashState: (s: FlashState) => void;
  setBuildState: (s: BuildState) => void;
  setFlashChip: (c: string) => void;
  addPreviewTab: (tab: PreviewTab) => void;
  removePreviewTab: (id: string) => void;
  setActivePreviewTabId: (id: string | null) => void;
  updatePreviewTabCode: (id: string, code: string) => void;
  setTemplatePanelOpen: (open: boolean) => void;
  setSnapshotPanelOpen: (open: boolean) => void;
  setShortcutHelpOpen: (open: boolean) => void;
  setBranchTreeOpen: (open: boolean) => void;
}

export const useAppStore = create<AppState>((set) => ({
  themeMode: "light",
  lang: "zh",
  activeNav: "chat",
  activeSession: "s1",
  leftPanelOpen: true,
  rightPanelOpen: true,
  leftPanelWidth: 280,
  rightPanelWidth: 280,
  rightMode: "workbench",
  wbTab: "serial",
  flashCode: "",
  isStreaming: false,
  highlightSourceId: null,
  quotedMsg: null,
  searchOpen: false,
  searchQuery: "",
  sessionGroupsCollapsed: {},
  chatFontSize: 14,
  skipScroll: false,
  fileViewerSource: null,
  serialConnected: false,
  flashState: "idle",
  buildState: "idle",
  flashChip: "ESP32-S3",
  previewTabs: [],
  activePreviewTabId: null,
  templatePanelOpen: false,
  snapshotPanelOpen: false,
  shortcutHelpOpen: false,
  branchTreeOpen: false,

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
  setActiveSession: (activeSession) => set({ activeSession }),
  setLeftPanelOpen: (leftPanelOpen) => set({ leftPanelOpen }),
  setRightPanelOpen: (rightPanelOpen) => set({ rightPanelOpen }),
  setLeftPanelWidth: (leftPanelWidth) => set({ leftPanelWidth }),
  setRightPanelWidth: (rightPanelWidth) => set({ rightPanelWidth }),
  setRightMode: (rightMode) => set({ rightMode }),
  setWbTab: (wbTab) => set({ wbTab }),
  setFlashCode: (flashCode) => set({ flashCode }),
  setIsStreaming: (isStreaming) => set({ isStreaming }),
  setHighlightSourceId: (highlightSourceId) => set({ highlightSourceId }),
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
  setFileViewerSource: (fileViewerSource) => set({ fileViewerSource }),
  setSerialConnected: (serialConnected) => set({ serialConnected }),
  setFlashState: (flashState) => set({ flashState }),
  setBuildState: (buildState) => set({ buildState }),
  setFlashChip: (flashChip) => set({ flashChip }),
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
}));
