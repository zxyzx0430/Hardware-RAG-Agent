// API 请求/响应类型 — 保持与现有组件兼容的导出集合

import type { SourceRef, ActivityBlock, ContentPart, TodoItem } from './session';
import type { ReasoningMode } from './reasoning';
import type { ProviderInfo } from './settings';
import type { KBItem } from './kb';
import type { SerialDevice } from './serial';
import type { Plan } from './plan';

export type { SourceRef, ActivityBlock, ContentPart, ProviderInfo, KBItem as KBDoc, SerialDevice };
export type { Plan, PlanStep, PlanStatus, PlanMeta } from './plan';

export interface ModelInfo {
  id: string;
  label: string;
  provider: string;
}

export interface Attachment {
  id: string;
  name: string;
  type: string;
  content: string;
}

export interface ChatRequest {
  messages: {
    role: 'user' | 'assistant' | 'system';
    content: string | ContentPart[];
    /** Optional stable UI/DB message ID; advisory only until owner-side verification. */
    message_id?: string;
  }[];
  /**
   * Who supplies conversation history for this request.
   * `client` (default): `messages` carries the full known history.
   * `server`: `messages` carries only the current user turn (plus adjacent quote
   * context); the backend loads and compacts the saved history for this session.
   */
  history_mode?: 'client' | 'server';
  model?: string;
  temperature?: number;
  max_tokens?: number;
  top_k?: number;
  relevance_threshold?: number;
  system_prompt?: string;
  long_term_memory?: string;
  provider?: string;
  api_key?: string;
  base_url?: string;
  attachments?: Attachment[];
  kb_ids?: string[];
  session_id?: string;
  use_agent?: boolean;
  skills_mode?: 'off' | 'auto' | 'manual';
  skill_ids?: string[];
  permission_mode?: string;
  reasoning_mode?: ReasoningMode;
  /** Execute an approved plan for this session; validated server-side. */
  approved_plan_id?: string;
  tool_keys?: Record<string, string>;
}

export interface TokenUsageSSE {
  /** 后端已兜底，始终返回 number */
  prompt_tokens: number;
  /** 后端已兜底，始终返回 number */
  completion_tokens: number;
  /** 后端已兜底，始终返回 number */
  total_tokens: number;
}

export interface ThinkingSSEEvent {
  type: 'thinking';
  content: string;
  source?: 'rag' | 'llm' | 'reasoning' | 'agent';
}

export interface TextSSEEvent {
  type: 'text';
  content: string;
}

export interface ToolCallSSEEvent {
  type: 'tool_call';
  tool: string;
  args: Record<string, unknown> | string;
  call_id: string;
  step_index?: number;
  tool_confirm_required?: boolean;
  risk_level?: 'low' | 'medium' | 'high';
  decision_source?: string;
  /** Backend wall-clock time (unix seconds) when the tool_call was emitted. */
  timestamp?: number;
}

export interface ToolErrorDetail {
  error_type: string;
  error_message: string;
  suggestion?: string;
  retryable?: boolean;
}

export interface SkillToolResultData {
  skill_id?: string;
  content_hash?: string;
  stage?: "instructions" | "resource";
  path?: string;
  success?: boolean;
}

export interface ToolResultSSEEvent {
  type: 'tool_result';
  call_id: string;
  tool: string;
  result: {
    success?: boolean;
    output?: string;
    error?: string | ToolErrorDetail | null;
    // Backend ToolResultEnvelope wraps render payload inside data; some legacy
    // tools still put target_pane/render_data at the top level. Unknown panes
    // are ignored by the workbench bridge.
    target_pane?: string;
    render_data?: unknown;
    data?: SkillToolResultData & {
      target_pane?: string;
      render_data?: unknown;
    };
  } | string;
  duration?: number;
  success?: boolean;
  step_index?: number;
  /** Backend wall-clock time (unix seconds) when the tool_result was emitted. */
  end_timestamp?: number;
}

export interface TodoUpdateSSEEvent {
  type: 'todo_update';
  /** An empty list is a valid clear and must replace older message-level todos. */
  todos: TodoItem[];
}

export interface PlanUpdateSSEEvent {
  type: 'plan_update';
  plan: Plan;
}

/** Agent-initiated permission downgrade (currently only to plan mode). */
export interface PermissionModeChangedSSEEvent {
  type: 'permission_mode_changed';
  permission_mode: 'plan' | string;
  source?: 'agent' | string;
  reason?: string;
}

export interface SourceSSEEvent {
  type: 'source';
  id: string;
  title: string;
  doc?: string;
  page?: number; // chunk_index (backward compat)
  chunk_index?: number;
  page_start?: number | null; // real PDF page start
  page_end?: number | null; // real PDF page end
  section_title?: string;
  source_url?: string;
  category?: string;
  chunk_method?: string;
  score?: number;
  score_percentage?: number;
  relevance_level?: 'high' | 'medium' | 'low' | string;
  citation?: string;
  excerpt?: string;
  kb_id?: string;
  kb_name?: string;
  small_chunk_id?: string;
  big_chunk_id?: string;
  small_chunk_text?: string;
}

export interface DoneSSEEvent {
  type: 'done';
  success: boolean;
  /** Whether an answer completed, distinct from a paused HITL request. */
  completed?: boolean;
  /** True when the stream ended while awaiting an explicit tool confirmation. */
  awaiting_confirmation?: boolean;
  /** Terminal replay flags for a run that never finished normally. */
  cancelled?: boolean;
  interrupted?: boolean;
  /** Backend-provided reason (e.g. "user stopped"). */
  reason?: string;
  usage?: TokenUsageSSE;
}

export interface ErrorSSEEvent {
  type: 'error';
  code?: string;
  message: string;
  detail?: string;
  /** Safe to resend the same turn without user action (e.g. previous run still releasing). */
  retryable?: boolean;
}

export interface ProgressSSEEvent {
  type: 'progress';
  percent?: number;
  message?: string;
}

export interface ToolConfirmRequiredSSEEvent {
  type: 'tool_confirm_required';
  calls: Array<{
    name: string;
    args: Record<string, unknown>;
    call_id: string;
    risk_level: 'low' | 'medium' | 'high';
  }>;
  count: number;
}

export interface HeartbeatSSEEvent {
  type: 'heartbeat';
  /** Elapsed seconds since the stream started. */
  elapsed: number;
  /** Optional heartbeat message. */
  message?: string;
}

export interface ContextCompressingSSEEvent {
  type: 'context_compressing';
  /** Status message, e.g. "正在压缩上下文..." */
  message?: string;
}

/** Metadata attached to every persisted SSE event (and to the `id:` line). */
export interface RunEventMeta {
  /** Run that produced this event. */
  run_id?: string;
  /** Monotonic per-run sequence. Absent on non-persisted events (heartbeat). */
  seq?: number;
}

/** First persisted event of a run. */
export interface RunStartedSSEEvent extends RunEventMeta {
  type: 'run_started';
  run_id: string;
  seq: number;
}

export type ChatSSEEvent = RunEventMeta & (
  | ThinkingSSEEvent
  | TextSSEEvent
  | ToolCallSSEEvent
  | ToolResultSSEEvent
  | SourceSSEEvent
  | TodoUpdateSSEEvent
  | PlanUpdateSSEEvent
  | PermissionModeChangedSSEEvent
  | ProgressSSEEvent
  | BuildCompileLogSSEEvent
  | ToolConfirmRequiredSSEEvent
  | HeartbeatSSEEvent
  | ContextCompressingSSEEvent
  | DoneSSEEvent
  | ErrorSSEEvent
  | RunStartedSSEEvent
);

/** Lifecycle of a persisted chat run. */
export type RunStatus =
  | 'running'
  | 'awaiting_confirmation'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted';

/** Persisted HITL approval row, as returned by the run state endpoint. */
export interface PendingApprovalRecord {
  id: string;
  run_id: string;
  call_id: string;
  tool_name: string;
  args: Record<string, unknown> | string;
  risk_level?: 'low' | 'medium' | 'high';
  status: 'pending' | 'allowed' | 'denied' | 'stopped' | 'expired' | string;
  created_at?: string;
  expires_at?: string | null;
  decided_at?: string | null;
}

/** Response from GET /api/runs/{run_id} (and the per-session active-run route). */
export interface RunStateResponse {
  id?: string;
  run_id?: string;
  session_id?: string;
  status: RunStatus;
  last_event_seq?: number;
  error_code?: string | null;
  error_message?: string | null;
  pending_approvals?: PendingApprovalRecord[];
  /** ISO timestamps; newer backends expose them for elapsed-time display. */
  started_at?: string | null;
  finished_at?: string | null;
  /** Checkpoint the run can be continued from (interrupted runs). */
  checkpoint_id?: string | null;
  /** Backend assessment: the run can be resumed from its checkpoint. */
  resumable?: boolean;
  /** Backend assessment: a tool side effect may or may not have happened. */
  result_unknown?: boolean;
  /** Last tool/step the backend knows about, when it exposes one. */
  last_tool?: string | null;
}

export interface ModelsRequest {
  base_url: string;
}

export interface ModelsResponse {
  models: string[];
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
}

export interface ToolResult {
  success?: boolean;
  output: string;
  duration_ms?: number;
}

export interface BuildRequest {
  code: string;
  board: string;        // PlatformIO 板 ID，如 esp32-s3-devkitc-1 / black_f407vg
  platform: string;     // espressif32 / ststm32
  options?: Record<string, unknown>;
}

export interface BuildCompileLogSSEEvent {
  type: 'compile_log';
  line: string;
  stream?: 'stdout' | 'stderr';
}

export interface BuildThinkingSSEEvent {
  type: 'thinking';
  content: string;
  source?: 'build' | 'flash';
}

export interface BuildProgressSSEEvent {
  type: 'progress';
  percent?: number;
  message?: string;
}

export interface BuildDoneSSEEvent {
  type: 'done';
  success: boolean;
  binary_path?: string;
  message?: string;
  errors?: string[];
  error?: {
    code: string;
    message: string;
    details?: string;
  };
}

export interface BuildHeartbeatSSEEvent {
  type: 'heartbeat';
  elapsed?: number;
  message?: string;
}

export type BuildSSEEvent =
  | BuildThinkingSSEEvent
  | BuildProgressSSEEvent
  | BuildCompileLogSSEEvent
  | BuildHeartbeatSSEEvent
  | BuildDoneSSEEvent;

export type UploadSSEEvent = BuildSSEEvent;

export interface PinAuditRequest {
  chip: string;
  pin_assignments: Record<string, { function: string; config: string }>;
}

export interface PinWarning {
  pin: string;
  severity: 'critical' | 'warning';
  message: string;
  suggestion: string;
}

export interface PinAuditResponse {
  safe?: boolean;
  warnings: PinWarning[];
  conflicts: PinWarning[];
  pin_map: Record<string, unknown>;
}

export interface DiagnoseRequest {
  code: string;
  env?: string;
  chip?: string;
}

export interface DiagnoseItem {
  name: string;
  status: "PASS" | "WARN" | "FAIL";
  detail: string;
}

export interface DiagnoseResponse {
  results: DiagnoseItem[];
}

export interface DevicesResponse {
  devices: SerialDevice[];
}

// ─── Backend API response types (raw shapes returned by /api/* endpoints) ───
// These describe the wire format from the FastAPI backend before the frontend
// stores remap them into their local domain types (Message / Session / KBDoc …).

/** Raw Message shape from GET /api/sessions/{id}/messages (and nested in GET /api/sessions/{id}). */
export interface BackendMessage {
  id: string;
  /** Backend enum: "user" | "assistant" | "tool" | "system". Kept as string for forward-compat. */
  role: string;
  content: string;
  sources?: SourceRef[] | null;
  tool_calls?: unknown[] | null;
  activity?: ActivityBlock | null;
  /** ISO date string from SQLAlchemy datetime.isoformat() */
  created_at?: string;
}

/** Successful CRUD responses use this { success, data } envelope. */
export interface BackendApiSuccessEnvelope<T> {
  success: true;
  data: T;
}

/** Owner-only data returned by GET /api/sessions/{id}/lifecycle. */
export interface BackendSessionLifecycleData {
  session_id: string;
  incarnation_id: string;
}

/** Full successful response from the owner-only session lifecycle route. */
export type BackendSessionLifecycleResponse = BackendApiSuccessEnvelope<BackendSessionLifecycleData>;

/** Optional query parameters for a paginated GET /api/sessions/{id}/messages request. */
export interface BackendMessagePageQuery {
  limit?: number;
  before_id?: string;
  expected_revision?: number;
  expected_incarnation_id?: string;
}

/** Data returned by the paginated GET /api/sessions/{id}/messages route. */
export interface BackendMessagePageData {
  messages: BackendMessage[];
  /** Null when this page has no older page. */
  next_before_id: string | null;
  has_more: boolean;
  message_revision: number;
}

/** Full successful response from the paginated message-list route. */
export type BackendMessagePageResponse = BackendApiSuccessEnvelope<BackendMessagePageData>;

/** Body for POST /api/sessions/{id}/messages; id enables retry without duplicate rows. */
export interface CreateMessageRequest {
  id?: string;
  role: string;
  content: string;
  sources?: SourceRef[] | null;
  tool_calls?: unknown[] | null;
  activity?: ActivityBlock | null;
}

/** Body for POST /api/sessions/{id}/messages/append-v1. */
export interface AppendMessageV1Request {
  id: string;
  expected_incarnation_id: string;
  role: string;
  content: string;
  sources?: SourceRef[] | null;
  tool_calls?: unknown[] | null;
  activity?: ActivityBlock | null;
}

/** Data returned after a new message is saved or a matching retry is recognized. */
export interface AppendMessageV1Receipt {
  id: string;
  status: 'saved' | 'already_saved';
  message_revision: number;
  /** ISO date string, or an empty string when the stored timestamp is absent. */
  created_at: string;
}

/** Full successful response from the append-v1 route. */
export type AppendMessageV1Response = BackendApiSuccessEnvelope<AppendMessageV1Receipt>;

/** Body for POST /api/sessions/{id}/messages/truncate-v1. */
export interface TruncateMessagesV1Request {
  keep_through_id: string;
  expected_revision: number;
  expected_incarnation_id: string;
}

/** Data returned by the truncate-v1 route. */
export interface TruncateMessagesV1Receipt {
  kept: number;
  deleted: number;
  message_revision: number;
}

/** Full successful response from the truncate-v1 route. */
export type TruncateMessagesV1Response = BackendApiSuccessEnvelope<TruncateMessagesV1Receipt>;

/** Raw Explorer read payload (the legacy read route has no success/data envelope). */
export interface ExplorerReadResponse {
  name: string;
  path: string;
  is_text: boolean;
  /** Present for editable text files; binary previews may omit it. */
  version?: string;
  content?: string;
  data_url?: string;
  size?: number;
}

export interface ExplorerWriteRequest {
  path: string;
  content: string;
  expected_version: string | null;
}

export interface ExplorerWriteResponse {
  path: string;
  version: string;
}

export interface ExplorerDeleteResponse {
  path: string;
  trash_id: string;
}

export interface ExplorerTrashItem {
  item_id: string;
  root_path: string;
  original_path: string;
  name: string;
  kind: "file" | "directory";
  deleted_at: string;
}

export interface ExplorerTrashResponse {
  items: ExplorerTrashItem[];
}

export interface ExplorerRestoreRequest {
  root_path: string;
  item_id: string;
  target_path?: string;
}

export interface ExplorerRestoreResponse {
  path: string;
  item_id: string;
}

/** Raw Session shape from GET /api/sessions (and GET /api/sessions/{id}). */
export interface BackendSession {
  id: string;
  title: string;
  model: string;
  project: string;
  pinned: boolean;
  msg_count: number;
  branch_from_session_id?: string | null;
  branch_from_message_id?: string | null;
  /** Per-session context window budget (tokens). Default 262144 (256K). */
  context_window?: number;
  /** True while the Agent's persisted plan-mode request is active. */
  plan_mode_requested?: boolean;
  /** ISO date string */
  created_at: string;
  /** ISO date string */
  updated_at: string;
}

/** Raw knowledge-base document shape from GET /api/kb/list (documents[] element). */
export interface BackendKBDoc {
  doc_id?: string;
  id?: string;
  title?: string;
  file_size?: number;
  chunk_count?: number;
  status?: string;
  file_type?: string;
  error_message?: string;
  kb_id?: string;
  chunk_method_used?: string;
  /** ISO date string */
  created_at?: string;
}

/** Raw chunk shape from GET /api/kb/documents/{doc_id}/chunks (chunks[] element). */
export interface BackendChunk {
  id?: string;
  chunk_id?: string;
  chunk_index?: number;
  content?: string;
  content_length?: number;
  page_start?: number | null;
  page_end?: number | null;
  section_title?: string;
  chunk_method?: string;
  chunk_size?: number;
  title?: string;
}

/** Raw knowledge-base / collection shape from GET /api/kb/collections (collections[] element). */
export interface BackendKB {
  id: string;
  name: string;
  description?: string;
  collection_name: string;
  chunk_method?: string;
  embedding_model?: string;
  embedding_base_url?: string;
  agent_chunker_model?: string;
  agent_chunker_base_url?: string;
  context_window?: number;
  enabled?: boolean;
  is_builtin?: boolean;
  doc_count?: number;
  chunk_count?: number;
  /** ISO date string */
  created_at?: string;
}

/** Alias for BackendKB — useKnowledgeStore.fetchCollections consumes this shape. */
export type BackendCollection = BackendKB;
