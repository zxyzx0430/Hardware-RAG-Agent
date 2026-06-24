import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useChatStore } from "../../stores/useChatStore";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import type { ContentPart } from "../../types/session";
import { MarkdownRenderer} from "../shared/MarkdownRenderer";
import { copyToClipboard } from "../../utils/clipboard";
import { useI18n } from "../../i18n";
import type { ActivityBlock, ActivityStep } from "../../types/session";

function formatDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}


/** 将消息内容转为可渲染字符串：纯文本原样返回，ContentPart[] 拼成 Markdown */
function renderContent(content: string | ContentPart[]): string {
  if (typeof content === "string") return content;
  return content.map((part) => {
    if (part.type === "text") return part.text;
    if (part.type === "image_url") return `![Image](${part.image_url.url})`;
    return "";
  }).join("\n\n");
}

/** 渲染用户消息内容：文本走 MarkdownRenderer，图片直接用 <img> 避免超长 base64 URL 解析问题 */
function UserMessageContent({ content }: { content: string | ContentPart[] }) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  if (typeof content === "string") {
    return <MarkdownRenderer content={content} streaming={false} />;
  }
  // ContentPart[]: 分别渲染文本和图片
  const textParts = content.filter((p) => p.type === "text").map((p) => p.type === "text" ? p.text : "").join("\n\n");
  const imageParts = content.filter((p) => p.type === "image_url");
  // Debug log: trace image count to find duplication root cause
  if (imageParts.length > 0) {
    // eslint-disable-next-line no-console
    console.log("[UserMessageContent] imageParts=", imageParts.length, "content parts=", content.length);
  }
  return (
    <>
      {textParts.trim() && <MarkdownRenderer content={textParts} streaming={false} />}
      {imageParts.map((p, idx) => {
        const imgPart = p as Extract<ContentPart, { type: "image_url" }>;
        return (
          <div key={idx} className="user-image-wrap" style={{ marginTop: 8, cursor: "zoom-in", display: "inline-block" }}>
            <img
              src={imgPart.image_url.url}
              alt="uploaded"
              onClick={() => setLightboxSrc(imgPart.image_url.url)}
              style={{ maxWidth: 320, maxHeight: 320, borderRadius: 8, border: "1px solid var(--border)", display: "block" }}
            />
          </div>
        );
      })}
      {lightboxSrc && (
        <div
          onClick={() => setLightboxSrc(null)}
          style={{
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(0,0,0,0.85)",
            display: "flex", alignItems: "center", justifyContent: "center",
            cursor: "zoom-out",
          }}
        >
          <img src={lightboxSrc} alt="original" style={{ maxWidth: "95vw", maxHeight: "95vh", objectFit: "contain" }} />
        </div>
      )}
    </>
  );
}

export function ChatArea() {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Track whether the user is near the bottom; only auto-scroll when true
  const isAtBottomRef = useRef(true);
  // Track whether user has manually scrolled — user scroll has highest priority
  const userScrolledRef = useRef(false);
  const {
    messages,
    isStreaming,
    streamingContent,
    streamingSteps,
    streamingStartTime,
    quoteMessage,
    toggleBookmark,
    retryMessage,
    editAndResend,
    isBookmarked,
    bookmarkFolders,
    addBookmarkToFolder,
    addBookmarkFolder,
    setBookmarkTargetMsgId,
  } = useChatStore();
  const {
    chatFontSize,
    highlightSourceId,
    setHighlightSourceId,
    setFileViewerSource,
    setRightPanelOpen,
    setRightMode,
    setWbTab,
    addPreviewTab,
  } = useAppStore();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const currentSession = sessions.find((s) => s.id === activeSessionId);
  const parentSession = currentSession?.branchFromSessionId
    ? sessions.find((s) => s.id === currentSession.branchFromSessionId)
    : null;

  // 编辑状态：msgId -> 编辑内容
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  // 复制反馈：最近复制的消息 ID
  const [copiedMsgId, setCopiedMsgId] = useState<string | null>(null);
  // 收藏文件夹选择：正在选择的消息 ID
  const [pickerMsgId, setPickerMsgId] = useState<string | null>(null);
  const [pickerNewName, setPickerNewName] = useState("");

  // 自动滚动策略：
  // 1. 新消息添加时（messages.length 变化）自动滚到底部
  // 2. 流式输出期间（isStreaming）完全不自动滚动，由用户滑轮控制
  // 3. 用户手动滚动后，userScrolledRef 置 true，停止自动滚动直到用户主动回到底部
  useEffect(() => {
    // 只在新消息添加时滚动，不在流式内容更新时滚动
    if (scrollRef.current && !userScrolledRef.current) {
      scrollRef.current.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "auto",  // 用 auto 避免 smooth 动画堆积卡顿
      });
    }
  }, [messages.length]);

  // 流式结束时滚到底部（如果用户没有手动滚动过）
  useEffect(() => {
    if (!isStreaming && scrollRef.current && !userScrolledRef.current) {
      scrollRef.current.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
    // 流式开始时重置 userScrolledRef，允许新的一轮自动滚动
    if (isStreaming) {
      userScrolledRef.current = false;
    }
  }, [isStreaming]);

  // 滑轮事件：用 addEventListener + { passive: true } 确保浏览器原生滚动优先级最高
  // 不用 preventDefault，让浏览器立即响应滑轮
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      // 用户滑轮操作，标记为手动滚动
      userScrolledRef.current = true;
      // 检查是否滚回底部，如果是则清除标记
      const threshold = 80;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
      if (atBottom && e.deltaY > 0) {
        // 向下滚到底部时清除标记
        userScrolledRef.current = false;
      }
    };
    el.addEventListener("wheel", onWheel, { passive: true });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Track scroll position to detect whether user is at the bottom
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const threshold = 80; // px from bottom considered "at bottom"
    isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  };

  const scrollToMessage = (id: string) => {
    const el = document.getElementById(`msg-${id}`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('msg-highlight');
    setTimeout(() => el.classList.remove('msg-highlight'), 1500);
  };

  const pushCodeToPreview = (code: string, label: string, language = 'cpp') => {
    setRightPanelOpen(true);
    setRightMode('workbench');
    setWbTab('preview');
    addPreviewTab({
      id: `preview-${Date.now()}`,
      label: `${label.slice(0, 18) || 'code'}.${language}`,
      code,
      language,
    });
  };

  const startEdit = (msgId: string, content: string) => {
    setEditingId(msgId);
    setEditText(content);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditText("");
  };

  const saveEdit = () => {
    if (editingId && editText.trim()) {
      editAndResend(editingId, editText.trim());
      setEditingId(null);
      setEditText("");
    }
  };


  const lastUserMsgId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') return messages[i].id;
    }
    return '';
  }, [messages]);

  if (!messages.length) return (
    <div className="chat-scroll" id="chatScroll" ref={scrollRef} onScroll={handleScroll} style={{ fontSize: chatFontSize }}>
      <EmptyState />
    </div>
  );

  return (
    <div className="chat-scroll" id="chatScroll" ref={scrollRef} onScroll={handleScroll} style={{ fontSize: chatFontSize }}>
      {/* 分支路径 */}
      {currentSession?.branchFromSessionId && (
        <div style={{ padding: "4px 12px", fontSize: 11, color: "var(--muted-fg)", background: "var(--hover-bg)", display: "flex", alignItems: "center", gap: 4 }}>
          <span>分支自: {parentSession?.title || "主线"}</span>

        </div>
      )}
      {messages.map((msg) => {
        if (msg.role === 'user') {
          const isEditing = editingId === msg.id;
          return (
            <div className="msg-row user" id={`msg-${msg.id}`} key={msg.id}>
              <div className="user-msg-wrap">
                {isEditing ? (
                  <div className="user-bubble editing" style={{ maxWidth: '100%', width: '100%', alignSelf: 'stretch', background: 'transparent', padding: 0 }}>
                    <textarea
                      className="edit-textarea"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveEdit(); }
                      }}
                      style={{ width: '100%', minHeight: 120, maxHeight: 400, borderRadius: 10, background: 'var(--card)', border: '1px solid var(--border)', padding: 12, fontSize: chatFontSize, color: 'var(--fg)', resize: 'vertical' }}
                    />
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                      <button className="edit-action-btn primary" onClick={saveEdit}>{t('saveAndSend')}</button>
                      <button className="edit-action-btn" onClick={cancelEdit}>{t('cancel')}</button>
                    </div>
                  </div>
                ) : (
                  <div className="user-bubble" id={`userBubble-${msg.id}`}>
                    <UserMessageContent content={msg.content} />
                  </div>
                )}
              </div>
              {!isEditing && <button className="msg-edit-trigger" onClick={() => startEdit(msg.id, renderContent(msg.content))}>{t('edit')}</button>}
              <div className="msg-avatar user"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--muted-fg)' }}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>
            </div>
          );
        }

        const bookmarked = isBookmarked(msg.id);
        // 判断该消息是否正在流式输出（最后一条 assistant 消息且正在 streaming）
        const isCurrentlyStreaming = isStreaming && msg.id === messages[messages.length - 1]?.id;

        return (
          <div className="msg-row" id={`msg-${msg.id}`} key={msg.id}>
            <div className="msg-avatar bot"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)' }}><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg></div>
            <div className="msg-body">
              {msg.activity && msg.activity.steps.length > 0
                ? <ActivityBlock activity={msg.activity} msgId={msg.id} />
                : isCurrentlyStreaming && streamingSteps.length > 0
                  ? <ActivityBlock activity={{ durationMs: 0, steps: streamingSteps, status: 'running' }} msgId={msg.id} startTime={streamingStartTime || undefined} />
                  : null}
              <div className="assistant-text">
                <MarkdownRenderer content={renderContent(msg.content)} streaming={isCurrentlyStreaming} />
              </div>
              {msg.sources?.length ? (
                <div className="source-refs">
                  <span className="source-ref-label">{t('sources')}</span>
                  <div className="source-refs-row">
                    {msg.sources.map((src) => (
                      <button
                        className={`source-chip${highlightSourceId === src.id ? ' highlight' : ''}`}
                        key={src.id}
                        onClick={() => {
                          setHighlightSourceId(src.id);
                          setRightPanelOpen(true);
                          setRightMode('content');
                          setFileViewerSource(src.id);
                        }}
                      >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)' }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        {src.kb_name ? <span className="source-chip-kb">{src.kb_name}</span> : null}
                        <span>{src.title}</span>
                        <span className="source-chip-score">{(src.score * 100).toFixed(0)}%</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              {/* 操作栏：仅在非流式状态时显示 */}
              {!isCurrentlyStreaming && (
                <div className="msg-actions" style={{ position: "relative" }}>
                  {lastUserMsgId ? <button className="action-btn" onClick={() => scrollToMessage(lastUserMsgId)}><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>{t('backToQuestion')}</button> : null}
                  <button className={`action-btn${bookmarked ? ' bookmarked' : ''}`} onClick={(e) => {
                    if (bookmarked) {
                      toggleBookmark(msg.id);
                    } else if (bookmarkFolders.length === 0) {
                      // No folders: create default, then bookmark
                      addBookmarkFolder(t('defaultFolder') || '默认收藏');
                      setTimeout(() => toggleBookmark(msg.id), 50);
                    } else if (bookmarkFolders.length === 1) {
                      // One folder: bookmark directly
                      addBookmarkToFolder(msg.id, bookmarkFolders[0].id);
                    } else {
                      // Multiple folders: show picker near the button
                      setPickerMsgId(pickerMsgId === msg.id ? null : msg.id);
                      setPickerNewName("");
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
                          onClick={() => { addBookmarkToFolder(msg.id, f.id); setPickerMsgId(null); }}
                        >{f.name}</button>
                      ))}
                      <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0", paddingTop: 4 }}>
                        <input
                          placeholder={t('newFolderPlaceholder') || "新建文件夹"}
                          value={pickerNewName}
                          onChange={(e) => setPickerNewName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && pickerNewName.trim()) {
                              addBookmarkFolder(pickerNewName.trim());
                              setTimeout(() => {
                                const folders = useChatStore.getState().bookmarkFolders;
                                const newFolder = folders.find((f) => f.name === pickerNewName.trim());
                                if (newFolder) addBookmarkToFolder(msg.id, newFolder.id);
                                setPickerMsgId(null);
                                setPickerNewName("");
                              }, 50);
                            }
                          }}
                          style={{ width: "100%", padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 4, fontSize: 12, background: "transparent", color: "inherit", boxSizing: "border-box" }}
                          autoFocus
                        />
                      </div>
                    </div>
                  )}
                  <button className={`action-btn${copiedMsgId === msg.id ? ' copied' : ''}`} onClick={() => { copyToClipboard(renderContent(msg.content)); setCopiedMsgId(msg.id); setTimeout(() => setCopiedMsgId(null), 2000); }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill={copiedMsgId === msg.id ? 'var(--success)' : 'none'} stroke={copiedMsgId === msg.id ? 'var(--success)' : 'currentColor'} strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>{copiedMsgId === msg.id ? '✓ 已复制' : t('copy')}
                  </button>
                  <button className="action-btn" onClick={() => retryMessage(msg.id)} disabled={isStreaming}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>{t('retry')}
                  </button>
                  <button className="action-btn" onClick={() => quoteMessage(msg.id)}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z"/><path d="M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z"/></svg>{t('quote')}
                  </button>




                </div>
              )}
              {(() => {
                const contentStr = renderContent(msg.content);
                const match = contentStr.match(/```([a-zA-Z0-9+#-]*)\n([\s\S]*?)```/);
                if (!match) return null;
                const language = match[1].trim().toLowerCase() || 'cpp';
                const code = match[2];
                return (
                  <div className="tool-output-actions" style={{ marginTop: 10 }}>
                    <button className="tool-output-btn" onClick={() => pushCodeToPreview(code, `assistant-code-${language}`, language)}>{t('pushToPreviewShort')}</button>
                  </div>
                );
              })()}
            </div>
          </div>
        );
      })}

    </div>
  );
}

function EmptyState() {
  const { t } = useI18n();
  const sendMessage = useChatStore((s) => s.sendMessage);
  const suggestions = [
    t('suggest1'),
    t('suggest2'),
    t('suggest3'),
    t('suggest4'),
  ];
  return (
    <div className="empty-state">
      <div className="empty-icon"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg></div>
      <div style={{ textAlign:'center' }}><p className="empty-title">{t('emptyTitle')}</p><p className="empty-desc">{t('emptyDesc')}</p></div>
      <div className="empty-grid">{suggestions.map((s) => <button className="empty-suggestion" key={s} onClick={() => sendMessage(s)}>{s}</button>)}</div>
    </div>
  );
}

function ActivityBlock({ activity, msgId, startTime }: { activity: ActivityBlock; msgId: string; startTime?: number }) {
  const { t } = useI18n();
  const stepCount = activity.steps.length;
  const [collapsed, setCollapsed] = useState(true);
  const isRunning = activity.status === 'running';

  // 实时计时：流式时用 startTime 动态计算，完成后用 durationMs
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    const timer = setInterval(() => {
      setElapsed(Date.now() - (startTime || Date.now()));
    }, 200);
    return () => clearInterval(timer);
  }, [isRunning, startTime]);

  const displayDuration = isRunning ? elapsed : (activity.durationMs || 0);

  return (
    <div className={`activity-block${isRunning ? ' running' : ''}`} id={`act-${msgId}`}>
      <button className={`activity-header${collapsed ? ' collapsed' : ''}`} onClick={() => setCollapsed((v) => !v)}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="activity-chevron"><polyline points="6 9 12 15 18 9"/></svg>
        <span className="activity-header-label">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="svg-accent" style={{ flexShrink: 0, marginRight: 4 }}><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          {t('activityLabel')}: {stepCount} {t('tools')}
        </span>
        <span className="activity-header-duration">
          {isRunning ? <span className="activity-spinner" /> : null}
          {isRunning ? '' : t('doneIn') + ' '}{formatDuration(displayDuration)}
        </span>
      </button>
      {!collapsed ? (
        <div className="activity-steps" id={`act-body-${msgId}`}>
          <div className="activity-chain">
            {activity.steps.map((step, idx) => {
              const isLast = idx === activity.steps.length - 1;
              // Stable key: source/type + index avoids remount when step.id regenerates
              const stepKey = `${step.source ?? step.type}-${idx}`;
              if (step.type === 'thinking') {
                return (
                  <div className="activity-chain-node" key={stepKey}>
                    <ThinkingStep step={step} />
                    {!isLast ? <div className="activity-chain-line" /> : null}
                  </div>
                );
              }
              return (
                <div className="activity-chain-node" key={stepKey}>
                  <ToolStep step={step} running={isRunning} />
                  {!isLast ? <div className="activity-chain-line" /> : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ThinkingStep({ step }: { step: ActivityStep }) {
  const isReasoning = step.source === 'reasoning';
  const label = isReasoning ? '推理思考' : step.source === 'rag' ? '知识库检索' : '思考中';
  // 推理思考默认展开，其他默认折叠
  const [open, setOpen] = useState(isReasoning);
  return (
    <div className={`thinking-row${isReasoning ? ' reasoning' : ''}`}>
      <button className="thinking-header" onClick={() => setOpen((v) => !v)}>
        <span className={`thinking-icon${isReasoning ? ' reasoning' : ''}`}>
          {isReasoning
            ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>
            : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
          }
        </span>
        <span className="thinking-header-label">
          <span className="thinking-label-tag">{label}</span>
          {!open && step.content ? step.content.slice(0, 40) + (step.content.length > 40 ? '…' : '') : ''}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="step-chevron" style={{ transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'none' }}><polyline points="9 18 15 12 9 6"/></svg>
      </button>
      {open ? (
        <div className="thinking-body">
          <p>{step.content}</p>
        </div>
      ) : null}
    </div>
  );
}

function ToolStep({ step, running }: { step: ActivityStep; running?: boolean }) {
  const [open, setOpen] = useState(false);
  const stepStatus = step.status || (running ? 'running' : 'done');
  return (
    <div className={`tool-row${stepStatus === 'running' ? ' running' : stepStatus === 'error' ? ' error' : ''}`}>
      <button className="tool-header" onClick={() => setOpen((v) => !v)}>
        <span className="tool-icon">
          <ToolIcon name={step.name} />
        </span>
        <span className="tool-name">{step.name || 'Tool'}</span>
        {step.args ? <span className="tool-args-preview">{typeof step.args === 'string' ? step.args.slice(0, 40) + (step.args.length > 40 ? '…' : '') : JSON.stringify(step.args).slice(0, 40) + '…'}</span> : null}
        {step.duration ? <span className="tool-duration">{formatDuration(step.duration)}</span> : null}
        {stepStatus === 'running' ? <span className="tool-spinner" /> : null}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="step-chevron" style={{ transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'none' }}><polyline points="9 18 15 12 9 6"/></svg>
      </button>
      {open ? (
        <div className="tool-body">
          {step.args ? (
            <div className="tool-body-section">
              <span className="tool-body-label">Input</span>
              <pre className="tool-body-code">{typeof step.args === 'string' ? step.args : JSON.stringify(step.args, null, 2)}</pre>
            </div>
          ) : null}
          {step.result ? (
            <div className="tool-body-section">
              <span className="tool-body-label">Output</span>
              <pre className="tool-body-code">{step.result}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// Tool icon mapping table: keyword groups → icon. Add new tools by extending this map.
const TOOL_ICON_KEYWORDS: Array<{ key: string; keywords: string[] }> = [
  { key: "search", keywords: ["search", "query", "retriev"] },
  { key: "code", keywords: ["code", "compile", "flash", "build"] },
  { key: "document", keywords: ["datasheet", "doc", "pdf", "spec"] },
  { key: "serial", keywords: ["serial", "uart", "connect"] },
  { key: "signal", keywords: ["analyz", "debug", "signal", "oscilloscope"] },
];

const TOOL_ICONS: Record<string, ReactNode> = {
  search: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
  code: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>,
  document: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>,
  serial: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="7" width="20" height="10" rx="2"/><path d="M6 12h4M14 12h4"/></svg>,
  signal: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12h4l3-9 4 18 3-9h6"/></svg>,
};

const DEFAULT_TOOL_ICON = <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>;

/** 根据工具名称返回对应图标（基于关键字映射表匹配） */
function ToolIcon({ name }: { name?: string }) {
  const n = (name || "").toLowerCase();
  for (const rule of TOOL_ICON_KEYWORDS) {
    if (rule.keywords.some((kw) => n.includes(kw))) {
      return TOOL_ICONS[rule.key] || DEFAULT_TOOL_ICON;
    }
  }
  return DEFAULT_TOOL_ICON;
}
