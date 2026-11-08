import { useEffect, useState } from "react";
import { useChatStore } from "../../stores/useChatStore";
import type { RunStatus } from "../../types/api";
import type { ActivityStep } from "../../types/session";
import { useI18n } from "../../i18n";
import { formatDuration } from "../../utils/format";

const EMPTY_STEPS: ActivityStep[] = [];

const STATUS_LABEL_KEYS: Record<RunStatus, string> = {
  running: "runStatusRunning",
  awaiting_confirmation: "runStatusAwaiting",
  completed: "runStatusCompleted",
  failed: "runStatusFailed",
  cancelled: "runStatusCancelled",
  interrupted: "runStatusInterrupted",
};

const EVENT_LABEL_KEYS: Record<string, string> = {
  run_started: "runEventRunStarted",
  thinking: "runEventThinking",
  text: "runEventText",
  tool_call: "runEventToolCall",
  tool_result: "runEventToolResult",
  approval: "runEventApproval",
  source: "runEventSource",
  error: "runEventError",
  done: "runEventDone",
  artifact: "runEventArtifact",
  todo: "runEventTodo",
  plan: "runEventPlan",
  permission: "runEventPermission",
  context: "runEventContext",
  progress: "runEventProgress",
};

function formatClock(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export interface RunStatusBarProps {
  sessionId: string;
  /** Open an existing source panel entry from a timeline row. */
  onOpenSource: (sourceId: string, messageId?: string) => void;
}

/**
 * Compact lifecycle bar for the run that owns the active session.
 *
 * It is not a second conversation: the header shows state, run id, the current
 * tool, the last event and elapsed time, and the optional body is a short,
 * deduplicated event timeline. Cancel/resume are the only lifecycle actions.
 */
export function RunStatusBar({ sessionId, onOpenSource }: RunStatusBarProps) {
  const { t } = useI18n();
  const view = useChatStore((s) => s.runViewBySession?.[sessionId]);
  const notice = useChatStore((s) => s.runRecoveryNoticeBySession?.[sessionId]);
  const timeline = useChatStore((s) => s.runTimelineBySession?.[sessionId]);
  const steps = useChatStore((s) => {
    if (s.activeSessionId !== sessionId) return EMPTY_STEPS;
    if (s.streamingSteps?.length > 0) return s.streamingSteps;
    return s.messages?.at(-1)?.activity?.steps ?? EMPTY_STEPS;
  });
  const cancelActiveRun = useChatStore((s) => s.cancelActiveRun);
  const resumeInterruptedRun = useChatStore((s) => s.resumeInterruptedRun);
  const dismissRunView = useChatStore((s) => s.dismissRunView);

  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const status: RunStatus = view?.status ?? notice?.status ?? "running";
  const isLive = status === "running" || status === "awaiting_confirmation";
  const isRestoring = view?.phase === "restoring";
  const isTerminal = view?.phase === "terminal" || Boolean(notice);
  const resultUnknown = view?.resultUnknown ?? notice?.resultUnknown ?? false;
  const hardwareUnknown = view?.hardwareUnknown ?? notice?.hardwareUnknown ?? false;
  const resumable = view?.resumable ?? notice?.resumable ?? false;
  const actionPending = view?.actionPending ?? false;
  const pendingApprovals = view?.pendingApprovals ?? 0;

  useEffect(() => {
    if (!isLive) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isLive]);

  if (!view && !notice) return null;

  const runId = view?.runId ?? notice?.runId ?? "";
  const toolSteps = steps.filter((step) => step.type === "tool");
  const currentTool = view?.currentTool
    ?? toolSteps.at(-1)?.name
    ?? null;
  const stepIndex = view?.currentStepIndex ?? toolSteps.length;
  const startedAt = view?.startedAt ?? null;
  const finishedAt = view?.finishedAt ?? null;
  const elapsed = startedAt !== null
    ? Math.max(0, (finishedAt ?? now) - startedAt)
    : null;
  const lastEventType = view?.lastEventType ?? null;
  const lastEventAt = view?.lastEventAt ?? null;
  const lastEventLabel = lastEventType
    ? t(EVENT_LABEL_KEYS[lastEventType] ?? "runEventUnknown", t("runEventUnknown"))
    : null;

  const canCancel = isLive && !actionPending;
  const canResume = status === "interrupted" && resumable && !resultUnknown && !actionPending;
  const showDismiss = isTerminal && !isLive;

  return (
    <section
      className={`run-status-bar status-${status}${isRestoring ? " restoring" : ""}`}
      aria-label={t("runPanelTitle", "Run status")}
      aria-live="polite"
      data-testid="run-status-bar"
    >
      <div className="run-status-header">
        <button
          type="button"
          className="run-status-toggle"
          aria-expanded={expanded}
          aria-label={t("runTimeline", "Event timeline")}
          title={t("runTimeline", "Event timeline")}
          onClick={() => setExpanded((value) => !value)}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <polyline points={expanded ? "6 9 12 15 18 9" : "9 18 15 12 9 6"} />
          </svg>
          <span className={`run-status-dot status-${status}`} aria-hidden="true" />
          <span className="run-status-label">{t(STATUS_LABEL_KEYS[status], status)}</span>
        </button>

        {runId ? (
          <span className="run-status-runid" title={runId}>
            {t("runIdLabel", "Run")} {runId.length > 14 ? `${runId.slice(0, 14)}…` : runId}
          </span>
        ) : null}

        {currentTool ? (
          <span className="run-status-tool" title={currentTool}>
            {t("runCurrentToolLabel", "Current tool")}: {currentTool}
            {stepIndex > 0 ? ` · ${t("runStepLabel", "Step")} ${stepIndex}` : ""}
          </span>
        ) : null}

        {lastEventLabel ? (
          <span className="run-status-last" title={lastEventAt ? formatClock(lastEventAt) : undefined}>
            {t("runLastEventLabel", "Last event")}: {lastEventLabel}
            {lastEventAt ? ` · ${formatClock(lastEventAt)}` : ""}
          </span>
        ) : null}

        {elapsed !== null ? (
          <span className="run-status-elapsed">
            {t("runElapsedLabel", "Elapsed")}: {formatDuration(elapsed)}
          </span>
        ) : null}

        <span className="run-status-actions">
          {canCancel ? (
            <button
              type="button"
              className="run-status-action danger"
              onClick={() => { void cancelActiveRun(sessionId); }}
              disabled={actionPending}
            >
              {actionPending ? t("runCancelling", "Cancelling…") : t("runCancel", "Cancel run")}
            </button>
          ) : null}
          {canResume ? (
            <button
              type="button"
              className="run-status-action primary"
              onClick={() => { void resumeInterruptedRun(sessionId); }}
              disabled={actionPending}
            >
              {actionPending ? t("runResuming", "Resuming…") : t("runResume", "Resume from checkpoint")}
            </button>
          ) : null}
          {showDismiss ? (
            <button
              type="button"
              className="run-status-dismiss"
              aria-label={t("runDismiss", "Dismiss run status")}
              title={t("runDismiss", "Dismiss run status")}
              onClick={() => dismissRunView(sessionId)}
            >
              ×
            </button>
          ) : null}
        </span>
      </div>

      {isRestoring ? (
        <div className="run-status-notice restoring" role="status">
          <span className="activity-spinner" aria-hidden="true" />
          {t("runRestoring", "Restoring this run…")}
        </div>
      ) : null}

      {resultUnknown ? (
        <div className="run-status-notice unknown" role="alert">
          {hardwareUnknown
            ? t("runResultUnknown", "Result unknown — check the device state")
            : t("runResultUnknownGeneric", "Result unknown — verify the outcome")}
          <span className="run-status-notice-detail">{t("runUnknownHint", "Some actions have unknown outcomes; verify them before continuing.")}</span>
        </div>
      ) : null}

      {status === "interrupted" && !resultUnknown ? (
        <div className="run-status-notice interrupted" role="status">
          {view?.errorMessage ?? notice?.message ?? t("runStatusInterrupted", "Interrupted")}
        </div>
      ) : null}

      {status === "awaiting_confirmation" ? (
        <div className="run-status-notice approval" role="status">
          {pendingApprovals > 0 ? `${pendingApprovals} ${t("runPendingApprovals", "pending approval(s)")}` : null}
          {pendingApprovals > 0 ? " · " : null}
          {t("runApprovalHint", "The agent requested tool actions; confirm them in the dialog.")}
        </div>
      ) : null}

      {expanded ? (
        <div className="run-status-timeline" role="region" aria-label={t("runTimeline", "Event timeline")}>
          {timeline && timeline.length > 0 ? (
            <ol className="run-timeline-list">
              {timeline.slice(-20).map((entry) => {
                const label = t(EVENT_LABEL_KEYS[entry.type] ?? "runEventUnknown", t("runEventUnknown"));
                const detail = entry.type === "source"
                  ? entry.sourceTitle ?? entry.sourceId ?? ""
                  : entry.type === "tool_call" || entry.type === "tool_result"
                    ? entry.tool ?? ""
                    : entry.type === "approval"
                      ? `${entry.message ?? "1"} ${t("runPendingApprovals", "pending approval(s)")}`
                      : entry.type === "done"
                        ? t(STATUS_LABEL_KEYS[(entry.status as RunStatus) ?? "completed"] ?? "runEventDone", entry.status ?? "")
                        : entry.message ?? "";
                return (
                  <li key={`${entry.seq}-${entry.type}`} className={`run-timeline-row type-${entry.type}${entry.status ? ` status-${entry.status}` : ""}`}>
                    <span className="run-timeline-time">{formatClock(entry.at)}</span>
                    <span className="run-timeline-label">{label}</span>
                    {entry.type === "source" && entry.sourceId ? (
                      <button
                        type="button"
                        className="run-timeline-source"
                        onClick={() => onOpenSource(entry.sourceId!, entry.messageId)}
                        title={detail}
                      >
                        {detail}
                      </button>
                    ) : (
                      <span className="run-timeline-detail" title={detail}>{detail}</span>
                    )}
                  </li>
                );
              })}
            </ol>
          ) : (
            <div className="run-timeline-empty">{t("runTimelineEmpty", "No events yet")}</div>
          )}
        </div>
      ) : null}
    </section>
  );
}

export default RunStatusBar;
