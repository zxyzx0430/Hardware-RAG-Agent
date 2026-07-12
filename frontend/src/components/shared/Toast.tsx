// 全局 Toast 容器：固定右上角，用 createPortal 渲染到 document.body，
// 避免被父级 containment 钳制。z-index 10000 高于 Modal 的 9999。
// 进入动画：从右滑入（CSS transition）。
import { useEffect, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { useToastStore, type ToastItem, type ToastType } from "../../stores/useToastStore";

const TOAST_Z_INDEX = 10000;
const TOAST_TOP_PX = 16;
const TOAST_RIGHT_PX = 16;
const TOAST_GAP_PX = 8;
const TOAST_MIN_WIDTH = 240;
const TOAST_MAX_WIDTH = 360;
const SLIDE_DURATION_MS = 250;
const CARD_BORDER_LEFT_WIDTH = 4;

// error 用 --danger（项目无 --error 变量），info 用 --accent-fg（项目无 --info 变量）
const BORDER_VAR: Record<ToastType, string> = {
  success: "var(--success)",
  error: "var(--danger)",
  warning: "var(--warn)",
  info: "var(--accent-fg)",
};

const containerStyle: CSSProperties = {
  position: "fixed",
  top: TOAST_TOP_PX,
  right: TOAST_RIGHT_PX,
  zIndex: TOAST_Z_INDEX,
  display: "flex",
  flexDirection: "column",
  gap: TOAST_GAP_PX,
  pointerEvents: "none",
};

const messageStyle: CSSProperties = { flex: 1, fontSize: 13, lineHeight: 1.4 };

const closeBtnStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--muted-fg)",
  cursor: "pointer",
  padding: 0,
  fontSize: 14,
  lineHeight: 1,
};

const itemWrapperStyle: CSSProperties = { pointerEvents: "auto" };

function buildCardStyle(type: ToastType, visible: boolean): CSSProperties {
  return {
    background: "var(--card)",
    color: "var(--fg)",
    borderLeft: `${CARD_BORDER_LEFT_WIDTH}px solid ${BORDER_VAR[type]}`,
    borderRadius: 6,
    boxShadow: "var(--shadow-md)",
    padding: "10px 14px",
    minWidth: TOAST_MIN_WIDTH,
    maxWidth: TOAST_MAX_WIDTH,
    display: "flex",
    alignItems: "flex-start",
    gap: TOAST_GAP_PX,
    transform: visible ? "translateX(0)" : "translateX(120%)",
    opacity: visible ? 1 : 0,
    transition: `transform ${SLIDE_DURATION_MS}ms ease, opacity ${SLIDE_DURATION_MS}ms ease`,
  };
}

// 进入动画：mount 后下一帧触发 transform 归位
function useSlideIn(): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(raf);
  }, []);
  return visible;
}

function ToastCard({ toast, onClose }: { toast: ToastItem; onClose: () => void }) {
  const visible = useSlideIn();
  return (
    <div style={buildCardStyle(toast.type, visible)} role="alert">
      <span style={messageStyle}>{toast.message}</span>
      <button onClick={onClose} aria-label="关闭" style={closeBtnStyle}>×</button>
    </div>
  );
}

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts);
  const removeToast = useToastStore((s) => s.removeToast);
  if (toasts.length === 0 || typeof document === "undefined") return null;
  return createPortal(
    <div style={containerStyle}>
      {toasts.map((t) => (
        <div key={t.id} style={itemWrapperStyle}>
          <ToastCard toast={t} onClose={() => removeToast(t.id)} />
        </div>
      ))}
    </div>,
    document.body,
  );
}
