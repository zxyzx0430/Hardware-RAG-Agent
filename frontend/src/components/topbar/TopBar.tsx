import { useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { HamburgerMenu } from "../shared/HamburgerMenu";
import { useI18n } from "../../i18n";

export function TopBar() {
  const { t } = useI18n();
  const { activeNav, rightPanelOpen, setRightPanelOpen, setRightMode, snapshotPanelOpen, setSnapshotPanelOpen } = useAppStore();
  const { messages, sources } = useChatStore();
  const [hamburgerOpen, setHamburgerOpen] = useState(false);

  if (activeNav !== "chat") return null;

  // content may be string or ContentPart[]; extract a safe string title
  const firstUserContent = messages.find((m) => m.role === "user")?.content;
  const titleStr = typeof firstUserContent === "string"
    ? firstUserContent
    : Array.isArray(firstUserContent)
      ? firstUserContent.map((p) => (p.type === "text" ? p.text : "")).join("")
      : "";
  const sessionTitle = titleStr.slice(0, 32) || "STM32 I2C 通信问题排查";

  return (
    <div className="topbar" id="topbar">
      <div className="topbar-left">
        <span className="topbar-title" id="topbarTitle">{sessionTitle}</span>
        <div className={`kb-badge${sources.length ? "" : " hidden"}`} id="kbBadge">
          <span className="kb-badge-dot" id="kbBadgeDot" />
          <span className="kb-badge-label" id="kbBadgeLabel">{sources.length} {t('sourceCount')}</span>
        </div>
      </div>

      <div className="topbar-right">
        <button
          className={`topbar-btn${snapshotPanelOpen ? " active" : ""}`}
          id="snapshotToggleBtn"
          onClick={() => setSnapshotPanelOpen(!snapshotPanelOpen)}
          title={t('snapshotBtn')}
        >
          📷
          <span>{t('snapshotBtn')}</span>
        </button>
        <div className="topbar-divider" />
        <button
          className={`topbar-btn${rightPanelOpen ? " active" : ""}`}
          id="sourceToggleBtn"
          onClick={() => {
            if (!rightPanelOpen) setRightPanelOpen(true);
            setRightMode("content");
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="2" y="4" width="20" height="16" rx="2" />
            <line x1="9" y1="4" x2="9" y2="20" />
          </svg>
          <span>{t('sourcePanel')}</span>
        </button>
        <div className="topbar-divider" />
        <div className="hamburger-wrap" id="hamburgerWrap" style={{ position: 'relative' }}>
          <button className="topbar-btn" id="hamburgerBtn" onClick={() => setHamburgerOpen((v) => !v)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="4" y1="12" x2="20" y2="12" />
              <line x1="4" y1="18" x2="20" y2="18" />
            </svg>
          </button>
          {hamburgerOpen && <HamburgerMenu onClose={() => setHamburgerOpen(false)} />}
        </div>
      </div>
    </div>
  );
}
