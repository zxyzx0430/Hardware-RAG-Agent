import { useState, useEffect } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import type { DocChunk } from "../../stores/useKnowledgeStore";
import { WorkbenchPanel } from "../workbench/WorkbenchPanel";
import { useI18n } from "../../i18n";
import type { ActivityStep } from "../../types/session";
import { copyToClipboard } from "../../utils/clipboard";

function formatDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function RightPanel() {
  const { t } = useI18n();
  const { rightMode, setRightMode } = useAppStore();

  return (
    <div className="right-panel-wrap" id="rightPanelWrap">
      <div className="right-panel" id="rightPanel">
        <div className="right-modebar" id="rightModebar">
          <button className={`right-mode${rightMode === 'workbench' ? ' active' : ''}`} data-rmode="workbench" onClick={() => setRightMode('workbench')}>{t('workbench')}</button>
          <button className={`right-mode${rightMode === 'content' ? ' active' : ''}`} data-rmode="content" onClick={() => setRightMode('content')}>{t('chatContent')}</button>
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
  const { sources, messages, isStreaming, streamingSteps, pushCodeToWorkbench } = useChatStore();
  const { items: kbItems } = useKnowledgeStore();
  const { fileViewerSource, setFileViewerSource, highlightSourceId, setHighlightSourceId, setRightMode } = useAppStore();
  const [tab, setTab] = useState<'sources' | 'tools'>('sources');
  const [toolOpen, setToolOpen] = useState<Record<string, boolean>>({});

  // 从最后一条含 activity 的 assistant 消息中提取 tool 步骤
  const toolSteps: ActivityStep[] = (() => {
    // 如果正在流式输出，优先使用 streamingSteps
    if (isStreaming) {
      return streamingSteps.filter((s) => s.type === 'tool');
    }
    // 否则从 messages 中找最后一条有 activity 的 assistant 消息
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === 'assistant' && msg.activity?.steps) {
        return msg.activity.steps.filter((s) => s.type === 'tool');
      }
    }
    return [];
  })();

  const toolCalls = toolSteps.map((step) => ({
    id: step.id,
    name: step.name || step.id,
    status: 'success' as const,
    duration: step.duration ?? 0,
    args: step.args ? (typeof step.args === 'string' ? (() => { try { return JSON.parse(step.args); } catch { return {}; } })() : step.args) : {},
    result: step.result || step.content || '',
  }));

  // 查找文件查看源：先从 sources 找，再从 KB items 找
  const viewed = sources.find((s) => s.id === fileViewerSource) || null;
  const viewedKbItem = !viewed ? kbItems.find((item) => item.id === fileViewerSource) || null : null;

  if (viewed) {
    // Build page label: prefer real PDF pages, fallback to chunk index
    const pageLabel = viewed.page_start != null
      ? (viewed.page_end != null && viewed.page_end !== viewed.page_start
          ? `${t('pages')} ${viewed.page_start}-${viewed.page_end}`
          : `${t('page')} ${viewed.page_start}`)
      : `${t('chunk')} #${viewed.page}`;
    // Score: show actual relevance percentage (0-100)
    const displayScore = Math.round((viewed.score || 0) * 100);
    return (
      <div className="source-panel" id="sourcePanel">
        <div className="source-fv-header">
          <button className="source-fv-back" onClick={() => setFileViewerSource(null)}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('back')}</button>
          <span className="source-fv-title">{t('fileViewer')}</span>
        </div>
        <div className="source-fv-scroll">
          <div className="source-fv-name">{viewed.title}</div>
          {viewed.section_title ? <div className="source-fv-section">{viewed.section_title}</div> : null}
          <div className="source-fv-meta">
            {viewed.kb_name ? <span className="fv-kb-badge">{viewed.kb_name}</span> : null}
            {viewed.source_url ? <span>{viewed.source_url}</span> : null}
            {viewed.source_url ? <span className="fv-sep">·</span> : null}
            <span>{pageLabel}</span>
            <span className="fv-sep">·</span>
            <span>{t('relevance')} {displayScore}%</span>
            {viewed.category ? <span className="fv-sep">·</span> : null}
            {viewed.category ? <span className="fv-category">{viewed.category}</span> : null}
          </div>
          <div className="source-fv-content">{viewed.excerpt}</div>
        </div>
      </div>
    );
  }

  if (viewedKbItem) {
    return <ChunkViewer docId={viewedKbItem.id} docName={viewedKbItem.name} onBack={() => setFileViewerSource(null)} />;
  }

  return (
    <div className="source-panel" id="sourcePanel">
      <div className="source-header">
        <div style={{ display:'flex', alignItems:'center', marginBottom:8 }}>
          <span>{t('retrievalContext')}</span>
          <button className="source-collapse-btn" onClick={() => setRightMode('workbench')} title={t('switchWorkbench')}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
        </div>
        <div className="source-tabs">
          <button className={`source-tab ${tab === 'sources' ? 'active' : 'inactive'}`} id="tab-sources" onClick={() => setTab('sources')}>{t('sources')} <span className="tab-badge">{sources.length}</span></button>
          <button className={`source-tab ${tab === 'tools' ? 'active' : 'inactive'}`} id="tab-tools" onClick={() => setTab('tools')}>{t('toolCalls')} <span className="tab-badge">{toolCalls.length}</span></button>
        </div>
      </div>
      <div className="source-scroll" id="sourceScroll">
        {tab === 'sources' ? (
          sources.length ? sources.map((src, index) => {
            const scoreClass = src.score >= 0.9 ? 'high' : src.score >= 0.8 ? 'med' : 'low';
            return (
              <div
                className={`source-list-item ${scoreClass} ${highlightSourceId === src.id ? 'highlight' : ''}`}
                key={src.id}
                onClick={() => { setHighlightSourceId(src.id); setFileViewerSource(src.id); }}
                title={src.section_title ? `${src.title} · ${src.section_title}` : src.title}
              >
                <span className="source-list-num">{index + 1}</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)', flexShrink:0 }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <span className="source-list-title">{src.title}</span>
                <span className={`source-list-score ${scoreClass}`}>{(src.score * 100).toFixed(0)}%</span>
              </div>
            );
          }) : <div className="source-empty">{t('noSourceData')}</div>
        ) : (
          toolCalls.length ? toolCalls.map((tool) => {
            const open = !!toolOpen[tool.id];
            const isCodeResult = tool.result.length > 60 && /[#{};\/]/.test(tool.result);
            return (
              <div className="tool-card" key={tool.id}>
                <button className="tool-card-header" onClick={() => setToolOpen((s) => ({ ...s, [tool.id]: !s[tool.id] }))}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--muted-fg)', flexShrink:0 }}><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                  <span className="tool-card-name">{tool.name}()</span>
                  <span className="tool-duration">{formatDuration(tool.duration)}</span>
                </button>
                {open ? (
                  <div className="tool-card-body">
                    <div className="tool-card-args"><div className="tool-card-args-title">{t('params')}</div>{Object.entries(tool.args).map(([k, v]) => <div className="tool-card-arg-row" key={k}><span className="tool-card-arg-key">{k}:</span><span className="tool-card-arg-val">"{String(v)}"</span></div>)}</div>
                    <div className="tool-card-result">
                      <div className="tool-card-result-title">{t('result')}</div>
                      {isCodeResult ? (
                        <>
                          <div className="tool-code-output">{tool.result}</div>
                          <div className="tool-output-actions">
                            <button className="tool-output-btn" onClick={() => copyToClipboard(tool.result)}>{t('copy')}</button>
                            <button className="tool-output-btn" onClick={() => pushCodeToWorkbench(tool.result, tool.name)}>{t('pushToPreview')}</button>
                          </div>
                        </>
                      ) : (
                        <div className="tool-card-result-text">{tool.result}</div>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            );
          }) : <div className="source-empty">{t('noToolCalls')}</div>
        )}
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
          <div className="chunk-viewer-empty">{t('noChunks')}</div>
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
