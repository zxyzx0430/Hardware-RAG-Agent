// 全局 toast 状态管理：提供 success/error/warning/info 四类轻量提示。
// 用 Zustand create，addToast 后 setTimeout 自动 removeToast。
// 在非组件代码（如 useChatStore）中用 useToastStore.getState().showError(...) 调用，
// 避免 React hook 规则问题。
import { create } from "zustand";

export type ToastType = "success" | "error" | "warning" | "info";

export interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
  duration: number;
}

interface ToastState {
  toasts: ToastItem[];
  addToast: (type: ToastType, message: string, duration?: number) => void;
  removeToast: (id: string) => void;
  showError: (message: string, duration?: number) => void;
  showSuccess: (message: string, duration?: number) => void;
  showWarning: (message: string, duration?: number) => void;
  showInfo: (message: string, duration?: number) => void;
}

const DEFAULT_DURATION = 3000;

function genId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function scheduleRemoval(id: string, duration: number): void {
  if (duration > 0) {
    setTimeout(() => useToastStore.getState().removeToast(id), duration);
  }
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  addToast: (type, message, duration = DEFAULT_DURATION) => {
    const id = genId();
    set((s) => ({ toasts: [...s.toasts, { id, type, message, duration }] }));
    scheduleRemoval(id, duration);
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  showError: (message, duration) => get().addToast("error", message, duration),
  showSuccess: (message, duration) => get().addToast("success", message, duration),
  showWarning: (message, duration) => get().addToast("warning", message, duration),
  showInfo: (message, duration) => get().addToast("info", message, duration),
}));
