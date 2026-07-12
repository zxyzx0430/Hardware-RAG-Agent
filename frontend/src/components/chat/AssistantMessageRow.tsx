import { memo, useCallback } from "react";
import type { ContentPart, Message, SourceRef } from "../../types/session";
import { useBookmarkStore } from "../../stores/useBookmarkStore";
import { AssistantMessageContent } from "./AssistantMessageContent";
import ActivityBlock from "./ActivityBlock";
import ErrorBlock from "./ErrorBlock";

/** Assistant 消息行 — memo 化，content 不变时跳过 render */
export interface AssistantMessageRowProps {
  msg: Message;
  isCurrentlyStreaming: boolean;
  isStreaming: boolean;
  chatFontSize: number;
  highlightSourceId: string | null;
  bookmarked: boolean;
  copiedMsgId: string | null;
  lastUserMsgId: string;
  pickerMsgId: string | null;
  pickerNewName: string;
  bookmarkFolders: Array<{ id: string; name: string }>;
  streamingSteps: any[];
  streamingStartTime?: number;
  streamingError: { code: string; message: string; detail?: string } | null;
  streamingSources: SourceRef[];
  t: (key: string) => string;
  onSourceClick: (sourceId: string, messageId: string, msgSources?: SourceRef[]) => void;
  onPushCodeToPreview: (code: string, label: string, language?: string) => void;
  onOpenInEditor: (code: string, language?: string) => void;
  onScrollToMessage: (id: string) => void;
  onToggleBookmark: (msgId: string) => void;
  onRetry: (msgId: string) => void;
  onQuote: (msgId: string) => void;
  onCopy: (msgId: string, content: string | ContentPart[]) => void;
  onSetPickerMsgId: (id: string | null) => void;
  onSetPickerNewName: (name: string) => void;
  onAddBookmarkToFolder: (msgId: string, folderId: string) => void;
  onAddBookmarkFolder: (name: string) => void;
}

export const AssistantMessageRow = memo(function AssistantMessageRow(props: AssistantMessageRowProps) {
  const {
    msg,
    isCurrentlyStreaming,
    isStreaming,
    chatFontSize,
    highlightSourceId,
    bookmarked,
    copiedMsgId,
    lastUserMsgId,
    pickerMsgId,
    pickerNewName,
    bookmarkFolders,
    streamingSteps,
    streamingStartTime,
    streamingError,
    streamingSources,
    t,
    onSourceClick,
    onPushCodeToPreview,
    onOpenInEditor,
    onScrollToMessage,
    onToggleBookmark,
    onRetry,
    onQuote,
    onCopy,
    onSetPickerMsgId,
    onSetPickerNewName,
    onAddBookmarkToFolder,
    onAddBookmarkFolder,
  } = props;

  // 稳定 onSourceClick 引用，避免内联箭头每次 render 新建导致 AssistantMessageContent memo 失效
  const handleSourceClick = useCallback((id: string) => {
    onSourceClick(id, msg.id, msg.sources);
  }, [onSourceClick, msg.id, msg.sources]);

  return (
    <div className="msg-row" id={`msg-${msg.id}`} key={msg.id} style={{ fontSize: chatFontSize }}>
      <div className="msg-avatar bot"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg></div>
      <div className="msg-body">
        {msg.activity && msg.activity.steps.length > 0
          ? <ActivityBlock activity={msg.activity} msgId={msg.id} />
          : isCurrentlyStreaming
          ? <ActivityBlock activity={{ durationMs: 0, steps: streamingSteps, status: 'running' }}
              msgId={msg.id} startTime={streamingStartTime || undefined} />
          : null}
        <div className="assistant-text">
          <AssistantMessageContent
            content={msg.content}
            streaming={isCurrentlyStreaming}
            sources={msg.sources}
            onSourceClick={handleSourceClick}
            onPushCodeToPreview={onPushCodeToPreview}
            onOpenInEditor={onOpenInEditor}
          />
        </div>
        {streamingError && msg.id !== '' && (
          <ErrorBlock
            code={streamingError.code}
            message={streamingError.message}
            detail={streamingError.detail}
            onRetry={() => onRetry(msg.id)}
          />
        )}
        {msg.sources && msg.sources.length > 0 && !isCurrentlyStreaming && (
          <div className="src-count-badge">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            <span>本文引用了 {msg.sources.length} 个来源</span>
            {(() => {
              const high = msg.sources.filter((s) => s.relevance_level === 'high').length;
              return high > 0 ? <span className="src-count-detail">· {high} 个高相关</span> : null;
            })()}
          </div>
        )}
        {msg.sources?.length ? (
          <div className="source-refs">
            <span className="source-ref-label">{t('sources')}</span>
            <div className="source-refs-row">
              {[...msg.sources].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).map((src, idx) => (
                <button
                  className={`source-chip${src.relevance_level ? ` rel-${src.relevance_level}` : ''}${highlightSourceId === src.id ? ' highlight' : ''}`}
                  key={src.id}
                  title={src.section_title ? `${src.title} · ${src.section_title}` : src.title}
                  onClick={() => onSourceClick(src.id, msg.id, msg.sources)}
                >
                  <span className="source-chip-num">{idx + 1}</span>
                  <span className="source-chip-title">{src.title}</span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {/* 操作栏：仅在非流式状态时显示 */}
        {!isCurrentlyStreaming && (
          <div className="msg-actions" style={{ position: "relative" }}>
            {lastUserMsgId ? <button className="action-btn" onClick={() => onScrollToMessage(lastUserMsgId)}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>{t('backToQuestion')}</button> : null}
            <button className={`action-btn${bookmarked ? ' bookmarked' : ''}`} onClick={() => {
              if (bookmarked) {
                onToggleBookmark(msg.id);
              } else if (bookmarkFolders.length === 0) {
                onAddBookmarkFolder(t('defaultFolder') || '默认收藏');
                setTimeout(() => onToggleBookmark(msg.id), 50);
              } else if (bookmarkFolders.length === 1) {
                onAddBookmarkToFolder(msg.id, bookmarkFolders[0].id);
              } else {
                onSetPickerMsgId(pickerMsgId === msg.id ? null : msg.id);
                onSetPickerNewName("");
              }
            }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill={bookmarked ? 'var(--warn)' : 'none'} stroke="currentColor" strokeWidth="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
            </button>
            {pickerMsgId === msg.id && bookmarkFolders.length > 1 && (
              <div className="folder-picker" style={{
                position: "absolute", bottom: "100%", left: 0, zIndex: 100,
                background: "var(--card-bg, #fff)", border: "1px solid var(--border)",
                borderRadius: 8, padding: 8, minWidth: 160,
                boxShadow: "0 4px 16px rgba(0,0,0,0.15)",
              }}>
                {bookmarkFolders.map((f) => (
                  <button key={f.id}
                    style={{ display: "block", width: "100%", padding: "6px 10px", border: "none", borderRadius: 4, background: "transparent", cursor: "pointer", fontSize: 12, color: "inherit", textAlign: "left" }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover-bg, rgba(0,0,0,0.06))")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    onClick={() => { onAddBookmarkToFolder(msg.id, f.id); onSetPickerMsgId(null); }}
                  >{f.name}</button>
                ))}
                <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0", paddingTop: 4 }}>
                  <input
                    placeholder={t('newFolderPlaceholder') || "新建文件夹"}
                    value={pickerNewName}
                    onChange={(e) => onSetPickerNewName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && pickerNewName.trim()) {
                        onAddBookmarkFolder(pickerNewName.trim());
                        setTimeout(() => {
                          const folders = useBookmarkStore.getState().bookmarkFolders;
                          const newFolder = folders.find((f) => f.name === pickerNewName.trim());
                          if (newFolder) onAddBookmarkToFolder(msg.id, newFolder.id);
                          onSetPickerMsgId(null);
                          onSetPickerNewName("");
                        }, 50);
                      }
                    }}
                    style={{ width: "100%", padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 4, fontSize: 12, background: "transparent", color: "inherit", boxSizing: "border-box" }}
                    autoFocus
                  />
                </div>
              </div>
            )}
            <button className={`action-btn${copiedMsgId === msg.id ? ' copied' : ''}`} onClick={() => { onCopy(msg.id, msg.content); }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill={copiedMsgId === msg.id ? 'var(--success)' : 'none'} stroke={copiedMsgId === msg.id ? 'var(--success)' : 'currentColor'} strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>{copiedMsgId === msg.id ? '✓ 已复制' : t('copy')}
            </button>
            <button className="action-btn" onClick={() => onRetry(msg.id)} disabled={isStreaming}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>{t('retry')}
            </button>
            <button className="action-btn" onClick={() => onQuote(msg.id)}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z"/><path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z"/></svg>{t('quote')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
});
