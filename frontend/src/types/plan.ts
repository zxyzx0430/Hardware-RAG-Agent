/** Plan mode data — server-authoritative, versioned plan proposals. */

export type PlanStatus = "pending" | "approved" | "rejected" | "superseded";

/** Optional backend-reported execution outcome of an approved plan. */
export type PlanExecutionState = "none" | "started" | "completed" | "rejected" | "failed";

export interface PlanStep {
  title: string;
  detail?: string;
  verification?: string;
}

/** Full plan record returned by plan endpoints and `plan_update` SSE events. */
export interface Plan {
  id: string;
  session_id: string;
  version: number;
  status: PlanStatus;
  title: string;
  summary: string;
  steps: PlanStep[];
  risks: string[];
  open_questions: string[];
  run_id?: string | null;
  executed_run_id?: string | null;
  /** Optional: absent on older backends; UI must fall back to approved/executed display. */
  execution_state?: PlanExecutionState;
  execution_note?: string | null;
  created_at: string;
  decided_at?: string | null;
}

/** Metadata-only row returned by `GET /api/sessions/{id}/plans`. */
export interface PlanMeta {
  id: string;
  version: number;
  status: PlanStatus;
  title: string;
  created_at: string;
  decided_at?: string | null;
  executed_run_id?: string | null;
}
