import { useRef, useState, useEffect } from "react";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import { useLogStore } from "../../stores/useLogStore";
import { useAppStore } from "../../stores/useAppStore";
import { apiPost } from "../../api/client";
import { useI18n } from "../../i18n";

export function KnowledgePanel() {
  const { t } = useI18n();
  const { items, isUploading, setIsUploading, addItem, toggleItem, deleteItemWithAPI, fetchItems } = useKnowledgeStore();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  // 组件挂载时从 API 加载 KB 列表
  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const enabledCount = items.filter((i) => i.enabled).length;
  const totalChunks = items.reduce((sum, i) => sum + i.chunks, 0);

  const handleFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setIsUploading(true);
    for (const file of Array.from(files)) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await apiPost<{ doc_id: string; filename: string; chunks: number; status?: string }>("kb/upload", formData, 120000);
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
        });
        // 如果后端返回 indexing 状态，启动轮询等待向量化完成
        if (res.status === "indexing") {
          pollIndexingStatus(docId);
        }
      } catch {
      useLogStore.getState().log("warn", "kb", `轮询索引状态失败`);
      useLogStore.getState().log("error", "kb", `上传失败: ${file.name}`);
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
          errorMessage: t('fileIncompatible'),
        });
      }
    }
    setIsUploading(false);
  };

  const pollIndexingStatus = (docId: string) => {
    const POLL_INTERVAL = 2000;
    const POLL_TIMEOUT = 120000;
    const startTime = Date.now();

    const poll = async () => {
      if (Date.now() - startTime > POLL_TIMEOUT) {
        // 轮询超时，标记为 error
        useKnowledgeStore.getState().setItems(
          useKnowledgeStore.getState().items.map((item) =>
            item.id === docId ? { ...item, status: "error" as const, errorMessage: "索引超时" } : item
          )
        );
        return;
      }

      try {
        await fetchItems();
        const item = useKnowledgeStore.getState().items.find((i) => i.id === docId);
        if (item && (item.status === "indexed" || item.status === "error")) {
          return; // 向量化完成或出错，停止轮询
        }
      } catch {
        // 轮询请求失败，继续重试
      }

      setTimeout(poll, POLL_INTERVAL);
    };

    setTimeout(poll, POLL_INTERVAL);
  };

  const handlePreview = (itemId: string) => {
    useAppStore.getState().setActiveNav('chat');
    useAppStore.getState().setRightPanelOpen(true);
    useAppStore.getState().setRightMode('content');
    useAppStore.getState().setFileViewerSource(itemId);
  };

  return (
    <div className="content-page kb-page">
      <input ref={inputRef} type="file" multiple accept=".pdf,.md,.txt,.py,.c,.h,.ino,.xlsx,.xls,.csv,.json" style={{ display: "none" }} onChange={(e) => handleFiles(e.target.files)} />

      <div className="content-page-header kb-header-page">
        <div className="content-page-title-wrap">
          <div className="content-page-title">{t('knowledgeBase')}</div>
          <div className="content-page-stats">
            <span>{t('enabled')}</span>
            <span>{enabledCount} / {items.length}</span>
            <span>{totalChunks.toLocaleString()}</span>
          </div>
        </div>
        <button className="btn-new" onClick={() => inputRef.current?.click()}>+ {t('add')}</button>
      </div>

      <div className="content-page-scroll kb-scroll-page">
        {items.map((item) => (
          <div className="kb-item" key={item.id}>
            <div className="kb-item-row-main">
              <div className="kb-item-left">
                <div className="kb-item-top">
                  <div className="kb-item-name-wrap">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color:'var(--primary)' }}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                    <span className={`kb-item-name ${item.enabled ? 'enabled' : 'disabled'}`}>{item.name}</span>
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
                  {item.tags.map((t) => <span className="kb-tag" key={t}>{t}</span>)}
                </div>
                <div className="kb-item-meta"><span>{item.size}</span></div>
                {item.errorMessage ? <div className="kb-error"><span style={{ color: 'var(--danger)' }}>◉</span>{item.errorMessage}</div> : null}
              </div>

              <div className="kb-item-right">
                <button className="kb-item-icon-btn" title={t('previewBtn')} onClick={() => handlePreview(item.id)}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
                <button className="kb-item-icon-btn" title={t('refreshBtn')}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                </button>
                <button className="kb-item-icon-btn" title={t('delete')} onClick={() => deleteItemWithAPI(item.id)}>
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
            onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M4 21h16"/></svg>
            <div style={{ fontSize: 13 }}>{isUploading ? t('uploading') : t('uploadDoc')}</div>
          </div>
          <button className="kb-upload-bottom-btn" onClick={() => inputRef.current?.click()}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M4 21h16"/></svg>
            {t('uploadDoc')}
          </button>
        </div>
      </div>
    </div>
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
