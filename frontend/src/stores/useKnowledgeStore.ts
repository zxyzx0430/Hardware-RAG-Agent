import { create } from "zustand";
import type { KBDoc } from "../types/api";
import { apiGet, apiPost } from "../api/client";
import { useLogStore } from "./useLogStore";

interface KnowledgeState {
  items: KBDoc[];
  isUploading: boolean;
  setItems: (items: KBDoc[]) => void;
  addItem: (item: KBDoc) => void;
  toggleItem: (id: string) => void;
  deleteItem: (id: string) => void;
  setIsUploading: (v: boolean) => void;
  fetchItems: () => Promise<void>;
  deleteItemWithAPI: (id: string) => Promise<void>;
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
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

  fetchItems: async () => {
    try {
      const data = await apiGet<{ documents: any[] }>("kb/list");
      if (data?.documents) {
        const items: KBDoc[] = data.documents.map((doc: any) => ({
          id: doc.doc_id ?? doc.id,
          name: doc.filename ?? doc.name ?? "未知文件",
          size: doc.size ?? "—",
          chunks: doc.chunks ?? 0,
          status: doc.status ?? "indexed",
          enabled: doc.enabled ?? true,
          updatedAt: doc.updated_at ?? doc.updatedAt ?? new Date().toISOString().slice(0, 10),
          docType: doc.doc_type ?? doc.docType ?? "Text",
          tags: doc.tags ?? [],
          errorMessage: doc.error_message ?? doc.errorMessage,
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
}));
