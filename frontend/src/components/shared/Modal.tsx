import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useModalStore } from "../../stores/useModalStore";

// 通用 Modal 原语：仅渲染遮罩层，子内容由调用方自定义。
// 已被 BranchTree / ShortcutHelp / KnowledgePanel 复用，保留向后兼容。
interface ModalProps {
  children: React.ReactNode;
  onClose?: () => void;
  /** Whether clicking the backdrop closes the modal. Default: true */
  closeOnBackdrop?: boolean;
}

const BACKDROP_STYLE: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 9999,
  background: "rgba(0,0,0,0.5)",
};

export function Modal({ children, onClose, closeOnBackdrop = true }: ModalProps) {
  return (
    <div
      style={BACKDROP_STYLE}
      onClick={closeOnBackdrop ? onClose : undefined}
    >
      {children}
    </div>
  );
}

export default Modal;

// ============================================================
// ModalContainer：基于 useModalStore 的 confirm/prompt 容器
// 用 createPortal 渲染到 document.body，z-index 9999（低于 Toast 10000）
// ============================================================

const MODAL_Z_INDEX = 9999;
const CARD_MIN_WIDTH = 320;
const CARD_MAX_WIDTH = 480;
const CARD_PADDING_PX = 24;
const CARD_RADIUS_PX = 12;
const BTN_PADDING = "8px 16px";
const BTN_RADIUS_PX = 8;
const INPUT_PADDING = "8px 12px";
const FOCUSABLE_SELECTOR =
  'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

const backdropStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: MODAL_Z_INDEX,
  background: "rgba(0,0,0,0.5)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const cardStyle: React.CSSProperties = {
  background: "var(--card)",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: CARD_RADIUS_PX,
  boxShadow: "var(--shadow-lg)",
  padding: CARD_PADDING_PX,
  minWidth: CARD_MIN_WIDTH,
  maxWidth: CARD_MAX_WIDTH,
  width: "90%",
};

const titleStyle: React.CSSProperties = {
  fontSize: 16,
  fontWeight: 600,
  marginBottom: 12,
};

const messageStyle: React.CSSProperties = {
  fontSize: 13,
  color: "var(--fg)",
  lineHeight: 1.5,
  marginBottom: 16,
  whiteSpace: "pre-wrap",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: INPUT_PADDING,
  border: "1px solid var(--border)",
  borderRadius: BTN_RADIUS_PX,
  background: "var(--bg)",
  color: "var(--fg)",
  fontSize: 13,
  marginBottom: 16,
  outline: "none",
};

const buttonRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  gap: 8,
};

const cancelButtonStyle: React.CSSProperties = {
  padding: BTN_PADDING,
  borderRadius: BTN_RADIUS_PX,
  border: "1px solid var(--border)",
  background: "var(--secondary)",
  color: "var(--secondary-fg)",
  fontSize: 13,
  cursor: "pointer",
};

/** 危险/普通确认按钮样式 */
function buildConfirmStyle(danger?: boolean): React.CSSProperties {
  return {
    padding: BTN_PADDING,
    borderRadius: BTN_RADIUS_PX,
    border: "none",
    background: danger ? "var(--danger)" : "var(--primary)",
    color: "var(--primary-fg)",
    fontSize: 13,
    cursor: "pointer",
    fontWeight: 500,
  };
}

/** 在 modal 卡片内循环焦点（Tab / Shift+Tab） */
function useFocusTrap(cardRef: React.RefObject<HTMLDivElement>, active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const node = cardRef.current;
    if (!node) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const focusables = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    node.addEventListener("keydown", onKey);
    return () => node.removeEventListener("keydown", onKey);
  }, [cardRef, active]);
}

/** ESC 等价取消 */
function useEscapeKey(active: boolean, onCancel: () => void): void {
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, onCancel]);
}

/** prompt 模式自动聚焦输入框并选中文本 */
function useAutofocusInput(
  cardRef: React.RefObject<HTMLDivElement>,
  mode: string | null,
  visible: boolean,
): void {
  useEffect(() => {
    if (!visible || mode !== "prompt") return;
    const node = cardRef.current;
    if (!node) return;
    const input = node.querySelector<HTMLInputElement>("input");
    if (!input) return;
    // 下一帧聚焦，确保 DOM 已挂载
    const raf = requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
    return () => cancelAnimationFrame(raf);
  }, [cardRef, mode, visible]);
}

/** confirm 模式自动聚焦确认按钮 */
function useAutofocusConfirm(
  cardRef: React.RefObject<HTMLDivElement>,
  mode: string | null,
  visible: boolean,
): void {
  useEffect(() => {
    if (!visible || mode !== "confirm") return;
    const node = cardRef.current;
    if (!node) return;
    const raf = requestAnimationFrame(() => {
      const btns = node.querySelectorAll<HTMLButtonElement>("button.confirm-btn");
      btns[btns.length - 1]?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [cardRef, mode, visible]);
}

/** confirm 模式卡片：标题 + 消息 + 取消/确认 */
function ConfirmCard(): React.ReactElement {
  const opts = useModalStore((s) => s.confirmOpts)!;
  const onCancel = useModalStore((s) => s._cancel);
  const onConfirm = useModalStore((s) => s._confirm);
  return (
    <>
      <div style={titleStyle}>{opts.title}</div>
      {opts.message ? <div style={messageStyle}>{opts.message}</div> : null}
      <div style={buttonRowStyle}>
        <button style={cancelButtonStyle} onClick={onCancel}>
          {opts.cancelText ?? "取消"}
        </button>
        <button className="confirm-btn" style={buildConfirmStyle(opts.danger)} onClick={onConfirm}>
          {opts.confirmText ?? "确认"}
        </button>
      </div>
    </>
  );
}

/** prompt 模式卡片：标题 + 输入框 + 取消/确认 */
function PromptCard(): React.ReactElement {
  const opts = useModalStore((s) => s.promptOpts)!;
  const inputValue = useModalStore((s) => s.inputValue);
  const setInput = useModalStore((s) => s._setInput);
  const onCancel = useModalStore((s) => s._cancel);
  const onConfirm = useModalStore((s) => s._confirm);
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onConfirm();
    }
  };
  return (
    <>
      <div style={titleStyle}>{opts.title}</div>
      <input
        style={inputStyle}
        value={inputValue}
        placeholder={opts.placeholder}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKey}
      />
      <div style={buttonRowStyle}>
        <button style={cancelButtonStyle} onClick={onCancel}>
          {opts.cancelText ?? "取消"}
        </button>
        <button className="confirm-btn" style={buildConfirmStyle(false)} onClick={onConfirm}>
          {opts.confirmText ?? "确认"}
        </button>
      </div>
    </>
  );
}

/** 顶层容器：createPortal + 遮罩 + 卡片，由 useModalStore 驱动 */
export function ModalContainer(): React.ReactElement | null {
  const visible = useModalStore((s) => s.visible);
  const mode = useModalStore((s) => s.mode);
  const onCancel = useModalStore((s) => s._cancel);
  const cardRef = useRef<HTMLDivElement>(null);
  useFocusTrap(cardRef, visible);
  useEscapeKey(visible, onCancel);
  useAutofocusInput(cardRef, mode, visible);
  useAutofocusConfirm(cardRef, mode, visible);
  if (!visible || !mode || typeof document === "undefined") return null;
  return createPortal(
    <div style={backdropStyle} onClick={onCancel}>
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        style={cardStyle}
        onClick={(e) => e.stopPropagation()}
      >
        {mode === "confirm" ? <ConfirmCard /> : <PromptCard />}
      </div>
    </div>,
    document.body,
  );
}
