// Recovery R1: per-session active-run bookkeeping.
//
// A chat request is a persisted run. This module keeps the run id and the last
// applied SSE sequence for each session in memory and in localStorage, so a
// refresh can reattach with `GET /api/runs/{id}/events?after=<seq>`.
// It contains no UI state; the chat store owns the run event state machine.

import { apiGet, type SSEEventMeta } from "../api/client";
import type { ChatSSEEvent, PendingApprovalRecord, RunStateResponse, RunStatus } from "../types/api";

export const RUN_RECORDS_STORAGE_KEY = "hwrag_active_runs_v1";
const SEQ_PERSIST_DEBOUNCE_MS = 250;

export interface ActiveRunRecord {
  runId: string;
  sessionId: string;
  lastEventSeq: number;
  status: RunStatus;
  updatedAt: number;
}

export interface RunEventInfo {
  runId?: string;
  seq?: number;
}

export interface PendingApprovalCall {
  name: string;
  args: Record<string, unknown>;
  call_id: string;
  risk_level: "low" | "medium" | "high";
}

let _records: Record<string, ActiveRunRecord> | null = null;
let _persistTimer: ReturnType<typeof setTimeout> | null = null;

function _storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function _isRunStatus(value: unknown): value is RunStatus {
  return value === "running"
    || value === "awaiting_confirmation"
    || value === "completed"
    || value === "failed"
    || value === "cancelled"
    || value === "interrupted";
}

function _parseRecords(raw: string | null): Record<string, ActiveRunRecord> {
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};

  const records: Record<string, ActiveRunRecord> = {};
  for (const [sessionId, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!value || typeof value !== "object") continue;
    const candidate = value as Partial<ActiveRunRecord>;
    if (typeof candidate.runId !== "string" || !candidate.runId) continue;
    records[sessionId] = {
      runId: candidate.runId,
      sessionId,
      lastEventSeq: typeof candidate.lastEventSeq === "number" && candidate.lastEventSeq >= 0
        ? Math.trunc(candidate.lastEventSeq)
        : 0,
      status: _isRunStatus(candidate.status) ? candidate.status : "running",
      updatedAt: typeof candidate.updatedAt === "number" ? candidate.updatedAt : 0,
    };
  }
  return records;
}

function _persistNow(): void {
  if (_persistTimer) {
    clearTimeout(_persistTimer);
    _persistTimer = null;
  }
  const storage = _storage();
  if (!storage || !_records) return;
  try {
    if (Object.keys(_records).length === 0) storage.removeItem(RUN_RECORDS_STORAGE_KEY);
    else storage.setItem(RUN_RECORDS_STORAGE_KEY, JSON.stringify(_records));
  } catch {
    // Recovery is best effort; the backend run log remains authoritative.
  }
}

function _schedulePersist(): void {
  if (_persistTimer) return;
  _persistTimer = setTimeout(() => {
    _persistTimer = null;
    _persistNow();
  }, SEQ_PERSIST_DEBOUNCE_MS);
}

/** Read the persisted records (cached after the first call). */
export function loadRunRecords(): Record<string, ActiveRunRecord> {
  if (_records) return _records;
  const storage = _storage();
  _records = storage ? _parseRecords(storage.getItem(RUN_RECORDS_STORAGE_KEY)) : {};
  return _records;
}

/** Write any debounced sequence updates immediately. */
export function flushRunRecords(): void {
  _persistNow();
}

export function getActiveRun(sessionId: string): ActiveRunRecord | undefined {
  if (!sessionId) return undefined;
  return loadRunRecords()[sessionId];
}

/** Start tracking a new run for a session, replacing any stale record. */
export function startActiveRun(sessionId: string, runId: string, status: RunStatus = "running"): ActiveRunRecord {
  const records = loadRunRecords();
  const record: ActiveRunRecord = {
    runId,
    sessionId,
    lastEventSeq: 0,
    status,
    updatedAt: Date.now(),
  };
  records[sessionId] = record;
  _persistNow();
  return record;
}

/**
 * Record an applied sequence. Returns null when the event belongs to a
 * different (stale) run than the one tracked for this session.
 */
export function updateRunSeq(
  sessionId: string,
  runId: string,
  seq: number,
  status?: RunStatus,
): ActiveRunRecord | null {
  const records = loadRunRecords();
  const existing = records[sessionId];
  if (existing && existing.runId !== runId) return null;
  const record: ActiveRunRecord = existing ?? {
    runId,
    sessionId,
    lastEventSeq: 0,
    status: "running",
    updatedAt: Date.now(),
  };
  record.lastEventSeq = Math.max(record.lastEventSeq, Math.trunc(seq));
  if (status) record.status = status;
  record.updatedAt = Date.now();
  records[sessionId] = record;
  _schedulePersist();
  return record;
}

/** Update the run status without touching the sequence. */
export function setRunStatus(sessionId: string, runId: string, status: RunStatus): ActiveRunRecord | null {
  const records = loadRunRecords();
  const existing = records[sessionId];
  if (!existing || existing.runId !== runId) return null;
  existing.status = status;
  existing.updatedAt = Date.now();
  _persistNow();
  return existing;
}

/** Drop the run record (terminal done, explicit stop, or session deleted). */
export function clearActiveRun(sessionId: string): void {
  const records = loadRunRecords();
  if (!(sessionId in records)) return;
  delete records[sessionId];
  _persistNow();
}

/** Extract run id / sequence from a persisted SSE event plus its id metadata. */
export function parseRunEventMeta(
  event: ChatSSEEvent,
  meta?: SSEEventMeta,
): RunEventInfo {
  const payload = event as { run_id?: unknown; seq?: unknown };
  const runId = typeof payload.run_id === "string" && payload.run_id
    ? payload.run_id
    : meta?.runId;
  const seq = typeof payload.seq === "number"
    ? payload.seq
    : meta?.seq;
  return { runId, seq };
}

/**
 * Map persisted pending approvals to ConfirmDialog calls.
 * Only `pending` (and unexpired) rows are returned; nothing is fabricated.
 */
export function pendingApprovalsToCalls(
  approvals: PendingApprovalRecord[] | undefined,
): PendingApprovalCall[] {
  const now = Date.now();
  return (approvals ?? [])
    .filter((approval) => {
      if (!approval || approval.status !== "pending") return false;
      if (!approval.expires_at) return true;
      const expires = Date.parse(approval.expires_at);
      return Number.isNaN(expires) || expires > now;
    })
    .map((approval) => ({
      name: approval.tool_name,
      args: _parseArgs(approval.args),
      call_id: approval.call_id,
      risk_level: approval.risk_level === "low" || approval.risk_level === "high"
        ? approval.risk_level
        : "medium",
    }));
}

function _parseArgs(args: PendingApprovalRecord["args"]): Record<string, unknown> {
  if (args && typeof args === "object" && !Array.isArray(args)) return args;
  if (typeof args === "string") {
    try {
      const parsed: unknown = JSON.parse(args);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Fall through to a wrapped value so the tool name stays actionable.
    }
    return { raw: args };
  }
  return {};
}

/** Read run status from the backend; null when unavailable (older backend). */
export async function fetchRunState(runId: string): Promise<RunStateResponse | null> {
  if (!runId) return null;
  try {
    const data = await apiGet<RunStateResponse>(`runs/${encodeURIComponent(runId)}`);
    if (!data || typeof data !== "object") return null;
    return data;
  } catch {
    return null;
  }
}

export function isTerminalRunStatus(status: RunStatus | undefined): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}
