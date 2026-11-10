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
import type { ChatRequest, ChatSSEEvent, Attachment, BackendMessage, RunStateResponse, RunStatus } from "../types/api";
import type { Plan } from "../types/plan";
import type { ChangeSet, ChangeSetsResponse, WorkspaceContext } from "../types/change";
import type { SSEEventMeta } from "../api/client";
import { loadFromStorage, saveToStorage, saveSessionMessages as writeSessionMessages, loadSessionMessages, removeSessionMessages, migrateMessagesToShards } from "../utils/persistence";
import { getSessionHistoryMode } from "../utils/rollingHistoryClient";
import { normalizeTodos } from "../utils/planTodo";
import { fetchSessionTodos as fetchSessionTodosApi, clearSessionTodos as clearSessionTodosApi } from "../api/todo";
import { approveSessionPlan, fetchLatestPlan, rejectSessionPlan, setAgentPlanMode } from "../api/plan";
import { planSnapshotState, planUpdateState, todoSnapshotState, todoUpdateState } from "./planTodoState";
import {
  assessInterruptedRun,
  clearActiveRun,
  fetchActiveRun,
  fetchRunState,
  getActiveRun,
  isTerminalRunStatus,
  loadRunRecords,
  parseRunEventMeta,
  pendingApprovalsToCalls,
  requestCancelRun,
  requestResumeRun,
  runStateRunId,
  setRunStatus,
  startActiveRun,
  updateRunSeq,
  type ActiveRunRecord,
  type InterruptedRunAssessment,
} from "../utils/runRecovery";
import { post, on } from "../utils/broadcast";
import { reasoningModesFor, type ReasoningMode } from "../types/reasoning";
import { useAppStore } from "./useAppStore";
import { useSessionStore, CONTEXT_WINDOW_256K } from "./useSessionStore";
import { apiSSE, apiPost, apiGet, apiDelete } from "../api/client";
import { useSettingsStore } from "./useSettingsStore";
import { useLogStore } from "./useLogStore";
import { useToastStore } from "./useToastStore";
import { handleToolCallEvent, handleToolResultEvent, handleCompileLogEvent, handleProgressEvent } from "./useWorkbenchBridge";

const DEFAULT_MODEL = "";
const THINKING_PLACEHOLDER_TEXT = "模型正在思考...";
const REASONING_MISSING_TEXT = "模型没有返回思考信息";
const REASONING_OFF_TEXT = "思考已关闭";
/** User-visible turn that starts executing an approved plan. */
export const APPROVED_PLAN_EXECUTION_MESSAGE = "按照已批准的计划开始执行。";
type KbScopeIssue = "corrupt" | "unavailable" | "repair" | "storage_error" | "verification_failed" | null;
type StoredKbScope = { version: 1; mode: "all" } | { version: 1; mode: "selected"; ids: string[] } | { version: 1; mode: "repair" };
const KB_SCOPE_STORAGE_PREFIX = "hwrag_chat_kb_scope_v1_";
let _sendValidationInProgress = false;

// 段落换行标志：tool_result 后设置的标志，下一次 text chunk 前插入 \n\n 分隔工具结果和回答文本
// 按 sessionId 隔离，避免多会话切换时串号
const _needsParagraphBreak = new Map<string, boolean>();

// Track the most recent Agent tool_call id so compile_log / progress SSE events
// can be routed to the correct Workbench pane (FlashPane for build/flash tools).
// 按 sessionId 隔离，避免多会话切换时串号
const _lastToolCallId = new Map<string, string>();

declare global {
  interface Window {
    __loadMockData?: () => void;
    __clearMockData?: () => void;
  }
}

interface PagedHistoryStatus {
  loading: boolean;
  pagesLoaded: number;
  hasMore: boolean | null;
  error: string | null;
  persistenceError: string | null;
  restartRequired?: boolean;
}

const MAX_REQUEST_MESSAGES = 200;

function _messageSnapshotToken(messages: Message[]): string | null {
  try {
    const token = JSON.stringify(messages);
    return typeof token === "string" ? token : null;
  } catch {
    return null;
  }
}

function _knownHistorySendBlockReason(
  state: ChatState,
  sessionId: string,
  quoted: Message | undefined,
  serverHistory: boolean,
): string | null {
  if (state.activeSessionId !== sessionId) {
    return "会话已切换，未发送本次提问；原输入仍保留，请在目标会话重新确认后重试。";
  }
  const pageStatus = state.pagedHistoryBySession[sessionId];
  if (state.isLoadingMessages || pageStatus?.loading) {
    return "会话历史仍在恢复或分页加载；为避免遗漏历史，本次未发送。请等待加载完成后重试。";
  }
  if (!serverHistory && pageStatus?.hasMore === true) {
    return "仍有更早的分页历史未加载；为避免把不完整历史发送给模型，本次未发送。请先加载更早历史后重试。";
  }
  if (pageStatus?.error
      || (pageStatus && pageStatus.pagesLoaded > 0 && pageStatus.hasMore === null)) {
    return "无法确认当前会话历史是否完整；本次未发送。请重新加载历史后重试。";
  }
  const cachedMessages = state.sessionMessages[sessionId];
  if (!serverHistory) {
    const historySlots = MAX_REQUEST_MESSAGES - 1 - (quoted ? 1 : 0);
    const knownHistoryCount = Math.max(state.messages.length, cachedMessages?.length ?? 0);
    if (knownHistoryCount > historySlots) {
      return `当前已知历史有 ${knownHistoryCount} 条消息，超过本次请求可容纳的 ${historySlots} 条历史消息上限。为避免省略已知历史，本次未发送；输入和本地消息已保留。请新建会话或等待安全上下文整理接入。`;
    }
  }
  if (cachedMessages) {
    const activeToken = _messageSnapshotToken(state.messages);
    const cachedToken = _messageSnapshotToken(cachedMessages);
    if (activeToken === null || cachedToken === null || activeToken !== cachedToken) {
      return "当前会话显示内容与本地历史缓存不一致，无法确认完整上下文；本次未发送。请重新加载历史后重试。";
    }
  }
  return null;
}

interface ChatState {
  messages: Message[];
  sessionMessages: Record<string, Message[]>;
  sources: SourceRef[];
  isStreaming: boolean;
  /** fetchMessages 期间为 true，用于区分"加载中"和"真空会话" */
  isLoadingMessages: boolean;
  /** 显式分页读取状态，按 sessionId 隔离；缺省表示尚未请求分页历史 */
  pagedHistoryBySession: Record<string, PagedHistoryStatus>;
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
  /** Error shown for the active session; mirrors streamingErrorBySession[activeSessionId]. */
  streamingError: StreamingErrorInfo | null;
  /** Per-session terminal error, so a background run's failure never leaks into another chat. */
  streamingErrorBySession: Record<string, StreamingErrorInfo>;
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
  /** 非空表示检索范围需要用户显式修复或重新验证 */
  kbScopeIssue: KbScopeIssue;
  setSelectedKbIds: (ids: string[]) => void;
  /** 切换某个知识库的选中状态（在数组中增删） */
  toggleKbSelection: (kbId: string) => void;
  beginKbScopeRepair: () => void;
  verifySelectedKbScope: () => Promise<boolean>;
  /** 401 未授权：为 true 时提示用户配置 API Key */
  needsApiKey: boolean;
  setNeedsApiKey: (v: boolean) => void;
  /** 按 sessionId 存储输入框草稿（内存，不持久化到 localStorage） */
  drafts: Record<string, string>;
  /** 设置某会话的草稿（输入时持续调用） */
  setDraft: (sessionId: string, text: string) => void;
  /** 清空某会话的草稿（发送后调用） */
  clearDraft: (sessionId: string) => void;
  /** 按 sessionId 存储输入框附件（内存，不持久化到 localStorage） */
  sessionAttachments: Record<string, Attachment[]>;
  /** 设置某会话的附件 */
  setSessionAttachments: (sessionId: string, attachments: Attachment[]) => void;
  /** 清空某会话的附件（发送后调用） */
  clearSessionAttachments: (sessionId: string) => void;

  // ── Plan mode + server-authoritative todos/plans ──
  /** Request-scoped read-only plan mode. Never written to settings or localStorage. */
  planMode: boolean;
  /** Per-session mirror; planMode is the active session's value. */
  planModeBySession: Record<string, boolean>;
  setPlanMode: (value: boolean) => void;
  /** Clear the Agent's persisted plan-mode request (user override). */
  leaveAgentPlanMode: () => Promise<boolean>;
  /** Restore planMode for one session from its persisted marker. */
  restorePlanModeForSession: (sessionId: string) => void;
  /** Server-authoritative todo snapshot per session (loaded flag separates empty from unloaded). */
  sessionTodos: Record<string, TodoItem[]>;
  sessionTodosLoaded: Record<string, boolean>;
  sessionTodosRevision: Record<string, number>;
  /** True once an SSE todo_update arrived for the session (including a clear). */
  streamingTodosSeen: Record<string, boolean>;
  sessionPlans: Record<string, Plan | null>;
  sessionPlanLoaded: Record<string, boolean>;
  sessionPlanRevision: Record<string, number>;
  /** Session-scoped P2 change facts, loaded from the change-set API and refreshed by SSE. */
  changeSetsBySession: Record<string, ChangeSet[]>;
  changeSetsLoadingBySession: Record<string, boolean>;
  workspaceContextBySession: Record<string, WorkspaceContext | undefined>;
  selectedChangeItemBySession: Record<string, string | null>;
  setSelectedChangeItem: (sessionId: string, itemId: string | null) => void;
  loadChangeSets: (sessionId: string) => Promise<void>;
  /** Load GET /todos for a session; stale responses lose to newer SSE updates. */
  loadSessionTodos: (sessionId: string) => Promise<void>;
  /** Load GET /plans/latest for a session; stale responses lose to newer SSE updates. */
  loadSessionPlan: (sessionId: string) => Promise<void>;
  /** DELETE the persisted todo list and clear it from the visible assistant message. */
  clearSessionTodos: (sessionId?: string) => Promise<boolean>;
  /** Approve a pending plan; closes planMode on success. */
  approvePlan: (sessionId: string, planId: string) => Promise<Plan | null>;
  /** Reject a pending plan; closes planMode on success. */
  rejectPlan: (sessionId: string, planId: string) => Promise<boolean>;
  /** Send the approved-plan execution turn. Returns false when it cannot start. */
  executeApprovedPlan: (plan: Plan) => boolean;
  /** HITL pending confirm: when set, ConfirmDialog shows (v2-T4) */
  pendingConfirm: PendingConfirm | null;
  /** Last agent request body, cached for resume API (v2-T4) */
  _lastAgentPayload: ChatRequest | null;
  /** Resume agent after HITL decision (allow/deny/stop) */
  resumeAgent: (decision: "allow" | "deny" | "stop") => void;
  /** Clear pending confirm without resuming (e.g. dialog dismissed) (v2-T4) */
  clearPendingConfirm: () => void;

  // ── Recovery R1: persisted run tracking ──
  /** Active run per session (mirror of the localStorage recovery records). */
  activeRuns: Record<string, ActiveRunRecord>;
  /** Runs that came back `interrupted`; offer retry/continue instead of fake success. */
  runRecoveryNoticeBySession: Record<string, RunRecoveryNotice>;
  /** Visible run lifecycle per session (live, restoring, or terminal). */
  runViewBySession: Record<string, RunView>;
  /** Bounded per-run event timeline per session (deduplicated by seq). */
  runTimelineBySession: Record<string, RunTimelineEvent[]>;
  /** Reattach a persisted unfinished run after refresh or session load. */
  reattachRun: (sessionId: string) => Promise<void>;
  /** Dismiss an interrupted-run notice without retrying. */
  clearRunRecoveryNotice: (sessionId: string) => void;
  /** Ask the backend to cancel the run that owns this session. */
  cancelActiveRun: (sessionId: string) => Promise<boolean>;
  /** Ask the backend to resume an interrupted run from its checkpoint. */
  resumeInterruptedRun: (sessionId: string) => Promise<boolean>;
  /** Hide the visible run lifecycle bar for this session. */
  dismissRunView: (sessionId: string) => void;

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

  sendMessage: (
    content: string,
    attachments?: Attachment[],
    quoted?: Message,
    skills?: ChatSkillsRequest,
    options?: { approvedPlanId?: string },
  ) => void;
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
  refreshMessagePage: (sessionId: string) => void;
  loadInitialMessagePage: (sessionId: string) => Promise<void>;
  loadOlderMessagePage: (sessionId: string) => Promise<void>;
  /** 流式结束后将最后一轮 user+assistant 消息持久化到后端 */
  persistLastTurn: (sessionId: string) => Promise<boolean>;
  /** 重试保存当前会话里尚未确认写入的消息；不会重新发起聊天或 Agent 请求 */
  retryPendingSave: (sessionId: string) => Promise<void>;
  /** 会话消息保存状态；缺省表示已保存 */
  pendingSaveBySession: Record<string, "saving" | "pending">;
  quoteMessage: (msgId: string) => void;
  exportConversation: (format: "markdown" | "json") => void;
  showStats: () => void;
  hideStats: () => void;
}

/** A terminal chat error, kept per session. */
interface StreamingErrorInfo {
  code: string;
  message: string;
  detail: string;
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
  /** Session that owns the paused run; the dialog only shows for it. */
  sessionId: string;
}

/** Recovery R1: a run that ended without producing a complete answer. */
interface RunRecoveryNotice {
  runId: string;
  status: RunStatus;
  message: string;
  /** A tool side effect may be unknown after the interruption. */
  resultUnknown?: boolean;
  /** The unknown side effect may involve hardware (device state must be checked). */
  hardwareUnknown?: boolean;
  /** The run exposes a checkpoint the backend can continue from. */
  resumable?: boolean;
}

/** Visible run lifecycle for one session. Terminal views stay until dismissed. */
export interface RunView {
  runId: string;
  status: RunStatus;
  /** live = streaming now; restoring = reattaching after load; terminal = ended. */
  phase: "live" | "restoring" | "terminal";
  startedAt: number | null;
  finishedAt: number | null;
  lastEventType: string | null;
  lastEventSeq: number | null;
  lastEventAt: number | null;
  currentTool: string | null;
  currentStepIndex: number | null;
  checkpointId: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  pendingApprovals: number;
  resultUnknown: boolean;
  hardwareUnknown: boolean;
  resumable: boolean;
  actionPending: boolean;
}

/** One compact timeline row for the current run (never one row per text chunk). */
export interface RunTimelineEvent {
  seq: number;
  type: string;
  at: number;
  tool?: string;
  callId?: string;
  sourceId?: string;
  sourceTitle?: string;
  messageId?: string;
  message?: string;
  status?: string;
}

/** Events recorded at most once per contiguous run of identical kinds. */
const RUN_TIMELINE_MAX = 60;
const RUN_TEXT_EVENT_TYPES = new Set(["text", "thinking", "compile_log", "progress"]);

/** Mutable terminal state shared by the live stream, replays and reattach. */
interface ChatTerminalState {
  outcome: "running" | "error" | "awaiting_confirmation" | "success" | "cancelled";
  errorMessage: string;
  finalized: boolean;
}

interface ResumeTerminalState {
  outcome: "running" | "error" | "awaiting_confirmation" | "success" | "stopped";
  errorMessage?: string;
  decision?: "allow" | "deny" | "stop";
}

// Supplementary replay streams are per session; never run two at once.
const _runReplayInFlight = new Set<string>();
const _runReattachInFlight = new Set<string>();
// One automatic "submit the plan" follow-up per session when plan mode ends
// with research but without a submitted plan.
const _planAutoRetryUsed = new Set<string>();
// A stopped or refreshed request may still be releasing its writer lease on
// the backend. Retry the same turn a few times with short backoff before
// showing an error, without dropping the draft or messages.
const _WRITER_BUSY_MAX_AUTO_RETRIES = 3;
const _WRITER_BUSY_RETRY_DELAYS_MS = [1000, 2000, 3000] as const;

export interface ChatSkillsRequest {
  mode: NonNullable<ChatRequest["skills_mode"]>;
  skillIds: string[];
}

function getLog() {
  return useLogStore.getState().log;
}

// Maximum number of messages retained in memory to avoid OOM on long conversations
const MAX_MESSAGES = 200;

function _kbScopeStorageKey(sessionId: string): string {
  return `${KB_SCOPE_STORAGE_PREFIX}${encodeURIComponent(sessionId)}`;
}

function _loadKbScope(sessionId: string): { ids: string[]; issue: KbScopeIssue } {
  if (!sessionId) return { ids: [], issue: null };
  let raw: string | null;
  try {
    raw = localStorage.getItem(_kbScopeStorageKey(sessionId));
  } catch {
    return { ids: [], issue: "storage_error" };
  }
  if (raw === null) return { ids: [], issue: null };

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ids: [], issue: "corrupt" };
  }
  if (!parsed || typeof parsed !== "object" || (parsed as { version?: unknown }).version !== 1) {
    return { ids: [], issue: "corrupt" };
  }
  const scope = parsed as Partial<StoredKbScope>;
  if (scope.mode === "all") return { ids: [], issue: null };
  if (scope.mode === "repair") return { ids: [], issue: "repair" };
  if (
    scope.mode === "selected"
    && Array.isArray(scope.ids)
    && scope.ids.length > 0
    && scope.ids.every((id) => typeof id === "string" && id.length > 0)
  ) {
    return { ids: [...new Set(scope.ids)], issue: null };
  }
  return { ids: [], issue: "corrupt" };
}

function _saveKbScope(sessionId: string, scope: StoredKbScope): boolean {
  if (!sessionId) return true;
  try {
    localStorage.setItem(_kbScopeStorageKey(sessionId), JSON.stringify(scope));
    return true;
  } catch {
    return false;
  }
}

function _kbScopeIssueMessage(issue: KbScopeIssue): string {
  switch (issue) {
    case "corrupt": return "本会话的知识库范围无法读取，请重新选择或明确使用全部知识库。";
    case "unavailable": return "所选知识库已停用或不存在，请修复范围或明确使用全部知识库。";
    case "repair": return "请至少选择一个知识库，或明确使用全部已启用知识库。";
    case "storage_error": return "知识库范围未能保存在本机，请重新选择后再发送。";
    case "verification_failed": return "暂时无法验证所选知识库，请检查连接后重试。";
    default: return "知识库范围需要修复后才能发送。";
  }
}

const PENDING_SAVE_PREFIX = "hwrag_pending_save_";
const LOCAL_PENDING_SAVE_FIELD = "__hwrag_pending_save";
const _messageSaveQueues = new Map<string, Promise<void>>();
const _localMessageSnapshotFailures = new Set<string>();
const _localStatusRefreshQueued = new Set<string>();

function _scheduleLocalSaveStatusRefresh(sessionId: string): void {
  if (_localStatusRefreshQueued.has(sessionId)) return;
  _localStatusRefreshQueued.add(sessionId);
  queueMicrotask(() => {
    _localStatusRefreshQueued.delete(sessionId);
    const status = _localMessageSnapshotFailures.has(sessionId)
      ? "pending"
      : _pendingSaveStatus(sessionId);
    const state = useChatStore.getState();
    if (state.pendingSaveBySession[sessionId] === status) return;
    useChatStore.setState((currentState) => {
      const next = { ...currentState.pendingSaveBySession };
      if (status) next[sessionId] = status;
      else delete next[sessionId];
      return { pendingSaveBySession: next };
    });
  });
}

function saveSessionMessages(sessionId: string, msgs: unknown): boolean {
  const hadLocalFailure = _localMessageSnapshotFailures.has(sessionId);
  const persisted = writeSessionMessages(sessionId, msgs);
  if (persisted) _localMessageSnapshotFailures.delete(sessionId);
  else _localMessageSnapshotFailures.add(sessionId);
  if (!persisted || hadLocalFailure) _scheduleLocalSaveStatusRefresh(sessionId);
  return persisted;
}

type LocallyTrackedMessage = Message & { __hwrag_pending_save?: true };

function _pendingSaveStorageKey(sessionId: string): string {
  return `${PENDING_SAVE_PREFIX}${encodeURIComponent(sessionId)}`;
}

function _loadPendingSaveIds(sessionId: string): string[] {
  try {
    const raw = localStorage.getItem(_pendingSaveStorageKey(sessionId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function _storePendingSaveIds(sessionId: string, ids: string[]): boolean {
  try {
    const uniqueIds = [...new Set(ids)];
    const key = _pendingSaveStorageKey(sessionId);
    if (uniqueIds.length > 0) localStorage.setItem(key, JSON.stringify(uniqueIds));
    else localStorage.removeItem(key);
    return true;
  } catch {
    // The message shard remains the recovery source if the marker cannot be written.
    return false;
  }
}

function _rememberPendingSaveIds(sessionId: string, ids: string[]): void {
  _storePendingSaveIds(sessionId, [..._loadPendingSaveIds(sessionId), ...ids]);
}

function _localPendingSaveIds(messages: Message[]): string[] {
  return messages
    .filter((message) => (message as LocallyTrackedMessage)[LOCAL_PENDING_SAVE_FIELD] === true)
    .map((message) => message.id);
}

function _markLocalPendingSaveIds(sessionId: string, ids: string[]): boolean {
  const marking = new Set(ids);
  if (marking.size === 0) return true;
  let persisted = false;
  useChatStore.setState((state) => {
    const sessionMessages = state.sessionMessages[sessionId];
    const current = sessionMessages && sessionMessages.length > 0
      ? sessionMessages
      : state.activeSessionId === sessionId
        ? state.messages
        : sessionMessages ?? [];
    const mark = (message: Message): Message => marking.has(message.id)
      ? { ...message, [LOCAL_PENDING_SAVE_FIELD]: true } as Message
      : message;
    const nextMessages = current.map(mark);
    const nextActiveMessages = state.activeSessionId === sessionId
      ? state.messages.map(mark)
      : undefined;
    persisted = saveSessionMessages(sessionId, nextMessages);
    return {
      sessionMessages: { ...state.sessionMessages, [sessionId]: nextMessages },
      ...(nextActiveMessages ? { messages: nextActiveMessages } : {}),
    };
  });
  return persisted;
}

function _clearLocalPendingSaveIds(sessionId: string, ids: string[]): boolean {
  const clearing = new Set(ids);
  if (clearing.size === 0) return true;
  let persisted = false;
  useChatStore.setState((state) => {
    const sessionMessages = state.sessionMessages[sessionId];
    const current = sessionMessages && sessionMessages.length > 0
      ? sessionMessages
      : state.activeSessionId === sessionId
        ? state.messages
        : sessionMessages ?? [];
    const clear = (message: Message): Message => {
      if (!clearing.has(message.id)) return message;
      const tracked = { ...message } as LocallyTrackedMessage;
      delete tracked[LOCAL_PENDING_SAVE_FIELD];
      return tracked;
    };
    const nextMessages = current.map(clear);
    const nextActiveMessages = state.activeSessionId === sessionId
      ? state.messages.map(clear)
      : undefined;
    persisted = saveSessionMessages(sessionId, nextMessages);
    if (!persisted) return {};
    return {
      sessionMessages: { ...state.sessionMessages, [sessionId]: nextMessages },
      ...(nextActiveMessages ? { messages: nextActiveMessages } : {}),
    };
  });
  return persisted;
}

function _clearPendingSaveIds(sessionId: string, ids: string[]): string[] {
  const clearing = new Set(ids);
  const current = _loadPendingSaveIds(sessionId);
  const remaining = current.filter((id) => !clearing.has(id));
  return _storePendingSaveIds(sessionId, remaining) ? remaining : current;
}

/**
 * Drop save markers that no longer belong to the turn being saved.
 *
 * A failed turn can leave its assistant ID in the per-session marker store.
 * When a later turn succeeds, only that turn's messages are allowed to remain
 * pending; older or no-longer-present IDs would otherwise keep the save banner
 * visible forever.
 */
function _reconcilePendingSaveMarkers(sessionId: string, keepIds: Iterable<string>): void {
  const messages = useChatStore.getState().sessionMessages[sessionId] || [];
  const byId = new Map(messages.map((message) => [message.id, message]));
  const keep = new Set(keepIds);
  const staleStoredIds = _loadPendingSaveIds(sessionId).filter((id) => {
    const message = byId.get(id);
    return !keep.has(id) || message === undefined || _isMessageSaveConfirmed(sessionId, message);
  });
  if (staleStoredIds.length > 0) _clearPendingSaveIds(sessionId, staleStoredIds);

  const staleLocalIds = messages
    .filter((message) => {
      const tracked = (message as LocallyTrackedMessage)[LOCAL_PENDING_SAVE_FIELD] === true;
      return tracked && (!keep.has(message.id) || _isMessageSaveConfirmed(sessionId, message));
    })
    .map((message) => message.id);
  if (staleLocalIds.length > 0) _clearLocalPendingSaveIds(sessionId, staleLocalIds);
}

function _pendingSaveStatus(sessionId: string): "saving" | "pending" | undefined {
  if (_messageSaveQueues.has(sessionId)) return "saving";
  const messages = useChatStore.getState().sessionMessages[sessionId] || [];
  return _localMessageSnapshotFailures.has(sessionId)
    || _loadPendingSaveIds(sessionId).length > 0
    || _localPendingSaveIds(messages).length > 0
    ? "pending"
    : undefined;
}

function _loadPendingSaveStatuses(): Record<string, "pending"> {
  const sessions = loadFromStorage("sessions", [] as { id: string }[]);
  if (!Array.isArray(sessions)) return {};
  return Object.fromEntries(
    sessions.filter((session) =>
      _loadPendingSaveIds(session.id).length > 0
      || _localPendingSaveIds(loadSessionMessages<Message[]>(session.id, [])).length > 0
    )
      .map((session) => [session.id, "pending" as const])
  );
}

const _initialActiveSessionId = loadFromStorage("activeSession", "");
const _initialKbScope = _loadKbScope(_initialActiveSessionId);

function _messageSavePayload(message: Message): Record<string, unknown> {
  return {
    id: message.id,
    role: message.role,
    content: serializeMessageContent(message.content),
    sources: message.sources || [],
    tool_calls: [],
    activity: message.activity || null,
  };
}

function _withMessageId(message: Message, id: string): Message {
  if (message.id === id) return message;
  return {
    ...message,
    id,
    ...(message.sources
      ? {
          sources: message.sources.map((source) =>
            source.messageId === message.id ? { ...source, messageId: id } : source
          ),
        }
      : {}),
  };
}

function _remapPendingLocalMessageIds(sessionId: string, idMap: Map<string, string>): void {
  if (idMap.size === 0) return;
  const remapMessages = (messages: Message[]) => messages.map((message) => {
    const id = idMap.get(message.id);
    return id ? _withMessageId(message, id) : message;
  });

  useChatStore.setState((state) => {
    const sessionMessages = state.sessionMessages[sessionId];
    const current = sessionMessages && sessionMessages.length > 0
      ? sessionMessages
      : state.activeSessionId === sessionId
        ? state.messages
        : sessionMessages ?? [];
    const nextMessages = remapMessages(current);
    const nextActiveMessages = state.activeSessionId === sessionId
      ? remapMessages(state.messages)
      : undefined;
    saveSessionMessages(sessionId, nextMessages);
    return {
      sessionMessages: { ...state.sessionMessages, [sessionId]: nextMessages },
      ...(nextActiveMessages ? { messages: nextActiveMessages } : {}),
    };
  });

  _storePendingSaveIds(
    sessionId,
    _loadPendingSaveIds(sessionId).map((id) => idMap.get(id) ?? id),
  );
}

const _confirmedMessageSnapshots = new Map<string, string>();
const MAX_CONFIRMED_MESSAGE_SNAPSHOTS = 2048;

function _messageSaveKey(sessionId: string, messageId: string): string {
  return `${sessionId}\u0000${messageId}`;
}

function _isMessageSaveConfirmed(sessionId: string, message: Message): boolean {
  return _confirmedMessageSnapshots.get(_messageSaveKey(sessionId, message.id))
    === JSON.stringify(_messageSavePayload(message));
}

function _rememberMessageSaveConfirmed(sessionId: string, message: Message): void {
  const key = _messageSaveKey(sessionId, message.id);
  if (!_confirmedMessageSnapshots.has(key) && _confirmedMessageSnapshots.size >= MAX_CONFIRMED_MESSAGE_SNAPSHOTS) {
    const oldestKey = _confirmedMessageSnapshots.keys().next().value;
    if (oldestKey !== undefined) _confirmedMessageSnapshots.delete(oldestKey);
  }
  _confirmedMessageSnapshots.set(key, JSON.stringify(_messageSavePayload(message)));
}

function _rememberConfirmedBackendMessages(sessionId: string, messages: BackendMessage[]): void {
  for (const backendMessage of messages.slice(-2)) {
    _rememberMessageSaveConfirmed(sessionId, mapBackendMessage(backendMessage));
  }
}

function _updateSessionMetaAfterPersistedMessages(sessionId: string, messages: Message[]): void {
  try {
    const msgCount = messages.filter(
      (message) => message.role === "user" || (message.role === "assistant" && message.content),
    ).length;
    const lastUserMessage = messages.filter((message) => message.role === "user").pop();
    const userText = lastUserMessage
      ? typeof lastUserMessage.content === "string"
        ? lastUserMessage.content
        : lastUserMessage.content.filter((part) => part.type === "text").map((part) => part.text).join(" ")
      : "";
    const currentSession = useSessionStore.getState().sessions.find((session) => session.id === sessionId);
    const title = currentSession?.title?.startsWith("新对话") && userText
      ? userText.slice(0, 30).replace(/\n/g, " ")
      : undefined;

    useSessionStore.getState().updateSessionMeta(sessionId, {
      msgCount,
      preview: userText.slice(0, 60) || "(图片)",
      ...(title ? { title } : {}),
    });
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    try {
      getLog()("warn", "chat", `会话摘要同步失败（消息已保存）: ${sessionId} - ${errorMessage}`);
    } catch {
      // Local summary and its logging are best effort after the message write succeeds.
    }
  }
}

function _isMessageSaveSnapshotCurrent(sessionId: string, messages: Message[]): boolean {
  const currentById = new Map(
    (useChatStore.getState().sessionMessages[sessionId] || []).map((message) => [message.id, message])
  );
  return messages.every((message) => {
    const current = currentById.get(message.id);
    return current !== undefined
      && JSON.stringify(_messageSavePayload(current)) === JSON.stringify(_messageSavePayload(message));
  });
}

function _saveCurrentLocalSnapshot(sessionId: string): boolean {
  const state = useChatStore.getState();
  const sessionMessages = state.sessionMessages[sessionId];
  const messages = sessionMessages && sessionMessages.length > 0
    ? sessionMessages
    : state.activeSessionId === sessionId
      ? state.messages
      : sessionMessages ?? [];
  return saveSessionMessages(sessionId, messages);
}

function _clearSavedMessageIdsIfCurrent(sessionId: string, messages: Message[]): string[] {
  const pendingIds = _loadPendingSaveIds(sessionId);
  if (!_isMessageSaveSnapshotCurrent(sessionId, messages)) {
    return [...new Set([...pendingIds, ..._localPendingSaveIds(useChatStore.getState().sessionMessages[sessionId] || [])])];
  }
  if (!_clearLocalPendingSaveIds(sessionId, messages.map((message) => message.id))) {
    return [...new Set([
      ..._loadPendingSaveIds(sessionId),
      ..._localPendingSaveIds(useChatStore.getState().sessionMessages[sessionId] || []),
    ])];
  }
  return _clearPendingSaveIds(sessionId, messages.map((message) => message.id));
}

function _matchesBackendMessage(local: Message, backend: BackendMessage): boolean {
  return local.role === backend.role
    && serializeMessageContent(local.content) === serializeMessageContent(parseMessageContent(backend.content))
    && JSON.stringify(local.sources || []) === JSON.stringify(backend.sources || [])
    && JSON.stringify(local.activity || null) === JSON.stringify(backend.activity || null);
}

async function _postMessagesWithSessionRecovery(sessionId: string, messages: Message[]): Promise<void> {
  const postMessages = async () => {
    for (const message of messages) {
      // A prior queued save may have confirmed this exact snapshot while this
      // request was waiting. Do not issue the same CRUD POST twice; keep newer
      // local snapshots pending until their own save completes.
      if (_isMessageSaveConfirmed(sessionId, message)) {
        if (
          _isMessageSaveSnapshotCurrent(sessionId, [message])
          && _clearLocalPendingSaveIds(sessionId, [message.id])
        ) {
          _clearPendingSaveIds(sessionId, [message.id]);
        }
        continue;
      }
      await apiPost(`sessions/${sessionId}/messages`, _messageSavePayload(message));
      _rememberMessageSaveConfirmed(sessionId, message);
      if (
        _isMessageSaveSnapshotCurrent(sessionId, [message])
        && _clearLocalPendingSaveIds(sessionId, [message.id])
      ) {
        _clearPendingSaveIds(sessionId, [message.id]);
      }
    }
  };

  const postWithSessionRecovery = async () => {
    try {
      await postMessages();
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      if (!errMsg.includes("404") && !errMsg.includes("NOT_FOUND")) throw err;

      const session = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
      await apiPost("sessions", {
        id: sessionId,
        title: session?.title || "新对话",
        model: session?.model || "",
        project: session?.project || "",
        context_window: session?.contextWindow || CONTEXT_WINDOW_256K,
      });
      await postMessages();
    }
  };

  // A confirmation can be accepted while the initial "awaiting confirmation"
  // write is still in flight. Serialize each session's CRUD writes so an older
  // snapshot cannot commit after and overwrite the resumed answer.
  const previous = _messageSaveQueues.get(sessionId) ?? Promise.resolve();
  const current = previous.catch(() => undefined).then(postWithSessionRecovery);
  _messageSaveQueues.set(sessionId, current);
  try {
    await current;
  } finally {
    if (_messageSaveQueues.get(sessionId) === current) _messageSaveQueues.delete(sessionId);
  }
}

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

function _extractChangeItemId(toolName: string, result: unknown): string | undefined {
  if (!_FILE_EDIT_TOOLS.has(toolName)) return undefined;
  if (!result || typeof result !== "object") return undefined;
  const metadata = (result as Record<string, unknown>).metadata;
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) return undefined;
  const id = (metadata as Record<string, unknown>).change_item_id;
  return typeof id === "string" && id ? id : undefined;
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
  return steps.map((step) => {
    const placeholder = step.type === "thinking"
      && (step.placeholder === true || step.content === THINKING_PLACEHOLDER_TEXT);
    const resolved = placeholder
      ? {
          ...step,
          content: step.reasoningMode === "off" ? REASONING_OFF_TEXT : REASONING_MISSING_TEXT,
          placeholder: false,
        }
      : step;
    return resolved.status === "pending"
      ? {
          ...resolved,
          status: "done" as const,
          duration: resolved.startTime ? Date.now() - resolved.startTime : resolved.duration,
        }
      : resolved;
  });
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

// ─── Recovery R1: run tracking helpers ───────────────────────
function _syncActiveRunState(record: ActiveRunRecord | null, sessionId: string): void {
  useChatStore.setState((state) => {
    const activeRuns = { ...state.activeRuns };
    if (record) activeRuns[sessionId] = record;
    else delete activeRuns[sessionId];
    return { activeRuns };
  });
}

/** Latest server run state seen for a session, used to assess interruptions. */
const _runStateBySession = new Map<string, RunStateResponse | null>();
/** Runs already observed as terminal; avoids reload loops on repeated loads. */
const _terminalRunIds = new Set<string>();

function _parseIsoTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function _emptyRunView(runId: string, status: RunStatus, phase: RunView["phase"]): RunView {
  return {
    runId,
    status,
    phase,
    startedAt: null,
    finishedAt: null,
    lastEventType: null,
    lastEventSeq: null,
    lastEventAt: null,
    currentTool: null,
    currentStepIndex: null,
    checkpointId: null,
    errorCode: null,
    errorMessage: null,
    pendingApprovals: 0,
    resultUnknown: false,
    hardwareUnknown: false,
    resumable: false,
    actionPending: false,
  };
}

function _patchRunView(sessionId: string, patch: Partial<RunView>): void {
  useChatStore.setState((state) => {
    const view = state.runViewBySession[sessionId];
    if (!view) return {};
    return {
      runViewBySession: {
        ...state.runViewBySession,
        [sessionId]: { ...view, ...patch },
      },
    };
  });
}

function _setRunViewTerminal(
  sessionId: string,
  status: RunStatus,
  errorMessage?: string | null,
  finishedAt?: number,
): void {
  useChatStore.setState((state) => {
    const view = state.runViewBySession[sessionId];
    if (!view) return {};
    return {
      runViewBySession: {
        ...state.runViewBySession,
        [sessionId]: {
          ...view,
          status,
          phase: "terminal",
          finishedAt: finishedAt ?? view.finishedAt ?? Date.now(),
          errorMessage: errorMessage ?? view.errorMessage,
          actionPending: false,
        },
      },
    };
  });
}

/** Drop every visible run artifact for a session (new turn or session delete). */
function _resetRunView(sessionId: string): void {
  _runStateBySession.delete(sessionId);
  useChatStore.setState((state) => {
    const hasView = state.runViewBySession[sessionId] !== undefined;
    const hasTimeline = state.runTimelineBySession[sessionId] !== undefined;
    const hasNotice = state.runRecoveryNoticeBySession[sessionId] !== undefined;
    if (!hasView && !hasTimeline && !hasNotice) return {};
    const runViewBySession = { ...state.runViewBySession };
    const runTimelineBySession = { ...state.runTimelineBySession };
    const runRecoveryNoticeBySession = { ...state.runRecoveryNoticeBySession };
    delete runViewBySession[sessionId];
    delete runTimelineBySession[sessionId];
    delete runRecoveryNoticeBySession[sessionId];
    return { runViewBySession, runTimelineBySession, runRecoveryNoticeBySession };
  });
}

/** "Running" vs a paused approval: both are live for the view. */
function _isLiveRunStatus(status: RunStatus): boolean {
  return status === "running" || status === "awaiting_confirmation";
}

/** The most recent tool step, used to tell whether an interruption left a tool in flight. */
function _lastToolStepForSession(sessionId: string): { name?: string; status?: string } | null {
  const state = useChatStore.getState();
  const steps = state.activeSessionId === sessionId && state.streamingSteps.length > 0
    ? state.streamingSteps
    : (state.sessionMessages[sessionId] ?? state.messages).at(-1)?.activity?.steps ?? [];
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].type === "tool") {
      return { name: steps[i].name, status: steps[i].status };
    }
  }
  return null;
}

function _lastAssistantMessageId(sessionId: string): string | undefined {
  const state = useChatStore.getState();
  const messages = state.activeSessionId === sessionId ? state.messages : (state.sessionMessages[sessionId] ?? []);
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return messages[i].id;
  }
  return undefined;
}

function _beginActiveRun(
  sessionId: string,
  runId: string,
  status: RunStatus = "running",
  startedAt?: number | null,
): ActiveRunRecord | null {
  if (!sessionId || !runId) return null;
  const existing = useChatStore.getState().activeRuns[sessionId] ?? getActiveRun(sessionId);
  const record = existing?.runId === runId ? existing : startActiveRun(sessionId, runId, status);
  useChatStore.setState((state) => {
    const runRecoveryNoticeBySession = { ...state.runRecoveryNoticeBySession };
    delete runRecoveryNoticeBySession[sessionId];
    const previousView = state.runViewBySession[sessionId];
    const sameRun = previousView?.runId === runId;
    const view: RunView = sameRun
      ? { ...previousView, status, phase: "live", actionPending: false }
      : _emptyRunView(runId, status, "live");
    if (startedAt !== undefined && startedAt !== null) view.startedAt = startedAt;
    return {
      activeRuns: { ...state.activeRuns, [sessionId]: record },
      runRecoveryNoticeBySession,
      runViewBySession: { ...state.runViewBySession, [sessionId]: view },
      runTimelineBySession: sameRun
        ? state.runTimelineBySession
        : { ...state.runTimelineBySession, [sessionId]: [] },
    };
  });
  return record;
}

function _markRunStatus(sessionId: string, runId: string, status: RunStatus): void {
  const record = setRunStatus(sessionId, runId, status);
  if (record) _syncActiveRunState(record, sessionId);
}

function _clearRunTracking(sessionId: string): void {
  clearActiveRun(sessionId);
  _syncActiveRunState(null, sessionId);
}

/** Concrete guidance for an interrupted run; never claims it succeeded. */
function _interruptedMessage(
  message: string,
  assessment: InterruptedRunAssessment,
): string {
  if (assessment.resultUnknown || assessment.resumable) return assessment.message;
  return message || assessment.message;
}

/** Record an interrupted run once, then stop reattaching it on later loads. */
function _markRunInterrupted(sessionId: string, runId: string | undefined, message: string): void {
  const state = useChatStore.getState();
  const effectiveRunId = runId
    ?? state.activeRuns[sessionId]?.runId
    ?? getActiveRun(sessionId)?.runId
    ?? state.runViewBySession[sessionId]?.runId
    ?? "";
  const assessment = assessInterruptedRun(
    _runStateBySession.get(sessionId) ?? null,
    _lastToolStepForSession(sessionId),
  );
  const noticeMessage = _interruptedMessage(message, assessment);
  if (effectiveRunId) setRunStatus(sessionId, effectiveRunId, "interrupted");
  clearActiveRun(sessionId);
  useChatStore.setState((s) => {
    const activeRuns = { ...s.activeRuns };
    delete activeRuns[sessionId];
    const previousView = s.runViewBySession[sessionId];
    const view: RunView = previousView ?? _emptyRunView(effectiveRunId, "interrupted", "terminal");
    return {
      activeRuns,
      runRecoveryNoticeBySession: {
        ...s.runRecoveryNoticeBySession,
        [sessionId]: {
          runId: effectiveRunId,
          status: "interrupted",
          message: noticeMessage,
          resultUnknown: assessment.resultUnknown,
          hardwareUnknown: assessment.hardwareUnknown,
          resumable: assessment.resumable,
        },
      },
      runViewBySession: {
        ...s.runViewBySession,
        [sessionId]: {
          ...view,
          status: "interrupted",
          phase: "terminal",
          finishedAt: view.finishedAt ?? Date.now(),
          errorMessage: noticeMessage,
          resultUnknown: assessment.resultUnknown,
          hardwareUnknown: assessment.hardwareUnknown,
          resumable: assessment.resumable,
          actionPending: false,
        },
      },
    };
  });
}

/** Apply a fetched run state to the visible lifecycle bar. */
function _applyRunStateToView(
  sessionId: string,
  runState: RunStateResponse,
  phase?: RunView["phase"],
): void {
  const runId = runStateRunId(runState);
  if (!runId) return;
  _runStateBySession.set(sessionId, runState);
  const assessment = assessInterruptedRun(runState, _lastToolStepForSession(sessionId));
  const pendingApprovals = (runState.pending_approvals ?? [])
    .filter((approval) => approval && approval.status === "pending")
    .length;
  const effectivePhase = phase
    ?? (_isLiveRunStatus(runState.status) ? "live" : "terminal");
  useChatStore.setState((s) => {
    const previousView = s.runViewBySession[sessionId];
    const sameRun = previousView?.runId === runId;
    const backendStartedAt = _parseIsoTime(runState.started_at);
    const backendFinishedAt = _parseIsoTime(runState.finished_at);
    const view: RunView = {
      ...(sameRun ? previousView : _emptyRunView(runId, runState.status, effectivePhase)),
      status: runState.status,
      phase: effectivePhase,
      startedAt: sameRun ? (previousView.startedAt ?? backendStartedAt) : backendStartedAt,
      finishedAt: sameRun ? (previousView.finishedAt ?? backendFinishedAt) : backendFinishedAt,
      lastEventSeq: runState.last_event_seq ?? (sameRun ? previousView.lastEventSeq : null),
      checkpointId: runState.checkpoint_id ?? null,
      errorCode: runState.error_code ?? null,
      errorMessage: runState.error_message ?? (sameRun ? previousView.errorMessage : null),
      pendingApprovals,
      resultUnknown: assessment.resultUnknown,
      hardwareUnknown: assessment.hardwareUnknown,
      resumable: assessment.resumable,
      actionPending: false,
    };
    if (runState.status === "awaiting_confirmation") view.currentTool = view.currentTool ?? null;
    const runRecoveryNoticeBySession = { ...s.runRecoveryNoticeBySession };
    if (runState.status === "interrupted") {
      const noticeMessage = _interruptedMessage(runState.error_message ?? "", assessment);
      runRecoveryNoticeBySession[sessionId] = {
        runId,
        status: "interrupted",
        message: noticeMessage,
        resultUnknown: assessment.resultUnknown,
        hardwareUnknown: assessment.hardwareUnknown,
        resumable: assessment.resumable,
      };
      view.errorMessage = noticeMessage;
    }
    return {
      runViewBySession: { ...s.runViewBySession, [sessionId]: view },
      runRecoveryNoticeBySession,
    };
  });
}

/** One compact timeline row for an accepted run event; text streams coalesce. */
function _timelineEntry(
  event: ChatSSEEvent,
  seq: number,
  sessionId: string,
): RunTimelineEvent | null {
  const at = Date.now();
  switch (event.type) {
    case "run_started":
      return { seq, type: "run_started", at };
    case "thinking":
      return { seq, type: "thinking", at, message: event.source ?? "llm" };
    case "text":
      return { seq, type: "text", at };
    case "tool_call":
      return { seq, type: "tool_call", at, tool: event.tool, callId: event.call_id };
    case "tool_result":
      return {
        seq,
        type: "tool_result",
        at,
        tool: event.tool,
        callId: event.call_id,
        status: event.success === false ? "error" : "done",
      };
    case "tool_confirm_required":
      return {
        seq,
        type: "approval",
        at,
        message: String(event.count ?? event.calls?.length ?? 0),
      };
    case "source":
      return {
        seq,
        type: "source",
        at,
        sourceId: event.id,
        sourceTitle: event.title,
        messageId: _lastAssistantMessageId(sessionId),
      };
    case "error":
      return { seq, type: "error", at, message: event.message, status: event.code };
    case "done":
      return {
        seq,
        type: "done",
        at,
        status: event.success === false
          ? (event.interrupted ? "interrupted" : event.cancelled ? "cancelled" : "failed")
          : event.awaiting_confirmation ? "awaiting_confirmation" : "completed",
      };
    case "compile_log":
      return { seq, type: "artifact", at, message: event.line?.slice(0, 120) };
    case "progress":
      return { seq, type: "progress", at, message: event.message };
    case "todo_update":
      return { seq, type: "todo", at, message: String(event.todos?.length ?? 0) };
    case "plan_update":
      return { seq, type: "plan", at, message: event.plan?.title ?? "" };
    case "change_set_updated":
      return { seq, type: "change_set", at, message: event.change_set_id };
    case "change_item_updated":
      return {
        seq, type: "change_item", at,
        message: event.item?.path || event.item?.tool_name || "",
      };
    case "change_reviewed":
      return { seq, type: "change_review", at, message: event.change_set_id };
    case "change_reverted":
      return { seq, type: "change_revert", at, message: event.item_id || event.change_set_id };
    case "validation_started":
      return { seq, type: "validation_start", at, message: event.validation_id };
    case "validation_finished":
      return { seq, type: "validation_end", at, message: event.change_set_id };
    case "permission_mode_changed":
      return { seq, type: "permission", at, message: event.permission_mode };
    case "context_compressing":
      return { seq, type: "context", at, message: event.message };
    default:
      return null;
  }
}

function _appendRunTimeline(sessionId: string, event: ChatSSEEvent, seq: number): void {
  const entry = _timelineEntry(event, seq, sessionId);
  if (!entry) return;
  useChatStore.setState((state) => {
    const previous = state.runTimelineBySession[sessionId] ?? [];
    const last = previous[previous.length - 1];
    let next: RunTimelineEvent[];
    if (last && last.type === entry.type && RUN_TEXT_EVENT_TYPES.has(entry.type)) {
      // Streaming text/logs produce one row per chunk; keep one live row so the
      // timeline stays compact and never renders every token.
      next = [...previous.slice(0, -1), {
        ...entry,
        ...(entry.message !== undefined ? {} : { message: last.message }),
        ...(entry.tool !== undefined ? {} : { tool: last.tool }),
      }];
    } else {
      next = [...previous, entry];
    }
    if (next.length > RUN_TIMELINE_MAX) next = next.slice(next.length - RUN_TIMELINE_MAX);
    return { runTimelineBySession: { ...state.runTimelineBySession, [sessionId]: next } };
  });
}

/** Mirror an accepted event into the visible run lifecycle. */
function _recordRunEventView(sessionId: string, event: ChatSSEEvent, seq: number): void {
  const state = useChatStore.getState();
  const view = state.runViewBySession[sessionId];
  const runId = event.run_id ?? view?.runId;
  if (!runId) return;
  let status: RunStatus | undefined;
  if (event.type === "tool_confirm_required") status = "awaiting_confirmation";
  else if (event.type === "done" && event.interrupted) status = "interrupted";
  else if (event.type === "done" && event.awaiting_confirmation) status = "awaiting_confirmation";
  const currentTool = event.type === "tool_call"
    ? event.tool
    : event.type === "tool_result" && view?.currentTool === event.tool
      ? null
      : undefined;
  const pendingApprovals = event.type === "tool_confirm_required"
    ? (event.count ?? event.calls?.length ?? 0)
    : event.type === "done" ? 0 : undefined;
  useChatStore.setState((s) => {
    const previous = s.runViewBySession[sessionId];
    const base: RunView = previous ?? _emptyRunView(runId, status ?? "running", "live");
    const effectiveStatus = status ?? base.status;
    return {
      runViewBySession: {
        ...s.runViewBySession,
        [sessionId]: {
          ...base,
          runId,
          startedAt: base.startedAt ?? useChatStore.getState().streamingStartTime ?? Date.now(),
          ...(status ? { status } : {}),
          phase: _isLiveRunStatus(effectiveStatus) ? "live" : base.phase,
          lastEventType: event.type,
          lastEventSeq: seq,
          lastEventAt: Date.now(),
          ...(currentTool !== undefined ? { currentTool } : {}),
          ...(pendingApprovals !== undefined ? { pendingApprovals } : {}),
        },
      },
    };
  });
}


/**
 * Apply run sequence semantics: drop duplicates, reject stale runs, and ask
 * for a replay when a gap is observed. Returns true when the event should be
 * applied to the session state.
 */
function _trackChatRunEvent(
  sessionId: string,
  event: ChatSSEEvent,
  meta: SSEEventMeta | undefined,
  onGap: (runId: string, afterSeq: number) => void,
): boolean {
  const info = parseRunEventMeta(event, meta);
  if (event.type === "run_started" && info.runId) {
    _beginActiveRun(sessionId, info.runId, "running", Date.now());
  }
  if (info.seq === undefined) return true;

  const tracked = useChatStore.getState().activeRuns[sessionId] ?? getActiveRun(sessionId);
  const runId = info.runId ?? tracked?.runId;
  if (!runId) return true;
  // An older stream finishing late must not mutate the current run.
  if (tracked && tracked.runId !== runId) return false;

  const lastSeq = tracked?.lastEventSeq ?? 0;
  if (info.seq <= lastSeq) return false;
  if (info.seq > lastSeq + 1) {
    onGap(runId, lastSeq);
    return false; // the replay stream redelivers this sequence in order
  }
  const updated = updateRunSeq(sessionId, runId, info.seq);
  if (updated) {
    _syncActiveRunState(updated, sessionId);
    _recordRunEventView(sessionId, event, info.seq);
    _appendRunTimeline(sessionId, event, info.seq);
  }
  return true;
}

/**
 * Apply a todo snapshot to the owning session, including a completed stream's
 * background session. An empty list is authoritative and replaces older todos.
 */
function _todoUpdatePatch(state: ChatState, sessionId: string, rawTodos: unknown): Partial<ChatState> {
  const todos = normalizeTodos(rawTodos);
  const isActive = state.activeSessionId === sessionId;
  const msgs = [...(isActive ? state.messages : (state.sessionMessages[sessionId] ?? []))];
  let sessionMessages = state.sessionMessages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === "assistant") {
      msgs[i] = { ...msgs[i], todos };
      sessionMessages = { ...state.sessionMessages, [sessionId]: msgs };
      saveSessionMessages(sessionId, msgs);
      break;
    }
  }
  return {
    ...todoUpdateState(state, sessionId, todos),
    sessionMessages,
    ...(isActive ? { messages: msgs, streamingTodos: todos } : {}),
  };
}

/** Store or clear one session's terminal error; the visible field follows the active session. */
function _withSessionStreamingError(
  state: ChatState,
  sessionId: string,
  error: StreamingErrorInfo | null,
): Partial<ChatState> {
  const streamingErrorBySession = { ...state.streamingErrorBySession };
  if (error) streamingErrorBySession[sessionId] = error;
  else delete streamingErrorBySession[sessionId];
  return {
    streamingErrorBySession,
    ...(state.activeSessionId === sessionId ? { streamingError: error } : {}),
  };
}

function _sessionStreamingError(state: ChatState, sessionId: string): StreamingErrorInfo | null {
  return state.streamingErrorBySession[sessionId]
    ?? (state.activeSessionId === sessionId ? state.streamingError : null);
}

/**
 * Apply one chat SSE event to the session that owns the run.
 * Shared by the initial POST stream, gap replays and refresh reattachment.
 */
/** Apply an Agent-initiated permission change to its owning session. */
function _applyAgentPlanModeEvent(
  event: Extract<ChatSSEEvent, { type: "permission_mode_changed" }>,
  sessionId: string,
): void {
  const requested = event.permission_mode === "plan";
  const state = useChatStore.getState();
  if (requested) {
    const session = useSessionStore.getState().sessions.find((item) => item.id === sessionId);
    const alreadyKnown = state.planModeBySession[sessionId] === true
      || session?.planModeRequested === true;
    useSessionStore.getState().updateSessionMeta(sessionId, { planModeRequested: true });
    if (!alreadyKnown && state.activeSessionId === sessionId) {
      const english = useAppStore.getState().lang === "en";
      useToastStore.getState().showInfo(english
        ? "The Agent switched this session to plan mode (read-only)."
        : "Agent 已进入计划模式（只读），完成调研后会提交计划。");
    }
  }
  useChatStore.setState((s) => ({
    planModeBySession: { ...s.planModeBySession, [sessionId]: requested },
    ...(s.activeSessionId === sessionId ? { planMode: requested } : {}),
  }));
}

function _applyChatEvent(event: ChatSSEEvent, requestSessionId: string, terminal: ChatTerminalState): void {
  if (event.type === "permission_mode_changed") {
    _applyAgentPlanModeEvent(event, requestSessionId);
    return;
  }
  if (event.type === "error") {
    terminal.outcome = "error";
    const errPayload = event as { type: "error"; code?: string; message: string; detail?: string };
    const code = errPayload.code ?? "UNKNOWN";
    // MODEL_NOT_FOUND: backend reports the requested model is unavailable.
    // INTERRUPTED: the process restarted while the run was still going.
    const friendlyMessage = code === "INTERRUPTED"
      ? (errPayload.message || "运行已中断（后端重启或连接中断），可重试或继续")
      : code === "MODEL_NOT_FOUND" && !errPayload.message
        ? "模型不存在，请在设置中检查模型名"
        : (errPayload.message ?? "未知错误");
    terminal.errorMessage = friendlyMessage;
    getLog()("error", "chat", `SSE 错误: ${friendlyMessage}`);
    useChatStore.setState((s) => _withSessionStreamingError(s, requestSessionId, {
      code,
      message: friendlyMessage,
      detail: errPayload.detail ?? "",
    }));
    if (code === "INTERRUPTED") {
      _markRunInterrupted(requestSessionId, event.run_id, friendlyMessage);
    }
    useChatStore.getState().stopStreaming(friendlyMessage);
    return;
  }
  if (event.type === "run_started") return;
  if (event.type === "tool_confirm_required" && terminal.outcome !== "error") {
    terminal.outcome = "awaiting_confirmation";
  }
  const sse = event;
  // ★ 始终写入发起请求时的会话，而非当前活跃会话
  useChatStore.setState((s) => {
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
        if (lastStep && lastStep.type === "thinking"
            && (lastStep.placeholder === true || lastStep.content === THINKING_PLACEHOLDER_TEXT)) {
          steps[steps.length - 1] = {
            ...lastStep, content: thinkContent, source: thinkSource, placeholder: false,
          };
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
        // Backend keepalive: no UI state change.
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
            const stepsToUpdate = [...existingSteps];
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
        const newStatus: ActivityStep["status"] = sse.success === false ? "error" : "done";
        // image_generation: extract image_base64/image_url and append as ImagePart
        const imagePart = sse.tool === "image_generation" ? _extractImagePart(sse.result) : null;
        // File-editing tools: extract file_path for "查看 diff" button
        const filePath = _extractFilePath(sse.tool, sse.result);
        const changeItemId = _extractChangeItemId(sse.tool, sse.result);

        if (isActive) {
          const steps = s.streamingSteps.map((step) =>
            step.call_id === sse.call_id
              ? {
                  ...step, status: newStatus, result: resultText,
                  duration: sse.duration, file_path: filePath ?? step.file_path,
                  change_item_id: changeItemId ?? step.change_item_id,
                }
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
                ? {
                    ...step, status: newStatus, result: resultText,
                    duration: sse.duration, file_path: filePath ?? step.file_path,
                    change_item_id: changeItemId ?? step.change_item_id,
                  }
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
        return _todoUpdatePatch(s, sid, sse.todos);
      }

      case "plan_update": {
        _planAutoRetryUsed.delete(sid);
        return planUpdateState(s, sid, sse.plan ?? null);
      }

      case "change_set_updated":
      case "change_item_updated":
      case "change_reviewed":
      case "change_reverted":
      case "validation_started":
      case "validation_finished": {
        void useChatStore.getState().loadChangeSets(sid);
        return {};
      }

      case "done": {
        if (terminal.outcome !== "error" && terminal.outcome !== "cancelled") {
          if (sse.cancelled) {
            terminal.outcome = "cancelled";
          } else if (sse.interrupted) {
            terminal.outcome = "error";
            terminal.errorMessage = "运行已中断（后端重启或连接中断），可重试或继续";
          } else if (sse.success === false) {
            terminal.outcome = "error";
          } else if (sse.awaiting_confirmation || sse.completed === false) {
            terminal.outcome = "awaiting_confirmation";
          } else {
            terminal.outcome = "success";
          }
          if (terminal.outcome === "error") {
            terminal.errorMessage = terminal.errorMessage || "回答未能完成";
          }
        }
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
        // v2-T4: Agent paused for HITL — show ConfirmDialog for this session only
        return {
          pendingConfirm: {
            calls: (sse.calls ?? []) as PendingConfirm["calls"],
            count: sse.count ?? (sse.calls?.length ?? 0),
            sessionId: sid,
          },
        };
      }

      default:
        return {};
    }
  });
}

/** Finalize a chat stream on its terminal `done` event. */
function _finalizeChatStream(sessionId: string, terminal: ChatTerminalState, startTime: number): void {
  if (terminal.finalized) return;
  terminal.finalized = true;
  const finishedSteps = useChatStore.getState().streamingSteps;
  const wasPlanMode = useChatStore.getState().planModeBySession[sessionId]
    ?? useChatStore.getState().planMode;

  if (terminal.outcome === "error") {
    if (!_sessionStreamingError(useChatStore.getState(), sessionId)) {
      useChatStore.setState((s) => _withSessionStreamingError(s, sessionId, {
        code: "INTERNAL_ERROR",
        message: terminal.errorMessage,
        detail: "",
      }));
    }
    const state = useChatStore.getState();
    if (state.isStreaming || state.streamingSessionId || state.currentSseRequest) {
      state.stopStreaming(terminal.errorMessage);
    }
    const viewStatus = useChatStore.getState().runViewBySession[sessionId]?.status;
    _clearRunTracking(sessionId);
    _setRunViewTerminal(
      sessionId,
      viewStatus === "interrupted" ? "interrupted" : "failed",
      terminal.errorMessage,
    );
    return;
  }

  const awaitingConfirmation = terminal.outcome === "awaiting_confirmation";
  const cancelledRun = terminal.outcome === "cancelled";
  const finishedAt = Date.now();
  const durationMs = finishedAt - startTime;
  const usage = useChatStore.getState()._pendingUsage;
  const finalMsgId = useChatStore.getState().messages[useChatStore.getState().messages.length - 1]?.id ?? '';
  console.info('[ChatStore] stream_done session=%s msg_id=%s', sessionId, finalMsgId);
  if (awaitingConfirmation) {
    getLog()("info", "chat", "Agent 等待工具操作确认");
  } else if (cancelledRun) {
    getLog()("info", "chat", "运行已取消");
  } else {
    getLog()("ok", "chat", `回答完成 (${(durationMs / 1000).toFixed(2)}秒)${usage ? ` tokens=${usage.totalTokens}` : ''}`);
  }

  useChatStore.setState((s) => {
    const sid = sessionId;
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
        ? {
            durationMs,
            steps: awaitingConfirmation ? s.streamingSteps : _finalizePendingSteps(s.streamingSteps),
            status: awaitingConfirmation ? "running" as const : "done" as const,
          }
        : (!isActive && m.activity
            ? {
                ...m.activity,
                durationMs,
                steps: awaitingConfirmation ? (m.activity.steps || []) : _finalizePendingSteps(m.activity.steps || []),
                status: awaitingConfirmation ? "running" as const : "done" as const,
              }
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

    // 只有当前活跃会话是请求会话时，才修改 isStreaming/currentSseRequest
    if (isActive) {
      return {
        ..._withSessionStreamingError(s, sid, null),
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
        ..._withSessionStreamingError(s, sid, null),
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

  // If plan mode produced research but no plan, ask once for submit_plan.
  const afterState = useChatStore.getState();
  const planForRun = afterState.sessionPlans[sessionId] ?? null;
  const runForPlan = afterState.activeRuns[sessionId] ?? getActiveRun(sessionId);
  const planThisRun = Boolean(planForRun && runForPlan && planForRun.run_id === runForPlan.runId);
  const toolCallCount = finishedSteps.filter((step) => step.type === "tool").length;
  if (
    wasPlanMode
    && terminal.outcome === "success"
    && afterState.activeSessionId === sessionId
    && !planThisRun
    && toolCallCount >= 3
    && !_planAutoRetryUsed.has(sessionId)
  ) {
    _planAutoRetryUsed.add(sessionId);
    useToastStore.getState().showInfo("模型没有提交计划，正在让它补交…");
    setTimeout(() => {
      void useChatStore.getState().sendMessage(
        "请把上面的调研结论用 submit_plan 提交成正式计划，未确认项放进 open_questions，不要再输出正文计划。",
      );
    }, 0);
  }

  // Recovery: a paused run stays resumable; any other terminal outcome is done.
  const record = useChatStore.getState().activeRuns[sessionId] ?? getActiveRun(sessionId);
  if (record) {
    if (awaitingConfirmation) _markRunStatus(sessionId, record.runId, "awaiting_confirmation");
    else _clearRunTracking(sessionId);
  }
  if (awaitingConfirmation) {
    _patchRunView(sessionId, {
      status: "awaiting_confirmation",
      phase: "live",
      finishedAt: null,
      pendingApprovals: useChatStore.getState().pendingConfirm?.sessionId === sessionId
        ? useChatStore.getState().pendingConfirm?.count ?? 0
        : 0,
    });
  } else {
    const viewStatus = useChatStore.getState().runViewBySession[sessionId]?.status;
    // The bar and ActivityBlock must describe the same client turn; backend
    // run timestamps can start after request validation, so pin both edges to
    // the send/onDone clock.
    _patchRunView(sessionId, { startedAt: startTime, finishedAt });
    _setRunViewTerminal(
      sessionId,
      viewStatus === "interrupted" ? "interrupted" : cancelledRun ? "cancelled" : "completed",
      null,
      finishedAt,
    );
  }

  // 会话元数据仅在 persistLastTurn 确认 CRUD 写入成功后更新。
  void useChatStore.getState().persistLastTurn(sessionId).then((saved) => {
    // 多 tab 同步：通知其他 tab 此会话消息已变化
    if (saved) post('messages_changed', sessionId);
  });
}

/** Surface a stream failure without discarding the run record (it may still be running). */
function _failChatStream(sessionId: string, terminal: ChatTerminalState, err: Error): void {
  terminal.outcome = "error";
  terminal.errorMessage = err.message;
  console.warn('[ChatStore] stream_error session=%s error=%s', sessionId, err.message);
  getLog()("error", "chat", `SSE 连接错误: ${err.message}`);
  const isAuthError = /(^|\D)40[13](\D|$)|AUTH_REQUIRED|AUTH_INVALID|Unauthorized|Forbidden|认证失败|未提供认证/i
    .test(err.message);
  // A 401/403 must read as an authentication problem (and point at the login
  // hint), not as a generic dropped connection.
  useChatStore.setState((s) => ({
    ..._withSessionStreamingError(s, sessionId, isAuthError
      ? {
          code: "AUTH_FAILED",
          message: "认证失败：请先登录或检查访问令牌后重试。",
          detail: err.message,
        }
      : {
          code: "NETWORK_ERROR",
          message: err.message,
          detail: "",
        }),
    needsApiKey: isAuthError ? true : s.needsApiKey,
  }));
  // SubTask 8.5: 清理 backgroundSseRequests 对应条目（后台流错误也要清理）
  useChatStore.setState((s) => {
    if (!s.backgroundSseRequests.has(sessionId)) return {};
    const newBg = new Map(s.backgroundSseRequests);
    newBg.delete(sessionId);
    return { backgroundSseRequests: newBg };
  });
  useChatStore.getState().stopStreaming(err.message);
}


export const useChatStore = create<ChatState>((set, get) => {
  type InitialComposerSnapshot = {
    sessionId: string;
    draft: string;
    attachments: Attachment[];
    quoted?: Message;
  };
  let initialSessionSendInProgress = false;
  let pendingInitialComposers: InitialComposerSnapshot[] = [];

  function captureInitialComposer(
    sessionId: string,
    content: string,
    attachments?: Attachment[],
    quoted?: Message,
  ): InitialComposerSnapshot {
    const state = get();
    return {
      sessionId,
      draft: state.drafts[sessionId] ?? content,
      attachments: state.sessionAttachments[sessionId] ?? attachments ?? [],
      quoted,
    };
  }

  function mergeInitialQuotes(quotes: Array<Message | undefined>): Message | undefined {
    const uniqueQuotes = [...new Map(
      quotes.filter((quote): quote is Message => Boolean(quote)).map((quote) => [quote.id, quote])
    ).values()];
    if (uniqueQuotes.length === 0) return undefined;
    if (uniqueQuotes.length === 1) return uniqueQuotes[0];

    return {
      id: `recovered-quotes-${uniqueQuotes.map((quote) => quote.id).join("-")}`,
      role: "system",
      content: uniqueQuotes.map((quote, index) => {
        const text = typeof quote.content === "string"
          ? quote.content
          : quote.content.map((part) => part.type === "text" ? part.text : "[图片引用，详见原消息]").join("\n");
        return `引用 ${index + 1}（${quote.role}）：\n${text}`;
      }).join("\n\n"),
      timestamp: Math.max(...uniqueQuotes.map((quote) => quote.timestamp)),
    };
  }

  function mergeInitialComposerSnapshots(snapshots: InitialComposerSnapshot[]): InitialComposerSnapshot | null {
    const first = snapshots[0];
    if (!first) return null;
    const attachments = [...new Map(
      snapshots.flatMap((snapshot) => snapshot.attachments).map((attachment) => [attachment.id, attachment])
    ).values()];
    return {
      sessionId: first.sessionId,
      draft: snapshots.map((snapshot) => snapshot.draft).filter(Boolean).join("\n\n"),
      attachments,
      quoted: mergeInitialQuotes(snapshots.map((snapshot) => snapshot.quoted)),
    };
  }

  function restoreInitialComposers(snapshots: InitialComposerSnapshot[]): void {
    const merged = mergeInitialComposerSnapshots(snapshots);
    if (!merged) return;

    const state = get();
    const currentDraft = state.drafts[merged.sessionId];
    const drafts = [merged.draft, currentDraft]
      .filter((draft, index, all): draft is string => Boolean(draft) && all.indexOf(draft) === index);
    if (drafts.length > 0) state.setDraft(merged.sessionId, drafts.join("\n\n"));

    const attachments = [...new Map([
      ...merged.attachments,
      ...(state.sessionAttachments[merged.sessionId] || []),
    ].map((attachment) => [attachment.id, attachment])).values()];
    if (attachments.length > 0) state.setSessionAttachments(merged.sessionId, attachments);

    const appState = useAppStore.getState();
    const quoted = mergeInitialQuotes([
      ...snapshots.map((snapshot) => snapshot.quoted),
      appState.quotedMsg || undefined,
    ]);
    if (quoted) appState.setQuotedMsg(quoted);
  }

  function movePendingComposerToSession(snapshot: InitialComposerSnapshot, sessionId: string): void {
    const state = get();
    const pending = mergeInitialComposerSnapshots(pendingInitialComposers);
    const currentDraft = state.drafts[snapshot.sessionId];
    const draftToMove = [
      pending?.draft,
      currentDraft && currentDraft !== snapshot.draft && currentDraft !== pending?.draft ? currentDraft : "",
    ].filter(Boolean).join("\n\n");
    const currentAttachments = state.sessionAttachments[snapshot.sessionId];
    const attachmentsToMove = [...new Map([
      ...(pending?.attachments || []),
      ...(currentAttachments && currentAttachments !== snapshot.attachments ? currentAttachments : []),
    ].map((attachment) => [attachment.id, attachment])).values()];

    set((current) => {
      const drafts = { ...current.drafts };
      const sessionAttachments = { ...current.sessionAttachments };
      delete drafts[snapshot.sessionId];
      delete sessionAttachments[snapshot.sessionId];
      if (draftToMove) drafts[sessionId] = draftToMove;
      if (attachmentsToMove.length > 0) sessionAttachments[sessionId] = attachmentsToMove;
      return { drafts, sessionAttachments };
    });

    const pendingQuotes = pendingInitialComposers.map((composer) => composer.quoted);
    const currentQuote = useAppStore.getState().quotedMsg;
    const quoted = mergeInitialQuotes([...pendingQuotes, currentQuote || undefined]);
    if (quoted) useAppStore.getState().setQuotedMsg(quoted);
    pendingInitialComposers = [];
  }

  async function verifyKbScopeForSession(sessionId: string, ids: string[]): Promise<boolean> {
    if (ids.length === 0) return true;
    try {
      const data = await apiGet<{ collections?: Array<{ id?: unknown; enabled?: unknown }> }>("kb/collections");
      if (!Array.isArray(data?.collections)) throw new Error("知识库列表响应无效");
      const enabledIds = new Set(
        data.collections
          .filter((collection) => typeof collection.id === "string" && collection.enabled !== false)
          .map((collection) => collection.id as string)
      );
      const unavailable = ids.some((id) => !enabledIds.has(id));
      if (get().activeSessionId === sessionId) {
        if (unavailable) set({ kbScopeIssue: "unavailable" });
        else if (get().kbScopeIssue === "verification_failed") set({ kbScopeIssue: null });
      }
      return !unavailable;
    } catch {
      if (get().activeSessionId === sessionId) set({ kbScopeIssue: "verification_failed" });
      return false;
    }
  }

  function updatePendingSaveStatus(sessionId: string, status?: "saving" | "pending"): void {
    set((state) => {
      const next = { ...state.pendingSaveBySession };
      if (status) next[sessionId] = status;
      else delete next[sessionId];
      return { pendingSaveBySession: next };
    });
  }

  function _markResumeAssistantPending(sessionId: string): void {
    const state = get();
    const messages = state.activeSessionId === sessionId
      ? state.messages
      : state.sessionMessages[sessionId] || [];
    const assistant = messages[messages.length - 1];
    if (assistant?.role !== "assistant") return;

    if (!_localPendingSaveIds(messages).includes(assistant.id)) {
      _markLocalPendingSaveIds(sessionId, [assistant.id]);
    }
    if (!_loadPendingSaveIds(sessionId).includes(assistant.id)) {
      _rememberPendingSaveIds(sessionId, [assistant.id]);
    }
    updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId) ?? "pending");
  }

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
  /** Handle SSE events against the session that owns this resume stream. */
  function _handleResumeEvent(evt: ChatSSEEvent, sid: string, resumeSteps: ActivityStep[]): void {
    if (evt.type === "context_compressing") {
      if (get().activeSessionId === sid) {
        set({ isCompressing: true, compressingMessage: evt.message || "正在压缩上下文..." });
      }
      return;
    }
    if (evt.type === "text") { _appendResumeText(evt, sid); return; }
    if (evt.type === "thinking") { _appendResumeThinking(evt, sid, resumeSteps); return; }
    if (evt.type === "source") { _appendResumeSource(evt, sid); return; }
    if (evt.type === "tool_call") { _appendResumeToolCall(evt, sid, resumeSteps); return; }
    if (evt.type === "tool_result") { _updateResumeToolResult(evt, sid, resumeSteps); return; }
    if (evt.type === "todo_update") {
      set((s) => _todoUpdatePatch(s, sid, evt.todos));
      return;
    }
    if (evt.type === "plan_update") {
      set((s) => planUpdateState(s, sid, evt.plan ?? null));
      return;
    }
    if (evt.type === "permission_mode_changed") {
      _applyAgentPlanModeEvent(evt, sid);
      return;
    }
    if (evt.type === "tool_confirm_required") {
      set({ pendingConfirm: { calls: evt.calls, count: evt.count, sessionId: sid } });
      return;
    }
    if (evt.type === "error") {
      set((s) => _withSessionStreamingError(s, sid, {
        code: evt.code ?? "ERROR",
        message: evt.message,
        detail: evt.detail ?? "",
      }));
    }
  }

  /** Append resumed text to the correct session, including while it is in the background. */
  function _appendResumeText(evt: Extract<ChatSSEEvent, { type: "text" }>, sid: string): void {
    set((s) => {
      const chunk = evt.content || "";
      const isActive = s.activeSessionId === sid;
      const msgs = [...(isActive ? s.messages : (s.sessionMessages[sid] || []))];
      const last = msgs[msgs.length - 1];
      if (last?.role !== "assistant") {
        return isActive
          ? { streamingContent: s.streamingContent + chunk, messages: msgs, isCompressing: false }
          : {};
      }
      let streamingContent: string;
      if (typeof last.content === "string") {
        streamingContent = (last.content || "") + chunk;
        msgs[msgs.length - 1] = { ...last, content: streamingContent };
      } else {
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
        streamingContent = parts
          .filter((p): p is Extract<ContentPart, { type: "text" }> => p.type === "text")
          .map((p) => p.text)
          .join("\n\n");
      }
      saveSessionMessages(sid, msgs);
      return {
        sessionMessages: { ...s.sessionMessages, [sid]: msgs },
        ...(isActive ? { streamingContent, messages: msgs, isCompressing: false } : {}),
      };
    });
  }

  function _syncResumeActivity(sid: string, resumeSteps: ActivityStep[]): void {
    set((s) => {
      const isActive = s.activeSessionId === sid;
      const msgs = [...(isActive ? s.messages : (s.sessionMessages[sid] || []))];
      const last = msgs[msgs.length - 1];
      if (last?.role !== "assistant") return isActive ? { streamingSteps: [...resumeSteps] } : {};
      msgs[msgs.length - 1] = {
        ...last,
        activity: { durationMs: 0, steps: [...resumeSteps], status: "running" },
      };
      saveSessionMessages(sid, msgs);
      return {
        sessionMessages: { ...s.sessionMessages, [sid]: msgs },
        ...(isActive ? { messages: msgs, streamingSteps: [...resumeSteps] } : {}),
      };
    });
  }

  /** Append thinking chunks to the resume stream's own activity buffer. */
  function _appendResumeThinking(
    evt: Extract<ChatSSEEvent, { type: "thinking" }>,
    sid: string,
    resumeSteps: ActivityStep[],
  ): void {
    const content = evt.content ?? "";
    const source = evt.source;
    const steps = [...resumeSteps];
    const last = steps[steps.length - 1];
    if (source === "reasoning" && last?.type === "thinking" && last.source === "llm") {
      steps[steps.length - 1] = { ...last, content, source: "reasoning" };
    } else if (last?.type === "thinking" && last.source === source) {
      steps[steps.length - 1] = { ...last, content: (last.content || "") + content };
    } else {
      if (last?.type === "thinking") steps[steps.length - 1] = { ...last, status: "done" };
      steps.push({ type: "thinking", id: `h-${Date.now()}`, content, source });
    }
    resumeSteps.splice(0, resumeSteps.length, ...steps);
    _syncResumeActivity(sid, resumeSteps);
  }

  /** Append source refs to the correct resumed assistant message. */
  function _appendResumeSource(evt: Extract<ChatSSEEvent, { type: "source" }>, sid: string): void {
    const baseSrc: SourceRef = { id: evt.id ?? `src-${Date.now()}`, title: evt.title ?? "未知来源", doc: evt.doc ?? "", page: evt.page ?? 0, chunk_index: evt.chunk_index, page_start: evt.page_start, page_end: evt.page_end, section_title: evt.section_title, source_url: evt.source_url, category: evt.category, chunk_method: evt.chunk_method, score: evt.score ?? 0, score_percentage: evt.score_percentage, relevance_level: evt.relevance_level, citation: evt.citation, excerpt: evt.excerpt ?? "", kb_id: evt.kb_id, kb_name: evt.kb_name, small_chunk_id: evt.small_chunk_id, big_chunk_id: evt.big_chunk_id, small_chunk_text: evt.small_chunk_text };
    set((s) => {
      const isActive = s.activeSessionId === sid;
      const msgs = [...(isActive ? s.messages : (s.sessionMessages[sid] || []))];
      const last = msgs[msgs.length - 1];
      const src: SourceRef = { ...baseSrc, messageId: last?.id };
      if (last?.role === "assistant") msgs[msgs.length - 1] = { ...last, sources: [...(last.sources || []), src] };
      saveSessionMessages(sid, msgs);
      return {
        sessionMessages: { ...s.sessionMessages, [sid]: msgs },
        ...(isActive ? { streamingSources: [...s.streamingSources, src], sources: [...s.streamingSources, src], messages: msgs } : {}),
      };
    });
  }

  function _appendResumeToolCall(evt: Extract<ChatSSEEvent, { type: "tool_call" }>, sid: string, resumeSteps: ActivityStep[]): void {
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
    resumeSteps.push(step);
    _syncResumeActivity(sid, resumeSteps);
  }

  function _updateResumeToolResult(evt: Extract<ChatSSEEvent, { type: "tool_result" }>, sid: string, resumeSteps: ActivityStep[]): void {
    const resultText = _flattenToolResult(evt.result);
    const status: ActivityStep["status"] = evt.success === false ? "error" : "done";
    const steps = resumeSteps.map((step) =>
      step.call_id === evt.call_id ? { ...step, status, result: resultText, duration: evt.duration } : step
    );
    resumeSteps.splice(0, resumeSteps.length, ...steps);
    _syncResumeActivity(sid, resumeSteps);
  }

  /** Finalize resume stream — attach activity to last assistant message.
   *  Content/sources already updated in real-time by _appendResumeText/_appendResumeSource
   *  (aligns with main stream onDone, which does NOT re-merge streamingContent). */
  function _finalizeResume(
    sid: string,
    resumeSteps: ActivityStep[],
    outcome: "success" | "error" | "awaiting_confirmation" | "stopped" = "success",
    errorMessage?: string,
  ): void {
    set((s) => {
      const isActive = s.activeSessionId === sid;
      const msgs = [...(isActive ? s.messages : (s.sessionMessages[sid] || []))];
      const last = msgs[msgs.length - 1];
      if (last?.role === "assistant") {
        const content = outcome === "error"
          ? (typeof last.content === "string"
              ? `${last.content || ""}${last.content ? "\n\n" : ""}❌ ${errorMessage}`
              : (() => {
                  const existingText = last.content
                    .filter((part): part is Extract<ContentPart, { type: "text" }> => part.type === "text")
                    .map((part) => part.text)
                    .join("\n\n");
                  return _mergeTextIntoParts(last.content, `${existingText}${existingText ? "\n\n" : ""}❌ ${errorMessage}`);
                })())
          : last.content;
        const existingSteps = last.activity?.steps || [];
        const steps = resumeSteps.length > 0 ? resumeSteps : existingSteps;
        const shouldUpdateActivity = resumeSteps.length > 0 || outcome !== "success";
        const finalSteps = outcome === "awaiting_confirmation"
          ? [...steps]
          : outcome === "error"
            ? steps.map((step) => step.status === "pending"
                ? {
                    ...step,
                    status: "error" as const,
                    duration: step.startTime ? Date.now() - step.startTime : step.duration,
                  }
                : step)
            : _finalizePendingSteps(steps);
        msgs[msgs.length - 1] = {
          ...last,
          ...(outcome === "error" ? { content } : {}),
          ...(shouldUpdateActivity
            ? {
                activity: {
                  durationMs: 0,
                  steps: finalSteps,
                  status: outcome === "error" ? "error" as const : outcome === "awaiting_confirmation" ? "running" as const : "done" as const,
                },
              }
            : {}),
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
      const isCurrentStream = s.streamingSessionId === sid;
      return {
        sessionMessages: newSM,
        ...(isActive ? { messages: msgs } : {}),
        backgroundSseRequests: newBg,
        ...(isCurrentStream ? {
          isStreaming: false,
          isCompressing: false,
          streamingContent: "",
          streamingSteps: [],
          streamingSources: [],
          streamingTodos: [],
          // Awaiting confirmation is paused; terminal outcomes must release stream ownership.
          streamingSessionId: outcome === "awaiting_confirmation" ? s.streamingSessionId : null,
          streamingStartTime: outcome === "awaiting_confirmation" ? s.streamingStartTime : null,
          currentSseRequest: null,
          _lastAgentPayload: outcome === "awaiting_confirmation" ? s._lastAgentPayload : null,
          // A terminal error wins over any confirmation request emitted earlier in this resume.
          ...(outcome === "error" ? { pendingConfirm: null } : {}),
          ...(outcome === "awaiting_confirmation"
            ? {}
            : _withSessionStreamingError(
                s,
                sid,
                outcome === "error"
                  ? (_sessionStreamingError(s, sid)
                      ?? { code: "ERROR", message: errorMessage || "Agent 恢复未能完成", detail: "" })
                  : null,
              )),
        } : {}),
      };
    });

    // The resume stream has reached a terminal event. Save the completed
    // user/assistant pair through the same idempotent path as ordinary chat.
    void get().persistLastTurn(sid).then((saved) => {
      if (saved) post("messages_changed", sid);
    });

    // Recovery: a paused run stays resumable; terminal outcomes release the run.
    const record = get().activeRuns[sid] ?? getActiveRun(sid);
    if (record) {
      if (outcome === "awaiting_confirmation") _markRunStatus(sid, record.runId, "awaiting_confirmation");
      else _clearRunTracking(sid);
    }
  }

  /** Update the terminal state of a resume stream from one SSE event. */
  function _updateResumeTerminal(evt: ChatSSEEvent, terminal: ResumeTerminalState): void {
    if (evt.type === "error") {
      terminal.outcome = "error";
      terminal.errorMessage = evt.message;
    } else if (evt.type === "tool_confirm_required") {
      if (terminal.outcome !== "error") terminal.outcome = "awaiting_confirmation";
    } else if (evt.type === "done" && terminal.outcome !== "error") {
      if (
        terminal.decision === "stop" && evt.success === false &&
        (evt as typeof evt & { reason?: string }).reason === "user stopped"
      ) {
        terminal.outcome = "stopped";
      } else if (evt.success === false && !evt.awaiting_confirmation) {
        terminal.outcome = "error";
        terminal.errorMessage = "Agent 恢复未能完成";
      } else if (evt.awaiting_confirmation || evt.completed === false) {
        terminal.outcome = "awaiting_confirmation";
      } else if (terminal.outcome !== "awaiting_confirmation") {
        terminal.outcome = "success";
      }
    }
  }

  /**
   * Fetch missed events with `GET runs/{id}/events?after=<seq>`.
   * Used for detected gaps in a live stream and for reattaching after refresh.
   */
  function _replayRunEvents(
    sid: string,
    runId: string,
    afterSeq: number,
    mode: "chat" | "resume",
    resumeContext?: { terminal: ResumeTerminalState; steps: ActivityStep[] },
  ): void {
    if (!sid || !runId || _runReplayInFlight.has(sid)) return;
    _runReplayInFlight.add(sid);
    const controller = new AbortController();
    const terminal: ChatTerminalState = { outcome: "running", errorMessage: "回答未能完成", finalized: false };
    const onGap = (rid: string, after: number) => {
      _replayRunEvents(sid, rid, after, mode, resumeContext);
    };

    void apiSSE(`runs/${encodeURIComponent(runId)}/events`, undefined, {
      onEvent: (event, meta) => {
        const evt = event as ChatSSEEvent;
        if (!_trackChatRunEvent(sid, evt, meta, onGap)) return;
        if (mode === "resume" && resumeContext) {
          _updateResumeTerminal(evt, resumeContext.terminal);
          _handleResumeEvent(evt, sid, resumeContext.steps);
        } else {
          _applyChatEvent(evt, sid, terminal);
        }
      },
      onDone: () => {
        if (mode === "resume" && resumeContext) {
          const outcome = resumeContext.terminal.outcome === "running"
            ? "success"
            : resumeContext.terminal.outcome;
          _finalizeResume(sid, resumeContext.steps, outcome, resumeContext.terminal.errorMessage);
        } else {
          _finalizeChatStream(sid, terminal, Date.now());
        }
      },
      onError: (err) => {
        getLog()("warn", "chat", `运行事件补放失败: ${runId} - ${err.message}`);
      },
    }, controller, { method: "GET", after: afterSeq, reconnect: { maxAttempts: 3 } })
      .finally(() => { _runReplayInFlight.delete(sid); });
  }

  /** Reattach to a persisted unfinished run after refresh or session load. */
  async function _reattachRun(sessionId: string): Promise<void> {
    if (!sessionId) return;
    if (_runReattachInFlight.has(sessionId)) return;
    const liveState = get();
    if (liveState.isStreaming && liveState.streamingSessionId === sessionId) return;
    if (liveState.backgroundSseRequests.has(sessionId)) return;

    const localRecord = get().activeRuns[sessionId] ?? getActiveRun(sessionId);
    _runReattachInFlight.add(sessionId);
    try {
      // Ask the server first: the run may have started in another tab while
      // this one has no local record. Older backends return null and we fall
      // back to the locally tracked run id.
      const activeRun = await fetchActiveRun(sessionId);
      const activeRunId = runStateRunId(activeRun);
      const runId = activeRunId ?? localRecord?.runId;
      if (!runId) {
        _runStateBySession.delete(sessionId);
        // A resume request may have been accepted while the backend still has
        // no active run; never leave the bar spinning in "restoring".
        if (get().runViewBySession[sessionId]?.phase === "restoring") {
          const notice = get().runRecoveryNoticeBySession[sessionId];
          if (notice) {
            _applyRunStateToView(sessionId, {
              run_id: notice.runId,
              status: "interrupted",
              error_message: notice.message,
            }, "terminal");
          } else {
            _setRunViewTerminal(sessionId, "failed", "无法重新连接到这次执行");
          }
        }
        return;
      }
      const localMatches = localRecord?.runId === runId;
      let runState = activeRun;
      if (!runState && localMatches) {
        runState = await fetchRunState(runId);
      }
      const status = runState?.status ?? (localMatches ? localRecord?.status ?? "running" : "running");
      if (runState) _runStateBySession.set(sessionId, runState);

      getLog()(
        "info", "chat",
        `尝试恢复未完成运行: session=${sessionId} run=${runId} status=${status} after=${localMatches ? localRecord?.lastEventSeq : "server"}`,
      );

      const mergedState: RunStateResponse = {
        ...(runState ?? {}),
        ...(activeRunId ? {} : { run_id: runId }),
        status,
      };

      // Show "正在恢复" before any replay starts (only for live runs; terminal
      // runs jump straight to their final state).
      if (!isTerminalRunStatus(status)) {
        _applyRunStateToView(sessionId, mergedState, "restoring");
      }

      if (isTerminalRunStatus(status)) {
        // The run finished while this page was away; the fetched messages are
        // authoritative and there is nothing left to stream.
        const alreadyReloaded = _terminalRunIds.has(runId);
        _terminalRunIds.add(runId);
        _applyRunStateToView(sessionId, mergedState, "terminal");
        _clearRunTracking(sessionId);
        if (status === "completed" && runState && !alreadyReloaded) {
          await get().fetchMessages(sessionId);
        }
        return;
      }

      if (status === "awaiting_confirmation") {
        // Rebuild the HITL card from persisted approvals; never invent one.
        const calls = pendingApprovalsToCalls(runState?.pending_approvals);
        if (calls.length > 0) {
          set({ pendingConfirm: { calls, count: calls.length, sessionId } });
        }
        _markRunStatus(sessionId, runId, "awaiting_confirmation");
        _patchRunView(sessionId, {
          status: "awaiting_confirmation",
          phase: "live",
          pendingApprovals: calls.length,
        });
        if (!get().streamingSessionId) set({ streamingSessionId: sessionId });
        return;
      }

      if (status === "interrupted") {
        // Replay the tail so already-persisted events render once, but the
        // view and notice never claim the run is still alive.
        _applyRunStateToView(sessionId, mergedState, "terminal");
      }

      const msgs = get().sessionMessages[sessionId]
        ?? (get().activeSessionId === sessionId ? get().messages : []);
      const lastAssistant = [...msgs].reverse().find((message) => message.role === "assistant");
      const restoredContent = lastAssistant
        ? typeof lastAssistant.content === "string"
          ? lastAssistant.content
          : lastAssistant.content
              .filter((part): part is Extract<ContentPart, { type: "text" }> => part.type === "text")
              .map((part) => part.text)
              .join("\n\n")
        : "";

      // Replay from the last applied sequence when this tab tracked the run.
      // When adopting a server-side run without local bookkeeping, start after
      // the server sequence if a partial answer is already visible; otherwise
      // rebuilding from 0 would duplicate text. No local record + no visible
      // content replays from the beginning so the answer can be rebuilt.
      const serverSeq = runState?.last_event_seq ?? 0;
      const afterSeq = localMatches
        ? localRecord?.lastEventSeq ?? 0
        : (restoredContent.trim() ? serverSeq : 0);
      if (!localMatches) {
        const adopted = startActiveRun(sessionId, runId, status);
        const synced = afterSeq > 0 ? updateRunSeq(sessionId, runId, afterSeq) ?? adopted : adopted;
        _syncActiveRunState(synced, sessionId);
      }

      const controller = new AbortController();
      set((s) => {
        const isActive = s.activeSessionId === sessionId;
        if (!isActive) {
          const backgroundSseRequests = new Map(s.backgroundSseRequests);
          backgroundSseRequests.set(sessionId, controller);
          return { backgroundSseRequests };
        }
        return {
          isStreaming: true,
          streamingSessionId: sessionId,
          streamingStartTime: Date.now(),
          streamingContent: restoredContent,
          streamingSteps: lastAssistant?.activity?.steps ?? [],
          streamingSources: lastAssistant?.sources ?? [],
          streamingTodos: lastAssistant?.todos ?? [],
          currentSseRequest: controller,
          isCompressing: false,
        };
      });

      const terminal: ChatTerminalState = { outcome: "running", errorMessage: "回答未能完成", finalized: false };
      void apiSSE(`runs/${encodeURIComponent(runId)}/events`, undefined, {
        onEvent: (event, meta) => {
          const evt = event as ChatSSEEvent;
          if (!_trackChatRunEvent(sessionId, evt, meta, (trackedRunId, after) => {
            _replayRunEvents(sessionId, trackedRunId, after, "chat");
          })) return;
          if (evt.type === "tool_confirm_required") {
            _markRunStatus(sessionId, runId, "awaiting_confirmation");
          }
          _applyChatEvent(evt, sessionId, terminal);
        },
        onDone: () => _finalizeChatStream(sessionId, terminal, Date.now()),
        onError: (err) => _failChatStream(sessionId, terminal, err),
      }, controller, {
        method: "GET",
        after: afterSeq,
        reconnect: { maxAttempts: 5 },
      });
    } finally {
      _runReattachInFlight.delete(sessionId);
    }
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
    // Keep legacy sessions visible while migrating any shards missing locally.
    const raw = loadFromStorage("messages", {} as Record<string, Message[]>);
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const legacySessions = Object.fromEntries(
        Object.entries(raw).filter(([sessionId]) => !sm[sessionId]?.length)
      );
      migrateMessagesToShards(legacySessions);
      return { ...legacySessions, ...sm };
    }
    return sm;
  })(),
  sources: [],
  isStreaming: false,
  isLoadingMessages: false,
  pagedHistoryBySession: {},
  streamingContent: "",
  streamingSteps: [],
  streamingSources: [],
      streamingTodos: [],
  streamingSessionId: null,
  streamingStartTime: null,
  _pendingUsage: null,
  streamingError: null,
  streamingErrorBySession: {},
  isCompressing: false,
  compressingMessage: "",
  currentSseRequest: null,
  backgroundSseRequests: new Map<string, AbortController>(),
  activeSessionId: _initialActiveSessionId,
  statsOpen: false,
  selectedKbIds: _initialKbScope.ids,
  kbScopeIssue: _initialKbScope.issue,
  needsApiKey: false,
  pendingConfirm: null,
  pendingSaveBySession: _loadPendingSaveStatuses(),
  _lastAgentPayload: null,
  // Recovery R1: persisted unfinished runs + interrupted notices.
  activeRuns: loadRunRecords(),
  runRecoveryNoticeBySession: {},
  runViewBySession: {},
  runTimelineBySession: {},
  // 草稿按 sessionId 隔离，仅存内存（spec 决策 4：刷新后丢失可接受）
  drafts: {},
  // 输入框附件按 sessionId 隔离，仅存内存（避免切换会话时附件串号）
  sessionAttachments: {},
  // 来源查看器状态按 sessionId 隔离（仅内存，刷新后丢失可接受）
  sessionFileViewerSource: {},
  sessionHighlightSourceId: {},
  // Plan mode is request-scoped; todo/plan snapshots are reloaded from the server.
  planMode: false,
  planModeBySession: {},
  sessionTodos: {},
  sessionTodosLoaded: {},
  sessionTodosRevision: {},
  streamingTodosSeen: {},
  sessionPlans: {},
  sessionPlanLoaded: {},
  sessionPlanRevision: {},
  changeSetsBySession: {},
  changeSetsLoadingBySession: {},
  workspaceContextBySession: {},
  selectedChangeItemBySession: {},

  setSelectedKbIds: (ids) => {
    const selectedKbIds = [...new Set(ids.filter((id) => typeof id === "string" && id.length > 0))];
    const saved = _saveKbScope(
      get().activeSessionId,
      selectedKbIds.length > 0 ? { version: 1, mode: "selected", ids: selectedKbIds } : { version: 1, mode: "all" }
    );
    set({ selectedKbIds, kbScopeIssue: saved ? null : "storage_error" });
  },

  setNeedsApiKey: (needsApiKey) => set({ needsApiKey }),

  toggleKbSelection: (kbId) => {
    const state = get();
    if (!kbId || (state.kbScopeIssue !== null && state.kbScopeIssue !== "repair")) return;
    const baseIds = state.kbScopeIssue === "repair" ? [] : state.selectedKbIds;
    const selectedKbIds = baseIds.includes(kbId)
      ? baseIds.filter((id) => id !== kbId)
      : [...baseIds, kbId];
    const mode: StoredKbScope["mode"] = selectedKbIds.length > 0 ? "selected" : state.kbScopeIssue === "repair" ? "repair" : "all";
    const scope: StoredKbScope = mode === "selected"
      ? { version: 1, mode, ids: selectedKbIds }
      : { version: 1, mode };
    const saved = _saveKbScope(state.activeSessionId, scope);
    set({
      selectedKbIds,
      kbScopeIssue: !saved ? "storage_error" : mode === "repair" ? "repair" : null,
    });
  },

  beginKbScopeRepair: () => {
    const sessionId = get().activeSessionId;
    const saved = _saveKbScope(sessionId, { version: 1, mode: "repair" });
    set({ selectedKbIds: [], kbScopeIssue: saved ? "repair" : "storage_error" });
  },

  verifySelectedKbScope: async () => {
    const { activeSessionId, selectedKbIds, kbScopeIssue } = get();
    if (kbScopeIssue && kbScopeIssue !== "verification_failed") return false;
    return verifyKbScopeForSession(activeSessionId, selectedKbIds);
  },

  setDraft: (sessionId, text) => set((s) => ({
    drafts: { ...s.drafts, [sessionId]: text },
  })),

  clearDraft: (sessionId) => set((s) => {
    const next = { ...s.drafts };
    delete next[sessionId];
    return { drafts: next };
  }),

  setSessionAttachments: (sessionId, attachments) => set((s) => ({
    sessionAttachments: { ...s.sessionAttachments, [sessionId]: attachments },
  })),

  clearSessionAttachments: (sessionId) => set((s) => {
    const next = { ...s.sessionAttachments };
    delete next[sessionId];
    return { sessionAttachments: next };
  }),

  setPlanMode: (planMode) => set((s) => (
    s.activeSessionId
      ? {
          planMode,
          planModeBySession: { ...s.planModeBySession, [s.activeSessionId]: planMode },
        }
      : { planMode }
  )),

  leaveAgentPlanMode: async () => {
    const sid = get().activeSessionId;
    if (!sid) {
      set({ planMode: false });
      return true;
    }
    try {
      await setAgentPlanMode(sid, false);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `清除计划模式标记失败: ${sid} - ${errMsg}`);
      useToastStore.getState().showError(`无法退出计划模式：${errMsg}`);
      return false;
    }
    set((s) => ({
      planMode: false,
      planModeBySession: { ...s.planModeBySession, [sid]: false },
    }));
    useSessionStore.getState().updateSessionMeta(sid, { planModeRequested: false });
    return true;
  },

  restorePlanModeForSession: (sessionId) => {
    if (!sessionId) return;
    const session = useSessionStore.getState().sessions.find((s) => s.id === sessionId);
    set((s) => {
      const mapped = s.planModeBySession[sessionId];
      return { planMode: mapped ?? (session?.planModeRequested === true) };
    });
  },

  loadSessionTodos: async (sessionId) => {
    if (!sessionId) return;
    const startRevision = get().sessionTodosRevision[sessionId] ?? 0;
    try {
      const snapshot = await fetchSessionTodosApi(sessionId);
      if ((get().sessionTodosRevision[sessionId] ?? 0) !== startRevision) {
        getLog()("debug", "chat", `丢弃过期任务清单快照: ${sessionId}`);
        return;
      }
      set((s) => todoSnapshotState(s, sessionId, snapshot.todos));
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `加载任务清单失败: ${sessionId} - ${errMsg}`);
    }
  },

  loadSessionPlan: async (sessionId) => {
    if (!sessionId) return;
    const startRevision = get().sessionPlanRevision[sessionId] ?? 0;
    try {
      const plan = await fetchLatestPlan(sessionId);
      if ((get().sessionPlanRevision[sessionId] ?? 0) !== startRevision) {
        getLog()("debug", "chat", `丢弃过期计划快照: ${sessionId}`);
        return;
      }
      set((s) => planSnapshotState(s, sessionId, plan));
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `加载计划失败: ${sessionId} - ${errMsg}`);
    }
  },

  loadChangeSets: async (sessionId) => {
    if (!sessionId) return;
    set((s) => ({
      changeSetsLoadingBySession: {
        ...s.changeSetsLoadingBySession, [sessionId]: true,
      },
    }));
    try {
      const data = await apiGet<ChangeSetsResponse>(
        `sessions/${encodeURIComponent(sessionId)}/change-sets`,
      );
      set((s) => ({
        changeSetsBySession: {
          ...s.changeSetsBySession, [sessionId]: data.change_sets || [],
        },
        workspaceContextBySession: {
          ...s.workspaceContextBySession, [sessionId]: data.workspace,
        },
      }));
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `加载变更集失败: ${sessionId} - ${errMsg}`);
    } finally {
      set((s) => ({
        changeSetsLoadingBySession: {
          ...s.changeSetsLoadingBySession, [sessionId]: false,
        },
      }));
    }
  },

  setSelectedChangeItem: (sessionId, itemId) => set((s) => ({
    selectedChangeItemBySession: {
      ...s.selectedChangeItemBySession, [sessionId]: itemId,
    },
  })),

  clearSessionTodos: async (sessionId) => {
    const sid = sessionId || get().activeSessionId;
    if (!sid) return false;
    try {
      await clearSessionTodosApi(sid);
      // Clear the visible assistant message too: the stream's done handler only
      // copies streamingTodos when it is non-empty, so a message-level list
      // would otherwise resurrect on the next view.
      set((s) => _todoUpdatePatch(s, sid, []));
      return true;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `清空任务清单失败: ${sid} - ${errMsg}`);
      useToastStore.getState().showError(`清空任务清单失败: ${errMsg}`);
      return false;
    }
  },

  approvePlan: async (sessionId, planId) => {
    try {
      const plan = await approveSessionPlan(sessionId, planId);
      set((s) => ({
        ...planSnapshotState(s, sessionId, plan),
        planModeBySession: { ...s.planModeBySession, [sessionId]: false },
        ...(s.activeSessionId === sessionId || !s.activeSessionId ? { planMode: false } : {}),
      }));
      useSessionStore.getState().updateSessionMeta(sessionId, { planModeRequested: false });
      return plan;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `批准计划失败: ${planId} - ${errMsg}`);
      useToastStore.getState().showError(`批准计划失败: ${errMsg}`);
      return null;
    }
  },

  rejectPlan: async (sessionId, planId) => {
    try {
      const plan = await rejectSessionPlan(sessionId, planId);
      // Rejecting a proposal must not silently drop the user out of plan mode:
      // they usually want to revise the request and generate a new plan.
      set((s) => ({
        ...planSnapshotState(s, sessionId, plan),
        planModeBySession: { ...s.planModeBySession, [sessionId]: true },
        ...(s.activeSessionId === sessionId || !s.activeSessionId ? { planMode: true } : {}),
      }));
      const english = useAppStore.getState().lang === "en";
      useToastStore.getState().showInfo(
        english
          ? `Plan v${plan.version} rejected; plan mode stays on so you can revise and regenerate.`
          : `已拒绝计划 v${plan.version}；仍处于计划模式，可修改描述后重新生成计划。`,
      );
      return true;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `拒绝计划失败: ${planId} - ${errMsg}`);
      useToastStore.getState().showError(`拒绝计划失败: ${errMsg}`);
      return false;
    }
  },

  executeApprovedPlan: (plan) => {
    const { activeSessionId, isStreaming } = get();
    if (!plan || plan.status !== "approved") return false;
    if (plan.session_id && plan.session_id !== activeSessionId) return false;
    if (isStreaming) return false;
    const sid = plan.session_id || activeSessionId;
    set((s) => ({
      planMode: false,
      ...(sid ? { planModeBySession: { ...s.planModeBySession, [sid]: false } } : {}),
    }));
    void get().sendMessage(
      APPROVED_PLAN_EXECUTION_MESSAGE,
      undefined,
      undefined,
      undefined,
      { approvedPlanId: plan.id },
    );
    return true;
  },

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

  cleanupSessionSourceState: (sessionId) => {
    clearActiveRun(sessionId);
    _runStateBySession.delete(sessionId);
    set((s) => {
      const nextFv = { ...s.sessionFileViewerSource };
      const nextHl = { ...s.sessionHighlightSourceId };
      const nextAtt = { ...s.sessionAttachments };
      const nextErrors = { ...s.streamingErrorBySession };
      const nextRuns = { ...s.activeRuns };
      const nextViews = { ...s.runViewBySession };
      const nextTimelines = { ...s.runTimelineBySession };
      const nextNotices = { ...s.runRecoveryNoticeBySession };
      delete nextFv[sessionId];
      delete nextHl[sessionId];
      delete nextAtt[sessionId];
      delete nextErrors[sessionId];
      delete nextRuns[sessionId];
      delete nextViews[sessionId];
      delete nextTimelines[sessionId];
      delete nextNotices[sessionId];
      return {
        sessionFileViewerSource: nextFv,
        sessionHighlightSourceId: nextHl,
        sessionAttachments: nextAtt,
        streamingErrorBySession: nextErrors,
        activeRuns: nextRuns,
        runViewBySession: nextViews,
        runTimelineBySession: nextTimelines,
        runRecoveryNoticeBySession: nextNotices,
        ...(s.pendingConfirm?.sessionId === sessionId ? { pendingConfirm: null } : {}),
        ...(s.activeSessionId === sessionId ? { streamingError: null } : {}),
      };
    });
  },

  resumeAgent: (decision) => {
    const payload = get()._lastAgentPayload;
    const confirm = get().pendingConfirm;
    const sid = confirm?.sessionId || get().streamingSessionId || get().activeSessionId;
    const runRecord = get().activeRuns[sid] ?? getActiveRun(sid);
    const runId = runRecord?.runId;
    const callId = confirm?.calls[0]?.call_id;
    // After a refresh the raw request body is gone; the persisted run and its
    // pending approval are enough for the backend to resume safely.
    if (!payload && !(runId && callId)) {
      getLog()("warn", "chat", "resumeAgent: no cached payload or persisted approval, ignoring");
      useToastStore.getState().showWarning("没有可继续的 Agent 会话");
      return;
    }
    set((s) => ({
      ..._withSessionStreamingError(s, sid, null),
      pendingConfirm: null,
      isCompressing: false,
      streamingContent: "",
      streamingSteps: [],
      streamingSources: [],
      streamingTodos: [],
      streamingTodosSeen: { ...s.streamingTodosSeen, [sid]: false },
      isStreaming: true,
      streamingSessionId: sid,
      streamingStartTime: Date.now(),
    }));
    const controller = new AbortController();
    const resumeSteps: ActivityStep[] = [];
    const resumeTerminal: ResumeTerminalState = { outcome: "running", decision };
    set({ currentSseRequest: controller });
    apiSSE("agent-sandbox/resume", {
      ...(payload ? { payload } : {}),
      decision,
      ...(callId ? { call_id: callId } : {}),
      ...(runId ? { run_id: runId } : {}),
    }, {
      onEvent: (event, meta) => {
        const evt = event as ChatSSEEvent;
        if (!_trackChatRunEvent(sid, evt, meta, (trackedRunId, after) => {
          _replayRunEvents(sid, trackedRunId, after, "resume", { terminal: resumeTerminal, steps: resumeSteps });
        })) return;
        if (
          evt.type === "text"
          || evt.type === "thinking"
          || evt.type === "source"
          || evt.type === "tool_call"
          || evt.type === "tool_result"
        ) {
          // Resume updates an assistant row that already exists on the server.
          // Mark its local snapshot dirty before applying the event so refresh
          // reconciles it by the same stable ID instead of accepting stale data.
          _markResumeAssistantPending(sid);
        }
        _updateResumeTerminal(evt, resumeTerminal);
        _handleResumeEvent(evt, sid, resumeSteps);
      },
      onDone: () => _finalizeResume(
        sid,
        resumeSteps,
        resumeTerminal.outcome === "running" ? "success" : resumeTerminal.outcome,
        resumeTerminal.errorMessage,
      ),
      onError: (err) => {
        resumeTerminal.outcome = "error";
        resumeTerminal.errorMessage = err.message;
        getLog()("error", "chat", `Agent 恢复流错误: ${err.message}`);
        _finalizeResume(sid, resumeSteps, "error", err.message);
      },
    }, controller, {
      reconnect: { maxAttempts: 5 },
      onRunId: (id) => _beginActiveRun(sid, id),
    });
  },

  clearPendingConfirm: () => set({ pendingConfirm: null }),

  reattachRun: (sessionId) => _reattachRun(sessionId),

  clearRunRecoveryNotice: (sessionId) => set((s) => {
    if (!s.runRecoveryNoticeBySession[sessionId]) return {};
    const runRecoveryNoticeBySession = { ...s.runRecoveryNoticeBySession };
    delete runRecoveryNoticeBySession[sessionId];
    return { runRecoveryNoticeBySession };
  }),

  cancelActiveRun: async (sessionId) => {
    const state = get();
    const view = state.runViewBySession[sessionId];
    const record = state.activeRuns[sessionId] ?? getActiveRun(sessionId);
    const runId = view?.runId ?? record?.runId;
    if (!runId || view?.actionPending) return false;
    if (!view || !_isLiveRunStatus(view.status)) return false;
    _patchRunView(sessionId, { actionPending: true });
    try {
      await requestCancelRun(runId);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `取消运行失败: ${runId} - ${message}`);
      _patchRunView(sessionId, { actionPending: false });
      // The run may have ended between the click and the request; converge on
      // the backend state instead of guessing.
      const runState = await fetchRunState(runId);
      if (runState) {
        _applyRunStateToView(
          sessionId,
          runState,
          isTerminalRunStatus(runState.status) || runState.status === "interrupted" ? "terminal" : undefined,
        );
        if (runState.status === "interrupted") {
          _markRunInterrupted(sessionId, runId, runState.error_message ?? "");
        } else if (isTerminalRunStatus(runState.status)) {
          _clearRunTracking(sessionId);
        }
      }
      useToastStore.getState().showError(`取消运行失败：${message}`);
      return false;
    }

    // Accepted: converge locally at once; the user must not wait for the
    // backend stream to close before the UI reflects the cancel.
    if (get().streamingSessionId === sessionId) {
      get().stopStreaming();
    } else {
      const background = get().backgroundSseRequests.get(sessionId);
      if (background) {
        background.abort();
        set((s) => {
          const backgroundSseRequests = new Map(s.backgroundSseRequests);
          backgroundSseRequests.delete(sessionId);
          return { backgroundSseRequests };
        });
      }
    }
    if (get().pendingConfirm?.sessionId === sessionId) set({ pendingConfirm: null });
    _clearRunTracking(sessionId);
    _setRunViewTerminal(sessionId, "cancelled", null);
    useToastStore.getState().showInfo("已请求取消运行");
    return true;
  },

  resumeInterruptedRun: async (sessionId) => {
    const state = get();
    const view = state.runViewBySession[sessionId];
    const notice = state.runRecoveryNoticeBySession[sessionId];
    const record = state.activeRuns[sessionId] ?? getActiveRun(sessionId);
    const runId = view?.runId ?? notice?.runId ?? record?.runId;
    if (!runId || view?.actionPending) return false;
    _patchRunView(sessionId, { actionPending: true });
    try {
      await requestResumeRun(runId);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `恢复运行失败: ${runId} - ${message}`);
      _patchRunView(sessionId, { actionPending: false });
      useToastStore.getState().showError(`恢复运行失败：${message}`);
      return false;
    }

    set((s) => {
      const runRecoveryNoticeBySession = { ...s.runRecoveryNoticeBySession };
      delete runRecoveryNoticeBySession[sessionId];
      const previous = s.runViewBySession[sessionId];
      const nextView: RunView = previous
        ? {
            ...previous,
            status: "running",
            phase: "restoring",
            finishedAt: null,
            errorCode: null,
            errorMessage: null,
            resultUnknown: false,
            hardwareUnknown: false,
            actionPending: false,
          }
        : _emptyRunView(runId, "running", "restoring");
      return {
        runRecoveryNoticeBySession,
        runViewBySession: { ...s.runViewBySession, [sessionId]: nextView },
      };
    });
    useToastStore.getState().showInfo("已请求从检查点恢复运行");
    // Reattach picks the resumed run up; when the backend cannot offer one the
    // view falls back to the interrupted notice instead of spinning forever.
    void _reattachRun(sessionId);
    return true;
  },

  dismissRunView: (sessionId) => {
    _resetRunView(sessionId);
    set((s) => {
      if (!s.runRecoveryNoticeBySession[sessionId]) return {};
      const runRecoveryNoticeBySession = { ...s.runRecoveryNoticeBySession };
      delete runRecoveryNoticeBySession[sessionId];
      return { runRecoveryNoticeBySession };
    });
  },

  setMessages: (messages) => {
    const trimmed = trimMessages(messages);
    set((s) => syncToSession({ ...s, messages: trimmed }));
  },
  setSources: (sources) => set({ sources }),

  refreshMessagePage: (sessionId: string) => {
    void get().loadInitialMessagePage(sessionId);
  },

  loadInitialMessagePage: async (sessionId: string) => {
    if (!sessionId) return;
    set((state) => ({
      pagedHistoryBySession: {
        ...state.pagedHistoryBySession,
        [sessionId]: {
          ...(state.pagedHistoryBySession[sessionId] ?? {
            loading: false, pagesLoaded: 0, hasMore: null,
            error: null, persistenceError: null,
          }),
          loading: true,
          error: null,
        },
      },
    }));
    try {
      await get().fetchMessages(sessionId);
      set((state) => ({
        pagedHistoryBySession: {
          ...state.pagedHistoryBySession,
          [sessionId]: {
            ...(state.pagedHistoryBySession[sessionId] ?? {
              loading: false, pagesLoaded: 0, hasMore: null,
              error: null, persistenceError: null,
            }),
            loading: false,
            pagesLoaded: Math.max(1, state.pagedHistoryBySession[sessionId]?.pagesLoaded ?? 0),
            hasMore: false,
            error: null,
          },
        },
      }));
    } catch (error) {
      set((state) => ({
        pagedHistoryBySession: {
          ...state.pagedHistoryBySession,
          [sessionId]: {
            ...(state.pagedHistoryBySession[sessionId] ?? {
              loading: false, pagesLoaded: 0, hasMore: null,
              error: null, persistenceError: null,
            }),
            loading: false,
            error: error instanceof Error ? error.message : String(error),
          },
        },
      }));
    }
  },

  loadOlderMessagePage: async (sessionId: string) => {
    if (!sessionId) return;
    const status = get().pagedHistoryBySession[sessionId];
    if (!status || status.pagesLoaded === 0) return;
    set((state) => ({
      pagedHistoryBySession: {
        ...state.pagedHistoryBySession,
        [sessionId]: { ...status, hasMore: false },
      },
    }));
  },

  fetchMessages: async (sessionId: string) => {
    if (!sessionId) return;
    // 标记加载中：让 UI 在无缓存时显示骨架屏，而非 EmptyState
    set({ isLoadingMessages: true });
    // 1. 先用 localStorage 缓存立即填充（避免 UI 闪烁）
    const cached = get().sessionMessages[sessionId] || [];
    if (cached.length > 0 && get().activeSessionId === sessionId) {
      set({ messages: cached });
    }
    // Server-authoritative todo/plan snapshots start immediately, in parallel
    // with the message fetch. Waiting for messages meant a slow or failed
    // message request left the todo tab empty even though the server had rows.
    // A live stream will replay its own todo_update/plan_update events; the
    // revision guard in loadSessionTodos/loadSessionPlan discards a stale GET.
    const liveForSessionAtStart = get().isStreaming && get().streamingSessionId === sessionId
      && (get().currentSseRequest !== null || get().backgroundSseRequests.has(sessionId));
    if (!liveForSessionAtStart) {
      void get().loadSessionTodos(sessionId);
      void get().loadSessionPlan(sessionId);
    }
    // 2. 从后端拉权威数据
    try {
      const data = await apiGet<{ messages: BackendMessage[] }>(`sessions/${sessionId}/messages`);
      let backendMessages = data?.messages || [];
      _rememberConfirmedBackendMessages(sessionId, backendMessages);

      // Do not overwrite a live stream's own messages. A stale isStreaming flag
      // without a controller (for example after a crash) must not block the
      // backend snapshot from replacing the local cache.
      const state = get();
      const liveStreamForSession = state.isStreaming
        && state.streamingSessionId === sessionId
        && (state.currentSseRequest !== null || state.backgroundSseRequests.has(sessionId));
      if (liveStreamForSession) {
        getLog()("info", "chat", `跳过 fetchMessages: 会话 ${sessionId} 正在流式中`);
        return;
      }

      // The local shard is the recovery copy. Retry messages marked before an
      // interrupted write, plus any local IDs missing from the backend. Stable
      // IDs make this safe even if the previous request committed but lost its reply.
      const localMessages = get().sessionMessages[sessionId] || cached;
      const backendIds = new Set(backendMessages.map((message) => message.id));
      const backendById = new Map(backendMessages.map((message) => [message.id, message]));
      const pendingIds = new Set(_loadPendingSaveIds(sessionId));
      for (const id of _localPendingSaveIds(localMessages)) pendingIds.add(id);
      const legacyMatchedLocalIds = new Set<string>();
      const legacyUpdateBackendIdByLocalId = new Map<string, string>();
      const matchedBackendIds = new Set<string>();
      let confirmedPendingWrite = false;
      const rolesRemainAligned = localMessages.length === backendMessages.length
        && localMessages.every((message, index) => message.role === backendMessages[index]?.role);
      localMessages.forEach((localMessage, index) => {
        if (backendIds.has(localMessage.id)) {
          matchedBackendIds.add(localMessage.id);
          return;
        }
        if (rolesRemainAligned) {
          const positional = backendMessages[index];
          if (!positional || matchedBackendIds.has(positional.id)) return;
          matchedBackendIds.add(positional.id);
          if (_matchesBackendMessage(localMessage, positional)) {
            legacyMatchedLocalIds.add(localMessage.id);
          } else {
            // For pre-stable-ID records, keep the same ordered message slot and
            // update its server ID instead of inserting a second conversation row.
            legacyUpdateBackendIdByLocalId.set(localMessage.id, positional.id);
          }
          return;
        }
        const atSamePosition = backendMessages[index];
        const exactMatch = atSamePosition
          && !matchedBackendIds.has(atSamePosition.id)
          && _matchesBackendMessage(localMessage, atSamePosition)
          ? atSamePosition
          : backendMessages.find((candidate) =>
              !matchedBackendIds.has(candidate.id) && _matchesBackendMessage(localMessage, candidate)
            );
        if (exactMatch) {
          // Older clients used separate browser/server IDs. Exact content and
          // source matches identify those records without inserting them again.
          legacyMatchedLocalIds.add(localMessage.id);
          matchedBackendIds.add(exactMatch.id);
        }
      });
      const recoveryMessages = localMessages.filter((message) => {
        const backendMessage = backendById.get(message.id);
        const pendingWriteIsConfirmed = pendingIds.has(message.id)
          && ((backendMessage !== undefined && _matchesBackendMessage(message, backendMessage))
            || legacyMatchedLocalIds.has(message.id));
        if (pendingWriteIsConfirmed) {
          _rememberMessageSaveConfirmed(sessionId, message);
          const localSnapshotCleared = _clearLocalPendingSaveIds(sessionId, [message.id]);
          const remainingIds = localSnapshotCleared
            ? _clearPendingSaveIds(sessionId, [message.id])
            : _loadPendingSaveIds(sessionId);
          if (localSnapshotCleared && !remainingIds.includes(message.id)) {
            confirmedPendingWrite = true;
            pendingIds.delete(message.id);
            return false;
          }
          return true;
        }
        return pendingIds.has(message.id)
          || (!backendIds.has(message.id) && !legacyMatchedLocalIds.has(message.id));
      });
      if (recoveryMessages.length > 0) {
        const recoveryIdMap = new Map(
          recoveryMessages.flatMap((message) => {
            const backendId = legacyUpdateBackendIdByLocalId.get(message.id);
            return backendId ? [[message.id, backendId] as const] : [];
          })
        );
        _remapPendingLocalMessageIds(sessionId, recoveryIdMap);
        const recoveryWrites = recoveryMessages.map((message) => {
          const backendId = recoveryIdMap.get(message.id);
          return backendId ? _withMessageId(message, backendId) : message;
        });
        const recoveryIds = recoveryWrites.map((message) => message.id);
        _markLocalPendingSaveIds(sessionId, recoveryIds);
        _rememberPendingSaveIds(sessionId, recoveryIds);
        updatePendingSaveStatus(sessionId, "saving");
        try {
          await _postMessagesWithSessionRecovery(sessionId, recoveryWrites);
          const recoverySnapshotIsCurrent = _isMessageSaveSnapshotCurrent(sessionId, recoveryWrites);
          const remainingPendingIds = _clearSavedMessageIdsIfCurrent(sessionId, recoveryWrites);
          updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId));
          const refreshed = await apiGet<{ messages: BackendMessage[] }>(`sessions/${sessionId}/messages`);
          backendMessages = refreshed?.messages || [];
          _rememberConfirmedBackendMessages(sessionId, backendMessages);
          if (
            recoverySnapshotIsCurrent
            && remainingPendingIds.length === 0
            && _loadPendingSaveIds(sessionId).length === 0
            && _localPendingSaveIds(get().sessionMessages[sessionId] || []).length === 0
          ) {
            _updateSessionMetaAfterPersistedMessages(
              sessionId,
              get().sessionMessages[sessionId] || localMessages,
            );
          }
        } catch (syncErr) {
          // Keep a durable marker so refresh can retry only the save request.
          _rememberPendingSaveIds(sessionId, recoveryIds);
          updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId) ?? "pending");
          const syncMessage = syncErr instanceof Error ? syncErr.message : String(syncErr);
          getLog()("warn", "chat", `恢复未保存消息失败: ${sessionId} - ${syncMessage}`);
        }
      } else if (_loadPendingSaveIds(sessionId).length > 0) {
        updatePendingSaveStatus(sessionId, "pending");
      }
      if (
        confirmedPendingWrite
        && _loadPendingSaveIds(sessionId).length === 0
        && _localPendingSaveIds(get().sessionMessages[sessionId] || []).length === 0
      ) {
        _updateSessionMetaAfterPersistedMessages(sessionId, localMessages);
      }

      let msgs: Message[] = backendMessages.map(mapBackendMessage);
      const remainingPending = new Set(_loadPendingSaveIds(sessionId));
      for (const id of _localPendingSaveIds(get().sessionMessages[sessionId] || cached)) {
        remainingPending.add(id);
      }
      if (remainingPending.size > 0) {
        // Keep the local version of unsaved messages, including one that shares
        // an ID with an older partially saved Agent answer. Legacy messages
        // already matched by role/content are represented by their backend IDs.
        const merged = new Map(msgs.map((message) => [message.id, message]));
        for (const message of get().sessionMessages[sessionId] || cached) {
          if (
            remainingPending.has(message.id)
            || (!merged.has(message.id) && !legacyMatchedLocalIds.has(message.id))
          ) {
            merged.set(message.id, message);
          }
        }
        msgs = [...merged.values()];
      }

      if (remainingPending.size === 0) updatePendingSaveStatus(sessionId);
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
      // Recovery R1: after a refresh, reattach to a run that was still active.
      void get().reattachRun(sessionId);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      if (errMsg.includes("401")) {
        set({ needsApiKey: true });
      } else if (errMsg.includes("aborted") || errMsg.includes("signal is aborted")) {
        // 请求被中断（快速切换会话等），静默使用缓存，不弹 toast
        getLog()("debug", "chat", `加载会话消息被中断: ${sessionId}，使用缓存`);
      } else {
        getLog()("warn", "chat", `加载会话消息失败: ${sessionId}，使用缓存`);
        if (_loadPendingSaveIds(sessionId).length > 0) {
          updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId) ?? "pending");
        }
        useToastStore.getState().showError(`加载会话消息失败: ${errMsg}`);
      }
    } finally {
      set({ isLoadingMessages: false });
    }
  },

  persistLastTurn: async (sessionId: string) => {
    if (!sessionId) return false;
    const msgs = get().sessionMessages[sessionId] || [];
    if (msgs.length < 2) return false;
    const lastTwo = msgs.slice(-2);
    const messagesToSave = lastTwo.filter((message) => !_isMessageSaveConfirmed(sessionId, message));
    if (messagesToSave.length === 0) {
      const snapshotIsCurrent = _isMessageSaveSnapshotCurrent(sessionId, lastTwo);
      const lastTwoIds = lastTwo.map((message) => message.id);
      const hadPendingMarkers = lastTwoIds.some((id) =>
        _loadPendingSaveIds(sessionId).includes(id)
        || _localPendingSaveIds(get().sessionMessages[sessionId] || []).includes(id)
      );
      let localSnapshotPersisted = !hadPendingMarkers;
      if (snapshotIsCurrent) {
        if (hadPendingMarkers) {
          localSnapshotPersisted = _clearLocalPendingSaveIds(sessionId, lastTwoIds);
          if (localSnapshotPersisted) _clearPendingSaveIds(sessionId, lastTwoIds);
        } else {
          localSnapshotPersisted = _saveCurrentLocalSnapshot(sessionId);
        }
      }
      const remainingPendingIds = new Set(_loadPendingSaveIds(sessionId));
      const remainingLocalPendingIds = new Set(_localPendingSaveIds(get().sessionMessages[sessionId] || []));
      const lastTwoRemainPending = lastTwoIds.some((id) =>
        remainingPendingIds.has(id) || remainingLocalPendingIds.has(id)
      );
      const saved = snapshotIsCurrent && localSnapshotPersisted && !lastTwoRemainPending;
      if (saved) _reconcilePendingSaveMarkers(sessionId, lastTwoIds);
      const pendingAfterSave = new Set(_loadPendingSaveIds(sessionId));
      const localPendingAfterSave = new Set(_localPendingSaveIds(get().sessionMessages[sessionId] || []));
      updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId));
      if (saved && pendingAfterSave.size === 0 && localPendingAfterSave.size === 0) {
        _updateSessionMetaAfterPersistedMessages(
          sessionId,
          get().sessionMessages[sessionId] || lastTwo,
        );
      }
      return saved;
    }

    const messageIds = messagesToSave.map((message) => message.id);
    _markLocalPendingSaveIds(sessionId, messageIds);
    _rememberPendingSaveIds(sessionId, messageIds);
    updatePendingSaveStatus(sessionId, "saving");
    try {
      await _postMessagesWithSessionRecovery(sessionId, messagesToSave);
      const snapshotIsCurrent = _isMessageSaveSnapshotCurrent(sessionId, lastTwo);
      let localSnapshotPersisted = false;
      if (snapshotIsCurrent) {
        localSnapshotPersisted = _clearLocalPendingSaveIds(sessionId, lastTwo.map((message) => message.id));
        if (localSnapshotPersisted) _clearPendingSaveIds(sessionId, lastTwo.map((message) => message.id));
      }
      const lastTwoAreConfirmed = lastTwo.every((message) => _isMessageSaveConfirmed(sessionId, message));
      const remainingPendingIds = new Set(_loadPendingSaveIds(sessionId));
      const remainingLocalPendingIds = new Set(_localPendingSaveIds(get().sessionMessages[sessionId] || []));
      const lastTwoRemainPending = lastTwo.some((message) =>
        remainingPendingIds.has(message.id) || remainingLocalPendingIds.has(message.id)
      );
      const saved = snapshotIsCurrent && localSnapshotPersisted && lastTwoAreConfirmed && !lastTwoRemainPending;
      if (saved) _reconcilePendingSaveMarkers(sessionId, lastTwo.map((message) => message.id));
      const pendingAfterSave = new Set(_loadPendingSaveIds(sessionId));
      const localPendingAfterSave = new Set(_localPendingSaveIds(get().sessionMessages[sessionId] || []));
      updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId));
      if (saved && pendingAfterSave.size === 0 && localPendingAfterSave.size === 0) {
        _updateSessionMetaAfterPersistedMessages(
          sessionId,
          get().sessionMessages[sessionId] || lastTwo,
        );
        getLog()("ok", "chat", `已持久化会话 ${sessionId} 最后两条消息到后端`);
      } else {
        getLog()("debug", "chat", `会话 ${sessionId} 的旧保存快照已完成，保留较新消息的待保存标记`);
      }
      return saved;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId) ?? "pending");
      getLog()("warn", "chat", `持久化消息失败: ${sessionId}`);
      useToastStore.getState().showError(`保存会话失败: ${errMsg}`);
      return false;
    }
  },

  retryPendingSave: async (sessionId: string) => {
    if (!sessionId) return;
    const localMessages = get().sessionMessages[sessionId] || [];
    const pendingIds = new Set(_loadPendingSaveIds(sessionId));
    for (const id of _localPendingSaveIds(localMessages)) pendingIds.add(id);
    const retryCandidates = pendingIds.size > 0
      ? localMessages.filter((message) => pendingIds.has(message.id))
      : localMessages.slice(-2);
    const alreadyConfirmed = retryCandidates.filter((message) => _isMessageSaveConfirmed(sessionId, message));
    if (alreadyConfirmed.length > 0) {
      const confirmedIds = alreadyConfirmed.map((message) => message.id);
      if (
        _isMessageSaveSnapshotCurrent(sessionId, alreadyConfirmed)
        && _clearLocalPendingSaveIds(sessionId, confirmedIds)
      ) {
        _clearPendingSaveIds(sessionId, confirmedIds);
      }
    }
    const retryMessages = retryCandidates.filter((message) => !_isMessageSaveConfirmed(sessionId, message));
    if (retryMessages.length === 0) {
      const snapshotIsCurrent = _isMessageSaveSnapshotCurrent(sessionId, retryCandidates);
      const localSnapshotPersisted = snapshotIsCurrent && _saveCurrentLocalSnapshot(sessionId);
      if (snapshotIsCurrent && localSnapshotPersisted) {
        _reconcilePendingSaveMarkers(
          sessionId,
          retryCandidates.map((message) => message.id),
        );
      }
      const pendingIdsRemain = _loadPendingSaveIds(sessionId).length > 0
        || _localPendingSaveIds(get().sessionMessages[sessionId] || []).length > 0;
      const retrySaveCompleted = snapshotIsCurrent && localSnapshotPersisted && !pendingIdsRemain;
      updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId));
      if (retrySaveCompleted) {
        _updateSessionMetaAfterPersistedMessages(sessionId, localMessages);
        post("messages_changed", sessionId);
      }
      return;
    }

    const retryIds = retryMessages.map((message) => message.id);
    _markLocalPendingSaveIds(sessionId, retryIds);
    _rememberPendingSaveIds(sessionId, retryIds);
    updatePendingSaveStatus(sessionId, "saving");
    try {
      await _postMessagesWithSessionRecovery(sessionId, retryMessages);
      const snapshotIsCurrent = _isMessageSaveSnapshotCurrent(sessionId, retryMessages);
      const localSnapshotPersisted = snapshotIsCurrent
        && _clearLocalPendingSaveIds(sessionId, retryIds);
      const remainingPendingIds = localSnapshotPersisted
        ? _clearPendingSaveIds(sessionId, retryIds)
        : _loadPendingSaveIds(sessionId);
      const remainingLocalPendingIds = _localPendingSaveIds(get().sessionMessages[sessionId] || []);
      const retryMessagesAreConfirmed = retryMessages.every((message) => _isMessageSaveConfirmed(sessionId, message));
      const retryMessagesRemainPending = retryIds.some((id) =>
        remainingPendingIds.includes(id) || remainingLocalPendingIds.includes(id)
      );
      const retrySaveCompleted = snapshotIsCurrent
        && localSnapshotPersisted
        && retryMessagesAreConfirmed
        && !retryMessagesRemainPending;
      if (retrySaveCompleted) _reconcilePendingSaveMarkers(sessionId, retryIds);
      const pendingAfterRetry = _loadPendingSaveIds(sessionId);
      const localPendingAfterRetry = _localPendingSaveIds(get().sessionMessages[sessionId] || []);
      updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId));
      if (
        retrySaveCompleted
        && pendingAfterRetry.length === 0
        && localPendingAfterRetry.length === 0
      ) {
        _updateSessionMetaAfterPersistedMessages(
          sessionId,
          get().sessionMessages[sessionId] || localMessages,
        );
      }
      if (retrySaveCompleted) post("messages_changed", sessionId);
    } catch (err) {
      updatePendingSaveStatus(sessionId, _pendingSaveStatus(sessionId) ?? "pending");
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("warn", "chat", `重试保存会话失败: ${sessionId} - ${errMsg}`);
      useToastStore.getState().showError(`保存会话失败: ${errMsg}`);
    }
  },

  setActiveSession: (id) => {
    const { activeSessionId, messages, sessionMessages, isStreaming,
            streamingSessionId, currentSseRequest, streamingContent,
            streamingSteps, backgroundSseRequests,
            streamingError, streamingErrorBySession, planModeBySession } = get();
    if (id === activeSessionId) return;
    const nextKbScope = _loadKbScope(id);
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

    // A terminal error belongs to the session that produced it. Keep the
    // outgoing session's error with it and show only the target's own error.
    const nextErrors = { ...streamingErrorBySession };
    if (streamingError && activeSessionId) nextErrors[activeSessionId] = streamingError;

    // Plan mode belongs to the target session: its live value, else the
    // persisted Agent marker loaded with the session list.
    const targetSession = useSessionStore.getState().sessions.find((s) => s.id === id);
    const targetPlanMode = planModeBySession[id]
      ?? (targetSession?.planModeRequested === true);

    set({
      activeSessionId: id,
      planMode: targetPlanMode,
      selectedKbIds: nextKbScope.ids,
      kbScopeIssue: nextKbScope.issue,
      messages: loadedMessages,
      sessionMessages: updatedSessionMessages,
      streamingError: nextErrors[id] ?? null,
      streamingErrorBySession: nextErrors,
      isStreaming: hasBackgroundStream,
      streamingContent: restoredContent,
      streamingSteps: restoredSteps,
      streamingSources: [],
      streamingTodos: [],
      // Until the target session emits its own todo_update, prefer its persisted
      // snapshot over an empty streaming buffer (background streams included).
      streamingTodosSeen: { ...get().streamingTodosSeen, [id]: false },
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

  sendMessage: async (content, attachments, quoted, skills, options) => {
    const turnStartedAt = Date.now();
    const initialState = get();
    let { messages, activeSessionId } = initialState;
    const { isStreaming, selectedKbIds, kbScopeIssue } = initialState;
    const initialSessionSend = !activeSessionId;
    const initialComposerSnapshot = initialSessionSend
      ? captureInitialComposer(activeSessionId, content, attachments, quoted)
      : null;
    if (initialSessionSend && initialSessionSendInProgress) {
      if (initialComposerSnapshot) pendingInitialComposers.push(initialComposerSnapshot);
      return;
    }
    if (initialSessionSend) pendingInitialComposers = [];
    // 允许只有附件没有文字（如只发图片）
    if (isStreaming || _sendValidationInProgress) {
      if (initialSessionSend && _sendValidationInProgress) {
        if (initialComposerSnapshot) pendingInitialComposers.push(initialComposerSnapshot);
      }
      useToastStore.getState().showWarning(
        _sendValidationInProgress ? "正在验证知识库范围，请稍候" : "正在生成中，请先停止当前回答"
      );
      return;
    }
    if (!content.trim() && (!attachments || attachments.length === 0)) return;
    if (kbScopeIssue && kbScopeIssue !== "verification_failed") {
      useToastStore.getState().showWarning(_kbScopeIssueMessage(kbScopeIssue));
      if (initialComposerSnapshot) queueMicrotask(() => restoreInitialComposers([initialComposerSnapshot]));
      return;
    }
    if (selectedKbIds.length > 0) {
      _sendValidationInProgress = true;
      let scopeIsValid = false;
      try {
        scopeIsValid = await verifyKbScopeForSession(activeSessionId, selectedKbIds);
      } finally {
        _sendValidationInProgress = false;
      }
      if (!scopeIsValid) {
        useToastStore.getState().showWarning(_kbScopeIssueMessage(get().kbScopeIssue));
        if (initialComposerSnapshot) {
          const snapshotsToRestore = [initialComposerSnapshot, ...pendingInitialComposers];
          pendingInitialComposers = [];
          restoreInitialComposers(snapshotsToRestore);
        }
        return;
      }
    }
    // Skills are discovered and loaded automatically; the input bar no longer
    // exposes a manual per-chat selection.
    const skillsMode = "auto";
    if (initialSessionSend) {
      initialSessionSendInProgress = true;
      try {
        const createdSessionId = await useSessionStore.getState().newSession({ requirePersisted: true });
        if (!createdSessionId) throw new Error("后端未创建会话，请重试");
        activeSessionId = createdSessionId;
        messages = get().sessionMessages[createdSessionId] || [];
        movePendingComposerToSession(initialComposerSnapshot!, createdSessionId);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : String(err);
        const snapshotsToRestore = [initialComposerSnapshot!, ...pendingInitialComposers];
        pendingInitialComposers = [];
        restoreInitialComposers(snapshotsToRestore);
        getLog()("warn", "chat", `首发前创建会话失败，未发送聊天请求: ${errorMessage}`);
        useToastStore.getState().showError("新建会话失败，输入已保留，请重试");
        return;
      } finally {
        initialSessionSendInProgress = false;
      }
    }
    // SubTask 8.4: abort 后台 SSE（避免两个流并发导致消息损坏）
    const currentBackgroundSseRequests = get().backgroundSseRequests;
    if (currentBackgroundSseRequests.has(activeSessionId)) {
      const bgController = currentBackgroundSseRequests.get(activeSessionId);
      bgController?.abort();
      const newBg = new Map(currentBackgroundSseRequests);
      newBg.delete(activeSessionId);
      set({ backgroundSseRequests: newBg });
      getLog()("warn", "chat", `会话 ${activeSessionId} 有后台 SSE，已中止以避免并发`);
    }
    const imgCount = attachments?.filter((a) => a.type.startsWith("image/")).length ?? 0;
    console.info('[ChatStore] sendMessage session=%s prompt_len=%d', activeSessionId, content.length);
    getLog()("info", "chat", `发送消息: ${content.slice(0, 50) || "(仅附件)"}... attachments=${attachments?.length ?? 0} images=${imgCount}`);

    try {
    const hasPagedHistory = Boolean(get().pagedHistoryBySession[activeSessionId]);
    let serverHistory = false;
    if (hasPagedHistory) {
      const historyMode = await getSessionHistoryMode(activeSessionId).catch(() => null);
      if (historyMode === null) {
        get().setDraft(activeSessionId, content);
        useToastStore.getState().showWarning("无法确认会话历史版本；本次未发送。请重新加载历史后重试。");
        return;
      }
      serverHistory = historyMode.mode !== "legacy" || historyMode.server_history === true;
      const historyBlockReason = _knownHistorySendBlockReason(
        get(), activeSessionId, quoted, serverHistory,
      );
      if (historyBlockReason) {
        useToastStore.getState().showWarning(historyBlockReason);
        return;
      }
    }

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
    saveSessionMessages(activeSessionId, nextMessages);

    const currentSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
    const model = currentSession?.model || useSettingsStore.getState().chatModel;
    const settingsForReasoning = useSettingsStore.getState();
    const reasoningProviders = Array.isArray(settingsForReasoning.providers)
      ? settingsForReasoning.providers
      : [];
    const reasoningProvider = reasoningProviders.find(
      (provider) => provider.id === settingsForReasoning.chatProviderId,
    );
    const requestedReasoningMode = currentSession?.reasoningMode ?? "auto";
    const reasoningMode: ReasoningMode = reasoningModesFor(
      reasoningProvider?.modelCapabilities?.[model],
    ).includes(requestedReasoningMode)
      ? requestedReasoningMode
      : "auto";

    set((state) => {
      const sessionMessages = { ...state.sessionMessages, [activeSessionId]: nextMessages };
      if (state.activeSessionId !== activeSessionId) return { sessionMessages };
      return {
        ..._withSessionStreamingError(state, activeSessionId, null),
        // A new turn must not be able to decide an approval that belonged to
        // the previous run.
        ...(state.pendingConfirm?.sessionId === activeSessionId ? { pendingConfirm: null } : {}),
        messages: nextMessages,
        isStreaming: true,
        streamingContent: "",
        streamingSteps: [{
          id: `h-${Date.now()}`,
          type: "thinking",
          content: THINKING_PLACEHOLDER_TEXT,
          source: "reasoning",
          status: "running",
          placeholder: true,
          reasoningMode,
        }],
        streamingSources: [],
        streamingTodos: [],
        // A new turn starts from the persisted list until its own todo_update arrives.
        streamingTodosSeen: { ...state.streamingTodosSeen, [activeSessionId]: false },
        streamingSessionId: activeSessionId,
        streamingStartTime: turnStartedAt,
        _pendingUsage: null,
        sessionMessages,
      };
    });
    getLog()("info", "chat", `sendMessage set messages: activeSessionId=${activeSessionId} count=${nextMessages.length}`);

    // 构建请求体：历史消息 + 设置（扁平结构，对齐后端 ChatRequest）
    // 模型优先从当前会话读取（各对话独立），回退到全局设置
    const { topK, temperature, systemPrompt, longTermMemory, maxTokens, relevanceThreshold, permissionMode, webSearchConfig } = useSettingsStore.getState();
    // Plan mode is request-scoped: it maps to permission_mode "plan" for this
    // turn only and never touches the persisted settings value.
    const planMode = get().planMode;
    getLog()("info", "chat", `model=${model} topK=${topK} maxTokens=${maxTokens} threshold=${relevanceThreshold} sessionId=${activeSessionId} systemPrompt="${systemPrompt?.slice(0, 50)}..."`);
    const history = messages.map((m) => ({ role: m.role, content: m.content, message_id: m.id }));
    const newMsg = { role: "user" as const, content: apiContent };
    // Owner-bound sessions can send only the active turn; the server loads the
    // persisted history and compacts it without a client-side message cap.
    // 若有引用回复，在被引用消息后插入一条 system 上下文，让 LLM 知道用户引用了之前的对话
    const quotedContext = quoted
      ? [{ role: "system" as const, content: `用户引用了之前的对话内容：\n> ${typeof quoted.content === "string" ? quoted.content : ""}` }]
      : [];
    const requestBody: ChatRequest = {
      messages: serverHistory
        ? [...quotedContext, newMsg]
        : [...history, ...quotedContext, newMsg],
      ...(serverHistory ? { history_mode: "server" as const } : {}),
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
      skills_mode: skillsMode,
      permission_mode: planMode ? "plan" : permissionMode,
      reasoning_mode: reasoningMode,
      ...(options?.approvedPlanId ? { approved_plan_id: options.approvedPlanId } : {}),
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

    const startTime = turnStartedAt;
    // ★ 关键：捕获发起请求时的 sessionId，回调中始终使用此 ID
    const requestSessionId = activeSessionId;
    // v2-T4: cache payload for the resume API (HITL confirm flow)
    set({ _lastAgentPayload: requestBody });
    // A new send supersedes any leftover recovery record for this session.
    _clearRunTracking(requestSessionId);
    _resetRunView(requestSessionId);
    const terminal: ChatTerminalState = { outcome: "running", errorMessage: "回答未能完成", finalized: false };

    let writerBusyRetries = 0;
    let retryAfterWriterBusy = false;

    const startAgentStream = (): void => {
      const controller = new AbortController();
      if (get().activeSessionId === requestSessionId) {
        set({ currentSseRequest: controller });
      } else {
        set((state) => {
          const backgroundSseRequests = new Map(state.backgroundSseRequests);
          backgroundSseRequests.set(requestSessionId, controller);
          return { backgroundSseRequests };
        });
      }

      apiSSE("chat", requestBody, {
        onEvent: (event, meta) => {
          const evt = event as ChatSSEEvent;
          if (!_trackChatRunEvent(requestSessionId, evt, meta, (runId, after) => {
            _replayRunEvents(requestSessionId, runId, after, "chat");
          })) return;
          if (
            evt.type === "error"
            && evt.code === "AGENT_WRITER_BUSY"
            && evt.retryable === true
            && writerBusyRetries < _WRITER_BUSY_MAX_AUTO_RETRIES
          ) {
            writerBusyRetries += 1;
            retryAfterWriterBusy = true;
            return;
          }
          if (evt.type === "tool_confirm_required") {
            const record = get().activeRuns[requestSessionId] ?? getActiveRun(requestSessionId);
            if (record) _markRunStatus(requestSessionId, record.runId, "awaiting_confirmation");
          }
          _applyChatEvent(evt, requestSessionId, terminal);
        },
        onDone: () => {
          if (retryAfterWriterBusy) {
            retryAfterWriterBusy = false;
            const stillWanted = get().streamingSessionId === requestSessionId
              || get().backgroundSseRequests.has(requestSessionId);
            if (!stillWanted) return;
            const retryDelay = _WRITER_BUSY_RETRY_DELAYS_MS[writerBusyRetries - 1]
              ?? _WRITER_BUSY_RETRY_DELAYS_MS[_WRITER_BUSY_RETRY_DELAYS_MS.length - 1];
            getLog()(
              "warn", "chat",
              `上一轮仍在结束中，自动重试 ${writerBusyRetries}/${_WRITER_BUSY_MAX_AUTO_RETRIES}`,
            );
            window.setTimeout(() => {
              const stillStreaming = get().streamingSessionId === requestSessionId
                || get().backgroundSseRequests.has(requestSessionId);
              if (!stillStreaming) return;
              terminal.outcome = "running";
              terminal.errorMessage = "回答未能完成";
              startAgentStream();
            }, retryDelay);
            return;
          }
          _finalizeChatStream(requestSessionId, terminal, startTime);
        },
        onError: (err) => _failChatStream(requestSessionId, terminal, err),
      }, controller, {
        reconnect: { maxAttempts: 5 },
        onRunId: (runId) => _beginActiveRun(requestSessionId, runId, "running", startTime),
      });
    };

    startAgentStream();
    } catch (err) {
      // 同步异常（如构建请求体失败）也要让用户看到 ErrorBlock，不能只 console.warn
      const errMsg = err instanceof Error ? err.message : String(err);
      getLog()("error", "chat", `sendMessage 异常: ${errMsg}`);
      set((s) => ({
        isStreaming: false,
        ..._withSessionStreamingError(s, activeSessionId, {
          code: "INTERNAL_ERROR",
          message: errMsg,
          detail: "",
        }),
      }));
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

    // A user-initiated stop abandons the run locally: do not reattach it on
    // refresh. Error stops keep the record because the run may still be alive.
    if (!errorMessage && sidToPersist) {
      _clearRunTracking(sidToPersist);
      _setRunViewTerminal(sidToPersist, "cancelled", null);
    }

    // 流式停止（用户主动或错误）：将最后一轮 user+assistant 消息持久化到后端
    if (sidToPersist) {
      void get().persistLastTurn(sidToPersist).then((saved) => {
        // 多 tab 同步：通知其他 tab 此会话消息已变化
        if (saved) post('messages_changed', sidToPersist);
      });
    }
  },

  retryMessage: (msgId) => {
    const { messages, isStreaming, activeSessionId } = get();
    if (isStreaming) {
      useToastStore.getState().showWarning("正在生成中，请稍候");
      return; // 流式输出中不允许重试
    }
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) {
      useToastStore.getState().showWarning("消息不存在，无法重试");
      return;
    }
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
    if (isStreaming) {
      useToastStore.getState().showWarning("正在生成中，请稍候");
      return; // 流式输出中不允许编辑
    }
    if (!newContent.trim()) {
      useToastStore.getState().showWarning("内容不能为空");
      return;
    }
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) {
      useToastStore.getState().showWarning("消息不存在");
      return;
    }
    getLog()("info", "chat", `编辑重发: ${msgId}`);

    void truncateAndResend(activeSessionId, idx, newContent);
  },

  branchThread: (msgId) => {
    const { activeSessionId, messages } = get();
    if (!activeSessionId) {
      useToastStore.getState().showWarning("没有活动会话");
      return;
    }
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) {
      useToastStore.getState().showWarning("消息不存在，无法分叉");
      return;
    }
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
      useToastStore.getState().showError("分叉对话失败");
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

// 刷新/关闭页面前主动 abort 所有 SSE 连接，避免浏览器 keep-alive 连接池
// 被旧 SSE 流卡住导致刷新后新请求全部超时（"signal is aborted without reason"）
if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", () => {
    const { currentSseRequest, backgroundSseRequests } = useChatStore.getState();
    currentSseRequest?.abort();
    backgroundSseRequests.forEach((c) => c.abort());
  });
}

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
const _pendingShards: Map<string, Message[]> = new Map();
const _localShardSaveFailureNotified = new Set<string>();

function flushPendingShards() {
  for (const [sid, msgs] of _pendingShards) {
    if (saveSessionMessages(sid, msgs)) {
      _localShardSaveFailureNotified.delete(sid);
      continue;
    }
    useChatStore.setState((state) => ({
      pendingSaveBySession: { ...state.pendingSaveBySession, [sid]: "pending" },
    }));
    if (!_localShardSaveFailureNotified.has(sid)) {
      _localShardSaveFailureNotified.add(sid);
      useToastStore.getState().showError("本地消息快照保存失败；当前未保存消息可能无法在刷新后恢复，请在当前页面重试保存。");
    }
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
