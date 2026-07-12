import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MonacoEditor from "@monaco-editor/react";
import { useAppStore } from "../../stores/useAppStore";
import { useToastStore } from "../../stores/useToastStore";
import { useI18n } from "../../i18n";
import { useAppliedDarkMode } from "../shared/MarkdownRenderer";
import { Modal } from "../shared/Modal";
import { DiffPanel } from "./DiffPanel";
import { fileLanguage } from "./fileLanguage";
import { ContextMenu, type MenuItem } from "../shared/ContextMenu";
import { copyToClipboard } from "../../utils/clipboard";
import { relativePath } from "./treeUtils";
import { apiPost } from "../../api/client";
import { CloseConfirmDialog, EditorTabItem, ImagePreview } from "./editorTabParts";
import {
  orderedTabs,
  reorder,
  useRestoreFileContents,
  type ContextMenuPos,
} from "./editorTabUtils";

export function EditorPanel() {
  const { t } = useI18n();
  const openFiles = useAppStore((s) => s.openFiles);
  const activeFileId = useAppStore((s) => s.activeFileId);
  const pinnedFileIds = useAppStore((s) => s.pinnedFileIds);
  const explorerRootPath = useAppStore((s) => s.explorerRootPath);
  const editorShowTree = useAppStore((s) => s.editorShowTree);
  const setActiveFile = useAppStore((s) => s.setActiveFile);
  const closeFile = useAppStore((s) => s.closeFile);
  const pinFile = useAppStore((s) => s.pinFile);
  const unpinFile = useAppStore((s) => s.unpinFile);
  const markFileDirty = useAppStore((s) => s.markFileDirty);
  const setFileContent = useAppStore((s) => s.setFileContent);
  const saveFile = useAppStore((s) => s.saveFile);
  const setEditorShowTree = useAppStore((s) => s.setEditorShowTree);
  const diffViewOpen = useAppStore((s) => s.diffViewOpen);
  const diffViewFileId = useAppStore((s) => s.diffViewFileId);
  const openDiffForFile = useAppStore((s) => s.openDiffForFile);
  const closeDiffView = useAppStore((s) => s.closeDiffView);

  const isDark = useAppliedDarkMode();
  const themeMode = useAppStore((s) => s.themeMode);
  const editorTheme =
    themeMode === "dark" || (themeMode === "auto" && isDark) ? "vs-dark" : "vs-light";

  const [closingId, setClosingId] = useState<string | null>(null);
  const [batchQueue, setBatchQueue] = useState<string[]>([]);
  const [contextMenu, setContextMenu] = useState<ContextMenuPos | null>(null);
  const [tabOrder, setTabOrder] = useState<string[]>([]);
  const [dragId, setDragId] = useState<string | null>(null);

  const showError = useCallback((message: string) => {
    useToastStore.getState().showError(message);
  }, []);
  const showSuccess = useCallback((message: string) => {
    useToastStore.getState().showSuccess(message);
  }, []);

  // Keep a ref to the latest context menu so action callbacks don't need it in deps.
  const contextMenuRef = useRef<ContextMenuPos | null>(null);
  useEffect(() => {
    contextMenuRef.current = contextMenu;
  }, [contextMenu]);

  const activeFile = useMemo(
    () => openFiles.find((f) => f.id === activeFileId) ?? null,
    [openFiles, activeFileId],
  );
  const diffFile = useMemo(
    () => openFiles.find((f) => f.id === diffViewFileId) ?? null,
    [openFiles, diffViewFileId],
  );
  const orderedFiles = useMemo(
    () => orderedTabs(openFiles, pinnedFileIds, tabOrder),
    [openFiles, pinnedFileIds, tabOrder],
  );
  const activeLanguage = useMemo(() => {
    if (!activeFile) return "text";
    return activeFile.language ?? fileLanguage(activeFile.path);
  }, [activeFile]);

  // Keep tabOrder in sync with openFiles: drop removed ids, append new ones.
  useEffect(() => {
    setTabOrder((prev) => {
      const prevSet = new Set(prev);
      const next = prev.filter((id) => openFiles.some((f) => f.id === id));
      for (const f of openFiles) if (!prevSet.has(f.id)) next.push(f.id);
      return next;
    });
  }, [openFiles]);

  // Close the context menu on any outside click.
  useEffect(() => {
    if (!contextMenu) return;
    const onDocClick = () => setContextMenu(null);
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [contextMenu]);

  const handleEditorChange = useCallback(
    (value: string | undefined) => {
      if (!activeFile || value === undefined || value === activeFile.content) return;
      setFileContent(activeFile.id, value);
      markFileDirty(activeFile.id, true);
    },
    [activeFile, setFileContent, markFileDirty],
  );

  const handleSave = useCallback(async () => {
    if (activeFile) await saveFile(activeFile.id);
  }, [activeFile, saveFile]);

  const handleClose = useCallback(
    async (id: string) => {
      const file = openFiles.find((f) => f.id === id);
      if (!file) return;
      if (!file.dirty) {
        await closeFile(id);
        return;
      }
      setClosingId(id);
    },
    [openFiles, closeFile],
  );

  // Process the next pending dirty file in a batch close, or finish.
  const confirmClose = useCallback(
    async (action: "save" | "discard" | "cancel") => {
      if (!closingId) return;
      if (action === "cancel") {
        setClosingId(null);
        setBatchQueue([]);
        return;
      }
      await closeFile(closingId, action);
      if (batchQueue.length > 0) {
        setClosingId(batchQueue[0]);
        setBatchQueue((q) => q.slice(1));
      } else {
        setClosingId(null);
      }
    },
    [closingId, batchQueue, closeFile],
  );

  // Close clean files immediately; queue dirty ones for sequential confirm.
  const processBatchClose = useCallback(
    async (ids: string[]) => {
      const isDirty = (id: string) => openFiles.find((f) => f.id === id)?.dirty === true;
      const clean = ids.filter((id) => !isDirty(id));
      const dirty = ids.filter(isDirty);
      await Promise.all(clean.map((id) => closeFile(id)));
      if (dirty.length === 0) return;
      setBatchQueue(dirty.slice(1));
      setClosingId(dirty[0]);
    },
    [openFiles, closeFile],
  );

  const handleTabClick = useCallback(
    (id: string) => {
      if (id !== activeFileId) setActiveFile(id);
    },
    [activeFileId, setActiveFile],
  );

  const togglePin = useCallback(
    (id: string) => {
      if (pinnedFileIds.includes(id)) unpinFile(id);
      else pinFile(id);
    },
    [pinnedFileIds, pinFile, unpinFile],
  );

  const openMenu = useCallback((e: React.MouseEvent, fileId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, fileId });
  }, []);

  const closeOthers = useCallback(async () => {
    if (!contextMenu) return;
    const keep = contextMenu.fileId;
    setContextMenu(null);
    const pinnedSet = new Set(pinnedFileIds);
    const ids = openFiles.filter((f) => f.id !== keep && !pinnedSet.has(f.id)).map((f) => f.id);
    await processBatchClose(ids);
  }, [contextMenu, openFiles, pinnedFileIds, processBatchClose]);

  const closeAll = useCallback(async () => {
    setContextMenu(null);
    const pinnedSet = new Set(pinnedFileIds);
    const ids = openFiles.filter((f) => !pinnedSet.has(f.id)).map((f) => f.id);
    await processBatchClose(ids);
  }, [openFiles, pinnedFileIds, processBatchClose]);

  const closeSaved = useCallback(async () => {
    setContextMenu(null);
    const pinnedSet = new Set(pinnedFileIds);
    const ids = openFiles
      .filter((f) => !pinnedSet.has(f.id) && !f.dirty)
      .map((f) => f.id);
    await Promise.all(ids.map((id) => closeFile(id)));
  }, [openFiles, pinnedFileIds, closeFile]);

  const togglePinFromMenu = useCallback(() => {
    const menu = contextMenuRef.current;
    if (!menu) return;
    togglePin(menu.fileId);
    setContextMenu(null);
  }, [togglePin]);

  const closeCurrentFromMenu = useCallback(async () => {
    const menu = contextMenuRef.current;
    if (!menu) return;
    const id = menu.fileId;
    setContextMenu(null);
    await handleClose(id);
  }, [handleClose]);

  const handleCopyPath = useCallback(async () => {
    const menu = contextMenuRef.current;
    if (!menu) return;
    const file = openFiles.find((f) => f.id === menu.fileId);
    setContextMenu(null);
    if (!file) return;
    const ok = await copyToClipboard(file.path);
    if (ok) showSuccess(t("copyPathSuccess", "路径已复制"));
    else showError(t("copyPathFailed", "复制路径失败"));
  }, [openFiles, showError, showSuccess, t]);

  const handleCopyRelativePath = useCallback(async () => {
    const menu = contextMenuRef.current;
    if (!menu) return;
    const file = openFiles.find((f) => f.id === menu.fileId);
    setContextMenu(null);
    if (!file || !explorerRootPath) return;
    const rel = relativePath(explorerRootPath, file.path);
    const ok = await copyToClipboard(rel);
    if (ok) showSuccess(t("copyPathSuccess", "路径已复制"));
    else showError(t("copyPathFailed", "复制路径失败"));
  }, [explorerRootPath, openFiles, showError, showSuccess, t]);

  const handleReveal = useCallback(async () => {
    const menu = contextMenuRef.current;
    if (!menu) return;
    const file = openFiles.find((f) => f.id === menu.fileId);
    setContextMenu(null);
    if (!file) return;
    try {
      await apiPost("explorer/reveal", { path: file.path });
    } catch (err) {
      showError(`${t("revealFailed", "在文件管理器中显示失败")}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, [openFiles, showError, t]);

  const handleDiffFromMenu = useCallback(() => {
    const menu = contextMenuRef.current;
    if (!menu) return;
    const id = menu.fileId;
    setContextMenu(null);
    openDiffForFile(id);
  }, [openDiffForFile]);

  const buildTabMenuItems = useCallback((): MenuItem[] => {
    const menu = contextMenuRef.current;
    if (!menu) return [];
    const file = openFiles.find((f) => f.id === menu.fileId);
    const isPinned = pinnedFileIds.includes(menu.fileId);
    const hasChanges = file ? file.snapshot !== undefined && file.snapshot !== file.content : false;
    return [
      { label: isPinned ? t("unpin", "取消置顶") : t("pin", "置顶"), icon: "📌", onClick: togglePinFromMenu },
      { label: "---" },
      { label: t("close", "关闭"), icon: "❌", onClick: closeCurrentFromMenu },
      { label: t("closeOthers", "关闭其他"), icon: "🗂️", onClick: closeOthers },
      { label: t("closeAll", "关闭所有"), icon: "🚪", onClick: closeAll },
      { label: t("closeSaved", "关闭已保存"), icon: "✅", onClick: closeSaved },
      { label: "---" },
      { label: t("copyPath", "复制路径"), icon: "📋", onClick: handleCopyPath },
      { label: t("copyRelativePath", "复制相对路径"), icon: "📎", onClick: handleCopyRelativePath },
      { label: t("reveal", "在文件管理器中显示"), icon: "📂", onClick: handleReveal },
      { label: "---" },
      { label: t("viewDiff", "查看 diff"), icon: "👁️", onClick: handleDiffFromMenu, danger: !hasChanges },
    ];
  }, [openFiles, pinnedFileIds, t, togglePinFromMenu, closeCurrentFromMenu, closeOthers, closeAll, closeSaved, handleCopyPath, handleCopyRelativePath, handleReveal, handleDiffFromMenu]);

  const handleTabsWheel = useCallback((e: React.WheelEvent) => {
    const el = e.currentTarget as HTMLDivElement;
    if (el.scrollWidth > el.clientWidth) {
      e.preventDefault();
      el.scrollLeft += e.deltaY;
    }
  }, []);

  const handleDragStart = useCallback((id: string) => setDragId(id), []);
  const handleDragOver = useCallback((e: React.DragEvent) => e.preventDefault(), []);
  const handleDrop = useCallback(
    (targetId: string) => {
      if (!dragId || dragId === targetId) return;
      const pinnedSet = new Set(pinnedFileIds);
      // Only allow reordering among non-pinned tabs.
      if (pinnedSet.has(dragId) || pinnedSet.has(targetId)) return;
      setTabOrder((prev) => reorder(prev, dragId, targetId));
      setDragId(null);
    },
    [dragId, pinnedFileIds],
  );

  const toggleTree = useCallback(() => {
    setEditorShowTree(!editorShowTree);
  }, [editorShowTree, setEditorShowTree]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        void handleSave();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handleSave]);

  // Restore content for files recovered from localStorage without content.
  useRestoreFileContents(openFiles, setFileContent);

  const closingFile = closingId ? openFiles.find((f) => f.id === closingId) : null;
  const isBinary = activeFile?.is_text === false;

  if (openFiles.length === 0) {
    return (
      <div className="editor-panel">
        <div className="editor-empty">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <polyline points="10 9 9 9 8 9" />
          </svg>
          <p>{t("editorEmpty", "点击文件树中的文本文件开始编辑")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="editor-panel">
      <div className="editor-tabs" onWheel={handleTabsWheel}>
        {orderedFiles.map((file) => (
          <EditorTabItem
            key={file.id}
            file={file}
            isActive={file.id === activeFileId}
            isPinned={pinnedFileIds.includes(file.id)}
            hasChanges={file.snapshot !== undefined && file.snapshot !== file.content}
            rootPath={explorerRootPath ?? undefined}
            isDragging={dragId === file.id}
            onClick={handleTabClick}
            onContextMenu={openMenu}
            onClose={(id) => void handleClose(id)}
            onTogglePin={togglePin}
            onDiff={openDiffForFile}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
          />
        ))}
        <div className="editor-tab-actions">
          <button
            className={`editor-tree-toggle-btn${editorShowTree ? " active" : ""}`}
            onClick={() => toggleTree()}
            title={t("toggleFileTree", "文件树")}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 6h18M3 12h12M3 18h6" />
            </svg>
          </button>
          <button className="editor-save-btn" onClick={() => void handleSave()} title={t("save", "保存")}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
              <polyline points="17 21 17 13 7 13 7 21" />
              <polyline points="7 3 7 8 15 8" />
            </svg>
            <span>{t("save", "保存")}</span>
          </button>
        </div>
      </div>
      <div className="editor-body">
        {activeFile && isBinary ? (
          <ImagePreview file={activeFile} />
        ) : activeFile ? (
          <MonacoEditor
            language={activeLanguage}
            value={activeFile.content ?? ""}
            theme={editorTheme}
            height="100%"
            options={{
              readOnly: false,
              minimap: { enabled: false },
              fontSize: 13,
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              wordWrap: "on",
              tabSize: 2,
              automaticLayout: true,
            }}
            onChange={handleEditorChange}
          />
        ) : (
          <div className="editor-empty">{t("selectFile", "请选择要编辑的文件")}</div>
        )}
      </div>
      {closingFile && (
        <CloseConfirmDialog
          fileName={closingFile.name}
          onSave={() => void confirmClose("save")}
          onDiscard={() => void confirmClose("discard")}
          onCancel={() => confirmClose("cancel")}
        />
      )}
      {contextMenu && (
        <ContextMenu
          items={buildTabMenuItems()}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
        />
      )}
      {diffViewOpen && diffFile && (
        <Modal onClose={closeDiffView} closeOnBackdrop>
          <DiffPanel file={diffFile} onClose={closeDiffView} />
        </Modal>
      )}
    </div>
  );
}
