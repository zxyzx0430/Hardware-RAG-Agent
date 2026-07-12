import { useCallback, useMemo } from "react";
import { FixedSizeList, type ListChildComponentProps } from "react-window";
import { useAppStore } from "../../stores/useAppStore";
import { InlineInput } from "./InlineInput";
import { isTextFile, type FileNode } from "./treeUtils";
import { t } from "../../i18n";

const INDENT_PX = 16;
const ROW_HEIGHT = 28;
const OVERSCAN_COUNT = 5;

interface FlatNode {
  node: FileNode;
  depth: number;
  siblings: FileNode[];
}

interface RowData {
  flatNodes: FlatNode[];
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
): string {
  return [
    "explorer-tree-node",
    isDir ? "is-dir" : "is-file",
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
    flatNodes,
    expanded,
    selectedPaths,
    highlightPaths,
    renaming,
    creating,
    dragOverPath,
    draggingPaths,
    loadingDirPaths,
    dirErrors,
    focusedPath,
    newItemPlaceholder,
    onToggle,
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
  const { node, depth, siblings } = flatNodes[index];
  const isDir = node.type === "directory";
  const isExpanded = expanded.has(node.path);
  const isHighlighted = highlightPaths?.has(node.path) ?? false;
  const isSelected = selectedPaths.has(node.path);
  const isDragOver = dragOverPath === node.path && isDir;
  const isDragging = draggingPaths?.has(node.path) ?? false;
  const dragCount = draggingPaths?.size ?? 0;
  const isLoading = loadingDirPaths?.has(node.path) ?? false;
  const dirError = dirErrors?.get(node.path);
  const isFocused = focusedPath === node.path;
  const indent = depth * INDENT_PX;

  const handleClick = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    onSelect(node, e, siblings.length > 0 ? siblings : [node]);
    if (e.ctrlKey || e.shiftKey || e.metaKey) return;
    if (isDir) onToggle(node);
    else if (isTextFile(node.name)) void openFile(node.path);
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
        else if (isTextFile(node.name)) void openFile(node.path);
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

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    onDrop(node.path, isDir);
  };

  const nodeClass = buildNodeClass(isDir, isExpanded, isHighlighted, isDragOver, isSelected, isDragging, isFocused);

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
        onDragLeave={onDragLeave}
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
            <span className="explorer-tree-label">
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
      {creating?.path === node.path && (
        <div
          className="explorer-tree-node explorer-tree-create-row"
          style={{ paddingLeft: indent + INDENT_PX }}
        >
          <span className="explorer-tree-icon">{creating.type === "directory" ? "📁" : "📄"}</span>
          <InlineInput
            initialValue=""
            placeholder={creating.type === "directory" ? newItemPlaceholder.folder : newItemPlaceholder.file}
            onSubmit={(name) => onFinishCreate(node.path, creating.type, name)}
            onCancel={() => onFinishCreate(node.path, creating.type, "")}
          />
        </div>
      )}
      {isDir && isExpanded && dirError && (
        <div className="explorer-tree-error-hint" style={{ paddingLeft: indent + INDENT_PX }}>
          <span>{t("loadDirFailed", "加载失败")}: {dirError}</span>
          <button type="button" className="explorer-tree-retry-btn" onClick={() => onToggle(node)}>
            {t("retry", "重试")}
          </button>
        </div>
      )}
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

function flattenVisibleNodes(
  node: FileNode,
  expanded: Set<string>,
  siblings: FileNode[] = [node],
  depth = 0,
): FlatNode[] {
  const result: FlatNode[] = [{ node, depth, siblings }];
  if (node.type === "directory" && expanded.has(node.path) && node.children) {
    for (const child of node.children) {
      result.push(...flattenVisibleNodes(child, expanded, node.children, depth + 1));
    }
  }
  return result;
}

export function VirtualFileTree(props: VirtualFileTreeProps) {
  const { tree } = props;
  const flatNodes = useMemo(() => flattenVisibleNodes(tree, props.expanded), [tree, props.expanded]);
  const itemData = useMemo<RowData>(() => ({ flatNodes, ...props }), [flatNodes, props]);
  const height = typeof window !== "undefined" ? window.innerHeight - 160 : 600;

  const outerRef = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    el.setAttribute("role", "tree");
  }, []);

  return (
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
      itemCount={flatNodes.length}
      itemSize={ROW_HEIGHT}
      itemData={itemData}
      overscanCount={OVERSCAN_COUNT}
      width="100%"
      className="explorer-virtual-tree"
    >
      {FileTreeRow}
    </FixedSizeList>
  );
}
