import { useState } from "react";
import { useChatStore } from "../../stores/useChatStore";
import { useAppStore } from "../../stores/useAppStore";
import { useI18n } from "../../i18n";

function renderContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part: any) => {
      if (typeof part === "string") return part;
      if (part.type === "text") return part.text || "";
      if (part.type === "image_url") return "![Image](" + part.image_url.url + ")";
      return "";
    }).join("\n\n");
  }
  return String(content ?? "");
}


function formatBookmarkTime(ts: number, t: (key: string) => string) {
  const diff = Date.now() - ts;
  if (diff < 60000) return t('bookmarkTime');
  if (diff < 3600000) return `${Math.floor(diff / 60000)}${t('minutesAgo')}`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}${t('hoursAgo')}`;
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}${t('daysAgo')}`;
  const d = new Date(ts);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/* ---------- Folder selection dialog ---------- */
function FolderSelectDialog({
  title,
  folders,
  onSelect,
  onClose,
}: {
  title: string;
  folders: { id: string; name: string }[];
  onSelect: (folderId: string) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 9999,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "rgba(0,0,0,0.35)",
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--card-bg, #fff)", borderRadius: 8,
          minWidth: 260, maxWidth: 340, padding: 16,
          boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12 }}>{title}</div>
        {folders.map((f) => (
          <button
            key={f.id}
            onClick={() => onSelect(f.id)}
            style={{
              display: "flex", alignItems: "center", gap: 8, width: "100%",
              padding: "8px 10px", border: "none", borderRadius: 4,
              background: "transparent", cursor: "pointer", fontSize: 12,
              color: "inherit", textAlign: "left",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover-bg, rgba(0,0,0,0.06))")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: "var(--muted-fg)", flexShrink: 0 }}>
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            {f.name}
          </button>
        ))}
        <button
          onClick={onClose}
          style={{
            marginTop: 8, width: "100%", padding: "6px 0", border: "1px solid var(--border)",
            borderRadius: 4, background: "transparent", cursor: "pointer",
            fontSize: 11, color: "inherit",
          }}
        >
          {t('cancel')}
        </button>
      </div>
    </div>
  );
}

/* ---------- Main panel ---------- */
export function BookmarkPanel() {
  const { t } = useI18n();
  const bookmarkFolders = useChatStore((s) => s.bookmarkFolders);
  const bookmarkData = useChatStore((s) => s.bookmarkData);
  const bookmarkTargetMsgId = useChatStore((s) => s.bookmarkTargetMsgId);
  const removeBookmark = useChatStore((s) => s.removeBookmark);
  const addBookmarkFolder = useChatStore((s) => s.addBookmarkFolder);
  const deleteBookmarkFolder = useChatStore((s) => s.deleteBookmarkFolder);
  const setBookmarkTargetMsgId = useChatStore((s) => s.setBookmarkTargetMsgId);
  const addBookmarkToFolder = useChatStore((s) => s.addBookmarkToFolder);
  const moveBookmarkToFolder = useChatStore((s) => s.moveBookmarkToFolder);
  const renameBookmarkFolder = useChatStore((s) => s.renameBookmarkFolder);

  const [newFolderMode, setNewFolderMode] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [moveTargetMsgId, setMoveTargetMsgId] = useState<string | null>(null);
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const allBookmarkIds = Object.keys(bookmarkData);

  const handleNavigate = (bookmarkId: string, sessionId?: string) => {
    useAppStore.getState().setActiveNav("chat");
    if (sessionId) {
      useChatStore.getState().setActiveSession(sessionId);
      useAppStore.getState().setActiveSession(sessionId);
    }
    setTimeout(() => {
      document.getElementById("msg-" + bookmarkId)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 100);
  };

  const handleDelete = (msgId: string) => {
    removeBookmark(msgId);
  };

  const handleNewFolder = () => {
    const name = newFolderName.trim();
    if (!name) return;
    addBookmarkFolder(name);
    setNewFolderName("");
    setNewFolderMode(false);
  };

  const handleDeleteFolder = (folderId: string) => {
    deleteBookmarkFolder(folderId);
  };

  const handleStartRename = (folderId: string, currentName: string) => {
    setRenamingFolderId(folderId);
    setRenameValue(currentName);
  };

  const handleConfirmRename = () => {
    if (renamingFolderId && renameValue.trim()) {
      renameBookmarkFolder(renamingFolderId, renameValue.trim());
    }
    setRenamingFolderId(null);
    setRenameValue("");
  };

  const handleFolderSelectForBookmark = (folderId: string) => {
    if (bookmarkTargetMsgId) {
      addBookmarkToFolder(bookmarkTargetMsgId, folderId);
    }
  };

  const handleFolderSelectForMove = (folderId: string) => {
    if (moveTargetMsgId) {
      moveBookmarkToFolder(moveTargetMsgId, folderId);
    }
    setMoveTargetMsgId(null);
  };

  if (allBookmarkIds.length === 0 && !bookmarkTargetMsgId) {
    return (
      <div className="content-page bookmark-page">
        <div className="content-page-header">
          <div className="content-page-title">{t('bookmarks')}</div>
        </div>
        <div className="content-page-scroll bookmark-scroll-page">
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: 200, opacity: 0.5, fontSize: 13, gap: 8 }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" /></svg>
            <span>{t('noBookmarkContent')}</span>
            <span style={{ fontSize: 11 }}>{t('bookmarkHintShort')}</span>
          </div>
        </div>

        {/* Folder selection dialog for new bookmark */}
        {bookmarkTargetMsgId && (
          <FolderSelectDialog
            title={t('selectFolderTitle')}
            folders={bookmarkFolders}
            onSelect={handleFolderSelectForBookmark}
            onClose={() => setBookmarkTargetMsgId(null)}
          />
        )}
      </div>
    );
  }

  return (
    <div className="content-page bookmark-page">
      <div className="content-page-header">
        <div className="content-page-title">{t('bookmarks')}</div>
        <button className="btn-new" onClick={() => setNewFolderMode(true)}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
          {t('newBtn')}
        </button>
      </div>

      {newFolderMode && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", borderBottom: "1px solid var(--border)" }}>
          <input
            type="text"
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleNewFolder(); if (e.key === "Escape") { setNewFolderMode(false); setNewFolderName(""); } }}
            placeholder={t('folderNamePlaceholder')}
            autoFocus
            style={{ flex: 1, background: "var(--input-bg, #fff)", border: "1px solid var(--border)", borderRadius: 4, padding: "4px 8px", fontSize: 12, color: "inherit", outline: "none" }}
          />
          <button onClick={handleNewFolder} style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4, border: "none", background: "var(--accent)", color: "#fff", cursor: "pointer" }}>{t('determineBtn')}</button>
          <button onClick={() => { setNewFolderMode(false); setNewFolderName(""); }} style={{ fontSize: 11, padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "transparent", color: "inherit", cursor: "pointer" }}>{t('cancel')}</button>
        </div>
      )}

      <div className="content-page-scroll bookmark-scroll-page">
        {bookmarkFolders.map((folder) => {
          const folderItems = allBookmarkIds
            .filter((id) => bookmarkData[id].folderId === folder.id)
            .map((id) => ({ id, ...bookmarkData[id] }));
          const isRenaming = renamingFolderId === folder.id;
          return (
            <div className="bookmark-folder" key={folder.id}>
              <div className="bookmark-folder-header">
                <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: "var(--muted-fg)", flexShrink: 0 }}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
                  {isRenaming ? (
                    <input
                      type="text"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") handleConfirmRename(); if (e.key === "Escape") { setRenamingFolderId(null); setRenameValue(""); } }}
                      onBlur={handleConfirmRename}
                      autoFocus
                      style={{
                        flex: 1, background: "var(--input-bg, #fff)", border: "1px solid var(--border)",
                        borderRadius: 3, padding: "1px 4px", fontSize: 12, color: "inherit", outline: "none",
                      }}
                    />
                  ) : (
                    <span className="bookmark-folder-name">{folder.name}</span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="bookmark-folder-count">{folderItems.length}</span>
                  {folder.id !== "default" && !isRenaming && (
                    <>
                      <button
                        onClick={() => handleStartRename(folder.id, folder.name)}
                        title={t('rename')}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted-fg)", fontSize: 13, lineHeight: 1, padding: "0 2px", display: "inline-flex", alignItems: "center" }}
                      >
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" /></svg>
                      </button>
                      <button
                        onClick={() => handleDeleteFolder(folder.id)}
                        title={t('deleteFolder')}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted-fg)", fontSize: 13, lineHeight: 1, padding: "0 2px" }}
                      >
                        &times;
                      </button>
                    </>
                  )}
                </div>
              </div>
              {!folderItems.length ? (
                <div className="bookmark-empty" style={{ padding: 12, fontSize: 11, opacity: 0.5 }}>{t('noCollection')}</div>
              ) : folderItems.map((item) => {
                const preview = `${item.role === "user" ? "👤" : "🤖"} ${renderContent(item.content).slice(0, 80)}${renderContent(item.content).length > 80 ? "…" : ""}`;
                return (
                  <div className="bookmark-item" key={item.id}>
                    <div className="bookmark-item-title">{item.sessionTitle}</div>
                    <div className="bookmark-item-meta">{preview}</div>
                    <div className="bookmark-item-footer">
                      <span className="bookmark-item-time">{formatBookmarkTime(item.bookmarkedAt, t)}</span>
                      <span className="bookmark-item-actions">
                        <button className="bookmark-item-action-btn" title={t('open')} onClick={() => handleNavigate(item.id, item.sessionId)}>
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="9 18 15 12 9 6" /></svg>
                        </button>
                        <button className="bookmark-item-action-btn" title={t('moveTo')} onClick={() => setMoveTargetMsgId(item.id)}>
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>
                        </button>
                        <button className="bookmark-item-action-btn" title={t('delete')} onClick={() => handleDelete(item.id)}>
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                        </button>
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Folder selection dialog for new bookmark (bookmarkTargetMsgId) */}
      {bookmarkTargetMsgId && (
        <FolderSelectDialog
          title={t('selectFolderTitle')}
          folders={bookmarkFolders}
          onSelect={handleFolderSelectForBookmark}
          onClose={() => setBookmarkTargetMsgId(null)}
        />
      )}

      {/* Folder selection dialog for move */}
      {moveTargetMsgId && (
        <FolderSelectDialog
          title={t('moveToFolder')}
          folders={bookmarkFolders}
          onSelect={handleFolderSelectForMove}
          onClose={() => setMoveTargetMsgId(null)}
        />
      )}
    </div>
  );
}
