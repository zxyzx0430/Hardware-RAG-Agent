import { useEffect, useRef, useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useToastStore } from "../../stores/useToastStore";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";

/** Plan card shown inline in the conversation, with the three user actions. */
export function PlanChatCard() {
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const plan = useChatStore((s) => s.sessionPlans?.[s.activeSessionId] ?? null);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const approvePlan = useChatStore((s) => s.approvePlan);
  const rejectPlan = useChatStore((s) => s.rejectPlan);
  const executeApprovedPlan = useChatStore((s) => s.executeApprovedPlan);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const setRightMode = useAppStore((s) => s.setRightMode);
  const setRightContentTab = useAppStore((s) => s.setRightContentTab);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const openedPlanRef = useRef<string | null>(null);

  const pending = plan?.status === "pending";
  const approved = plan?.status === "approved" && !plan?.executed_run_id;

  useEffect(() => {
    if (!activeSessionId || !plan || !pending) return;
    if (openedPlanRef.current === plan.id) return;
    openedPlanRef.current = plan.id;
    setRightPanelOpen(true);
    setRightMode("content");
    setRightContentTab(activeSessionId, "plan");
  }, [activeSessionId, plan, pending, setRightPanelOpen, setRightMode, setRightContentTab]);

  if (!activeSessionId || !plan || (!pending && !approved)) return null;

  const disabled = busy !== null || isStreaming;

  const viewPlan = () => {
    setRightPanelOpen(true);
    setRightMode("content");
    setRightContentTab(activeSessionId, "plan");
  };

  const implement = async () => {
    if (disabled) return;
    if (!pending) {
      executeApprovedPlan(plan);
      return;
    }
    setBusy("approve");
    const approvedPlan = await approvePlan(activeSessionId, plan.id);
    setBusy(null);
    if (approvedPlan) executeApprovedPlan(approvedPlan);
  };

  const supplement = () => {
    useToastStore.getState().showInfo("请补充信息，发送后会生成新的计划版本");
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>('textarea[placeholder*="向硬件文档提问"]')?.focus();
    });
  };

  const cancel = async () => {
    if (!pending || disabled) return;
    setBusy("reject");
    await rejectPlan(activeSessionId, plan.id);
    setBusy(null);
  };

  return (
    <section className="plan-chat-card" aria-label="计划">
      <div className="plan-chat-head">
        <span className={`plan-status plan-status-${plan.status}`}>
          {pending ? "待批准" : "已批准"}
        </span>
        <span className="plan-chat-version">计划 v{plan.version}</span>
        <button type="button" className="plan-chat-view" onClick={viewPlan}>在右侧查看</button>
      </div>
      <h3 className="plan-chat-title">{plan.title}</h3>
      {plan.summary ? (
        <div className="plan-chat-summary plan-chat-markdown">
          <MarkdownRenderer content={plan.summary} />
        </div>
      ) : null}
      {plan.steps.length > 0 ? (
        <ol className="plan-chat-steps">
          {plan.steps.map((step, index) => (
            <li key={`${index}-${step.title}`}>
              <span className="plan-chat-step-number">{index + 1}.</span>
              <span>{step.title}</span>
            </li>
          ))}
        </ol>
      ) : null}
      {plan.file_path ? (
        <div className="plan-chat-file">
          计划文件：<code>{plan.file_path}</code>
        </div>
      ) : null}
      <div className="plan-chat-actions">
        {pending ? (
          <>
            <button type="button" className="plan-chat-btn primary" onClick={() => void implement()} disabled={disabled}>
              {busy === "approve" ? "正在实施…" : "按计划实施"}
            </button>
            <button type="button" className="plan-chat-btn" onClick={supplement} disabled={disabled}>
              补充信息
            </button>
            <button type="button" className="plan-chat-btn danger" onClick={() => void cancel()} disabled={disabled}>
              {busy === "reject" ? "正在取消…" : "取消"}
            </button>
          </>
        ) : (
          <button type="button" className="plan-chat-btn primary" onClick={() => void implement()} disabled={disabled}>
            按计划实施
          </button>
        )}
      </div>
    </section>
  );
}
