export interface FileNode {
  name: string;
  type: "file" | "directory";
  path: string;
  children?: FileNode[];
  /** True when the directory's children have not been loaded yet. */
  lazy?: boolean;
  /** Byte offsets of the highlighted match inside `name` (inclusive start, exclusive end). */
  matchStart?: number;
  matchEnd?: number;
}

// 可打开的文本文件扩展名白名单（与 fileLanguage.ts 对齐）
// 注意：后端 _is_text_file 用 UTF-8 字节采样做最终判断，这里只是前端快速过滤
const TEXT_EXTS = new Set([
  // 文本
  ".txt", ".md", ".markdown", ".log",
  // 数据
  ".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".conf", ".cfg", ".properties",
  ".csv", ".tsv", ".sql", ".graphql", ".proto",
  // C/C++/嵌入式
  ".c", ".cpp", ".cc", ".cxx", ".h", ".hh", ".hpp", ".ino", ".pde", ".asm", ".s",
  // Python
  ".py", ".pyw", ".pyi",
  // JS/TS
  ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
  // Web
  ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
  // Shell
  ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1",
  // Systems
  ".rs", ".go", ".java", ".kt", ".swift", ".cs", ".rb", ".php",
  // Build
  ".cmake", ".gradle",
]);

// 无扩展名但可打开的文件名
const TEXT_NAMES = new Set([
  "makefile", "dockerfile", "license", "readme", "rakefile", "gemfile",
  ".gitignore", ".dockerignore", ".editorconfig", ".env", ".npmrc",
  "platformio.ini", "platformio.local.ini",
]);

export function isTextFile(name: string): boolean {
  const lower = name.toLowerCase();
  const lastDot = name.lastIndexOf(".");
  const ext = lastDot >= 0 ? name.slice(lastDot).toLowerCase() : "";
  // 无扩展名：检查文件名白名单
  if (lastDot < 0 || lastDot === 0) {
    return TEXT_NAMES.has(lower) || TEXT_NAMES.has(lower.replace(/^\./, ""));
  }
  return TEXT_EXTS.has(ext);
}

function nameMatches(node: FileNode, query: string): boolean {
  return node.name.toLowerCase().includes(query.toLowerCase());
}

function anyChildMatches(node: FileNode, query: string): boolean {
  return node.children?.some((child) => matchesSearch(child, query)) ?? false;
}

export function matchesSearch(node: FileNode, query: string): boolean {
  if (!query) return true;
  return nameMatches(node, query) || anyChildMatches(node, query);
}

export function filterTree(node: FileNode, query: string): FileNode | null {
  if (!query) return node;
  if (node.type === "file") return nameMatches(node, query) ? node : null;
  const kept = (node.children ?? [])
    .map((child) => filterTree(child, query))
    .filter((child): child is FileNode => child !== null);
  if (kept.length === 0 && !nameMatches(node, query)) return null;
  return { ...node, children: kept };
}

function findMatchSpan(name: string, query: string): [number, number] | null {
  const idx = name.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return null;
  return [idx, idx + query.length];
}

/** Return a new tree with `matchStart`/`matchEnd` set on nodes whose name matches `query`. */
export function highlightTree(node: FileNode, query: string): FileNode {
  if (!query) return node;
  const span = findMatchSpan(node.name, query);
  const children = node.children?.map((child) => highlightTree(child, query));
  const result: FileNode = { ...node, children };
  if (span) {
    result.matchStart = span[0];
    result.matchEnd = span[1];
  }
  return result;
}

/** Count file nodes whose name matches `query` (directories are not counted). */
export function countMatches(node: FileNode, query: string): number {
  if (!query) return 0;
  if (node.type === "file") return nameMatches(node, query) ? 1 : 0;
  return (node.children ?? []).reduce((sum, child) => sum + countMatches(child, query), 0);
}

export function fileNameFromPath(path: string): string {
  return path.split(/[\\/]/).pop() ?? "";
}

/** Recursively collect paths of every directory under (and including) `node`. */
export function collectDirPaths(node: FileNode): string[] {
  const paths: string[] = [];
  const walk = (n: FileNode) => {
    if (n.type === "directory") {
      paths.push(n.path);
      n.children?.forEach(walk);
    }
  };
  walk(node);
  return paths;
}

/**
 * Collect directory paths inside `node` that are both expanded and still lazy
 * (children not loaded yet). Used to restore expansion after a tree refresh.
 */
export function collectLazyExpandedPaths(node: FileNode, expanded: Set<string>): string[] {
  const paths: string[] = [];
  const walk = (n: FileNode) => {
    if (n.type !== "directory") return;
    if (expanded.has(n.path) && n.lazy) {
      paths.push(n.path);
    }
    // Only descend into already-loaded children; lazy nodes have no children to inspect.
    n.children?.forEach(walk);
  };
  walk(node);
  return paths;
}

/** Compute a path relative to rootPath (both normalized to forward slashes). */
export function relativePath(rootPath: string, fullPath: string): string {
  const normRoot = rootPath.replace(/\\/g, "/").replace(/\/$/, "");
  const normFull = fullPath.replace(/\\/g, "/");
  if (normFull === normRoot) return "";
  if (normFull.startsWith(normRoot + "/")) return normFull.slice(normRoot.length + 1);
  return normFull;
}
