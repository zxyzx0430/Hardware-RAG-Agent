import { memo, useCallback } from "react";
import type { ContentPart, Message } from "../../types/session";
import { renderMessageContent as renderContent } from "../../utils/content";
import { UserMessageContent } from "./UserMessageContent";
import { copyToClipboard } from "../../utils/clipboard";
import { useToastStore } from "../../stores/useToastStore";

/** 用户消息行 — memo 化，content 不变时跳过 render */
export interface UserMessageRowProps {
  msg: Message;
  chatFontSize: number;
  isEditing: boolean;
  editText: string;
  onEditChange: (text: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onStartEdit: (msgId: string, content: string) => void;
  t: (key: string) => string;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  const MM = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${MM}-${dd} ${hh}:${mm}`;
}

export const UserMessageRow = memo(function UserMessageRow({
  msg,
  chatFontSize,
  isEditing,
  editText,
  onEditChange,
  onSaveEdit,
  onCancelEdit,
  onStartEdit,
  t,
}: UserMessageRowProps) {
  const showSuccess = useCallback((message: string) => {
    useToastStore.getState().showSuccess(message);
  }, []);

  const handleCopy = useCallback(async () => {
    const text = renderContent(msg.content);
    const ok = await copyToClipboard(text);
    if (ok) showSuccess(t('copySuccess', '已复制'));
  }, [msg.content, showSuccess, t]);

  return (
    <div className="msg-row user" id={`msg-${msg.id}`} key={msg.id}>
      <div className="user-msg-wrap">
        {isEditing ? (
          <div className="user-bubble editing" style={{ maxWidth: '100%', width: '100%', alignSelf: 'stretch', background: 'transparent', padding: 0 }}>
            <textarea
              className="edit-textarea"
              value={editText}
              onChange={(e) => onEditChange(e.target.value)}
              onKeyDown={(e) => {
                // Enter 发送，Shift+Enter 默认换行，ESC 取消编辑
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSaveEdit(); }
                if (e.key === "Escape") { e.preventDefault(); onCancelEdit(); }
              }}
              style={{ width: '100%', minHeight: 120, maxHeight: 400, borderRadius: 10, background: 'var(--card)', border: '1px solid var(--border)', padding: 12, fontSize: chatFontSize, color: 'var(--fg)', resize: 'vertical' }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="edit-action-btn primary" onClick={onSaveEdit}>{t('saveAndSend')}</button>
              <button className="edit-action-btn" onClick={onCancelEdit}>{t('cancel')}</button>
            </div>
          </div>
        ) : (
          <>
            <div className="user-bubble" id={`userBubble-${msg.id}`}>
              <UserMessageContent content={msg.content} />
            </div>
            <div className="user-msg-actions">
              <button className="action-btn" onClick={() => onStartEdit(msg.id, renderContent(msg.content))} title={t('edit')}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                <span>{t('edit')}</span>
              </button>
              <button className="action-btn" onClick={handleCopy} title={t('copy')}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                <span>{t('copy')}</span>
              </button>
              <span className="user-msg-time">{formatTime(msg.timestamp)}</span>
            </div>
          </>
        )}
      </div>
      <div className="msg-avatar user"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>
    </div>
  );
});
