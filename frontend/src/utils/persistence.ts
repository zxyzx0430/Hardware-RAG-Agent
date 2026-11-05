import { projectSkillTracesInMessages } from "./skillTrace";
import type { AppendMessageV1Receipt } from "../types/api";

const KEYS = {
  settings: "hwrag_settings",
  sessions: "hwrag_sessions",
  messages: "hwrag_session_messages",
  bookmarks: "hwrag_bookmarks",
  bookmarkFolders: "hwrag_bookmark_folders",
  bookmarkData: "hwrag_bookmark_data",
  activeSession: "hwrag_active_session",
} as const;

const MSG_PREFIX = "hwrag_msg_";
const LEGACY_SESSION_KEY_BLOCKLIST = new Set(["__proto__", "constructor", "prototype"]);

export function loadFromStorage<T>(key: keyof typeof KEYS, fallback: T): T {
  try {
    const raw = localStorage.getItem(KEYS[key]);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function saveToStorage<T>(key: keyof typeof KEYS, data: T): void {
  try {
    localStorage.setItem(KEYS[key], JSON.stringify(data));
  } catch (e) {
    if (e instanceof DOMException && (e.name === "QuotaExceededError" || e.code === 22)) {
      console.error(`[persistence] 存储空间不足: ${key}`);
      const event = new CustomEvent("storage-quota-exceeded", { detail: { key } });
      window.dispatchEvent(event);
    }
  }
}

export function removeFromStorage(key: keyof typeof KEYS): void {
  try {
    localStorage.removeItem(KEYS[key]);
  } catch {
    // ignore
  }
}

function _writeSessionMessagesShard(sessionId: string, msgs: unknown): string | undefined {
  try {
    const serialized = JSON.stringify(projectSkillTracesInMessages(msgs));
    localStorage.setItem(`${MSG_PREFIX}${sessionId}`, serialized);
    return serialized;
  } catch (e) {
    if (e instanceof DOMException && (e.name === "QuotaExceededError" || e.code === 22)) {
      console.error(`[persistence] 存储空间不足: msg_${sessionId}`);
      const event = new CustomEvent("storage-quota-exceeded", { detail: { key: `msg_${sessionId}` } });
      window.dispatchEvent(event);
    }
    return undefined;
  }
}

/** 按 session 分片存储消息 */
export function saveSessionMessages(sessionId: string, msgs: unknown): boolean {
  return _writeSessionMessagesShard(sessionId, msgs) !== undefined;
}

export type SessionMessagesSaveError =
  | { kind: "serialization_failed" }
  | { kind: "storage_unavailable" }
  | { kind: "storage_access_failed" }
  | { kind: "quota_exceeded" }
  | { kind: "storage_security_error" }
  | { kind: "storage_write_failed" };

export type SessionMessagesSaveResult =
  | { ok: true; key: string }
  | { ok: false; error: SessionMessagesSaveError };

type SessionMessagesStorageErrorKind =
  | "storage_access_failed"
  | "quota_exceeded"
  | "storage_security_error"
  | "storage_write_failed";

function classifySessionMessagesStorageError(
  error: unknown,
  fallback: "storage_access_failed" | "storage_write_failed",
): SessionMessagesStorageErrorKind {
  try {
    if (typeof error === "object" && error !== null) {
      const details = error as { name?: unknown; code?: unknown };
      if (
        details.name === "QuotaExceededError"
        || details.name === "NS_ERROR_DOM_QUOTA_REACHED"
        || details.code === 22
        || details.code === 1014
      ) {
        return "quota_exceeded";
      }
      if (details.name === "SecurityError" || details.code === 18) {
        return "storage_security_error";
      }
    }
  } catch {
    // Fall back when an unusual exception object cannot be inspected.
  }
  return fallback;
}

/** Save a session shard and report serialization or localStorage failures explicitly. */
export function saveSessionMessagesWithResult(
  sessionId: string,
  msgs: unknown,
): SessionMessagesSaveResult {
  let serialized: string | undefined;
  try {
    serialized = JSON.stringify(projectSkillTracesInMessages(msgs));
  } catch {
    return { ok: false, error: { kind: "serialization_failed" } };
  }
  if (serialized === undefined) {
    return { ok: false, error: { kind: "serialization_failed" } };
  }

  let storage: Storage;
  try {
    if (typeof localStorage === "undefined") {
      return { ok: false, error: { kind: "storage_unavailable" } };
    }
    storage = localStorage;
  } catch (error) {
    return {
      ok: false,
      error: { kind: classifySessionMessagesStorageError(error, "storage_access_failed") },
    };
  }

  const key = `${MSG_PREFIX}${sessionId}`;
  try {
    storage.setItem(key, serialized);
    return { ok: true, key };
  } catch (error) {
    return {
      ok: false,
      error: { kind: classifySessionMessagesStorageError(error, "storage_write_failed") },
    };
  }
}

const PENDING_APPEND_JOURNAL_PREFIX = "hwrag_pending_append_payload_v1_";
const PENDING_APPEND_JOURNAL_VERSION = 1 as const;
const PENDING_APPEND_ATTEMPT_PREFIX = "hwrag_pending_append_payload_v2_";
const PENDING_APPEND_ATTEMPT_VERSION = 2 as const;
const PENDING_APPEND_WRITER_LOCK_PREFIX = "hwrag_pending_append_writer_lock_v1_";

/** A JSON-safe normalized chat message, including any caller-provided message fields. */
export interface PendingAppendMessage {
  id: string;
  role: string;
  content: unknown;
  sources?: unknown;
  tool_calls?: unknown;
  activity?: unknown;
  [key: string]: unknown;
}

/**
 * The caller supplies a lifecycle/incarnation identifier that it has already validated.
 * This journal binds the value to an entry but does not validate its authenticity.
 */
export interface PendingAppendPayloadInput {
  sessionId: string;
  lifecycleId: string;
  message: PendingAppendMessage;
}

/** The lifecycle ID is supplied only after the caller has validated the host incarnation. */
export interface PendingAppendPayloadScope {
  sessionId: string;
  lifecycleId: string;
}

export interface PendingAppendPayloadIdentity {
  sessionId: string;
  lifecycleId: string;
  messageId: string;
  /** Required to disambiguate concurrent v2 attempts with the same message ID. */
  attemptId?: string;
}

export interface PendingAppendJournalEntry extends PendingAppendPayloadIdentity {
  version: typeof PENDING_APPEND_JOURNAL_VERSION | typeof PENDING_APPEND_ATTEMPT_VERSION;
  message: PendingAppendMessage;
  /** Exact JSON representation written for the normalized message. */
  payloadJson: string;
}

export interface PendingAppendSaveReceipt extends PendingAppendPayloadInput, AppendMessageV1Receipt {
  /** The attempt ID returned by persistPendingAppendPayload for this exact payload. */
  attemptId: string;
}

export interface PendingAppendPayloadVerificationInput extends PendingAppendPayloadInput {
  attemptId?: string;
}

export type PendingAppendJournalErrorKind =
  | "invalid_input"
  | "invalid_message"
  | "invalid_receipt"
  | "serialization_failed"
  | "storage_unavailable"
  | "storage_access_failed"
  | "storage_read_failed"
  | "storage_write_failed"
  | "storage_remove_failed"
  | "quota_exceeded"
  | "storage_security_error"
  | "corrupt_entry"
  | "unsupported_version"
  | "not_found"
  | "id_conflict"
  | "payload_mismatch"
  | "receipt_mismatch"
  | "readback_mismatch"
  | "atomic_lock_unavailable"
  | "atomic_lock_failed"
  | "attempt_id_unavailable"
  | "ambiguous_entry";

export type PendingAppendJournalResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: { kind: PendingAppendJournalErrorKind } };

export type PendingAppendWriterLockErrorKind =
  | "invalid_scope"
  | "writer_lock_unavailable"
  | "writer_lock_failed";

/** The outer result describes lock acquisition; `value` is the callback's untouched business result. */
export type PendingAppendWriterLockResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: { kind: PendingAppendWriterLockErrorKind } };

interface PendingAppendJournalRecord {
  version: typeof PENDING_APPEND_JOURNAL_VERSION;
  sessionId: string;
  lifecycleId: string;
  messageId: string;
  payloadJson: string;
}

interface PendingAppendAttemptRecord {
  version: typeof PENDING_APPEND_ATTEMPT_VERSION;
  sessionId: string;
  lifecycleId: string;
  messageId: string;
  attemptId: string;
  payloadJson: string;
}

interface PendingAppendAttemptManifest {
  version: typeof PENDING_APPEND_ATTEMPT_VERSION;
  sessionId: string;
  lifecycleId: string;
  entries: PendingAppendAttemptRecord[];
}

interface SerializedPendingAppendMessage {
  message: PendingAppendMessage;
  payloadJson: string;
}

type PendingAppendStorageErrorKind =
  | "storage_access_failed"
  | "storage_read_failed"
  | "storage_write_failed"
  | "storage_remove_failed"
  | "quota_exceeded"
  | "storage_security_error";

function isPendingAppendRecordObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyPendingAppendString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function classifyPendingAppendStorageError(
  error: unknown,
  fallback: "storage_access_failed" | "storage_read_failed" | "storage_write_failed" | "storage_remove_failed",
): PendingAppendStorageErrorKind {
  try {
    if (typeof error === "object" && error !== null) {
      const details = error as { name?: unknown; code?: unknown };
      if (
        details.name === "QuotaExceededError"
        || details.name === "NS_ERROR_DOM_QUOTA_REACHED"
        || details.code === 22
        || details.code === 1014
      ) {
        return "quota_exceeded";
      }
      if (details.name === "SecurityError" || details.code === 18) {
        return "storage_security_error";
      }
    }
  } catch {
    // Use the operation-specific error when an unusual exception cannot be inspected.
  }
  return fallback;
}

function getPendingAppendStorage(): PendingAppendJournalResult<Storage> {
  try {
    if (typeof localStorage === "undefined") {
      return { ok: false, error: { kind: "storage_unavailable" } };
    }
    return { ok: true, value: localStorage };
  } catch (error) {
    return {
      ok: false,
      error: { kind: classifyPendingAppendStorageError(error, "storage_access_failed") },
    };
  }
}

function buildPendingAppendJournalKey(
  identity: PendingAppendPayloadIdentity,
): PendingAppendJournalResult<string> {
  if (
    !isNonEmptyPendingAppendString(identity.sessionId)
    || !isNonEmptyPendingAppendString(identity.lifecycleId)
    || !isNonEmptyPendingAppendString(identity.messageId)
    || (identity.attemptId !== undefined && !/^[0-9a-f]{32}$/.test(identity.attemptId))
  ) {
    return { ok: false, error: { kind: "invalid_input" } };
  }
  try {
    return {
      ok: true,
      value: `${PENDING_APPEND_JOURNAL_PREFIX}${encodeURIComponent(identity.sessionId)}:${encodeURIComponent(identity.lifecycleId)}:${encodeURIComponent(identity.messageId)}`,
    };
  } catch {
    return { ok: false, error: { kind: "invalid_input" } };
  }
}

function buildPendingAppendAttemptManifestKey(
  scope: PendingAppendPayloadScope,
): PendingAppendJournalResult<string> {
  if (
    !isNonEmptyPendingAppendString(scope.sessionId)
    || !isNonEmptyPendingAppendString(scope.lifecycleId)
  ) {
    return { ok: false, error: { kind: "invalid_input" } };
  }
  try {
    return {
      ok: true,
      value: `${PENDING_APPEND_ATTEMPT_PREFIX}${encodeURIComponent(scope.sessionId)}:${encodeURIComponent(scope.lifecycleId)}`,
    };
  } catch {
    return { ok: false, error: { kind: "invalid_input" } };
  }
}

function hasExactPendingAppendKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return actual.length === sortedExpected.length
    && actual.every((key, index) => key === sortedExpected[index]);
}

interface PendingAppendLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T>;
}

function getPendingAppendLockManager(): PendingAppendLockManager | undefined {
  try {
    if (typeof navigator === "undefined") return undefined;
    const lockManager = (navigator as unknown as { locks?: unknown }).locks;
    if (
      typeof lockManager !== "object"
      || lockManager === null
      || typeof (lockManager as { request?: unknown }).request !== "function"
    ) {
      return undefined;
    }
    return lockManager as PendingAppendLockManager;
  } catch {
    return undefined;
  }
}

async function withPendingAppendScopeLock<T>(
  scope: PendingAppendPayloadScope,
  operation: () => Promise<PendingAppendJournalResult<T>>,
): Promise<PendingAppendJournalResult<T>> {
  const manifestKey = buildPendingAppendAttemptManifestKey(scope);
  if (!manifestKey.ok) return manifestKey;
  const lockManager = getPendingAppendLockManager();
  if (!lockManager) return { ok: false, error: { kind: "atomic_lock_unavailable" } };
  const lockName = `hwrag_pending_append_lock_v2_${manifestKey.value.slice(PENDING_APPEND_ATTEMPT_PREFIX.length)}`;
  try {
    return await lockManager.request(lockName, { mode: "exclusive" }, operation);
  } catch {
    return { ok: false, error: { kind: "atomic_lock_failed" } };
  }
}

/**
 * Run one cooperating writer flow under a separate outer lock for this exact scope.
 * Always acquire this writer lock before calling journal APIs, which acquire the v2
 * manifest lock internally. Do not call this helper recursively from its callback.
 * The callback can discover/reuse or create an attempt, call append-v1, verify its
 * receipt, and remove only that exact receipt. This wrapper does not migrate or clean
 * up journal entries. The lock coordinates only same-origin tabs using this protocol;
 * it does not authorize users or prove that data is durable or that a tool did not execute.
 */
export async function withPendingAppendWriterLock<T>(
  scope: PendingAppendPayloadScope,
  operation: (scope: PendingAppendPayloadScope) => Promise<T>,
): Promise<PendingAppendWriterLockResult<T>> {
  let stableScope: PendingAppendPayloadScope;
  let manifestKey: string;
  try {
    if (
      typeof scope !== "object"
      || scope === null
      || !isNonEmptyPendingAppendString(scope.sessionId)
      || !isNonEmptyPendingAppendString(scope.lifecycleId)
    ) {
      return { ok: false, error: { kind: "invalid_scope" } };
    }
    stableScope = { sessionId: scope.sessionId, lifecycleId: scope.lifecycleId };
    const manifestKeyResult = buildPendingAppendAttemptManifestKey(stableScope);
    if (!manifestKeyResult.ok) return { ok: false, error: { kind: "invalid_scope" } };
    manifestKey = manifestKeyResult.value;
  } catch {
    return { ok: false, error: { kind: "invalid_scope" } };
  }

  const lockManager = getPendingAppendLockManager();
  if (!lockManager) return { ok: false, error: { kind: "writer_lock_unavailable" } };
  const lockName = `${PENDING_APPEND_WRITER_LOCK_PREFIX}${manifestKey.slice(PENDING_APPEND_ATTEMPT_PREFIX.length)}`;
  let callbackStarted = false;
  try {
    const value = await lockManager.request(lockName, { mode: "exclusive" }, async () => {
      callbackStarted = true;
      return operation(stableScope);
    });
    if (!callbackStarted) return { ok: false, error: { kind: "writer_lock_failed" } };
    return { ok: true, value };
  } catch (error) {
    if (callbackStarted) throw error;
    return { ok: false, error: { kind: "writer_lock_failed" } };
  }
}

function createPendingAppendAttemptId(): PendingAppendJournalResult<string> {
  try {
    if (typeof crypto === "undefined" || typeof crypto.getRandomValues !== "function") {
      return { ok: false, error: { kind: "attempt_id_unavailable" } };
    }
    const randomBytes = new Uint8Array(16);
    crypto.getRandomValues(randomBytes);
    return {
      ok: true,
      value: Array.from(randomBytes, (byte) => byte.toString(16).padStart(2, "0")).join(""),
    };
  } catch {
    return { ok: false, error: { kind: "attempt_id_unavailable" } };
  }
}

function serializePendingAppendMessage(
  message: unknown,
): PendingAppendJournalResult<SerializedPendingAppendMessage> {
  if (!isPendingAppendRecordObject(message)) {
    return { ok: false, error: { kind: "invalid_message" } };
  }

  let payloadJson: string | undefined;
  try {
    payloadJson = JSON.stringify(message);
  } catch {
    return { ok: false, error: { kind: "serialization_failed" } };
  }
  if (payloadJson === undefined) {
    return { ok: false, error: { kind: "serialization_failed" } };
  }

  let normalized: unknown;
  try {
    normalized = JSON.parse(payloadJson) as unknown;
  } catch {
    return { ok: false, error: { kind: "serialization_failed" } };
  }
  if (
    !isPendingAppendRecordObject(normalized)
    || !isNonEmptyPendingAppendString(normalized.id)
    || !isNonEmptyPendingAppendString(normalized.role)
    || !Object.prototype.hasOwnProperty.call(normalized, "content")
  ) {
    return { ok: false, error: { kind: "invalid_message" } };
  }

  return {
    ok: true,
    value: { message: normalized as PendingAppendMessage, payloadJson },
  };
}

function decodePendingAppendJournalRecord(
  raw: string,
): PendingAppendJournalResult<{ record: PendingAppendJournalRecord; entry: PendingAppendJournalEntry }> {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw) as unknown;
  } catch {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  if (!isPendingAppendRecordObject(decoded)) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  if (decoded.version !== PENDING_APPEND_JOURNAL_VERSION) {
    return { ok: false, error: { kind: "unsupported_version" } };
  }
  if (!hasExactPendingAppendKeys(decoded, ["version", "sessionId", "lifecycleId", "messageId", "payloadJson"])) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  if (
    !isNonEmptyPendingAppendString(decoded.sessionId)
    || !isNonEmptyPendingAppendString(decoded.lifecycleId)
    || !isNonEmptyPendingAppendString(decoded.messageId)
    || typeof decoded.payloadJson !== "string"
  ) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }

  let message: unknown;
  try {
    message = JSON.parse(decoded.payloadJson) as unknown;
  } catch {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  if (
    !isPendingAppendRecordObject(message)
    || !isNonEmptyPendingAppendString(message.id)
    || !isNonEmptyPendingAppendString(message.role)
    || !Object.prototype.hasOwnProperty.call(message, "content")
    || message.id !== decoded.messageId
    || JSON.stringify(message) !== decoded.payloadJson
  ) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }

  const record: PendingAppendJournalRecord = {
    version: PENDING_APPEND_JOURNAL_VERSION,
    sessionId: decoded.sessionId,
    lifecycleId: decoded.lifecycleId,
    messageId: decoded.messageId,
    payloadJson: decoded.payloadJson,
  };
  return {
    ok: true,
    value: {
      record,
      entry: {
        version: PENDING_APPEND_JOURNAL_VERSION,
        sessionId: record.sessionId,
        lifecycleId: record.lifecycleId,
        messageId: record.messageId,
        message: message as PendingAppendMessage,
        payloadJson: record.payloadJson,
      },
    },
  };
}

function decodePendingAppendAttemptManifest(
  raw: string,
  scope: PendingAppendPayloadScope,
): PendingAppendJournalResult<{ manifest: PendingAppendAttemptManifest; entries: PendingAppendJournalEntry[] }> {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw) as unknown;
  } catch {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  if (!isPendingAppendRecordObject(decoded)) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  if (decoded.version !== PENDING_APPEND_ATTEMPT_VERSION) {
    return { ok: false, error: { kind: "unsupported_version" } };
  }
  if (
    !hasExactPendingAppendKeys(decoded, ["version", "sessionId", "lifecycleId", "entries"])
    || decoded.sessionId !== scope.sessionId
    || decoded.lifecycleId !== scope.lifecycleId
    || !Array.isArray(decoded.entries)
  ) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }

  const records: PendingAppendAttemptRecord[] = [];
  const entries: PendingAppendJournalEntry[] = [];
  const attemptIds = new Set<string>();
  for (const candidate of decoded.entries) {
    if (!isPendingAppendRecordObject(candidate)) {
      return { ok: false, error: { kind: "corrupt_entry" } };
    }
    if (candidate.version !== PENDING_APPEND_ATTEMPT_VERSION) {
      return { ok: false, error: { kind: "unsupported_version" } };
    }
    if (
      !hasExactPendingAppendKeys(candidate, [
        "version",
        "sessionId",
        "lifecycleId",
        "messageId",
        "attemptId",
        "payloadJson",
      ])
      || candidate.sessionId !== scope.sessionId
      || candidate.lifecycleId !== scope.lifecycleId
      || !isNonEmptyPendingAppendString(candidate.messageId)
      || typeof candidate.attemptId !== "string"
      || !/^[0-9a-f]{32}$/.test(candidate.attemptId)
      || typeof candidate.payloadJson !== "string"
      || attemptIds.has(candidate.attemptId)
    ) {
      return { ok: false, error: { kind: "corrupt_entry" } };
    }

    let message: unknown;
    try {
      message = JSON.parse(candidate.payloadJson) as unknown;
    } catch {
      return { ok: false, error: { kind: "corrupt_entry" } };
    }
    if (
      !isPendingAppendRecordObject(message)
      || !isNonEmptyPendingAppendString(message.id)
      || !isNonEmptyPendingAppendString(message.role)
      || !Object.prototype.hasOwnProperty.call(message, "content")
      || message.id !== candidate.messageId
      || JSON.stringify(message) !== candidate.payloadJson
    ) {
      return { ok: false, error: { kind: "corrupt_entry" } };
    }

    const record: PendingAppendAttemptRecord = {
      version: PENDING_APPEND_ATTEMPT_VERSION,
      sessionId: scope.sessionId,
      lifecycleId: scope.lifecycleId,
      messageId: candidate.messageId,
      attemptId: candidate.attemptId,
      payloadJson: candidate.payloadJson,
    };
    attemptIds.add(record.attemptId);
    records.push(record);
    entries.push({
      version: PENDING_APPEND_ATTEMPT_VERSION,
      sessionId: record.sessionId,
      lifecycleId: record.lifecycleId,
      messageId: record.messageId,
      attemptId: record.attemptId,
      message: message as PendingAppendMessage,
      payloadJson: record.payloadJson,
    });
  }

  return {
    ok: true,
    value: {
      manifest: {
        version: PENDING_APPEND_ATTEMPT_VERSION,
        sessionId: scope.sessionId,
        lifecycleId: scope.lifecycleId,
        entries: records,
      },
      entries,
    },
  };
}

function readPendingAppendAttemptManifest(
  storage: Storage,
  manifestKey: string,
  scope: PendingAppendPayloadScope,
): PendingAppendJournalResult<{ manifest: PendingAppendAttemptManifest; entries: PendingAppendJournalEntry[]; exists: boolean }> {
  const rawResult = readPendingAppendRecord(storage, manifestKey);
  if (!rawResult.ok) return rawResult;
  if (rawResult.value === null) {
    return {
      ok: true,
      value: {
        manifest: {
          version: PENDING_APPEND_ATTEMPT_VERSION,
          sessionId: scope.sessionId,
          lifecycleId: scope.lifecycleId,
          entries: [],
        },
        entries: [],
        exists: false,
      },
    };
  }
  const decoded = decodePendingAppendAttemptManifest(rawResult.value, scope);
  if (!decoded.ok) return decoded;
  return { ok: true, value: { ...decoded.value, exists: true } };
}

function writePendingAppendAttemptManifest(
  storage: Storage,
  manifestKey: string,
  manifest: PendingAppendAttemptManifest,
): PendingAppendJournalResult<void> {
  let serialized: string;
  try {
    serialized = JSON.stringify(manifest);
  } catch {
    return { ok: false, error: { kind: "serialization_failed" } };
  }
  try {
    storage.setItem(manifestKey, serialized);
    return { ok: true, value: undefined };
  } catch (error) {
    return {
      ok: false,
      error: { kind: classifyPendingAppendStorageError(error, "storage_write_failed") },
    };
  }
}

function readPendingAppendRecord(
  storage: Storage,
  key: string,
): PendingAppendJournalResult<string | null> {
  try {
    return { ok: true, value: storage.getItem(key) };
  } catch (error) {
    return {
      ok: false,
      error: { kind: classifyPendingAppendStorageError(error, "storage_read_failed") },
    };
  }
}

/**
 * Persist one exact message as a uniquely identified attempt. Web Locks serialize the
 * scope-level read/modify/write across tabs; without that browser guarantee this fails closed.
 */
export async function persistPendingAppendPayload(
  input: PendingAppendPayloadInput,
): Promise<PendingAppendJournalResult<{
  entry: PendingAppendJournalEntry;
  outcome: "persisted";
  readBackVerified: true;
}>> {
  if (
    !isNonEmptyPendingAppendString(input.sessionId)
    || !isNonEmptyPendingAppendString(input.lifecycleId)
  ) {
    return { ok: false, error: { kind: "invalid_input" } };
  }
  const serialized = serializePendingAppendMessage(input.message);
  if (!serialized.ok) return serialized;
  const scope = { sessionId: input.sessionId, lifecycleId: input.lifecycleId };
  return withPendingAppendScopeLock(scope, async () => {
    const keyResult = buildPendingAppendAttemptManifestKey(scope);
    if (!keyResult.ok) return keyResult;
    const storageResult = getPendingAppendStorage();
    if (!storageResult.ok) return storageResult;
    const storage = storageResult.value;
    const current = readPendingAppendAttemptManifest(storage, keyResult.value, scope);
    if (!current.ok) return current;

    let attemptId: string | undefined;
    for (let retry = 0; retry < 8; retry += 1) {
      const generated = createPendingAppendAttemptId();
      if (!generated.ok) return generated;
      if (!current.value.manifest.entries.some((entry) => entry.attemptId === generated.value)) {
        attemptId = generated.value;
        break;
      }
    }
    if (!attemptId) return { ok: false, error: { kind: "attempt_id_unavailable" } };

    const record: PendingAppendAttemptRecord = {
      version: PENDING_APPEND_ATTEMPT_VERSION,
      sessionId: scope.sessionId,
      lifecycleId: scope.lifecycleId,
      messageId: serialized.value.message.id,
      attemptId,
      payloadJson: serialized.value.payloadJson,
    };
    const nextManifest: PendingAppendAttemptManifest = {
      ...current.value.manifest,
      entries: [...current.value.manifest.entries, record],
    };
    const written = writePendingAppendAttemptManifest(storage, keyResult.value, nextManifest);
    if (!written.ok) return written;

    const rawReadBack = readPendingAppendRecord(storage, keyResult.value);
    if (!rawReadBack.ok) return rawReadBack;
    if (rawReadBack.value === null) return { ok: false, error: { kind: "readback_mismatch" } };
    const decodedReadBack = decodePendingAppendAttemptManifest(rawReadBack.value, scope);
    if (!decodedReadBack.ok) return decodedReadBack;
    const matchingEntries = decodedReadBack.value.entries.filter((entry) => entry.attemptId === attemptId);
    const readBackEntry = matchingEntries[0];
    if (
      matchingEntries.length !== 1
      || !readBackEntry
      || readBackEntry.sessionId !== scope.sessionId
      || readBackEntry.lifecycleId !== scope.lifecycleId
      || readBackEntry.messageId !== serialized.value.message.id
      || readBackEntry.payloadJson !== serialized.value.payloadJson
    ) {
      return { ok: false, error: { kind: "readback_mismatch" } };
    }

    return {
      ok: true,
      value: {
        entry: readBackEntry,
        outcome: "persisted",
        readBackVerified: true,
      },
    };
  });
}

/** Discover every v2 attempt for one caller-validated session/lifecycle scope. */
export async function discoverPendingAppendPayloads(
  scope: PendingAppendPayloadScope,
): Promise<PendingAppendJournalResult<{ entries: PendingAppendJournalEntry[] }>> {
  return withPendingAppendScopeLock(scope, async () => {
    const keyResult = buildPendingAppendAttemptManifestKey(scope);
    if (!keyResult.ok) return keyResult;
    const storageResult = getPendingAppendStorage();
    if (!storageResult.ok) return storageResult;
    const decoded = readPendingAppendAttemptManifest(storageResult.value, keyResult.value, scope);
    if (!decoded.ok) return decoded;
    return { ok: true, value: { entries: decoded.value.entries } };
  });
}

/** Read one attempt; an unqualified message ID is ambiguous when attempts coexist. */
export async function readPendingAppendPayload(
  identity: PendingAppendPayloadIdentity,
): Promise<PendingAppendJournalResult<{ entry: PendingAppendJournalEntry }>> {
  if (
    !isNonEmptyPendingAppendString(identity.sessionId)
    || !isNonEmptyPendingAppendString(identity.lifecycleId)
    || !isNonEmptyPendingAppendString(identity.messageId)
    || (identity.attemptId !== undefined && !/^[0-9a-f]{32}$/.test(identity.attemptId))
  ) {
    return { ok: false, error: { kind: "invalid_input" } };
  }
  const scope = { sessionId: identity.sessionId, lifecycleId: identity.lifecycleId };
  const discovered = await discoverPendingAppendPayloads(scope);
  if (!discovered.ok) return discovered;
  const matches = discovered.value.entries.filter((entry) => (
    entry.messageId === identity.messageId
    && (identity.attemptId === undefined || entry.attemptId === identity.attemptId)
  ));
  if (matches.length > 1) return { ok: false, error: { kind: "ambiguous_entry" } };
  if (matches.length === 1) return { ok: true, value: { entry: matches[0] } };
  if (identity.attemptId !== undefined) return { ok: false, error: { kind: "not_found" } };

  // Legacy v1 entries remain readable by their full identity, but are never migrated or removed here.
  const keyResult = buildPendingAppendJournalKey(identity);
  if (!keyResult.ok) return keyResult;
  const storageResult = getPendingAppendStorage();
  if (!storageResult.ok) return storageResult;
  const rawResult = readPendingAppendRecord(storageResult.value, keyResult.value);
  if (!rawResult.ok) return rawResult;
  if (rawResult.value === null) return { ok: false, error: { kind: "not_found" } };

  const decoded = decodePendingAppendJournalRecord(rawResult.value);
  if (!decoded.ok) return decoded;
  if (
    decoded.value.record.sessionId !== identity.sessionId
    || decoded.value.record.lifecycleId !== identity.lifecycleId
    || decoded.value.record.messageId !== identity.messageId
  ) {
    return { ok: false, error: { kind: "corrupt_entry" } };
  }
  return { ok: true, value: { entry: decoded.value.entry } };
}

/** Verify one identified attempt against the caller's exact serialized payload. */
export async function verifyPendingAppendPayload(
  input: PendingAppendPayloadVerificationInput,
): Promise<PendingAppendJournalResult<{ entry: PendingAppendJournalEntry; payloadMatched: true }>> {
  if (
    !isNonEmptyPendingAppendString(input.sessionId)
    || !isNonEmptyPendingAppendString(input.lifecycleId)
  ) {
    return { ok: false, error: { kind: "invalid_input" } };
  }
  const serialized = serializePendingAppendMessage(input.message);
  if (!serialized.ok) return serialized;
  const readBack = await readPendingAppendPayload({
    sessionId: input.sessionId,
    lifecycleId: input.lifecycleId,
    messageId: serialized.value.message.id,
    attemptId: input.attemptId,
  });
  if (!readBack.ok) return readBack;
  if (readBack.value.entry.payloadJson !== serialized.value.payloadJson) {
    return { ok: false, error: { kind: "payload_mismatch" } };
  }
  return { ok: true, value: { entry: readBack.value.entry, payloadMatched: true } };
}

/**
 * Remove only the entry that exactly matches a saved/already_saved receipt.
 * A missing message page is never treated as a receipt or as proof of save state.
 */
export function removePendingAppendPayload(
  receipt: PendingAppendSaveReceipt,
): Promise<PendingAppendJournalResult<{ removed: true; messageId: string; attemptId: string }>> {
  if (typeof receipt !== "object" || receipt === null) {
    return Promise.resolve({ ok: false, error: { kind: "invalid_receipt" } });
  }
  if (receipt.status !== "saved" && receipt.status !== "already_saved") {
    return Promise.resolve({ ok: false, error: { kind: "invalid_receipt" } });
  }
  if (
    !isNonEmptyPendingAppendString(receipt.sessionId)
    || !isNonEmptyPendingAppendString(receipt.lifecycleId)
    || !isNonEmptyPendingAppendString(receipt.id)
    || !Number.isSafeInteger(receipt.message_revision)
    || receipt.message_revision < 0
    || typeof receipt.created_at !== "string"
    || (receipt.created_at !== "" && !Number.isFinite(Date.parse(receipt.created_at)))
    || typeof receipt.attemptId !== "string"
    || !/^[0-9a-f]{32}$/.test(receipt.attemptId)
  ) {
    return Promise.resolve({ ok: false, error: { kind: "invalid_receipt" } });
  }
  const serialized = serializePendingAppendMessage(receipt.message);
  if (!serialized.ok) return Promise.resolve(serialized);
  if (receipt.id !== serialized.value.message.id) {
    return Promise.resolve({ ok: false, error: { kind: "receipt_mismatch" } });
  }

  const scope: PendingAppendPayloadScope = {
    sessionId: receipt.sessionId,
    lifecycleId: receipt.lifecycleId,
  };
  return withPendingAppendScopeLock(scope, async () => {
    const keyResult = buildPendingAppendAttemptManifestKey(scope);
    if (!keyResult.ok) return keyResult;
    const storageResult = getPendingAppendStorage();
    if (!storageResult.ok) return storageResult;
    const storage = storageResult.value;
    const current = readPendingAppendAttemptManifest(storage, keyResult.value, scope);
    if (!current.ok) return current;
    const matching = current.value.manifest.entries.filter((entry) => entry.attemptId === receipt.attemptId);
    if (matching.length === 0) return { ok: false, error: { kind: "not_found" } };
    if (
      matching.length !== 1
      || matching[0].messageId !== receipt.id
      || matching[0].payloadJson !== serialized.value.payloadJson
      || matching[0].sessionId !== receipt.sessionId
      || matching[0].lifecycleId !== receipt.lifecycleId
    ) {
      return { ok: false, error: { kind: "receipt_mismatch" } };
    }

    const remaining = current.value.manifest.entries.filter((entry) => entry.attemptId !== receipt.attemptId);
    if (remaining.length === 0) {
      try {
        storage.removeItem(keyResult.value);
      } catch (error) {
        return {
          ok: false,
          error: { kind: classifyPendingAppendStorageError(error, "storage_remove_failed") },
        };
      }
    } else {
      const updated = writePendingAppendAttemptManifest(storage, keyResult.value, {
        ...current.value.manifest,
        entries: remaining,
      });
      if (!updated.ok) return updated;
    }
    return {
      ok: true,
      value: { removed: true, messageId: receipt.id, attemptId: receipt.attemptId },
    };
  });
}

/** 按 session 加载消息 */
export function loadSessionMessages<T>(sessionId: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`${MSG_PREFIX}${sessionId}`);
    if (raw !== null) {
      return projectSkillTracesInMessages(JSON.parse(raw) as unknown) as T;
    }

    const legacyRaw = localStorage.getItem(KEYS.messages);
    if (legacyRaw === null || LEGACY_SESSION_KEY_BLOCKLIST.has(sessionId)) return fallback;
    const legacy = JSON.parse(legacyRaw) as unknown;
    if (
      typeof legacy !== "object"
      || legacy === null
      || Array.isArray(legacy)
      || !Object.prototype.hasOwnProperty.call(legacy, sessionId)
    ) {
      return fallback;
    }
    const legacyMessages = (legacy as Record<string, unknown>)[sessionId];
    if (!Array.isArray(legacyMessages)) return fallback;
    const messages = legacyMessages;
    return projectSkillTracesInMessages(messages) as T;
  } catch {
    return fallback;
  }
}

/** 删除某个 session 的消息分片 */
export function removeSessionMessages(sessionId: string): void {
  try {
    localStorage.removeItem(`${MSG_PREFIX}${sessionId}`);
  } catch {
    // ignore
  }
}

/** 从旧格式 (hwrag_session_messages) 迁移到分片格式 */
export function migrateMessagesToShards(sessionMessages: Record<string, unknown>): void {
  const entries = Object.entries(sessionMessages);
  if (entries.length === 0) return;

  const expectedBySession = new Map<string, string>();
  try {
    for (const [sid, msgs] of entries) {
      const expected = JSON.stringify(projectSkillTracesInMessages(msgs));
      if (expected === undefined) return;
      const key = `${MSG_PREFIX}${sid}`;
      const existing = localStorage.getItem(key);
      if (existing === null) {
        const written = _writeSessionMessagesShard(sid, msgs);
        if (written === undefined) return;
        expectedBySession.set(sid, written);
      } else {
        // An existing shard is authoritative; never replace it with legacy data.
        expectedBySession.set(sid, expected);
      }
    }

    // Keep the legacy source until every target shard can be read back byte-for-byte.
    for (const [sid, expected] of expectedBySession) {
      if (localStorage.getItem(`${MSG_PREFIX}${sid}`) !== expected) return;
    }
    localStorage.removeItem(KEYS.messages);
  } catch {
    // Leave the legacy source available if a read, verification, or removal fails.
  }
}
