import { useState } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { useI18n } from "../../i18n";

/** Pending/approved plan banner above the input bar. */
export function PlanBanner() {
  const { t } = useI18n();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const plan = useChatStore((s) => s.sessionPlans[s.activeSessionId] ?? null);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const approvePlan = useChatStore((s) => s.approvePlan);
  const rejectPlan = useChatStore((s) => s.rejectPlan);
  const executeApprovedPlan = useChatStore((s) => s.executeApprovedPlan);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const setRightMode = useAppStore((s) => s.setRightMode);
  const setRightContentTab = useAppStore((s) => s.setRightContentTab);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);

  if (!activeSessionId || !plan) return null;
  const pending = plan.status === "pending";
  const approved = plan.status === "approved" && !plan.executed_run_id;
  if (!pending && !approved) return null;

  const disabled = busy !== null || isStreaming;

  const viewPlan = () => {
    setRightPanelOpen(true);
    setRightMode("content");
    setRightContentTab("plan");
  };

  const handleApprove = async () => {
    if (disabled) return;
    setBusy("approve");
    const approvedPlan = await approvePlan(activeSessionId, plan.id);
    setBusy(null);
    if (approvedPlan) executeApprovedPlan(approvedPlan);
  };

  const handleReject = async () => {
    if (disabled) return;
    setBusy("reject");
    await rejectPlan(activeSessionId, plan.id);
    setBusy(null);
  };

  const handleExecute = () => {
    if (disabled) return;
    executeApprovedPlan(plan);
  };

  const title = pending
    ? t('planPendingTitle').replace('{n}', String(plan.version))
    : t('planApprovedTitle').replace('{n}', String(plan.version));

  return (
    <div className={`plan-banner ${pending ? 'pending' : 'approved'}`} role="status">
      <span className="plan-banner-icon" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M9 11l3 3L22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
      </span>
      <span className="plan-banner-label">{title}</span>
      <span className="plan-banner-title" title={plan.title}>{plan.title}</span>
      <div className="plan-banner-actions">
        <button type="button" className="plan-banner-btn" onClick={viewPlan}>
          {t('planView')}
        </button>
        {pending ? (
          <>
            <button
              type="button"
              className="plan-banner-btn primary"
              onClick={() => void handleApprove()}
              disabled={disabled}
            >
              {busy === "approve" ? t('planApproving') : t('planApprove')}
            </button>
            <button
              type="button"
              className="plan-banner-btn danger"
              onClick={() => void handleReject()}
              disabled={disabled}
            >
              {busy === "reject" ? t('planRejecting') : t('planReject')}
            </button>
          </>
        ) : (
          <button type="button" className="plan-banner-btn primary" onClick={handleExecute} disabled={disabled}>
            {t('planExecute')}
          </button>
        )}
      </div>
    </div>
  );
}
