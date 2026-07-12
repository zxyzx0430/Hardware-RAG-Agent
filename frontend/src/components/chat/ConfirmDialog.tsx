import { forwardRef, useEffect, useRef } from "react";
import { useChatStore } from "../../stores/useChatStore";

// v2-T4: HITL tool-call confirmation dialog.
// Shows when pendingConfirm is set (after tool_confirm_required SSE event).
// 3 buttons: allow once / deny / stop.

const ARGS_PREVIEW_MAX = 160;
const ARGS_PREVIEW_SLICE = 80;
const FOCUSABLE_SELECTOR = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

type ResumeDecision = "allow" | "deny" | "stop";

type DialogButtonProps = {
  label: string;
  variant: "primary" | "secondary" | "danger" | "danger-stop";
  onClick: () => void;
  disabled?: boolean;
};

/** Truncate args to a readable preview string. */
function formatArgs(args: Record<string, unknown>): string {
  const raw = typeof args === "string" ? args : JSON.stringify(args ?? {});
  if (raw.length <= ARGS_PREVIEW_MAX) return raw;
  return raw.slice(0, ARGS_PREVIEW_SLICE) + "…";
}

/** Risk level badge — reuses .risk-badge risk-${level} from chat.css. */
function RiskBadge({ level }: { level: "low" | "medium" | "high" }) {
  return <span className={`risk-badge risk-${level}`}>{level.toUpperCase()}</span>;
}

/** One tool call row inside the confirm dialog. */
function CallRow({ call }: { call: { name: string; args: Record<string, unknown>; risk_level: "low" | "medium" | "high" } }) {
  return (
    <div className="confirm-call-row">
      <div className="confirm-call-header">
        <span className="confirm-call-name">{call.name}</span>
        <RiskBadge level={call.risk_level} />
      </div>
      <pre className="confirm-call-args">{formatArgs(call.args)}</pre>
    </div>
  );
}

/** Footer button — variant controls color. */
const DialogButton = forwardRef<HTMLButtonElement, DialogButtonProps>(function DialogButton(
  { label, variant, onClick, disabled },
  ref
) {
  return (
    <button
      ref={ref}
      className={`confirm-btn confirm-btn-${variant}`}
      onClick={onClick}
      disabled={disabled}
    >
      {label}
    </button>
  );
});

/** 获取对话框内所有可聚焦元素 */
function getFocusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

/** Tab 焦点循环：首尾元素之间循环，防止焦点跑到对话框外 */
function cycleTab(e: KeyboardEvent, dialog: HTMLElement | null) {
  const focusables = getFocusables(dialog);
  if (focusables.length === 0) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
}

export default function ConfirmDialog() {
  const pendingConfirm = useChatStore((s) => s.pendingConfirm);
  const resumeAgent = useChatStore((s) => s.resumeAgent);
  const dialogRef = useRef<HTMLDivElement>(null);
  const denyBtnRef = useRef<HTMLButtonElement>(null);

  // ESC = deny decision, 让 Agent 收到拒绝继续往下走（不永久阻塞）
  useEffect(() => {
    if (!pendingConfirm) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); resumeAgent("deny"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingConfirm, resumeAgent]);

  // Focus trap + 自动聚焦拒绝按钮（危险操作默认焦点在取消）
  useEffect(() => {
    if (!pendingConfirm) return;
    denyBtnRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Tab") cycleTab(e, dialogRef.current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pendingConfirm]);

  if (!pendingConfirm) return null;

  const handleDecision = (decision: ResumeDecision) => {
    resumeAgent(decision);
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
      <div className="confirm-dialog" ref={dialogRef}>
        <div className="confirm-dialog-header">
          <span id="confirm-dialog-title" className="confirm-dialog-title">
            工具调用确认
          </span>
          <span className="confirm-dialog-count">
            {pendingConfirm.count} 个操作待确认
          </span>
        </div>
        <div className="confirm-dialog-body">
          <p className="confirm-dialog-hint">
            Agent 请求执行以下操作，请确认是否允许：
          </p>
          {pendingConfirm.calls.map((call, idx) => (
            <CallRow key={call.call_id || idx} call={call} />
          ))}
        </div>
        <div className="confirm-dialog-footer">
          <DialogButton label="允许本次" variant="primary" onClick={() => handleDecision("allow")} />
          <DialogButton label="拒绝" variant="danger" onClick={() => handleDecision("deny")} ref={denyBtnRef} />
          <DialogButton label="停止" variant="danger-stop" onClick={() => handleDecision("stop")} />
        </div>
        <div className="confirm-dialog-hint-footer">
          <kbd>Esc</kbd> 关闭
        </div>
      </div>
    </div>
  );
}
