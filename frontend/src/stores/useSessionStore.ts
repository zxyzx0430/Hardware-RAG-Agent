import { create } from "zustand";
import type { Session } from "../types/session";
import type { BackendSession } from "../types/api";
import { loadFromStorage, saveToStorage, removeSessionMessages } from "../utils/persistence";
import { post, on } from "../utils/broadcast";
import { useChatStore } from "./useChatStore";
import { useSettingsStore } from "./useSettingsStore";
import { apiGet, apiPost, apiPut, apiPatch, apiDelete } from "../api/client";
import { useLogStore } from "./useLogStore";

const MAX_PINNED = 5;

const DEFAULT_MODEL = "";

/** Context window presets (tokens) — mirror backend CONTEXT_WINDOW_256K / _1M. */
export const CONTEXT_WINDOW_256K = 262144;
export const CONTEXT_WINDOW_1M = 1048576;
const DEFAULT_CONTEXT_WINDOW = CONTEXT_WINDOW_256K;

/** One day in milliseconds — used for session time bucketing. */
const DAY_MS = 24 * 60 * 60 * 1000;

function getLog() {
  return useLogStore.getState().log;
}

// ─── 时间工具 ────────────────────────────────────────────────

/** 根据 createdAt 计算会话分组 */
export function getSessionGroup(createdAt: number): string {
  const now = new Date();
  const created = new Date(createdAt);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - DAY_MS);
  const weekAgo = new Date(today.getTime() - 6 * DAY_MS);

  if (created >= today) return "today";
  if (created >= yesterday) return "yesterday";
  if (created >= weekAgo) return "thisWeek";
  return "earlier";
}

/** 格式化时间戳为显示字符串 */
export function formatSessionTime(createdAt: number): string {
  const now = new Date();
  const created = new Date(createdAt);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - DAY_MS);

  if (created >= today) {
    return created.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  if (created >= yesterday) {
    return "昨天";
  }
  const weekAgo = new Date(today.getTime() - 6 * DAY_MS);
  if (created >= weekAgo) {
    const days = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
    return days[created.getDay()];
  }
  return `${created.getMonth() + 1}月${created.getDate()}日`;
}

/** 格式化创建日期 */
export function formatCreateDate(createdAt: number): string {
  return new Date(createdAt).toISOString().slice(0, 10);
}

/** 会话标题最大长度，超出截断 */
const MAX_TITLE_LENGTH = 50;

/** 清洗会话标题：去除首尾引号/反斜杠/空白，并截断到合理长度 */
function cleanTitle(title: string): string {
  const trimmed = title.trim();
  if (!trimmed) return trimmed;
  const stripped = trimmed.replace(/^["“‘『』\\]+|["“‘『』\\]+$/g, "");
  return stripped.length > MAX_TITLE_LENGTH
    ? stripped.slice(0, MAX_TITLE_LENGTH)
    : stripped;
}

/** 生成带时间后缀的默认会话标题，便于区分多个空会话 */
function getDefaultSessionTitle(): string {
  const time = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return `新对话 · ${time}`;
}

/** 兼容旧数据：将旧字段迁移到 createdAt，并迁移旧的硬编码默认 model */
function migrateSession(s: unknown): Session {
  const base = (s && typeof s === "object" && "createdAt" in s
    ? s
    : { ...(s as Record<string, unknown>), createdAt: (s as Record<string, unknown>)?.createdAt || (s as Record<string, unknown>)?.updatedAt || Date.now() }) as Session;
  // Backfill contextWindow for old sessions that predate the field
  const contextWindow = (base as { contextWindow?: number }).contextWindow ?? DEFAULT_CONTEXT_WINDOW;
  // FIX-2: 迁移旧的硬编码默认 model "gpt-4o-mini" → ""，让 footer fallthrough 到全局 chatModel
  const model = base.model === "gpt-4o-mini" ? "" : base.model;
  return { ...base, contextWindow, model };
}

// ─── Store ───────────────────────────────────────────────────

interface SessionState {
  sessions: Session[];
  activeProject: string;
  searchQuery: string;
  createProjectInputVisible: boolean;
  initialized: boolean;

  // Actions
  initSessions: () => Promise<void>;
  newSession: () => Promise<void>;
  selectSession: (id: string) => void;
  deleteSession: (id: string, confirm?: () => boolean) => boolean;
  pinSession: (id: string) => boolean;
  renameSession: (id: string, title: string) => void;
  moveSessionToProject: (sessionId: string, project: string) => void;
  setActiveProject: (project: string) => void;
  setSearchQuery: (query: string) => void;
  createProject: (name: string) => void;
  deleteProject: (name: string) => void;
  setCreateProjectInputVisible: (visible: boolean) => void;
  /** 更新会话元数据（msgCount, preview, title, branchFromSessionId, branchFromMessageId 等） */
  updateSessionMeta: (sessionId: string, updates: Partial<Pick<Session, "msgCount" | "preview" | "title" | "model" | "branchFromSessionId" | "branchFromMessageId">>) => void;
  /** Update current session's context window budget (tokens) and PATCH backend. */
  setContextWindow: (window: number) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: (() => {
    const raw = loadFromStorage("sessions", [] as Session[]);
    return Array.isArray(raw) ? (Array.isArray(raw) ? raw : []).map((item: unknown) => {
      const s = migrateSession(item);
      if (!s.title) return s;
      const cleaned = cleanTitle(s.title);
      return cleaned === s.title ? s : { ...s, title: cleaned };
    }) : [];
  })(),
  activeProject: "all",
  searchQuery: "",
  createProjectInputVisible: false,
  initialized: false,

  initSessions: async () => {
    if (get().initialized) return;
    try {
      const data = await apiGet<{ sessions: BackendSession[] }>("sessions");
      if (data?.sessions) {
        const sessions: Session[] = data.sessions.map((s) => ({
          id: s.id,
          title: s.title && s.title !== "新对话" ? cleanTitle(s.title) : getDefaultSessionTitle(),
          preview: "",
          // FIX-2: 迁移旧的硬编码默认 model "gpt-4o-mini" → ""，让 footer fallthrough 到全局 chatModel
          model: s.model === "gpt-4o-mini" ? "" : (s.model ?? DEFAULT_MODEL),
          createdAt: s.created_at ? new Date(s.created_at).getTime() : Date.now(),
          project: s.project ?? "",
          pinned: s.pinned ?? false,
          msgCount: s.msg_count ?? 0,
          branchFromSessionId: s.branch_from_session_id ?? undefined,
          branchFromMessageId: s.branch_from_message_id ?? undefined,
          contextWindow: s.context_window ?? DEFAULT_CONTEXT_WINDOW,
        }));
        getLog()("ok", "session", `从后端加载 ${sessions.length} 个会话`);
        set({ sessions, initialized: true });
        saveToStorage("sessions", sessions);
        return;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("401")) {
        useChatStore.setState({ needsApiKey: true });
      }
      getLog()("error", "session", "会话列表加载失败");
    }
    set({ initialized: true });
  },

  newSession: async () => {
    const { chatModel } = useSettingsStore.getState();
    const { activeProject } = get();
    // activeProject 为 "all" 时不指定项目
    const project = activeProject === "all" ? "" : activeProject;
    const sessionModel = chatModel || DEFAULT_MODEL;

    const defaultTitle = getDefaultSessionTitle();

    // 先调用后端创建会话，拿到真实 ID 再创建本地会话
    // 消除 localId/res.id 双 ID 并存窗口，避免 SSE 回调写入幽灵会话
    let sid = "";
    try {
      const res = await apiPost<{ id: string }>("sessions", {
        title: defaultTitle,
        model: sessionModel,
        project,
        context_window: DEFAULT_CONTEXT_WINDOW,
      });
      if (res?.id) {
        sid = res.id;
        getLog()("ok", "session", `会话已创建于后端: ${sid}`);
      } else {
        getLog()("warn", "session", "后端未返回 id，回退到本地会话");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("401")) {
        useChatStore.setState({ needsApiKey: true });
      }
      getLog()("warn", "session", "后端创建失败，回退到本地会话");
    }

    // 后端失败时回退到 localId
    if (!sid) {
      sid = `s${Date.now()}`;
    }

    const session: Session = {
      id: sid,
      title: defaultTitle,
      preview: "",
      model: sessionModel,
      createdAt: Date.now(),
      project,
      pinned: false,
      msgCount: 0,
      contextWindow: DEFAULT_CONTEXT_WINDOW,
    };

    getLog()("info", "session", `新建会话: ${sid}`);

    set((s) => {
      const updated = { sessions: [session, ...s.sessions] };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    // 多 tab 同步：通知其他 tab 会话列表变化
    post('sessions_changed');

    // 切换到新会话
    useChatStore.getState().setActiveSession(sid);
  },

  selectSession: (id) => {
    console.info('[SessionStore] active_change id=%s', id);
    getLog()("info", "session", `选中会话: ${id}`);
    useChatStore.getState().setActiveSession(id);
  },

  deleteSession: (id, confirm) => {
    if (confirm && !confirm()) return false;

    getLog()("info", "session", `删除会话: ${id}`);

    const { sessions: beforeSessions } = get();
    const deletedIndex = beforeSessions.findIndex((x) => x.id === id);
    const deletedSession = deletedIndex >= 0 ? beforeSessions[deletedIndex] : null;

    set((s) => {
      const updated = { sessions: s.sessions.filter((x) => x.id !== id) };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    // 多 tab 同步：通知其他 tab 会话列表变化 + 特定会话被删除
    post('sessions_changed');
    post('session_deleted', id);

    const chatStore = useChatStore.getState();
    // 删除活跃会话时先中止正在进行的 SSE
    if (chatStore.activeSessionId === id && chatStore.isStreaming) {
      chatStore.stopStreaming();
    }
    const newSM = { ...chatStore.sessionMessages };
    delete newSM[id];
    useChatStore.setState({ sessionMessages: newSM });
    removeSessionMessages(id);

    // 清理已删会话的来源查看器状态，避免内存遗留
    chatStore.cleanupSessionSourceState(id);

    if (chatStore.activeSessionId === id) {
      const remaining = get().sessions;
      const nextId = remaining[0]?.id ?? "";
      if (nextId) {
        useChatStore.getState().setActiveSession(nextId);
      } else {
        useChatStore.setState({ messages: [], activeSessionId: "" });
      }
    }

    apiDelete(`sessions/${id}`).catch((err) => {
      if (deletedSession) {
        set((s) => {
          const restored = [...s.sessions];
          restored.splice(deletedIndex, 0, deletedSession);
          saveToStorage("sessions", restored);
          return { sessions: restored };
        });
        // 删除失败回滚：通知其他 tab 会话列表恢复
        post('sessions_changed');
      }
      console.warn('[useSessionStore] deleteSession failed:', err);
    });

    return true;
  },

  pinSession: (id) => {
    const { sessions } = get();
    const target = sessions.find((x) => x.id === id);
    if (!target) return false;
    if (!target.pinned) {
      const pinnedCount = sessions.filter((x) => x.pinned).length;
      if (pinnedCount >= MAX_PINNED) {
        getLog()("warn", "session", `置顶数量已达上限 (${MAX_PINNED})`);
        return false;
      }
    }
    const oldPinned = target.pinned;
    const newPinned = !target.pinned;
    getLog()("info", "session", `${newPinned ? "置顶" : "取消置顶"}: ${id}`);

    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) => x.id === id ? { ...x, pinned: newPinned } : x),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    apiPut(`sessions/${id}`, { pinned: newPinned }).catch((err) => {
      set((s) => {
        const updated = {
          sessions: s.sessions.map((x) => x.id === id ? { ...x, pinned: oldPinned } : x),
        };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });
      console.warn('[useSessionStore] pinSession failed:', err);
    });

    return true;
  },

  renameSession: (id, title) => {
    getLog()("info", "session", `重命名会话 ${id}: ${title}`);
    const oldTitle = get().sessions.find((x) => x.id === id)?.title;
    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) => x.id === id ? { ...x, title } : x),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    // 多 tab 同步：通知其他 tab 会话列表变化
    post('sessions_changed');

    apiPut(`sessions/${id}`, { title }).catch((err) => {
      set((s) => {
        const updated = {
          sessions: s.sessions.map((x) => x.id === id ? { ...x, title: oldTitle } : x),
        };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });
      // 重命名失败回滚：通知其他 tab 会话列表恢复
      post('sessions_changed');
      console.warn('[useSessionStore] renameSession failed:', err);
    });
  },

  moveSessionToProject: (sessionId, project) => {
    getLog()("info", "session", `移动会话 ${sessionId} 到项目: ${project}`);
    const oldProject = get().sessions.find((x) => x.id === sessionId)?.project;
    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) =>
          x.id === sessionId ? { ...x, project } : x
        ),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    // 多 tab 同步：通知其他 tab 会话列表变化
    post('sessions_changed');

    apiPut(`sessions/${sessionId}`, { project }).catch((err) => {
      set((s) => {
        const updated = {
          sessions: s.sessions.map((x) =>
            x.id === sessionId ? { ...x, project: oldProject } : x
          ),
        };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });
      // 移动失败回滚：通知其他 tab 会话列表恢复
      post('sessions_changed');
      console.warn('[useSessionStore] moveSession failed:', err);
    });
  },

  setActiveProject: (activeProject) => set({ activeProject }),

  setSearchQuery: (searchQuery) => set({ searchQuery }),

  createProject: (name) => {
    if (!name.trim()) return;
    getLog()("info", "session", `新建项目: ${name}`);
    const project = name.trim();
    const sessionModel = useSettingsStore.getState().chatModel || DEFAULT_MODEL;
    const defaultTitle = getDefaultSessionTitle();
    // 调后端持久化会话，避免刷新丢失；失败回退本地 id
    // 不切换 activeSession，保留 SessionPanel "创建后移动当前会话" 的 50ms 约定
    void (async () => {
      let sid = `s${Date.now()}`;
      try {
        const res = await apiPost<{ id: string }>("sessions", {
          title: defaultTitle,
          model: sessionModel,
          project,
          context_window: DEFAULT_CONTEXT_WINDOW,
        });
        if (res?.id) {
          sid = res.id;
          getLog()("ok", "session", `项目会话已创建于后端: ${sid}`);
        } else {
          getLog()("warn", "session", "后端未返回 id，回退本地会话");
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("401")) {
          useChatStore.setState({ needsApiKey: true });
        }
        getLog()("warn", "session", "项目会话后端创建失败，回退本地会话");
      }
      set((s) => {
        const updated = {
          sessions: [{
            id: sid,
            title: defaultTitle,
            preview: "",
            model: sessionModel,
            createdAt: Date.now(),
            project,
            pinned: false,
            msgCount: 0,
            contextWindow: DEFAULT_CONTEXT_WINDOW,
          }, ...s.sessions],
        };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });
      // 多 tab 同步：通知其他 tab 会话列表变化
      post('sessions_changed');
    })();
    set({ createProjectInputVisible: false });
  },

  deleteProject: (name) => {
    getLog()("info", "session", `删除项目: ${name}`);
    return set((s) => {
      const updated = {
        sessions: s.sessions.map((x) =>
          x.project === name ? { ...x, project: "" } : x
        ),
        activeProject: s.activeProject === name ? "all" : s.activeProject,
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });
  },

  setCreateProjectInputVisible: (createProjectInputVisible) =>
    set({ createProjectInputVisible }),

  updateSessionMeta: (sessionId, updates) => {
    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) =>
          x.id === sessionId ? { ...x, ...updates } : x
        ),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });
  },

  setContextWindow: (window) => {
    const sid = useChatStore.getState().activeSessionId;
    if (!sid) return;
    getLog()("info", "session", `更新 contextWindow: ${sid} → ${window}`);
    const old = get().sessions.find((x) => x.id === sid)?.contextWindow;
    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) => x.id === sid ? { ...x, contextWindow: window } : x),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });
    apiPatch(`sessions/${sid}`, { context_window: window }).catch((err) => {
      set((s) => {
        const updated = {
          sessions: s.sessions.map((x) => x.id === sid ? { ...x, contextWindow: old } : x),
        };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });
      console.warn('[useSessionStore] setContextWindow failed:', err);
    });
  },
}));

// 多 tab 同步：监听其他 tab 的 session 变化，重新加载会话列表
// session_deleted 事件也会触发 sessions_changed，故不单独监听
// active session 的切换由 useChatStore 监听 session_deleted 处理
on('sessions_changed', () => {
  useSessionStore.setState({ initialized: false });
  void useSessionStore.getState().initSessions();
});
