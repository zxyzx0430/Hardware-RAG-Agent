import { useChatStore } from "../../stores/useChatStore";
import { useI18n } from "../../i18n";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";
import { buildPlanMarkdown } from "../../utils/planMarkdown";

/** Read-only plan view rendered inside the right panel's "plan" tab. */
export function PlanPanel() {
  const { t } = useI18n();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const plan = useChatStore((s) => s.sessionPlans[s.activeSessionId] ?? null);
  const changeSets = useChatStore((s) => s.changeSetsBySession?.[s.activeSessionId]);
  const linkedSets = (changeSets || []).filter((set) => plan?.id && set.plan_id === plan.id);
  const linkedItems = linkedSets.flatMap((set) => set.items);
  const accepted = linkedItems.filter((item) => item.review_status === "accepted").length;
  const pending = linkedItems.filter((item) => item.review_status === "pending").length;
  const reverted = linkedItems.filter((item) => item.status === "reverted").length;

  if (!activeSessionId || !plan) {
    return (
      <div className="plan-panel-empty">
        {t('noPlan')}
      </div>
    );
  }

  // A plan rejected before it was ever executed is not an execution failure;
  // "执行被拒绝" is reserved for an approved run whose tools were denied.
  const showExecution = Boolean(
    plan.execution_state
    && plan.execution_state !== "none"
    && !(plan.status === "rejected" && !plan.executed_run_id),
  );

  const markdown = buildPlanMarkdown(plan, {
    summary: t('planSummary'),
    steps: t('planSteps'),
    verification: t('planStepVerification'),
    risks: t('planRisks'),
    openQuestions: t('planOpenQuestions'),
  });

  return (
    <div className="plan-panel">
      <div className="plan-panel-head">
        <span className={`plan-status plan-status-${plan.status}`}>
          {t(`planStatus_${plan.status}`)}
        </span>
        {showExecution ? (
          <span className={`plan-execution plan-execution-${plan.execution_state}`}>
            {t(`planExecution_${plan.execution_state}`)}
          </span>
        ) : null}
        <span className="plan-version">{t('planVersion')} v{plan.version}</span>
      </div>
      <div className="plan-panel-markdown">
        <MarkdownRenderer content={markdown} />
      </div>

      {linkedItems.length > 0 ? (
        <div className="plan-change-summary">
          <strong>{t('planChanges', '计划变更')}</strong>
          <span>{linkedItems.length} {t('files', '文件')}</span>
          <span>{accepted} {t('accepted', '已接受')}</span>
          <span>{pending} {t('pendingReview', '待审阅')}</span>
          {reverted ? <span>{reverted} {t('reverted', '已撤销')}</span> : null}
        </div>
      ) : null}

      <div className="plan-meta">
        <div>{t('planStatus')}: {t(`planStatus_${plan.status}`)}</div>
        <div>{t('planVersion')}: v{plan.version}</div>
        {showExecution ? (
          <div>{t('planExecutionState')}: {t(`planExecution_${plan.execution_state}`)}</div>
        ) : null}
        {showExecution && plan.execution_note ? <div>{t('planExecutionNote')}: {plan.execution_note}</div> : null}
        {plan.created_at ? <div>{t('planCreatedAt')}: {plan.created_at}</div> : null}
        {plan.decided_at ? <div>{t('planDecidedAt')}: {plan.decided_at}</div> : null}
        {plan.executed_run_id ? <div>{t('planExecutedRun')}: {plan.executed_run_id}</div> : null}
        {plan.file_path ? <div>{t('planFile', '计划文件')}: {plan.file_path}</div> : null}
      </div>
    </div>
  );
}
