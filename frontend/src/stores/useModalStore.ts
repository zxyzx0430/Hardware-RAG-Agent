// 全局 Modal 状态管理：提供 Promise 异步接口的 confirmDialog / promptDialog。
// 用 Zustand create，调用方 await confirmDialog(opts) 等待用户操作。
// _confirm / _cancel 内部调用 resolveFn 并关闭 Modal；ESC 等价 _cancel。
// 在非组件代码中可用 useModalStore.getState().confirmDialog(...) 调用。
import { create } from "zustand";

export interface ConfirmOptions {
  title: string;
  message?: string;
  confirmText?: string;
  cancelText?: string;
  /** 危险操作时确认按钮红色 */
  danger?: boolean;
}

export interface PromptOptions {
  title: string;
  placeholder?: string;
  defaultValue?: string;
  confirmText?: string;
  cancelText?: string;
}

export type ModalMode = "confirm" | "prompt";

interface ModalState {
  visible: boolean;
  mode: ModalMode | null;
  confirmOpts: ConfirmOptions | null;
  promptOpts: PromptOptions | null;
  inputValue: string;
  /** Promise resolve 函数，_confirm/_cancel 调用后置 null */
  resolveFn: ((value: boolean | string | null) => void) | null;
  /** 确认操作：resolve(true) 或 resolve(inputValue)，然后关闭 */
  confirmDialog: (opts: ConfirmOptions) => Promise<boolean>;
  /** 输入操作：resolve(inputValue) 或 resolve(null)，然后关闭 */
  promptDialog: (opts: PromptOptions) => Promise<string | null>;
  _confirm: () => void;
  _cancel: () => void;
  _setInput: (v: string) => void;
}

const EMPTY_INPUT = "";

function resetState(): Partial<ModalState> {
  return {
    visible: false,
    mode: null,
    confirmOpts: null,
    promptOpts: null,
    inputValue: EMPTY_INPUT,
    resolveFn: null,
  };
}

export const useModalStore = create<ModalState>((set, get) => ({
  visible: false,
  mode: null,
  confirmOpts: null,
  promptOpts: null,
  inputValue: EMPTY_INPUT,
  resolveFn: null,

  confirmDialog: (opts) =>
    new Promise<boolean>((resolve) => {
      set({
        visible: true,
        mode: "confirm",
        confirmOpts: opts,
        promptOpts: null,
        inputValue: EMPTY_INPUT,
        resolveFn: resolve as (value: boolean | string | null) => void,
      });
    }),

  promptDialog: (opts) =>
    new Promise<string | null>((resolve) => {
      set({
        visible: true,
        mode: "prompt",
        confirmOpts: null,
        promptOpts: opts,
        inputValue: opts.defaultValue ?? EMPTY_INPUT,
        resolveFn: resolve as (value: boolean | string | null) => void,
      });
    }),

  _confirm: () => {
    const { mode, resolveFn, inputValue } = get();
    if (!resolveFn) return;
    // confirm 模式 resolve(true)，prompt 模式 resolve(当前输入值)
    resolveFn(mode === "prompt" ? inputValue : true);
    set(resetState());
  },

  _cancel: () => {
    const { mode, resolveFn } = get();
    if (!resolveFn) return;
    // confirm 模式 resolve(false)，prompt 模式 resolve(null)
    resolveFn(mode === "prompt" ? null : false);
    set(resetState());
  },

  _setInput: (v) => set({ inputValue: v }),
}));
