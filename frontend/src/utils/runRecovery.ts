// Recovery R1: per-session active-run bookkeeping.
//
// A chat request is a persisted run. This module keeps the run id and the last
// applied SSE sequence for each session in memory and in localStorage, so a
// refresh can reattach with `GET /api/runs/{id}/events?after=<seq>`.
// It contains no UI state; the chat store owns the run event state machine.

import { apiGet, getAuthHeaders, type SSEEventMeta } from "../api/client";
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

/**
 * Ask the backend which run is active for a session.
 *
 * Contract: `GET /api/sessions/{session_id}/runs/active`. The response may be
 * an envelope (`{success, data}` is already unwrapped by `apiGet`), a
 * `{run}`/`{active}` wrapper, a raw run object, or empty when no run is active.
 * An older backend without the route simply returns null; callers fall back to
 * the locally tracked run id and `GET /api/runs/{id}`.
 */
export async function fetchActiveRun(sessionId: string): Promise<RunStateResponse | null> {
  if (!sessionId) return null;
  try {
    const raw = await apiGet<unknown>(`sessions/${encodeURIComponent(sessionId)}/runs/active`);
    return normalizeRunStatePayload(raw);
  } catch {
    return null;
  }
}

function _isRunLike(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.status === "string"
    || typeof candidate.id === "string"
    || typeof candidate.run_id === "string";
}

/**
 * Normalize the many acceptable active-run payload shapes into one run state.
 * Unknown/empty payloads return null instead of fabricating a run.
 */
export function normalizeRunStatePayload(raw: unknown): RunStateResponse | null {
  if (!raw || typeof raw !== "object") return null;
  const root = raw as Record<string, unknown>;
  const data = root.data && typeof root.data === "object" && !Array.isArray(root.data)
    ? root.data as Record<string, unknown>
    : null;

  const candidates: unknown[] = [
    root.run,
    data?.run,
    root.active,
    data?.active,
    root.active_run,
    data?.active_run,
  ];
  for (const list of [root.runs, data?.runs]) {
    if (Array.isArray(list)) candidates.push(...list);
  }
  candidates.push(root);
  if (data) candidates.push(data);

  for (const candidate of candidates) {
    if (!_isRunLike(candidate)) continue;
    return _normalizeRunState(candidate);
  }
  return null;
}

function _normalizeRunState(value: Record<string, unknown>): RunStateResponse {
  const status = _isRunStatus(value.status) ? value.status : "running";
  return {
    ...(typeof value.id === "string" ? { id: value.id } : {}),
    ...(typeof value.run_id === "string" ? { run_id: value.run_id } : {}),
    ...(typeof value.session_id === "string" ? { session_id: value.session_id } : {}),
    status,
    ...(typeof value.last_event_seq === "number" && value.last_event_seq >= 0
      ? { last_event_seq: Math.trunc(value.last_event_seq) }
      : {}),
    ...(typeof value.error_code === "string" || value.error_code === null
      ? { error_code: value.error_code as string | null }
      : {}),
    ...(typeof value.error_message === "string" || value.error_message === null
      ? { error_message: value.error_message as string | null }
      : {}),
    ...(Array.isArray(value.pending_approvals)
      ? { pending_approvals: value.pending_approvals as PendingApprovalRecord[] }
      : {}),
    ...(typeof value.started_at === "string" || value.started_at === null
      ? { started_at: value.started_at as string | null }
      : {}),
    ...(typeof value.finished_at === "string" || value.finished_at === null
      ? { finished_at: value.finished_at as string | null }
      : {}),
    ...(typeof value.checkpoint_id === "string" || value.checkpoint_id === null
      ? { checkpoint_id: value.checkpoint_id as string | null }
      : {}),
    ...(typeof value.resumable === "boolean" ? { resumable: value.resumable } : {}),
    ...(typeof value.result_unknown === "boolean" ? { result_unknown: value.result_unknown } : {}),
    ...(typeof value.last_tool === "string" || value.last_tool === null
      ? { last_tool: value.last_tool as string | null }
      : {}),
  };
}

/** Stable run id from either backend naming convention. */
export function runStateRunId(state: RunStateResponse | null | undefined): string | null {
  const id = state?.run_id ?? state?.id;
  return typeof id === "string" && id ? id : null;
}

function _runActionError(code: string, message: string): Error & { code: string } {
  const error = new Error(message) as Error & { code: string };
  error.code = code;
  return error;
}

/**
 * POST a run lifecycle action and surface the backend's error envelope.
 *
 * `apiPost` only unwraps top-level `error`, while FastAPI wraps HTTPException
 * bodies as `{detail: {success: false, error: {code, message}}}`. Run actions
 * need those codes (`RUN_NOT_ACTIVE`, `RESUME_UNSUPPORTED`) for accurate UI.
 */
async function postRunAction(path: string): Promise<unknown> {
  const headers = typeof getAuthHeaders === "function" ? getAuthHeaders() : {};
  const res = await fetch(`/api/${path.replace(/^\//, "")}`, { method: "POST", headers });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // Non-JSON error/empty body; fall back to the HTTP status.
  }
  if (!res.ok) {
    const root = body && typeof body === "object" ? body as Record<string, unknown> : null;
    const detail = root?.detail && typeof root.detail === "object"
      ? root.detail as Record<string, unknown>
      : root;
    const envelope = detail?.error && typeof detail.error === "object"
      ? detail.error as Record<string, unknown>
      : detail;
    const code = typeof envelope?.code === "string" ? envelope.code : `HTTP_${res.status}`;
    const message = typeof envelope?.message === "string"
      ? envelope.message
      : `请求失败（HTTP ${res.status}）`;
    throw _runActionError(code, message);
  }
  const root = body && typeof body === "object" ? body as Record<string, unknown> : null;
  if (root && "success" in root) {
    if (root.success === true) return root.data ?? root;
    const envelope = root.error && typeof root.error === "object"
      ? root.error as Record<string, unknown>
      : {};
    throw _runActionError(
      typeof envelope.code === "string" ? envelope.code : "UNKNOWN",
      typeof envelope.message === "string" ? envelope.message : "请求失败",
    );
  }
  return body;
}

/** Request cancellation of an active run. */
export async function requestCancelRun(runId: string): Promise<void> {
  if (!runId) return;
  await postRunAction(`runs/${encodeURIComponent(runId)}/cancel`);
}

/** Request resuming an interrupted run from its checkpoint. */
export async function requestResumeRun(runId: string): Promise<void> {
  if (!runId) return;
  await postRunAction(`runs/${encodeURIComponent(runId)}/resume`);
}

const HARDWARE_TOOL_PATTERN = /serial|uart|flash|burn|upload|connect|gpio|pin|i2c|spi|pwm|adc|device/i;
const SIDE_EFFECT_TOOL_PATTERN = /write|edit|patch|delete|move|rename|execute|run|shell|command|flash|burn|upload|serial|connect/i;

export interface InterruptedRunAssessment {
  /** A tool may have run (or partially run) before the interruption. */
  resultUnknown: boolean;
  /** A hardware side effect may be unknown; the user must check the device. */
  hardwareUnknown: boolean;
  /** A checkpoint exists (or the backend says the run is resumable). */
  resumable: boolean;
  /** User-facing guidance; never claims the run succeeded. */
  message: string;
}

/**
 * Decide how to present an interrupted run. Uses backend flags when present and
 * falls back to the last unfinished tool call, because a pending hardware tool
 * at interruption time may have already changed the device.
 */
export function assessInterruptedRun(
  state: RunStateResponse | null | undefined,
  lastStep?: { name?: string; status?: string } | null,
): InterruptedRunAssessment {
  const errorCode = state?.error_code ?? "";
  const backendUnknown = state?.result_unknown === true || /UNKNOWN/i.test(errorCode);
  const stepPending = lastStep?.status === "pending" || lastStep?.status === "running";
  const stepName = typeof lastStep?.name === "string" ? lastStep.name : (state?.last_tool ?? "");
  const stepUnknown = stepPending && SIDE_EFFECT_TOOL_PATTERN.test(stepName);
  const resultUnknown = backendUnknown || stepUnknown;
  const hardwareUnknown = resultUnknown && HARDWARE_TOOL_PATTERN.test(stepName);
  const resumable = state?.resumable === true || Boolean(state?.checkpoint_id);

  let message: string;
  if (hardwareUnknown) {
    message = "运行已中断，硬件操作结果未知，请核对设备状态后再继续。";
  } else if (resultUnknown) {
    message = "运行已中断，部分工具结果未知，请核对结果后再继续。";
  } else if (resumable) {
    message = "运行已中断，可从检查点继续。";
  } else {
    message = "运行已中断，无法自动继续；请重新发送请求或重试待确认操作。";
  }
  return { resultUnknown, hardwareUnknown, resumable, message };
}

export function isTerminalRunStatus(status: RunStatus | undefined): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}
