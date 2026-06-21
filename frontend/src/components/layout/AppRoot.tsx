import { useAppStore } from "../../stores/useAppStore";
import { usePanelResize } from "../../hooks/usePanelResize";
import { IconNav } from "./IconNav";
import { LeftPanel } from "./LeftPanel";
import { TopBar } from "../topbar/TopBar";
import { ChatArea } from "../chat/ChatArea";
import { InputBar } from "../input/InputBar";
import { RightPanel } from "./RightPanel";
import { SettingsPage } from "../settings/SettingsPage";
import { KnowledgePanel } from "../knowledge/KnowledgePanel";
import { BookmarkPanel } from "../bookmarks/BookmarkPanel";
import { StatsPanel } from "../shared/StatsPanel";
import { SearchModal } from "../shared/SearchModal";
import { SnapshotPanel } from "../shared/SnapshotPanel";
import { ShortcutHelp } from "../chat/ShortcutHelp";

export function AppRoot() {
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
  } = useAppStore();

  const left = usePanelResize(leftPanelWidth, "left", 180, 500, setLeftPanelWidth);
  const right = usePanelResize(rightPanelWidth, "right", 200, 600, setRightPanelWidth);

  const showKnowledgePage = activeNav === "knowledge";
  const showBookmarkPage = activeNav === "bookmarks";
  const showChatShell = activeNav === "chat" || activeNav === "settings";
  const showLeftRail = showChatShell && leftPanelOpen;

  return (
    <div className="app-root" id="app">
      <IconNav />
      <LeftPanel />
      <div
        className={`sidebar-resizer${showLeftRail ? '' : ' hidden'}`}
        id="sidebarResizer"
        onMouseDown={left.onMouseDown}
        style={{ cursor: 'col-resize' }}
      />
      <div className={`panel-btn-strip left${showLeftRail ? '' : ' hidden'}`} id="leftStrip">
        <button className="panel-toggle left" id="leftToggleBtn" onClick={() => setLeftPanelOpen(!leftPanelOpen)}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      </div>
      <div className="main-area" id="mainArea" style={{ minWidth: 0 }}>
        {showChatShell ? <TopBar /> : null}

        {showChatShell ? (
          <div id="chatFlex" style={{ flex: 1, display: "flex", minHeight: 0, overflow: "hidden" }}>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 280, overflow: "hidden", background: "var(--bg)" }}>
              <ChatArea />
              <InputBar />
            </div>
            <div className={`panel-btn-strip right${rightPanelOpen ? '' : ' hidden'}`} id="rightStrip">
              <button className="panel-toggle right" id="rightToggleBtn" onClick={() => setRightPanelOpen(!rightPanelOpen)}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </button>
            </div>
            <div
              className={`right-resizer${rightPanelOpen ? '' : ' hidden'}`}
              id="rightResizer"
              onMouseDown={right.onMouseDown}
              style={{ cursor: 'col-resize' }}
            />
            <div style={{ width: rightPanelOpen ? rightPanelWidth : 0, overflow: 'hidden', display: 'flex', transition: rightPanelOpen ? 'none' : 'width 0.2s' }}>
              {rightPanelOpen ? <RightPanel /> : null}
            </div>
          </div>
        ) : null}

        {showKnowledgePage ? <KnowledgePanel /> : null}
        {showBookmarkPage ? <BookmarkPanel /> : null}
      </div>
      {activeNav === "settings" && <SettingsPage />}
      <StatsPanel />
      <SearchModal />
      {snapshotPanelOpen && <SnapshotPanel />}
      {shortcutHelpOpen && <ShortcutHelp onClose={() => setShortcutHelpOpen(false)} />}
    </div>
  );
}
