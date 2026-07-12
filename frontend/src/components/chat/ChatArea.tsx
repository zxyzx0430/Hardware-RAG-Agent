import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useChatStore } from "../../stores/useChatStore";
import { useBookmarkStore } from "../../stores/useBookmarkStore";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import type { ContentPart, Message, SourceRef } from "../../types/session";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";
import { copyToClipboard } from "../../utils/clipboard";
import { useI18n } from "../../i18n";
import { renderMessageContent as renderContent } from "../../utils/content";
import ActivityBlock from "./ActivityBlock";
import ConfirmDialog from "./ConfirmDialog";
import { UserMessageRow } from "./UserMessageRow";
import { AssistantMessageRow } from "./AssistantMessageRow";
import { LoadingState } from "./LoadingState";
import { EmptyState } from "./EmptyState";

// 模块级常量：历史消息传这些稳定引用，避免 memo 浅比较失效。
// 流式 props（streamingSteps 等）每 token 都变，若传给所有消息会导致所有 memo 失效。
const EMPTY_STEPS: any[] = [];
const EMPTY_SOURCES: SourceRef[] = [];

// 滚动相关常量（避免魔法数字）
const SCROLL_BOTTOM_THRESHOLD = 80;     // 判定"贴底"的像素阈值
const RESIZE_FALLBACK_WINDOW_MS = 2000; // 图片加载后补滚的兜底窗口时长

export function ChatArea() {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Track whether the user is near the bottom; only auto-scroll when true
  const isAtBottomRef = useRef(true);
  // Track whether user has manually scrolled — user scroll has highest priority
  const userScrolledRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  // 滚动位置缓存：Map<sessionId, scrollTop>，切会话时存旧取新
  const scrollPosCacheRef = useRef<Map<string, number>>(new Map());
  // 上一次的 activeSessionId，用于区分"切会话"和"同会话新消息"
  const prevSessionIdRef = useRef<string | null>(null);
  // ResizeObserver 兜底：监听 scrollHeight 变化（图片加载后补滚）
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const resizeFallbackTimerRef = useRef<number | null>(null);
  const lastScrollHeightRef = useRef<number>(0);
  // 当前 activeSessionId 的实时引用（rAF closure 读取，避免切会话时写错 cache）
  const activeSessionIdRef = useRef<string | null>(null);
  // 待执行的 rAF id 列表（切会话时取消，避免旧 rAF 把新会话强制滚到底）
  const pendingRafsRef = useRef<number[]>([]);

  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const isLoadingMessages = useChatStore((s) => s.isLoadingMessages);
  const streamingContent = useChatStore((s) => s.streamingContent);
  const streamingSteps = useChatStore((s) => s.streamingSteps);
  const streamingStartTime = useChatStore((s) => s.streamingStartTime);
  const streamingError = useChatStore((s) => s.streamingError);
  const isCompressing = useChatStore((s) => s.isCompressing);
  const compressingMessage = useChatStore((s) => s.compressingMessage);
  const streamingSources = useChatStore((s) => s.streamingSources);
  const quoteMessage = useChatStore((s) => s.quoteMessage);
  const retryMessage = useChatStore((s) => s.retryMessage);
  const editAndResend = useChatStore((s) => s.editAndResend);

  const toggleBookmark = useBookmarkStore((s) => s.toggleBookmark);
  const isBookmarked = useBookmarkStore((s) => s.isBookmarked);
  const bookmarkFolders = useBookmarkStore((s) => s.bookmarkFolders);
  const addBookmarkToFolder = useBookmarkStore((s) => s.addBookmarkToFolder);
  const addBookmarkFolder = useBookmarkStore((s) => s.addBookmarkFolder);
  const setBookmarkTargetMsgId = useBookmarkStore((s) => s.setBookmarkTargetMsgId);

  const chatFontSize = useAppStore((s) => s.chatFontSize);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const setRightMode = useAppStore((s) => s.setRightMode);
  const setWbTab = useAppStore((s) => s.setWbTab);
  const addPreviewTab = useAppStore((s) => s.addPreviewTab);

  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setSessionHighlightSourceId = useChatStore((s) => s.setSessionHighlightSourceId);
  const setSessionFileViewerSource = useChatStore((s) => s.setSessionFileViewerSource);
  const sessionHighlightSourceId = useChatStore((s) => s.sessionHighlightSourceId);
  // 每次 render 同步更新 ref，供 rAF closure 读取实时值（避免切会话时写错 cache）
  activeSessionIdRef.current = activeSessionId;
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

  // —— 滚动辅助函数 ——
  // 取消所有待执行的 rAF（切会话或卸载时调用，避免旧 rAF 把新会话强制滚到底）
  const cancelPendingRafs = useCallback(() => {
    pendingRafsRef.current.forEach((id) => cancelAnimationFrame(id));
    pendingRafsRef.current = [];
  }, []);

  // 滚到底部并同步 isAtBottomRef 与位置缓存（用 ref 读实时 sessionId 避免写错 cache）
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    isAtBottomRef.current = true;
    const sid = activeSessionIdRef.current;
    if (sid) {
      scrollPosCacheRef.current.set(sid, el.scrollTop);
    }
  }, []);

  // ResizeObserver 回调：scrollHeight 变化时补滚到底（图片加载兜底）
  const handleScrollHeightChange = useCallback(() => {
    const el = scrollRef.current;
    if (!el || el.scrollHeight === lastScrollHeightRef.current) return;
    lastScrollHeightRef.current = el.scrollHeight;
    el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
  }, []);

  // 停止 ResizeObserver 兜底（窗口超时或组件卸载时调用）
  const stopResizeFallback = useCallback(() => {
    if (resizeFallbackTimerRef.current !== null) {
      clearTimeout(resizeFallbackTimerRef.current);
      resizeFallbackTimerRef.current = null;
    }
    resizeObserverRef.current?.disconnect();
    resizeObserverRef.current = null;
  }, []);

  // 启动 ResizeObserver 兜底：在 RESIZE_FALLBACK_WINDOW_MS 内监听 scrollHeight 变化
  const startResizeFallback = useCallback(() => {
    stopResizeFallback();
    const el = scrollRef.current;
    if (!el) return;
    lastScrollHeightRef.current = el.scrollHeight;
    const observer = new ResizeObserver(handleScrollHeightChange);
    observer.observe(el);
    resizeObserverRef.current = observer;
    resizeFallbackTimerRef.current = window.setTimeout(stopResizeFallback, RESIZE_FALLBACK_WINDOW_MS);
  }, [stopResizeFallback, handleScrollHeightChange]);

  // 双帧 rAF 延迟滚动：等 DOM 渲染完成后再滚，避免 scrollHeight 还是旧值；
  // 跟踪 rAF id 以便切会话时取消，防止旧 rAF 覆盖新会话恢复的位置
  const scrollToBottomAfterRender = useCallback(() => {
    const id1 = requestAnimationFrame(() => {
      const id2 = requestAnimationFrame(() => {
        pendingRafsRef.current = pendingRafsRef.current.filter((id) => id !== id1 && id !== id2);
        scrollToBottom();
        startResizeFallback();
      });
      pendingRafsRef.current.push(id2);
    });
    pendingRafsRef.current.push(id1);
  }, [scrollToBottom, startResizeFallback]);

  // 恢复滚动位置：先取消旧 rAF 避免覆盖，再恢复缓存位置或滚到底
  const restoreScrollPosition = useCallback((sessionId: string) => {
    cancelPendingRafs();
    const cached = scrollPosCacheRef.current.get(sessionId);
    const el = scrollRef.current;
    if (cached !== undefined && el) {
      el.scrollTop = cached;
      isAtBottomRef.current = el.scrollHeight - cached - el.clientHeight < SCROLL_BOTTOM_THRESHOLD;
      startResizeFallback();
    } else {
      scrollToBottomAfterRender();
    }
  }, [cancelPendingRafs, scrollToBottomAfterRender, startResizeFallback]);

  // 自动滚动策略：
  // 1. 切会话：恢复缓存位置（曾访问过）或滚到底（首次进入）
  // 2. 同会话新消息：流式中或用户已手动滚动则不滚，否则滚到底
  // 3. 流式输出期间完全不自动滚动，由用户滑轮控制（保留原有优化）
  useEffect(() => {
    const prevId = prevSessionIdRef.current;
    const isSessionSwitch = prevId !== activeSessionId;
    prevSessionIdRef.current = activeSessionId;
    if (!scrollRef.current || !messages.length) return;
    if (isSessionSwitch) {
      restoreScrollPosition(activeSessionId);
      return;
    }
    if (isStreaming || userScrolledRef.current) return;
    scrollToBottomAfterRender();
  }, [messages, activeSessionId, isStreaming, restoreScrollPosition, scrollToBottomAfterRender]);

  // 切换会话时重置用户滚动状态，避免旧会话状态污染新会话
  useEffect(() => {
    userScrolledRef.current = false;
    isAtBottomRef.current = true;
  }, [activeSessionId]);

  // 组件卸载时清理 rAF、ResizeObserver 与兜底定时器
  useEffect(() => {
    return () => {
      cancelPendingRafs();
      stopResizeFallback();
    };
  }, [cancelPendingRafs, stopResizeFallback]);

  // 流式开始时重置 userScrolledRef
  useEffect(() => {
    if (isStreaming) {
      userScrolledRef.current = false;
    }
  }, [isStreaming]);

  // 滑轮事件：passive listener 确保浏览器原生滚动优先级最高
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      userScrolledRef.current = true;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_BOTTOM_THRESHOLD;
      if (atBottom && e.deltaY > 0) {
        userScrolledRef.current = false;
      }
    };
    el.addEventListener("wheel", onWheel, { passive: true });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // onScroll rAF 化：避免每个滚动事件都触发 setState 抢主线程；
  // 同时持续缓存当前会话 scrollTop，切会话时直接读取无需主动存。
  // 用 ref 读实时 sessionId，避免 closure 持有旧值导致切会话后写错 cache
  const handleScroll = useCallback(() => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const el = scrollRef.current;
      const sid = activeSessionIdRef.current;
      if (!el || !sid) return;
      isAtBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_BOTTOM_THRESHOLD;
      scrollPosCacheRef.current.set(sid, el.scrollTop);
    });
  }, []);

  const scrollToMessage = useCallback((id: string) => {
    const el = document.getElementById(`msg-${id}`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('msg-highlight');
    setTimeout(() => el.classList.remove('msg-highlight'), 1500);
  }, []);

  const openSource = useCallback((sourceId: string, messageId: string, msgSources?: SourceRef[]) => {
    // 从 store 实时读 streamingSources，避免把 streamingSources 放进依赖导致每 token 引用变化
    const ss = useChatStore.getState().streamingSources;
    const sid = useChatStore.getState().activeSessionId;
    const src = msgSources?.find((s) => s.id === sourceId) || ss.find((s) => s.id === sourceId);
    if (src?.source_url) {
      window.open(src.source_url, '_blank', 'noopener,noreferrer');
    }
    setSessionHighlightSourceId(sid, sourceId);
    setRightPanelOpen(true);
    setRightMode('content');
    setSessionFileViewerSource(sid, messageId, sourceId);
  }, [setSessionHighlightSourceId, setRightPanelOpen, setRightMode, setSessionFileViewerSource]);

  const pushCodeToPreview = useCallback((code: string, label: string, language = 'cpp') => {
    setRightPanelOpen(true);
    setRightMode('workbench');
    setWbTab('preview');
    addPreviewTab({
      id: `preview-${Date.now()}`,
      label: `${label.slice(0, 18) || 'code'}.${language}`,
      code,
      language,
    });
  }, [setRightPanelOpen, setRightMode, setWbTab, addPreviewTab]);

  const openCodeInEditor = useCallback((code: string, language = 'cpp') => {
    const ext = language === 'text' ? 'txt' : language || 'txt';
    useAppStore.getState().openCodeBuffer({
      name: `snippet.${ext}`,
      content: code,
      language,
    });
  }, []);

  const startEdit = useCallback((msgId: string, content: string) => {
    setEditingId(msgId);
    setEditText(content);
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditText("");
  }, []);

  const saveEdit = useCallback(() => {
    if (editingId && editText.trim()) {
      editAndResend(editingId, editText.trim());
      setEditingId(null);
      setEditText("");
    }
  }, [editingId, editText, editAndResend]);

  const handleCopy = useCallback((msgId: string, content: string | ContentPart[]) => {
    copyToClipboard(renderContent(content));
    setCopiedMsgId(msgId);
    setTimeout(() => setCopiedMsgId(null), 2000);
  }, []);

  const handleRetry = useCallback((msgId: string) => {
    retryMessage(msgId);
  }, [retryMessage]);

  const handleQuote = useCallback((msgId: string) => {
    quoteMessage(msgId);
  }, [quoteMessage]);

  const handleToggleBookmark = useCallback((msgId: string) => {
    toggleBookmark(msgId);
  }, [toggleBookmark]);

  const handleAddBookmarkToFolder = useCallback((msgId: string, folderId: string) => {
    addBookmarkToFolder(msgId, folderId);
  }, [addBookmarkToFolder]);

  const handleAddBookmarkFolder = useCallback((name: string) => {
    addBookmarkFolder(name);
  }, [addBookmarkFolder]);

  const lastUserMsgId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') return messages[i].id;
    }
    return '';
  }, [messages]);

  // 防御性处理：流式进行中但 messages 为空时（状态同步异常），显示流式占位
  if (!messages.length) {
    // 加载中（fetchMessages 期间无本地缓存）：显示骨架屏，而非 EmptyState
    if (isLoadingMessages) {
      return (
        <div className="chat-scroll" id="chatScroll" ref={scrollRef} onScroll={handleScroll} style={{ fontSize: chatFontSize }}>
          <LoadingState />
        </div>
      );
    }
    if (isStreaming && streamingSteps.length > 0) {
      return (
        <div className="chat-scroll" id="chatScroll" ref={scrollRef} onScroll={handleScroll} style={{ fontSize: chatFontSize }}>
          <div className="msg-row" id="msg-streaming-placeholder">
            <div className="msg-avatar bot"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg></div>
            <div className="msg-body">
              <ActivityBlock activity={{ durationMs: 0, steps: streamingSteps, status: 'running' }} msgId="streaming-placeholder" startTime={streamingStartTime || undefined} />
              <div className="assistant-text"><MarkdownRenderer content={streamingContent} streaming={true} enableSourceRef sources={streamingSources} onSourceClick={(id) => openSource(id, "streaming-placeholder")} onOpenInEditor={openCodeInEditor} /></div>
            </div>
          </div>
        </div>
      );
    }
    return (
      <div className="chat-scroll" id="chatScroll" ref={scrollRef} onScroll={handleScroll} style={{ fontSize: chatFontSize }}>
        <EmptyState />
      </div>
    );
  }

  // 找到最后一条 assistant 消息的 id，用于判断哪条消息正在流式输出
  const lastAssistantMsgId = messages[messages.length - 1]?.role === 'assistant'
    ? messages[messages.length - 1].id
    : '';

  return (
    <div className="chat-scroll" id="chatScroll" ref={scrollRef} onScroll={handleScroll} style={{ fontSize: chatFontSize }}>
      {/* 分支路径 */}
      {currentSession?.branchFromSessionId && (
        <div style={{ padding: "4px 12px", fontSize: 11, color: "var(--muted-fg)", background: "var(--hover-bg)", display: "flex", alignItems: "center", gap: 4 }}>
          <span>分支自: {parentSession?.title || "主线"}</span>
        </div>
      )}
      {/* Context compression status banner — cleared on next text/done event */}
      {isCompressing && (
        <div className="compressing-banner">
          <span className="compressing-spinner" />
          <span className="compressing-text">{compressingMessage}</span>
        </div>
      )}
      {messages.map((msg) => {
        if (msg.role === 'user') {
          const isEditing = editingId === msg.id;
          return (
            <UserMessageRow
              key={msg.id}
              msg={msg}
              chatFontSize={chatFontSize}
              isEditing={isEditing}
              editText={editText}
              onEditChange={setEditText}
              onSaveEdit={saveEdit}
              onCancelEdit={cancelEdit}
              onStartEdit={startEdit}
              t={t}
            />
          );
        }

        const bookmarked = isBookmarked(msg.id);
        const isCurrentlyStreaming = isStreaming && msg.id === lastAssistantMsgId;

        return (
          <AssistantMessageRow
            key={msg.id}
            msg={msg}
            isCurrentlyStreaming={isCurrentlyStreaming}
            isStreaming={isStreaming}
            chatFontSize={chatFontSize}
            highlightSourceId={sessionHighlightSourceId[activeSessionId] ?? null}
            bookmarked={bookmarked}
            copiedMsgId={copiedMsgId}
            lastUserMsgId={lastUserMsgId}
            pickerMsgId={pickerMsgId}
            pickerNewName={pickerNewName}
            bookmarkFolders={bookmarkFolders}
            // 关键：历史消息传模块级常量（引用稳定），只有流式中的消息传真实值
            streamingSteps={isCurrentlyStreaming ? streamingSteps : EMPTY_STEPS}
            streamingStartTime={isCurrentlyStreaming ? streamingStartTime : undefined}
            streamingError={msg.id === lastAssistantMsgId ? streamingError : null}
            streamingSources={isCurrentlyStreaming ? streamingSources : EMPTY_SOURCES}
            t={t}
            onSourceClick={openSource}
            onPushCodeToPreview={pushCodeToPreview}
            onOpenInEditor={openCodeInEditor}
            onScrollToMessage={scrollToMessage}
            onToggleBookmark={handleToggleBookmark}
            onRetry={handleRetry}
            onQuote={handleQuote}
            onCopy={handleCopy}
            onSetPickerMsgId={setPickerMsgId}
            onSetPickerNewName={setPickerNewName}
            onAddBookmarkToFolder={handleAddBookmarkToFolder}
            onAddBookmarkFolder={handleAddBookmarkFolder}
          />
        );
      })}

      <ConfirmDialog />
    </div>
  );
}
