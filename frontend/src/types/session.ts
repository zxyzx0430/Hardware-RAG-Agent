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
  quotedMsg?: string;
  branchParentId?: string;
  /** 分支点消息 ID，记录该消息是从哪条消息分支出来的 */
  parentId?: string;
  /** API 返回的真实 token 用量（仅 assistant 消息有） */
  usage?: TokenUsage;
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
  /** 来源：rag=知识库检索, llm=LLM生成, reasoning=模型推理思考 */
  source?: "rag" | "llm" | "reasoning";
}

export interface SourceRef {
  id: string;
  title: string;
  doc: string;
  page: number;
  score: number;
  excerpt: string;
  kb_id?: string;
  kb_name?: string;
}
