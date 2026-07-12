import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { InlineInput } from "./InlineInput";
import { isTextFile, type FileNode } from "./treeUtils";
import { t } from "../../i18n";

const INDENT_PX = 16;
// Lightweight virtualization: cap rendered children per directory. A "show more"
// button reveals the rest. Avoids rendering thousands of nodes for huge dirs.
const MAX_CHILDREN_RENDER = 200;

interface TreeNodeProps {
  node: FileNode;
  depth: number;
  expanded: Set<string>;
  highlightPaths?: Set<string>;
  selectedPaths: Set<string>;
  renaming: string | null;
  creating: { path: string; type: "file" | "directory" } | null;
  dragOverPath: string | null;
  draggingPaths?: Set<string>;
  loadingDirPaths?: Set<string>;
  dirErrors?: Map<string, string>;
  focusedPath?: string;
  siblings: FileNode[];
  newItemPlaceholder: { file: string; folder: string };
  onToggle: (node: FileNode) => void;
  onSelect: (node: FileNode, e: React.MouseEvent, siblings: FileNode[]) => void;
  onContextMenu: (node: FileNode, e: React.MouseEvent) => void;
  onDragStart: (path: string) => void;
  onDragEnd: () => void;
  onDragOver: (path: string) => void;
  onDragLeave: () => void;
  onDrop: (targetDir: string, targetIsDir: boolean) => void;
  onFinishRename: (path: string, newName: string) => void;
  onFinishCreate: (parentPath: string, type: "file" | "directory", name: string) => void;
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

// Move focus to the next/previous visible tree node in DOM order within the tree.
function focusRelativeNode(current: HTMLElement, dir: 1 | -1): void {
  const container = current.closest(".explorer-tree");
  if (!container) return;
  const nodes = Array.from(
    container.querySelectorAll<HTMLElement>(".explorer-tree-node[data-path]"),
  );
  const idx = nodes.indexOf(current);
  const target = nodes[idx + dir];
  if (target) target.focus();
}

export function FileTreeNode(props: TreeNodeProps) {
  const {
    node, depth, expanded, highlightPaths, selectedPaths, renaming, creating, dragOverPath,
    draggingPaths, loadingDirPaths, dirErrors, focusedPath, siblings, newItemPlaceholder,
    onToggle, onSelect, onContextMenu,
    onDragStart, onDragEnd, onDragOver, onDragLeave, onDrop,
    onFinishRename, onFinishCreate, onFocusNode,
  } = props;
  const openFile = useAppStore((s) => s.openFile);
  const [showAll, setShowAll] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
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

  useEffect(() => {
    if (isFocused) {
      requestAnimationFrame(() => {
        const el = ref.current;
        el?.focus();
      });
    }
  }, [isFocused]);

  const handleClick = (e: React.MouseEvent) => {
    // Avoid toggling/selecting when the user interacts with the rename input.
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    onSelect(node, e, siblings);
    if (e.ctrlKey || e.shiftKey || e.metaKey) return;
    if (isDir) onToggle(node);
    else if (isTextFile(node.name)) void openFile(node.path);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        focusRelativeNode(e.currentTarget as HTMLElement, 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusRelativeNode(e.currentTarget as HTMLElement, -1);
        break;
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

  const isText = !isDir && isTextFile(node.name);
  const nodeClass = buildNodeClass(isDir, isExpanded, isHighlighted, isDragOver, isSelected, isDragging, isFocused, isText);
  const indent = depth * INDENT_PX;
  const children = node.children ?? [];
  const visibleChildren = showAll ? children : children.slice(0, MAX_CHILDREN_RENDER);
  const hiddenCount = children.length - visibleChildren.length;

  return (
    <div className="explorer-tree-branch" role="treeitem" aria-expanded={isDir ? isExpanded : undefined} aria-level={depth + 1}>
      <div
        className={nodeClass}
        style={{ paddingLeft: indent }}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        onContextMenu={(e) => onContextMenu(node, e)}
        draggable
        tabIndex={0}
        ref={ref}
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
      {isDir && isExpanded && (
        <div className="explorer-tree-children">
          {children.length > 0 && visibleChildren.map((child) => (
            <FileTreeNode key={child.path} {...props} node={child} depth={depth + 1} siblings={children} />
          ))}
          {hiddenCount > 0 && (
            <button
              type="button"
              className="explorer-tree-more"
              style={{ paddingLeft: indent + INDENT_PX }}
              onClick={() => setShowAll(true)}
            >
              {t("showMore", "显示更多")} (+{hiddenCount})
            </button>
          )}
          {children.length === 0 && !node.lazy && !isLoading && !dirError && (
            <div className="explorer-tree-empty-hint" style={{ paddingLeft: indent + INDENT_PX }}>
              {t("emptyFolder", "空文件夹")}
            </div>
          )}
          {dirError && (
            <div className="explorer-tree-error-hint" style={{ paddingLeft: indent + INDENT_PX }}>
              <span>{t("loadDirFailed", "加载失败")}: {dirError}</span>
              <button type="button" className="explorer-tree-retry-btn" onClick={() => onToggle(node)}>
                {t("retry", "重试")}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
