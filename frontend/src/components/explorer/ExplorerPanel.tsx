import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useToastStore } from "../../stores/useToastStore";
import {
  loadExpandedPaths,
  loadSelectedPaths,
  saveExpandedPaths,
  saveSelectedPaths,
} from "../../stores/appStore/persistence";
import { apiGet, apiPost } from "../../api/client";
import { useI18n } from "../../i18n";
import { EditorPanel } from "./EditorPanel";
import { FileTree, type FileNode } from "./FileTree";
import { FolderPickerDialog } from "./FolderPickerDialog";
import { useExplorerWatch, type WatchEvent } from "./useExplorerWatch";
import { collectDirPaths, collectLazyExpandedPaths, fileNameFromPath } from "./treeUtils";

interface OpenResponse {
  tree: FileNode[];
}

interface DirResponse {
  children: FileNode[];
}

function replaceNodeChildren(node: FileNode, targetPath: string, children: FileNode[]): FileNode {
  if (node.path === targetPath) {
    return { ...node, children, lazy: false };
  }
  if (!node.children) return node;
  return {
    ...node,
    children: node.children.map((child) => replaceNodeChildren(child, targetPath, children)),
  };
}

const HIGHLIGHT_DURATION_MS = 3000;
// Tree pane width bounds (px), persisted to localStorage
const TREE_PANE_MIN = 180;
const TREE_PANE_MAX = 600;
const TREE_PANE_DEFAULT = 280;
const TREE_PANE_KEY = "hwrag_tree_pane_width";

function normalizePath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/$/, "");
}

function formatError(prefix: string, err: unknown): string {
  return `${prefix}: ${err instanceof Error ? err.message : String(err)}`;
}

export function ExplorerPanel() {
  const rootPath = useAppStore((s) => s.explorerRootPath);
  const recentFolders = useAppStore((s) => s.recentFolders);
  const followMode = useAppStore((s) => s.followMode);
  const explorerOpen = useAppStore((s) => s.explorerOpen);
  const openFiles = useAppStore((s) => s.openFiles);
  const editorShowTree = useAppStore((s) => s.editorShowTree);
  const setExplorerRootPath = useAppStore((s) => s.setExplorerRootPath);
  const setExplorerOpen = useAppStore((s) => s.setExplorerOpen);
  const addRecentFolder = useAppStore((s) => s.addRecentFolder);
  const toggleFollowMode = useAppStore((s) => s.toggleFollowMode);
  const { t } = useI18n();

  const [tree, setTree] = useState<FileNode | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [showRecent, setShowRecent] = useState(false);
  const [showFolderPicker, setShowFolderPicker] = useState(false);
  const [highlightPaths, setHighlightPaths] = useState<Set<string>>(new Set());
  // Lifted from FileTree so expanded/selected state survives loadTree refreshes
  // (which previously unmounted FileTree and lost internal state).
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    rootPath ? new Set(loadExpandedPaths(rootPath)) : new Set()
  );
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(() =>
    rootPath ? new Set(loadSelectedPaths(rootPath)) : new Set()
  );
  // Lazy directory loading state (path -> loading / error).
  const [loadingDirPaths, setLoadingDirPaths] = useState<Set<string>>(new Set());
  const [dirErrors, setDirErrors] = useState<Map<string, string>>(new Map());
  const [watchConnected, setWatchConnected] = useState(false);
  const [matchCount, setMatchCount] = useState(0);
  // Tree pane width (px) — draggable divider, persisted to localStorage
  const [treePaneWidth, setTreePaneWidth] = useState<number>(() => {
    try {
      const n = Number(localStorage.getItem(TREE_PANE_KEY));
      return Number.isFinite(n) ? Math.min(TREE_PANE_MAX, Math.max(TREE_PANE_MIN, n)) : TREE_PANE_DEFAULT;
    } catch {
      return TREE_PANE_DEFAULT;
    }
  });
  const treeContainerRef = useRef<HTMLDivElement>(null);
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const recentDropdownRef = useRef<HTMLDivElement>(null);
  // 防并发覆盖：每次 loadTree 递增 token，只有最新 token 的响应才能 setState。
  const loadTokenRef = useRef(0);
  // 期望路径：用户最新打开/切换的路径，refreshTree 用它而不是 rootPath state。
  const expectedPathRef = useRef<string | null>(null);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevRootRef = useRef(rootPath);

  const showError = useCallback((message: string) => {
    useToastStore.getState().showError(message);
  }, []);

  // Re-authorization mutex: when multiple parallel dir requests fail due to
  // backend restart, only one should re-authorize; the rest wait and retry.
  const reauthPromiseRef = useRef<Promise<void> | null>(null);

  const ensureAuthorized = useCallback(async (): Promise<void> => {
    if (reauthPromiseRef.current) return reauthPromiseRef.current;
    const currentRoot = expectedPathRef.current ?? rootPath;
    if (!currentRoot) return;
    reauthPromiseRef.current = (async () => {
      try {
        await apiPost<OpenResponse>("explorer/open", { path: currentRoot }, 120_000);
      } finally {
        reauthPromiseRef.current = null;
      }
    })();
    return reauthPromiseRef.current;
  }, [rootPath]);

  const fetchAndReplaceChildren = useCallback(async (path: string, token?: number) => {
    let res: DirResponse | null = null;
    try {
      res = await apiGet<DirResponse>(`explorer/dir?path=${encodeURIComponent(path)}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("security") || msg.includes("authorized") || msg.includes("outside") || msg.includes("EXPLORER_ERROR")) {
        await ensureAuthorized();
        res = await apiGet<DirResponse>(`explorer/dir?path=${encodeURIComponent(path)}`);
      } else {
        throw err;
      }
    }
    if (res === null) return;
    if (token !== undefined && token !== loadTokenRef.current) return;
    setTree((prev) => (prev ? replaceNodeChildren(prev, path, res!.children) : prev));
  }, [ensureAuthorized]);

  const loadTree = useCallback(async (path: string, silent = false) => {
    const token = ++loadTokenRef.current;
    expectedPathRef.current = path;
    if (!silent) setLoading(true);
    try {
      const res = await apiPost<OpenResponse>("explorer/open", { path }, 120_000);
      if (token !== loadTokenRef.current) return;
      const first = res.tree[0];
      if (first) {
        setTree(first);
        if (!silent) {
          setExplorerRootPath(first.path);
          addRecentFolder(first.path);
        }
        // Restore previously-expanded directories on the freshly loaded tree.
        const lazyExpanded = collectLazyExpandedPaths(first, expanded);
        if (lazyExpanded.length > 0) {
          // Limit concurrent directory loads to avoid overwhelming the backend.
          const CONCURRENCY = 10;
          for (let i = 0; i < lazyExpanded.length; i += CONCURRENCY) {
            if (token !== loadTokenRef.current) return;
            const batch = lazyExpanded.slice(i, i + CONCURRENCY);
            await Promise.allSettled(batch.map((p) => fetchAndReplaceChildren(p, token)));
          }
        }
      }
    } catch (err) {
      if (token !== loadTokenRef.current) return;
      if (!silent) showError(formatError(t("openFolderFailed", "打开文件夹失败"), err));
    } finally {
      if (token === loadTokenRef.current && !silent) setLoading(false);
    }
  }, [addRecentFolder, setExplorerRootPath, showError, t, expanded, fetchAndReplaceChildren]);

  const refreshTree = useCallback(async () => {
    const path = expectedPathRef.current;
    if (!path) return;
    await loadTree(path, true);
  }, [loadTree]);

  const loadNodeChildren = useCallback(async (path: string) => {
    if (loadingDirPaths.has(path)) return;
    setLoadingDirPaths((prev) => new Set([...prev, path]));
    setDirErrors((prev) => {
      const next = new Map(prev);
      next.delete(path);
      return next;
    });
    try {
      await fetchAndReplaceChildren(path);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setDirErrors((prev) => new Map([...prev, [path, msg]]));
    } finally {
      setLoadingDirPaths((prev) => {
        const next = new Set(prev);
        next.delete(path);
        return next;
      });
    }
  }, [loadingDirPaths, fetchAndReplaceChildren]);

  // Drag the vertical divider to resize the tree pane width live.
  const handleResizerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    document.body.style.userSelect = "none";
    const startX = e.clientX;
    let latest = treePaneWidth;
    const onMove = (ev: MouseEvent) => {
      latest = Math.min(TREE_PANE_MAX, Math.max(TREE_PANE_MIN, treePaneWidth + ev.clientX - startX));
      setTreePaneWidth(latest);
    };
    const onUp = () => {
      document.body.style.userSelect = "";
      try { localStorage.setItem(TREE_PANE_KEY, String(latest)); } catch { /* ignore */ }
      document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove); document.addEventListener("mouseup", onUp);
  }, [treePaneWidth]);

  const debouncedRefresh = useCallback(() => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    refreshTimerRef.current = setTimeout(() => {
      refreshTimerRef.current = null;
      void refreshTree();
    }, 300);
  }, [refreshTree]);

  const openFolder = useCallback(() => setShowFolderPicker(true), []);

  const handleFolderPicked = useCallback((path: string) => {
    setShowFolderPicker(false);
    loadTree(normalizePath(path));
  }, [loadTree]);

  const switchFolder = useCallback((path: string) => {
    setShowRecent(false);
    loadTree(path);
  }, [loadTree]);

  const expandAll = useCallback(() => {
    if (!tree) return;
    setExpanded(new Set(collectDirPaths(tree)));
  }, [tree, setExpanded]);

  const collapseAll = useCallback(() => setExpanded(new Set()), [setExpanded]);

  const highlightPath = useCallback((path: string | undefined) => {
    if (!path) return;
    setHighlightPaths((prev) => new Set([...prev, path]));
    if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = setTimeout(() => {
      setHighlightPaths((prev) => {
        const next = new Set(prev);
        next.delete(path);
        return next;
      });
    }, HIGHLIGHT_DURATION_MS);
  }, []);

  const scrollToNode = useCallback((path: string) => {
    const container = treeContainerRef.current;
    if (!container) return;
    const node = container.querySelector(`[data-path="${CSS.escape(path)}"]`);
    if (node instanceof HTMLElement) {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, []);

  const handleWatchEvent = useCallback((event: WatchEvent) => {
    if (event.type === "heartbeat") return;
    debouncedRefresh();
    if (event.type === "change" && event.path) {
      const state = useAppStore.getState();
      const isOpen = state.openFiles.some((f) => f.path === event.path);
      if (isOpen) void state.handleExternalFileChange(event.path);
    }
    if (!followMode) return;
    const targetPath = event.dest_path ?? event.path;
    if (targetPath) {
      highlightPath(targetPath);
      scrollToNode(targetPath);
      if (!explorerOpen) setExplorerOpen(true);
      useToastStore.getState().showInfo(
        t("followModeChangeToast", "{name} 已更新").replace("{name}", fileNameFromPath(targetPath)),
      );
    }
  }, [followMode, explorerOpen, highlightPath, debouncedRefresh, scrollToNode, setExplorerOpen, t]);

  useExplorerWatch(
    rootPath,
    handleWatchEvent,
    (err) => showError(t("watchError", "文件监听异常") + ": " + err.message),
    setWatchConnected,
  );

  // mount: restore tree from persisted rootPath
  useEffect(() => {
    if (rootPath && !tree) void loadTree(rootPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reset selection/expanded when the user switches to a different folder,
  // restoring any previously persisted state for that root.
  useEffect(() => {
    if (prevRootRef.current !== rootPath) {
      prevRootRef.current = rootPath;
      setSelectedPaths(rootPath ? new Set(loadSelectedPaths(rootPath)) : new Set());
      setExpanded(rootPath ? new Set(loadExpandedPaths(rootPath)) : new Set());
    }
  }, [rootPath]);

  // Persist expanded/selected states per root with a small debounce.
  useEffect(() => {
    if (!rootPath) return;
    const timer = setTimeout(() => saveExpandedPaths(rootPath, [...expanded]), 200);
    return () => clearTimeout(timer);
  }, [expanded, rootPath]);

  useEffect(() => {
    if (!rootPath) return;
    const timer = setTimeout(() => saveSelectedPaths(rootPath, [...selectedPaths]), 200);
    return () => clearTimeout(timer);
  }, [selectedPaths, rootPath]);

  useEffect(() => {
    return () => {
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!recentDropdownRef.current?.contains(e.target as Node)) setShowRecent(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const recentItems = useMemo(
    () => recentFolders.map((p) => ({ path: p, name: fileNameFromPath(p) })),
    [recentFolders],
  );

  const hasFiles = openFiles.length > 0;
  const treeHiddenStyle = { display: loading ? "none" : undefined } as const;
  const fileTreeEl = tree && (
    <FileTree
      tree={tree}
      searchQuery={searchQuery}
      highlightPaths={highlightPaths}
      expanded={expanded}
      setExpanded={setExpanded}
      selectedPaths={selectedPaths}
      setSelectedPaths={setSelectedPaths}
      rootPath={rootPath ?? ""}
      onRefresh={refreshTree}
      onLoadNodeChildren={loadNodeChildren}
      loadingDirPaths={loadingDirPaths}
      dirErrors={dirErrors}
      onMatchCountChange={setMatchCount}
    />
  );

  return (
    <div className="explorer-panel">
      <div className="explorer-header">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
        <span>{t("explorerTitle", "文件资源管理器")}</span>
        {rootPath ? <span className="explorer-root-hint" title={rootPath}>{rootPath}</span> : null}
        <div className="explorer-header-actions">
          <button
            className={`explorer-follow-btn${followMode ? " active" : ""}`}
            title={t("followMode", "跟随模式")}
            onClick={toggleFollowMode}
          >
            {followMode ? "👁" : "🚫"}
          </button>
          <div className="explorer-recent-wrapper" ref={recentDropdownRef}>
            <button
              className="explorer-recent-toggle"
              onClick={() => setShowRecent((v) => !v)}
              disabled={recentItems.length === 0}
            >
              {t("recentFolders", "最近")}
            </button>
            {showRecent && (
              <div className="explorer-recent-dropdown">
                {recentItems.map((item) => (
                  <button key={item.path} className="explorer-recent-item" onClick={() => switchFolder(item.path)}>
                    <span className="explorer-recent-name">{item.name}</span>
                    <span className="explorer-recent-path">{item.path}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="explorer-open-btn" onClick={openFolder}>
            {t("openFolder", "打开文件夹")}
          </button>
        </div>
      </div>

      {showFolderPicker && (
        <FolderPickerDialog
          initialPath={rootPath}
          onConfirm={handleFolderPicked}
          onCancel={() => setShowFolderPicker(false)}
        />
      )}

      {(editorShowTree || !hasFiles) && (
        <div className="explorer-toolbar">
          <input
            className="explorer-search-input"
            type="text"
            placeholder={t("searchFiles", "搜索文件…")}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <span className="explorer-search-count">
              {t("searchResultsCount", "{n} 个结果").replace("{n}", String(matchCount))}
            </span>
          )}
          <button type="button" className="explorer-toolbar-btn" onClick={expandAll} disabled={!tree} title={t("expandAll", "展开所有")}>⊕</button>
          <button type="button" className="explorer-toolbar-btn" onClick={collapseAll} disabled={!tree} title={t("collapseAll", "折叠所有")}>⊖</button>
          <button type="button" className="explorer-toolbar-btn" onClick={refreshTree} disabled={!tree || loading} title={t("refreshTree", "刷新文件树")}>🔄</button>
          {rootPath && (
            <span
              className={`explorer-watch-dot ${watchConnected ? "on" : "off"}`}
              title={watchConnected ? t("watchConnected", "监听已连接") : t("watchDisconnected", "监听未连接")}
            >
              {watchConnected ? "●" : "○"}
            </span>
          )}
        </div>
      )}

      <div className="explorer-body" ref={treeContainerRef}>
        {loading && <div className="explorer-loading">{t("loading", "加载中…")}</div>}
        {tree && hasFiles && editorShowTree && (
          <div className="explorer-split">
            <div className="explorer-tree-pane" style={{ width: treePaneWidth, ...treeHiddenStyle }}>
              {fileTreeEl}
            </div>
            <div className="explorer-tree-resizer" onMouseDown={handleResizerMouseDown} />
            <div className="explorer-editor-pane">
              <EditorPanel />
            </div>
          </div>
        )}
        {tree && hasFiles && !editorShowTree && <EditorPanel />}
        {tree && !hasFiles && <div style={treeHiddenStyle}>{fileTreeEl}</div>}
        {!tree && !loading && (
          <div className="explorer-placeholder">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            <p className="explorer-placeholder-title">{t("noFolderOpen", "尚未打开文件夹")}</p>
            <button className="explorer-open-btn" onClick={openFolder}>
              {t("openFolder", "打开文件夹")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
