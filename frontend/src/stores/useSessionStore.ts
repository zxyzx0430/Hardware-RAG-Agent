import { create } from "zustand";
import type { Session } from "../types/session";
import { loadFromStorage, saveToStorage, removeSessionMessages } from "../utils/persistence";
import { useChatStore } from "./useChatStore";
import { useAppStore } from "./useAppStore";
import { useSettingsStore } from "./useSettingsStore";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useLogStore } from "./useLogStore";

const MAX_PINNED = 5;

const log = useLogStore.getState().log;

// ─── 时间工具 ────────────────────────────────────────────────

/** 根据 createdAt 计算会话分组 */
export function getSessionGroup(createdAt: number): string {
  const now = new Date();
  const created = new Date(createdAt);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 6 * 86400000);

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
  const yesterday = new Date(today.getTime() - 86400000);

  if (created >= today) {
    return created.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  if (created >= yesterday) {
    return "昨天";
  }
  const weekAgo = new Date(today.getTime() - 6 * 86400000);
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

/** 兼容旧数据：将旧字段迁移到 createdAt */
function migrateSession(s: any): Session {
  if (s.createdAt) return s as Session;
  // 旧数据没有 createdAt，从 createTime 字符串推算
  let createdAt = Date.now();
  if (s.createTime) {
    const parsed = Date.parse(s.createTime);
    if (!isNaN(parsed)) createdAt = parsed;
  }
  return {
    id: s.id,
    title: s.title ?? "新对话",
    preview: s.preview ?? "",
    model: s.model ?? "GPT-4o",
    createdAt,
    project: s.project ?? "",
    pinned: s.pinned ?? false,
    msgCount: s.msgCount ?? 0,
  };
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
  updateSessionMeta: (sessionId: string, updates: Partial<Pick<Session, "msgCount" | "preview" | "title" | "branchFromSessionId" | "branchFromMessageId">>) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: (() => {
    const raw = loadFromStorage("sessions", [] as Session[]);
    return Array.isArray(raw) ? (raw as any[]).map(migrateSession) : [];
  })(),
  activeProject: "all",
  searchQuery: "",
  createProjectInputVisible: false,
  initialized: false,

  initSessions: async () => {
    if (get().initialized) return;
    try {
      const data = await apiGet<{ sessions: any[] }>("sessions");
      if (data?.sessions) {
        const sessions: Session[] = data.sessions.map((s: any) => ({
          id: s.id,
          title: s.title ?? "新对话",
          preview: s.preview ?? "",
          model: s.model ?? "GPT-4o",
          createdAt: s.created_at ? new Date(s.created_at).getTime() : (s.createdAt ?? Date.now()),
          project: s.project ?? "",
          pinned: s.pinned ?? false,
          msgCount: s.msg_count ?? s.msgCount ?? 0,
          branchFromSessionId: s.branch_from_session_id ?? undefined,
          branchFromMessageId: s.branch_from_message_id ?? undefined,
        }));
        log("ok", "session", `从后端加载 ${sessions.length} 个会话`);
        set({ sessions, initialized: true });
        saveToStorage("sessions", sessions);
        return;
      }
    } catch {
      log("error", "session", "会话列表加载失败");
    }
    set({ initialized: true });
  },

  newSession: async () => {
    const { model } = useSettingsStore.getState();
    const { activeProject } = get();
    // activeProject 为 "all" 时不指定项目
    const project = activeProject === "all" ? "" : activeProject;
    const sessionModel = model || "GPT-4o";

    // 先调用后端创建会话，拿到真实 ID 再创建本地会话
    // 消除 localId/res.id 双 ID 并存窗口，避免 SSE 回调写入幽灵会话
    let sid = "";
    try {
      const res = await apiPost<{ id: string }>("sessions", {
        title: "新对话",
        model: sessionModel,
        project,
      });
      if (res?.id) {
        sid = res.id;
        log("ok", "session", `会话已创建于后端: ${sid}`);
      } else {
        log("warn", "session", "后端未返回 id，回退到本地会话");
      }
    } catch {
      log("warn", "session", "后端创建失败，回退到本地会话");
    }

    // 后端失败时回退到 localId
    if (!sid) {
      sid = `s${Date.now()}`;
    }

    const session: Session = {
      id: sid,
      title: "新对话",
      preview: "",
      model: sessionModel,
      createdAt: Date.now(),
      project,
      pinned: false,
      msgCount: 0,
    };

    log("info", "session", `新建会话: ${sid}`);

    set((s) => {
      const updated = { sessions: [session, ...s.sessions] };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    // 切换到新会话
    useChatStore.getState().setActiveSession(sid);
    useAppStore.getState().setActiveSession(sid);
  },

  selectSession: (id) => {
    log("info", "session", `选中会话: ${id}`);
    useChatStore.getState().setActiveSession(id);
    useAppStore.getState().setActiveSession(id);
  },

  deleteSession: (id, confirm) => {
    if (confirm && !confirm()) return false;

    log("info", "session", `删除会话: ${id}`);

    set((s) => {
      const updated = { sessions: s.sessions.filter((x) => x.id !== id) };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    const chatStore = useChatStore.getState();
    // 删除活跃会话时先中止正在进行的 SSE
    if (chatStore.activeSessionId === id && chatStore.isStreaming) {
      chatStore.stopStreaming();
    }
    const newSM = { ...chatStore.sessionMessages };
    delete newSM[id];
    useChatStore.setState({ sessionMessages: newSM });
    removeSessionMessages(id);

    if (chatStore.activeSessionId === id) {
      const remaining = get().sessions;
      const nextId = remaining[0]?.id ?? "";
      if (nextId) {
        useChatStore.getState().setActiveSession(nextId);
        useAppStore.getState().setActiveSession(nextId);
      } else {
        useChatStore.setState({ messages: [], activeSessionId: "" });
        useAppStore.getState().setActiveSession("");
      }
    }

    apiDelete(`sessions/${id}`).catch(() => {
      log("warn", "session", `后端删除失败: ${id}`);
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
        log("warn", "session", `置顶数量已达上限 (${MAX_PINNED})`);
        return false;
      }
    }
    const newPinned = !target.pinned;
    log("info", "session", `${newPinned ? "置顶" : "取消置顶"}: ${id}`);

    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) => x.id === id ? { ...x, pinned: newPinned } : x),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    apiPut(`sessions/${id}`, { pinned: newPinned }).catch(() => {});

    return true;
  },

  renameSession: (id, title) => {
    log("info", "session", `重命名会话 ${id}: ${title}`);
    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) => x.id === id ? { ...x, title } : x),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    apiPut(`sessions/${id}`, { title }).catch(() => {});
  },

  moveSessionToProject: (sessionId, project) => {
    log("info", "session", `移动会话 ${sessionId} 到项目: ${project}`);
    set((s) => {
      const updated = {
        sessions: s.sessions.map((x) =>
          x.id === sessionId ? { ...x, project } : x
        ),
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });

    apiPut(`sessions/${sessionId}`, { project }).catch(() => {});
  },

  setActiveProject: (activeProject) => set({ activeProject }),

  setSearchQuery: (searchQuery) => set({ searchQuery }),

  createProject: (name) => {
    if (!name.trim()) return;
    log("info", "session", `新建项目: ${name}`);
    // 创建一个归属该项目的空会话，使项目 chip 出现在列表中
    const { model } = useSettingsStore.getState();
    const localId = `s${Date.now()}`;
    const session: Session = {
      id: localId,
      title: "新对话",
      preview: "",
      model: model || "GPT-4o",
      createdAt: Date.now(),
      project: name.trim(),
      pinned: false,
      msgCount: 0,
    };
    set((s) => {
      const updated = {
        sessions: [session, ...s.sessions],
        createProjectInputVisible: false,
      };
      saveToStorage("sessions", updated.sessions);
      return updated;
    });
  },

  deleteProject: (name) => {
    log("info", "session", `删除项目: ${name}`);
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
}));
