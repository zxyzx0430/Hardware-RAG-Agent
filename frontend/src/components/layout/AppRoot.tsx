import { useEffect } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useChatStore } from "../../stores/useChatStore";
import { usePanelResize } from "../../hooks/usePanelResize";
import { RIGHT_PANEL_MIN_WIDTH, RIGHT_PANEL_MAX_WIDTH, CHAT_MIN_WIDTH, EXPLORER_MIN_WIDTH } from "../../stores/appStore/persistence";
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
  } = useAppStore();

  // 启动/刷新时：会话列表加载完成后，加载当前活跃会话的消息；
  // 若 activeSessionId 指向不存在的会话（幽灵 "s1" 或首次进入），切到第一个真实会话。
  const sessionsInitialized = useSessionStore((s) => s.initialized);
  const sessions = useSessionStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const fetchMessages = useChatStore((s) => s.fetchMessages);

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

  const left = usePanelResize(leftPanelWidth, "left", 180, 500, setLeftPanelWidth);
  const right = usePanelResize(
    rightPanelWidth, "right",
    RIGHT_PANEL_MIN_WIDTH, RIGHT_PANEL_MAX_WIDTH,
    setRightPanelWidth, "px",
    () => CHAT_MIN_WIDTH + (explorerOpen ? explorerWidth : 0) + 64
  );
  const explorerMaxWidth = typeof window !== "undefined" ? window.innerWidth / 2 : 960;
  const explorer = usePanelResize(
    explorerWidth, "right",
    EXPLORER_MIN_WIDTH, explorerMaxWidth,
    setExplorerWidth, "px",
    () => CHAT_MIN_WIDTH + rightPanelWidth + 64
  );

  // 窗口缩放时钳制右面板宽度，确保对话区不小于 CHAT_MIN_WIDTH
  useEffect(() => {
    const onResize = () => {
      const windowWidth = window.innerWidth;
      // 动态计算非对话区/右面板占用的固定宽度
      const overhead = leftPanelWidth + (explorerOpen ? explorerWidth : 0) + 64; // IconNav(48) + strips/resizers ~16
      const availableForChatAndRight = Math.max(0, windowWidth - overhead);
      const maxRight = Math.max(RIGHT_PANEL_MIN_WIDTH, availableForChatAndRight - CHAT_MIN_WIDTH);
      const current = useAppStore.getState().rightPanelWidth;
      if (current > maxRight) {
        setRightPanelWidth(Math.floor(maxRight));
      }
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [setRightPanelWidth, leftPanelWidth, explorerOpen, explorerWidth]);

  const showKnowledgePage = activeNav === "knowledge";
  const showBookmarkPage = activeNav === "bookmarks";
  const showChatShell = activeNav === "chat" || activeNav === "settings";
  const showLeftRail = showChatShell && leftPanelOpen;

  return (
    <div className="app-root" id="app" style={{ flexDirection: "column" }}>
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
            onClick={() => setLeftPanelOpen(!leftPanelOpen)}
            aria-label={t('toggleLeftPanel')}
            title={leftPanelOpen ? '折叠左侧面板' : '展开左侧面板'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              {leftPanelOpen
                ? <polyline points="15 18 9 12 15 6" />
                : <polyline points="9 18 15 12 9 6" />}
            </svg>
          </button>
        </div>
        <div className="main-area" id="mainArea" style={{ minWidth: 0 }}>
          <div id="chatFlex" style={{ flex: 1, display: showChatShell ? "flex" : "none", minHeight: 0, overflow: "hidden" }}>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: CHAT_MIN_WIDTH, overflow: "hidden", background: "var(--bg)" }}>
              <ChatArea />
              <InputBar />
            </div>
            <div className={`panel-btn-strip right${showChatShell ? '' : ' hidden'}`} id="rightStrip">
              <button
                className="panel-toggle right"
                id="rightToggleBtn"
                onClick={() => setRightPanelOpen(!rightPanelOpen)}
                aria-label={t('toggleRightPanel')}
                title={rightPanelOpen ? '折叠右侧面板' : '展开右侧面板'}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {rightPanelOpen
                    ? <polyline points="9 18 15 12 9 6" />
                    : <polyline points="15 18 9 12 15 6" />}
                </svg>
              </button>
            </div>
            <div
              className={`right-resizer${showChatShell && rightPanelOpen ? '' : ' hidden'}`}
              id="rightResizer"
              onMouseDown={(e) => {
                right.onMouseDown(e);
              }}
              style={{ cursor: 'col-resize' }}
            />
            <div style={{ width: rightPanelOpen ? `${rightPanelWidth}px` : 0, overflow: 'hidden', display: 'flex', transition: rightPanelOpen ? 'none' : 'width 0.2s' }}>
              <RightPanel />
            </div>

            <div
              className={`explorer-resizer${explorerOpen ? '' : ' hidden'}`}
              id="explorerResizer"
              onMouseDown={(e) => {
                if (!explorerOpen) setExplorerOpen(true);
                explorer.onMouseDown(e);
              }}
              style={{ cursor: 'col-resize' }}
            />
            <div className={`explorer-collapsed-strip${explorerOpen ? ' hidden' : ''}`} id="explorerStrip">
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
              style={{
                width: explorerOpen ? explorerWidth : 0,
                overflow: 'hidden',
                display: 'flex',
                transition: explorerOpen ? 'none' : 'width 0.2s',
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
