import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet } from "../../api/client";
import { useAppStore } from "../../stores/useAppStore";
import { useToastStore } from "../../stores/useToastStore";
import { useI18n } from "../../i18n";

interface DirEntry {
  name: string;
  path: string;
}

interface BrowseResponse {
  current: string;
  parent: string | null;
  directories: DirEntry[];
}

interface FolderPickerDialogProps {
  initialPath?: string | null;
  onConfirm: (path: string) => void;
  onCancel: () => void;
}

const DRIVE_PLACEHOLDER = "此电脑";
const FAVORITE_FOLDERS_KEY = "hwrag_favorite_folders";

function loadFavorites(): string[] {
  try {
    const raw = localStorage.getItem(FAVORITE_FOLDERS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveFavorites(list: string[]): void {
  try {
    localStorage.setItem(FAVORITE_FOLDERS_KEY, JSON.stringify(list));
  } catch {
    // ignore
  }
}

function pathBaseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

export function FolderPickerDialog({ initialPath, onConfirm, onCancel }: FolderPickerDialogProps) {
  const { t } = useI18n();
  const [current, setCurrent] = useState<string>(initialPath ?? "");
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<DirEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [pathInput, setPathInput] = useState<string>(initialPath ?? "");
  const recentFolders = useAppStore((s) => s.recentFolders);
  const [favorites, setFavorites] = useState<string[]>(() => loadFavorites());

  // Persist favorites whenever they change
  useEffect(() => {
    saveFavorites(favorites);
  }, [favorites]);

  // Merge manual favorites with recent folders (recent items are not removable)
  const favoriteItems = useMemo(() => {
    const seen = new Set<string>();
    const items: { path: string; name: string; removable: boolean }[] = [];
    for (const p of favorites) {
      if (seen.has(p)) continue;
      seen.add(p);
      items.push({ path: p, name: pathBaseName(p), removable: true });
    }
    for (const p of recentFolders) {
      if (seen.has(p)) continue;
      seen.add(p);
      items.push({ path: p, name: pathBaseName(p), removable: false });
    }
    return items;
  }, [favorites, recentFolders]);

  const addToFavorites = useCallback(() => {
    const target = current || pathInput.trim();
    if (!target) return;
    setFavorites((prev) => (prev.includes(target) ? prev : [...prev, target]));
  }, [current, pathInput]);

  const removeFromFavorites = useCallback((path: string) => {
    setFavorites((prev) => prev.filter((p) => p !== path));
  }, []);

  // 浏览历史栈：支持前进/后退
  const historyRef = useRef<string[]>(initialPath ? [initialPath] : []);
  const historyIndexRef = useRef<number>(initialPath ? 0 : -1);
  const [navState, setNavState] = useState({ canGoBack: false, canGoForward: false });
  const isNavigatingRef = useRef(false);

  const updateNavState = useCallback(() => {
    setNavState({
      canGoBack: historyIndexRef.current > 0,
      canGoForward: historyIndexRef.current < historyRef.current.length - 1,
    });
  }, []);

  const load = useCallback(async (path: string | null) => {
    setLoading(true);
    try {
      const query = path ? `explorer/browse?path=${encodeURIComponent(path)}` : "explorer/browse";
      const res = await apiGet<BrowseResponse>(query);
      setCurrent(res.current);
      setParent(res.parent);
      setDirs(res.directories);
      setPathInput(res.current);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      useToastStore.getState().showError("浏览目录失败: " + msg);
    } finally {
      setLoading(false);
    }
  }, []);

  // 导航到指定路径，并记录到历史栈
  const navigateTo = useCallback((path: string | null) => {
    // 如果是前进/后退触发的，不记录历史
    if (!isNavigatingRef.current) {
      // 如果历史栈为空且当前有路径，先把当前路径加入历史（作为后退起点）
      if (historyRef.current.length === 0 && current) {
        historyRef.current.push(current);
        historyIndexRef.current = 0;
      }
      const cur = historyIndexRef.current;
      // 截断当前位置之后的历史（前进方向）
      historyRef.current = historyRef.current.slice(0, cur + 1);
      historyRef.current.push(path ?? "");
      historyIndexRef.current = historyRef.current.length - 1;
      updateNavState();
    }
    isNavigatingRef.current = false;
    void load(path);
  }, [load, updateNavState, current]);

  const goBack = useCallback(() => {
    const idx = historyIndexRef.current;
    if (idx <= 0) return;
    historyIndexRef.current = idx - 1;
    updateNavState();
    isNavigatingRef.current = true;
    void load(historyRef.current[idx - 1]);
  }, [load, updateNavState]);

  const goForward = useCallback(() => {
    const idx = historyIndexRef.current;
    if (idx >= historyRef.current.length - 1) return;
    historyIndexRef.current = idx + 1;
    updateNavState();
    isNavigatingRef.current = true;
    void load(historyRef.current[idx + 1]);
  }, [load, updateNavState]);

  const goUp = useCallback(() => {
    if (parent) navigateTo(parent);
  }, [parent, navigateTo]);

  const enterDir = useCallback((dir: DirEntry) => {
    navigateTo(dir.path);
  }, [navigateTo]);

  const handlePathInputKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const trimmed = pathInput.trim();
      if (trimmed) navigateTo(trimmed);
    }
  }, [pathInput, navigateTo]);

  const handleConfirm = useCallback(() => {
    const target = current || pathInput.trim();
    if (target) onConfirm(target);
  }, [current, pathInput, onConfirm]);

  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onCancel();
  }, [onCancel]);

  // 初始加载
  useEffect(() => {
    void load(initialPath ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const breadcrumb = current ? current.split(/[\\/]/).filter(Boolean) : [];

  return (
    <div className="folder-picker-embed" onClick={handleBackdropClick}>
      <div className="folder-picker-embed-inner">
        <div className="folder-picker-header">
          <span className="folder-picker-title">{t("openFolder", "打开文件夹")}</span>
          <button className="folder-picker-close" onClick={onCancel} title={t("cancel", "取消")}>×</button>
        </div>

        <div className="folder-picker-pathbar">
          <button
            className="folder-picker-nav-btn"
            onClick={goBack}
            disabled={!navState.canGoBack}
            title={t("back", "后退")}
          >
            ←
          </button>
          <button
            className="folder-picker-nav-btn"
            onClick={goForward}
            disabled={!navState.canGoForward}
            title={t("forward", "前进")}
          >
            →
          </button>
          <button
            className="folder-picker-up"
            onClick={goUp}
            disabled={!parent}
            title={t("parentDir", "上一级")}
          >
            ↑
          </button>
          <div className="folder-picker-breadcrumb">
            {current ? (
              breadcrumb.length > 0 ? (
                breadcrumb.map((part, i) => (
                  <span key={i} className="folder-picker-crumb">
                    {part}
                    {i < breadcrumb.length - 1 && <span className="folder-picker-sep">›</span>}
                  </span>
                ))
              ) : (
                <span className="folder-picker-crumb">{current}</span>
              )
            ) : (
              <span className="folder-picker-crumb muted">{DRIVE_PLACEHOLDER}</span>
            )}
          </div>
        </div>

        <div className="folder-picker-path-input-wrap">
          <input
            className="folder-picker-path-input"
            type="text"
            value={pathInput}
            onChange={(e) => setPathInput(e.target.value)}
            onKeyDown={handlePathInputKeyDown}
            placeholder={t("folderPathPlaceholder", "例如 E:\\project\\agent 或 /home/user/project")}
          />
          <button
            type="button"
            className="folder-picker-fav-add"
            onClick={addToFavorites}
            disabled={!current && !pathInput.trim()}
            title={t("addToFavorites", "添加到收藏")}
          >
            ⭐
          </button>
        </div>

        {favoriteItems.length > 0 && (
          <div className="folder-picker-favorites">
            <span className="folder-picker-fav-label" title={t("favorites", "收藏")}>⭐</span>
            <div className="folder-picker-fav-list">
              {favoriteItems.map((item) => (
                <div key={item.path} className="folder-picker-fav-chip" title={item.path}>
                  <button
                    type="button"
                    className="folder-picker-fav-chip-btn"
                    onClick={() => navigateTo(item.path)}
                  >
                    📁 {item.name}
                  </button>
                  {item.removable && (
                    <button
                      type="button"
                      className="folder-picker-fav-remove"
                      onClick={() => removeFromFavorites(item.path)}
                      title={t("removeFavorite", "移除收藏")}
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="folder-picker-list">
          {loading && <div className="folder-picker-loading">{t("loading", "加载中…")}</div>}
          {!loading && dirs.length === 0 && (
            <div className="folder-picker-empty">{t("noSubdirs", "没有子文件夹")}</div>
          )}
          {!loading && dirs.map((dir) => (
            <button
              key={dir.path}
              className="folder-picker-item"
              onDoubleClick={() => enterDir(dir)}
              onClick={() => setPathInput(dir.path)}
            >
              <span className="folder-picker-item-icon">📁</span>
              <span className="folder-picker-item-name">{dir.name}</span>
            </button>
          ))}
        </div>

        <div className="folder-picker-footer">
          <span className="folder-picker-hint">
            {t("folderPickerHint", "双击进入文件夹，单击选中路径")}
          </span>
          <div className="folder-picker-actions">
            <button className="folder-picker-btn cancel" onClick={onCancel}>
              {t("cancel", "取消")}
            </button>
            <button
              className="folder-picker-btn confirm"
              onClick={handleConfirm}
              disabled={!current && !pathInput.trim()}
            >
              {t("selectFolder", "选择文件夹")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
