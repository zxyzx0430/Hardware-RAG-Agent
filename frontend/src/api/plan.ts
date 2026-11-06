import { apiGet, apiPost } from "./client";
import type { Plan, PlanMeta } from "../types/plan";
import { normalizePlan, planFromEnvelope } from "../utils/planTodo";

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** GET /api/sessions/{id}/plans/latest — newest plan for the session, or null. */
export async function fetchLatestPlan(sessionId: string): Promise<Plan | null> {
  const raw = await apiGet<unknown>(`sessions/${encodeURIComponent(sessionId)}/plans/latest`);
  return planFromEnvelope(raw);
}

/** GET /api/sessions/{id}/plans?limit=N — metadata-only plan history. */
export async function listSessionPlans(sessionId: string, limit = 20): Promise<PlanMeta[]> {
  const raw = await apiGet<unknown>(
    `sessions/${encodeURIComponent(sessionId)}/plans?limit=${encodeURIComponent(String(limit))}`,
  );
  const record = asRecord(raw);
  const rows = Array.isArray(raw) ? raw : (record && Array.isArray(record.plans) ? record.plans : []);
  return rows.flatMap((row) => {
    const plan = normalizePlan(row);
    if (!plan) return [];
    return [{
      id: plan.id,
      version: plan.version,
      status: plan.status,
      title: plan.title,
      created_at: plan.created_at,
      decided_at: plan.decided_at,
      executed_run_id: plan.executed_run_id,
    }];
  });
}

async function decidePlan(
  sessionId: string,
  planId: string,
  action: "approve" | "reject",
): Promise<Plan> {
  const raw = await apiPost<unknown>(
    `sessions/${encodeURIComponent(sessionId)}/plans/${encodeURIComponent(planId)}/${action}`,
  );
  const plan = planFromEnvelope(raw);
  if (!plan) throw new Error(`计划${action === "approve" ? "批准" : "拒绝"}响应无效`);
  return plan;
}

/** POST /api/sessions/{id}/plans/{plan_id}/approve */
export async function approveSessionPlan(sessionId: string, planId: string): Promise<Plan> {
  return decidePlan(sessionId, planId, "approve");
}

/** POST /api/sessions/{id}/plans/{plan_id}/reject */
export async function rejectSessionPlan(sessionId: string, planId: string): Promise<Plan> {
  return decidePlan(sessionId, planId, "reject");
}
