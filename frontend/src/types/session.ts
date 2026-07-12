export interface Session {
  id: string;
  title: string;
  preview: string;
  model: string;
  /** 创建时间的 epoch 毫秒，用于动态计算 group 和显示时间 */
  createdAt: number;
  project: string;
  pinned: boolean;
  msgCount: number;
  /** Per-session context window budget (tokens). Default 256K = 262144. */
  contextWindow: number;
  /** 兼容旧数据：原始的 timestamp/group/createTime 字段 */
  timestamp?: string;
  group?: string;
  createTime?: string;
  /** 分支来源会话 ID */
  branchFromSessionId?: string;
  /** 分支来源消息 ID */
  branchFromMessageId?: string;
}

export interface TokenUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}


export type { Attachment } from './api';

export interface TextPart {
  type: "text";
  text: string;
}

export interface ImagePart {
  type: "image_url";
  image_url: {
    url: string;
    detail?: "auto" | "low" | "high";
  };
}

export type ContentPart = TextPart | ImagePart;

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string | ContentPart[];
  timestamp: number;
  activity?: ActivityBlock;
  sources?: SourceRef[];
  /** Agent 任务清单（由 TodoWriteTool 推送的 todo_update 事件填充） */
  todos?: TodoItem[];
  quotedMsg?: string;
  branchParentId?: string;
  /** 分支点消息 ID，记录该消息是从哪条消息分支出来的 */
  parentId?: string;
  /** API 返回的真实 token 用量（仅 assistant 消息有） */
  usage?: TokenUsage;
}

export interface TodoItem {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed";
  priority?: "high" | "medium" | "low";
}

export interface ActivityBlock {
  durationMs: number;
  steps: ActivityStep[];
  /** 执行状态：running / done / error（预留） */
  status?: "running" | "done" | "error";
}

export interface ActivityStep {
  type: "thinking" | "tool";
  id: string;
  content?: string;
  name?: string;
  /** 工具图标名（预留，如 'search', 'code', 'datasheet'） */
  icon?: string;
  args?: string;
  result?: string;
  duration?: number;
  /** 步骤状态：pending / running / done / error（预留） */
  status?: "pending" | "running" | "done" | "error";
  /** 来源：rag=知识库检索, llm=LLM生成, reasoning=模型推理思考, agent=普通模型占位思考 */
  source?: "rag" | "llm" | "reasoning" | "agent";
  /** Tool call ID (links tool_call and tool_result SSE events) */
  call_id?: string;
  /** Risk level for permission gating */
  risk_level?: "low" | "medium" | "high";
  /** Decision source (mode_bypass/user_temporary/classifier_low/...) */
  decision_source?: string;
  /** Tool call start timestamp (epoch ms) for live elapsed timer while pending */
  startTime?: number;
  /** File path for file-editing tools (write_file/edit_file/multi_edit/apply_patch) — enables "查看 diff" button */
  file_path?: string;
}

export interface SourceRef {
  id: string;
  title: string;
  doc: string;
  page: number; // chunk_index (backward compat)
  chunk_index?: number;
  page_start?: number | null; // real PDF page start
  page_end?: number | null; // real PDF page end
  section_title?: string;
  source_url?: string;
  category?: string;
  chunk_method?: string;
  score: number;
  score_percentage?: number;
  relevance_level?: 'high' | 'medium' | 'low' | string;
  citation?: string;
  excerpt: string;
  kb_id?: string;
  kb_name?: string;
  small_chunk_id?: string; // for fetching big_chunk_text via API
  big_chunk_id?: string; // parent big chunk id, shared by multiple small chunks
  small_chunk_text?: string; // small chunk text for highlight in big chunk
  /** 所属消息 ID，用于解决 source id 跨消息重复（src1/src2 per-request） */
  messageId?: string;
}
