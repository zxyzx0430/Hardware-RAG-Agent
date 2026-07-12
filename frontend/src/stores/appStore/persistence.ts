import { loadFromStorage } from "../../utils/persistence";
import type { OpenFileItem } from "../../types";

export const RIGHT_PANEL_KEY = "hwrag_right_panel_open";
export const RIGHT_PANEL_WIDTH_KEY = "hwrag_right_panel_width";
export const EXPLORER_OPEN_KEY = "hwrag_explorer_open";
export const EXPLORER_WIDTH_KEY = "hwrag_explorer_width";
export const EXPLORER_ROOT_PATH_KEY = "hwrag_explorer_root_path";
export const EDITOR_SHOW_TREE_KEY = "hwrag_editor_show_tree";
export const RECENT_FOLDERS_KEY = "hwrag_recent_folders";
export const OPEN_FILES_KEY = "hwrag_open_files";
export const ACTIVE_FILE_ID_KEY = "hwrag_active_file_id";

export const EXPLORER_EXPANDED_PREFIX = "hwrag_explorer_expanded_";
export const EXPLORER_SELECTED_PREFIX = "hwrag_explorer_selected_";

export const DEFAULT_EXPLORER_WIDTH = 280;
export const MAX_RECENT_FOLDERS = 10;
export const MAX_PERSISTED_PATHS = 500;

// Right panel width bounds (px), persisted to localStorage
export const DEFAULT_RIGHT_PANEL_WIDTH = 340;
export const RIGHT_PANEL_MIN_WIDTH = 340;
export const RIGHT_PANEL_MAX_WIDTH = 560;
export const CHAT_MIN_WIDTH = 400;
export const EXPLORER_MIN_WIDTH = 340;

export function loadRightPanelOpen(): boolean | null {
  return loadBoolean(RIGHT_PANEL_KEY);
}

export function saveRightPanelOpen(open: boolean): void {
  saveBoolean(RIGHT_PANEL_KEY, open);
}

export function loadRightPanelWidth(): number {
  const n = loadNumber(RIGHT_PANEL_WIDTH_KEY);
  if (n === null) return DEFAULT_RIGHT_PANEL_WIDTH;
  return Math.min(RIGHT_PANEL_MAX_WIDTH, Math.max(RIGHT_PANEL_MIN_WIDTH, n));
}

export function saveRightPanelWidth(width: number): void {
  saveNumber(RIGHT_PANEL_WIDTH_KEY, width);
}

export function loadBoolean(key: string): boolean | null {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? null : raw === "true";
  } catch {
    return null;
  }
}

export function saveBoolean(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    // ignore
  }
}

export function loadNumber(key: string): number | null {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

export function saveNumber(key: string, value: number): void {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    // ignore
  }
}

export function loadString(key: string): string | null {
  try {
    const raw = localStorage.getItem(key);
    return raw === null || raw === "" ? null : raw;
  } catch {
    return null;
  }
}

export function saveString(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore
  }
}

export function loadExplorerRootPath(): string | null {
  return loadString(EXPLORER_ROOT_PATH_KEY);
}

export function saveExplorerRootPath(path: string): void {
  saveString(EXPLORER_ROOT_PATH_KEY, path);
}

export function clearExplorerRootPath(): void {
  try {
    localStorage.removeItem(EXPLORER_ROOT_PATH_KEY);
  } catch {
    // ignore
  }
}

export function loadEditorShowTree(): boolean {
  return loadBoolean(EDITOR_SHOW_TREE_KEY) ?? false;
}

export function saveEditorShowTree(show: boolean): void {
  saveBoolean(EDITOR_SHOW_TREE_KEY, show);
}

export function loadStringArray(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

export function saveStringArray(key: string, value: string[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore
  }
}

function fileNameFromPath(path: string): string {
  return path.split(/[\\/]/).pop() ?? "";
}

export function loadOpenFiles(): OpenFileItem[] {
  try {
    const raw = localStorage.getItem(OPEN_FILES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is { id?: unknown; path?: unknown; pinned?: unknown } =>
          item !== null && typeof item === "object",
      )
      .map((item) => ({
        id: String(item.id ?? ""),
        path: String(item.path ?? ""),
        name: fileNameFromPath(String(item.path ?? "")),
        pinned: item.pinned === true,
      }))
      .filter(
        (item) =>
          item.id !== "" && item.path !== "" && !item.path.startsWith("buffer://"),
      );
  } catch {
    return [];
  }
}

export function saveOpenFiles(files: OpenFileItem[]): void {
  try {
    const stash = files
      .filter((f) => f.isBuffer !== true && !f.path.startsWith("buffer://"))
      .map((f) => ({
        id: f.id,
        path: f.path,
        pinned: f.pinned ?? false,
      }));
    localStorage.setItem(OPEN_FILES_KEY, JSON.stringify(stash));
  } catch {
    // ignore
  }
}

export function loadActiveFileId(): string | null {
  try {
    const raw = localStorage.getItem(ACTIVE_FILE_ID_KEY);
    return raw === null ? null : raw;
  } catch {
    return null;
  }
}

export function saveActiveFileId(id: string | null): void {
  try {
    if (id === null) {
      localStorage.removeItem(ACTIVE_FILE_ID_KEY);
    } else {
      localStorage.setItem(ACTIVE_FILE_ID_KEY, id);
    }
  } catch {
    // ignore
  }
}

function storageKeyForRoot(prefix: string, rootPath: string): string {
  // Normalize separators so the key is stable across platforms.
  return prefix + rootPath.replace(/[\\/]/g, "|");
}

export function loadExpandedPaths(rootPath: string): string[] {
  return loadStringArray(storageKeyForRoot(EXPLORER_EXPANDED_PREFIX, rootPath));
}

export function saveExpandedPaths(rootPath: string, paths: string[]): void {
  saveStringArray(storageKeyForRoot(EXPLORER_EXPANDED_PREFIX, rootPath), paths.slice(0, MAX_PERSISTED_PATHS));
}

export function loadSelectedPaths(rootPath: string): string[] {
  return loadStringArray(storageKeyForRoot(EXPLORER_SELECTED_PREFIX, rootPath));
}

export function saveSelectedPaths(rootPath: string, paths: string[]): void {
  saveStringArray(storageKeyForRoot(EXPLORER_SELECTED_PREFIX, rootPath), paths.slice(0, MAX_PERSISTED_PATHS));
}

export function clearExplorerStateForRoot(rootPath: string): void {
  try {
    localStorage.removeItem(storageKeyForRoot(EXPLORER_EXPANDED_PREFIX, rootPath));
    localStorage.removeItem(storageKeyForRoot(EXPLORER_SELECTED_PREFIX, rootPath));
  } catch {
    // ignore
  }
}

function hasConfiguredApiKey(): boolean {
  const settings = loadFromStorage("settings", null as Record<string, unknown> | null);
  const providers = settings?.providers;
  if (!Array.isArray(providers)) return false;
  return providers.some(
    (p) => p && typeof p === "object" && Boolean((p as Record<string, unknown>).apiKey),
  );
}

function activeSessionHasMessages(): boolean {
  const sessions = loadFromStorage("sessions", [] as { id: string; msgCount?: number }[]);
  const activeId = loadFromStorage("activeSession", "");
  if (!activeId) return false;
  const session = sessions.find((s) => s.id === activeId);
  return !!session && (session.msgCount ?? 0) > 0;
}

export function getDefaultRightPanelOpen(): boolean {
  const saved = loadRightPanelOpen();
  if (saved !== null) return saved;
  if (!hasConfiguredApiKey()) return false;
  if (!activeSessionHasMessages()) return false;
  return true;
}
