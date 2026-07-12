/**
 * Bookmark Store — 收藏夹状态管理
 *
 * 从 useChatStore 抽离，消除 useChatStore 的 9 关注点混合。
 * toggleBookmark/addBookmarkToFolder 需访问消息列表，通过 useChatStore.getState() 延迟访问
 * 避免模块级循环依赖。
 */
import { create } from "zustand";
import { loadFromStorage, saveToStorage } from "../utils/persistence";
import { useChatStore } from "./useChatStore";
import { useLogStore } from "./useLogStore";
import { useSessionStore } from "./useSessionStore";

interface BookmarkEntry {
  folderId: string;
  bookmarkedAt: number;
  sessionId: string;
  sessionTitle: string;
  content: string;
  role: string;
}

interface BookmarkState {
  bookmarks: string[];
  bookmarkFolders: { id: string; name: string; createdAt: number }[];
  bookmarkData: Record<string, BookmarkEntry>;
  bookmarkTargetMsgId: string | null;

  toggleBookmark: (msgId: string, folderId?: string) => void;
  removeBookmark: (msgId: string) => void;
  isBookmarked: (msgId: string) => boolean;
  addBookmarkFolder: (name: string) => string;
  deleteBookmarkFolder: (folderId: string) => void;
  setBookmarkTargetMsgId: (id: string | null) => void;
  addBookmarkToFolder: (msgId: string, folderId: string) => void;
  moveBookmarkToFolder: (bookmarkId: string, folderId: string) => void;
  renameBookmarkFolder: (folderId: string, name: string) => void;
}

function getLog() {
  return useLogStore.getState().log;
}

/** 从消息内容中提取前 100 字符作为书签预览 */
function extractPreview(content: unknown): string {
  if (typeof content === "string") return content.slice(0, 100);
  return "";
}

/** 按 sessionId 查找会话标题；找不到或为空时 fallback 为 "未命名对话" */
function resolveSessionTitle(sessionId: string): string {
  const session = useSessionStore
    .getState()
    .sessions.find((s) => s.id === sessionId);
  return session?.title?.trim() || "未命名对话";
}

const DEFAULT_FOLDER = { id: "default", name: "默认收藏夹", createdAt: Date.now() };

/** 加载收藏夹列表；localStorage 存了空数组时 fallback 不触发，此处兜底补 default */
function loadBookmarkFolders() {
  const loaded = loadFromStorage("bookmarkFolders", [DEFAULT_FOLDER]);
  if (loaded.length === 0) {
    saveToStorage("bookmarkFolders", [DEFAULT_FOLDER]);
    return [DEFAULT_FOLDER];
  }
  return loaded;
}

export const useBookmarkStore = create<BookmarkState>((set, get) => ({
  bookmarks: loadFromStorage("bookmarks", [] as string[]),
  bookmarkFolders: loadBookmarkFolders(),
  bookmarkData: loadFromStorage("bookmarkData", {}),
  bookmarkTargetMsgId: null,

  toggleBookmark: (msgId, folderId) => {
    const { bookmarkData, bookmarkFolders } = get();
    if (bookmarkData[msgId]) {
      const next = { ...bookmarkData };
      delete next[msgId];
      const nextBookmarks = Object.keys(next);
      saveToStorage("bookmarkData", next);
      saveToStorage("bookmarks", nextBookmarks);
      set({ bookmarkData: next, bookmarks: nextBookmarks });
      getLog()("info", "bookmark", `移除书签: ${msgId}`);
    } else {
      let fid = folderId || bookmarkFolders[0]?.id;
      if (!fid) {
        // 收藏夹为空时兜底创建 default，避免书签落入不存在的夹
        fid = "default";
        const folders = [DEFAULT_FOLDER];
        saveToStorage("bookmarkFolders", folders);
        set({ bookmarkFolders: folders });
      }
      const chatStore = useChatStore.getState();
      const msg = chatStore.messages.find((m) => m.id === msgId);
      const newEntry: BookmarkEntry = {
        folderId: fid,
        bookmarkedAt: Date.now(),
        sessionId: chatStore.activeSessionId,
        sessionTitle: resolveSessionTitle(chatStore.activeSessionId),
        content: msg ? extractPreview(msg.content) : "",
        role: msg ? msg.role : "assistant",
      };
      const next = { ...bookmarkData, [msgId]: newEntry };
      const nextBookmarks = Object.keys(next);
      saveToStorage("bookmarkData", next);
      saveToStorage("bookmarks", nextBookmarks);
      set({ bookmarkData: next, bookmarks: nextBookmarks });
      getLog()("info", "bookmark", `添加书签: ${msgId} → folder=${fid}`);
    }
  },

  removeBookmark: (msgId) => {
    const { bookmarkData } = get();
    const next = { ...bookmarkData };
    delete next[msgId];
    const nextBookmarks = Object.keys(next);
    saveToStorage("bookmarkData", next);
    saveToStorage("bookmarks", nextBookmarks);
    set({ bookmarkData: next, bookmarks: nextBookmarks });
  },

  isBookmarked: (msgId) => !!get().bookmarkData[msgId],

  addBookmarkFolder: (name) => {
    const id = `folder-${Date.now()}`;
    const folder = { id, name, createdAt: Date.now() };
    const next = [...get().bookmarkFolders, folder];
    saveToStorage("bookmarkFolders", next);
    set({ bookmarkFolders: next });
    getLog()("info", "bookmark", `新建收藏夹: ${name}`);
    return id;
  },

  deleteBookmarkFolder: (folderId) => {
    if (folderId === "default") return;
    const next = get().bookmarkFolders.filter((f) => f.id !== folderId);
    saveToStorage("bookmarkFolders", next);
    set({ bookmarkFolders: next });
  },

  setBookmarkTargetMsgId: (bookmarkTargetMsgId) => set({ bookmarkTargetMsgId }),

  addBookmarkToFolder: (msgId, folderId) => {
    const { bookmarkData } = get();
    const existing = bookmarkData[msgId];
    if (existing) {
      const next = { ...bookmarkData, [msgId]: { ...existing, folderId } };
      saveToStorage("bookmarkData", next);
      set({ bookmarkData: next });
    } else {
      const chatStore = useChatStore.getState();
      const msg = chatStore.messages.find((m) => m.id === msgId);
      const newEntry: BookmarkEntry = {
        folderId,
        bookmarkedAt: Date.now(),
        sessionId: chatStore.activeSessionId,
        sessionTitle: resolveSessionTitle(chatStore.activeSessionId),
        content: msg ? extractPreview(msg.content) : "",
        role: msg ? msg.role : "assistant",
      };
      const next = { ...bookmarkData, [msgId]: newEntry };
      const nextBookmarks = Object.keys(next);
      saveToStorage("bookmarkData", next);
      saveToStorage("bookmarks", nextBookmarks);
      set({ bookmarkData: next, bookmarks: nextBookmarks });
    }
  },

  moveBookmarkToFolder: (bookmarkId, folderId) => {
    const { bookmarkData } = get();
    const existing = bookmarkData[bookmarkId];
    if (!existing) return;
    const next = { ...bookmarkData, [bookmarkId]: { ...existing, folderId } };
    saveToStorage("bookmarkData", next);
    set({ bookmarkData: next });
  },

  renameBookmarkFolder: (folderId, name) => {
    const next = get().bookmarkFolders.map((f) =>
      f.id === folderId ? { ...f, name } : f,
    );
    saveToStorage("bookmarkFolders", next);
    set({ bookmarkFolders: next });
  },
}));
