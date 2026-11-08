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

/** Canonical comparison form. The backend may return `\` even when the picker
 *  supplied `/`; using one separator everywhere keeps expanded/selected sets,
 *  merge comparisons and refresh paths consistent across open sources. */
export function normalizePathSeparators(path: string): string {
  return path.replace(/\\/g, "/");
}

export function normalizeTreePaths(node: FileNode): FileNode {
  const normalized: FileNode = {
    ...node,
    path: normalizePathSeparators(node.path),
  };
  if (node.children) {
    normalized.children = node.children.map(normalizeTreePaths);
  }
  return normalized;
}

/** Normalized path without a trailing separator, for identity comparisons. */
export function pathKey(path: string): string {
  return normalizePathSeparators(path).replace(/\/+$/, "");
}

/**
 * Directories whose children must be refreshed after a tree reload: every
 * expanded directory plus every directory whose children were already loaded
 * (so collapse → expand does not resurrect stale entries). The root is
 * excluded because the reload itself already fetched its children.
 */
export function collectRefreshDirPaths(node: FileNode, expanded: Set<string>): string[] {
  const rootKey = pathKey(node.path);
  const paths: string[] = [];
  const walk = (n: FileNode) => {
    if (n.type !== "directory") return;
    if (pathKey(n.path) !== rootKey && (expanded.has(n.path) || !n.lazy)) {
      paths.push(n.path);
    }
    n.children?.forEach(walk);
  };
  walk(node);
  return paths;
}

const AUTH_ERROR_PATTERN =
  /outside authorized directories|not been explicitly opened|Explorer requires a session|Explorer session is invalid or expired|SSE 403|SSE 401|AUTH_REQUIRED|AUTH_INVALID/i;

/** Convert backend Explorer errors into user-facing Chinese without leaking
 *  temporary paths or raw Win32 messages. */
export function friendlyExplorerError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  const lower = raw.toLowerCase();
  if (lower.includes("path does not exist") || lower.includes("file not found")) {
    return "文件或目录不存在，可能已被移动或删除";
  }
  if (
    lower.includes("winerror 5") ||
    lower.includes("permission denied") ||
    lower.includes("access is denied") ||
    raw.includes("拒绝访问")
  ) {
    return "没有权限访问该文件或目录";
  }
  if (lower.includes("file too large")) {
    return "文件过大，无法在编辑器中打开";
  }
  if (lower.includes("not a directory") || lower.includes("directory not found")) {
    return "目标不是有效文件夹";
  }
  if (lower.includes("not a file")) {
    return "目标不是文件";
  }
  if (lower.includes("could not be resolved")) {
    return "无法解析该路径";
  }
  if (lower.includes("symbolic links are not allowed")) {
    return "不支持通过符号链接访问";
  }
  if (lower.includes("path traversal")) {
    return "路径不合法";
  }
  if (lower.includes("file changed") || lower.includes("version conflict")) {
    return "文件已在外部修改，请重新加载后再保存";
  }
  if (lower.includes("search timeout")) {
    return "搜索超时，请缩小范围后重试";
  }
  if (lower.includes("already exists")) {
    return "目标已存在";
  }
  if (AUTH_ERROR_PATTERN.test(raw)) {
    return "该项目尚未授权，请重新打开项目";
  }
  return raw.replace(/^security check failed:\s*/i, "").replace(/^write failed:\s*/i, "");
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

/** Compute a path relative to rootPath (both normalized to forward slashes). */
export function relativePath(rootPath: string, fullPath: string): string {
  const normRoot = rootPath.replace(/\\/g, "/").replace(/\/$/, "");
  const normFull = fullPath.replace(/\\/g, "/");
  if (normFull === normRoot) return "";
  if (normFull.startsWith(normRoot + "/")) return normFull.slice(normRoot.length + 1);
  return normFull;
}
