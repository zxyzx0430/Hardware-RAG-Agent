// TODO(refactor): 此文件 1301 行严重超 max-lines ≤ 300，demo 后拆分为
// useChatStream / useAgentEvents / useHitlConfirm / useChatPersistence 4 个 hook。
//
// ── 拆分计划（demo 后执行）──────────────────────────────────────
// 现状：单文件 1457 行，包含 SSE 流式 / HITL resume / 持久化 / 会话动作 / 工具方法 5 类职责。
// 目标：用 Zustand slice pattern 拆成 4 个文件，保持 set/get 共享访问。
//
// 1. useChatStream.ts —— SSE 流式处理
//    职责：sendMessage / 内联 onEvent / stopStreaming / streaming* 状态
//    含：streamingContent / streamingSteps / streamingSources / streamingTodos /
//        streamingSessionId / streamingStartTime / streamingError / currentSseRequest / _pendingUsage
//    模块级变量：_needsParagraphBreak
//
// 2. useAgentEvents.ts —— HITL 确认 + Agent resume 事件
//    职责：_handleResumeEvent / _appendResumeText / _appendResumeThinking /
//          _appendResumeSource / _appendResumeToolCall / _updateResumeToolResult /
//          _finalizeResume / resumeAgent / clearPendingConfirm / pendingConfirm / _lastAgentPayload
//
// 3. useChatPersistence.ts —— 消息持久化
//    职责：fetchMessages / persistLastTurn / syncToSession / saveSessionMessages /
//          flushPendingShards / scheduleShardSave / persistedMsgIds
//    依赖：persistence utils（saveSessionMessages / loadSessionMessages / migrateMessagesToShards）
//
// 4. useChatActions.ts —— 会话动作 + 工具方法
//    职责：retryMessage / editAndResend / branchThread / truncateAndResend /
//          quoteMessage / exportConversation / pushCodeToWorkbench /
//          setActiveSession / setMessages / setSources / showStats / hideStats /
//          setSelectedKbIds / toggleKbSelection
//    纯函数工具（可独立 utils 文件）：trimMessages / parseMessageContent /
//          serializeMessageContent / _flattenToolResult / mapBackendMessage / getLog
//
// 拆分策略：Zustand slice pattern，每个 slice 是一个函数 (set, get) => ({...})
//   合并方式：
//     useChatStore = create<ChatState>((...a) => ({
//       ...useChatStreamSlice(...a),
//       ...useAgentEventsSlice(...a),
//       ...useChatPersistenceSlice(...a),
//       ...useChatActionsSlice(...a),
//     }))
//
// 风险点：
//   - sendMessage 和 _handleResumeEvent 互相引用（onEvent 回调），拆分时需保留 set/get 共享访问
//   - truncateAndResend 被 retryMessage / editAndResend 共用，归到 Actions slice
//   - syncToSession 被多处调用，归到 Persistence slice 并导出
//   - persistedMsgIds / _needsParagraphBreak 是模块级状态，拆分时需确认是否提升为 slice 内部 ref
//   - ChatState interface 需拆成 4 个 slice interface 再 extends 合并
//
// 优先级：demo 后第 1 周（不阻塞 7.15 demo）
// 前置条件：补齐 useChatStore 单元测试（streaming / resume / persist 三条主路径）后再动手
import { create } from "zustand";
import type { Message, SourceRef, ActivityStep, Session, ContentPart, TodoItem } from "../types/session";
import type { ChatSSEEvent, Attachment } from "../types/api";
import { loadFromStorage, saveToStorage, saveSessionMessages, loadSessionMessages, removeSessionMessages, migrateMessagesToShards } from "../utils/persistence";
import { post, on } from "../utils/broadcast";
import { useAppStore } from "./useAppStore";
import { useSessionStore, CONTEXT_WINDOW_256K } from "./useSessionStore";
import { apiSSE, apiPost, apiGet, apiDelete } from "../api/client";
import { useSettingsStore } from "./useSettingsStore";
import { useLogStore } from "./useLogStore";
import { useToastStore } from "./useToastStore";
import { handleToolCallEvent, handleToolResultEvent, handleCompileLogEvent, handleProgressEvent } from "./useWorkbenchBridge";

const DEFAULT_MODEL = "";

// 段落换行标志：tool_result 后设置的标志，下一次 text chunk 前插入 \n\n 分隔工具结果和回答文本
// 按 sessionId 隔离，避免多会话切换时串号
const _needsParagraphBreak = new Map<string, boolean>();

// Track the most recent Agent tool_call id so compile_log / progress SSE events
// can be routed to the correct Workbench pane (FlashPane for build/flash tools).
// 按 sessionId 隔离，避免多会话切换时串号
const _lastToolCallId = new Map<string, string>();

// Last heartbeat timestamp (module-level: avoids UI re-renders on heartbeat).
let _lastHeartbeatAt: number = 0;

declare global {
  interface Window {
    __loadMockData?: () => void;
    __clearMockData?: () => void;
  }
}

interface ChatState {
  messages: Message[];
  sessionMessages: Record<string, Message[]>;
  sources: SourceRef[];
  isStreaming: boolean;
  /** fetchMessages 期间为 true，用于区分"加载中"和"真空会话" */
  isLoadingMessages: boolean;
  streamingContent: string;
  streamingSteps: ActivityStep[];
  streamingSources: SourceRef[];
  streamingTodos: TodoItem[];
  /** 当前流式请求所属的 sessionId，用于回调时定位正确的会话 */
  streamingSessionId: string | null;
  /** 流式请求开始时间，用于实时计时 */
  streamingStartTime: number | null;
  /** done 事件中携带的 usage 数据，onDone 时写入 Message */
  _pendingUsage: import("../types/session").TokenUsage | null;
  streamingError: { code: string; message: string; detail: string } | null;
  /** Context compression in progress — show "正在压缩上下文..." status banner */
  isCompressing: boolean;
  /** Compression status message from backend */
  compressingMessage: string;
  currentSseRequest: AbortController | null;
  /** 后台 SSE 控制器（用户切换会话时未完成的流，key 为 sessionId，不 abort 让其继续） */
  backgroundSseRequests: Map<string, AbortController>;
  activeSessionId: string;
  statsOpen: boolean;
  /** 选中的知识库 ID 列表，空数组表示使用全部已启用知识库 */
  selectedKbIds: string[];
  setSelectedKbIds: (ids: string[]) => void;
  /** 切换某个知识库的选中状态（在数组中增删） */
  toggleKbSelection: (kbId: string) => void;
  /** 401 未授权：为 true 时提示用户配置 API Key */
  needsApiKey: boolean;
  setNeedsApiKey: (v: boolean) => void;
  /** 按 sessionId 存储输入框草稿（内存，不持久化到 localStorage） */
  drafts: Record<string, string>;
  /** 设置某会话的草稿（输入时持续调用） */
  setDraft: (sessionId: string, text: string) => void;
  /** 清空某会话的草稿（发送后调用） */
  clearDraft: (sessionId: string) => void;
  /** HITL pending confirm: when set, ConfirmDialog shows (v2-T4) */
  pendingConfirm: PendingConfirm | null;
  /** Last agent request body, cached for resume API (v2-T4) */
  _lastAgentPayload: Record<string, unknown> | null;
  /** Resume agent after HITL decision (allow/deny/stop) */
  resumeAgent: (decision: "allow" | "deny" | "stop") => void;
  /** Clear pending confirm without resuming (e.g. dialog dismissed) (v2-T4) */
  clearPendingConfirm: () => void;

  // ── 来源查看器状态（按 sessionId 隔离，防止跨会话污染）──
  /** key = sessionId, value = {messageId, sourceId} 定位来源（解决 source id 跨消息重复） */
  sessionFileViewerSource: Record<string, { messageId: string; sourceId: string } | null>;
  /** key = sessionId, value = 当前高亮的来源 ID */
  sessionHighlightSourceId: Record<string, string | null>;
  /** 设置某会话的来源查看器目标；messageId 或 sourceId 为 null 时清除该会话状态 */
  setSessionFileViewerSource: (sessionId: string, messageId: string | null, sourceId: string | null) => void;
  /** 设置某会话的高亮来源 ID */
  setSessionHighlightSourceId: (sessionId: string, id: string | null) => void;
  /** 删除会话时清理对应来源查看器状态 */
  cleanupSessionSourceState: (sessionId: string) => void;

  sendMessage: (content: string, attachments?: Attachment[], quoted?: Message) => void;
  stopStreaming: (errorMessage?: string) => void;
  retryMessage: (msgId: string) => void;
  editAndResend: (msgId: string, newContent: string) => void;
  branchThread: (msgId: string) => void;
  pushCodeToWorkbench: (code: string, name: string) => void;
  setMessages: (msgs: Message[]) => void;
  setSources: (srcs: SourceRef[]) => void;
  setActiveSession: (id: string) => void;
  /** 从后端加载会话消息（localStorage 缓存先填充，后端数据覆盖） */
  fetchMessages: (sessionId: string) => Promise<void>;
  /** 流式结束后将最后一轮 user+assistant 消息持久化到后端 */
  persistLastTurn: (sessionId: string) => Promise<void>;
  quoteMessage: (msgId: string) => void;
  exportConversation: (format: "markdown" | "json") => void;
  showStats: () => void;
  hideStats: () => void;
}

/** HITL pending confirmation payload (v2-T4). */
interface PendingConfirm {
  calls: Array<{
    name: string;
    args: Record<string, unknown>;
    call_id: string;
    risk_level: "low" | "medium" | "high";
  }>;
  count: number;
}

function getLog() {
  return useLogStore.getState().log;
}

// Maximum number of messages retained in memory to avoid OOM on long conversations
const MAX_MESSAGES = 200;

// Track which assistant message ids have already been persisted to backend,
// to prevent duplicate POSTs when onDone/stopStreaming could both fire.
const persistedMsgIds = new Set<string>();

/** Trim messages array to the last MAX_MESSAGES entries (rolling window) */
function trimMessages(msgs: Message[]): Message[] {
  if (msgs.length <= MAX_MESSAGES) return msgs;
  return msgs.slice(-MAX_MESSAGES);
}

/** Deserialize backend message content (string) into string | ContentPart[].
 *  Multimodal messages are stored as JSON string; pure text stays as string. */
function parseMessageContent(raw: unknown): string | ContentPart[] {
  if (typeof raw !== "string") return (raw as string) || "";
  if (raw.startsWith("[")) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0 && parsed[0]?.type) {
        return parsed as ContentPart[];
      }
    } catch { /* fallthrough to plain string */ }
  }
  return raw;
}

/** Serialize local message content (string | ContentPart[]) into backend-storable string. */
function serializeMessageContent(content: string | ContentPart[]): string {
  if (typeof content === "string") return content;
  return JSON.stringify(content);
}

/** Flatten a tool_result SSE payload into a single display string.
 *  Handles legacy string results, envelope.output, and structured ErrorDetail
 *  ({error_type, error_message, suggestion, retryable}) which would otherwise
 *  crash React when rendered as a child. */
function _flattenToolResult(result: unknown): string {
  if (typeof result === "string") return result;
  if (!result || typeof result !== "object") return String(result ?? "");
  const r = result as Record<string, unknown>;
  const out = typeof r.output === "string" ? r.output : "";
  if (out) return out;
  const err = r.error;
  if (err && typeof err === "object") {
    const e = err as Record<string, unknown>;
    const parts = [
      e.error_message ? String(e.error_message) : "",
      e.suggestion ? `建议：${e.suggestion}` : "",
    ].filter(Boolean);
    if (parts.length) return parts.join(" | ");
  }
  if (typeof err === "string" && err) return err;
  try { return JSON.stringify(r); } catch { return String(r); }
}

/** Extract image data from image_generation tool_result.
 *  Backend envelope: { output, data: { image_url, image_base64 } }
 *  Returns an ImagePart (ContentPart) ready to append to message.content, or null. */
function _extractImagePart(result: unknown): ContentPart | null {
  if (!result || typeof result !== "object") return null;
  const r = result as Record<string, unknown>;
  // image_base64 / image_url may be at top level (legacy) or inside data
  const data = (typeof r.data === "object" && r.data !== null) ? r.data as Record<string, unknown> : r;
  const b64 = typeof data.image_base64 === "string" ? data.image_base64 : "";
  const url = typeof data.image_url === "string" ? data.image_url : "";
  if (!b64 && !url) return null;
  const imgUrl = b64 ? `data:image/png;base64,${b64}` : url;
  return { type: "image_url", image_url: { url: imgUrl, detail: "auto" } };
}

/** Extract file_path from file-editing tool_result for "查看 diff" button.
 *  Tools: write_file/edit_file return data.path; multi_edit/apply_patch return data.file_path. */
const _FILE_EDIT_TOOLS = new Set(["write_file", "edit_file", "multi_edit", "apply_patch"]);
function _extractFilePath(toolName: string, result: unknown): string | undefined {
  if (!_FILE_EDIT_TOOLS.has(toolName)) return undefined;
  if (!result || typeof result !== "object") return undefined;
  const r = result as Record<string, unknown>;
  const data = (typeof r.data === "object" && r.data !== null) ? r.data as Record<string, unknown> : r;
  const path = typeof data.path === "string" ? data.path : (typeof data.file_path === "string" ? data.file_path : "");
  return path || undefined;
}

/** Append an ImagePart to existing message content (string | ContentPart[]).
 *  If content is a string, convert to ContentPart[] with text + image.
 *  If content is already ContentPart[], append the image. */
function _appendImageToContent(content: string | ContentPart[], image: ContentPart): string | ContentPart[] {
  if (typeof content === "string") {
    return content.trim()
      ? [{ type: "text", text: content }, image]
      : [image];
  }
  return [...content, image];
}

/** Replace the text in a ContentPart[] with a new string (used when switching
 *  active sessions while streaming, so streamingContent does not overwrite the
 *  whole ContentPart[] and lose ImageParts). */
function _mergeTextIntoParts(parts: ContentPart[], text: string): ContentPart[] {
  const newParts: ContentPart[] = parts.filter((p) => p.type !== "text");
  if (text.trim()) {
    newParts.push({ type: "text", text });
  }
  return newParts;
}

/** FIX-1: 判断消息 content 是否非空（支持 string 和 ContentPart[] 两种形态）。 */
function _hasContent(content: string | ContentPart[]): boolean {
  if (typeof content === "string") return content !== "";
  return Array.isArray(content) && content.length > 0;
}

/** FIX-3: 将 pending steps 标记为 done 并补 duration，防止耗时无限累计。 */
function _finalizePendingSteps(steps: ActivityStep[]): ActivityStep[] {
  return steps.map((step) =>
    step.status === "pending"
      ? {
          ...step,
          status: "done" as const,
          duration: step.startTime ? Date.now() - step.startTime : step.duration,
        }
      : step
  );
}

/** 同步 messages 到 sessionMessages 并持久化 */
function syncToSession(
  state: { messages: Message[]; sessionMessages: Record<string, Message[]>; activeSessionId: string },
  overrideMessages?: Message[]
): { messages: Message[]; sessionMessages: Record<string, Message[]> } {
  const msgs = overrideMessages ?? state.messages;
  const updatedSM = { ...state.sessionMessages, [state.activeSessionId]: msgs };
  saveSessionMessages(state.activeSessionId, msgs);
  return { messages: msgs, sessionMessages: updatedSM };
}

/** Map a backend Message payload (BackendMessage) to the frontend Message shape.
 *  Used by fetchMessages for both the initial load and the lazy-migration refetch. */
function mapBackendMessage(m: BackendMessage): Message {
  return {
    id: m.id,
    role: m.role as Message["role"],
    content: parseMessageContent(m.content),
    timestamp: m.created_at ? new Date(m.created_at).getTime() : Date.now(),
    sources: m.sources || [],
    activity: m.activity || undefined,
  };
}


export const useChatStore = create<ChatState>((set, get) => {
  /** Truncate messages to truncatedCount, sync to sessionMessages + backend, then resend.
   *  Deduplicates the truncate→apiDelete→sendMessage template shared by retryMessage and editAndResend. */
  async function truncateAndResend(
    sessionId: string,
    truncatedCount: number,
    sendContent: string,
    attachments?: Attachment[]
  ): Promise<void> {
    const currentMsgs = get().messages;
    const truncated = currentMsgs.slice(0, truncatedCount);
    const removed = currentMsgs.slice(truncatedCount);
    set((s) => syncToSession({ ...s, messages: truncated }));
    apiDelete(`sessions/${sessionId}/messages?keep_count=${truncated.length}`)
      .then(() => {
        get().sendMessage(sendContent, attachments);
      })
      .catch((err) => {
        set((s) => syncToSession({ ...s, messages: [...truncated, ...removed] }));
        console.warn('[useChatStore] clearMessages failed:', err);
        useToastStore.getState().showError("重发失败，请手动重试");
      });
  }

  // ── v2-T4: HITL resume helpers ──────────────────────────────
  /** Handle SSE event during resume stream (text/thinking/source/tool_call/tool_result/confirm/error). */
  function _handleResumeEvent(evt: ChatSSEEvent): void {
    if (evt.type === "context_compressing") {
      set({ isCompressing: true, compressingMessage: evt.message || "正在压缩上下文..." });
      return;
    }
    if (evt.type === "text") { _appendResumeText(evt); return; }
    if (evt.type === "thinking") { _appendResumeThinking(evt); return; }
    if (evt.type === "source") { _appendResumeSource(evt); return; }
    if (evt.type === "tool_call") { _appendResumeToolCall(evt); return; }
    if (evt.type === "tool_result") { _updateResumeToolResult(evt); return; }
    if (evt.type === "tool_confirm_required") { set({ pendingConfirm: { calls: evt.calls, count: evt.count } }); return; }
    if (evt.type === "error") { set({ streamingError: { code: "ERROR", message: evt.message, detail: "" } }); }
  }

  /** Append text chunk: update both streamingContent and last assistant message content.
   *  FIX-1.4: 改为追加到 last.content，不依赖可能被重置的 streamingContent。 */
  function _appendResumeText(evt: Extract<ChatSSEEvent, { type: "text" }>): void {
    set((s) => {
      const chunk = evt.content || "";
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role !== "assistant") {
        return { streamingContent: s.streamingContent + chunk, messages: msgs, isCompressing: false };
      }
      if (typeof last.content === "string") {
        // FIX-1.4: 追加到 last.content，避免 streamingContent 被重置后覆盖主流程文本
        const newContent = (last.content || "") + chunk;
        msgs[msgs.length - 1] = { ...last, content: newContent };
        return { streamingContent: newContent, messages: msgs, isCompressing: false };
      }
      // ContentPart[] path: append text to last TextPart (preserves ImageParts)
      const parts: ContentPart[] = [...last.content];
      const lastTextIdx = parts.map((p) => p.type).lastIndexOf("text");
      if (lastTextIdx >= 0) {
        const lastText = parts[lastTextIdx] as Extract<ContentPart, { type: "text" }>;
        parts[lastTextIdx] = { ...lastText, text: lastText.text + chunk };
      } else {
        parts.push({ type: "text", text: chunk });
      }
      msgs[msgs.length - 1] = { ...last, content: parts };
      return {
        streamingContent: parts
          .filter((p): p is Extract<ContentPart, { type: "text" }> => p.type === "text")
          .map((p) => p.text)
          .join("\n\n"),
        messages: msgs,
        isCompressing: false,
      };
    });
  }

  /** Append thinking chunk: reuse main-stream source-switch logic (active session only). */
  function _appendResumeThinking(evt: Extract<ChatSSEEvent, { type: "thinking" }>): void {
    const content = evt.content ?? "";
    const source = evt.source;
    set((s) => {
      const steps = [...s.streamingSteps];
      const last = steps[steps.length - 1];
      if (source === "reasoning" && last?.type === "thinking" && last.source === "llm") {
        steps[steps.length - 1] = { ...last, content, source: "reasoning" };
        return { streamingSteps: steps };
      }
      if (last?.type === "thinking" && last.source === source) {
        steps[steps.length - 1] = { ...last, content: (last.content || "") + content };
        return { streamingSteps: steps };
      }
      if (last?.type === "thinking") steps[steps.length - 1] = { ...last, status: "done" };
      steps.push({ type: "thinking", id: `h-${Date.now()}`, content, source });
      return { streamingSteps: steps };
    });
  }

  /** Append source ref: update streamingSources and last assistant message sources. */
  function _appendResumeSource(evt: Extract<ChatSSEEvent, { type: "source" }>): void {
    const baseSrc: SourceRef = { id: evt.id ?? `src-${Date.now()}`, title: evt.title ?? "未知来源", doc: evt.doc ?? "", page: evt.page ?? 0, chunk_index: evt.chunk_index, page_start: evt.page_start, page_end: evt.page_end, section_title: evt.section_title, source_url: evt.source_url, category: evt.category, chunk_method: evt.chunk_method, score: evt.score ?? 0, score_percentage: evt.score_percentage, relevance_level: evt.relevance_level, citation: evt.citation, excerpt: evt.excerpt ?? "", kb_id: evt.kb_id, kb_name: evt.kb_name, small_chunk_id: evt.small_chunk_id, big_chunk_id: evt.big_chunk_id, small_chunk_text: evt.small_chunk_text };
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      const src: SourceRef = { ...baseSrc, messageId: last?.id };
      if (last?.role === "assistant") msgs[msgs.length - 1] = { ...last, sources: [...(last.sources || []), src] };
      return { streamingSources: [...s.streamingSources, src], sources: [...s.streamingSources, src], messages: msgs };
    });
  }

  function _appendResumeToolCall(evt: Extract<ChatSSEEvent, { type: "tool_call" }>): void {
    const step: ActivityStep = {
      type: "tool",
      id: evt.call_id || `t-${Date.now()}`,
      name: evt.tool,
      args: typeof evt.args === "string" ? evt.args : JSON.stringify(evt.args ?? {}),
      status: "pending",
      call_id: evt.call_id,
      risk_level: evt.risk_level,
      startTime: Date.now(),
    };
    set((s) => ({ streamingSteps: [...s.streamingSteps, step] }));
  }

  function _updateResumeToolResult(evt: Extract<ChatSSEEvent, { type: "tool_result" }>): void {
    const resultText = _flattenToolResult(evt.result);
    const status = evt.success === false ? "error" : "done";
    set((s) => ({
      streamingSteps: s.streamingSteps.map((step) =>
        step.call_id === evt.call_id ? { ...step, status, result: resultText, duration: evt.duration } : step
      ),
    }));
  }

  /** Finalize resume stream — attach activity to last assistant message.
   *  Content/sources already updated in real-time by _appendResumeText/_appendResumeSource
   *  (aligns with main stream onDone, which does NOT re-merge streamingContent). */
  function _finalizeResume(sid: string): void {
    set((s) => {
      const msgs = [...s.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant") {
        msgs[msgs.length - 1] = {
          ...last,
          activity: s.streamingSteps.length > 0
            ? { durationMs: 0, steps: s.streamingSteps, status: "done" as const }
            : last.activity,
        };
      }
      const newSM = { ...s.sessionMessages, [sid]: msgs };
      saveSessionMessages(sid, msgs);
      // SubTask 8.5: 同步清理 backgroundSseRequests（resume 可能也在后台）
      let newBg = s.backgroundSseRequests;
      if (s.backgroundSseRequests.has(sid)) {
        newBg = new Map(s.backgroundSseRequests);
        newBg.delete(sid);
      }
      return {
        messages: msgs,
        sessionMessages: newSM,
        isStreaming: false,
        isCompressing: false,
        streamingContent: "",
        streamingSteps: [],
        streamingSources: [],
      streamingTodos: [],
        currentSseRequest: null,
        backgroundSseRequests: newBg,
        _lastAgentPayload: null,
      };
    });
  }

  return {
  messages: [],
  sessionMessages: (() => {
    // 优先从分片格式加载
    const sessions = loadFromStorage("sessions", [] as { id: string }[]);
    const sm: Record<string, Message[]> = {};
    for (const s of sessions) {
      const msgs = loadSessionMessages<Message[]>(s.id, []);
      if (msgs.length > 0) sm[s.id] = msgs;
    }
    // 兼容旧格式：如果分片为空但旧 key 存在，迁移
    const raw = loadFromStorage("messages", {} as Record<string, Message[]>);
    if (raw && typeof raw === "object" && !Array.isArray(raw) && Object.keys(sm).length === 0 && Object.keys(raw).length > 0) {
      migrateMessagesToShards(raw);
      return raw;
    }
    return sm;
  })(),
  sources: [],
  isStreaming: false,
  isLoadingMessages: false,
  streamingContent: "",
  streamingSteps: [],
  streamingSources: [],
      streamingTodos: [],
  streamingSessionId: null,
  streamingStartTime: null,
  _pendingUsage: null,
  streamingError: null,
  isCompressing: false,
  compressingMessage: "",
  currentSseRequest: null,
  backgroundSseRequests: new Map<string, AbortController>(),
  activeSessionId: loadFromStorage("activeSession", ""),
  statsOpen: false,
  selectedKbIds: [],
  needsApiKey: false,
  pendingConfirm: null,
  _lastAgentPayload: null,
  // 草稿按 sessionId 隔离，仅存内存（spec 决策 4：刷新后丢失可接受）
  drafts: {},
  // 来源查看器状态按 sessionId 隔离（仅内存，刷新后丢失可接受）
  sessionFileViewerSource: {},
  sessionHighlightSourceId: {},

  setSelectedKbIds: (selectedKbIds) => set({ selectedKbIds }),

  setNeedsApiKey: (needsApiKey) => set({ needsApiKey }),

  toggleKbSelection: (kbId) => set((s) => ({
    selectedKbIds: s.selectedKbIds.includes(kbId)
      ? s.selectedKbIds.filter((id) => id !== kbId)
      : [...s.selectedKbIds, kbId],
  })),

  setDraft: (sessionId, text) => set((s) => ({
    drafts: { ...s.drafts, [sessionId]: text },
  })),

  clearDraft: (sessionId) => set((s) => {
    const next = { ...s.drafts };
    delete next[sessionId];
    return { drafts: next };
  }),

  setSessionFileViewerSource: (sessionId, messageId, sourceId) => set((s) => {
    if (!messageId || !sourceId) {
      const next = { ...s.sessionFileViewerSource };
      delete next[sessionId];
      return { sessionFileViewerSource: next };
    }
    return { sessionFileViewerSource: { ...s.sessionFileViewerSource, [sessionId]: { messageId, sourceId } } };
  }),

  setSessionHighlightSourceId: (sessionId, id) => set((s) => ({
    sessionHighlightSourceId: { ...s.sessionHighlightSourceId, [sessionId]: id },
  })),

  cleanupSessionSourceState: (sessionId) => set((s) => {
    const nextFv = { ...s.sessionFileViewerSource };
    const nextHl = { ...s.sessionHighlightSourceId };
    delete nextFv[sessionId];
    delete nextHl[sessionId];
    return { sessionFileViewerSource: nextFv, sessionHighlightSourceId: nextHl };
  }),

  resumeAgent: (decision) => {
    const payload = get()._lastAgentPayload;
    if (!payload) {
      getLog()("warn", "chat", "resumeAgent: no cached payload, ignoring");
      return;
    }
    const sid = get().streamingSessionId || get().activeSessionId;
    set({
      pendingConfirm: null,
      streamingError: null,
      isCompressing: false,
      streamingContent: "",
      streamingSteps: [],
      streamingSources: [],
      streamingTodos: [],
      isStreaming: true,
      streamingSessionId: sid,
      streamingStartTime: Date.now(),
    });
    const controller = new AbortController();
    set({ currentSseRequest: controller });
    apiSSE("agent-sandbox/resume", { payload, decision }, {
      onEvent: (evt) => _handleResumeEvent(evt),
      onDone: () => _finalizeResume(sid),
    }, controller);
  },

  clearPendingConfirm: () => set({ pendingConfirm: null }),

  setMessages: (messages) => {
    const trimmed = trimMessages(messages);
    set((s) => syncToSession({ ...s, messages: trimmed }));
  },
  setSources: (sources) => set({ sources }),

  fetchMessages: async (sessionId: string) => {
    if (!sessionId) return;
    // 标记加载中：让 UI 在无缓存时显示骨架屏，而非 EmptyState
    set({ isLoadingMessages: true });
    // 1. 先用 localStorage 缓存立即填充（避免 UI 闪烁）
    const cached = get().sessionMessages[sessionId] || [];
    if (cached.length > 0 && get().activeSessionId === sessionId) {
      set({ messages: cached });
    }
    // 2. 从后端拉权威数据
    try {
      const data = await apiGet<{ messages: BackendMessage[] }>(`sessions/${sessionId}/messages`);
      let msgs: Message[] = (data?.messages || []).map(mapBackendMessage);

      // 懒迁移：后端空但 localStorage 有消息 → POST 到后端（修复前的旧数据）
      if (msgs.length === 0 && cached.length > 0) {
        getLog()("info", "chat", `懒迁移: 会话 ${sessionId} 有 ${cached.length} 条本地消息，同步到后端`);
        try {
          for (const m of cached) {
            await apiPost(`sessions/${sessionId}/messages`, {
              role: m.role,
              content: serializeMessageContent(m.content),
              sources: m.sources || [],
              tool_calls: [],
              activity: m.activity || null,
            });
          }
          getLog()("ok", "chat", `懒迁移完成: ${sessionId} ${cached.length} 条消息已同步`);
          // 迁移后重新 fetch 拿后端 ID
          const refetch = await apiGet<{ messages: BackendMessage[] }>(`sessions/${sessionId}/messages`);
          msgs = (refetch?.messages || []).map(mapBackendMessage);
        } catch {
          getLog()("warn", "chat", `懒迁移失败: ${sessionId}，使用本地缓存`);
          msgs = cached;
        }
      }

      // 防止 race condition：仅当当前活跃会话仍是该会话时才更新 messages
      // 额外守卫：如果本地缓存的消息数量 > 后端返回，说明本地更新（流式刚完成或
      // persistLastTurn 还没完成），不覆盖，避免流式内容消失。
      const localMsgs = get().sessionMessages[sessionId] || [];
      // FIX-1.2: 流式中跳过 fetchMessages，避免后端空数据覆盖本地流式内容
      if (get().isStreaming && get().streamingSessionId === sessionId) {
        getLog()("info", "chat", `跳过 fetchMessages: 会话 ${sessionId} 正在流式中`);
        return;
      }
      if (localMsgs.length > msgs.length) {
        getLog()("info", "chat", `跳过后端覆盖: 会话 ${sessionId} 本地 ${localMsgs.length} 条 > 后端 ${msgs.length} 条`);
        return;
      }
      // FIX-1.2: content-aware 守卫 — 本地末尾 assistant 有 content 而后端为空时，保留本地
      if (localMsgs.length === msgs.length && localMsgs.length > 0) {
        const localLast = localMsgs[localMsgs.length - 1];
        const backendLast = msgs[msgs.length - 1];
        if (localLast?.role === "assistant" && backendLast?.role === "assistant"
          && _hasContent(localLast.content) && !_hasContent(backendLast.content)) {
          getLog()("info", "chat", `跳过后端覆盖: 会话 ${sessionId} 本地末尾 assistant 有 content，后端为空`);
          return;
        }
      }
      if (get().activeSessionId === sessionId) {
        set((s) => ({
          messages: msgs,
          sessionMessages: { ...s.sessionMessages, [sessionId]: msgs },
          needsApiKey: false,
        }));
        saveSessionMessages(sessionId, msgs);
      } else {
        // 用户已切到别的会话：仍更新 sessionMessages 缓存，不动 messages
        set((s) => ({
          sessionMessages: { ...s.sessionMessages, [sessionId]: msgs },
          needsApiKey: false,
        }));
        saveSessionMessages(sessionId, msgs);
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      if (errMsg.includes("401")) {
        set({ needsApiKey: true });
      } else {
        getLog()("warn", "chat", `加载会话消息失败: ${sessionId}，使用缓存`);
        useToastStore.getState().showError(`加载会话消息失败: ${errMsg}`);
      }
    } finally {
      set({ isLoadingMessages: false });
    }
  },

  persistLastTurn: async (sessionId: string) => {
    if (!sessionId) return;
    const msgs = get().sessionMessages[sessionId] || [];
    if (msgs.length < 2) return;
    // 只持久化最后两条（user + assistant）
    const lastTwo = msgs.slice(-2);
    // 防重复：用 assistant 消息 id 判断是否已持久化过
    const lastAssistant = lastTwo.find((m) => m.role === "assistant");
    if (lastAssistant && persistedMsgIds.has(lastAssistant.id)) return;
    // FIX-1.3: POST 前校验 — 若最后一条 assistant 消息 content 为空，跳过持久化
    // （避免空消息覆盖后端已有数据，导致刷新后回答消失）
    if (lastAssistant && !_hasContent(lastAssistant.content)) {
      getLog()("warn", "chat", `跳过 persistLastTurn: 会话 ${sessionId} 最后一条 assistant 消息 content 为空`);
      return;
    }
    try {
      for (const m of lastTwo) {
        await apiPost(`sessions/${sessionId}/messages`, {
          role: m.role,
          content: serializeMessageContent(m.content),
          sources: m.sources || [],
          tool_calls: [],
          activity: m.activity || null,
        });
      }
      if (lastAssistant) persistedMsgIds.add(lastAssistant.id);
      getLog()("ok", "chat", `已持久化会话 ${sessionId} 最后两条消息到后端`);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `持久化消息失败: ${sessionId}`);
      useToastStore.getState().showError(`保存会话失败: ${errMsg}`);
    }
  },

  setActiveSession: (id) => {
    const { activeSessionId, messages, sessionMessages, isStreaming,
            streamingSessionId, currentSseRequest, streamingContent,
            streamingSteps, streamingStartTime, backgroundSseRequests } = get();
    if (id === activeSessionId) return;
    getLog()("info", "chat", `切换会话: ${activeSessionId} → ${id}`);

    // 流式中切换会话：保存部分内容到 sessionMessages，但不中止 SSE。
    // 后台 SSE 继续运行，onEvent 回调中 isActive=false 时会写入 sessionMessages[sid]。
    let finalMessages = messages;
    if (isStreaming && streamingSessionId === activeSessionId) {
      // 当前活跃会话在流式中：把 streamingContent 同步到最后一条 assistant 消息。
      // 若消息已是 ContentPart[]（如 image_generation 已追加 ImagePart），只更新其中
      // 的 TextPart，不要整体覆盖为字符串导致图片丢失。
      finalMessages = messages.map((m, i) =>
        i === messages.length - 1 && m.role === "assistant"
          ? {
              ...m,
              content: typeof m.content === "string"
                ? (streamingContent || m.content)
                : _mergeTextIntoParts(m.content, streamingContent),
              ...(streamingSteps.length > 0
                ? { activity: { durationMs: 0, steps: streamingSteps, status: "running" as const } }
                : {}),
            }
          : m
      );
      getLog()("info", "chat", `会话 ${streamingSessionId} 流式输出在后台继续（未中止）`);
    }

    // 保存当前会话的消息
    const updatedSessionMessages = {
      ...sessionMessages,
      [activeSessionId]: finalMessages,
    };

    // 加载目标会话的消息
    const loadedMessages = updatedSessionMessages[id] || [];

    // SubTask 8.2: 当前会话有未完成的 SSE，移入 backgroundSseRequests（不 abort）
    const newBackgroundSse = new Map(backgroundSseRequests);
    const isLeavingStreaming = isStreaming && !!currentSseRequest && streamingSessionId === activeSessionId;
    if (isLeavingStreaming && streamingSessionId) {
      newBackgroundSse.set(streamingSessionId, currentSseRequest!);
      getLog()("info", "chat", `会话 ${streamingSessionId} 的 SSE 移入后台继续运行（未中止）`);
    }

    // SubTask 8.3: 目标会话若有后台 SSE，恢复为当前流（原 no-op 三元已修复为正确清理）
    const hasBackgroundStream = newBackgroundSse.has(id);
    let restoredContent = "";
    let restoredSteps: ActivityStep[] = [];
    let restoredController: AbortController | null = null;
    let restoredStartTime: number | null = null;
    if (hasBackgroundStream) {
      restoredController = newBackgroundSse.get(id) ?? null;
      newBackgroundSse.delete(id);
      const lastMsg = loadedMessages[loadedMessages.length - 1];
      if (lastMsg?.role === "assistant") {
        restoredContent = typeof lastMsg.content === "string"
          ? lastMsg.content
          : lastMsg.content
              .filter((p): p is Extract<ContentPart, { type: "text" }> => p.type === "text")
              .map((p) => p.text)
              .join("\n\n");
        restoredSteps = lastMsg.activity?.steps || [];
      }
      // streamingStartTime 未随后台 SSE 持久化，用 now 作为近似值
      restoredStartTime = Date.now();
      getLog()("info", "chat", `切回流式会话 ${id}，恢复 content (${restoredContent.length} chars)`);
    }

    set({
      activeSessionId: id,
      messages: loadedMessages,
      sessionMessages: updatedSessionMessages,
      isStreaming: hasBackgroundStream,
      streamingContent: restoredContent,
      streamingSteps: restoredSteps,
      streamingSources: [],
      streamingTodos: [],
      // SubTask 8.3: 正确清理 streamingSessionId / currentSseRequest（原 no-op 三元已修复）
      streamingSessionId: hasBackgroundStream ? id : null,
      streamingStartTime: hasBackgroundStream ? restoredStartTime : null,
      currentSseRequest: hasBackgroundStream ? restoredController : null,
      backgroundSseRequests: newBackgroundSse,
    });

    saveSessionMessages(activeSessionId, finalMessages);
    saveToStorage("activeSession", id);

    // 异步从后端拉取该会话的完整消息（覆盖缓存，防止 localStorage 丢失/不一致）
    // 不 await：让 UI 立即响应，后端数据到达后再刷新
    void get().fetchMessages(id);
  },

  sendMessage: async (content, attachments, quoted) => {
    const { messages, isStreaming, activeSessionId, backgroundSseRequests } = get();
    // 允许只有附件没有文字（如只发图片）
    if (isStreaming) {
      useToastStore.getState().showWarning("正在生成中，请先停止当前回答");
      return;
    }
    // SubTask 8.4: abort 后台 SSE（避免两个流并发导致消息损坏）
    if (backgroundSseRequests.has(activeSessionId)) {
      const bgController = backgroundSseRequests.get(activeSessionId);
      bgController?.abort();
      const newBg = new Map(backgroundSseRequests);
      newBg.delete(activeSessionId);
      set({ backgroundSseRequests: newBg });
      getLog()("warn", "chat", `会话 ${activeSessionId} 有后台 SSE，已中止以避免并发`);
    }
    if (!content.trim() && (!attachments || attachments.length === 0)) return;
    const imgCount = attachments?.filter((a) => a.type.startsWith("image/")).length ?? 0;
    console.info('[ChatStore] sendMessage session=%s prompt_len=%d', activeSessionId, content.length);
    getLog()("info", "chat", `发送消息: ${content.slice(0, 50) || "(仅附件)"}... attachments=${attachments?.length ?? 0} images=${imgCount}`);

    try {

    // 编码图片附件为 base64 ContentPart
    // 本地消息用 ContentPart[] 存储（支持图片渲染），API 请求也用 ContentPart[]
    let messageContent: string | import("../types/session").ContentPart[] = content.trim();
    let apiContent: string | import("../types/session").ContentPart[] = content.trim();
    if (attachments && attachments.length > 0) {
      const parts: import("../types/session").ContentPart[] = [
        { type: "text", text: content.trim() || "请描述这张图片" }
      ];
      // Deduplicate image attachments by content to prevent duplicate rendering
      const seenImageUrls = new Set<string>();
      for (const attachment of attachments) {
        if (attachment.type.startsWith("image/")) {
          if (seenImageUrls.has(attachment.content)) {
            getLog()("warn", "chat", `跳过重复图片附件: ${attachment.name}`);
            continue;
          }
          seenImageUrls.add(attachment.content);
          parts.push({
            type: "image_url",
            image_url: { url: attachment.content, detail: "auto" },
          });
        }
      }
      apiContent = parts;
      // 本地显示也用 ContentPart[]，让 MarkdownRenderer 正确渲染图片
      messageContent = parts;
    }
    const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: messageContent, timestamp: Date.now() };
    const assistantMsg: Message = { id: crypto.randomUUID(), role: "assistant", content: "", timestamp: Date.now() };
    const nextMessages = trimMessages([...messages, userMsg, assistantMsg]);

    // 立即同步到 sessionMessages
    const updatedSM = { ...get().sessionMessages, [activeSessionId]: nextMessages };
    saveSessionMessages(activeSessionId, nextMessages);

    set({
      messages: nextMessages,
      isStreaming: true,
      streamingContent: "",
      streamingSteps: [{
        id: `h-${Date.now()}`,
        type: "thinking",
        content: "模型正在思考...",
        source: "reasoning",
        status: "running",
      }],
      streamingSources: [],
      streamingTodos: [],
      streamingSessionId: activeSessionId,
      streamingStartTime: Date.now(),
      _pendingUsage: null,
      streamingError: null,
      sessionMessages: updatedSM,
    });
    getLog()("info", "chat", `sendMessage set messages: activeSessionId=${activeSessionId} count=${nextMessages.length}`);

    // 构建请求体：历史消息 + 设置（扁平结构，对齐后端 ChatRequest）
    // 模型优先从当前会话读取（各对话独立），回退到全局设置
    const { topK, temperature, systemPrompt, longTermMemory, maxTokens, relevanceThreshold, permissionMode, webSearchConfig } = useSettingsStore.getState();
    const { selectedKbIds } = get();
    const currentSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
    const model = currentSession?.model || useSettingsStore.getState().chatModel;
    getLog()("info", "chat", `model=${model} topK=${topK} maxTokens=${maxTokens} threshold=${relevanceThreshold} sessionId=${activeSessionId} systemPrompt="${systemPrompt?.slice(0, 50)}..."`);
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    const newMsg = { role: "user" as const, content: apiContent };
    // 若有引用回复，在被引用消息后插入一条 system 上下文，让 LLM 知道用户引用了之前的对话
    const quotedContext = quoted
      ? [{ role: "system" as const, content: `用户引用了之前的对话内容：\n> ${typeof quoted.content === "string" ? quoted.content : ""}` }]
      : [];
    const requestBody = {
      messages: [...history, ...quotedContext, newMsg],
      top_k: topK,
      relevance_threshold: relevanceThreshold > 0 ? relevanceThreshold / 100 : 0,
      temperature,
      max_tokens: maxTokens,
      system_prompt: systemPrompt,
      long_term_memory: longTermMemory || undefined,
      model: model || undefined,
      // 空数组表示搜索全部已启用知识库，非空时只搜选中的 KB
      kb_ids: selectedKbIds.length > 0 ? selectedKbIds : undefined,
      // 只发送非图片附件（图片已在 messages 的 ContentPart[] 中）
      // 后端会从 attachments 提取图片再拼到消息里，导致重复
      attachments: attachments && attachments.length > 0
        ? attachments.filter((a) => !a.type.startsWith("image/"))
        : undefined,
      session_id: activeSessionId || undefined,
      // Agent mode (v1-T6): enable LangGraph ReAct Agent by default
      use_agent: true,
      permission_mode: permissionMode,
      tool_keys: {
        tavily: webSearchConfig.apiKey || "",
        tavily_base_url: webSearchConfig.baseUrl || "",
        // 传递视觉/图像生成模型凭证，供 Agent 的 vision_analysis/view_image/image_generation 工具使用
        vision_model: useSettingsStore.getState().resolveVisionCreds().model || "",
        vision_base_url: useSettingsStore.getState().resolveVisionCreds().baseUrl || "",
        vision_api_key: useSettingsStore.getState().resolveVisionCreds().apiKey || "",
        image_model: useSettingsStore.getState().resolveImageCreds().model || "",
        image_base_url: useSettingsStore.getState().resolveImageCreds().baseUrl || "",
        image_api_key: useSettingsStore.getState().resolveImageCreds().apiKey || "",
      },
    };

    const startTime = Date.now();
    // ★ 关键：捕获发起请求时的 sessionId，回调中始终使用此 ID
    const requestSessionId = activeSessionId;
    // v2-T4: cache payload for the resume API (HITL confirm flow)
    set({ _lastAgentPayload: requestBody as Record<string, unknown> });

    const controller = new AbortController();
    set({ currentSseRequest: controller });

    apiSSE("chat", requestBody, {
      onEvent: (event) => {
        if (event.type === "error") {
          const errPayload = event as { type: "error"; code?: string; message: string; detail?: string };
          const code = errPayload.code ?? "UNKNOWN";
          getLog()("error", "chat", `SSE 错误: ${errPayload.message}`);
          // MODEL_NOT_FOUND: backend reports the requested model is unavailable.
          // Provide a friendly fallback message when backend omits one.
          const friendlyMessage = code === "MODEL_NOT_FOUND" && !errPayload.message
            ? "模型不存在，请在设置中检查模型名"
            : (errPayload.message ?? "未知错误");
          set({
            streamingError: {
              code,
              message: friendlyMessage,
              detail: errPayload.detail ?? "",
            },
          });
          get().stopStreaming(friendlyMessage);
          return;
        }
        const sse = event;
        // ★ 始终写入发起请求时的会话，而非当前活跃会话
        set((s) => {
          const sid = requestSessionId;
          const isActive = s.activeSessionId === sid;
          const sessionMsgs = s.sessionMessages[sid] ?? [];
          const currentMsgs = isActive ? s.messages : sessionMsgs;

          switch (sse.type) {
            case "thinking": {
              const thinkContent = sse.content ?? "";
              const thinkSource = sse.source;
              getLog()("debug", "chat", `[thinking] source=${thinkSource} content="${thinkContent.slice(0, 30)}"`);

              // 构建 step
              const steps = [...(isActive ? s.streamingSteps : (currentMsgs[currentMsgs.length - 1]?.activity?.steps || []))];
              const lastStep = steps[steps.length - 1];

              // 占位 thinking 卡片（发送消息时预置）应被首个真实 thinking 事件替换，避免双卡。
              if (lastStep && lastStep.type === "thinking" && lastStep.content === "模型正在思考...") {
                steps[steps.length - 1] = { ...lastStep, content: thinkContent, source: thinkSource };
              } else if (thinkSource === "reasoning") {
                if (lastStep && lastStep.type === "thinking" && lastStep.source === "llm") {
                  steps[steps.length - 1] = { ...lastStep, content: thinkContent, source: "reasoning" };
                } else if (lastStep && lastStep.type === "thinking" && lastStep.source === "reasoning") {
                  steps[steps.length - 1] = { ...lastStep, content: (lastStep.content || "") + thinkContent };
                } else {
                  // Source switched: close previous thinking step before creating a new one
                  if (lastStep && lastStep.type === "thinking") {
                    steps[steps.length - 1] = { ...lastStep, status: "done" };
                  }
                  steps.push({ type: "thinking", id: `h-${Date.now()}`, content: thinkContent, source: "reasoning" });
                }
              } else if (lastStep && lastStep.type === "thinking" && lastStep.source === thinkSource) {
                steps[steps.length - 1] = { ...lastStep, content: (lastStep.content || "") + thinkContent };
              } else {
                // Source switched: close previous thinking step before creating a new one
                if (lastStep && lastStep.type === "thinking") {
                  steps[steps.length - 1] = { ...lastStep, status: "done" };
                }
                steps.push({ type: "thinking", id: `h-${Date.now()}`, content: thinkContent, source: thinkSource });
              }

              if (isActive) {
                return { streamingSteps: steps };
              } else {
                // 后台：直接更新 sessionMessages 中的 assistant 消息的 activity
                const msgs = [...currentMsgs];
                const last = msgs[msgs.length - 1];
                if (last?.role === "assistant") {
                  msgs[msgs.length - 1] = { ...last, activity: { durationMs: 0, steps, status: "running" } };
                }
                const newSM = { ...s.sessionMessages, [sid]: msgs };
                saveSessionMessages(sid, msgs);
                return { sessionMessages: newSM };
              }
            }

            case "text": {
              const chunk = sse.content ?? "";
              const msgs = [...currentMsgs];
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant") {
                let newContent: string | ContentPart[];
                let newStreamingContent: string;

                if (typeof last.content === "string") {
                  // Original string path: keep streamingContent in sync with message content.
                  const prevContent = isActive ? s.streamingContent : last.content;
                  const breakPrefix = _needsParagraphBreak.get(sid) && prevContent && !prevContent.endsWith("\n") ? "\n\n" : "";
                  _needsParagraphBreak.set(sid, false);
                  newStreamingContent = prevContent + breakPrefix + chunk;
                  newContent = newStreamingContent;
                } else {
                  // ContentPart[] path (e.g. after image_generation appends an ImagePart):
                  // append text chunks to the last TextPart so the image is preserved.
                  const parts: ContentPart[] = [...last.content];
                  const lastTextIdx = parts.map((p) => p.type).lastIndexOf("text");
                  const breakPrefix = _needsParagraphBreak.get(sid) ? "\n\n" : "";
                  _needsParagraphBreak.set(sid, false);
                  if (lastTextIdx >= 0) {
                    const lastText = parts[lastTextIdx] as Extract<ContentPart, { type: "text" }>;
                    const prefix = lastText.text && !lastText.text.endsWith("\n") ? breakPrefix : "";
                    parts[lastTextIdx] = { ...lastText, text: lastText.text + prefix + chunk };
                  } else {
                    parts.push({ type: "text", text: breakPrefix + chunk });
                  }
                  newContent = parts;
                  newStreamingContent = parts
                    .filter((p): p is Extract<ContentPart, { type: "text" }> => p.type === "text")
                    .map((p) => p.text)
                    .join("\n\n");
                }

                // Close any open reasoning/llm thinking step when answer text begins.
                // Without this, the thinking card stays expanded while text streams below it.
                let updatedSteps = s.streamingSteps;
                if (isActive) {
                  const steps = [...s.streamingSteps];
                  const lastStep = steps[steps.length - 1];
                  if (lastStep && lastStep.type === "thinking" && lastStep.status !== "done") {
                    steps[steps.length - 1] = { ...lastStep, status: "done" };
                    updatedSteps = steps;
                  }
                  msgs[msgs.length - 1] = { ...last, content: newContent };
                } else if (last.activity?.steps?.length) {
                  const steps = [...last.activity.steps];
                  const lastStep = steps[steps.length - 1];
                  if (lastStep && lastStep.type === "thinking" && lastStep.status !== "done") {
                    steps[steps.length - 1] = { ...lastStep, status: "done" };
                  }
                  msgs[msgs.length - 1] = { ...last, content: newContent, activity: { ...last.activity, steps } };
                } else {
                  msgs[msgs.length - 1] = { ...last, content: newContent };
                }

                const newSM = { ...s.sessionMessages, [sid]: msgs };
                // NOTE: saveSessionMessages is intentionally NOT called here.
                // The store subscribe (line ~927) handles persistence via 500ms debounce
                // during streaming. Calling it per-token caused main-thread blocking.
                if (isActive) {
                  return { streamingContent: newStreamingContent, messages: msgs, sessionMessages: newSM, streamingSteps: updatedSteps, isCompressing: false };
                } else {
                  return { sessionMessages: newSM, isCompressing: false };
                }
              }
              return { isCompressing: false };
            }

            case "context_compressing": {
              // Context being compressed — show status banner; cleared on next text/done.
              return {
                isCompressing: true,
                compressingMessage: sse.message || "正在压缩上下文...",
              };
            }

            case "heartbeat": {
              // Backend keepalive: update timestamp only, no UI state change.
              _lastHeartbeatAt = Date.now();
              return {};
            }

            case "tool_call": {
              // T6 协作点：通知工作台 bridge 新 call_id（重置 override）
              console.info('[ChatStore] tool_call tool=%s call_id=%s', sse.tool, sse.call_id);
              if (sse.call_id) {
                _lastToolCallId.set(sid, sse.call_id);
              }
              handleToolCallEvent(sse);
              // Agent decided to call a tool; create a pending step.
              // Close out the previous rag thinking step (same as the tool case).
              const stepsBeforeTool = [...(isActive ? s.streamingSteps : (currentMsgs[currentMsgs.length - 1]?.activity?.steps || []))];
              const lastBeforeTool = stepsBeforeTool[stepsBeforeTool.length - 1];
              if (lastBeforeTool && lastBeforeTool.type === "thinking" && lastBeforeTool.source === "rag" && lastBeforeTool.status !== "done") {
                stepsBeforeTool[stepsBeforeTool.length - 1] = { ...lastBeforeTool, content: "检索完成", status: "done" };
              }

              const newStep: ActivityStep = {
                type: "tool",
                id: sse.call_id || `t-${Date.now()}`,
                name: sse.tool,
                args: typeof sse.args === "string" ? sse.args : JSON.stringify(sse.args ?? {}),
                status: "pending",
                call_id: sse.call_id,
                risk_level: sse.risk_level,
                decision_source: sse.decision_source,
                startTime: Date.now(),
              };

              if (isActive) {
                return { streamingSteps: [...stepsBeforeTool, newStep] };
              } else {
                const msgs = [...currentMsgs];
                const last = msgs[msgs.length - 1];
                if (last?.role === "assistant") {
                  const existingSteps = last.activity?.steps || [];
                  let stepsToUpdate = [...existingSteps];
                  const lastExisting = stepsToUpdate[stepsToUpdate.length - 1];
                  if (lastExisting && lastExisting.type === "thinking" && lastExisting.source === "rag" && lastExisting.status !== "done") {
                    stepsToUpdate[stepsToUpdate.length - 1] = { ...lastExisting, content: "检索完成", status: "done" };
                  }
                  msgs[msgs.length - 1] = { ...last, activity: { durationMs: 0, steps: [...stepsToUpdate, newStep], status: "running" } };
                }
                const newSM = { ...s.sessionMessages, [sid]: msgs };
                saveSessionMessages(sid, msgs);
                return { sessionMessages: newSM };
              }
            }

            case "tool_result": {
              // T6 协作点：在工作台 bridge 路由 render_data（在 resultText 压平之前）
              console.info('[ChatStore] tool_result call_id=%s duration=%sms', sse.call_id, sse.duration);
              handleToolResultEvent(sse);
              // Tool execution finished; update the matching step by call_id.
              const resultText = _flattenToolResult(sse.result);
              const newStatus = sse.success === false ? "error" : "done";
              // image_generation: extract image_base64/image_url and append as ImagePart
              const imagePart = sse.tool === "image_generation" ? _extractImagePart(sse.result) : null;
              // File-editing tools: extract file_path for "查看 diff" button
              const filePath = _extractFilePath(sse.tool, sse.result);

              if (isActive) {
                const steps = s.streamingSteps.map((step) =>
                  step.call_id === sse.call_id
                    ? { ...step, status: newStatus, result: resultText, duration: sse.duration, file_path: filePath ?? step.file_path }
                    : step
                );
                _needsParagraphBreak.set(sid, true);
                if (imagePart) {
                  const msgs = [...currentMsgs];
                  const last = msgs[msgs.length - 1];
                  if (last?.role === "assistant") {
                    const newContent = _appendImageToContent(last.content, imagePart);
                    msgs[msgs.length - 1] = { ...last, content: newContent };
                    return {
                      streamingSteps: steps,
                      messages: msgs,
                      sessionMessages: { ...s.sessionMessages, [sid]: msgs },
                    };
                  }
                }
                return { streamingSteps: steps };
              } else {
                const msgs = [...currentMsgs];
                const last = msgs[msgs.length - 1];
                if (last?.role === "assistant" && last.activity) {
                  const steps = last.activity.steps.map((step) =>
                    step.call_id === sse.call_id
                      ? { ...step, status: newStatus, result: resultText, duration: sse.duration, file_path: filePath ?? step.file_path }
                      : step
                  );
                  msgs[msgs.length - 1] = { ...last, activity: { ...last.activity, steps } };
                }
                if (imagePart && last?.role === "assistant") {
                  msgs[msgs.length - 1] = {
                    ...msgs[msgs.length - 1],
                    content: _appendImageToContent(msgs[msgs.length - 1].content, imagePart),
                  };
                }
                const newSM = { ...s.sessionMessages, [sid]: msgs };
                saveSessionMessages(sid, msgs);
                _needsParagraphBreak.set(sid, true);
                return { sessionMessages: newSM };
              }
            }

            case "source": {
              const newSource: SourceRef = {
                id: sse.id ?? `src-${Date.now()}`,
                title: sse.title ?? "未知来源",
                doc: sse.doc ?? "",
                page: sse.page ?? 0,
                chunk_index: sse.chunk_index,
                page_start: sse.page_start,
                page_end: sse.page_end,
                section_title: sse.section_title,
                source_url: sse.source_url,
                category: sse.category,
                chunk_method: sse.chunk_method,
                score: sse.score ?? 0,
                score_percentage: sse.score_percentage,
                relevance_level: sse.relevance_level,
                citation: sse.citation,
                excerpt: sse.excerpt ?? "",
                kb_id: sse.kb_id,
                kb_name: sse.kb_name,
                small_chunk_id: sse.small_chunk_id,
                big_chunk_id: sse.big_chunk_id,
                small_chunk_text: sse.small_chunk_text,
                messageId: currentMsgs[currentMsgs.length - 1]?.id,
              };
              const msgs = [...currentMsgs];
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant") {
                const srcs = [...(last.sources || []), newSource];
                msgs[msgs.length - 1] = { ...last, sources: srcs };
              }
              const newSM = { ...s.sessionMessages, [sid]: msgs };
              saveSessionMessages(sid, msgs);
              if (isActive) {
                return { streamingSources: [...s.streamingSources, newSource], sources: [...s.streamingSources, newSource], messages: msgs, sessionMessages: newSM };
              } else {
                return { sessionMessages: newSM };
              }
            }

            case "todo_update": {
              const todos = (Array.isArray(sse.todos) ? (sse.todos as TodoItem[]) : []).map((t, idx) => ({
                ...t,
                id: t.id || `todo-${idx}`,
              }));
              const msgs = [...currentMsgs];
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant") {
                msgs[msgs.length - 1] = { ...last, todos };
              }
              const newSM = { ...s.sessionMessages, [sid]: msgs };
              saveSessionMessages(sid, msgs);
              if (isActive) {
                return { streamingTodos: todos, messages: msgs, sessionMessages: newSM };
              }
              return { sessionMessages: newSM };
            }

            case "done": {
              if (!("usage" in sse)) return { isCompressing: false };
              const usage = sse.usage;
              if (usage) {
                getLog()("ok", "chat", `Token usage: prompt=${usage.prompt_tokens} completion=${usage.completion_tokens} total=${usage.total_tokens}`);
                return { _pendingUsage: { promptTokens: usage.prompt_tokens || 0, completionTokens: usage.completion_tokens || 0, totalTokens: usage.total_tokens || 0 }, isCompressing: false };
              }
              return { isCompressing: false };
            }

            case "compile_log": {
              // T6 协作点：Agent build/flash 工具的实时编译日志 → FlashPane
              handleCompileLogEvent(_lastToolCallId.get(sid) ?? "", sse.line);
              return {};
            }

            case "progress": {
              // T6 协作点：Agent build/flash 工具的实时进度 → FlashPane
              handleProgressEvent(_lastToolCallId.get(sid) ?? "", sse.percent ?? 0, sse.message);
              return {};
            }

            case "tool_confirm_required": {
              // v2-T4: Agent paused for HITL — show ConfirmDialog
              return {
                pendingConfirm: {
                  calls: (sse.calls ?? []) as PendingConfirm["calls"],
                  count: sse.count ?? (sse.calls?.length ?? 0),
                },
              };
            }

            default:
              return {};
          }
        });
      },
      onDone: () => {
        const durationMs = Date.now() - startTime;
        const usage = get()._pendingUsage;
        const finalMsgId = get().messages[get().messages.length - 1]?.id ?? '';
        console.info('[ChatStore] stream_done session=%s msg_id=%s', requestSessionId, finalMsgId);
        getLog()("ok", "chat", `回答完成 (${(durationMs / 1000).toFixed(1)}s)${usage ? ` tokens=${usage.totalTokens}` : ''}`);

        // 提前计算 metaUpdate 参数；updateSessionMeta 延迟到 persistLastTurn 完成后执行，
        // 避免 updateSessionMeta 触发 AppRoot useEffect → fetchMessages 在 persistLastTurn
        // 完成前用后端旧数据覆盖 messages（流式内容消失 bug 的根因）。
        let pendingMetaUpdate: { msgCount: number; preview: string; title?: string } | null = null;

        set((s) => {
          const sid = requestSessionId;
          const isActive = s.activeSessionId === sid;
          const sessionMsgs = s.sessionMessages[sid] ?? [];
          const currentMsgs = isActive ? s.messages : sessionMsgs;
          // SubTask 8.5: 清理 backgroundSseRequests 对应条目（无论活跃或后台）
          let newBg = s.backgroundSseRequests;
          if (s.backgroundSseRequests.has(sid)) {
            newBg = new Map(s.backgroundSseRequests);
            newBg.delete(sid);
          }

          // 活跃会话：用 streamingSteps 构建 activity
          // 非活跃会话：从 sessionMessages 中的已有 activity 补充 durationMs 和 usage
          const finalMessages = currentMsgs.map((m, i) => {
            if (i !== currentMsgs.length - 1 || m.role !== "assistant") return m;
            // FIX-1.1: 兜底重合并 — 若 content 为空但 streamingContent 非空，用 streamingContent 填充
            // （仅活跃会话有效，s.streamingContent 属于当前活跃会话）
            let content = m.content;
            if (!_hasContent(content) && isActive && s.streamingContent) {
              content = s.streamingContent;
            }
            // FIX-3.1: 修复 pending steps 耗时无限累计（活跃用 streamingSteps，后台用 m.activity.steps）
            const baseActivity = isActive && s.streamingSteps.length > 0
              ? { durationMs, steps: _finalizePendingSteps(s.streamingSteps), status: "done" as const }
              : (!isActive && m.activity
                  ? { ...m.activity, durationMs, steps: _finalizePendingSteps(m.activity.steps || []), status: "done" as const }
                  : null);
            return {
              ...m,
              ...(content !== m.content ? { content } : {}),
              ...(baseActivity ? { activity: baseActivity } : {}),
              ...(s.streamingTodos.length > 0 ? { todos: s.streamingTodos } : {}),
              ...(s._pendingUsage ? { usage: s._pendingUsage } : {}),
            };
          });
          const updatedSM = { ...s.sessionMessages, [sid]: finalMessages };
          saveSessionMessages(sid, finalMessages);

          // 更新会话元数据
          const msgCount = finalMessages.filter((m) => m.role === "user" || (m.role === "assistant" && m.content)).length;
          const lastUserMsg = finalMessages.filter((m) => m.role === "user").pop();
          // 从 ContentPart[] 或 string 中提取文本用于 preview/title
          const extractText = (content: string | import("../types/session").ContentPart[]): string => {
            if (typeof content === "string") return content;
            return content.filter((p) => p.type === "text").map((p) => p.text).join(" ");
          };
          const userText = lastUserMsg ? extractText(lastUserMsg.content) : "";
          const preview = userText.slice(0, 60) || "(图片)";
          const sessionStore = useSessionStore.getState();
          const currentSession = sessionStore.sessions.find((x) => x.id === sid);
          const title = currentSession?.title?.startsWith("新对话") && userText
            ? userText.slice(0, 30).replace(/\n/g, " ")
            : undefined;
          // 不在此处调用 updateSessionMeta，避免触发 AppRoot useEffect → fetchMessages 竞态
          pendingMetaUpdate = {
            msgCount,
            preview,
            ...(title ? { title } : {}),
          };

          // 只有当前活跃会话是请求会话时，才修改 isStreaming/currentSseRequest
          if (isActive) {
            return {
              isStreaming: false,
              streamingSessionId: null,
              streamingStartTime: null,
              currentSseRequest: null,
              backgroundSseRequests: newBg,
              _pendingUsage: null,
              isCompressing: false,
              // Reset streaming buffers so the next send starts clean
              streamingSteps: [],
              streamingContent: "",
              streamingSources: [],
      streamingTodos: [],
              sessionMessages: updatedSM,
              messages: finalMessages,
            };
          } else {
            // 后台会话完成：只更新 sessionMessages
            // SubTask 8.5: 仍需清理 backgroundSseRequests 对应条目
            // FIX-6.1: 若完成的就是当前记录的流式会话（streamingSessionId === sid），
            // 即便不是活跃会话也应清空 isStreaming/streamingSessionId/currentSseRequest，
            // 否则发送按钮会卡在 isStreaming=true 状态（用户切走后又切回的场景）
            const shouldClearStreaming = s.streamingSessionId === sid;
            return {
              sessionMessages: updatedSM,
              backgroundSseRequests: newBg,
              ...(shouldClearStreaming ? {
                isStreaming: false,
                streamingSessionId: null,
                streamingStartTime: null,
                currentSseRequest: null,
                streamingContent: "",
                streamingSteps: [],
                streamingSources: [],
                streamingTodos: [],
                _pendingUsage: null,
              } : {}),
            };
          }
        });
        // 先持久化到后端，完成后再更新会话元数据（消除 fetchMessages 竞态）
        void get().persistLastTurn(requestSessionId).then(() => {
          if (pendingMetaUpdate) {
            useSessionStore.getState().updateSessionMeta(requestSessionId, pendingMetaUpdate);
          }
          // 多 tab 同步：通知其他 tab 此会话消息已变化
          post('messages_changed', requestSessionId);
        });
      },
      onError: (err) => {
        console.warn('[ChatStore] stream_error session=%s error=%s', requestSessionId, err.message);
        getLog()("error", "chat", `SSE 连接错误: ${err.message}`);
        // SubTask 8.5: 清理 backgroundSseRequests 对应条目（后台流错误也要清理）
        set((s) => {
          if (!s.backgroundSseRequests.has(requestSessionId)) return {};
          const newBg = new Map(s.backgroundSseRequests);
          newBg.delete(requestSessionId);
          return { backgroundSseRequests: newBg };
        });
        get().stopStreaming(err.message);
      },
    }, controller);
    } catch (err) {
      // 同步异常（如构建请求体失败）也要让用户看到 ErrorBlock，不能只 console.warn
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("error", "chat", `sendMessage 异常: ${errMsg}`);
      set({
        isStreaming: false,
        streamingError: { code: "INTERNAL_ERROR", message: errMsg, detail: "" },
      });
    }
  },

  stopStreaming: (errorMessage?: string) => {
    // 捕获当前流式会话 ID，用于 set 之后持久化（set 内部会清空 streamingSessionId）
    const sidToPersist = get().streamingSessionId;
    set((state) => {
      // 幂等检查：如果已经停止过（isStreaming=false 且无 SSE 请求），直接返回
      if (!state.isStreaming && !state.streamingSessionId && !state.currentSseRequest) {
        return {};
      }
      const { currentSseRequest, messages, streamingContent, streamingSteps, streamingSessionId, activeSessionId, sessionMessages } = state;
      getLog()("warn", "chat", errorMessage ? "流式输出因错误停止" : "用户手动停止流式输出");
      if (currentSseRequest) currentSseRequest.abort();

      // 防御：streamingSessionId 已丢失说明状态已过期（如切换会话后被清空），
      // 跳过写入避免误伤当前会话
      if (!streamingSessionId) {
        getLog()("warn", "chat", "streamingSessionId 已丢失，跳过 stopStreaming 写入");
        return {
          isStreaming: false,
          isCompressing: false,
          streamingContent: "",
          streamingSteps: [],
          streamingSources: [],
      streamingTodos: [],
          streamingSessionId: null,
          streamingStartTime: null,
          currentSseRequest: null,
        };
      }

      const sid = streamingSessionId;
      const isActive = activeSessionId === sid;
      const targetMsgs = isActive ? messages : (sessionMessages[sid] ?? []);

      // 优先取已有内容，避免 streamingContent 为空时用纯错误消息覆盖
      const lastMsg = targetMsgs[targetMsgs.length - 1];
      const existingContent = isActive
        ? streamingContent
        : (typeof lastMsg?.content === "string" ? lastMsg.content : "");

      const finalContent = errorMessage
        ? (existingContent ? `${existingContent}\n\n❌ ${errorMessage}` : `❌ ${errorMessage}`)
        : (streamingContent || existingContent);

      const finalTargetMsgs = targetMsgs.map((m, i) =>
        i === targetMsgs.length - 1 && m.role === "assistant" && (finalContent || errorMessage)
          ? {
              ...m,
              content: finalContent || m.content,
              // 出错时也要归一化 activity：置 status 让 ActivityBlock 的 activityDone 判 true，
              // 避免 pending step 残留导致 setInterval 继续跑（FIX-3.2 扩展到 error 路径）
              ...(streamingSteps.length > 0
                ? { activity: { durationMs: 0, steps: _finalizePendingSteps(streamingSteps), status: (errorMessage ? "error" : "done") as "error" | "done" } }
                : (errorMessage && m.activity
                    ? { activity: { ...m.activity, durationMs: 0, steps: _finalizePendingSteps(m.activity.steps || []), status: "error" as const } }
                    : {})),
              ...(state.streamingTodos.length > 0 ? { todos: state.streamingTodos } : {}),
            }
          : m
      );
      const newSM = { ...sessionMessages, [sid]: finalTargetMsgs };
      saveSessionMessages(sid, finalTargetMsgs);

      return {
        messages: isActive ? finalTargetMsgs : messages,
        sessionMessages: newSM,
        isStreaming: false,
        isCompressing: false,
        streamingContent: "",
        streamingSteps: [],
        streamingSources: [],
        streamingTodos: [],
        streamingSessionId: null,
        streamingStartTime: null,
        currentSseRequest: null,
      };
    });

    // 流式停止（用户主动或错误）：将最后一轮 user+assistant 消息持久化到后端
    if (sidToPersist) {
      void get().persistLastTurn(sidToPersist).then(() => {
        // 多 tab 同步：通知其他 tab 此会话消息已变化
        post('messages_changed', sidToPersist);
      });
    }
  },

  retryMessage: (msgId) => {
    const { messages, isStreaming, activeSessionId } = get();
    if (isStreaming) return; // 流式输出中不允许重试
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    console.info('[ChatStore] retry session=%s msg_id=%s', activeSessionId, msgId);
    getLog()("info", "chat", `重试消息: ${msgId}`);

    // 找到对应的用户消息
    let userIdx = -1;
    for (let i = idx; i >= 0; i--) {
      if (messages[i].role === "user") { userIdx = i; break; }
    }
    if (userIdx < 0) return;
    const userContent = messages[userIdx].content;

    // 从 ContentPart[] 中提取文本和图片附件，然后截断 + 重发
    if (typeof userContent === "string") {
      void truncateAndResend(activeSessionId, userIdx, userContent);
    } else {
      // ContentPart[]: 提取文本和图片
      const text = userContent
        .filter((p): p is Extract<import("../types/session").ContentPart, { type: "text" }> => p.type === "text")
        .map((p) => p.text)
        .join("\n");
      const imageAttachments: import("../types/session").Attachment[] = userContent
        .filter((p): p is Extract<import("../types/session").ContentPart, { type: "image_url" }> => p.type === "image_url")
        .map((p) => ({
          id: `retry-img-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          name: "image.png",
          type: "image/png",
          content: p.image_url.url,
        }));
      void truncateAndResend(
        activeSessionId,
        userIdx,
        text || "",
        imageAttachments.length > 0 ? imageAttachments : undefined,
      );
    }
  },

  editAndResend: (msgId, newContent) => {
    const { messages, isStreaming, activeSessionId } = get();
    if (isStreaming || !newContent.trim()) return; // 流式输出中不允许编辑
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    getLog()("info", "chat", `编辑重发: ${msgId}`);

    void truncateAndResend(activeSessionId, idx, newContent);
  },

  branchThread: (msgId) => {
    const { activeSessionId, messages } = get();
    if (!activeSessionId) return;
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    getLog()("info", "chat", `分支线程: ${msgId}`);

    const branchMsgs = messages.slice(0, idx + 1).map((m, i) =>
      i === idx ? { ...m, parentId: msgId } : { ...m }
    );

    // 通过后端 API 创建带分支信息的新会话
    const currentSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
    const model = currentSession?.model || useSettingsStore.getState().chatModel;
    const project = useSessionStore.getState().activeProject === "all" ? "" : useSessionStore.getState().activeProject;
    // Branch inherits the parent session's context window budget
    const contextWindow = currentSession?.contextWindow ?? CONTEXT_WINDOW_256K;

    apiPost<{ id: string }>("sessions", {
      title: "分支对话",
      model: model || DEFAULT_MODEL,
      project,
      branch_from_session_id: activeSessionId,
      branch_from_message_id: msgId,
      context_window: contextWindow,
    }).then((res) => {
      const sid = res?.id || `s${Date.now()}`;
      getLog()("ok", "chat", `分支会话已创建: ${sid}`);

      // 创建本地 Session 对象
      const now = Date.now();
      const session: Session = {
        id: sid,
        title: "分支对话",
        preview: "",
        model: model || DEFAULT_MODEL,
        createdAt: now,
        project,
        pinned: false,
        msgCount: branchMsgs.length,
        branchFromSessionId: activeSessionId,
        branchFromMessageId: msgId,
        contextWindow,
      };

      // 更新 sessionStore
      useSessionStore.setState((s) => {
        const updated = { sessions: [session, ...s.sessions] };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });

      // 设置分支消息到新会话
      set((s) => {
        // FIX-6.2: 若原会话正在流式，清空全局流式状态，
        // 让原 SSE 在后台继续写入 sessionMessages，避免 isStreaming 卡 true
        const shouldClearStreaming = s.isStreaming && s.streamingSessionId === activeSessionId;
        return {
          sessionMessages: {
            ...s.sessionMessages,
            [sid]: branchMsgs,
          },
          messages: branchMsgs,
          activeSessionId: sid,
          ...(shouldClearStreaming ? {
            isStreaming: false,
            streamingSessionId: null,
            streamingStartTime: null,
            currentSseRequest: null,
            streamingContent: "",
            streamingSteps: [],
            streamingSources: [],
            streamingTodos: [],
          } : {}),
        };
      });
      saveSessionMessages(sid, branchMsgs);

      // 切换到新会话
      saveToStorage("activeSession", sid);
    }).catch(() => {
      getLog()("warn", "chat", "分支会话仅保存在本地");
      // 回退：纯本地创建
      const sid = `s${Date.now()}`;
      const now = Date.now();
      const session: Session = {
        id: sid,
        title: "分支对话",
        preview: "",
        model: model || DEFAULT_MODEL,
        createdAt: now,
        project,
        pinned: false,
        msgCount: branchMsgs.length,
        branchFromSessionId: activeSessionId,
        branchFromMessageId: msgId,
        contextWindow,
      };

      useSessionStore.setState((s) => {
        const updated = { sessions: [session, ...s.sessions] };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });

      set((s) => {
        // FIX-6.2: 若原会话正在流式，清空全局流式状态，
        // 让原 SSE 在后台继续写入 sessionMessages，避免 isStreaming 卡 true
        const shouldClearStreaming = s.isStreaming && s.streamingSessionId === activeSessionId;
        return {
          sessionMessages: {
            ...s.sessionMessages,
            [sid]: branchMsgs,
          },
          messages: branchMsgs,
          activeSessionId: sid,
          ...(shouldClearStreaming ? {
            isStreaming: false,
            streamingSessionId: null,
            streamingStartTime: null,
            currentSseRequest: null,
            streamingContent: "",
            streamingSteps: [],
            streamingSources: [],
            streamingTodos: [],
          } : {}),
        };
      });
      saveSessionMessages(sid, branchMsgs);

      saveToStorage("activeSession", sid);
    });
  },

  pushCodeToWorkbench: (code, name) => {
    useAppStore.getState().setRightPanelOpen(true);
    useAppStore.getState().setRightMode("workbench");
    useAppStore.getState().setWbTab("preview");
    useAppStore.getState().addPreviewTab({
      id: `preview-${Date.now()}`,
      label: `${(name || "code").slice(0, 18)}.cpp`,
      code,
      language: "cpp",
    });
  },

  quoteMessage: (msgId) => {
    const target = get().messages.find((msg) => msg.id === msgId) ?? null;
    useAppStore.getState().setQuotedMsg(target);
  },

  exportConversation: (format) => {
    getLog()("info", "chat", `导出对话 (${format})`);
    const { messages } = get();
    let content: string;
    let filename: string;
    let mimeType: string;

    if (format === "markdown") {
      content = messages.map((m) => {
        const role = m.role === "user" ? "**用户**" : "**Assistant**";
        // Handle ContentPart[] content (images/multimodal)
        const text = typeof m.content === "string"
          ? m.content
          : Array.isArray(m.content)
            ? m.content.map((p) => {
                if (p.type === "text") return p.text;
                if (p.type === "image_url") return `![Image](${p.image_url.url})`;
                return "";
              }).join("\n\n")
            : "";
        return `${role}\n\n${text}\n\n---`;
      }).join("\n\n");
      filename = `chat-export-${Date.now()}.md`;
      mimeType = "text/markdown";
    } else {
      content = JSON.stringify(messages, null, 2);
      filename = `chat-export-${Date.now()}.json`;
      mimeType = "application/json";
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },

  showStats: () => set({ statsOpen: true }),
  hideStats: () => set({ statsOpen: false }),
};
});

// 多 tab 同步：监听其他 tab 的消息变化和会话删除
// 自己 tab 发的事件不会收到（BroadcastChannel 只跨 tab），不会重复刷新
on('messages_changed', (payload) => {
  if (!payload) return;
  const { activeSessionId, fetchMessages } = useChatStore.getState();
  if (payload === activeSessionId) {
    void fetchMessages(payload);
  }
});

on('session_deleted', (payload) => {
  if (!payload) return;
  const { activeSessionId, setActiveSession } = useChatStore.getState();
  if (payload !== activeSessionId) return;
  // 列表异步重载可能未完成，过滤掉被删除的会话避免切到已删除项
  const remaining = useSessionStore.getState().sessions.filter((s) => s.id !== payload);
  if (remaining.length > 0) {
    setActiveSession(remaining[0].id);
  }
});

// 自动持久化 sessionMessages（debounce，避免 SSE 期间频繁写入）
let _persistTimer: ReturnType<typeof setTimeout> | null = null;
let _pendingShards: Map<string, Message[]> = new Map();

function flushPendingShards() {
  for (const [sid, msgs] of _pendingShards) {
    saveSessionMessages(sid, msgs);
  }
  _pendingShards.clear();
  _persistTimer = null;
}

function scheduleShardSave(sessionId: string, msgs: Message[]) {
  _pendingShards.set(sessionId, msgs);
  if (_persistTimer) clearTimeout(_persistTimer);
  _persistTimer = setTimeout(flushPendingShards, 500);
}

useChatStore.subscribe((state, prevState) => {
  if (state.sessionMessages !== prevState.sessionMessages) {
    // 找出变化的 session
    const allKeys = new Set([...Object.keys(state.sessionMessages), ...Object.keys(prevState.sessionMessages)]);
    for (const sid of allKeys) {
      if (state.sessionMessages[sid] !== prevState.sessionMessages[sid]) {
        const msgs = state.sessionMessages[sid];
        if (msgs) {
          if (state.isStreaming) {
            scheduleShardSave(sid, msgs);
          } else {
            saveSessionMessages(sid, msgs);
          }
        }
      }
    }
  }
});

// ─── Mock 数据注入（控制台调用 window.__loadMockData() 测试 token 统计） ───
export function loadMockData() {
  const now = Date.now();
  const mockSessionId = "mock-test-session";

  const mockMessages: Message[] = [
    // 第 1 轮对话：简单问答
    {
      id: "mock-msg-1",
      role: "user",
      content: "STM32F103 的主频是多少？",
      timestamp: now - 3600_000,
    },
    {
      id: "mock-msg-2",
      role: "assistant",
      content: "STM32F103 的主频为 72MHz，基于 ARM Cortex-M3 内核，具有丰富的外设和较高的性价比。",
      timestamp: now - 3590_000,
      usage: { promptTokens: 28, completionTokens: 45, totalTokens: 73 },
      activity: {
        durationMs: 1200,
        steps: [
          { type: "thinking", id: "h-1", content: "正在检索知识库...", source: "rag" },
          { type: "thinking", id: "h-2", content: "STM32F103 主频 72MHz，Cortex-M3 内核", source: "llm" },
        ],
      },
      sources: [
        { id: "src-1", title: "STM32F103 数据手册", doc: "stm32f103-datasheet.pdf", page: 12, score: 0.95, excerpt: "STM32F103 主频 72MHz..." },
      ],
    },
    // 第 2 轮对话：推理模型
    {
      id: "mock-msg-3",
      role: "user",
      content: "帮我分析一下 I2C 通信失败的可能原因，从硬件和软件两个角度分析",
      timestamp: now - 1800_000,
    },
    {
      id: "mock-msg-4",
      role: "assistant",
      content: "## I2C 通信失败原因分析\n\n### 硬件原因\n1. 上拉电阻缺失或阻值不当\n2. 总线信号线短路或断路\n3. 供电电压不稳定\n\n### 软件原因\n1. I2C 地址配置错误\n2. 时序配置不匹配\n3. 中断优先级冲突导致超时",
      timestamp: now - 1780_000,
      usage: { promptTokens: 56, completionTokens: 128, totalTokens: 184 },
      activity: {
        durationMs: 8500,
        steps: [
          { type: "thinking", id: "h-3", content: "正在检索知识库...", source: "rag" },
          { type: "thinking", id: "h-4", content: "知识库中未找到匹配片段，将直接回答。", source: "rag" },
          { type: "thinking", id: "h-5", content: "正在生成回答...", source: "llm" },
          {
            type: "thinking",
            id: "h-6",
            content: "I2C通信失败需要从硬件和软件两方面分析。硬件方面最常见的是上拉电阻问题，标准模式要求4.7kΩ，快速模式要求1kΩ。软件方面地址配置错误是最高频的bug，7位地址需要左移1位...",
            source: "reasoning",
          },
        ],
      },
    },
    // 第 3 轮对话：工具调用
    {
      id: "mock-msg-5",
      role: "user",
      content: "搜索一下 ESP32-S3 的技术文档",
      timestamp: now - 600_000,
    },
    {
      id: "mock-msg-6",
      role: "assistant",
      content: "我为你找到了 ESP32-S3 的相关技术文档：\n\n1. **ESP32-S3 技术参考手册** - 涵盖 Wi-Fi、蓝牙 5.0、双核 Xtensa LX7 处理器等\n2. **ESP32-S3 数据手册** - 引脚定义、电气特性、封装信息",
      timestamp: now - 580_000,
      usage: { promptTokens: 42, completionTokens: 86, totalTokens: 128 },
      activity: {
        durationMs: 3200,
        steps: [
          { type: "thinking", id: "h-7", content: "正在检索知识库...", source: "rag" },
          {
            type: "tool",
            id: "t-1",
            name: "search_docs",
            args: '{"query": "ESP32-S3 技术文档"}',
            result: '{"count": 2, "titles": ["ESP32-S3 技术参考手册", "ESP32-S3 数据手册"]}',
          },
          { type: "thinking", id: "h-8", content: "正在生成回答...", source: "llm" },
        ],
      },
      sources: [
        { id: "src-2", title: "ESP32-S3 技术参考手册", doc: "esp32-s3-technical-reference.pdf", page: 1, score: 0.92, excerpt: "ESP32-S3 搭载 Xtensa LX7 双核处理器..." },
        { id: "src-3", title: "ESP32-S3 数据手册", doc: "esp32-s3-datasheet.pdf", page: 3, score: 0.88, excerpt: "引脚定义与电气特性..." },
      ],
    },
    // 第 4 轮：无 usage 的消息（测试估算 fallback）
    {
      id: "mock-msg-7",
      role: "user",
      content: "GPIO 的输出模式有哪些？",
      timestamp: now - 300_000,
    },
    {
      id: "mock-msg-8",
      role: "assistant",
      content: "GPIO 输出模式主要有：推挽输出、开漏输出、复用推挽输出、复用开漏输出。",
      timestamp: now - 295_000,
      // 故意不带 usage，测试估算逻辑
    },
    // 第 5 轮：今天的消息
    {
      id: "mock-msg-9",
      role: "user",
      content: "串口通信波特率怎么配置？",
      timestamp: now - 60_000,
    },
    {
      id: "mock-msg-10",
      role: "assistant",
      content: "串口波特率配置步骤：\n1. 确定所需的波特率（常用 9600、115200 等）\n2. 计算波特率寄存器值：BRR = fCK / (16 × BaudRate)\n3. 配置 USART_BRR 寄存器\n4. 使能发送/接收",
      timestamp: now - 50_000,
      usage: { promptTokens: 35, completionTokens: 92, totalTokens: 127 },
    },
  ];

  const updatedSM = { ...useChatStore.getState().sessionMessages, [mockSessionId]: mockMessages };
  saveSessionMessages(mockSessionId, mockMessages);

  // 确保会话存在
  const sessionStore = useSessionStore.getState();
  if (!sessionStore.sessions.find((s) => s.id === mockSessionId)) {
    const mockSession: import("../types/session").Session = {
      id: mockSessionId,
      title: "Mock 测试会话",
      preview: "串口通信波特率怎么配置？",
      model: "deepseek-v3",
      createdAt: now - 3600_000,
      project: "default",
      pinned: false,
      msgCount: 5,
      contextWindow: CONTEXT_WINDOW_256K,
    };
    useSessionStore.setState({ sessions: [mockSession, ...sessionStore.sessions] });
    saveToStorage("sessions", [mockSession, ...sessionStore.sessions]);
  }

  useChatStore.setState({
    messages: mockMessages,
    sessionMessages: updatedSM,
    activeSessionId: mockSessionId,
  });

  saveToStorage("activeSession", mockSessionId);
  getLog()("ok", "chat", "Mock 数据已加载，切换到 Mock 测试会话");
}

// 仅在开发环境挂到 window 方便控制台调用
if (typeof window !== "undefined" && import.meta.env.DEV) {
  window.__loadMockData = loadMockData;
  window.__clearMockData = () => {
    const mockSessionId = "mock-test-session";
    const sm = { ...useChatStore.getState().sessionMessages };
    delete sm[mockSessionId];
    removeSessionMessages(mockSessionId);
    useChatStore.setState({ sessionMessages: sm, messages: [], activeSessionId: "s1" });
    saveToStorage("activeSession", "s1");
    getLog()("ok", "chat", "Mock 数据已清除");
  };
}
