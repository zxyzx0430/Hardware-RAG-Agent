import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useToastStore } from "../../stores/useToastStore";
import { useModalStore } from "../../stores/useModalStore";
import { apiPost } from "../../api/client";
import { ContextMenu } from "../shared/ContextMenu";
import type { MenuItem } from "../shared/ContextMenu";
import { useI18n } from "../../i18n";
import { FileTreeNode } from "./FileTreeNode";
import { VirtualFileTree } from "./VirtualFileTree";
import {
  filterTree,
  fileNameFromPath,
  relativePath,
  highlightTree,
  countMatches,
  type FileNode,
} from "./treeUtils";
import { copyToClipboard } from "../../utils/clipboard";

export type { FileNode } from "./treeUtils";

// Safety cap so a runaway batch operation cannot issue thousands of requests.
const MAX_BATCH = 500;
// Maximum number of items that can be moved by one drag-and-drop gesture.
const MAX_BATCH_MOVE = 50;
// Switch to virtual scrolling when the visible tree exceeds this many nodes.
const VIRTUAL_SCROLL_THRESHOLD = 300;

function countVisibleNodes(node: FileNode, expanded: Set<string>): number {
  let count = 1;
  if (node.type === "directory" && expanded.has(node.path) && node.children) {
    for (const child of node.children) {
      count += countVisibleNodes(child, expanded);
    }
  }
  return count;
}

interface FileTreeProps {
  tree: FileNode;
  searchQuery: string;
  highlightPaths?: Set<string>;
  expanded: Set<string>;
  setExpanded: React.Dispatch<React.SetStateAction<Set<string>>>;
  selectedPaths: Set<string>;
  setSelectedPaths: React.Dispatch<React.SetStateAction<Set<string>>>;
  rootPath: string;
  onRefresh: () => void;
  onLoadNodeChildren: (path: string) => Promise<void>;
  loadingDirPaths: Set<string>;
  dirErrors: Map<string, string>;
  onMatchCountChange?: (count: number) => void;
}

interface ContextMenuState {
  x: number;
  y: number;
  node: FileNode;
}

interface ClipboardOp {
  op: "copy" | "cut";
  paths: string[];
}

function joinPath(parent: string, child: string): string {
  const separator = parent.endsWith("/") || parent.endsWith("\\") ? "" : "/";
  return `${parent}${separator}${child}`;
}

function isInside(parent: string, child: string): boolean {
  return child === parent || child.startsWith(parent + "/") || child.startsWith(parent + "\\");
}

function formatError(prefix: string, err: unknown): string {
  return `${prefix}: ${err instanceof Error ? err.message : String(err)}`;
}

// When acting on a node that is part of a multi-selection, operate on the whole set.
function selectionPathsFor(selected: Set<string>, nodePath: string): string[] {
  if (!selected.has(nodePath) || selected.size <= 1) return [nodePath];
  return [...selected].sort((a, b) => a.localeCompare(b));
}

/** Collect visible node paths in tree order. */
function collectVisiblePaths(node: FileNode, expanded: Set<string>): string[] {
  const paths: string[] = [node.path];
  if (node.type === "directory" && expanded.has(node.path) && node.children) {
    for (const child of node.children) {
      paths.push(...collectVisiblePaths(child, expanded));
    }
  }
  return paths;
}

/** Find the parent directory path of `childPath` inside `tree`. */
function findParentPath(tree: FileNode, childPath: string): string | null {
  if (tree.path === childPath) return null;
  if (tree.children?.some((c) => c.path === childPath)) return tree.path;
  for (const c of tree.children ?? []) {
    const found = findParentPath(c, childPath);
    if (found) return found;
  }
  return null;
}

/** Extract the file/directory name from a path. */
function nameFromPath(path: string): string {
  return path.split(/[\\/]/).pop() ?? "";
}

/** Find a node by path inside the tree. */
function findNodeByPath(node: FileNode, path: string): FileNode | null {
  if (node.path === path) return node;
  for (const child of node.children ?? []) {
    const found = findNodeByPath(child, path);
    if (found) return found;
  }
  return null;
}

export function FileTree({
  tree, searchQuery, highlightPaths, expanded, setExpanded,
  selectedPaths, setSelectedPaths, rootPath, onRefresh,
  onLoadNodeChildren, loadingDirPaths, dirErrors, onMatchCountChange,
}: FileTreeProps) {
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [creating, setCreating] = useState<{ path: string; type: "file" | "directory" } | null>(null);
  const [dragOverPath, setDragOverPath] = useState<string | null>(null);
  const [draggingPaths, setDraggingPaths] = useState<Set<string>>(new Set());
  const [focusedPath, setFocusedPath] = useState<string | undefined>(undefined);
  const dragSourceRef = useRef<string[]>([]);
  const anchorRef = useRef<string | null>(null);
  const clipboardRef = useRef<ClipboardOp | null>(null);
  const treeRef = useRef<HTMLDivElement>(null);
  const virtualListRef = useRef<{ scrollToItem: (index: number, align?: string) => void } | null>(null);
  const typeAheadRef = useRef("");
  const typeAheadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { t } = useI18n();

  const showError = useCallback((message: string) => {
    useToastStore.getState().showError(message);
  }, []);
  const showSuccess = useCallback((message: string) => {
    useToastStore.getState().showSuccess(message);
  }, []);

  const refresh = useCallback(() => {
    setRenaming(null);
    setCreating(null);
    onRefresh();
  }, [onRefresh]);

  const handleToggle = useCallback(async (node: FileNode) => {
    const path = node.path;
    const isExpanded = expanded.has(path);
    if (isExpanded) {
      setExpanded((prev) => {
        const next = new Set(prev);
        next.delete(path);
        return next;
      });
      return;
    }
    if (node.type === "directory" && node.lazy && !loadingDirPaths.has(path)) {
      await onLoadNodeChildren(path);
    }
    setExpanded((prev) => new Set([...prev, path]));
  }, [expanded, loadingDirPaths, onLoadNodeChildren, setExpanded]);

  const expandTo = useCallback((path: string) => {
    setExpanded((prev) => new Set([...prev, path]));
  }, [setExpanded]);

  // Multi-select: plain click = single select; ctrl/meta = toggle; shift = range.
  const handleSelect = useCallback((node: FileNode, e: React.MouseEvent, siblings: FileNode[]) => {
    const paths = siblings.map((s) => s.path);
    if (e.shiftKey) {
      if (anchorRef.current) {
        const from = paths.indexOf(anchorRef.current);
        const to = paths.indexOf(node.path);
        if (from >= 0 && to >= 0) {
          const [lo, hi] = from < to ? [from, to] : [to, from];
          const range = paths.slice(lo, hi + 1);
          setSelectedPaths(new Set(range));
          return;
        }
      }
      // Invalid anchor or no anchor: fall back to plain click and set anchor.
      setSelectedPaths(new Set([node.path]));
      anchorRef.current = node.path;
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      setSelectedPaths((prev) => {
        const next = new Set(prev);
        if (next.has(node.path)) next.delete(node.path);
        else next.add(node.path);
        return next;
      });
      anchorRef.current = node.path;
      return;
    }
    setSelectedPaths(new Set([node.path]));
    anchorRef.current = node.path;
  }, [setSelectedPaths]);

  const handleCreate = useCallback(async (parentPath: string, type: "file" | "directory", name: string) => {
    if (name.trim()) {
      const fullPath = joinPath(parentPath, name.trim());
      try {
        await apiPost("explorer/create", { path: fullPath, type });
        showSuccess(t("createSuccess", "创建成功"));
        expandTo(parentPath);
      } catch (err) {
        showError(formatError(t("createFailed", "创建失败"), err));
      }
    }
    refresh();
  }, [expandTo, refresh, showError, showSuccess, t]);

  const handleRename = useCallback(async (path: string, newName: string) => {
    setRenaming(null);
    if (!newName.trim()) return;
    try {
      await apiPost("explorer/rename", { path, new_name: newName.trim() });
      showSuccess(t("renameSuccess", "重命名成功"));
      refresh();
    } catch (err) {
      showError(formatError(t("renameFailed", "重命名失败"), err));
    }
  }, [refresh, showError, showSuccess, t]);

  // Uses the global Modal store instead of window.confirm for a consistent UX.
  const handleDelete = useCallback(async (paths: string[]) => {
    if (paths.length === 0) return;
    const message = paths.length === 1
      ? `${t("deleteConfirm", "确定删除")} "${fileNameFromPath(paths[0])}"?`
      : t("deleteConfirmMulti", "确定删除选中的 {n} 项？").replace("{n}", String(paths.length));
    const ok = await useModalStore.getState().confirmDialog({
      title: t("deleteConfirmTitle", "确认删除"),
      message,
      danger: true,
    });
    if (!ok) return;
    try {
      for (const p of paths.slice(0, MAX_BATCH)) {
        await apiPost("explorer/delete", { path: p });
      }
      showSuccess(t("deleteSuccess", "删除成功"));
      setSelectedPaths(new Set());
      refresh();
    } catch (err) {
      showError(formatError(t("deleteFailed", "删除失败"), err));
    }
  }, [refresh, setSelectedPaths, showError, showSuccess, t]);

  const moveSources = useCallback(async (sources: string[], targetDir: string) => {
    if (sources.length === 0) return;
    if (sources.length > MAX_BATCH_MOVE) {
      showError(t("maxBatchMove", "一次最多移动 {n} 项").replace("{n}", String(MAX_BATCH_MOVE)));
      return;
    }
    const validSources = sources.filter((s) => !isInside(s, targetDir));
    if (validSources.length === 0) {
      showError(t("moveIntoSelf", "不能移动到自身或其子目录"));
      return;
    }
    let moved = 0;
    let lastError: unknown = null;
    for (const s of validSources) {
      try {
        await apiPost("explorer/move", { path: s, target_dir: targetDir });
        moved += 1;
      } catch (err) {
        lastError = err;
      }
    }
    if (moved === validSources.length) {
      showSuccess(t("moveSuccess", "移动成功"));
    } else if (moved > 0) {
      showSuccess(t("movePartialSuccess", "已移动 {moved}/{total} 项").replace("{moved}", String(moved)).replace("{total}", String(validSources.length)));
      if (lastError) showError(formatError(t("moveFailed", "移动失败"), lastError));
    } else {
      showError(formatError(t("moveFailed", "移动失败"), lastError ?? t("moveIntoSelf", "不能移动到自身或其子目录")));
      return;
    }
    setSelectedPaths(new Set());
    refresh();
  }, [refresh, setSelectedPaths, showError, showSuccess, t]);

  const copyPath = useCallback(async (path: string) => {
    const ok = await copyToClipboard(path);
    if (ok) showSuccess(t("copyPathSuccess", "路径已复制"));
    else showError(t("copyPathFailed", "复制路径失败"));
  }, [showError, showSuccess, t]);

  const copyRelativePath = useCallback(async (node: FileNode) => {
    const rel = relativePath(rootPath, node.path);
    const ok = await copyToClipboard(rel);
    if (ok) showSuccess(t("copyPathSuccess", "路径已复制"));
    else showError(t("copyPathFailed", "复制路径失败"));
  }, [rootPath, showError, showSuccess, t]);

  const setClipboard = useCallback((node: FileNode, op: "copy" | "cut") => {
    const paths = selectionPathsFor(selectedPaths, node.path);
    clipboardRef.current = { op, paths };
    showSuccess(op === "copy" ? t("copied", "已复制") : t("cut", "已剪切"));
  }, [selectedPaths, showSuccess, t]);

  // Paste: copy → POST /explorer/copy, cut → POST /explorer/move (clears clipboard).
  const handlePaste = useCallback(async (targetDir: string) => {
    const clip = clipboardRef.current;
    if (!clip || clip.paths.length === 0) return;
    try {
      for (const p of clip.paths) {
        if (clip.op === "copy") {
          await apiPost("explorer/copy", { path: p, target_dir: targetDir });
        } else {
          await apiPost("explorer/move", { path: p, target_dir: targetDir });
        }
      }
      if (clip.op === "cut") clipboardRef.current = null;
      showSuccess(t("pasteSuccess", "粘贴成功"));
      refresh();
    } catch (err) {
      showError(formatError(t("pasteFailed", "粘贴失败"), err));
    }
  }, [refresh, showError, showSuccess, t]);

  const handleReveal = useCallback(async (node: FileNode) => {
    try {
      await apiPost("explorer/reveal", { path: node.path });
    } catch (err) {
      showError(formatError(t("revealFailed", "在文件管理器中显示失败"), err));
    }
  }, [showError, t]);

  const handleContextMenu = useCallback((node: FileNode, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Right-clicking outside the current selection narrows selection to this node.
    if (!selectedPaths.has(node.path)) setSelectedPaths(new Set([node.path]));
    setMenu({ x: e.clientX, y: e.clientY, node });
  }, [selectedPaths, setSelectedPaths]);

  const buildMenuItems = useCallback((node: FileNode): MenuItem[] => {
    const isDir = node.type === "directory";
    const inSelection = selectedPaths.has(node.path) && selectedPaths.size > 1;
    const target = inSelection ? [...selectedPaths] : [node.path];
    const count = target.length;
    const items: MenuItem[] = [];
    if (isDir) {
      items.push(
        { label: t("newFile", "新建文件"), onClick: () => setCreating({ path: node.path, type: "file" }) },
        { label: t("newFolder", "新建文件夹"), onClick: () => setCreating({ path: node.path, type: "directory" }) },
        { label: "---" },
      );
      if (clipboardRef.current) {
        items.push({ label: t("paste", "粘贴"), onClick: () => void handlePaste(node.path) });
        items.push({ label: "---" });
      }
    }
    const copyLabel = inSelection
      ? t("copySelected", "复制选中 ({n})").replace("{n}", String(count))
      : t("copy", "复制");
    const cutLabel = inSelection
      ? t("cutSelected", "剪切选中 ({n})").replace("{n}", String(count))
      : t("cut", "剪切");
    const deleteLabel = inSelection
      ? t("deleteSelected", "删除选中 ({n})").replace("{n}", String(count))
      : t("delete", "删除");
    items.push(
      { label: t("rename", "重命名"), onClick: () => setRenaming(node.path) },
      { label: copyLabel, onClick: () => setClipboard(node, "copy") },
      { label: cutLabel, onClick: () => setClipboard(node, "cut") },
      { label: t("copyPath", "复制路径"), onClick: () => void copyPath(node.path) },
      { label: t("copyRelativePath", "复制相对路径"), onClick: () => void copyRelativePath(node) },
      { label: t("reveal", "在文件管理器中显示"), onClick: () => void handleReveal(node) },
      { label: "---" },
      { label: deleteLabel, danger: true, onClick: () => void handleDelete(target) },
    );
    return items;
  }, [selectedPaths, t, handlePaste, setClipboard, copyPath, copyRelativePath, handleReveal, handleDelete]);

  const filteredTree = useMemo(() => filterTree(tree, searchQuery.trim()), [tree, searchQuery]);
  const highlightedTree = useMemo(() => {
    if (!filteredTree) return null;
    return highlightTree(filteredTree, searchQuery.trim());
  }, [filteredTree, searchQuery]);
  const matchCount = useMemo(() => countMatches(highlightedTree ?? tree, searchQuery.trim()), [highlightedTree, tree, searchQuery]);
  const visibleCount = useMemo(() => (highlightedTree ? countVisibleNodes(highlightedTree, expanded) : 0), [highlightedTree, expanded]);
  const useVirtual = visibleCount > VIRTUAL_SCROLL_THRESHOLD;
  const visiblePaths = useMemo(
    () => (highlightedTree ? collectVisiblePaths(highlightedTree, expanded) : []),
    [highlightedTree, expanded],
  );

  // Report match count and auto-expand directories that contain matches.
  useEffect(() => {
    onMatchCountChange?.(matchCount);
    if (!searchQuery.trim() || !highlightedTree) return;
    const toExpand = new Set<string>();
    const walk = (node: FileNode): boolean => {
      if (node.type !== "directory") return false;
      const childHasMatch = node.children?.some((child) =>
        child.matchStart !== undefined ? true : walk(child),
      ) ?? false;
      if (childHasMatch) toExpand.add(node.path);
      return childHasMatch;
    };
    walk(highlightedTree);
    setExpanded((prev) => {
      if (toExpand.size === 0) return prev;
      const merged = new Set(prev);
      for (const p of toExpand) merged.add(p);
      return merged;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, highlightedTree, onMatchCountChange, matchCount]);

  const newItemPlaceholder = useMemo(
    () => ({ file: t("newFileName", "新建文件"), folder: t("newFolderName", "新建文件夹") }),
    [t],
  );

  const dragHandlers = useMemo(() => ({
    onDragStart: (path: string) => {
      const sources = selectionPathsFor(selectedPaths, path);
      dragSourceRef.current = sources;
      setDraggingPaths(new Set(sources));
    },
    onDragEnd: () => {
      dragSourceRef.current = [];
      setDraggingPaths(new Set());
    },
    onDragOver: (path: string) => setDragOverPath(path),
    onDragLeave: () => setDragOverPath(null),
    onDrop: (targetDir: string, targetIsDir: boolean) => {
      const sources = dragSourceRef.current;
      setDragOverPath(null);
      setDraggingPaths(new Set());
      if (sources.length > 0 && targetIsDir) {
        void moveSources(sources, targetDir);
      }
      dragSourceRef.current = [];
    },
  }), [moveSources, selectedPaths]);

  const focusNode = useCallback((path: string) => {
    setFocusedPath(path);
    if (useVirtual && virtualListRef.current) {
      const idx = visiblePaths.indexOf(path);
      if (idx >= 0) virtualListRef.current.scrollToItem(idx, "center");
    }
    requestAnimationFrame(() => {
      const el = treeRef.current?.querySelector(`[data-path="${CSS.escape(path)}"]`) as HTMLElement | null;
      el?.focus();
    });
  }, [useVirtual, visiblePaths]);

  const handleTreeKeyDown = useCallback((e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
    if (!highlightedTree || visiblePaths.length === 0) return;
    const current = focusedPath ?? visiblePaths[0];
    const idx = visiblePaths.indexOf(current);

    if (e.key === "Home") {
      e.preventDefault();
      focusNode(visiblePaths[0]);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      focusNode(visiblePaths[visiblePaths.length - 1]);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (idx >= 0 && idx < visiblePaths.length - 1) focusNode(visiblePaths[idx + 1]);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (idx > 0) focusNode(visiblePaths[idx - 1]);
      return;
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const node = findNodeByPath(highlightedTree, current);
      if (node?.type === "directory" && !expanded.has(current)) {
        void handleToggle(node);
      } else if (node?.type === "directory" && expanded.has(current) && node.children && node.children.length > 0) {
        focusNode(node.children[0].path);
      }
      return;
    }
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      const node = findNodeByPath(highlightedTree, current);
      if (node?.type === "directory" && expanded.has(current)) {
        setExpanded((prev) => {
          const next = new Set(prev);
          next.delete(current);
          return next;
        });
      } else {
        const parent = findParentPath(highlightedTree, current);
        if (parent) focusNode(parent);
      }
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const node = findNodeByPath(highlightedTree, current);
      if (!node) return;
      if (node.type === "directory") void handleToggle(node);
      return;
    }
    if (e.key === "Delete") {
      e.preventDefault();
      if (selectedPaths.size === 1) {
        void handleDelete([...selectedPaths]);
      } else if (selectedPaths.size > 1) {
        showError(t("deleteMultiNotSupported", "多选时不支持 Delete 删除"));
      }
      return;
    }
    if (e.key === "F2") {
      e.preventDefault();
      if (selectedPaths.size === 1) setRenaming([...selectedPaths][0]);
      else showError(t("renameMultiNotSupported", "多选时不支持重命名"));
      return;
    }
    // Type-ahead: single printable character, no modifier.
    if (e.key.length === 1 && /[\p{L}\p{N}]/u.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      typeAheadRef.current += e.key.toLowerCase();
      if (typeAheadTimerRef.current) clearTimeout(typeAheadTimerRef.current);
      typeAheadTimerRef.current = setTimeout(() => {
        typeAheadRef.current = "";
      }, 500);
      const startIdx = idx >= 0 ? idx : -1;
      const candidates = [
        ...visiblePaths.slice(startIdx + 1),
        ...visiblePaths.slice(0, startIdx + 1),
      ];
      const match = candidates.find((p) =>
        nameFromPath(p).toLowerCase().startsWith(typeAheadRef.current),
      );
      if (match) focusNode(match);
    }
  }, [highlightedTree, visiblePaths, focusedPath, expanded, selectedPaths, focusNode, handleToggle, handleDelete, setExpanded, showError, t]);

  const handleFocus = useCallback((path: string) => {
    setFocusedPath(path);
  }, []);

  useEffect(() => {
    return () => {
      if (typeAheadTimerRef.current) clearTimeout(typeAheadTimerRef.current);
    };
  }, []);

  if (!highlightedTree) {
    return <div className="explorer-tree-empty">{t("noMatchResult", "未找到匹配结果")}</div>;
  }

  const commonNodeProps = {
    expanded,
    selectedPaths,
    highlightPaths,
    renaming,
    creating,
    dragOverPath,
    draggingPaths,
    focusedPath,
    loadingDirPaths,
    dirErrors,
    newItemPlaceholder,
    onToggle: handleToggle,
    onSelect: handleSelect,
    onContextMenu: handleContextMenu,
    onFinishRename: handleRename,
    onFinishCreate: handleCreate,
    onFocusNode: handleFocus,
    ...dragHandlers,
  };

  return (
    <div
      className="explorer-tree"
      role="tree"
      ref={treeRef}
      tabIndex={0}
      onKeyDown={handleTreeKeyDown}
      onFocus={(e) => {
        // Restore focus to the last focused node when the container receives focus.
        if (e.target === treeRef.current && focusedPath) {
          focusNode(focusedPath);
        }
      }}
    >
      {useVirtual ? (
        <VirtualFileTree
          tree={highlightedTree}
          {...commonNodeProps}
          onListRef={(ref) => { virtualListRef.current = ref; }}
        />
      ) : (
        <FileTreeNode
          node={highlightedTree}
          depth={0}
          siblings={[highlightedTree]}
          {...commonNodeProps}
        />
      )}
      {menu && (
        <ContextMenu
          items={buildMenuItems(menu.node)}
          x={menu.x}
          y={menu.y}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}
