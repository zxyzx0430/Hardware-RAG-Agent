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
    inputBarAuto: state.inputBarAuto,
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
      ? "Space is limited. Some panels are folded; reopen them from the side strips or widen the window."
      : "空间不足，已暂时收起部分面板；可从侧边按钮重新打开，窗口变宽后会自动恢复。",
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
    setRightPanelOpen,
    setLeftPanelWidth,
    setRightPanelWidth,
    snapshotPanelOpen,
    shortcutHelpOpen,
    setShortcutHelpOpen,
    explorerOpen,
    explorerWidth,
    setExplorerWidth,
    inputBarWidth,
    inputBarAuto,
    setInputBarWidth,
    resetInputBarWidth,
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
      inputBarAuto,
    }),
    [
      activeNav,
      explorerOpen,
      explorerWidth,
      inputBarAuto,
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
  const autoCollapsedKeyRef = useRef("");
  const lastFoldNoticeAtRef = useRef(Number.NEGATIVE_INFINITY);
  // Ephemeral drawer state: the right panel is preferred open but does not fit
  // in the row. It overlays the chat until closed or the window widens.
  const [rightDrawerOpen, setRightDrawerOpen] = useState(false);
  const rightPanelPreferredOpen = useAppStore((s) => s.rightPanelOpen);
  useEffect(() => {
    if (!rightPanelPreferredOpen) setRightDrawerOpen(false);
  }, [rightPanelPreferredOpen]);
  useEffect(() => {
    if (activeNav !== "chat") setRightDrawerOpen(false);
  }, [activeNav]);

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

  // Auto-fold is a render-time decision, never a persisted preference: widening
  // the window (or refreshing at a wider size) restores the same panels.
  useEffect(() => {
    const panels = resolvedLayout.autoCollapsedPanels;
    const nextKey = [...panels].sort().join(",");
    const previous = autoCollapsedKeyRef.current
      ? new Set(autoCollapsedKeyRef.current.split(","))
      : new Set<string>();
    autoCollapsedKeyRef.current = nextKey;
    if (panels.length === 0) return;
    const newlyFolded = panels.filter((panel) => !previous.has(panel));
    if (newlyFolded.length === 0) return;
    const now = Date.now();
    if (now - lastFoldNoticeAtRef.current < 2000) return;
    lastFoldNoticeAtRef.current = now;
    showLayoutFoldNotice();
  }, [resolvedLayout.autoCollapsedPanels]);

  // Any user-facing path that opens a pane is protected, including source/workbench actions.
  useLayoutEffect(() => {
    let previousState = useAppStore.getState();
    return useAppStore.subscribe((state) => {
      const newlyOpened: LayoutPanel[] = [];
      if (state.leftPanelOpen && !previousState.leftPanelOpen && state.activeNav === "chat") {
        newlyOpened.push("left");
      }
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

  // Opening the right panel is a reveal action even when the stored preference
  // is already true (the panel may have been auto-folded by a narrow window).
  useEffect(() => {
    let previousNonce = useAppStore.getState().rightPanelRevealNonce;
    return useAppStore.subscribe((state) => {
      if (state.rightPanelRevealNonce === previousNonce) return;
      previousNonce = state.rightPanelRevealNonce;
      if (!state.rightPanelOpen) {
        setRightDrawerOpen(false);
        return;
      }
      const nextLayout = resolveLayout(
        makeLayoutBudgetInput(state, viewportWidthRef.current, "right"),
      );
      if (nextLayout.rightPanelOverlay) {
        setRightDrawerOpen(true);
        return;
      }
      setRightDrawerOpen(false);
      const folded = closeAutoFoldedPanels(state, nextLayout.autoCollapsedPanels);
      if (folded.length > 0) showLayoutFoldNotice();
    });
  }, []);

  const togglePanel = (panel: LayoutPanel) => {
    const layout = resolvedLayoutRef.current;
    const effectiveOpen = panel === "left"
      ? layout.leftPanelOpen
      : panel === "right"
        ? layout.rightPanelOpen
        : layout.explorerOpen;
    if (panel === "right") {
      // setRightPanelOpen(true) bumps the reveal nonce, so the drawer/protection
      // logic in the subscription handles both already-open and folded states.
      useAppStore.getState().setRightPanelOpen(!effectiveOpen);
      return;
    }
    const store = useAppStore.getState();
    const setOpen = panel === "left" ? store.setLeftPanelOpen : store.setExplorerOpen;
    if (effectiveOpen) {
      setOpen(false);
      return;
    }
    setOpen(true);
    const nextState = useAppStore.getState();
    const nextLayout = resolveLayout(
      makeLayoutBudgetInput(nextState, viewportWidthRef.current, panel),
    );
    const folded = closeAutoFoldedPanels(nextState, nextLayout.autoCollapsedPanels);
    if (folded.length > 0) showLayoutFoldNotice();
  };

  useEffect(() => {
    if (!sessionsInitialized) return;
    const exists = sessions.some((s) => s.id === activeSessionId);
    if (!exists) {
      if (sessions.length > 0) {
        useChatStore.getState().setActiveSession(sessions[0].id);
      } else if (activeSessionId !== "") {
        // 无会话：清空 activeSessionId，避免幽灵 "s1" 持续触发
        useChatStore.setState({ messages: [], activeSessionId: "", planMode: false });
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
            onClick={() => togglePanel("left")}
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
          <div id="chatFlex" style={{ flex: 1, position: "relative", display: showChatShell ? "flex" : "none", minHeight: 0, overflow: "hidden" }}>
            <div
              id="chatColumn"
              data-chat-width={resolvedLayout.chatColumnWidth}
              style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: CHAT_COLUMN_MIN_WIDTH, overflow: "hidden", background: "var(--bg)" }}
            >
              <ChatArea />
              <div className="inputbar-resize-wrap" style={{ display: "flex", alignItems: "stretch" }}>
                <div
                  id="inputBarHost"
                  data-auto={inputBarAuto ? "true" : "false"}
                  style={{
                    width: resolvedLayout.inputBarWidth,
                    maxWidth: "calc(100% - 12px)",
                    minWidth: INPUT_BAR_MIN_WIDTH,
                    flexShrink: 0,
                  }}
                >
                  <InputBar />
                </div>
                <div
                  className="inputbar-resizer"
                  onMouseDown={inputBarResize.onMouseDown}
                  onDoubleClick={resetInputBarWidth}
                  style={{ cursor: "col-resize" }}
                  title="拖拽调整输入栏宽度；双击恢复随窗口自适应"
                />
              </div>
            </div>
            <div className={`panel-btn-strip right${showChatShell ? '' : ' hidden'}`} id="rightStrip">
              <button
                className="panel-toggle right"
                id="rightToggleBtn"
                onClick={() => togglePanel("right")}
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
              className={`chat-resizer${showChatShell && resolvedLayout.rightPanelOpen && !resolvedLayout.rightPanelOverlay ? '' : ' hidden'}`}
              id="chatResizer"
              onMouseDown={(e) => {
                right.onMouseDown(e);
              }}
              style={{ cursor: 'col-resize' }}
            />
            <div
              id="rightPanelHost"
              data-open={resolvedLayout.rightPanelOpen && !resolvedLayout.rightPanelOverlay ? "true" : "false"}
              style={{
                width: resolvedLayout.rightPanelOpen && !resolvedLayout.rightPanelOverlay
                  ? `${resolvedLayout.rightPanelWidth}px`
                  : 0,
                minWidth: resolvedLayout.rightPanelOpen && !resolvedLayout.rightPanelOverlay
                  ? `${RIGHT_PANEL_MIN_WIDTH}px`
                  : 0,
                flexShrink: 0,
                overflow: 'hidden',
                display: resolvedLayout.rightPanelOpen && !resolvedLayout.rightPanelOverlay ? 'flex' : 'none',
                transition: resolvedLayout.rightPanelOpen ? 'none' : 'width 0.2s',
              }}
            >
              <RightPanel />
            </div>

            {showChatShell && rightDrawerOpen && !resolvedLayout.rightPanelOpen ? (
              <>
                <div
                  className="right-panel-backdrop"
                  id="rightPanelBackdrop"
                  onClick={() => {
                    setRightDrawerOpen(false);
                    setRightPanelOpen(false);
                  }}
                  aria-hidden="true"
                />
                <div
                  className="right-panel-overlay"
                  id="rightPanelOverlay"
                  style={{ width: Math.min(RIGHT_PANEL_MAX_WIDTH, Math.max(RIGHT_PANEL_MIN_WIDTH, rightPanelWidth)) }}
                >
                  <RightPanel
                    overlay
                    onClose={() => {
                      setRightDrawerOpen(false);
                      setRightPanelOpen(false);
                    }}
                  />
                </div>
              </>
            ) : null}

            <div className={`explorer-collapsed-strip${resolvedLayout.explorerOpen ? ' hidden' : ''}`} id="explorerStrip">
              <button
                className="explorer-toggle"
                id="explorerToggleBtn"
                onClick={() => togglePanel("explorer")}
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
                if (!resolvedLayout.explorerOpen) togglePanel("explorer");
                explorer.onMouseDown(e);
              }}
              style={{ cursor: 'col-resize' }}
            />
            <div
              id="explorerHost"
              data-open={resolvedLayout.explorerOpen ? "true" : "false"}
              style={{
                width: resolvedLayout.explorerOpen ? resolvedLayout.explorerWidth : 0,
                minWidth: resolvedLayout.explorerOpen ? EXPLORER_MIN_WIDTH : 0,
                flexShrink: 0,
                overflow: 'hidden',
                display: resolvedLayout.explorerOpen ? 'flex' : 'none',
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
