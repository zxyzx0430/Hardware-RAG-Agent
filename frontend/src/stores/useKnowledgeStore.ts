import { create } from "zustand";
import type { KBDoc } from "../types/api";
import type { KnowledgeBase, CreateKBRequest, KBCollectionDetail } from "../types/kb";
import { apiGet, apiPost, apiDelete, apiPatch } from "../api/client";
import { useLogStore } from "./useLogStore";

interface KnowledgeState {
  // ─── Document-level (legacy) ───
  items: KBDoc[];
  isUploading: boolean;
  setItems: (items: KBDoc[]) => void;
  addItem: (item: KBDoc) => void;
  toggleItem: (id: string) => void;
  deleteItem: (id: string) => void;
  setIsUploading: (v: boolean) => void;
  fetchItems: (kbId?: string) => Promise<void>;
  deleteItemWithAPI: (id: string) => Promise<void>;

  // ─── Collection-level (multi-KB) ───
  collections: KnowledgeBase[];
  activeKbId: string;              // currently selected KB for upload
  isLoadingCollections: boolean;
  fetchCollections: () => Promise<void>;
  setActiveKb: (kbId: string) => void;
  createCollection: (req: CreateKBRequest) => Promise<KnowledgeBase | null>;
  deleteCollection: (kbId: string) => Promise<boolean>;
  toggleCollection: (kbId: string, enabled: boolean) => Promise<boolean>;
  getCollectionDetail: (kbId: string) => Promise<KBCollectionDetail | null>;
  fetchEmbeddingModels: (baseUrl: string, apiKey: string) => Promise<string[]>;
  exportCollection: (kbId: string) => Promise<void>;
  importCollection: (kbId: string, file: File) => Promise<number>;
}

const DEFAULT_KB_ID = "builtin-001";

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "—";
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
  // ─── Document-level ───
  items: [],
  isUploading: false,

  setItems: (items) => set({ items }),
  addItem: (item) => {
    useLogStore.getState().log("info", "kb", `添加文档: ${item.name}`);
    set((s) => ({ items: [item, ...s.items] }));
  },
  toggleItem: (id) =>
    set((s) => ({
      items: s.items.map((x) =>
        x.id === id ? { ...x, enabled: !x.enabled } : x
      ),
    })),
  deleteItem: (id) =>
    set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
  setIsUploading: (isUploading) => set({ isUploading }),

  fetchItems: async (kbId?: string) => {
    try {
      const endpoint = kbId ? `kb/list?kb_id=${encodeURIComponent(kbId)}` : "kb/list";
      const data = await apiGet<{ documents: any[] }>(endpoint);
      if (data?.documents) {
        const items: KBDoc[] = data.documents.map((doc: any) => ({
          id: doc.doc_id ?? doc.id,
          name: doc.title ?? doc.doc_id ?? "未知文件",
          size: formatFileSize(doc.file_size ?? 0),
          chunks: doc.chunk_count ?? 0,
          status: doc.status ?? "indexed",
          enabled: true,
          updatedAt: doc.created_at ? doc.created_at.slice(0, 10) : new Date().toISOString().slice(0, 10),
          docType: doc.file_type ?? "Text",
          tags: [],
          errorMessage: doc.error_message,
          kb_id: doc.kb_id,
          chunk_method_used: doc.chunk_method_used,
        }));
        set({ items });
        useLogStore.getState().log("ok", "kb", `加载 ${items.length} 个知识库文档`);
      }
    } catch {
      useLogStore.getState().log("error", "kb", "知识库列表加载失败");
    }
  },

  deleteItemWithAPI: async (id) => {
    useLogStore.getState().log("info", "kb", `删除文档: ${id}`);
    // 先从本地移除（乐观更新）
    set((s) => ({ items: s.items.filter((x) => x.id !== id) }));
    try {
      await apiPost("kb/delete", { doc_id: id });
    } catch {
      // API 失败仍保留本地删除（乐观策略）
      useLogStore.getState().log("warn", "kb", `后端删除失败: ${id}`);
    }
  },

  // ─── Collection-level ───
  collections: [],
  activeKbId: DEFAULT_KB_ID,
  isLoadingCollections: false,

  fetchCollections: async () => {
    set({ isLoadingCollections: true });
    try {
      const data = await apiGet<{ collections: any[] }>("kb/collections");
      if (data?.collections) {
        const kbs: KnowledgeBase[] = data.collections.map((kb: any) => ({
          id: kb.id,
          name: kb.name,
          description: kb.description ?? "",
          collection_name: kb.collection_name,
          chunk_method: kb.chunk_method ?? "hybrid",
          embedding_model: kb.embedding_model ?? "",
          embedding_base_url: kb.embedding_base_url ?? "",
          agent_chunker_model: kb.agent_chunker_model ?? "",
          agent_chunker_base_url: kb.agent_chunker_base_url ?? "",
          context_window: kb.context_window ?? 0,
          enabled: kb.enabled ?? true,
          is_builtin: kb.is_builtin ?? false,
          doc_count: kb.doc_count ?? 0,
          chunk_count: kb.chunk_count ?? 0,
          created_at: kb.created_at ?? "",
        }));
        set({ collections: kbs });

        // If active KB no longer exists, reset to first available (prefer builtin)
        const activeExists = kbs.some((k) => k.id === get().activeKbId);
        if (!activeExists) {
          const builtin = kbs.find((k) => k.is_builtin);
          set({ activeKbId: builtin?.id ?? kbs[0]?.id ?? DEFAULT_KB_ID });
        }

        useLogStore.getState().log("ok", "kb", `加载 ${kbs.length} 个知识库`);
      }
    } catch {
      useLogStore.getState().log("error", "kb", "知识库列表加载失败");
    } finally {
      set({ isLoadingCollections: false });
    }
  },

  setActiveKb: (kbId) => set({ activeKbId: kbId }),

  createCollection: async (req) => {
    try {
      const data = await apiPost<KnowledgeBase>("kb/collections", req);
      if (data) {
        await get().fetchCollections();
        useLogStore.getState().log("ok", "kb", `创建知识库: ${data.name}`);
        return data;
      }
      return null;
    } catch (e) {
      useLogStore.getState().log("error", "kb", `创建知识库失败: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  },

  deleteCollection: async (kbId) => {
    try {
      await apiDelete(`kb/collections/${kbId}`);
      const remaining = get().collections.filter((k) => k.id !== kbId);
      // Reset activeKbId if we just deleted the active KB
      const newActiveId = get().activeKbId === kbId
        ? (remaining.find((k) => k.is_builtin)?.id ?? remaining[0]?.id ?? DEFAULT_KB_ID)
        : get().activeKbId;
      set({ collections: remaining, activeKbId: newActiveId });
      // Sync with backend to ensure consistency
      get().fetchCollections();
      useLogStore.getState().log("ok", "kb", `删除知识库: ${kbId}`);
      return true;
    } catch (e) {
      useLogStore.getState().log("error", "kb", `删除知识库失败: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  },

  toggleCollection: async (kbId, enabled) => {
    // 乐观更新
    set((s) => ({
      collections: s.collections.map((k) =>
        k.id === kbId ? { ...k, enabled } : k
      ),
    }));
    try {
      await apiPatch(`kb/collections/${kbId}/toggle`, { enabled });
      useLogStore.getState().log("ok", "kb", `${enabled ? "启用" : "禁用"}知识库: ${kbId}`);
      return true;
    } catch (e) {
      // 回滚
      set((s) => ({
        collections: s.collections.map((k) =>
          k.id === kbId ? { ...k, enabled: !enabled } : k
        ),
      }));
      useLogStore.getState().log("error", "kb", `切换知识库开关失败: ${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
  },

  getCollectionDetail: async (kbId) => {
    try {
      return await apiGet<KBCollectionDetail>(`kb/collections/${kbId}`);
    } catch (e) {
      useLogStore.getState().log("error", "kb", `获取知识库详情失败: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  },

  fetchEmbeddingModels: async (baseUrl, apiKey) => {
    // Do not swallow errors — let callers show the real failure reason
    const data = await apiPost<{ models: string[] }>("kb/embedding-models", {
      base_url: baseUrl,
      api_key: apiKey,
    }, 30000);
    return data?.models ?? [];
  },

  exportCollection: async (kbId) => {
    try {
      const data = await apiPost<any>(`kb/${kbId}/export`, {});
      if (data) {
        // Trigger browser download
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `kb_${data.name || kbId}_${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        useLogStore.getState().log("ok", "kb", `导出知识库: ${data.name || kbId} (${data.chunk_count || 0} chunks)`);
      }
    } catch (e) {
      useLogStore.getState().log("error", "kb", `导出知识库失败: ${e instanceof Error ? e.message : String(e)}`);
      throw e;
    }
  },

  importCollection: async (kbId, file) => {
    try {
      const formData = new FormData();
      formData.append("file", file);
      const data = await apiPost<{ imported_chunks: number; source_kb: string; source_model: string }>(
        `kb/${kbId}/import`,
        formData,
        120000,
      );
      const imported = data?.imported_chunks ?? 0;
      useLogStore.getState().log("ok", "kb", `导入知识库: ${data?.source_kb || ""} (${imported} chunks)`);
      // Refresh collections to update doc/chunk counts
      get().fetchCollections();
      return imported;
    } catch (e) {
      useLogStore.getState().log("error", "kb", `导入知识库失败: ${e instanceof Error ? e.message : String(e)}`);
      throw e;
    }
  },
}));
