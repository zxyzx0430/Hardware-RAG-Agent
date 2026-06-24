import { useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
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
    return (
      <div className="source-panel" id="sourcePanel">
        <div className="source-fv-header">
          <button className="source-fv-back" onClick={() => setFileViewerSource(null)}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('back')}</button>
          <span className="source-fv-title">{t('fileViewer')}</span>
        </div>
        <div className="source-fv-scroll">
          <div className="source-fv-name">{viewed.title}</div>
          <div className="source-fv-meta"><span>{viewed.doc}</span><span className="fv-sep">·</span><span>p.{viewed.page}</span><span className="fv-sep">·</span><span>{t('relevance')} {(viewed.score * 100).toFixed(0)}%</span></div>
          <div className="source-fv-content">{viewed.excerpt}</div>
        </div>
      </div>
    );
  }

  if (viewedKbItem) {
    return (
      <div className="source-panel" id="sourcePanel">
        <div className="source-fv-header">
          <button className="source-fv-back" onClick={() => setFileViewerSource(null)}><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15 18 9 12 15 6"/></svg> {t('back')}</button>
          <span className="source-fv-title">{t('kbDocPreview')}</span>
        </div>
        <div className="source-fv-scroll">
          <div className="source-fv-name">{viewedKbItem.name}</div>
          <div className="source-fv-meta" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 4 }}>
            <span>{viewedKbItem.docType}</span>
            <span className="fv-sep">·</span>
            <span>{viewedKbItem.size}</span>
            <span className="fv-sep">·</span>
            <span>{viewedKbItem.chunks} {t('indexed')}</span>
            <span className="fv-sep">·</span>
            <span className={`kb-status-dot ${viewedKbItem.status}`} style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', verticalAlign: 'middle' }}></span>
            <span>{viewedKbItem.status === 'indexed' ? t('indexed') : viewedKbItem.status === 'indexing' ? t('indexing') : t('indexFailed')}</span>
          </div>
          {viewedKbItem.tags.length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 8 }}>
              {viewedKbItem.tags.map((tag) => <span className="kb-tag" key={tag}>{tag}</span>)}
            </div>
          )}
          <div style={{ marginTop: 12, fontSize: 12, color: 'var(--muted-fg)' }}>
            <div>{t('updatedAt')}: {viewedKbItem.updatedAt}</div>
            <div>{t('enabled')}: {viewedKbItem.enabled ? '✓' : '✗'}</div>
          </div>
          {viewedKbItem.errorMessage && (
            <div style={{ marginTop: 8, color: 'var(--danger)', fontSize: 12 }}>{viewedKbItem.errorMessage}</div>
          )}
        </div>
      </div>
    );
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
          sources.length ? sources.map((src) => {
            const scoreClass = src.score >= 0.9 ? 'high' : src.score >= 0.8 ? 'med' : 'low';
            return (
              <div
                className={`source-card ${highlightSourceId === src.id ? 'highlight' : 'default'}`}
                key={src.id}
                onClick={() => setHighlightSourceId(src.id)}
              >
                <div className="source-card-body">
                  <div className="source-card-row">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)', flexShrink:0, marginTop:2 }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    <div style={{ flex:1, minWidth:0 }}>
                      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:4 }}>
                        <span className="source-card-title">
                          {src.kb_name ? <span className="source-kb-tag">{src.kb_name}</span> : null}
                          {src.title}
                        </span>
                        <span className={`source-card-score ${scoreClass}`}>{(src.score * 100).toFixed(0)}%</span>
                      </div>
                      <div className="source-card-meta"><span>{src.doc}</span><span className="sep">·</span><span>p.{src.page}</span></div>
                    </div>
                    <button className="source-open-btn" onClick={(event) => { event.stopPropagation(); setHighlightSourceId(src.id); setFileViewerSource(src.id); }}>{t('open')}</button>
                  </div>
                </div>
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
