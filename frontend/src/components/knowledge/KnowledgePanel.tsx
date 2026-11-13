import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { KBDoc } from "../../types/api";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";
import { useLogStore } from "../../stores/useLogStore";
import { useToastStore } from "../../stores/useToastStore";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { apiUploadWithProgress } from "../../api/client";
import { useI18n } from "../../i18n";
import { formatFileSize } from "../../utils/format";
import { Modal } from "../shared/Modal";
import { KbCollectionManager } from "./KbCollectionManager";
import { UploadChunkMethodDialog } from "./UploadChunkMethodDialog";
import "../../styles/knowledge.css";

type StatusFilter = "all" | "indexed" | "indexing" | "error";
type StatusKind = Exclude<StatusFilter, "all">;

function statusKind(status: KBDoc["status"]): StatusKind {
  return status === "indexing" || status === "error" ? status : "indexed";
}

function matchesQuery(doc: KBDoc, query: string): boolean {
  if (!query) return true;
  return [doc.name, doc.docType, ...doc.tags].some((value) => value.toLowerCase().includes(query));
}

function Icon({ children, size = 13 }: { children: ReactNode; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

function DocRow({
  item, kbName, onPreview, onRefresh, onDelete, onToggle,
}: {
  item: KBDoc;
  kbName?: string;
  onPreview: () => void;
  onRefresh: () => void;
  onDelete: () => void;
  onToggle: () => void;
}) {
  const { t, lang } = useI18n();
  const kind = statusKind(item.status);
  const statusText = kind === "indexing"
    ? (lang === "zh" ? "索引中" : "Indexing")
    : kind === "error"
      ? (lang === "zh" ? "失败" : "Failed")
      : (lang === "zh" ? "已索引" : "Indexed");
  const isMarkdown = /\.(md|markdown)$/i.test(item.name) || /markdown/i.test(item.docType);
  return (
    <div className="kbv-row">
      <div className="kbv-doc">
        <span className={`kbv-doc-icon${isMarkdown ? " md" : ""}`}>
          <Icon size={15}>{isMarkdown ? <><path d="M4 6h16M4 12h10M4 18h16" /></> : <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></>}</Icon>
        </span>
        <div className="kbv-doc-info">
          <div className="kbv-doc-name" title={item.name}>{item.name}</div>
          <div className="kbv-doc-sub">
            <span className="kbv-tag" title={item.docType}>{item.docType}</span>
            {item.chunk_method_used ? <span className="kbv-tag kbv-tag-mono" title={item.chunk_method_used}>{item.chunk_method_used}</span> : null}
            {kbName ? <span className="kbv-tag" title={kbName}>{kbName}</span> : null}
            {item.tags.slice(0, 2).map((tag) => <span className="kbv-tag" title={tag} key={tag}>{tag}</span>)}
          </div>
          {kind === "error" && item.errorMessage ? <div className="kbv-doc-error" title={item.errorMessage}>{item.errorMessage}</div> : null}
        </div>
      </div>
      <span className={`kbv-status ${kind}`}><span className="kbv-dot" />{statusText}</span>
      <span className="kbv-cell kbv-num">{item.chunks.toLocaleString()}</span>
      <span className="kbv-cell">{item.size}</span>
      <span className="kbv-cell">{item.updatedAt}</span>
      <div className="kbv-rowactions">
        <button type="button" className="kbv-iconbtn" title={t("previewBtn")} aria-label={t("previewBtn")} onClick={onPreview}>
          <Icon><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></Icon>
        </button>
        <button type="button" className="kbv-iconbtn" title={t("refreshBtn")} aria-label={t("refreshBtn")} onClick={onRefresh}>
          <Icon><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></Icon>
        </button>
        <button type="button" className="kbv-iconbtn kbv-iconbtn-danger" title={t("delete")} aria-label={t("delete")} onClick={onDelete}>
          <Icon><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></Icon>
        </button>
        <button
          type="button"
          role="switch"
          aria-checked={item.enabled}
          className={`kbv-switch${item.enabled ? " on" : ""}`}
          title={t("enabled")}
          onClick={onToggle}
        >
          <span className="kbv-knob" />
        </button>
      </div>
    </div>
  );
}

export function KnowledgePanel() {
  const { t, lang } = useI18n();
  const {
    items, isUploading, setIsUploading, addItem, toggleItem, deleteItemWithAPI, fetchItems,
    collections, activeKbId, fetchCollections, fetchDocChunks,
    uploadProgress, setUploadProgress, removeUploadProgress, pollIndexingStatus,
  } = useKnowledgeStore();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [dragOver, setDragOver] = useState(false);
  const [showKbManager, setShowKbManager] = useState(false);
  const [chunkMethodOverride, setChunkMethodOverride] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [showChunkMethodDialog, setShowChunkMethodDialog] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KBDoc | null>(null);

  useEffect(() => {
    fetchCollections();
    fetchItems();
    return () => useKnowledgeStore.getState().stopPolling();
  }, [fetchCollections, fetchItems]);

  const enabledCount = items.filter((item) => item.enabled).length;
  const totalChunks = items.reduce((sum, item) => sum + item.chunks, 0);
  const lastUpdated = items.reduce((latest, item) => (item.updatedAt > latest ? item.updatedAt : latest), "");
  const kbNameById = useMemo(() => new Map(collections.map((kb) => [kb.id, kb.name])), [collections]);
  const activeKb = collections.find((kb) => kb.id === activeKbId);
  const effectiveChunkMethod = chunkMethodOverride || activeKb?.chunk_method || "hybrid";
  const filteredItems = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((doc) => (statusFilter === "all" || statusKind(doc.status) === statusFilter) && matchesQuery(doc, needle));
  }, [items, statusFilter, query]);

  const handleFiles = async (files: FileList | File[], methodOverride?: string) => {
    if (!files?.length) return;
    setIsUploading(true);
    for (const file of Array.from(files)) await uploadOne(file, methodOverride || chunkMethodOverride);
    setIsUploading(false);
  };

  const uploadOne = async (file: File, method: string) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("kb_id", activeKbId);
    if (method) formData.append("chunk_method", method);
    const progressKey = `${file.name}-${Date.now()}`;
    try {
      const { promise, abort } = apiUploadWithProgress<{ doc_id: string; filename: string; chunks: number; status?: string; chunk_method_used?: string }>(
        "kb/upload",
        formData,
        (percent) => setUploadProgress((prev) => ({ ...prev, [progressKey]: { phase: "uploading", percent, chunks: 0, abort } })),
      );
      setUploadProgress((prev) => ({ ...prev, [progressKey]: { phase: "uploading", percent: 0, chunks: 0, abort } }));
      const res = await promise;
      const docId = res.doc_id ?? `kb-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const docStatus = (res.status ?? "indexing") as KBDoc["status"];
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
      if (docStatus === "indexing") {
        setUploadProgress((prev) => ({ ...prev, [progressKey]: { phase: "indexing", percent: 100, chunks: res.chunks ?? 0 } }));
        pollIndexingStatus(docId, progressKey);
      } else {
        removeUploadProgress(progressKey);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : t("fileIncompatible");
      removeUploadProgress(progressKey);
      if (message === "aborted") {
        useLogStore.getState().log("info", "kb", `上传已取消: ${file.name}`);
        return;
      }
      useLogStore.getState().log("error", "kb", `上传失败: ${file.name} - ${message}`);
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
        kb_id: activeKbId,
        errorMessage: message,
      });
    }
  };

  const handleFileSelect = (files: FileList | null) => {
    if (!files?.length) { useToastStore.getState().showWarning("请选择文件"); return; }
    setPendingFiles(Array.from(files));
    setShowChunkMethodDialog(true);
  };

  const handleConfirmChunkMethod = (method: string) => {
    setChunkMethodOverride(method);
    setShowChunkMethodDialog(false);
    if (pendingFiles) void handleFiles(pendingFiles, method);
    setPendingFiles(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const handleCancelChunkMethod = () => {
    setShowChunkMethodDialog(false);
    setPendingFiles(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const handlePreview = (itemId: string) => {
    useAppStore.getState().setActiveNav("chat");
    useAppStore.getState().setRightPanelOpen(true);
    useAppStore.getState().setRightMode("content");
    const sid = useChatStore.getState().activeSessionId;
    if (sid) useChatStore.getState().setSessionFileViewerSource(sid, "kb-item", itemId);
    void fetchDocChunks(itemId);
  };

  const handleRefresh = () => {
    void fetchItems();
    void fetchCollections();
  };

  const handleDelete = async () => {
    const target = deleteTarget;
    if (!target) return;
    setDeleteTarget(null);
    try {
      await deleteItemWithAPI(target.id);
    } catch {
      // Store logs the failure and rolls the row back.
    }
  };

  const statusOptions: { key: StatusFilter; label: string }[] = [
    { key: "all", label: t("all") },
    { key: "indexed", label: lang === "zh" ? "已索引" : "Indexed" },
    { key: "indexing", label: lang === "zh" ? "索引中" : "Indexing" },
    { key: "error", label: lang === "zh" ? "失败" : "Failed" },
  ];

  return (
    <div className="kbv-page">
      <input ref={inputRef} type="file" multiple accept=".pdf,.md,.txt,.py,.c,.h,.ino,.xlsx,.xls,.csv,.json,.docx,.doc" style={{ display: "none" }} onChange={(e) => handleFileSelect(e.target.files)} />

      <header className="kbv-head">
        <div className="kbv-head-main">
          <h1 className="kbv-title">{lang === "zh" ? "全部文档" : "All documents"}</h1>
          <p className="kbv-desc">{lang === "zh" ? "所有知识库中的文档与索引状态" : "Documents and index status across every knowledge base"}</p>
          <div className="kbv-head-meta">
            <span className="kbv-meta-item">{items.length} {lang === "zh" ? "个文档" : "documents"}</span>
            <span className="kbv-meta-item">{totalChunks.toLocaleString()} chunks</span>
            <span className="kbv-meta-item">{collections.length} {lang === "zh" ? "个知识库" : "knowledge bases"}</span>
          </div>
        </div>
        <div className="kbv-actions">
          <button type="button" className="kbv-btn kbv-btn-primary" onClick={() => inputRef.current?.click()}>
            <Icon><path d="M12 3v12M7 8l5-5 5 5M4 21h16" /></Icon>
            {t("uploadDoc")}
          </button>
          <button type="button" className="kbv-btn" onClick={() => setShowKbManager(true)} title={t("manageKb")}>
            <Icon><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" /></Icon>
            {t("manageKb")}
          </button>
        </div>
      </header>

      <div className="kbv-scroll">
        {Object.keys(uploadProgress).length > 0 ? (
          <div className="kbv-progress">
            {Object.entries(uploadProgress).map(([key, progress]) => {
              const fileName = key.replace(/-\d+$/, "");
              return (
                <div className="kbv-progress-row" key={key}>
                  <span className="kbv-progress-name" title={fileName}>{fileName}</span>
                  {progress.phase === "uploading" ? (
                    <>
                      <span className="kbv-progress-track"><span className="kbv-progress-fill" style={{ width: `${progress.percent}%` }} /></span>
                      <span className="kbv-progress-text">{t("uploadingPercent").replace("{percent}", String(progress.percent))}</span>
                    </>
                  ) : (
                    <span className="kbv-progress-text">
                      {progress.chunks > 0 ? t("indexingChunks").replace("{n}", String(progress.chunks)) : t("indexing")}
                    </span>
                  )}
                  <button
                    type="button"
                    className="kbv-progress-cancel"
                    onClick={() => {
                      if (progress.phase === "uploading" && progress.abort) progress.abort();
                      else removeUploadProgress(key);
                    }}
                  >
                    {t("cancelUpload")}
                  </button>
                </div>
              );
            })}
          </div>
        ) : null}

        <div className="kbv-stats">
          <div className="kbv-stat"><div className="kbv-stat-label">{lang === "zh" ? "文档总数" : "Documents"}</div><div className="kbv-stat-value">{items.length}</div></div>
          <div className="kbv-stat"><div className="kbv-stat-label">{t("enabled")}</div><div className="kbv-stat-value">{enabledCount}</div></div>
          <div className="kbv-stat"><div className="kbv-stat-label">Chunks</div><div className="kbv-stat-value">{totalChunks.toLocaleString()}</div></div>
          <div className="kbv-stat"><div className="kbv-stat-label">{lang === "zh" ? "最近更新" : "Last updated"}</div><div className="kbv-stat-value kbv-stat-date">{lastUpdated || "—"}</div></div>
        </div>

        <div className="kbv-toolbar">
          <div className="kbv-search">
            <Icon><circle cx="11" cy="11" r="7" /><path d="m21 21-4.35-4.35" /></Icon>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={lang === "zh" ? "搜索文档名、类型或标签…" : "Search name, type, or tag…"}
              aria-label={lang === "zh" ? "搜索文档" : "Search documents"}
            />
          </div>
          <div className="kbv-chips" role="group" aria-label={lang === "zh" ? "状态筛选" : "Status filter"}>
            {statusOptions.map((option) => (
              <button
                type="button"
                key={option.key}
                className={`kbv-chip${statusFilter === option.key ? " active" : ""}`}
                aria-pressed={statusFilter === option.key}
                onClick={() => setStatusFilter(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button type="button" className="kbv-btn kbv-refresh" onClick={handleRefresh} title={t("refreshBtn")}>
            <Icon><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></Icon>
            {t("refreshBtn")}
          </button>
        </div>

        <div className="kbv-list">
          <div className="kbv-rowhead" aria-hidden="true">
            <span>{lang === "zh" ? "文档" : "Document"}</span>
            <span>{lang === "zh" ? "状态" : "Status"}</span>
            <span>Chunks</span>
            <span>{lang === "zh" ? "大小" : "Size"}</span>
            <span>{lang === "zh" ? "更新" : "Updated"}</span>
            <span className="kbv-rowhead-actions">{lang === "zh" ? "操作" : "Actions"}</span>
          </div>
          {filteredItems.map((item) => (
            <DocRow
              key={item.id}
              item={item}
              kbName={item.kb_id ? kbNameById.get(item.kb_id) : undefined}
              onPreview={() => handlePreview(item.id)}
              onRefresh={handleRefresh}
              onDelete={() => setDeleteTarget(item)}
              onToggle={() => toggleItem(item.id)}
            />
          ))}
        </div>
        {filteredItems.length === 0 ? (
          <div className="kbv-empty">
            <Icon size={26}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></Icon>
            <span>{items.length === 0 ? (lang === "zh" ? "还没有文档，上传一个开始构建知识库" : "No documents yet. Upload one to get started.") : (lang === "zh" ? "没有匹配的文档" : "No matching documents")}</span>
          </div>
        ) : null}

        <div
          className={`kbv-upload${dragOver ? " drag-over" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFileSelect(e.dataTransfer.files); }}
        >
          <Icon size={20}><path d="M12 3v12M7 8l5-5 5 5M4 21h16" /></Icon>
          <span>{isUploading ? t("uploading") : (lang === "zh" ? "拖拽 PDF / DOCX / Markdown 到此处，或点击上传" : "Drop PDF / DOCX / Markdown here, or click to upload")}</span>
          {effectiveChunkMethod === "agent" ? <span className="kbv-upload-hint">{t("agentChunkHint")}</span> : null}
        </div>
      </div>

      <KbCollectionManager open={showKbManager} onClose={() => setShowKbManager(false)} />

      {showChunkMethodDialog && pendingFiles ? (
        <UploadChunkMethodDialog
          files={pendingFiles}
          defaultMethod={activeKb?.chunk_method || "hybrid"}
          onConfirm={handleConfirmChunkMethod}
          onCancel={handleCancelChunkMethod}
        />
      ) : null}

      {deleteTarget ? (
        <Modal onClose={() => setDeleteTarget(null)}>
          <div className="kbv-dialog-wrap">
            <div className="kbv-dialog" onClick={(e) => e.stopPropagation()}>
              <h3 className="kbv-dialog-title">{t("deleteDocConfirmTitle")}</h3>
              <p className="kbv-dialog-text">{t("deleteDocConfirmMsg").replace("{name}", deleteTarget.name)}</p>
              <div className="kbv-dialog-actions">
                <button type="button" className="kbv-btn" onClick={() => setDeleteTarget(null)}>{t("cancel")}</button>
                <button type="button" className="kbv-btn kbv-btn-danger" onClick={handleDelete}>{t("delete")}</button>
              </div>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
