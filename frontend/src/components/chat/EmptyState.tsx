import { useI18n } from "../../i18n";
import { useChatStore } from "../../stores/useChatStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useAppStore } from "../../stores/useAppStore";

export function EmptyState() {
  const { t } = useI18n();
  const setDraft = useChatStore((s) => s.setDraft);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const providers = useSettingsStore((s) => s.providers);
  const hasAnyKey = providers.some((p) => p.verified);
  const suggestions = [
    t('suggest1'),
    t('suggest2'),
    t('suggest3'),
    t('suggest4'),
  ];
  if (!hasAnyKey) {
    return (
      <div className="empty-state">
        <div className="empty-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3"/></svg></div>
        <div style={{ maxWidth: 420, width: '100%', padding: 24, borderRadius: 12, border: '1px solid var(--border)', background: 'var(--card)', textAlign: 'center', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
          <p className="empty-title" style={{ fontSize: 15 }}>{t('setupApiKeyTitle')}</p>
          <p className="empty-desc" style={{ marginTop: 6, marginBottom: 16 }}>{t('setupApiKeyDesc')}</p>
          <button
            onClick={() => useAppStore.getState().setActiveNav('settings')}
            style={{ padding: '8px 20px', borderRadius: 8, background: 'var(--primary)', color: 'var(--primary-fg)', fontSize: 13, fontWeight: 500, transition: 'opacity 0.15s' }}
            onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.9')}
            onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
          >
            {t('goToSettings')}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="empty-state">
      <div className="empty-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg></div>
      <div style={{ textAlign:'center' }}><p className="empty-title">{t('emptyTitle')}</p><p className="empty-desc">{t('emptyDesc')}</p></div>
      <div className="empty-grid">{suggestions.map((s) => <button className="empty-suggestion" key={s} onClick={() => setDraft(activeSessionId, s)}>{s}</button>)}</div>
    </div>
  );
}
