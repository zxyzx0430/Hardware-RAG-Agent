import { useState, useEffect, useRef, useMemo } from "react";
import type { ReactNode, CSSProperties } from "react";
import MonacoEditor from "@monaco-editor/react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import type { DocChunk } from "../../stores/useKnowledgeStore";
import { WorkbenchPanel } from "../workbench/WorkbenchPanel";
import { useI18n } from "../../i18n";
import type { TodoItem, SourceRef } from "../../types/session";
import TodoCard from "../chat/TodoCard";
import { PlanPanel } from "../plan/PlanPanel";
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
  if (lang) {
    if (TEXT_LANGUAGES.has(lang)) return false;
    if (mapLanguage(lang) !== "plaintext") return true;
  }
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
      ) : (
        <div className="source-scroll" id="sourceScroll"><PlanPanel /></div>
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
  const pageNumber = viewed.page_start ?? null;

  return (
    <div className="source-fv-root">
      <div className="source-fv-header">
        <button className="source-fv-back" onClick={onBack}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('backToSources', '返回来源引用')}
        </button>
        <div className="source-fv-heading">
          <span className="source-fv-breadcrumb">{t('sources')}</span>
          <span className="source-fv-title" title={viewed.title}>{viewed.title}</span>
        </div>
      </div>
      <div className="source-fv-scroll">
        {viewed.section_title ? <div className="source-fv-section">{viewed.section_title}</div> : null}
        <div className="source-fv-meta">
          {viewed.kb_name ? <span className="fv-kb-badge">{viewed.kb_name}</span> : null}
          {viewed.source_url ? (
            <a className="source-url-link" href={viewed.source_url} target="_blank" rel="noopener noreferrer" title={viewed.source_url}>
              {viewed.source_url.length > 55 ? `${viewed.source_url.slice(0, 55)}...` : viewed.source_url}
            </a>
          ) : null}
          {viewed.source_url ? <span className="fv-sep">·</span> : null}
          <span>{pageLabel}</span>
          <span className="fv-sep">·</span>
          <span>{t('relevance')} {displayScore}%</span>
          {viewed.category ? <span className="fv-sep">·</span> : null}
          {viewed.category ? <span className="fv-category">{viewed.category}</span> : null}
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
            page={pageNumber}
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

function PageImageViewer({ docId, page, title, t, onShowText }: {
  docId: string;
  page: number | null;
  title: string;
  t: (key: string, fallback?: string) => string;
  onShowText: () => void;
}) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">(docId && page != null ? "loading" : "error");
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    if (!docId || page == null) {
      setStatus("error");
      setImageUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    setStatus("loading");
    setImageUrl(null);
    setZoomed(false);
    const query = new URLSearchParams({ page: String(page), scale: "1.5" });
    fetch(`/api/kb/documents/${encodeURIComponent(docId)}/page-image?${query.toString()}`, {
      headers: getAuthHeaders(),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [docId, page]);

  if (status === "loading") {
    return (
      <div className="source-fv-content source-page-viewer" aria-busy="true">
        <div className="source-page-skeleton" role="status" aria-live="polite">
          <span className="source-page-skeleton-label">{t('originalPageLoading', '正在加载原文页…')}</span>
        </div>
      </div>
    );
  }

  if (status === "error" || !imageUrl) {
    return (
      <div className="source-fv-content source-page-viewer">
        <div className="source-page-error" role="alert">
          <p>{t('originalPageUnavailable', '原文页不可用：该来源没有对应页面图像，或页面尚未生成。')}</p>
          <button type="button" className="source-page-text-btn" onClick={onShowText}>
            {t('viewTextExcerpt', '查看文字摘录')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`source-fv-content source-page-viewer${zoomed ? ' zoomed' : ''}`}>
      <div className="source-page-scroll">
        <img
          className="source-page-image"
          src={imageUrl}
          alt={`${title}${page != null ? ` p.${page}` : ''}`}
          title={zoomed ? t('clickToFit', '点击还原') : t('clickToZoom', '点击放大')}
          onClick={() => setZoomed((value) => !value)}
        />
      </div>
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
