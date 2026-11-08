import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { FixedSizeList, type ListChildComponentProps } from "react-window";
import { useAppStore } from "../../stores/useAppStore";
import { InlineInput } from "./InlineInput";
import { isTextFile, type FileNode } from "./treeUtils";
import { t } from "../../i18n";

const INDENT_PX = 16;
const ROW_HEIGHT = 28;
const OVERSCAN_COUNT = 5;
const PERMISSION_ERROR_PATTERN = /permission denied|access is denied|拒绝访问|没有权限|无权限|EACCES|EPERM/i;

function formatDirError(message: string): string {
  return PERMISSION_ERROR_PATTERN.test(message)
    ? t("dirPermissionDenied", "无权限读取该文件夹")
    : `${t("loadDirFailed", "加载失败")}: ${message}`;
}

interface NodeRow {
  kind: "node";
  node: FileNode;
  depth: number;
  siblings: FileNode[];
}

interface HintRow {
  kind: "hint";
  hint: "create" | "empty" | "error";
  id: string;
  node: FileNode;
  depth: number;
  error?: string;
}

type FlatRow = NodeRow | HintRow;

interface RowData {
  rows: FlatRow[];
  expanded: Set<string>;
  selectedPaths: Set<string>;
  highlightPaths?: Set<string>;
  renaming: string | null;
  creating: { path: string; type: "file" | "directory" } | null;
  dragOverPath: string | null;
  draggingPaths?: Set<string>;
  loadingDirPaths?: Set<string>;
  dirErrors?: Map<string, string>;
  focusedPath?: string;
  newItemPlaceholder: { file: string; folder: string };
  onToggle: (node: FileNode) => void;
  onRetry?: (node: FileNode) => void;
  onSelect: (node: FileNode, e: React.MouseEvent, siblings: FileNode[]) => void;
  onContextMenu: (node: FileNode, e: React.MouseEvent) => void;
  onFinishRename: (path: string, newName: string) => void;
  onFinishCreate: (parentPath: string, type: "file" | "directory", name: string) => void;
  onDragStart: (path: string) => void;
  onDragEnd: () => void;
  onDragOver: (path: string) => void;
  onDragLeave: () => void;
  onDrop: (targetDir: string, targetIsDir: boolean) => void;
  onFocusNode?: (path: string) => void;
}

function buildNodeClass(
  isDir: boolean,
  isExpanded: boolean,
  isHighlighted: boolean,
  isDragOver: boolean,
  isSelected: boolean,
  isDragging: boolean,
  isFocused: boolean,
  isText: boolean,
): string {
  return [
    "explorer-tree-node",
    isDir ? "is-dir" : "is-file",
    !isDir && isText ? "is-text" : "",
    isExpanded ? "is-expanded" : "",
    isHighlighted ? "is-highlighted" : "",
    isDragOver ? "is-drag-over" : "",
    isSelected ? "is-selected" : "",
    isDragging ? "is-dragging" : "",
    isFocused ? "is-focused" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function FileTreeRow({ index, style, data }: ListChildComponentProps<RowData>) {
  const {
    rows,
    expanded,
    selectedPaths,
    highlightPaths,
    renaming,
    creating,
    dragOverPath,
    draggingPaths,
    loadingDirPaths,
    focusedPath,
    newItemPlaceholder,
    onToggle,
    onRetry,
    onSelect,
    onContextMenu,
    onFinishRename,
    onFinishCreate,
    onDragStart,
    onDragEnd,
    onDragOver,
    onDragLeave,
    onDrop,
    onFocusNode,
  } = data;
  const openFile = useAppStore((s) => s.openFile);
  const row = rows[index];
  if (!row) return null;
  const indent = row.depth * INDENT_PX;

  // Auxiliary rows (create input, empty-folder hint, load error) keep the same
  // fixed height so react-window can lay them out as part of the virtual list.
  if (row.kind === "hint") {
    if (row.hint === "create") {
      return (
        <div style={style}>
          <div className="explorer-tree-node explorer-tree-create-row" style={{ paddingLeft: indent }}>
            <span className="explorer-tree-icon">{creating?.type === "directory" ? "📁" : "📄"}</span>
            {creating && (
              <InlineInput
                initialValue=""
                placeholder={creating.type === "directory" ? newItemPlaceholder.folder : newItemPlaceholder.file}
                onSubmit={(name) => onFinishCreate(row.node.path, creating.type, name)}
                onCancel={() => onFinishCreate(row.node.path, creating.type, "")}
              />
            )}
          </div>
        </div>
      );
    }
    if (row.hint === "empty") {
      return (
        <div style={style}>
          <div className="explorer-tree-empty-hint" style={{ paddingLeft: indent }}>
            {t("emptyFolder", "空文件夹")}
          </div>
        </div>
      );
    }
    return (
      <div style={style}>
        <div className="explorer-tree-error-hint" style={{ paddingLeft: indent }}>
          <span title={row.error}>{formatDirError(row.error ?? "")}</span>
          <button type="button" className="explorer-tree-retry-btn" onClick={() => onRetry?.(row.node)}>
            {t("retry", "重试")}
          </button>
        </div>
      </div>
    );
  }

  const { node, depth, siblings } = row;
  const isDir = node.type === "directory";
  const isExpanded = expanded.has(node.path);
  const isHighlighted = highlightPaths?.has(node.path) ?? false;
  const isSelected = selectedPaths.has(node.path);
  const isDragOver = dragOverPath === node.path && isDir;
  const isDragging = draggingPaths?.has(node.path) ?? false;
  const dragCount = draggingPaths?.size ?? 0;
  const isLoading = loadingDirPaths?.has(node.path) ?? false;
  const isFocused = focusedPath === node.path;

  const handleClick = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    onSelect(node, e, siblings.length > 0 ? siblings : [node]);
    if (e.ctrlKey || e.shiftKey || e.metaKey) return;
    if (isDir) onToggle(node);
    else void openFile(node.path);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowRight":
        if (isDir && !isExpanded) onToggle(node);
        break;
      case "ArrowLeft":
        if (isDir && isExpanded) onToggle(node);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (isDir) onToggle(node);
        else void openFile(node.path);
        break;
      default:
        break;
    }
  };

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData("text/plain", node.path);
    e.dataTransfer.effectAllowed = "move";
    onDragStart(node.path);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (isDir) onDragOver(node.path);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    const next = e.relatedTarget as Node | null;
    if (next && e.currentTarget.contains(next)) return;
    onDragLeave();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    onDrop(node.path, isDir);
  };

  const isText = !isDir && isTextFile(node.name);
  const nodeClass = buildNodeClass(isDir, isExpanded, isHighlighted, isDragOver, isSelected, isDragging, isFocused, isText);

  return (
    <div style={style} role="treeitem" aria-expanded={isDir ? isExpanded : undefined} aria-level={depth + 1}>
      <div
        className={nodeClass}
        style={{ paddingLeft: indent }}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        onContextMenu={(e) => onContextMenu(node, e)}
        draggable
        tabIndex={0}
        onFocus={() => onFocusNode?.(node.path)}
        onDragStart={handleDragStart}
        onDragEnd={onDragEnd}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        data-path={node.path}
        data-depth={depth}
      >
        {isDir ? (
          <span className="explorer-tree-chevron">{isExpanded ? "▼" : "▶"}</span>
        ) : (
          <span className="explorer-tree-icon">{isTextFile(node.name) ? "📄" : "📦"}</span>
        )}
        {renaming === node.path ? (
          <InlineInput
            initialValue={node.name}
            onSubmit={(name) => onFinishRename(node.path, name)}
            onCancel={() => onFinishRename(node.path, node.name)}
          />
        ) : (
          <>
            <span className="explorer-tree-label" title={node.name}>
              {node.matchStart !== undefined && node.matchEnd !== undefined ? (
                <>
                  {node.name.substring(0, node.matchStart)}
                  <mark className="explorer-tree-match">{node.name.substring(node.matchStart, node.matchEnd)}</mark>
                  {node.name.substring(node.matchEnd)}
                </>
              ) : (
                node.name
              )}
            </span>
            {isDragging && dragCount > 1 && (
              <span className="explorer-tree-drag-hint">
                {t("moveNItems", "移动 {n} 项").replace("{n}", String(dragCount))}
              </span>
            )}
            {isLoading && <span className="explorer-tree-loading-hint">⏳</span>}
          </>
        )}
      </div>
    </div>
  );
}

interface VirtualFileTreeProps {
  tree: FileNode;
  expanded: Set<string>;
  selectedPaths: Set<string>;
  highlightPaths?: Set<string>;
  renaming: string | null;
  creating: { path: string; type: "file" | "directory" } | null;
  dragOverPath: string | null;
  draggingPaths?: Set<string>;
  loadingDirPaths?: Set<string>;
  dirErrors?: Map<string, string>;
  focusedPath?: string;
  newItemPlaceholder: { file: string; folder: string };
  onToggle: (node: FileNode) => void;
  onRetry?: (node: FileNode) => void;
  onSelect: (node: FileNode, e: React.MouseEvent, siblings: FileNode[]) => void;
  onContextMenu: (node: FileNode, e: React.MouseEvent) => void;
  onFinishRename: (path: string, newName: string) => void;
  onFinishCreate: (parentPath: string, type: "file" | "directory", name: string) => void;
  onDragStart: (path: string) => void;
  onDragEnd: () => void;
  onDragOver: (path: string) => void;
  onDragLeave: () => void;
  onDrop: (targetDir: string, targetIsDir: boolean) => void;
  onFocusNode?: (path: string) => void;
  onListRef?: (ref: { scrollToItem: (index: number, align?: string) => void } | null) => void;
}

function flattenVisibleRows(
  node: FileNode,
  expanded: Set<string>,
  siblings: FileNode[],
  depth: number,
  dirErrors: Map<string, string> | undefined,
  creating: { path: string; type: "file" | "directory" } | null,
): FlatRow[] {
  const rows: FlatRow[] = [{ kind: "node", node, depth, siblings }];
  if (node.type !== "directory") return rows;
  if (creating?.path === node.path) {
    rows.push({ kind: "hint", hint: "create", id: `${node.path}::create`, node, depth: depth + 1 });
  }
  if (!expanded.has(node.path)) return rows;
  const children = node.children ?? [];
  for (const child of children) {
    rows.push(...flattenVisibleRows(child, expanded, children, depth + 1, dirErrors, creating));
  }
  const dirError = dirErrors?.get(node.path);
  if (dirError) {
    rows.push({ kind: "hint", hint: "error", id: `${node.path}::error`, node, depth: depth + 1, error: dirError });
  } else if (node.children && children.length === 0 && !node.lazy) {
    rows.push({ kind: "hint", hint: "empty", id: `${node.path}::empty`, node, depth: depth + 1 });
  }
  return rows;
}

/** Height of the scrollable tree pane; falls back to a window-derived size when
 *  the pane is not laid out yet (initial render, tests, detached tree). */
function useListHeight(containerRef: RefObject<HTMLDivElement | null>): number {
  const measure = useCallback(() => {
    const pane = containerRef.current?.closest(".explorer-tree-pane") as HTMLElement | null;
    if (pane && pane.clientHeight > 0) return pane.clientHeight;
    return typeof window !== "undefined" ? Math.max(160, window.innerHeight - 160) : 600;
  }, [containerRef]);
  const [height, setHeight] = useState<number>(() => measure());

  useEffect(() => {
    const update = () => setHeight(measure());
    update();
    const pane = containerRef.current?.closest(".explorer-tree-pane") as HTMLElement | null;
    let observer: ResizeObserver | null = null;
    if (pane && typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(update);
      observer.observe(pane);
    }
    if (typeof window !== "undefined") window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      if (typeof window !== "undefined") window.removeEventListener("resize", update);
    };
  }, [measure, containerRef]);

  return height;
}

export function VirtualFileTree(props: VirtualFileTreeProps) {
  const { tree, dirErrors, creating } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const rows = useMemo(
    () => flattenVisibleRows(tree, props.expanded, [tree], 0, dirErrors, creating),
    [tree, props.expanded, dirErrors, creating],
  );
  const itemData = useMemo<RowData>(() => ({ rows, ...props }), [rows, props]);
  const height = useListHeight(containerRef);

  const outerRef = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    el.setAttribute("role", "tree");
  }, []);

  return (
    <div ref={containerRef}>
      <FixedSizeList
        outerRef={outerRef}
        ref={(ref) => {
          if (ref) {
            const listRef = ref as unknown as { scrollToItem: (index: number, align?: string) => void };
            props.onListRef?.(listRef);
          } else {
            props.onListRef?.(null);
          }
        }}
        height={height}
        itemCount={rows.length}
        itemSize={ROW_HEIGHT}
        itemData={itemData}
        overscanCount={OVERSCAN_COUNT}
        width="100%"
        className="explorer-virtual-tree"
      >
        {FileTreeRow}
      </FixedSizeList>
    </div>
  );
}
