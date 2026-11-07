import { useChatStore } from "../../stores/useChatStore";
import { useI18n } from "../../i18n";

/** Read-only plan view rendered inside the right panel's "plan" tab. */
export function PlanPanel() {
  const { t } = useI18n();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const plan = useChatStore((s) => s.sessionPlans[s.activeSessionId] ?? null);

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
      <h2 className="plan-panel-title">{plan.title}</h2>
      {plan.summary ? <p className="plan-panel-summary">{plan.summary}</p> : null}

      {plan.steps.length > 0 ? (
        <section className="plan-section">
          <h3 className="plan-section-title">{t('planSteps')}</h3>
          <ol className="plan-steps">
            {plan.steps.map((step, index) => (
              <li className="plan-step" key={`${index}-${step.title}`}>
                <span className="plan-step-number">{String(index + 1).padStart(2, '0')}</span>
                <div className="plan-step-body">
                  <div className="plan-step-title">{step.title}</div>
                  {step.detail ? <div className="plan-step-detail">{step.detail}</div> : null}
                  {step.verification ? (
                    <div className="plan-step-verification">
                      <span className="plan-step-verification-label">{t('planStepVerification')}</span>
                      {step.verification}
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {plan.risks.length > 0 ? (
        <section className="plan-section">
          <h3 className="plan-section-title">{t('planRisks')}</h3>
          <ul className="plan-list">
            {plan.risks.map((risk, index) => <li key={`risk-${index}`}>{risk}</li>)}
          </ul>
        </section>
      ) : null}

      {plan.open_questions.length > 0 ? (
        <section className="plan-section">
          <h3 className="plan-section-title">{t('planOpenQuestions')}</h3>
          <ul className="plan-list">
            {plan.open_questions.map((question, index) => <li key={`question-${index}`}>{question}</li>)}
          </ul>
        </section>
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
