import { useMemo, useState } from "react";
import { useChatStore } from "../../stores/useChatStore";
import { useBookmarkStore } from "../../stores/useBookmarkStore";
import { useAppStore } from "../../stores/useAppStore";
import { useI18n } from "../../i18n";
import { useToastStore } from "../../stores/useToastStore";
import "../../styles/bookmarks.css";

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
const WEEK_MS = 7 * DAY_MS;
const ALL_FOLDERS = "all";

const ICONS = {
  folder: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
  bookmark: "M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z",
  search: "M21 21l-4.35-4.35M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z",
  plus: "M12 5v14M5 12h14",
  chevron: "M9 18l6-6-6-6",
  trash: "M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
  pencil: "M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z",
  close: "M18 6 6 18M6 6l12 12",
  check: "M20 6 9 17l-5-5",
} as const;

type Folder = { id: string; name: string };

function Icon({ path, size = 14 }: { path: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={path} />
    </svg>
  );
}

function formatBookmarkTime(ts: number, t: (key: string, fallback?: string) => string) {
  const diff = Date.now() - ts;
  if (diff < MINUTE_MS) return t('bookmarkTime');
  if (diff < HOUR_MS) return `${Math.floor(diff / MINUTE_MS)}${t('minutesAgo')}`;
  if (diff < DAY_MS) return `${Math.floor(diff / HOUR_MS)}${t('hoursAgo')}`;
  if (diff < WEEK_MS) return `${Math.floor(diff / DAY_MS)}${t('daysAgo')}`;
  const d = new Date(ts);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="bm-empty">
      <Icon path={ICONS.bookmark} size={28} />
      <div className="bm-empty-title">{title}</div>
      <div className="bm-empty-hint">{hint}</div>
    </div>
  );
}

type FolderSelectDialogProps = {
  title: string;
  folders: Folder[];
  onSelect: (folderId: string) => void;
  onClose: () => void;
  onCreateFolder: (name: string) => string;
};

function FolderSelectDialog({ title, folders, onSelect, onClose, onCreateFolder }: FolderSelectDialogProps) {
  const { t } = useI18n();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const confirmCreate = () => {
    const trimmed = name.trim();
    if (!trimmed) {
      useToastStore.getState().showWarning(t('folderNameRequired', '请输入收藏夹名称'));
      return;
    }
    onSelect(onCreateFolder(trimmed));
  };

  return (
    <div className="bm-dialog-mask" onClick={onClose}>
      <div className="bm-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="bm-dialog-head">
          <h3>{title}</h3>
          {!creating && (
            <button className="bm-btn bm-btn-small" onClick={() => setCreating(true)}>
              <Icon path={ICONS.plus} size={11} />
              {t('newBtn')}
            </button>
          )}
        </div>
        {creating && (
          <div className="bm-dialog-create">
            <input
              className="bm-input"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") confirmCreate();
                if (e.key === "Escape") { setCreating(false); setName(""); }
              }}
              placeholder={t('folderNamePlaceholder')}
              aria-label={t('folderNamePlaceholder')}
            />
            <button className="bm-icon-btn" title={t('determineBtn')} aria-label={t('determineBtn')} onClick={confirmCreate}>
              <Icon path={ICONS.check} size={13} />
            </button>
            <button className="bm-icon-btn" title={t('cancel')} aria-label={t('cancel')} onClick={() => { setCreating(false); setName(""); }}>
              <Icon path={ICONS.close} size={13} />
            </button>
          </div>
        )}
        <div className="bm-dialog-list">
          {folders.map((f) => (
            <button key={f.id} className="bm-dialog-folder" onClick={() => onSelect(f.id)}>
              <Icon path={ICONS.folder} size={14} />
              <span>{f.name}</span>
            </button>
          ))}
        </div>
        <div className="bm-dialog-foot">
          <button className="bm-btn" onClick={onClose}>{t('cancel')}</button>
        </div>
      </div>
    </div>
  );
}

export function BookmarkPanel() {
  const { t } = useI18n();
  const folders = useBookmarkStore((s) => s.bookmarkFolders);
  const bookmarkData = useBookmarkStore((s) => s.bookmarkData);
  const bookmarkTargetMsgId = useBookmarkStore((s) => s.bookmarkTargetMsgId);
  const setBookmarkTargetMsgId = useBookmarkStore((s) => s.setBookmarkTargetMsgId);
  const addBookmarkFolder = useBookmarkStore((s) => s.addBookmarkFolder);
  const addBookmarkToFolder = useBookmarkStore((s) => s.addBookmarkToFolder);
  const moveBookmarkToFolder = useBookmarkStore((s) => s.moveBookmarkToFolder);
  const removeBookmark = useBookmarkStore((s) => s.removeBookmark);
  const renameBookmarkFolder = useBookmarkStore((s) => s.renameBookmarkFolder);
  const deleteBookmarkFolder = useBookmarkStore((s) => s.deleteBookmarkFolder);

  const [selected, setSelected] = useState<string>(ALL_FOLDERS);
  const [query, setQuery] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [moveTargetMsgId, setMoveTargetMsgId] = useState<string | null>(null);

  const activeFolderId = selected !== ALL_FOLDERS && folders.some((f) => f.id === selected) ? selected : ALL_FOLDERS;
  const folderNames = useMemo(() => new Map(folders.map((f) => [f.id, f.name])), [folders]);
  const counts = useMemo(() => {
    const next = new Map<string, number>();
    for (const entry of Object.values(bookmarkData)) next.set(entry.folderId, (next.get(entry.folderId) ?? 0) + 1);
    return next;
  }, [bookmarkData]);
  const total = Object.keys(bookmarkData).length;
  const normalizedQuery = query.trim().toLowerCase();
  const visible = useMemo(() => Object.entries(bookmarkData)
    .map(([id, entry]) => ({ id, ...entry, folderName: folderNames.get(entry.folderId) ?? "" }))
    .filter((entry) => {
      if (activeFolderId !== ALL_FOLDERS && entry.folderId !== activeFolderId) return false;
      if (!normalizedQuery) return true;
      return [entry.sessionTitle, entry.content, entry.folderName]
        .some((value) => value.toLowerCase().includes(normalizedQuery));
    })
    .sort((a, b) => b.bookmarkedAt - a.bookmarkedAt),
  [bookmarkData, folderNames, activeFolderId, normalizedQuery]);

  const activeFolderName = folders.find((f) => f.id === activeFolderId)?.name;
  const title = activeFolderId === ALL_FOLDERS ? t('allBookmarks', '全部收藏') : activeFolderName ?? t('bookmarks');
  const countLabel = t('bookmarkTotal', '共 {n} 条收藏').replace('{n}', String(visible.length));
  const subtitle = normalizedQuery ? `${countLabel} · 「${query.trim()}」` : countLabel;
  const emptyState = total === 0
    ? { title: t('noBookmarks', '还没有收藏'), hint: t('bookmarkHintShort') }
    : normalizedQuery
      ? { title: t('noMatchBookmarks', '没有匹配的收藏'), hint: t('noMatchBookmarksHint', '换个关键词试试') }
      : { title: t('bmEmptyFolder', '这个收藏夹还是空的'), hint: t('bmEmptyFolderHint', '在聊天里收藏消息后，可移动到该收藏夹') };

  const startCreateFolder = () => { setNewFolderName(""); setCreatingFolder(true); };
  const confirmCreateFolder = () => {
    const name = newFolderName.trim();
    if (!name) {
      useToastStore.getState().showWarning(t('folderNameRequired', '请输入收藏夹名称'));
      return;
    }
    setSelected(addBookmarkFolder(name));
    setCreatingFolder(false);
    setNewFolderName("");
  };
  const startRename = (folder: Folder) => { setRenamingId(folder.id); setRenameValue(folder.name); };
  const confirmRename = () => {
    if (renamingId && renameValue.trim()) renameBookmarkFolder(renamingId, renameValue.trim());
    setRenamingId(null);
    setRenameValue("");
  };
  const handleFolderSelect = (folderId: string) => {
    if (bookmarkTargetMsgId) addBookmarkToFolder(bookmarkTargetMsgId, folderId);
    if (moveTargetMsgId) moveBookmarkToFolder(moveTargetMsgId, folderId);
    setSelected(folderId);
    setBookmarkTargetMsgId(null);
    setMoveTargetMsgId(null);
  };
  const openBookmark = (bookmarkId: string, sessionId: string) => {
    useAppStore.getState().setActiveNav("chat");
    useChatStore.getState().setActiveSession(sessionId);
    setTimeout(() => document.getElementById(`msg-${bookmarkId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 100);
  };

  return (
    <div className="content-page bm-page">
      <div className="bm-layout">
        <aside className="bm-rail" aria-label={t('bookmarks')}>
          <div className="bm-rail-head">
            <span className="bm-rail-label">{t('folders', '文件夹')}</span>
          </div>
          <div className="bm-rail-list">
            <div className={`bm-rail-row${activeFolderId === ALL_FOLDERS ? " active" : ""}`}>
              <button
                className="bm-rail-item"
                onClick={() => setSelected(ALL_FOLDERS)}
                aria-current={activeFolderId === ALL_FOLDERS ? "true" : undefined}
              >
                <Icon path={ICONS.bookmark} size={15} />
                <span className="bm-rail-name">{t('allBookmarks', '全部收藏')}</span>
                <span className="bm-rail-count">{total}</span>
              </button>
            </div>
            <div className="bm-rail-divider" />
            {folders.map((folder) => {
              const active = folder.id === activeFolderId;
              const isRenaming = folder.id === renamingId;
              return (
                <div key={folder.id} className={`bm-rail-row${active ? " active" : ""}`}>
                  {isRenaming ? (
                    <input
                      className="bm-rail-rename"
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") confirmRename();
                        if (e.key === "Escape") { setRenamingId(null); setRenameValue(""); }
                      }}
                      onBlur={confirmRename}
                      aria-label={t('rename')}
                    />
                  ) : (
                    <button
                      className="bm-rail-item"
                      onClick={() => setSelected(folder.id)}
                      title={folder.name}
                      aria-current={active ? "true" : undefined}
                    >
                      <Icon path={ICONS.folder} size={15} />
                      <span className="bm-rail-name">{folder.name}</span>
                      <span className="bm-rail-count">{counts.get(folder.id) ?? 0}</span>
                    </button>
                  )}
                  {folder.id !== "default" && !isRenaming && (
                    <span className="bm-rail-actions">
                      <button className="bm-icon-btn" title={t('rename')} aria-label={`${t('rename')} ${folder.name}`} onClick={() => startRename(folder)}>
                        <Icon path={ICONS.pencil} size={11} />
                      </button>
                      <button className="bm-icon-btn" title={t('deleteFolder')} aria-label={`${t('deleteFolder')} ${folder.name}`} onClick={() => deleteBookmarkFolder(folder.id)}>
                        <Icon path={ICONS.close} size={11} />
                      </button>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <div className="bm-rail-foot">
            {creatingFolder ? (
              <div className="bm-rail-create">
                <input
                  className="bm-input"
                  autoFocus
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") confirmCreateFolder();
                    if (e.key === "Escape") { setCreatingFolder(false); setNewFolderName(""); }
                  }}
                  placeholder={t('folderNamePlaceholder')}
                  aria-label={t('folderNamePlaceholder')}
                />
                <button className="bm-icon-btn" title={t('determineBtn')} aria-label={t('determineBtn')} onClick={confirmCreateFolder}>
                  <Icon path={ICONS.check} size={13} />
                </button>
                <button className="bm-icon-btn" title={t('cancel')} aria-label={t('cancel')} onClick={() => { setCreatingFolder(false); setNewFolderName(""); }}>
                  <Icon path={ICONS.close} size={13} />
                </button>
              </div>
            ) : (
              <button className="bm-new-folder" onClick={startCreateFolder}>
                <Icon path={ICONS.plus} size={12} />
                {t('bmNewFolder', '新建收藏夹')}
              </button>
            )}
          </div>
        </aside>

        <main className="bm-main">
          <header className="bm-head">
            <div className="bm-head-text">
              <h1 className="bm-title" title={title}>{title}</h1>
              <p className="bm-sub">{subtitle}</p>
            </div>
            <div className="bm-head-actions">
              <div className="bm-search">
                <span className="bm-search-icon"><Icon path={ICONS.search} size={13} /></span>
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t('searchBookmarks', '搜索收藏内容…')}
                  aria-label={t('searchBookmarks', '搜索收藏内容…')}
                />
                {query && (
                  <button className="bm-search-clear" title={t('clearSearch', '清除搜索')} aria-label={t('clearSearch', '清除搜索')} onClick={() => setQuery("")}>
                    <Icon path={ICONS.close} size={11} />
                  </button>
                )}
              </div>
              <button className="bm-btn bm-btn-primary" onClick={startCreateFolder}>
                <Icon path={ICONS.plus} size={12} />
                {t('bmNewFolder', '新建收藏夹')}
              </button>
            </div>
          </header>

          <div className="bm-scroll">
            {visible.length === 0 ? (
              <EmptyState title={emptyState.title} hint={emptyState.hint} />
            ) : (
              <div className="bm-grid">
                {visible.map((entry) => (
                  <article className="bm-card" key={entry.id} data-bookmark-id={entry.id}>
                    <div className="bm-card-top">
                      <span className="bm-card-title" title={entry.sessionTitle}>{entry.sessionTitle}</span>
                      <span className="bm-role">{entry.role === "user" ? t('userLabel') : t('assistantLabel', '助手')}</span>
                    </div>
                    <p className="bm-preview">{entry.content || t('noPreview', '暂无预览内容')}</p>
                    <div className="bm-card-foot">
                      <span className="bm-folder" title={entry.folderName}>
                        <Icon path={ICONS.folder} size={11} />
                        <span>{entry.folderName || t('unfiled', '未分类')}</span>
                      </span>
                      <span className="bm-time">{formatBookmarkTime(entry.bookmarkedAt, t)}</span>
                      <span className="bm-card-actions">
                        <button className="bm-icon-btn" title={t('open')} aria-label={t('open')} onClick={() => openBookmark(entry.id, entry.sessionId)}>
                          <Icon path={ICONS.chevron} size={12} />
                        </button>
                        <button className="bm-icon-btn" title={t('moveTo')} aria-label={t('moveTo')} onClick={() => setMoveTargetMsgId(entry.id)}>
                          <Icon path={ICONS.folder} size={12} />
                        </button>
                        <button className="bm-icon-btn bm-danger" title={t('delete')} aria-label={t('delete')} onClick={() => removeBookmark(entry.id)}>
                          <Icon path={ICONS.trash} size={12} />
                        </button>
                      </span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>

      {bookmarkTargetMsgId && (
        <FolderSelectDialog
          title={t('selectFolderTitle')}
          folders={folders}
          onSelect={handleFolderSelect}
          onClose={() => setBookmarkTargetMsgId(null)}
          onCreateFolder={addBookmarkFolder}
        />
      )}
      {moveTargetMsgId && (
        <FolderSelectDialog
          title={t('moveToFolder')}
          folders={folders}
          onSelect={handleFolderSelect}
          onClose={() => setMoveTargetMsgId(null)}
          onCreateFolder={addBookmarkFolder}
        />
      )}
    </div>
  );
}
