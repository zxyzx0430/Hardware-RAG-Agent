import { useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo } from "react";
import type { ReactNode, CSSProperties } from "react";
import MonacoEditor from "@monaco-editor/react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import type { DocChunk } from "../../stores/useKnowledgeStore";
import { useToastStore } from "../../stores/useToastStore";
import { WorkbenchPanel } from "../workbench/WorkbenchPanel";
import { useI18n } from "../../i18n";
import type { TodoItem, SourceRef } from "../../types/session";
import TodoCard from "../chat/TodoCard";
import { PlanPanel } from "../plan/PlanPanel";
import { ChangeSetPanel } from "../changes/ChangeSetPanel";
import { useAppliedDarkMode } from "../shared/MarkdownRenderer";
import { EmptyState } from "../shared/EmptyState";
import { fetchBigChunk, getAuthHeaders, type BigChunkData } from "../../api/client";
import "../../styles/source-viewer.css";

/** Monaco language id for a code source; unknown languages stay plain text. */
const mapLanguage = (lang?: string): string => {
  switch ((lang || "").trim().toLowerCase()) {
    case "c":
      return "c";
    case "cpp":
    case "c++":
    case "arduino":
    case "ino":
    case "h":
    case "hh":
    case "hpp":
    case "cc":
    case "cxx":
      return "cpp";
    case "python":
    case "py":
      return "python";
    case "javascript":
    case "js":
    case "jsx":
    case "mjs":
    case "cjs":
      return "javascript";
    case "typescript":
    case "ts":
    case "tsx":
      return "typescript";
    case "java":
      return "java";
    case "rust":
    case "rs":
      return "rust";
    case "go":
    case "golang":
      return "go";
    case "csharp":
    case "c#":
    case "cs":
      return "csharp";
    case "ruby":
    case "rb":
      return "ruby";
    case "kotlin":
    case "kt":
    case "kts":
      return "kotlin";
    case "swift":
      return "swift";
    case "php":
      return "php";
    case "lua":
      return "lua";
    case "shell":
    case "sh":
    case "bash":
    case "zsh":
      return "shell";
    case "sql":
      return "sql";
    case "html":
    case "htm":
      return "html";
    case "css":
    case "scss":
      return "css";
    case "json":
      return "json";
    case "yaml":
    case "yml":
      return "yaml";
    case "xml":
      return "xml";
    default:
      return "plaintext";
  }
};

const TEXT_LANGUAGES = new Set(["text", "txt", "plain", "plaintext", "markdown", "md", "pdf", "log", "csv"]);

const CODE_EXTENSIONS = new Set([
  "c", "h", "hh", "hpp", "cc", "cpp", "cxx", "ino",
  "py", "pyw", "js", "jsx", "mjs", "cjs", "ts", "tsx",
  "java", "rs", "go", "cs", "rb", "kt", "kts", "swift",
  "php", "lua", "sh", "bash", "zsh", "sql",
  "html", "htm", "css", "scss", "json", "yaml", "yml", "xml", "toml", "asm",
]);

function fileExtension(name?: string): string {
  if (!name) return "";
  const base = name.split(/[\\/]/).pop() || "";
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return "";
  return base.slice(dot + 1).toLowerCase();
}

/** Code sources keep Monaco; documents (PDF/TXT/MD/unknown) use the reading view. */
function isCodeSource(viewed: SourceRef): boolean {
  const lang = (viewed.language || "").trim().toLowerCase();
  // A non-empty language that is not prose means code, even when Monaco does
  // not know it; monacoLanguageFor then falls back to plaintext.
  if (lang) return !TEXT_LANGUAGES.has(lang);
  return [viewed.doc, viewed.title].some((name) => CODE_EXTENSIONS.has(fileExtension(name)));
}

function isPdfSource(viewed: SourceRef): boolean {
  if (viewed.is_pdf === true) return true;
  const mime = (viewed.mime_type || viewed.mime || "").trim().toLowerCase();
  if (mime === "application/pdf") return true;
  if ((viewed.language || "").trim().toLowerCase() === "pdf") return true;
  return [viewed.doc, viewed.title].some((name) => fileExtension(name) === "pdf");
}

/** Prefer the backend's language tag, then fall back to the file extension. */
function monacoLanguageFor(viewed: SourceRef): string {
  const byLanguage = mapLanguage(viewed.language);
  if (byLanguage !== "plaintext") return byLanguage;
  for (const name of [viewed.doc, viewed.title]) {
    const byExtension = mapLanguage(fileExtension(name));
    if (byExtension !== "plaintext") return byExtension;
  }
  return "plaintext";
}

export function RightPanel({ overlay = false, onClose }: { overlay?: boolean; onClose?: () => void } = {}) {
  const { t } = useI18n();
  const { rightMode, setRightMode } = useAppStore();

  return (
    <div className="right-panel-wrap" id="rightPanelWrap">
      <div className="right-panel" id="rightPanel">
        <div className="right-modebar" id="rightModebar">
          <button className={`right-mode${rightMode === 'workbench' ? ' active' : ''}`} data-rmode="workbench" onClick={() => setRightMode('workbench')}>{t('workbench')}</button>
          <button className={`right-mode${rightMode === 'content' ? ' active' : ''}`} data-rmode="content" onClick={() => setRightMode('content')}>{t('chatContent')}</button>
          {overlay ? (
            <button
              type="button"
              className="right-mode right-mode-close"
              onClick={onClose}
              aria-label={t('toggleRightPanel')}
              title={t('toggleRightPanel')}
            >
              ×
            </button>
          ) : null}
        </div>
        <div className="right-modepanes">
          <div className={`rmode-pane${rightMode === 'workbench' ? ' active' : ''}`} id="rmode-workbench"><WorkbenchPanel /></div>
          <div className={`rmode-pane${rightMode === 'content' ? ' active' : ''}`} id="rmode-content"><SourcePanel /></div>
        </div>
      </div>
    </div>
  );
}

function SourcePanel() {
  const { t } = useI18n();
  const { messages, isStreaming, streamingSources, activeSessionId,
          sessionFileViewerSource, sessionHighlightSourceId,
          sessionTodos, sessionTodosLoaded, streamingTodos, streamingTodosSeen,
          clearSessionTodos,
          setSessionFileViewerSource, setSessionHighlightSourceId } = useChatStore();
  const { items: kbItems } = useKnowledgeStore();
  const { themeMode, rightContentTabBySession, setRightContentTab } = useAppStore();
  const isDark = useAppliedDarkMode();
  const editorTheme = themeMode === "dark" || (themeMode === "auto" && isDark) ? "vs-dark" : "vs-light";
  const tab = activeSessionId ? (rightContentTabBySession[activeSessionId] ?? "sources") : "sources";

  // 当前会话的来源查看器状态（按 sessionId 隔离）
  const fileViewerSource = sessionFileViewerSource[activeSessionId] ?? null;
  const highlightSourceId = sessionHighlightSourceId[activeSessionId] ?? null;

  // 给每个 source 打上 messageId（解决 source id 跨消息重复 src1/src2 per-request）。
  const sourcesWithMessageId = useMemo(() => {
    return messages.flatMap((m) =>
      (m.sources || []).map((s) => (s.messageId ? s : { ...s, messageId: m.id }))
    );
  }, [messages]);

  // 聚合 sources：流式时合并 streamingSources 和历史消息 sources（解决 isStreaming 时旧消息 source 查不到）。
  const sources = useMemo(() => {
    if (!isStreaming) return sourcesWithMessageId;
    const map = new Map<string, SourceRef>();
    [...streamingSources, ...sourcesWithMessageId].forEach((s) =>
      map.set(`${s.messageId || ""}-${s.id}`, s)
    );
    return [...map.values()];
  }, [isStreaming, streamingSources, sourcesWithMessageId]);

  // 按消息分组，用于右侧面板 source 列表展示。
  const sourceGroups = useMemo(() => {
    return messages
      .map((m, idx) => ({
        messageId: m.id,
        messageIndex: idx,
        role: m.role,
        preview: (typeof m.content === "string" ? m.content : "").slice(0, 60).replace(/\s+/g, " ").trim() || `消息 ${idx + 1}`,
        sources: [...(m.sources || [])]
          .map((s) => (s.messageId ? s : { ...s, messageId: m.id }))
          .sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
      }))
      .filter((g) => g.sources.length > 0);
  }, [messages]);

  // 默认展开最后一条 assistant 消息的分组。
  const defaultExpandedId = useMemo(() => {
    for (let i = sourceGroups.length - 1; i >= 0; i--) {
      if (sourceGroups[i].role === "assistant") return sourceGroups[i].messageId;
    }
    return sourceGroups[sourceGroups.length - 1]?.messageId || null;
  }, [sourceGroups]);

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() =>
    defaultExpandedId ? new Set([defaultExpandedId]) : new Set()
  );
  useEffect(() => {
    if (defaultExpandedId) {
      setExpandedGroups((prev) => (prev.size === 0 ? new Set([defaultExpandedId]) : prev));
    }
  }, [defaultExpandedId]);

  // 当前待办优先级：流式中 SSE 快照 > 已加载的服务端快照 > 旧消息兜底。
  // loaded 为 true 时空清单必须显示空态，不能回退到旧消息里的 todos。
  const fallbackTodos: TodoItem[] = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === 'assistant' && msg.todos && msg.todos.length > 0) {
        return msg.todos;
      }
    }
    return [];
  })();
  const todosLoaded = sessionTodosLoaded[activeSessionId] ?? false;
  const todosSeen = streamingTodosSeen[activeSessionId] ?? false;
  const todos: TodoItem[] = isStreaming
    ? (todosSeen ? streamingTodos : todosLoaded ? (sessionTodos[activeSessionId] ?? []) : fallbackTodos)
    : (todosLoaded ? (sessionTodos[activeSessionId] ?? []) : fallbackTodos);

  // 查找文件查看源：先按 messageId 定位消息，再在该消息的 sources 里找 sourceId
  const viewed = useMemo(() => {
    if (!fileViewerSource) return null;
    const { messageId, sourceId } = fileViewerSource;
    const msg = messages.find((m) => m.id === messageId);
    if (msg?.sources) {
      const found = msg.sources.find((s) => s.id === sourceId);
      if (found) return found;
    }
    if (isStreaming) return streamingSources.find((s) => s.id === sourceId) || null;
    return messages.flatMap((m) => m.sources || []).find((s) => s.id === sourceId) || null;
  }, [fileViewerSource, messages, isStreaming, streamingSources]);

  const viewedKbItem = !viewed && fileViewerSource
    ? kbItems.find((item) => item.id === fileViewerSource.sourceId) || null
    : null;

  // Opening a source from chat or the KB panel while todos/plan is active
  // should bring the viewer into view. This only runs when the source changes,
  // so a later manual tab switch back to todos/plan is preserved.
  useEffect(() => {
    if (fileViewerSource) setRightContentTab(activeSessionId, 'sources');
  }, [fileViewerSource, activeSessionId, setRightContentTab]);

  // The tab bar stays mounted while a source viewer is open, so the user can
  // always switch to todos/plan instead of being trapped in the viewer.
  const viewer = viewed ? (
    viewed.big_chunk_id ? (
      <BigChunkViewer
        viewed={viewed}
        highlightTexts={collectSiblingTexts(sources, viewed)}
        onBack={() => setSessionFileViewerSource(activeSessionId, null, null)}
      />
    ) : (
      <SourceExcerptViewer
        key={`${viewed.messageId || ""}-${viewed.id}`}
        viewed={viewed}
        editorTheme={editorTheme}
        pageLabel={buildPageLabel(viewed, t)}
        displayScore={Math.round((viewed.score || 0) * 100)}
        onBack={() => setSessionFileViewerSource(activeSessionId, null, null)}
        t={t}
      />
    )
  ) : viewedKbItem ? (
    <ChunkViewer
      docId={viewedKbItem.id}
      docName={viewedKbItem.name}
      onBack={() => setSessionFileViewerSource(activeSessionId, null, null)}
    />
  ) : null;

  return (
    <div className="source-panel" id="sourcePanel">
      <div className="source-header">
        <div className="source-tabs">
          <button className={`source-tab ${tab === 'sources' ? 'active' : 'inactive'}`} id="tab-sources" onClick={() => setRightContentTab(activeSessionId, 'sources')}>{t('sources')}</button>
          <button className={`source-tab ${tab === 'todos' ? 'active' : 'inactive'}`} id="tab-todos" onClick={() => setRightContentTab(activeSessionId, 'todos')}>{t('todos')}</button>
          <button className={`source-tab ${tab === 'plan' ? 'active' : 'inactive'}`} id="tab-plan" onClick={() => setRightContentTab(activeSessionId, 'plan')}>{t('planTab')}</button>
          <button className={`source-tab ${tab === 'changes' ? 'active' : 'inactive'}`} id="tab-changes" onClick={() => setRightContentTab(activeSessionId, 'changes')}>{t('changesTab')}</button>
        </div>
      </div>
      {tab === 'sources' ? (
        viewer ? (
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>{viewer}</div>
        ) : (
          <div className="source-scroll" id="sourceScroll">
            {sourceGroups.length ? sourceGroups.map((group) => (
              <MessageSourceGroup
                key={group.messageId}
                group={group}
                expanded={expandedGroups.has(group.messageId)}
                onToggle={() => setExpandedGroups((prev) => {
                  const next = new Set(prev);
                  if (next.has(group.messageId)) next.delete(group.messageId);
                  else next.add(group.messageId);
                  return next;
                })}
                highlightSourceId={highlightSourceId}
                onSourceClick={(src) => {
                  setSessionHighlightSourceId(activeSessionId, src.id);
                  setSessionFileViewerSource(activeSessionId, src.messageId || "", src.id);
                }}
              />
            )) : <EmptyState size="sm" title={t('noSourceData')} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 7h16M4 12h16M4 17h10" strokeLinecap="round" /></svg>} />}
          </div>
        )
      ) : tab === 'todos' ? (
        <div className="source-scroll" id="sourceScroll">
          {todos.length ? (
            <TodoCard todos={todos} onClear={() => clearSessionTodos(activeSessionId)} />
          ) : (
            <EmptyState
              size="sm"
              title={t('noTodos') || '暂无任务清单'}
              desc={t('noTodosDesc')}
              icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 9h6M9 13h4" strokeLinecap="round" /></svg>}
            />
          )}
        </div>
      ) : tab === 'plan' ? (
        <div className="source-scroll" id="sourceScroll"><PlanPanel /></div>
      ) : (
        <div className="source-scroll" id="sourceScroll"><ChangeSetPanel /></div>
      )}
    </div>
  );
}

/** Page label: prefer real PDF pages, fallback to chunk index. */
function buildPageLabel(viewed: SourceRef, t: (key: string, fallback?: string) => string): string {
  if (viewed.page_start == null) return `${t('chunk')} #${viewed.page}`;
  if (viewed.page_end != null && viewed.page_end !== viewed.page_start) {
    return `${t('pages')} ${viewed.page_start}-${viewed.page_end}`;
  }
  return `${t('page')} ${viewed.page_start}`;
}

// ─── Source excerpt viewer: code uses Monaco, documents use a reading view ───
type SourceViewMode = "original" | "text";

function SourceExcerptViewer({ viewed, editorTheme, pageLabel, displayScore, onBack, t }: {
  viewed: SourceRef;
  editorTheme: string;
  pageLabel: string;
  displayScore: number;
  onBack: () => void;
  t: (key: string, fallback?: string) => string;
}) {
  const isCode = isCodeSource(viewed);
  const isPdf = !isCode && isPdfSource(viewed);
  const [viewMode, setViewMode] = useState<SourceViewMode>(isPdf ? "original" : "text");
  const [editorReady, setEditorReady] = useState(false);
  useEffect(() => {
    setEditorReady(false);
    setViewMode(isPdf ? "original" : "text");
  }, [viewed.id, viewed.messageId, isPdf]);

  // Until the backend ships doc_id on every source, `doc` may already hold it.
  const docId = viewed.doc_id || viewed.doc || "";
  const pageStart = viewed.page_start ?? null;
  const pageEnd = viewed.page_end ?? pageStart;

  return (
    <div className="source-fv-root">
      <div className="source-fv-header source-fv-header-compact">
        <button
          className="source-fv-back source-fv-back-icon"
          onClick={onBack}
          aria-label={t('backToSources', '返回来源引用')}
          title={t('backToSources', '返回来源引用')}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <div className="source-fv-heading">
          <span className="source-fv-breadcrumb">{t('sources')}</span>
          <span className="source-fv-title" title={viewed.title}>{viewed.title}</span>
        </div>
        {isPdf ? (
          <div className="source-view-tabs" role="tablist" aria-label={t('sourceViewMode', '来源视图')}>
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === 'original'}
              className={`source-view-tab${viewMode === 'original' ? ' active' : ''}`}
              onClick={() => setViewMode('original')}
            >
              {t('originalPage', '原文页')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={viewMode === 'text'}
              className={`source-view-tab${viewMode === 'text' ? ' active' : ''}`}
              onClick={() => setViewMode('text')}
            >
              {t('textExcerpt', '文字摘录')}
            </button>
          </div>
        ) : null}
      </div>
      <div className="source-fv-meta source-fv-meta-compact">
        {viewed.section_title ? <span className="source-fv-section-inline">{viewed.section_title}</span> : null}
        {viewed.section_title ? <span className="fv-sep">·</span> : null}
        {viewed.kb_name ? <span className="fv-kb-badge">{viewed.kb_name}</span> : null}
        {viewed.source_url ? (
          <a className="source-url-link" href={viewed.source_url} target="_blank" rel="noopener noreferrer" title={viewed.source_url}>
            {viewed.source_url.length > 40 ? `${viewed.source_url.slice(0, 40)}...` : viewed.source_url}
          </a>
        ) : null}
        {viewed.source_url ? <span className="fv-sep">·</span> : null}
        <span>{pageLabel}</span>
        <span className="fv-sep">·</span>
        <span>{t('relevance')} {displayScore}%</span>
        {viewed.category ? <span className="fv-sep">·</span> : null}
        {viewed.category ? <span className="fv-category">{viewed.category}</span> : null}
      </div>
      <div className="source-fv-body">
        {isCode ? (
          <div className="source-fv-content source-fv-code" style={{ position: "relative" }}>
            {!editorReady ? (
              <div className="source-fv-loading" role="status" aria-live="polite">
                {t('previewLoading', '正在加载预览…')}
              </div>
            ) : null}
            <MonacoEditor
              language={monacoLanguageFor(viewed)}
              value={viewed.excerpt}
              theme={editorTheme}
              height="100%"
              loading={<div className="source-fv-loading">{t('previewLoading', '正在加载预览…')}</div>}
              onMount={() => setEditorReady(true)}
              options={{
                readOnly: true,
                minimap: { enabled: false },
                fontSize: 13,
                lineNumbers: "on",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                tabSize: 2,
                automaticLayout: true,
              }}
            />
          </div>
        ) : isPdf && viewMode === 'original' ? (
          <PageImageViewer
            docId={docId}
            page={pageStart}
            pageEnd={pageEnd}
            title={viewed.title}
            t={t}
            onShowText={() => setViewMode('text')}
          />
        ) : (
          <div className="source-fv-content source-reading-scroll">
            <article className="source-reading-body">{viewed.excerpt}</article>
          </div>
        )}
      </div>
    </div>
  );
}

const PAGE_ZOOM_MIN = 0.5;
const PAGE_ZOOM_MAX = 4;
const PAGE_ZOOM_STEP = 1.25;
const PAGE_ZOOM_DEBOUNCE_MS = 250;
const PAGE_INITIAL_RENDER_SCALE = 1.5;
// Keep in step with the backend's page-image MAX_SCALE.
const PAGE_MAX_RENDER_SCALE = 4;

type PageImageStatus = "loading" | "ready" | "refreshing" | "error";
interface PageImageState {
  url: string;
  page: number;
}

function clampPageZoom(value: number): number {
  return Math.min(PAGE_ZOOM_MAX, Math.max(PAGE_ZOOM_MIN, value));
}

/** Pick the render scale that keeps the raster sharp at the current UI zoom. */
function renderScaleForZoom(zoom: number): number {
  const raw = Math.min(
    PAGE_MAX_RENDER_SCALE,
    Math.max(PAGE_ZOOM_MIN, zoom * PAGE_INITIAL_RENDER_SCALE),
  );
  return Math.round(raw * 4) / 4;
}

function PageImageViewer({ docId, page, pageEnd, title, t, onShowText }: {
  docId: string;
  page: number | null;
  pageEnd: number | null;
  title: string;
  t: (key: string, fallback?: string) => string;
  onShowText: () => void;
}) {
  const rangeStart = page ?? null;
  const rangeEnd = rangeStart != null && pageEnd != null ? Math.max(pageEnd, rangeStart) : rangeStart;
  const [currentPage, setCurrentPage] = useState(page ?? 1);
  const [zoom, setZoom] = useState(1);
  const [renderScale, setRenderScale] = useState(PAGE_INITIAL_RENDER_SCALE);
  const [status, setStatus] = useState<PageImageStatus>(docId && page != null ? "loading" : "error");
  const [image, setImage] = useState<PageImageState | null>(null);
  const [fileAction, setFileAction] = useState<"open" | "download" | null>(null);

  const viewerRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const zoomRef = useRef(1);
  const lastZoomRef = useRef(2);
  const imageRef = useRef<PageImageState | null>(null);
  const anchorRef = useRef<{ x: number; y: number; clientX: number; clientY: number } | null>(null);
  const dragRef = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const cacheRef = useRef<Map<string, string>>(new Map());
  zoomRef.current = zoom;

  // Reset page and zoom when the source (or its page anchor) changes.
  useEffect(() => {
    setCurrentPage(page ?? 1);
    zoomRef.current = 1;
    lastZoomRef.current = 2;
    setZoom(1);
    setRenderScale(PAGE_INITIAL_RENDER_SCALE);
    anchorRef.current = null;
  }, [docId, page]);

  useEffect(() => () => {
    for (const url of cacheRef.current.values()) URL.revokeObjectURL(url);
    cacheRef.current.clear();
  }, []);

  // Fetch a higher-resolution raster after zooming settles, not on every tick.
  useEffect(() => {
    const target = renderScaleForZoom(zoom);
    if (target === renderScale) return;
    const timer = window.setTimeout(() => setRenderScale(target), PAGE_ZOOM_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [zoom, renderScale]);

  useEffect(() => {
    if (!docId || currentPage == null) {
      setStatus("error");
      return;
    }
    const key = `${docId}|${currentPage}|${renderScale}`;
    const cached = cacheRef.current.get(key);
    if (cached) {
      imageRef.current = { url: cached, page: currentPage };
      setImage(imageRef.current);
      setStatus("ready");
      return;
    }
    const keepCurrent = imageRef.current?.page === currentPage;
    setStatus(keepCurrent ? "refreshing" : "loading");
    const controller = new AbortController();
    let cancelled = false;
    const query = new URLSearchParams({ page: String(currentPage), scale: String(renderScale) });
    fetch(`/api/kb/documents/${encodeURIComponent(docId)}/page-image?${query.toString()}`, {
      headers: getAuthHeaders(),
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        cacheRef.current.set(key, url);
        imageRef.current = { url, page: currentPage };
        setImage(imageRef.current);
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setStatus(keepCurrent ? "ready" : "error");
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [docId, currentPage, renderScale]);

  const applyZoom = useCallback((next: number, clientX?: number, clientY?: number) => {
    const previous = zoomRef.current;
    const clamped = Math.round(clampPageZoom(next) * 100) / 100;
    if (Math.abs(clamped - previous) < 0.001) return;
    const scroll = scrollRef.current;
    if (scroll && scroll.clientWidth > 0 && scroll.clientHeight > 0) {
      const rect = scroll.getBoundingClientRect();
      const x = clientX != null ? clientX - rect.left : scroll.clientWidth / 2;
      const y = clientY != null ? clientY - rect.top : scroll.clientHeight / 2;
      const style = getComputedStyle(scroll);
      const paddingLeft = parseFloat(style.paddingLeft) || 0;
      const paddingTop = parseFloat(style.paddingTop) || 0;
      anchorRef.current = {
        x: (scroll.scrollLeft + x - paddingLeft) / previous,
        y: (scroll.scrollTop + y - paddingTop) / previous,
        clientX: x,
        clientY: y,
      };
    }
    if (clamped > 1) lastZoomRef.current = clamped;
    zoomRef.current = clamped;
    setZoom(clamped);
  }, []);

  // Keep the point under the cursor fixed while the image width changes.
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const scroll = scrollRef.current;
    if (!anchor || !scroll) return;
    const style = getComputedStyle(scroll);
    const paddingLeft = parseFloat(style.paddingLeft) || 0;
    const paddingTop = parseFloat(style.paddingTop) || 0;
    scroll.scrollLeft = paddingLeft + anchor.x * zoom - anchor.clientX;
    scroll.scrollTop = paddingTop + anchor.y * zoom - anchor.clientY;
    anchorRef.current = null;
  }, [zoom]);

  // Ctrl/Cmd+wheel and trackpad pinch both arrive as wheel events; plain wheel
  // zooms too, so the page can be inspected without hunting for a modifier.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = Math.exp(-event.deltaY * 0.0015);
      applyZoom(zoomRef.current * factor, event.clientX, event.clientY);
    };
    viewer.addEventListener("wheel", onWheel, { passive: false });
    return () => viewer.removeEventListener("wheel", onWheel);
  }, [applyZoom]);

  const goToPage = useCallback((next: number) => {
    if (rangeStart == null || rangeEnd == null) return;
    const clamped = Math.min(rangeEnd, Math.max(rangeStart, next));
    if (clamped === currentPage) return;
    setCurrentPage(clamped);
    zoomRef.current = 1;
    setZoom(1);
    setRenderScale(PAGE_INITIAL_RENDER_SCALE);
    anchorRef.current = null;
  }, [currentPage, rangeStart, rangeEnd]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    const tag = (event.target as HTMLElement).tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    switch (event.key) {
      case "+":
      case "=":
        event.preventDefault();
        applyZoom(zoomRef.current * PAGE_ZOOM_STEP);
        break;
      case "-":
      case "_":
        event.preventDefault();
        applyZoom(zoomRef.current / PAGE_ZOOM_STEP);
        break;
      case "0":
        event.preventDefault();
        applyZoom(1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        goToPage(currentPage - 1);
        break;
      case "ArrowRight":
        event.preventDefault();
        goToPage(currentPage + 1);
        break;
      default:
        break;
    }
  };

  const handlePointerDown = (event: React.PointerEvent) => {
    if (zoomRef.current <= 1.01 || event.button !== 0) return;
    const scroll = scrollRef.current;
    if (!scroll) return;
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      left: scroll.scrollLeft,
      top: scroll.scrollTop,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    const scroll = scrollRef.current;
    if (!drag || !scroll) return;
    scroll.scrollLeft = drag.left - (event.clientX - drag.x);
    scroll.scrollTop = drag.top - (event.clientY - drag.y);
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const runFileAction = async (mode: "open" | "download") => {
    if (!docId || fileAction) return;
    setFileAction(mode);
    try {
      const res = await fetch(`/api/kb/documents/${encodeURIComponent(docId)}/file`, {
        headers: getAuthHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      if (mode === "open") {
        window.open(objectUrl, "_blank", "noopener,noreferrer");
        // Give the new tab time to load the blob before releasing it.
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
      } else {
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = title || `document-${docId}.pdf`;
        link.rel = "noopener";
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
      }
    } catch {
      useToastStore.getState().showError(
        mode === "open"
          ? t('openPdfFailed', '无法打开原 PDF')
          : t('downloadPdfFailed', '下载原 PDF 失败'),
      );
    } finally {
      setFileAction(null);
    }
  };

  const toggleZoom = () => {
    if (zoomRef.current > 1.01) {
      applyZoom(1);
      return;
    }
    applyZoom(lastZoomRef.current > 1.01 ? lastZoomRef.current : 2);
  };

  const canPrev = rangeStart != null && currentPage > rangeStart;
  const canNext = rangeEnd != null && currentPage < rangeEnd;
  const percent = Math.round(zoom * 100);
  const pageText = rangeStart === rangeEnd ? String(currentPage) : `${currentPage} / ${rangeEnd}`;
  const imageMatchesPage = image != null && image.page === currentPage;

  return (
    <div
      ref={viewerRef}
      className="source-fv-content source-page-viewer"
      role="region"
      aria-label={`${title} ${t('originalPage', '原文页')} ${currentPage}`}
      aria-keyshortcuts="+ - 0 ArrowLeft ArrowRight"
      aria-busy={status === "loading" || status === "refreshing"}
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="source-page-toolbar">
        <div className="source-page-toolbar-left">
          <div className="source-page-nav" role="group" aria-label={t('pageNavigation', '页面导航')}>
            <button
              type="button"
              className="source-page-tool"
              onClick={() => goToPage(currentPage - 1)}
              disabled={!canPrev}
              aria-label={t('previousPage', '上一页')}
              title={t('previousPage', '上一页')}
            >
              ‹
            </button>
            <span className="source-page-indicator" aria-live="polite">{pageText}</span>
            <button
              type="button"
              className="source-page-tool"
              onClick={() => goToPage(currentPage + 1)}
              disabled={!canNext}
              aria-label={t('nextPage', '下一页')}
              title={t('nextPage', '下一页')}
            >
              ›
            </button>
          </div>
          <div
            className="source-page-file-actions"
            role="group"
            aria-label={t('pdfFileActions', 'PDF 文件')}
            aria-busy={fileAction !== null}
          >
            <button
              type="button"
              className="source-page-file-btn"
              onClick={() => void runFileAction("open")}
              disabled={!docId || fileAction !== null}
              aria-label={t('openPdf', '打开原 PDF')}
              title={t('openPdf', '打开原 PDF')}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 3h7v7" /><path d="M10 14 21 3" /><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" /></svg>
              <span>{t('openPdfShort', '打开')}</span>
            </button>
            <button
              type="button"
              className="source-page-file-btn"
              onClick={() => void runFileAction("download")}
              disabled={!docId || fileAction !== null}
              aria-label={t('downloadPdf', '下载原 PDF')}
              title={t('downloadPdf', '下载原 PDF')}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3v12" /><polyline points="7 10 12 15 17 10" /><path d="M5 21h14" /></svg>
              <span>{t('downloadPdfShort', '下载')}</span>
            </button>
          </div>
          {status === "refreshing" ? (
            <span className="source-page-quality" role="status">
              {t('highResLoading', '正在加载高清图…')}
            </span>
          ) : null}
        </div>
        <div className="source-page-zoom" role="group" aria-label={t('zoomControls', '缩放')}>
          <button
            type="button"
            className="source-page-tool"
            onClick={() => applyZoom(zoom / PAGE_ZOOM_STEP)}
            disabled={zoom <= PAGE_ZOOM_MIN}
            aria-label={t('zoomOut', '缩小')}
            title={t('zoomOut', '缩小')}
          >
            −
          </button>
          <output className="source-page-percent" aria-live="polite">{percent}%</output>
          <button
            type="button"
            className="source-page-tool"
            onClick={() => applyZoom(zoom * PAGE_ZOOM_STEP)}
            disabled={zoom >= PAGE_ZOOM_MAX}
            aria-label={t('zoomIn', '放大')}
            title={t('zoomIn', '放大')}
          >
            +
          </button>
          <button
            type="button"
            className="source-page-fit"
            onClick={() => applyZoom(1)}
            disabled={Math.abs(zoom - 1) < 0.001}
          >
            {t('fitWidth', '适应宽度')}
          </button>
        </div>
      </div>
      {status === "error" ? (
        <div className="source-page-error" role="alert">
          <p>{t('originalPageUnavailable', '原文页不可用：该来源没有对应页面图像，或页面尚未生成。')}</p>
          <button type="button" className="source-page-text-btn" onClick={onShowText}>
            {t('viewTextExcerpt', '查看文字摘录')}
          </button>
        </div>
      ) : !imageMatchesPage ? (
        <div className="source-page-skeleton" role="status" aria-live="polite">
          <span className="source-page-skeleton-label">{t('originalPageLoading', '正在加载原文页…')}</span>
        </div>
      ) : (
        <div
          ref={scrollRef}
          className={`source-page-scroll${zoom > 1.01 ? ' pannable' : ''}`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
          onPointerCancel={endDrag}
          onDoubleClick={toggleZoom}
        >
          <img
            className="source-page-image"
            src={image.url}
            alt={`${title} p.${currentPage}`}
            style={{ width: `${zoom * 100}%` }}
            draggable={false}
          />
        </div>
      )}
    </div>
  );
}

// ─── Chunk Viewer: shows all chunks of a KB document ───
function ChunkViewer({ docId, docName, onBack }: { docId: string; docName: string; onBack: () => void }) {
  const { t } = useI18n();
  const { docChunks, chunksLoading, viewingDocId, fetchDocChunks, clearChunks } = useKnowledgeStore();

  // Load chunks when this document becomes the viewed one (covers direct right-panel entry
  // where KnowledgePanel didn't pre-fetch, and ensures data is fresh).
  useEffect(() => {
    if (viewingDocId !== docId) {
      fetchDocChunks(docId);
    }
    return () => {
      // Clear when leaving the viewer
      clearChunks();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  const totalLabel = t('chunksTotal').replace('{n}', String(docChunks.length));

  return (
    <div className="chunk-viewer" id="chunkViewer">
      <div className="chunk-viewer-header">
        <button className="source-fv-back" onClick={onBack}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('back')}
        </button>
        <div className="chunk-viewer-title-wrap">
          <div className="chunk-viewer-doc-name" title={docName}>{docName}</div>
          <div className="chunk-viewer-count">{totalLabel}</div>
        </div>
      </div>
      <div className="chunk-viewer-body">
        {chunksLoading ? (
          <div className="chunk-viewer-loading">{t('loadingChunks')}</div>
        ) : docChunks.length === 0 ? (
          <EmptyState size="sm" title={t('noChunks')} icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7l9-4 9 4-9 4z" /><path d="M3 12l9 4 9-4" /><path d="M3 17l9 4 9-4" /></svg>} />
        ) : (
          docChunks.map((chunk) => <ChunkItem key={chunk.id} chunk={chunk} />)
        )}
      </div>
    </div>
  );
}

// ─── Single chunk card — default fully expanded, no truncation ───
function ChunkItem({ chunk }: { chunk: DocChunk }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(true);

  const pageLabel = chunk.page_start != null
    ? (chunk.page_end != null && chunk.page_end !== chunk.page_start
        ? `p.${chunk.page_start}-${chunk.page_end}`
        : `p.${chunk.page_start}`)
    : '';

  return (
    <div className="chunk-item">
      <div className="chunk-item-header">
        <span className="chunk-item-index">#{chunk.chunk_index}</span>
        {chunk.section_title ? <span className="chunk-item-section">{chunk.section_title}</span> : null}
        {pageLabel ? <span className="chunk-item-page">{pageLabel}</span> : null}
        <button className="chunk-expand-btn" onClick={() => setExpanded((v) => !v)} style={{ marginLeft: 'auto' }}>
          {expanded ? t('collapseChunk') : t('expandChunk')}
        </button>
      </div>
      {expanded && (
        <div className="chunk-item-content">{chunk.content}</div>
      )}
      <div className="chunk-item-footer">
        {chunk.chunk_method ? <span className="chunk-method-badge">{chunk.chunk_method}</span> : null}
        <span className="chunk-size-label">{chunk.content_length} chars</span>
      </div>
    </div>
  );
}

/** 来源列表项：高亮时自动滚动到视口内 */
function SourceListItem({ src, index, isHighlighted, onClick }: {
  src: { id: string; title: string; score: number; section_title?: string };
  index: number;
  isHighlighted: boolean;
  onClick: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const scoreClass = src.score >= 0.9 ? 'high' : src.score >= 0.8 ? 'med' : 'low';
  useEffect(() => {
    if (isHighlighted && ref.current) {
      ref.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [isHighlighted]);
  return (
    <div
      ref={ref}
      className={`source-list-item ${scoreClass} ${isHighlighted ? 'highlight' : ''}`}
      key={src.id}
      onClick={onClick}
      title={src.section_title ? `${src.title} · ${src.section_title}` : src.title}
    >
      <span className="source-list-num">{index + 1}</span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)', flexShrink:0 }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <span className="source-list-title">{src.title}</span>
      <span className={`source-list-score ${scoreClass}`}>{(src.score * 100).toFixed(0)}%</span>
    </div>
  );
}

interface SourceGroup {
  messageId: string;
  messageIndex: number;
  role: string;
  preview: string;
  sources: SourceRef[];
}

/** 按消息分组的来源列表：解决多条消息 source 混在一起的重复/混乱问题。 */
function MessageSourceGroup({ group, expanded, onToggle, highlightSourceId, onSourceClick }: {
  group: SourceGroup;
  expanded: boolean;
  onToggle: () => void;
  highlightSourceId: string | null;
  onSourceClick: (src: SourceRef) => void;
}) {
  const label = `${group.role === "user" ? "问" : "答"}: ${group.preview}`;
  return (
    <div className="source-group">
      <button className="source-group-header" onClick={onToggle} type="button">
        <svg
          className={`source-group-chevron ${expanded ? "expanded" : ""}`}
          width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
        <span className="source-group-title" title={label}>{label}</span>
        <span className="source-group-count">{group.sources.length}</span>
      </button>
      {expanded && (
        <div className="source-group-body">
          {group.sources.map((src, index) => (
            <SourceListItem
              key={`${src.messageId || ""}-${src.id}`}
              src={src}
              index={index}
              isHighlighted={highlightSourceId === src.id}
              onClick={() => onSourceClick(src)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Big Chunk Viewer: shows parent big chunk with highlighted small chunks ───

const HIGHLIGHT_STYLE: CSSProperties = {
  backgroundColor: '#fef08a',
  padding: '0 1px',
  borderRadius: '2px',
};

type HighlightRange = { start: number; end: number };

/** Collect small_chunk_text from all sources sharing the same big_chunk_id. */
function collectSiblingTexts(sources: SourceRef[], viewed: SourceRef): string[] {
  return sources
    .filter((s) => s.big_chunk_id === viewed.big_chunk_id)
    .map((s) => s.small_chunk_text || s.excerpt)
    .filter((t): t is string => !!t && t.trim().length > 0);
}

/** Find all occurrences of each needle in text, merge overlapping ranges. */
function computeHighlightRanges(text: string, needles: string[]): HighlightRange[] {
  const ranges: HighlightRange[] = [];
  for (const needle of needles) {
    if (!needle || !needle.trim()) continue;
    let from = 0;
    while (from < text.length) {
      const idx = text.indexOf(needle, from);
      if (idx === -1) break;
      ranges.push({ start: idx, end: idx + needle.length });
      from = idx + needle.length;
    }
  }
  return mergeRanges(ranges);
}

/** Merge overlapping highlight ranges into non-overlapping set. */
function mergeRanges(ranges: HighlightRange[]): HighlightRange[] {
  if (ranges.length === 0) return [];
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  const merged: HighlightRange[] = [{ ...sorted[0] }];
  for (let i = 1; i < sorted.length; i++) {
    const last = merged[merged.length - 1];
    if (sorted[i].start <= last.end) last.end = Math.max(last.end, sorted[i].end);
    else merged.push({ ...sorted[i] });
  }
  return merged;
}

/** Render text with <mark> highlighting around all needle occurrences. */
function renderHighlightedText(text: string, needles: string[]): ReactNode {
  const ranges = computeHighlightRanges(text, needles);
  if (ranges.length === 0) return text;
  const parts: ReactNode[] = [];
  let pos = 0;
  ranges.forEach((r, i) => {
    if (pos < r.start) parts.push(text.slice(pos, r.start));
    parts.push(<mark key={`h${i}`} style={HIGHLIGHT_STYLE}>{text.slice(r.start, r.end)}</mark>);
    pos = r.end;
  });
  if (pos < text.length) parts.push(text.slice(pos));
  return parts;
}

/** Fetch big chunk text and render with highlighted small chunk regions. */
function BigChunkViewer({ viewed, highlightTexts, onBack }: {
  viewed: SourceRef;
  highlightTexts: string[];
  onBack: () => void;
}) {
  const { t } = useI18n();
  const [chunk, setChunk] = useState<BigChunkData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchBigChunk(viewed.big_chunk_id!)
      .then((d) => { if (!cancelled) { setChunk(d); setLoading(false); } })
      .catch((e: unknown) => { if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [viewed.big_chunk_id]);

  if (loading) return <BigChunkSimpleView onBack={onBack} title={viewed.title} body={t('loadingChunks') || '加载中...'} />;
  if (error || !chunk) return <BigChunkSimpleView onBack={onBack} title={viewed.title} body={error || '加载失败'} />;
  return <BigChunkDetail viewed={viewed} chunk={chunk} highlightTexts={highlightTexts} onBack={onBack} />;
}

/** Render the big chunk text with metadata + highlighted small chunk regions. */
function BigChunkDetail({ viewed, chunk, highlightTexts, onBack }: {
  viewed: SourceRef;
  chunk: BigChunkData;
  highlightTexts: string[];
  onBack: () => void;
}) {
  const { t } = useI18n();
  const pageLabel = chunk.page_start != null
    ? (chunk.page_end != null && chunk.page_end !== chunk.page_start
        ? `${t('pages')} ${chunk.page_start}-${chunk.page_end}`
        : `${t('page')} ${chunk.page_start}`)
    : '';
  const score = Math.round((viewed.score || 0) * 100);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
      <div className="source-fv-header">
        <button className="source-fv-back" onClick={onBack}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('back')}
        </button>
        <span className="source-fv-title" title={viewed.title}>{viewed.title}</span>
      </div>
      <div className="source-fv-scroll">
        {chunk.section_title ? <div className="source-fv-section">{chunk.section_title}</div> : null}
        <div className="source-fv-meta">
          {viewed.kb_name ? <span className="fv-kb-badge">{viewed.kb_name}</span> : null}
          {pageLabel ? <span>{pageLabel}</span> : null}
          {pageLabel ? <span className="fv-sep">·</span> : null}
          <span>{t('relevance')} {score}%</span>
        </div>
        <div className="big-chunk-content" style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '12px', whiteSpace: 'pre-wrap', fontSize: '13px', lineHeight: '1.6' }}>
          {renderHighlightedText(chunk.text, highlightTexts)}
        </div>
      </div>
    </div>
  );
}

/** Simple loading / error view for big chunk fetch. */
function BigChunkSimpleView({ onBack, title, body }: { onBack: () => void; title: string; body: string }) {
  const { t } = useI18n();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
      <div className="source-fv-header">
        <button className="source-fv-back" onClick={onBack}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('back')}
        </button>
        <span className="source-fv-title" title={title}>{title}</span>
      </div>
      <div className="source-fv-scroll">
        <div style={{ padding: '12px', color: 'var(--text-secondary, #888)', flex: 1 }}>{body}</div>
      </div>
    </div>
  );
}
