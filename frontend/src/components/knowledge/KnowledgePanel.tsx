import { useRef, useState, useEffect } from "react";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import { useLogStore } from "../../stores/useLogStore";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { apiPost, apiUploadWithProgress } from "../../api/client";
import { useI18n } from "../../i18n";
import { formatFileSize } from "../../utils/format";
import { Modal } from "../shared/Modal";
import { KbCollectionManager } from "./KbCollectionManager";
import { UploadChunkMethodDialog } from "./UploadChunkMethodDialog";

const POLL_INTERVAL = 2000;
const POLL_TIMEOUT = 120000;

type UploadProgress = {
  phase: 'uploading' | 'indexing';
  percent: number;
  chunks: number;
  abort?: () => void;
};

function removeUploadProgressKey(
  prev: Record<string, UploadProgress>,
  key: string
): Record<string, UploadProgress> {
  const next = { ...prev };
  delete next[key];
  return next;
}

// Test KB name patterns that should be hidden from regular users by default.
const TEST_KB_PATTERNS: RegExp[] = [
  /^pdf-multimodal-test-/i,
  /^eval_hybrid-/i,
  /^eval_agent-/i,
  /^docx-test-/i,
  /^gapfix-test-/i,
  /^rag-eval-/i,
  /^rag-multimodal-review-/i,
  /-test-/i,
];

function isTestKb(name: string): boolean {
  return TEST_KB_PATTERNS.some((re) => re.test(name));
}

type KbLike = { name: string; is_builtin?: boolean };

function filterVisibleKbs<T extends KbLike>(kbs: T[], showTest: boolean): T[] {
  if (showTest) return kbs;
  return kbs.filter((kb) => kb.is_builtin || !isTestKb(kb.name));
}

export function KnowledgePanel() {
  const { t } = useI18n();
  const {
    items, isUploading, setIsUploading, addItem, toggleItem, deleteItemWithAPI, fetchItems,
    collections, activeKbId, fetchCollections, setActiveKb, fetchDocChunks,
  } = useKnowledgeStore();
  const { selectedKbIds, toggleKbSelection } = useChatStore();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [showKbManager, setShowKbManager] = useState(false);
  const [chunkMethodOverride, setChunkMethodOverride] = useState<string>("");
  const [showSearchScope, setShowSearchScope] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [showChunkMethodDialog, setShowChunkMethodDialog] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<Record<string, UploadProgress>>({});
  // Track active poll timers so they can be cleared on unmount
  const pollTimersRef = useRef<Set<number>>(new Set());

  // Load collections on mount
  useEffect(() => {
    fetchCollections();
  }, [fetchCollections]);

  // When active KB changes (including mount): reset chunk method override and reload items filtered by KB
  useEffect(() => {
    setChunkMethodOverride("");
    fetchItems(activeKbId);
  }, [activeKbId, fetchItems]);

  // Clear all poll timers on unmount to prevent memory leaks
  useEffect(() => {
    return () => {
      pollTimersRef.current.forEach((id) => clearTimeout(id));
      pollTimersRef.current.clear();
    };
  }, []);

  const enabledCount = items.filter((i) => i.enabled).length;
  const totalChunks = items.reduce((sum, i) => sum + i.chunks, 0);
  const visibleCollections = filterVisibleKbs(collections, false);

  // Active KB object (for default chunk method)
  const activeKb = collections.find((k) => k.id === activeKbId);
  const effectiveChunkMethod = chunkMethodOverride || activeKb?.chunk_method || "hybrid";

  const handleFileSelect = (files: FileList | null) => {
    if (!files?.length) return;
    setPendingFiles(Array.from(files));
    setShowChunkMethodDialog(true);
  };

  const handleConfirmChunkMethod = (method: string) => {
    setChunkMethodOverride(method);
    setShowChunkMethodDialog(false);
    if (pendingFiles) {
      handleFiles(pendingFiles, method);
      setPendingFiles(null);
    }
  };

  const handleCancelChunkMethod = () => {
    setShowChunkMethodDialog(false);
    setPendingFiles(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const handleFiles = async (files: FileList | null | File[], methodOverride?: string) => {
    if (!files?.length) return;
    setIsUploading(true);
    for (const file of Array.from(files)) {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("kb_id", activeKbId);
      const methodToUse = methodOverride || chunkMethodOverride;
      if (methodToUse) {
        formData.append("chunk_method", methodToUse);
      }
      const progressKey = `${file.name}-${Date.now()}`;
      try {
        const { promise, abort } = apiUploadWithProgress<{ doc_id: string; filename: string; chunks: number; status?: string; chunk_method_used?: string }>(
          "kb/upload",
          formData,
          (percent) => setUploadProgress((prev) => ({ ...prev, [progressKey]: { phase: 'uploading', percent, chunks: 0, abort } })),
        );
        // 记录 abort 函数,取消按钮要用
        setUploadProgress((prev) => ({ ...prev, [progressKey]: { phase: 'uploading', percent: 0, chunks: 0, abort } }));
        const res = await promise;
        const docId = res.doc_id ?? `kb-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
        const docStatus = (res.status ?? "indexed") as "indexed" | "indexing" | "error";
        addItem({
          id: docId,
          name: res.filename ?? file.name,
          size: formatFileSize(file.size),
          chunks: res.chunks ?? 0,
          status: docStatus,
          enabled: true,
          updatedAt: new Date().toISOString().slice(0, 10),
          docType: file.name.endsWith(".pdf") ? "Reference Manual" : file.name.endsWith(".md") ? "Markdown" : "Text",
          tags: [],
          kb_id: activeKbId,
          chunk_method_used: res.chunk_method_used ?? effectiveChunkMethod,
        });
        // 上传完成:切换到 indexing 阶段或清理 progress
        if (res.status === "indexing") {
          setUploadProgress((prev) => ({ ...prev, [progressKey]: { phase: 'indexing', percent: 100, chunks: res.chunks ?? 0 } }));
          pollIndexingStatus(docId, progressKey);
        } else {
          setUploadProgress((prev) => removeUploadProgressKey(prev, progressKey));
        }
      } catch (e) {
        const errMsg = e instanceof Error ? e.message : t('fileIncompatible');
        setUploadProgress((prev) => removeUploadProgressKey(prev, progressKey));
        if (errMsg === 'aborted') {
          useLogStore.getState().log("info", "kb", `上传已取消: ${file.name}`);
          continue;
        }
        useLogStore.getState().log("error", "kb", `上传失败: ${file.name} - ${errMsg}`);
        addItem({
          id: `kb-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          name: file.name,
          size: formatFileSize(file.size),
          chunks: 0,
          status: "error",
          enabled: false,
          updatedAt: new Date().toISOString().slice(0, 10),
          docType: "Technical Reference",
          tags: [],
          errorMessage: errMsg,
        });
      }
    }
    setIsUploading(false);
  };

  const pollIndexingStatus = (docId: string, progressKey?: string) => {
    const startTime = Date.now();

    const poll = async () => {
      if (Date.now() - startTime > POLL_TIMEOUT) {
        // 轮询超时，标记为 error
        useKnowledgeStore.getState().setItems(
          useKnowledgeStore.getState().items.map((item) =>
            item.id === docId ? { ...item, status: "error" as const, errorMessage: "索引超时" } : item
          )
        );
        if (progressKey) setUploadProgress((prev) => removeUploadProgressKey(prev, progressKey));
        pollTimersRef.current.delete(timerId);
        return;
      }

      try {
        // Use current activeKbId from store to keep KB filter consistent
        const currentKbId = useKnowledgeStore.getState().activeKbId;
        await fetchItems(currentKbId);
        const item = useKnowledgeStore.getState().items.find((i) => i.id === docId);
        if (item) {
          // 更新索引计数
          if (progressKey) {
            setUploadProgress((prev) => prev[progressKey] ? { ...prev, [progressKey]: { ...prev[progressKey], chunks: item.chunks } } : prev);
          }
          if (item.status === "indexed" || item.status === "error") {
            if (progressKey) setUploadProgress((prev) => removeUploadProgressKey(prev, progressKey));
            pollTimersRef.current.delete(timerId);
            return; // 向量化完成或出错，停止轮询
          }
        }
      } catch {
        // 轮询请求失败，继续重试
      }

      timerId = window.setTimeout(poll, POLL_INTERVAL);
      pollTimersRef.current.add(timerId);
    };

    let timerId = window.setTimeout(poll, POLL_INTERVAL);
    pollTimersRef.current.add(timerId);
  };

  const handlePreview = (itemId: string) => {
    useAppStore.getState().setActiveNav('chat');
    useAppStore.getState().setRightPanelOpen(true);
    useAppStore.getState().setRightMode('content');
    const sid = useChatStore.getState().activeSessionId;
    if (sid) {
      useChatStore.getState().setSessionFileViewerSource(sid, "kb-item", itemId);
    }
    // Pre-fetch chunks so the right-panel ChunkViewer has data ready immediately
    fetchDocChunks(itemId);
  };

  const handleRefresh = () => {
    fetchItems(activeKbId);
    fetchCollections();
  };

  return (
    <div className="content-page kb-page">
      <input ref={inputRef} type="file" multiple accept=".pdf,.md,.txt,.py,.c,.h,.ino,.xlsx,.xls,.csv,.json,.docx,.doc" style={{ display: "none" }} onChange={(e) => handleFileSelect(e.target.files)} />

      <div className="content-page-header kb-header-page">
        <div className="content-page-title-wrap">
          <div className="content-page-title">{t('knowledgeBase')}</div>
          <div className="content-page-stats">
            <span>{t('enabled')}</span>
            <span>{enabledCount} / {items.length}</span>
            <span>{totalChunks.toLocaleString()}</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-new" onClick={() => setShowKbManager(true)} title={t('manageKb')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ verticalAlign: "middle", marginRight: 4 }}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            {t('manageKb')}
          </button>
        </div>
      </div>

      {/* 上传/索引进度卡片 */}
      {Object.entries(uploadProgress).length > 0 && (
        <div style={{ margin: "0 16px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
          {Object.entries(uploadProgress).map(([key, p]) => {
            const fileName = key.replace(/-\d+$/, "");
            return (
              <div key={key} style={{
                padding: "8px 12px", borderRadius: 6, background: "var(--thinking-bg)",
                border: "1px solid var(--border)", fontSize: 12,
                display: "flex", alignItems: "center", gap: 10,
              }}>
                <span style={{ flexShrink: 0, color: "var(--muted-fg)", fontSize: 11, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {fileName}
                </span>
                {p.phase === 'uploading' ? (
                  <>
                    <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
                      <div style={{ width: `${p.percent}%`, height: "100%", background: "var(--primary)", transition: "width 0.2s", borderRadius: 3 }} />
                    </div>
                    <span style={{ flexShrink: 0, minWidth: 80 }}>{t('uploadingPercent').replace('{percent}', String(p.percent))}</span>
                  </>
                ) : (
                  <span style={{ flex: 1 }}>{t('indexingChunks').replace('{n}', String(p.chunks))}</span>
                )}
                <button
                  onClick={() => {
                    if (p.phase === 'uploading' && p.abort) p.abort();
                    else setUploadProgress((prev) => removeUploadProgressKey(prev, key));
                  }}
                  style={{ flexShrink: 0, fontSize: 11, color: "var(--danger)", background: "transparent", border: "1px solid var(--border)", borderRadius: 4, padding: "2px 8px", cursor: "pointer" }}
                >
                  {t('cancelUpload')}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Active KB info card */}
      {activeKb && (
        <div style={{
          margin: "0 16px 8px", padding: "8px 12px", borderRadius: 6,
          background: "var(--thinking-bg)", border: "1px solid var(--border)",
        }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2, wordBreak: "break-all" }}>
            {activeKb.name}
          </div>
          <div style={{ fontSize: 11, color: "var(--muted-fg)" }}>
            {activeKb.chunk_method === "agent" ? t('agent') : activeKb.chunk_method === "multimodal" ? t('multimodal') : t('hybrid')}
            {activeKb.embedding_model ? ` · ${activeKb.embedding_model}` : ""}
            {` · ${t('docCount')}: ${activeKb.doc_count}`}
            {` · ${t('chunkCount')}: ${activeKb.chunk_count}`}
          </div>
        </div>
      )}

      {/* KB selector + chunk method override + search scope */}
      <div style={{ padding: "0 16px 12px", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <label style={{ fontSize: 12, color: "var(--muted-fg)" }}>{t('targetKb')}:</label>
          <select
            value={activeKbId}
            onChange={(e) => setActiveKb(e.target.value)}
            style={{
              fontSize: 12, padding: "2px 6px", borderRadius: 4,
              border: "1px solid var(--border)", background: "var(--bg)", color: "var(--fg)",
            }}
          >
            {visibleCollections.length === 0 && <option value="builtin-001">{t('builtinKb')}</option>}
            {visibleCollections.map((kb) => (
              <option key={kb.id} value={kb.id}>
                {kb.name}{kb.is_builtin ? ` (${t('builtinKb')})` : ""}
              </option>
            ))}
          </select>
        </div>

        {/* Search scope toggle button */}
        <div style={{ position: "relative" }}>
          <button
            onClick={() => setShowSearchScope((v) => !v)}
            style={{
              fontSize: 12, padding: "3px 10px", borderRadius: 4,
              border: "1px solid var(--border)", background: selectedKbIds.length > 0 ? "var(--accent)" : "var(--card)",
              color: "var(--fg)", cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
            {t('chatSearchScope')}
            {selectedKbIds.length > 0 && <span style={{ fontSize: 10, background: "var(--primary)", color: "var(--primary-fg)", padding: "0 4px", borderRadius: 3 }}>{selectedKbIds.length}</span>}
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ transform: showSearchScope ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          {showSearchScope && (
            <div style={{
              position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 10,
              minWidth: 220, maxHeight: 240, overflowY: "auto",
              background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6,
              padding: "8px", boxShadow: "var(--shadow-md)",
            }}>
              <div style={{ fontSize: 11, color: "var(--muted-fg)", marginBottom: 6 }}>{t('searchAllHint')}</div>
              {visibleCollections.length > 0 ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {visibleCollections.map((kb) => (
                    <label
                      key={kb.id}
                      style={{
                        display: "flex", alignItems: "center", gap: 6,
                        padding: "3px 6px", borderRadius: 4, cursor: "pointer",
                        background: selectedKbIds.includes(kb.id) ? "var(--accent)" : "transparent",
                        fontSize: 12,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedKbIds.includes(kb.id)}
                        onChange={() => toggleKbSelection(kb.id)}
                        style={{ margin: 0 }}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{kb.name}</span>
                      {kb.is_builtin && (
                        <span style={{ fontSize: 9, padding: "1px 3px", borderRadius: 2, background: "var(--primary)", color: "var(--primary-fg)", flexShrink: 0 }}>
                          {t('builtinKb')}
                        </span>
                      )}
                    </label>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: 11, color: "var(--muted-fg)", padding: "4px 0" }}>{t('noKb')}</div>
              )}
            </div>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>
            {t('chunkMethod')}: {activeKb?.chunk_method === "agent" ? t('agent') : activeKb?.chunk_method === "multimodal" ? t('multimodal') : t('hybrid')}
          </span>
        </div>
      </div>

      <div className="content-page-scroll kb-scroll-page">
        {items.map((item) => (
          <div className="kb-item" key={item.id}>
            <div className="kb-item-row-main">
              <div className="kb-item-left">
                <div className="kb-item-top">
                  <div className="kb-item-name-wrap">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)' }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    <span className={`kb-item-name ${item.enabled ? 'enabled' : 'disabled'}`} aria-label={`${item.name}, ${item.docType}, ${item.enabled ? t('enabled') : t('disabled')}, ${item.status === 'indexing' ? t('indexing') : item.status === 'error' ? t('indexFailed') : `${item.chunks.toLocaleString()} ${t('indexed')}`}`}>{item.name}</span>
                  </div>
                </div>
                <div className="kb-item-status">
                  <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                    <span className={`kb-status-dot ${item.status}`}></span>
                    <span className="kb-status-label">{item.status === 'indexing' ? t('indexing') : item.status === 'error' ? t('indexFailed') : `${item.chunks.toLocaleString()} ${t('indexed')}`}</span>
                  </div>
                </div>
                <div className="kb-extended">
                  <span>{item.docType}</span>
                  {item.chunk_method_used && <span className="kb-tag">{item.chunk_method_used}</span>}
                  {item.tags.map((tag) => <span className="kb-tag" key={tag}>{tag}</span>)}
                </div>
                <div className="kb-item-meta"><span>{item.size}</span></div>
                {item.errorMessage ? <div className="kb-error"><span style={{ color: 'var(--danger)' }}>◉</span>{item.errorMessage}</div> : null}
              </div>

              <div className="kb-item-right">
                <button className="kb-item-icon-btn" title={t('previewBtn')} onClick={() => handlePreview(item.id)}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
                <button className="kb-item-icon-btn" title={t('refreshBtn')} onClick={handleRefresh}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                </button>
                <button className="kb-item-icon-btn" title={t('delete')} onClick={() => setDeleteConfirmId(item.id)}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
                <div className={`toggle-switch ${item.enabled ? 'on' : 'off'}`} onClick={() => toggleItem(item.id)}><span className="toggle-knob"></span></div>
                <span className="kb-item-date">{item.updatedAt}</span>
              </div>
            </div>
          </div>
        ))}

        <div className="kb-footer-upload">
          <div
            className={`kb-upload-large${dragOver ? ' drag-over' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFileSelect(e.dataTransfer.files); }}
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M4 21h16"/></svg>
            <div style={{ fontSize: 13 }}>{isUploading ? t('uploading') : t('uploadDoc')}</div>
            {effectiveChunkMethod === "agent" && (
              <div style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 4 }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ verticalAlign: "middle", marginRight: 4 }}><path d="M9 18h6"/><path d="M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/></svg>
                {t('agentChunkHint')}
              </div>
            )}
          </div>
        </div>
      </div>

      <KbCollectionManager open={showKbManager} onClose={() => setShowKbManager(false)} />

      {showChunkMethodDialog && pendingFiles && (
        <UploadChunkMethodDialog
          files={pendingFiles}
          defaultMethod={activeKb?.chunk_method || "hybrid"}
          onConfirm={handleConfirmChunkMethod}
          onCancel={handleCancelChunkMethod}
        />
      )}

      {deleteConfirmId && (() => {
        const item = items.find((i) => i.id === deleteConfirmId);
        if (!item) return null;
        return (
          <Modal onClose={() => setDeleteConfirmId(null)}>
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                background: "var(--bg)", borderRadius: 8, padding: 20,
                width: "90%", maxWidth: 400, margin: "auto",
                border: "1px solid var(--border)",
                boxShadow: "var(--shadow-lg)",
              }}
            >
              <h3 style={{ margin: "0 0 12px", fontSize: 15, fontWeight: 600 }}>
                {t('deleteDocConfirmTitle')}
              </h3>
              <p style={{ margin: "0 0 18px", fontSize: 13, color: "var(--muted-fg)", lineHeight: 1.5 }}>
                {t('deleteDocConfirmMsg').replace('{name}', item.name)}
              </p>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button
                  className="kb-item-icon-btn"
                  onClick={() => setDeleteConfirmId(null)}
                  style={{ padding: "6px 14px", fontSize: 12, border: "1px solid var(--border)", borderRadius: 6 }}
                >
                  {t('cancel')}
                </button>
                <button
                  onClick={async () => {
                    const id = deleteConfirmId;
                    setDeleteConfirmId(null);
                    await deleteItemWithAPI(id);
                  }}
                  style={{
                    padding: "6px 14px", fontSize: 12, borderRadius: 6,
                    background: "var(--danger)", color: "var(--primary-fg)",
                    border: "none", cursor: "pointer", fontWeight: 500,
                  }}
                >
                  {t('delete')}
                </button>
              </div>
            </div>
          </Modal>
        );
      })()}
    </div>
  );
}
