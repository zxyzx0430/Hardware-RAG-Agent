// ─── Document-level (single file within a KB) ───
export interface KBItem {
  id: string;
  name: string;
  size: string;
  chunks: number;
  status: "indexed" | "indexing" | "error";
  enabled: boolean;
  updatedAt: string;
  docType: string;
  tags: string[];
  errorMessage?: string;
  kb_id?: string;
  chunk_method_used?: string;
}

// ─── Collection-level (knowledge base itself) ───
export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  collection_name: string;
  chunk_method: "hybrid" | "agent";
  embedding_model: string;
  embedding_base_url?: string;
  agent_chunker_model: string;
  agent_chunker_base_url?: string;
  context_window?: number;
  enabled: boolean;
  is_builtin: boolean;
  doc_count: number;
  chunk_count: number;
  created_at: string;
}

export interface CreateKBRequest {
  name: string;
  description?: string;
  chunk_method: "hybrid" | "agent";
  embedding_model: string;
  embedding_base_url?: string;
  embedding_api_key?: string;
  agent_chunker_model?: string;
  agent_chunker_base_url?: string;
  agent_chunker_api_key?: string;
  context_window?: number;
}

export interface KBCollectionDetail extends KnowledgeBase {
  documents: KBCollectionDoc[];
}

export interface KBCollectionDoc {
  doc_id: string;
  title: string;
  file_type: string;
  file_size: number;
  chunk_count: number;
  chunk_method_used: string;
  status: string;
  created_at: string;
}

// ─── Embedding models ───
export interface EmbeddingModelInfo {
  id: string;
  name?: string;
}
