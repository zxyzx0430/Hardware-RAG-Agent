import type { TodoItem } from "../types/session";
import type { Plan, PlanStatus, PlanStep } from "../types/plan";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** Normalize wire todos (never trust ids/status from the model or legacy storage). */
export function normalizeTodos(raw: unknown): TodoItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item, index) => {
    const record = asRecord(item) ?? {};
    const status = record.status === "in_progress" || record.status === "completed"
      ? record.status
      : "pending";
    const priority = record.priority === "high" || record.priority === "medium" || record.priority === "low"
      ? record.priority
      : undefined;
    return {
      id: typeof record.id === "string" && record.id ? record.id : `todo-${index}`,
      content: typeof record.content === "string" ? record.content : "",
      status,
      ...(priority ? { priority } : {}),
    } as TodoItem;
  });
}

const PLAN_STATUSES: PlanStatus[] = ["pending", "approved", "rejected", "superseded"];
const PLAN_EXECUTION_STATES = ["none", "started", "completed", "rejected", "failed"] as const;
type PlanExecutionState = (typeof PLAN_EXECUTION_STATES)[number];

function normalizeSteps(raw: unknown): PlanStep[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const record = asRecord(item);
    if (!record || typeof record.title !== "string") return [];
    return [{
      title: record.title,
      ...(typeof record.detail === "string" ? { detail: record.detail } : {}),
      ...(typeof record.verification === "string" ? { verification: record.verification } : {}),
    }];
  });
}

function normalizeStringList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === "string");
}

/** Normalize a plan payload; returns null when it cannot be identified as a plan. */
export function normalizePlan(raw: unknown): Plan | null {
  const record = asRecord(raw);
  if (!record || typeof record.id !== "string" || !record.id) return null;
  const status = PLAN_STATUSES.includes(record.status as PlanStatus)
    ? (record.status as PlanStatus)
    : "pending";
  const executionState = PLAN_EXECUTION_STATES.includes(record.execution_state as PlanExecutionState)
    ? (record.execution_state as PlanExecutionState)
    : undefined;
  return {
    id: record.id,
    session_id: typeof record.session_id === "string" ? record.session_id : "",
    version: typeof record.version === "number" && Number.isFinite(record.version) ? record.version : 1,
    status,
    title: typeof record.title === "string" ? record.title : "",
    summary: typeof record.summary === "string" ? record.summary : "",
    steps: normalizeSteps(record.steps),
    risks: normalizeStringList(record.risks),
    open_questions: normalizeStringList(record.open_questions),
    run_id: typeof record.run_id === "string" ? record.run_id : null,
    executed_run_id: typeof record.executed_run_id === "string" ? record.executed_run_id : null,
    ...(executionState ? { execution_state: executionState } : {}),
    ...(typeof record.execution_note === "string" && record.execution_note
      ? { execution_note: record.execution_note }
      : {}),
    ...(typeof record.file_path === "string" && record.file_path
      ? { file_path: record.file_path }
      : {}),
    created_at: typeof record.created_at === "string" ? record.created_at : "",
    decided_at: typeof record.decided_at === "string" ? record.decided_at : null,
  };
}

/** Unwrap `{plan: Plan|null}` or a bare plan payload. */
export function planFromEnvelope(raw: unknown): Plan | null {
  const record = asRecord(raw);
  if (record && "plan" in record) {
    return record.plan == null ? null : normalizePlan(record.plan);
  }
  return normalizePlan(raw);
}
