// API 请求/响应类型 — 保持与现有组件兼容的导出集合

import type { SourceRef, ActivityBlock, ContentPart, TodoItem } from './session';
import type { ProviderInfo } from './settings';
import type { KBItem } from './kb';
import type { SerialDevice } from './serial';

export type { SourceRef, ActivityBlock, ContentPart, ProviderInfo, KBItem as KBDoc, SerialDevice };

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
  messages: { role: 'user' | 'assistant' | 'system'; content: string | ContentPart[] }[];
  model?: string;
  temperature?: number;
  max_tokens?: number;
  top_k?: number;
  system_prompt?: string;
  long_term_memory?: string;
  provider?: string;
  api_key?: string;
  base_url?: string;
  attachments?: Attachment[];
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

export interface ToolResultSSEEvent {
  type: 'tool_result';
  call_id: string;
  tool: string;
  result: {
    output?: string;
    error?: string | ToolErrorDetail;
    // Backend ToolResultEnvelope wraps render payload inside data; some legacy
    // tools still put target_pane/render_data at the top level.
    target_pane?: 'wiring' | 'safety' | 'preview';
    render_data?: unknown;
    data?: {
      target_pane?: 'wiring' | 'safety' | 'preview';
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
  todos: TodoItem[];
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
  usage?: TokenUsageSSE;
}

export interface ErrorSSEEvent {
  type: 'error';
  message: string;
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

export type ChatSSEEvent =
  | ThinkingSSEEvent
  | TextSSEEvent
  | ToolCallSSEEvent
  | ToolResultSSEEvent
  | SourceSSEEvent
  | TodoUpdateSSEEvent
  | ProgressSSEEvent
  | BuildCompileLogSSEEvent
  | ToolConfirmRequiredSSEEvent
  | HeartbeatSSEEvent
  | ContextCompressingSSEEvent
  | DoneSSEEvent
  | ErrorSSEEvent;

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

export interface WiringRequest {
  title: string;
  connections: WiringConnection[];
  components: WiringComponent[];
}

export interface WiringConnection {
  from: { component: string; pin: string };
  to: { component: string; pin: string };
  color?: string;
  label?: string;
  /** 连线类型：power=电源, signal=信号, ground=地线 */
  line_type?: "power" | "signal" | "ground";
}

export interface WiringComponent {
  name: string;
  type: string;
  pins: string[];
}

export interface WiringResponse {
  svg: string;
  bom?: { component: string; qty: number }[];
}

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
