import { useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { HamburgerMenu } from "../shared/HamburgerMenu";
import { useI18n } from "../../i18n";

export function TopBar() {
  const { t } = useI18n();
  const { activeNav, rightPanelOpen, setRightPanelOpen, setRightMode } = useAppStore();
  const { messages, activeSessionId } = useChatStore();
  const { sessions } = useSessionStore();
  const [hamburgerOpen, setHamburgerOpen] = useState(false);

  if (activeNav !== "chat") return null;

  // 优先用 sessions 中按 activeSessionId 查到的 title
  const sessionTitleFromStore = sessions.find((s) => s.id === activeSessionId)?.title;

  // fallback: 从 messages 第一条 user 消息派生（用于旧会话无 title 的情况）
  const firstUserContent = messages.find((m) => m.role === "user")?.content;
  const fallbackTitleStr = typeof firstUserContent === "string"
    ? firstUserContent
    : Array.isArray(firstUserContent)
      ? firstUserContent.map((p) => (p.type === "text" ? p.text : "")).join("")
      : "";
  const fallbackTitle = fallbackTitleStr.slice(0, 32);

  const sessionTitle = sessionTitleFromStore || fallbackTitle || t('productName');

  // 来源徽章：取最后一条 assistant 消息的 sources（消息级，切换会话自动归零）
  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const sourceCount = lastAssistant?.sources?.length ?? 0;

  return (
    <div className="topbar" id="topbar">
      <div className="topbar-left-spacer" />

      <div className="topbar-center">
        <span className="topbar-title" id="topbarTitle">{sessionTitle}</span>
      </div>

      <div className="topbar-right">
        <div className={`kb-badge${sourceCount ? "" : " hidden"}`} id="kbBadge">
          <span className="kb-badge-dot" id="kbBadgeDot" />
          <span className="kb-badge-label" id="kbBadgeLabel">{sourceCount} {t('sourceCount')}</span>
        </div>
        <div className="hamburger-wrap" id="hamburgerWrap" style={{ position: 'relative' }}>
          <button className="topbar-btn" id="hamburgerBtn" onClick={() => setHamburgerOpen((v) => !v)} aria-label={t('openMenu')} title={t('openMenu')}>
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
