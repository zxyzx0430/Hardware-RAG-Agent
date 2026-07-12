const KEYS = {
  settings: "hwrag_settings",
  sessions: "hwrag_sessions",
  messages: "hwrag_session_messages",
  bookmarks: "hwrag_bookmarks",
  bookmarkFolders: "hwrag_bookmark_folders",
  bookmarkData: "hwrag_bookmark_data",
  activeSession: "hwrag_active_session",
} as const;

const MSG_PREFIX = "hwrag_msg_";

export function loadFromStorage<T>(key: keyof typeof KEYS, fallback: T): T {
  try {
    const raw = localStorage.getItem(KEYS[key]);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function saveToStorage<T>(key: keyof typeof KEYS, data: T): void {
  try {
    localStorage.setItem(KEYS[key], JSON.stringify(data));
  } catch (e) {
    if (e instanceof DOMException && (e.name === "QuotaExceededError" || e.code === 22)) {
      console.error(`[persistence] 存储空间不足: ${key}`);
      const event = new CustomEvent("storage-quota-exceeded", { detail: { key } });
      window.dispatchEvent(event);
    }
  }
}

export function removeFromStorage(key: keyof typeof KEYS): void {
  try {
    localStorage.removeItem(KEYS[key]);
  } catch {
    // ignore
  }
}

/** 按 session 分片存储消息 */
export function saveSessionMessages(sessionId: string, msgs: unknown): void {
  try {
    localStorage.setItem(`${MSG_PREFIX}${sessionId}`, JSON.stringify(msgs));
  } catch (e) {
    if (e instanceof DOMException && (e.name === "QuotaExceededError" || e.code === 22)) {
      console.error(`[persistence] 存储空间不足: msg_${sessionId}`);
      const event = new CustomEvent("storage-quota-exceeded", { detail: { key: `msg_${sessionId}` } });
      window.dispatchEvent(event);
    }
  }
}

/** 按 session 加载消息 */
export function loadSessionMessages<T>(sessionId: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`${MSG_PREFIX}${sessionId}`);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

/** 删除某个 session 的消息分片 */
export function removeSessionMessages(sessionId: string): void {
  try {
    localStorage.removeItem(`${MSG_PREFIX}${sessionId}`);
  } catch {
    // ignore
  }
}

/** 从旧格式 (hwrag_session_messages) 迁移到分片格式 */
export function migrateMessagesToShards(sessionMessages: Record<string, unknown>): void {
  Object.entries(sessionMessages).forEach(([sid, msgs]) => {
    saveSessionMessages(sid, msgs);
  });
  // 迁移完成后删除旧 key
  try {
    localStorage.removeItem(KEYS.messages);
  } catch {
    // ignore
  }
}
