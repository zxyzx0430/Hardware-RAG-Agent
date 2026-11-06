import type {
  Theme,
  Lang,
  NavView,
  RightMode,
  RightContentTab,
  WbTab,
  PreviewTab,
  OpenFileItem,
} from "../../types";
import type { Message } from "../../types/session";

export interface AppState {
  // UI 主题与语言
  themeMode: Theme;
  lang: Lang;
  // 导航
  activeNav: NavView;
  // 面板
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  /** Bumped whenever a caller explicitly asks to open the right panel, even if
   *  the preference is already true but the panel was auto-folded by width. */
  rightPanelRevealNonce: number;
  leftPanelWidth: number;
  rightPanelWidth: number;
  inputBarWidth: number;
  /** When true, the composer follows the available chat-column width. */
  inputBarAuto: boolean;
  rightMode: RightMode;
  /** Selected tab inside the "content" right-panel mode, keyed by session. */
  rightContentTabBySession: Record<string, RightContentTab>;
  // 工作台
  wbTab: WbTab;
  workbenchUserOverride: boolean;
  flashCode: string;
  // 聊天辅助
  quotedMsg: Message | null;
  searchOpen: boolean;
  searchQuery: string;
  sessionGroupsCollapsed: Record<string, boolean>;
  chatFontSize: number;
  skipScroll: boolean;
  // 硬件工作台
  flashChip: string;
  flashPlatform: string;
  flashBoard: string;
  previewTabs: PreviewTab[];
  activePreviewTabId: string | null;
  // 模板与快照面板
  templatePanelOpen: boolean;
  snapshotPanelOpen: boolean;
  // 快捷键帮助
  shortcutHelpOpen: boolean;
  // 分支图面板
  branchTreeOpen: boolean;
  // 资源管理器
  explorerOpen: boolean;
  explorerWidth: number;
  explorerRootPath: string | null;
  openFiles: OpenFileItem[];
  activeFileId: string | null;
  pinnedFileIds: string[];
  followMode: boolean;
  recentFolders: string[];
  editorShowTree: boolean;

  // Diff view
  diffViewOpen: boolean;
  diffViewFileId: string | null;

  // Actions
  setThemeMode: (t: Theme) => void;
  setLang: (l: Lang) => void;
  setActiveNav: (v: NavView) => void;
  setLeftPanelOpen: (o: boolean) => void;
  setRightPanelOpen: (o: boolean) => void;
  setLeftPanelWidth: (w: number) => void;
  setRightPanelWidth: (w: number) => void;
  setInputBarWidth: (w: number) => void;
  /** Return the composer to auto-fill mode. */
  resetInputBarWidth: () => void;
  setRightMode: (m: RightMode) => void;
  setRightContentTab: (sessionId: string, tab: RightContentTab) => void;
  setWbTab: (t: WbTab, source?: "user" | "bridge") => void;
  setWorkbenchUserOverride: (val: boolean) => void;
  resetWorkbenchOverride: () => void;
  setFlashCode: (code: string) => void;
  setQuotedMsg: (msg: Message | null) => void;
  setSearchOpen: (open: boolean) => void;
  setSearchQuery: (q: string) => void;
  toggleSessionGroupCollapsed: (group: string) => void;
  setChatFontSize: (s: number) => void;
  setSkipScroll: (s: boolean) => void;
  setFlashChip: (c: string) => void;
  setFlashPlatform: (platform: string) => void;
  setFlashBoard: (board: string) => void;
  addPreviewTab: (tab: PreviewTab) => void;
  removePreviewTab: (id: string) => void;
  setActivePreviewTabId: (id: string | null) => void;
  updatePreviewTabCode: (id: string, code: string) => void;
  setTemplatePanelOpen: (open: boolean) => void;
  setSnapshotPanelOpen: (open: boolean) => void;
  setShortcutHelpOpen: (open: boolean) => void;
  setBranchTreeOpen: (open: boolean) => void;
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
  setEditorShowTree: (show: boolean) => void;

  // Diff view actions
  setDiffViewOpen: (open: boolean) => void;
  openDiffForFile: (id: string) => void;
  closeDiffView: () => void;
}
