import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useChatStore } from "../../stores/useChatStore";
import { usePanelResize } from "../../hooks/usePanelResize";
import {
  RIGHT_PANEL_MIN_WIDTH, RIGHT_PANEL_MAX_WIDTH,
  EXPLORER_MIN_WIDTH,
  INPUT_BAR_MIN_WIDTH, INPUT_BAR_MAX_WIDTH,
} from "../../stores/appStore/persistence";
import { useToastStore } from "../../stores/useToastStore";
import {
  CHAT_COLUMN_MIN_WIDTH,
  LEFT_PANEL_MAX_WIDTH,
  LEFT_PANEL_MIN_WIDTH,
  getResizeReservedWidth,
  resolveLayout,
  type LayoutPanel,
} from "./layoutBudget";
import type { LayoutBudgetInput } from "./layoutBudget";
import { IconNav } from "./IconNav";
import { LeftPanel } from "./LeftPanel";
import { TopBar } from "../topbar/TopBar";
import { ChatArea } from "../chat/ChatArea";
import { InputBar } from "../input/InputBar";
import { RightPanel } from "./RightPanel";
import { ExplorerPanel } from "../explorer";
import { SettingsPage } from "../settings/SettingsPage";
import { KnowledgePanel } from "../knowledge/KnowledgePanel";
import { BookmarkPanel } from "../bookmarks/BookmarkPanel";
import { StatsPanel } from "../shared/StatsPanel";
import { SearchModal } from "../shared/SearchModal";
import { SnapshotPanel } from "../shared/SnapshotPanel";
import { ModalContainer } from "../shared/Modal";
import { ToastContainer } from "../shared/Toast";
import { ShortcutHelp } from "../chat/ShortcutHelp";
import { useI18n } from "../../i18n";

type AppStoreState = ReturnType<typeof useAppStore.getState>;

function makeLayoutBudgetInput(
  state: AppStoreState,
  viewportWidth: number,
  protectedPanel: LayoutPanel | null = null,
): LayoutBudgetInput {
  return {
    viewportWidth,
    showChatShell: state.activeNav === "chat" || state.activeNav === "settings",
    leftPanelOpen: state.activeNav === "chat" && state.leftPanelOpen,
    rightPanelOpen: state.rightPanelOpen,
    explorerOpen: state.explorerOpen,
    leftPanelWidth: state.leftPanelWidth,
    rightPanelWidth: state.rightPanelWidth,
    explorerWidth: state.explorerWidth,
    inputBarWidth: state.inputBarWidth,
    protectedPanel,
  };
}

function closeAutoFoldedPanels(state: AppStoreState, panels: LayoutPanel[]): LayoutPanel[] {
  const folded: LayoutPanel[] = [];
  for (const panel of panels) {
    if (panel === "left" && state.leftPanelOpen) {
      state.setLeftPanelOpen(false);
      folded.push(panel);
    } else if (panel === "right" && state.rightPanelOpen) {
      state.setRightPanelOpen(false);
      folded.push(panel);
    } else if (panel === "explorer" && state.explorerOpen) {
      state.setExplorerOpen(false);
      folded.push(panel);
    }
  }
  return folded;
}

function showLayoutFoldNotice(): void {
  const english = useAppStore.getState().lang === "en";
  useToastStore.getState().showInfo(
    english
      ? "Space is limited. Some panels were folded; reopen them from the side strips."
      : "空间不足，已暂时收起部分面板；可从侧边按钮重新打开。",
  );
}

export function AppRoot() {
  const { t } = useI18n();
  const {
    activeNav,
    leftPanelOpen,
    rightPanelOpen,
    leftPanelWidth,
    rightPanelWidth,
    setLeftPanelOpen,
    setRightPanelOpen,
    setLeftPanelWidth,
    setRightPanelWidth,
    snapshotPanelOpen,
    shortcutHelpOpen,
    setShortcutHelpOpen,
    explorerOpen,
    explorerWidth,
    setExplorerOpen,
    setExplorerWidth,
    inputBarWidth,
    setInputBarWidth,
  } = useAppStore();

  // 启动/刷新时：会话列表加载完成后，加载当前活跃会话的消息；
  // 若 activeSessionId 指向不存在的会话（幽灵 "s1" 或首次进入），切到第一个真实会话。
  const sessionsInitialized = useSessionStore((s) => s.initialized);
  const sessions = useSessionStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const fetchMessages = useChatStore((s) => s.fetchMessages);

  const showKnowledgePage = activeNav === "knowledge";
  const showBookmarkPage = activeNav === "bookmarks";
  const showChatShell = activeNav === "chat" || activeNav === "settings";
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window === "undefined" ? 1024 : window.innerWidth,
  );
  const viewportWidthRef = useRef(viewportWidth);
  viewportWidthRef.current = viewportWidth;
  const resolvedLayout = useMemo(
    () => resolveLayout({
      viewportWidth,
      showChatShell,
      leftPanelOpen: activeNav === "chat" && leftPanelOpen,
      rightPanelOpen,
      explorerOpen,
      leftPanelWidth,
      rightPanelWidth,
      explorerWidth,
      inputBarWidth,
    }),
    [
      activeNav,
      explorerOpen,
      explorerWidth,
      inputBarWidth,
      leftPanelOpen,
      leftPanelWidth,
      rightPanelOpen,
      rightPanelWidth,
      showChatShell,
      viewportWidth,
    ],
  );
  const resolvedLayoutRef = useRef(resolvedLayout);
  resolvedLayoutRef.current = resolvedLayout;

  // Re-budget the four resize handles whenever the visible layout changes.
  const left = usePanelResize(
    resolvedLayout.leftPanelWidth,
    "left",
    LEFT_PANEL_MIN_WIDTH,
    LEFT_PANEL_MAX_WIDTH,
    setLeftPanelWidth,
    "px",
    () => getResizeReservedWidth("left", resolvedLayoutRef.current),
  );
  const right = usePanelResize(
    resolvedLayout.rightPanelWidth,
    "right",
    RIGHT_PANEL_MIN_WIDTH,
    RIGHT_PANEL_MAX_WIDTH,
    setRightPanelWidth,
    "px",
    () => getResizeReservedWidth("right", resolvedLayoutRef.current),
  );
  const explorer = usePanelResize(
    resolvedLayout.explorerWidth,
    "right",
    EXPLORER_MIN_WIDTH,
    viewportWidth / 2,
    setExplorerWidth,
    "px",
    () => getResizeReservedWidth("explorer", resolvedLayoutRef.current),
  );
  const inputBarResize = usePanelResize(
    resolvedLayout.inputBarWidth,
    "right",
    INPUT_BAR_MIN_WIDTH,
    INPUT_BAR_MAX_WIDTH,
    setInputBarWidth,
    "px",
    () => getResizeReservedWidth("inputBar", resolvedLayoutRef.current),
  );

  const leftPanelOpenForRender = activeNav === "chat"
    ? resolvedLayout.leftPanelOpen
    : leftPanelOpen;
  const showLeftRail = showChatShell && resolvedLayout.leftPanelOpen;

  useEffect(() => {
    const onResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Close only panes that no longer fit. Stored width preferences and file/chat drafts remain intact.
  useLayoutEffect(() => {
    if (resolvedLayout.autoCollapsedPanels.length === 0) return;
    const folded = closeAutoFoldedPanels(
      useAppStore.getState(),
      resolvedLayout.autoCollapsedPanels,
    );
    if (folded.length > 0) showLayoutFoldNotice();
  }, [
    resolvedLayout.autoCollapsedPanels,
  ]);

  // Any user-facing path that opens a pane is protected, including source/workbench actions.
  useLayoutEffect(() => {
    let previousState = useAppStore.getState();
    return useAppStore.subscribe((state) => {
      const newlyOpened: LayoutPanel[] = [];
      if (state.leftPanelOpen && !previousState.leftPanelOpen && state.activeNav === "chat") {
        newlyOpened.push("left");
      }
      if (state.rightPanelOpen && !previousState.rightPanelOpen) newlyOpened.push("right");
      if (state.explorerOpen && !previousState.explorerOpen) newlyOpened.push("explorer");
      previousState = state;
      if (newlyOpened.length !== 1) return;

      const nextLayout = resolveLayout(
        makeLayoutBudgetInput(state, viewportWidthRef.current, newlyOpened[0]),
      );
      const folded = closeAutoFoldedPanels(state, nextLayout.autoCollapsedPanels);
      if (folded.length > 0) showLayoutFoldNotice();
    });
  }, []);

  useEffect(() => {
    if (!sessionsInitialized) return;
    const exists = sessions.some((s) => s.id === activeSessionId);
    if (!exists) {
      if (sessions.length > 0) {
        useChatStore.getState().setActiveSession(sessions[0].id);
      } else if (activeSessionId !== "") {
        // 无会话：清空 activeSessionId，避免幽灵 "s1" 持续触发
        useChatStore.setState({ messages: [], activeSessionId: "" });
        localStorage.removeItem("activeSession");
      }
      return;
    }
    // 会话存在：加载消息（localStorage 缓存先填充，后端数据覆盖）
    void fetchMessages(activeSessionId);
    // 只在 initialized/sessions/activeSessionId 变化时触发，不依赖 fetchMessages 引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionsInitialized, sessions, activeSessionId]);

  // 浏览器标签页标题跟随当前会话标题
  const activeSessionTitle = sessions.find((s) => s.id === activeSessionId)?.title ?? "";
  useEffect(() => {
    document.title = activeSessionTitle
      ? `${activeSessionTitle} - Hardware RAG Agent`
      : "Hardware RAG Agent";
  }, [activeSessionTitle]);

  return (
    <div
      className="app-root"
      id="app"
      style={{
        flexDirection: "column",
        "--layout-left-panel-width": `${showLeftRail ? resolvedLayout.leftPanelWidth : 0}px`,
      } as CSSProperties}
    >
      {showChatShell ? <TopBar /> : null}

      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        <IconNav />
        <LeftPanel />
        <div
          className={`sidebar-resizer${showLeftRail ? '' : ' hidden'}`}
          id="sidebarResizer"
          onMouseDown={left.onMouseDown}
          style={{ cursor: 'col-resize' }}
        />
        <div className={`panel-btn-strip left${showChatShell ? '' : ' hidden'}`} id="leftStrip">
          <button
            className="panel-toggle left"
            id="leftToggleBtn"
            onClick={() => setLeftPanelOpen(!leftPanelOpenForRender)}
            aria-label={t('toggleLeftPanel')}
            title={leftPanelOpenForRender ? '折叠左侧面板' : '展开左侧面板'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {leftPanelOpenForRender
                ? <polyline points="15 18 9 12 15 6" />
                : <polyline points="9 18 15 12 9 6" />}
            </svg>
          </button>
        </div>
        <div className="main-area" id="mainArea" style={{ minWidth: 0 }}>
          <div id="chatFlex" style={{ flex: 1, display: showChatShell ? "flex" : "none", minHeight: 0, overflow: "hidden" }}>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: CHAT_COLUMN_MIN_WIDTH, overflow: "hidden", background: "var(--bg)" }}>
              <ChatArea />
              <div className="inputbar-resize-wrap" style={{ display: "flex", alignItems: "stretch" }}>
                <div style={{ width: resolvedLayout.inputBarWidth, maxWidth: "100%", minWidth: INPUT_BAR_MIN_WIDTH, flexShrink: 0 }}>
                  <InputBar />
                </div>
                <div
                  className="inputbar-resizer"
                  onMouseDown={inputBarResize.onMouseDown}
                  style={{ cursor: "col-resize" }}
                  title="拖拽调整输入栏宽度"
                />
              </div>
            </div>
            <div className={`panel-btn-strip right${showChatShell ? '' : ' hidden'}`} id="rightStrip">
              <button
                className="panel-toggle right"
                id="rightToggleBtn"
                onClick={() => setRightPanelOpen(!resolvedLayout.rightPanelOpen)}
                aria-label={t('toggleRightPanel')}
                title={resolvedLayout.rightPanelOpen ? '折叠右侧面板' : '展开右侧面板'}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {resolvedLayout.rightPanelOpen
                    ? <polyline points="9 18 15 12 9 6" />
                    : <polyline points="15 18 9 12 15 6" />}
                </svg>
              </button>
            </div>
            <div
              className={`chat-resizer${showChatShell && resolvedLayout.rightPanelOpen ? '' : ' hidden'}`}
              id="chatResizer"
              onMouseDown={(e) => {
                right.onMouseDown(e);
              }}
              style={{ cursor: 'col-resize' }}
            />
            <div style={{ width: resolvedLayout.rightPanelOpen ? `${resolvedLayout.rightPanelWidth}px` : 0, overflow: 'hidden', display: 'flex', transition: resolvedLayout.rightPanelOpen ? 'none' : 'width 0.2s' }}>
              <RightPanel />
            </div>

            <div className={`explorer-collapsed-strip${resolvedLayout.explorerOpen ? ' hidden' : ''}`} id="explorerStrip">
              <button
                className="explorer-toggle"
                id="explorerToggleBtn"
                onClick={() => setExplorerOpen(true)}
                aria-label={t('toggleExplorerPanel')}
                title="展开资源管理器"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="15 18 9 12 15 6" />
                </svg>
              </button>
            </div>
            <div
              className={`explorer-resizer${resolvedLayout.explorerOpen ? '' : ' hidden'}`}
              id="explorerResizer"
              onMouseDown={(e) => {
                if (!resolvedLayout.explorerOpen) setExplorerOpen(true);
                explorer.onMouseDown(e);
              }}
              style={{ cursor: 'col-resize' }}
            />
            <div
              style={{
                width: resolvedLayout.explorerOpen ? resolvedLayout.explorerWidth : 0,
                minWidth: resolvedLayout.explorerOpen ? EXPLORER_MIN_WIDTH : 0,
                flexShrink: 0,
                overflow: 'hidden',
                display: 'flex',
                transition: resolvedLayout.explorerOpen ? 'none' : 'width 0.2s',
              }}
            >
              <ExplorerPanel />
            </div>
          </div>

          {showKnowledgePage ? <KnowledgePanel /> : null}
          {showBookmarkPage ? <BookmarkPanel /> : null}
        </div>
        {activeNav === "settings" && <SettingsPage />}
      </div>

      <StatsPanel />
      <SearchModal />
      {snapshotPanelOpen && <SnapshotPanel />}
      {shortcutHelpOpen && <ShortcutHelp onClose={() => setShortcutHelpOpen(false)} />}
      <ModalContainer />
      <ToastContainer />
    </div>
  );
}
