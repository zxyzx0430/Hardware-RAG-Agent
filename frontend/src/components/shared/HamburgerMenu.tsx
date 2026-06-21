import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useI18n } from "../../i18n";

interface Props {
  onClose: () => void;
}

export function HamburgerMenu({ onClose }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { themeMode, setThemeMode } = useAppStore();
  const { exportConversation, showStats } = useChatStore();
  const { t } = useI18n();
  const [showExportDialog, setShowExportDialog] = useState(false);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [onClose]);

  return (
    <>
      <div className="dropdown-backdrop" />
      <div className="dropdown-panel" ref={ref} style={{ top: 54, right: 0, width: 224 }}>
        <div className="dropdown-panel-body" style={{ padding: 8 }}>
          <button className="hamburger-item" onClick={() => { setThemeMode(themeMode === 'dark' ? 'light' : themeMode === 'light' ? 'auto' : 'dark'); onClose(); }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
            <span>{themeMode === 'dark' ? t('toggleLight') : themeMode === 'light' ? t('toggleDark') : t('followSystem')}</span>
          </button>

          {/* Export conversation button */}
          <div style={{ position: 'relative' }}>
            <button className="hamburger-item" onClick={() => setShowExportDialog(v => !v)}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              <span>{t('exportConversation')}</span>
            </button>

            {/* Export format selection popup */}
            {showExportDialog && (
              <div style={{
                position: 'absolute',
                left: 0,
                top: '100%',
                zIndex: 10,
                marginTop: 4,
                background: 'var(--bg-secondary, #2a2a2a)',
                border: '1px solid var(--border-primary, #444)',
                borderRadius: 8,
                padding: 8,
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                minWidth: 160,
                boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
              }}>
                <button
                  className="hamburger-item"
                  onClick={() => { exportConversation('markdown'); onClose(); }}
                >
                  <span>{t('markdown')} (.md)</span>
                </button>
                <button
                  className="hamburger-item"
                  onClick={() => { exportConversation('json'); onClose(); }}
                >
                  <span>{t('json')} (.json)</span>
                </button>
                <button
                  className="hamburger-item"
                  onClick={() => setShowExportDialog(false)}
                >
                  <span>{t('cancel')}</span>
                </button>
              </div>
            )}
          </div>

          <button className="hamburger-item" onClick={() => { showStats(); onClose(); }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
            <span>{t('chatStats')}</span>
          </button>
        </div>
      </div>
    </>
  );
}
