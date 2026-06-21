// API 请求/响应类型 — 保持与现有组件兼容的导出集合

import type { SourceRef, ActivityBlock } from './session';
import type { ProviderInfo } from './settings';
import type { KBItem } from './kb';
import type { SerialDevice } from './serial';

export type { SourceRef, ActivityBlock, ProviderInfo, KBItem as KBDoc, SerialDevice };

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
  messages: { role: 'user' | 'assistant' | 'system'; content: string }[];
  model?: string;
  temperature?: number;
  max_tokens?: number;
  top_k?: number;
  system_prompt?: string;
  long_term_memory?: boolean;
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
  source?: 'rag' | 'llm' | 'reasoning';
}

export interface TextSSEEvent {
  type: 'text';
  content: string;
}

export interface ToolSSEEvent {
  type: 'tool';
  name: string;
  args?: Record<string, unknown> | string;
  result?: string;
  icon?: string;
}

export interface SourceSSEEvent {
  type: 'source';
  id: string;
  title: string;
  doc?: string;
  page?: number;
  score?: number;
  excerpt?: string;
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
  percent: number;
  message?: string;
}

export type ChatSSEEvent =
  | ThinkingSSEEvent
  | TextSSEEvent
  | ToolSSEEvent
  | SourceSSEEvent
  | ProgressSSEEvent
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
  env: string;
  project_dir: string;
}

export interface BuildSSEEvent {
  type: 'progress' | 'done';
  percent?: number;
  message?: string;
  success?: boolean;
  errors?: string[];
}

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
