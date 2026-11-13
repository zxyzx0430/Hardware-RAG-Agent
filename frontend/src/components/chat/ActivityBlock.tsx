import { useEffect, useState, memo, type ReactNode, type MouseEvent } from "react";
import type { ActivityBlock as ActivityBlockData, ActivityStep } from "../../types/session";
import { useI18n } from "../../i18n";
import { formatDuration } from "../../utils/format";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";
import RerankerStatusRow from "./RerankerStatusRow";
import { isSkillToolName, isValidSkillId, isValidSkillResourcePath, type SkillToolName } from "../../utils/skillTrace";

const ACTIVITY_TIMER_INTERVAL_MS = 10;
const TOOLSTEP_TIMER_INTERVAL_MS = 10;

const ActivityBlock = memo(function ActivityBlock({ activity, msgId, startTime }: { activity: ActivityBlockData; msgId: string; startTime?: number }) {
  const { t } = useI18n();
  const toolStepCount = activity.steps.filter((step) => step.type === 'tool').length;
  // Reranker state is process-wide; the latest search_docs result is enough.
  // Render it outside the collapsed body so it stays visible after completion.
  const rerankerStatus = activity.steps
    .slice()
    .reverse()
    .find((step) => step.name === 'search_docs' && step.reranker)?.reranker;
  const isRunning = activity.status === 'running';
  // activityDone: activity 已结束（done/error/等），活跃流式期间为 false。
  // 用于阻止 pending step 在历史消息/已结束 activity 上启动定时器。
  const activityDone = activity.status !== 'running';
  // Expand by default while streaming so the user immediately sees RAG/thinking;
  // collapse once finished.
  const [collapsed, setCollapsed] = useState(!isRunning);

  // Live timer: use startTime while running, durationMs once done.
  // Option A (heartbeat Task 20): drive "已运行 X 秒" via streamingStartTime +
  // setInterval — UI updates independently of heartbeat event arrival.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    // Sync immediately on mount / when startTime changes so the header shows the
    // correct elapsed value without waiting for the first interval tick.
    setElapsed(startTime ? Date.now() - startTime : 0);
    const timer = setInterval(() => {
      setElapsed(Date.now() - (startTime || Date.now()));
    }, ACTIVITY_TIMER_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [isRunning, startTime]);

  const stepDurationMs = activity.steps.reduce(
    (total, step) => total + (step.duration || 0),
    0,
  );
  const measuredDuration = activity.durationMs || stepDurationMs;
  const displayDuration = isRunning ? elapsed : measuredDuration;

  return (
    <div className={`activity-block${isRunning ? ' running' : ''}`} id={`act-${msgId}`}>
      <button className={`activity-header${collapsed ? ' collapsed' : ''}`} onClick={() => setCollapsed((v) => !v)}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="activity-chevron"><polyline points="6 9 12 15 18 9"/></svg>
        <span className="activity-header-label">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="svg-accent" style={{ flexShrink: 0, marginRight: 4 }}><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          {toolStepCount === 0 && isRunning ? '...' : `${t('activityLabel')}: ${toolStepCount} ${t('tools')}`}
        </span>
        <span className="activity-header-duration">
          {isRunning ? <span className="activity-spinner" /> : null}
          {isRunning ? '' : (measuredDuration > 0 ? t('doneIn') + ' ' : '')}{measuredDuration > 0 ? formatDuration(displayDuration) : '—'}
        </span>
      </button>
      {rerankerStatus ? <RerankerStatusRow status={rerankerStatus} /> : null}
      {!collapsed ? (
        <div className="activity-steps" id={`act-body-${msgId}`}>
          <div className="activity-chain">
            {activity.steps
              .filter((step) => !(step.type === 'thinking' && step.source !== 'reasoning'))
              .map((step, idx, arr) => {
                const isLast = idx === arr.length - 1;
                const stepKey = `${step.source ?? step.type}-${idx}`;
              if (step.type === 'thinking') {
                return (
                  <div className="activity-chain-node" key={stepKey}>
                    <ThinkingStep step={step} streaming={isRunning} />
                    {!isLast ? <div className="activity-chain-line" /> : null}
                  </div>
                );
              }
              return (
                <div className="activity-chain-node" key={stepKey}>
                  {isSkillToolName(step.name)
                    ? <SkillTraceStep step={step} toolName={step.name} running={isRunning} />
                    : <ToolStep step={step} running={isRunning} activityDone={activityDone} />}
                  {!isLast ? <div className="activity-chain-line" /> : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
});

export default ActivityBlock;

const ThinkingStep = memo(function ThinkingStep({ step, streaming }: { step: ActivityStep; streaming: boolean }) {
  const isReasoning = step.source === 'reasoning';
  const label = isReasoning ? 'thinking' : step.source === 'rag' ? '知识库检索' : '思考中';
  // Never auto-open; user must click to expand.
  const [open, setOpen] = useState(false);
  return (
    <div className={`thinking-row${isReasoning ? ' reasoning' : ''}`}>
      <button className="thinking-header" onClick={() => setOpen((v) => !v)}>
        <span className={`thinking-icon${isReasoning ? ' reasoning' : ''}`}>
          {isReasoning
            ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>
            : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
          }
        </span>
        <span className="thinking-header-label">
          <span className="thinking-label-tag">{label}</span>
          {!open && step.content ? step.content.slice(0, 40) + (step.content.length > 40 ? '…' : '') : ''}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="step-chevron" style={{ transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'none' }}><polyline points="9 18 15 12 9 6"/></svg>
      </button>
      {open ? (
        <div className="thinking-body">
          <MarkdownRenderer content={step.content || ''} streaming={streaming} />
        </div>
      ) : null}
    </div>
  );
});

type SkillTraceDisplay = {
  skillId: string;
  stage: "instructions" | "resource";
  contentHash?: string;
  path?: string;
  success?: boolean;
};

type SkillTraceStatus = "success" | "failed" | "running" | "interrupted" | "unknown";

const SKILL_CONTENT_HASH_PATTERN = /^[0-9a-f]{64}$/;
const INTERRUPTED_SKILL_STATUSES = new Set(["cancel", "cancelled", "canceled", "interrupted", "stopped", "aborted"]);

function getSkillTraceDisplay(step: ActivityStep, toolName: SkillToolName): SkillTraceDisplay | null {
  const value: unknown = step.skill_trace;
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;

  const trace = value as Record<string, unknown>;
  if (
    !Object.prototype.hasOwnProperty.call(trace, "skill_id")
    || !Object.prototype.hasOwnProperty.call(trace, "stage")
  ) return null;
  const expectedStage = toolName === "load_skill" ? "instructions" : "resource";
  if (!isValidSkillId(trace.skill_id) || trace.stage !== expectedStage) return null;

  const hasPath = Object.prototype.hasOwnProperty.call(trace, "path");
  let path: string | undefined;
  if (toolName === "read_skill_resource") {
    if (!hasPath || !isValidSkillResourcePath(trace.path)) return null;
    path = trace.path;
  } else if (hasPath) {
    return null;
  }

  const hasContentHash = Object.prototype.hasOwnProperty.call(trace, "content_hash");
  if (hasContentHash && (typeof trace.content_hash !== "string" || !SKILL_CONTENT_HASH_PATTERN.test(trace.content_hash))) {
    return null;
  }
  const hasSuccess = Object.prototype.hasOwnProperty.call(trace, "success");
  if (hasSuccess && typeof trace.success !== "boolean") return null;
  if (trace.error !== undefined && trace.error !== null) return null;

  return {
    skillId: trace.skill_id,
    stage: expectedStage,
    ...(hasContentHash ? { contentHash: trace.content_hash as string } : {}),
    ...(path !== undefined ? { path } : {}),
    ...(hasSuccess ? { success: trace.success as boolean } : {}),
  };
}

function getSkillTraceStatus(step: ActivityStep, running: boolean, trace: SkillTraceDisplay | null): SkillTraceStatus {
  const status = step.status || (running ? "running" : undefined);
  if (status === "pending" || status === "running") return "running";
  if (status && INTERRUPTED_SKILL_STATUSES.has(status)) return "interrupted";
  if (status === "error" && (!trace || trace.success === false)) return "failed";
  if (status === "done" && trace?.success === true && trace.contentHash) return "success";
  return "unknown";
}

function getSkillTraceStatusLabel(status: SkillTraceStatus): string {
  switch (status) {
    case "success": return "成功";
    case "failed": return "失败";
    case "running": return "进行中";
    case "interrupted": return "已中断";
    case "unknown": return "无法验证";
  }
}

const SkillTraceStep = memo(function SkillTraceStep({ step, toolName, running }: {
  step: ActivityStep;
  toolName: SkillToolName;
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const trace = getSkillTraceDisplay(step, toolName);
  const status = getSkillTraceStatus(step, running, trace);
  const statusLabel = getSkillTraceStatusLabel(status);
  const toolLabel = toolName === "load_skill" ? "加载技能" : "读取技能资源";

  return (
    <div className={`tool-row${status === "running" ? " running" : status === "failed" ? " error" : ""}`}>
      <button
        type="button"
        className="tool-header"
        aria-label={`${toolLabel}，${statusLabel}`}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="tool-icon" aria-hidden="true"><ToolIcon name={toolName} /></span>
        <span className="tool-name">{toolLabel}</span>
        <span className="tool-duration">{statusLabel}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="step-chevron" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      </button>
      {open ? (
        <div className="tool-body">
          {trace ? (
            <>
              <div className="tool-body-section">
                <span className="tool-body-label">技能 ID</span>
                <pre className="tool-body-code">{trace.skillId}</pre>
              </div>
              <div className="tool-body-section">
                <span className="tool-body-label">阶段</span>
                <pre className="tool-body-code">{trace.stage}</pre>
              </div>
              {trace.contentHash ? (
                <div className="tool-body-section">
                  <span className="tool-body-label">SHA-256</span>
                  <pre className="tool-body-code">{trace.contentHash}</pre>
                </div>
              ) : null}
              {trace.path ? (
                <div className="tool-body-section">
                  <span className="tool-body-label">资源路径</span>
                  <pre className="tool-body-code">{trace.path}</pre>
                </div>
              ) : null}
            </>
          ) : (
            <div className="tool-body-section">
              <span className="tool-body-code">没有可验证的技能轨迹信息。</span>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
});

const ToolStep = memo(function ToolStep({ step, running, activityDone }: { step: ActivityStep; running?: boolean; activityDone?: boolean }) {
  const [open, setOpen] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const { t } = useI18n();
  const setExplorerOpen = useAppStore((s) => s.setExplorerOpen);
  const setRightPanelOpen = useAppStore((s) => s.setRightPanelOpen);
  const setRightMode = useAppStore((s) => s.setRightMode);
  const setRightContentTab = useAppStore((s) => s.setRightContentTab);
  const openFile = useAppStore((s) => s.openFile);
  const openDiffForFile = useAppStore((s) => s.openDiffForFile);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const setSelectedChangeItem = useChatStore((s) => s.setSelectedChangeItem);
  const stepStatus = step.status || (running ? 'running' : 'done');
  // isPending 护栏：仅当 step.status === 'pending' 且 activity 仍未结束时才视为 pending。
  // activity 已 done 时，pending step 走静态分支（显示 step.duration || 0），不启动 setInterval。
  const isPending = step.status === 'pending' && !activityDone;
  useEffect(() => {
    if (!isPending || !step.startTime) return;
    setElapsed(Date.now() - step.startTime);
    const timer = setInterval(() => setElapsed(Date.now() - (step.startTime || Date.now())), TOOLSTEP_TIMER_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [isPending, step.startTime]);

  const hasDiff = !!step.file_path && stepStatus !== 'pending';
  const hasChange = !!step.change_item_id && stepStatus !== 'pending';
  const handleViewDiff = (e: MouseEvent) => {
    e.stopPropagation();
    if (!step.file_path) return;
    setExplorerOpen(true);
    openFile(step.file_path).then(() => openDiffForFile(step.file_path!));
  };
  const handleViewChange = (e: MouseEvent) => {
    e.stopPropagation();
    if (!step.change_item_id || !activeSessionId) return;
    setSelectedChangeItem(activeSessionId, step.change_item_id);
    setRightPanelOpen(true);
    setRightMode('content');
    setRightContentTab(activeSessionId, 'changes');
  };

  return (
    <div className={`tool-row${stepStatus === 'running' ? ' running' : stepStatus === 'error' ? ' error' : ''}${isPending ? ' pending' : ''}`}>
      <button className="tool-header" onClick={() => setOpen((v) => !v)}>
        <span className="tool-icon">
          <ToolIcon name={step.name} />
        </span>
        <span className="tool-name">{step.name || 'Tool'}</span>
        {step.args ? <span className="tool-args-preview">{typeof step.args === 'string' ? step.args.slice(0, 40) + (step.args.length > 40 ? '…' : '') : JSON.stringify(step.args).slice(0, 40) + '…'}</span> : null}
        {isPending ? (
          <>
            <span className="tool-spinner" />
            {step.startTime ? <span className="tool-duration">已耗时 {formatDuration(elapsed)}</span> : null}
          </>
        ) : (
          <span className="tool-duration">耗时 {formatDuration(step.duration || 0)}</span>
        )}
        {stepStatus === 'running' ? <span className="tool-spinner" /> : null}
        {step.risk_level ? (
          <span className={`risk-badge risk-${step.risk_level}`}>{step.risk_level.toUpperCase()}</span>
        ) : null}
        {step.decision_source ? (
          <span className="decision-source">{step.decision_source}</span>
        ) : null}
        {hasChange ? (
          <button className="tool-diff-btn" onClick={handleViewChange} title={t('changesTab', '变更')}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 7h16M4 12h16M4 17h10"/></svg>
            <span>{t('changesTab', '变更')}</span>
          </button>
        ) : null}
        {hasDiff ? (
          <button className="tool-diff-btn" onClick={handleViewDiff} title={t('viewDiff', '查看 diff')}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><rect x="8" y="8" width="8" height="8" rx="1"/></svg>
            <span>{t('viewDiff', '查看 diff')}</span>
          </button>
        ) : null}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="step-chevron" style={{ transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'none' }}><polyline points="9 18 15 12 9 6"/></svg>
      </button>
      {open ? (
        <div className="tool-body">
          {step.args ? (
            <div className="tool-body-section">
              <span className="tool-body-label">Input</span>
              <pre className="tool-body-code">{typeof step.args === 'string' ? step.args : JSON.stringify(step.args, null, 2)}</pre>
            </div>
          ) : null}
          {step.result ? (
            <div className="tool-body-section">
              <span className="tool-body-label">Output</span>
              <pre className="tool-body-code">{step.result}</pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});

// Tool icon mapping: keyword groups → icon. Add new tools by extending this map.
const TOOL_ICON_KEYWORDS: Array<{ key: string; keywords: string[] }> = [
  { key: "search", keywords: ["search", "query", "retriev"] },
  { key: "code", keywords: ["code", "compile", "flash", "build"] },
  { key: "document", keywords: ["datasheet", "doc", "pdf", "spec"] },
  { key: "serial", keywords: ["serial", "uart", "connect"] },
  { key: "signal", keywords: ["analyz", "debug", "signal", "oscilloscope"] },
];

const TOOL_ICONS: Record<string, ReactNode> = {
  search: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
  code: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>,
  document: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>,
  serial: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="7" width="20" height="10" rx="2"/><path d="M6 12h4M14 12h4"/></svg>,
  signal: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 12h4l3-9 4 18 3-9h6"/></svg>,
};

const DEFAULT_TOOL_ICON = <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>;

function ToolIcon({ name }: { name?: string }) {
  const n = (name || "").toLowerCase();
  for (const rule of TOOL_ICON_KEYWORDS) {
    if (rule.keywords.some((kw) => n.includes(kw))) {
      return TOOL_ICONS[rule.key] || DEFAULT_TOOL_ICON;
    }
  }
  return DEFAULT_TOOL_ICON;
}
