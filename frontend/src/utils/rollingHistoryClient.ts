import { apiGet, apiPost } from "../api/client";
import type {
  AppendMessageV1Receipt,
  AppendMessageV1Request,
  BackendMessage,
  BackendSessionLifecycleData,
  TruncateMessagesV1Receipt,
  TruncateMessagesV1Request,
} from "../types/api";

const DEFAULT_PAGE_SIZE = 50;
const MAX_PAGE_SIZE = 200;

export type RollingHistoryClientErrorCode =
  | "INVALID_REQUEST"
  | "INVALID_RESPONSE"
  | "CURSOR_STALLED"
  | "REQUEST_IN_FLIGHT"
  | "SESSION_MISMATCH"
  | "INCARNATION_MISMATCH"
  | "MESSAGE_CONFLICT"
  | "REVISION_MISMATCH"
  | "RESTART_REQUIRED";

export class RollingHistoryClientError extends Error {
  constructor(
    readonly code: RollingHistoryClientErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "RollingHistoryClientError";
  }
}

export interface RollingHistoryPage {
  /** Session identity is client-side metadata used to reject stale responses. */
  sessionId: string;
  /** Present only for pages fetched through the owner-bound v1 pager path. */
  incarnation_id?: string;
  /** Cursor sent with this request; null identifies the first page. */
  before_id: string | null;
  /** Backend snapshot revision shared by every page in this transcript. */
  message_revision: number;
  /** Messages stay in the chronological order returned by the API. */
  messages: BackendMessage[];
  has_more: boolean;
  next_before_id: string | null;
}

export interface RollingHistoryTranscript {
  sessionId: string;
  /** Present only when every page in this transcript is bound to this incarnation. */
  incarnation_id?: string;
  message_revision: number;
  messages: BackendMessage[];
}

/** Opt in to owner-bound lifecycle checks while preserving the numeric legacy call. */
export interface RollingHistoryPagerOptions {
  mode: "v1";
  limit?: number;
}

/** Current-snapshot classification of a session's history storage mode. */
export type SessionHistoryMode =
  | { session_id: string; mode: "owner_v1"; incarnation_id: string }
  | { session_id: string; mode: "legacy"; server_history?: true };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalidResponse(message: string): never {
  throw new RollingHistoryClientError("INVALID_RESPONSE", message);
}

function invalidRequest(message: string): never {
  throw new RollingHistoryClientError("INVALID_REQUEST", message);
}

function isIncarnationId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{32}$/.test(value);
}

function assertRequestIncarnationId(value: unknown, description: string): asserts value is string {
  if (!isIncarnationId(value)) {
    invalidRequest(`${description} 必须是 32 位小写十六进制生命周期 ID`);
  }
}

function assertResponseIncarnationId(value: unknown, description: string): asserts value is string {
  if (!isIncarnationId(value)) {
    invalidResponse(`${description} 必须是 32 位小写十六进制生命周期 ID`);
  }
}

function assertIncarnationMatch(actual: string | undefined, expected: string, description: string): void {
  if (actual !== expected) {
    throw new RollingHistoryClientError(
      "INCARNATION_MISMATCH",
      `${description} 与当前会话生命周期不匹配`,
    );
  }
}

function assertMessageRevision(value: unknown, description: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    invalidResponse(`${description} 必须是非负安全整数`);
  }
}

function assertRequestRevision(value: unknown, description: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    invalidRequest(`${description} 必须是非负安全整数`);
  }
}

function parseTimestampMicros(value: string): number | null {
  if (value === "") return null;

  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$/.exec(value);
  if (!match) return invalidResponse("分页消息的 created_at 不是有效的 ISO 时间");

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const fraction = Number((match[7] ?? "").padEnd(6, "0"));
  const zone = match[8] ?? "Z";
  if (hour > 23 || minute > 59 || second > 59) {
    return invalidResponse("分页消息的 created_at 超出有效时间范围");
  }

  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, 0);
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) {
    return invalidResponse("分页消息的 created_at 包含无效日期");
  }

  let offsetMinutes = 0;
  if (zone !== "Z") {
    const offsetMatch = /^([+-])(\d{2}):(\d{2})$/.exec(zone);
    if (!offsetMatch) return invalidResponse("分页消息的 created_at 时区无效");
    const offsetHours = Number(offsetMatch[2]);
    const offsetRemainder = Number(offsetMatch[3]);
    if (offsetHours > 23 || offsetRemainder > 59) {
      return invalidResponse("分页消息的 created_at 时区超出有效范围");
    }
    const sign = offsetMatch[1] === "+" ? 1 : -1;
    offsetMinutes = sign * (offsetHours * 60 + offsetRemainder);
  }

  const timestampMicros = (date.getTime() - offsetMinutes * 60_000) * 1_000 + fraction;
  if (!Number.isSafeInteger(timestampMicros)) {
    return invalidResponse("分页消息的 created_at 超出可比较范围");
  }
  return timestampMicros;
}

function compareCodePoints(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? 0);
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    if (leftPoints[index] !== rightPoints[index]) {
      return leftPoints[index] < rightPoints[index] ? -1 : 1;
    }
  }
  if (leftPoints.length === rightPoints.length) return 0;
  return leftPoints.length < rightPoints.length ? -1 : 1;
}

/** Compare by the backend's ascending (created_at, id) transcript order. */
function compareMessagePosition(left: BackendMessage, right: BackendMessage): number {
  const leftTimestamp = parseTimestampMicros(left.created_at ?? "");
  const rightTimestamp = parseTimestampMicros(right.created_at ?? "");
  if (leftTimestamp === null && rightTimestamp !== null) return -1;
  if (leftTimestamp !== null && rightTimestamp === null) return 1;
  if (leftTimestamp !== null && rightTimestamp !== null && leftTimestamp !== rightTimestamp) {
    return leftTimestamp < rightTimestamp ? -1 : 1;
  }
  return compareCodePoints(left.id, right.id);
}

function assertBackendMessage(value: unknown): asserts value is BackendMessage {
  if (
    !isRecord(value)
    || typeof value.id !== "string"
    || value.id.length === 0
    || typeof value.role !== "string"
    || typeof value.content !== "string"
    || typeof value.created_at !== "string"
  ) {
    invalidResponse("分页响应包含格式无效的消息");
  }
  if (value.sources !== undefined && value.sources !== null && !Array.isArray(value.sources)) {
    invalidResponse("分页消息的 sources 必须是数组或 null");
  }
  if (value.tool_calls !== undefined && value.tool_calls !== null && !Array.isArray(value.tool_calls)) {
    invalidResponse("分页消息的 tool_calls 必须是数组或 null");
  }
  if (
    value.activity !== undefined
    && value.activity !== null
    && !isRecord(value.activity)
  ) {
    invalidResponse("分页消息的 activity 必须是对象或 null");
  }
  parseTimestampMicros(value.created_at);
}

function deepEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((item, index) => deepEqual(item, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && deepEqual(left[key], right[key]));
  }
  return false;
}

function validatePage(
  sessionId: string,
  incarnationId: string | undefined,
  limit: number,
  beforeId: string | null,
  cursorAnchor: BackendMessage | null,
  seenCursors: ReadonlySet<string>,
  expectedRevision: number | null,
  raw: unknown,
): RollingHistoryPage {
  if (!isRecord(raw) || !Array.isArray(raw.messages)) {
    return invalidResponse("分页响应缺少 messages 数组");
  }
  assertMessageRevision(raw.message_revision, "分页响应的 message_revision");
  if (expectedRevision !== null && raw.message_revision !== expectedRevision) {
    throw new RollingHistoryClientError(
      "REVISION_MISMATCH",
      "分页响应属于不同的消息快照，请重新读取首屏",
    );
  }
  if (typeof raw.has_more !== "boolean") {
    return invalidResponse("分页响应缺少有效的 has_more");
  }
  if (raw.next_before_id !== null && (typeof raw.next_before_id !== "string" || raw.next_before_id.length === 0)) {
    return invalidResponse("分页响应的 next_before_id 必须是非空字符串或 null");
  }
  if (raw.messages.length > limit) {
    return invalidResponse("分页响应超过请求的 limit");
  }

  const messages = raw.messages as unknown[];
  const ids = new Set<string>();
  for (const message of messages) {
    assertBackendMessage(message);
    if (ids.has(message.id)) return invalidResponse("单页消息中存在重复 ID");
    ids.add(message.id);
  }

  for (let index = 1; index < messages.length; index += 1) {
    if (compareMessagePosition(messages[index - 1] as BackendMessage, messages[index] as BackendMessage) > 0) {
      return invalidResponse("分页消息没有按 (created_at, id) 正序排列");
    }
  }

  if (cursorAnchor) {
    if (!beforeId || cursorAnchor.id !== beforeId) {
      return invalidResponse("分页请求的 cursor 与锚点不匹配");
    }
    if (messages.some((message) => compareMessagePosition(message as BackendMessage, cursorAnchor) >= 0)) {
      throw new RollingHistoryClientError("CURSOR_STALLED", "服务端分页结果没有向更旧消息推进");
    }
  }

  const nextBeforeId = raw.next_before_id as string | null;
  if (raw.has_more) {
    if (messages.length === 0 || nextBeforeId === null || nextBeforeId !== (messages[0] as BackendMessage).id) {
      return invalidResponse("仍有更多消息时必须返回本页最旧消息的游标");
    }
    if (nextBeforeId === beforeId || seenCursors.has(nextBeforeId)) {
      throw new RollingHistoryClientError("CURSOR_STALLED", "分页游标重复，已停止继续请求");
    }
  } else if (nextBeforeId !== null) {
    return invalidResponse("没有更多消息时 next_before_id 必须为 null");
  }

  return {
    sessionId,
    ...(incarnationId !== undefined ? { incarnation_id: incarnationId } : {}),
    before_id: beforeId,
    message_revision: raw.message_revision,
    messages: [...(messages as BackendMessage[])],
    has_more: raw.has_more,
    next_before_id: nextBeforeId,
  };
}

export class RollingHistoryPager {
  readonly sessionId: string;
  readonly limit: number;
  readonly mode: "legacy" | "v1";
  private beforeId: string | null = null;
  private cursorAnchor: BackendMessage | null = null;
  private readonly seenCursors = new Set<string>();
  private exhausted = false;
  private requestInFlight = false;
  private pinnedRevision: number | null = null;
  private restartRequired = false;
  private ownerIncarnationId: string | null = null;

  constructor(sessionId: string, limit?: number);
  constructor(sessionId: string, options: RollingHistoryPagerOptions);
  constructor(
    sessionId: string,
    limitOrOptions: number | RollingHistoryPagerOptions = DEFAULT_PAGE_SIZE,
  ) {
    if (typeof sessionId !== "string" || sessionId.length === 0) {
      throw new RollingHistoryClientError("INVALID_REQUEST", "sessionId 不能为空");
    }
    let limit: number;
    if (typeof limitOrOptions === "number") {
      this.mode = "legacy";
      limit = limitOrOptions;
    } else {
      if (!isRecord(limitOrOptions) || limitOrOptions.mode !== "v1") {
        invalidRequest("owner-bound 分页器需要显式指定 mode: v1");
      }
      this.mode = "v1";
      limit = limitOrOptions.limit === undefined ? DEFAULT_PAGE_SIZE : limitOrOptions.limit as number;
    }
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE) {
      throw new RollingHistoryClientError("INVALID_REQUEST", "limit 必须是 1 到 200 的整数");
    }
    this.sessionId = sessionId;
    this.limit = limit;
  }

  async fetchNextPage(): Promise<RollingHistoryPage | null> {
    if (this.requestInFlight) {
      throw new RollingHistoryClientError("REQUEST_IN_FLIGHT", "当前分页请求尚未完成");
    }
    if (this.exhausted) return null;
    if (this.restartRequired) {
      throw new RollingHistoryClientError(
        "RESTART_REQUIRED",
        "分页读取已失效，请重新创建分页器并读取首屏",
      );
    }

    this.requestInFlight = true;
    try {
      if (this.mode === "v1" && this.ownerIncarnationId === null) {
        const lifecycle = await getSessionLifecycle(this.sessionId);
        this.ownerIncarnationId = lifecycle.incarnation_id;
      }

      const requestedBeforeId = this.beforeId;
      const query = new URLSearchParams({ limit: String(this.limit) });
      if (this.mode === "v1") {
        if (this.ownerIncarnationId === null) {
          throw new RollingHistoryClientError(
            "RESTART_REQUIRED",
            "会话生命周期缺失，请重新创建分页器并读取首屏",
          );
        }
        query.set("expected_incarnation_id", this.ownerIncarnationId);
      }
      if (requestedBeforeId !== null) query.set("before_id", requestedBeforeId);
      if (requestedBeforeId !== null) {
        if (this.pinnedRevision === null) {
          throw new RollingHistoryClientError(
            "RESTART_REQUIRED",
            "分页快照版本缺失，请重新创建分页器并读取首屏",
          );
        }
        query.set("expected_revision", String(this.pinnedRevision));
      }
      const path = `sessions/${encodeURIComponent(this.sessionId)}/messages?${query.toString()}`;
      const raw = await apiGet<unknown>(path);
      const page = validatePage(
        this.sessionId,
        this.ownerIncarnationId ?? undefined,
        this.limit,
        requestedBeforeId,
        this.cursorAnchor,
        this.seenCursors,
        this.pinnedRevision,
        raw,
      );

      let nextAnchor: BackendMessage | undefined;
      if (page.has_more) {
        const nextBeforeId = page.next_before_id;
        nextAnchor = page.messages[0];
        if (nextBeforeId === null || nextAnchor === undefined) {
          throw new RollingHistoryClientError("INVALID_RESPONSE", "分页响应缺少可继续读取的游标");
        }
      }

      if (this.pinnedRevision === null) {
        this.pinnedRevision = page.message_revision;
      }
      if (page.has_more) {
        const nextBeforeId = page.next_before_id as string;
        if (requestedBeforeId !== null) this.seenCursors.add(requestedBeforeId);
        this.beforeId = nextBeforeId;
        this.cursorAnchor = { ...(nextAnchor as BackendMessage) };
      } else {
        if (requestedBeforeId !== null) this.seenCursors.add(requestedBeforeId);
        this.beforeId = null;
        this.cursorAnchor = null;
        this.exhausted = true;
      }
      return page;
    } catch (error) {
      this.restartRequired = true;
      throw error;
    } finally {
      this.requestInFlight = false;
    }
  }
}

export function mergeOlderRollingHistoryPage(
  current: RollingHistoryTranscript | null,
  olderPage: RollingHistoryPage,
  activeSessionId: string,
  expectedIncarnationId?: string,
): RollingHistoryTranscript {
  assertMessageRevision(olderPage.message_revision, "分页结果的 message_revision");
  if (
    typeof activeSessionId !== "string"
    || activeSessionId.length === 0
    || olderPage.sessionId !== activeSessionId
    || (current !== null && current.sessionId !== activeSessionId)
  ) {
    throw new RollingHistoryClientError("SESSION_MISMATCH", "分页结果属于已切换或不同的会话");
  }

  if (olderPage.incarnation_id !== undefined) {
    assertResponseIncarnationId(olderPage.incarnation_id, "分页结果的 incarnation_id");
  }
  if (current?.incarnation_id !== undefined) {
    assertResponseIncarnationId(current.incarnation_id, "当前会话的 incarnation_id");
  }
  if (expectedIncarnationId !== undefined) {
    assertRequestIncarnationId(expectedIncarnationId, "合并分页的 expected incarnation_id");
    assertIncarnationMatch(olderPage.incarnation_id, expectedIncarnationId, "分页结果");
    if (current !== null) {
      assertIncarnationMatch(current.incarnation_id, expectedIncarnationId, "当前转录");
    }
  } else if (olderPage.incarnation_id !== undefined || current?.incarnation_id !== undefined) {
    throw new RollingHistoryClientError(
      "INCARNATION_MISMATCH",
      "owner-bound 分页结果必须使用显式生命周期 ID 合并",
    );
  }

  if (current === null) {
    if (olderPage.before_id !== null) {
      throw new RollingHistoryClientError("CURSOR_STALLED", "无法把非首屏消息页合并到空会话");
    }
    return {
      sessionId: activeSessionId,
      ...(expectedIncarnationId !== undefined ? { incarnation_id: expectedIncarnationId } : {}),
      message_revision: olderPage.message_revision,
      messages: [...olderPage.messages],
    };
  }

  assertMessageRevision(current.message_revision, "当前会话的 message_revision");
  if (current.message_revision !== olderPage.message_revision) {
    throw new RollingHistoryClientError(
      "REVISION_MISMATCH",
      "分页结果与当前会话属于不同的消息快照",
    );
  }

  const currentAnchor = current.messages[0];
  if (currentAnchor === undefined || olderPage.before_id !== currentAnchor.id) {
    throw new RollingHistoryClientError("CURSOR_STALLED", "较旧消息页与当前会话游标不连续");
  }

  const currentById = new Map<string, BackendMessage>();
  for (const message of current.messages) {
    const existing = currentById.get(message.id);
    if (existing && !deepEqual(existing, message)) {
      throw new RollingHistoryClientError("MESSAGE_CONFLICT", `会话中同一消息 ID 内容冲突：${message.id}`);
    }
    currentById.set(message.id, existing ?? message);
  }

  const olderById = new Map<string, BackendMessage>();
  for (const message of olderPage.messages) {
    const duplicate = olderById.get(message.id);
    if (duplicate && !deepEqual(duplicate, message)) {
      throw new RollingHistoryClientError("MESSAGE_CONFLICT", `分页中同一消息 ID 内容冲突：${message.id}`);
    }
    olderById.set(message.id, duplicate ?? message);
  }

  const merged: BackendMessage[] = [];
  const emittedIds = new Set<string>();
  for (const message of olderById.values()) {
    const existing = currentById.get(message.id);
    if (existing && !deepEqual(existing, message)) {
      throw new RollingHistoryClientError("MESSAGE_CONFLICT", `分页消息与现有消息内容冲突：${message.id}`);
    }
    if (!emittedIds.has(message.id)) {
      merged.push(existing ?? message);
      emittedIds.add(message.id);
    }
  }
  for (const message of current.messages) {
    if (!emittedIds.has(message.id)) {
      merged.push(currentById.get(message.id) ?? message);
      emittedIds.add(message.id);
    }
  }

  return {
    sessionId: activeSessionId,
    ...(expectedIncarnationId !== undefined ? { incarnation_id: expectedIncarnationId } : {}),
    message_revision: current.message_revision,
    messages: merged,
  };
}

function assertSessionId(sessionId: string): void {
  if (typeof sessionId !== "string" || sessionId.length === 0) {
    invalidRequest("sessionId 不能为空");
  }
}

/** Read the owner-only lifecycle identity used to fence v1 history operations. */
export async function getSessionLifecycle(sessionId: string): Promise<BackendSessionLifecycleData> {
  assertSessionId(sessionId);
  const raw = await apiGet<unknown>(`sessions/${encodeURIComponent(sessionId)}/lifecycle`);
  if (!isRecord(raw) || raw.session_id !== sessionId) {
    invalidResponse("会话 lifecycle 响应的 session_id 与请求不匹配");
  }
  assertResponseIncarnationId(raw.incarnation_id, "会话 lifecycle 响应的 incarnation_id");
  return {
    session_id: raw.session_id,
    incarnation_id: raw.incarnation_id,
  };
}

/**
 * Classify the session history mode from the backend's current snapshot.
 * This is not authorization; page and write routes validate incarnation independently.
 */
export async function getSessionHistoryMode(sessionId: string): Promise<SessionHistoryMode> {
  assertSessionId(sessionId);
  const raw = await apiGet<unknown>(`sessions/${encodeURIComponent(sessionId)}/history-mode`);
  if (!isRecord(raw) || raw.session_id !== sessionId) {
    invalidResponse("会话 history-mode 响应的 session_id 与请求不匹配");
  }

  if (raw.mode === "owner_v1") {
    if (!Object.prototype.hasOwnProperty.call(raw, "incarnation_id")) {
      invalidResponse("owner_v1 history-mode 响应缺少 incarnation_id");
    }
    assertResponseIncarnationId(raw.incarnation_id, "会话 history-mode 响应的 incarnation_id");
    return {
      session_id: sessionId,
      mode: "owner_v1",
      incarnation_id: raw.incarnation_id,
    };
  }
  if (raw.mode === "legacy") {
    if ("incarnation_id" in raw) {
      invalidResponse("legacy history-mode 响应不得包含 incarnation_id");
    }
    if (raw.server_history !== undefined && typeof raw.server_history !== "boolean") {
      invalidResponse("legacy history-mode 的 server_history 必须是布尔值");
    }
    return {
      session_id: sessionId,
      mode: "legacy",
      ...(raw.server_history === true ? { server_history: true as const } : {}),
    };
  }
  return invalidResponse("会话 history-mode 响应的 mode 无效");
}

function assertReceiptCount(value: unknown, description: string): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    invalidResponse(`${description} 必须是非负安全整数`);
  }
}

function assertFiniteNumber(value: unknown, description: string, invalid: (message: string) => never): void {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    invalid(`${description} 必须是有限数字`);
  }
}

function assertOptionalStringFields(
  record: Record<string, unknown>,
  fields: readonly string[],
  description: string,
): void {
  for (const field of fields) {
    if (record[field] !== undefined && typeof record[field] !== "string") {
      invalidRequest(`${description} 的 ${field} 必须是字符串`);
    }
  }
}

function assertOptionalNumberFields(
  record: Record<string, unknown>,
  fields: readonly string[],
  description: string,
): void {
  for (const field of fields) {
    if (record[field] !== undefined) {
      assertFiniteNumber(record[field], `${description} 的 ${field}`, invalidRequest);
    }
  }
}

function assertOptionalNullableNumberFields(
  record: Record<string, unknown>,
  fields: readonly string[],
  description: string,
): void {
  for (const field of fields) {
    if (record[field] !== undefined && record[field] !== null) {
      assertFiniteNumber(record[field], `${description} 的 ${field}`, invalidRequest);
    }
  }
}

function assertAppendSources(value: unknown): void {
  if (value === undefined || value === null) return;
  if (!Array.isArray(value)) invalidRequest("append-v1 的 sources 必须是数组或 null");
  for (const [index, source] of value.entries()) {
    const description = `append-v1 的 sources[${index}]`;
    if (
      !isRecord(source)
      || typeof source.id !== "string"
      || typeof source.title !== "string"
      || typeof source.doc !== "string"
      || typeof source.excerpt !== "string"
    ) {
      invalidRequest(`${description} 缺少有效的 id、title、doc 或 excerpt`);
    }
    assertFiniteNumber(source.page, `${description} 的 page`, invalidRequest);
    assertFiniteNumber(source.score, `${description} 的 score`, invalidRequest);
    assertOptionalNumberFields(source, ["chunk_index", "score_percentage"], description);
    assertOptionalNullableNumberFields(source, ["page_start", "page_end"], description);
    assertOptionalStringFields(source, [
      "section_title",
      "source_url",
      "category",
      "chunk_method",
      "relevance_level",
      "citation",
      "language",
      "kb_id",
      "kb_name",
      "small_chunk_id",
      "big_chunk_id",
      "small_chunk_text",
      "messageId",
    ], description);
  }
}

function assertAppendActivity(value: unknown): void {
  if (value === undefined || value === null) return;
  if (!isRecord(value) || !Array.isArray(value.steps)) {
    invalidRequest("append-v1 的 activity 必须包含 steps 数组");
  }
  assertFiniteNumber(value.durationMs, "append-v1 的 activity.durationMs", invalidRequest);
  if (value.status !== undefined && !["running", "done", "error"].includes(String(value.status))) {
    invalidRequest("append-v1 的 activity.status 无效");
  }
  for (const [index, step] of value.steps.entries()) {
    const description = `append-v1 的 activity.steps[${index}]`;
    if (
      !isRecord(step)
      || (step.type !== "thinking" && step.type !== "tool")
      || typeof step.id !== "string"
    ) {
      invalidRequest(`${description} 缺少有效的 type 或 id`);
    }
    assertOptionalStringFields(step, [
      "content", "name", "icon", "args", "result", "call_id", "decision_source", "file_path",
    ], description);
    assertOptionalNumberFields(step, ["duration", "startTime"], description);
    if (step.status !== undefined && !["pending", "running", "done", "error"].includes(String(step.status))) {
      invalidRequest(`${description}.status 无效`);
    }
    if (step.source !== undefined && !["rag", "llm", "reasoning", "agent"].includes(String(step.source))) {
      invalidRequest(`${description}.source 无效`);
    }
    if (step.risk_level !== undefined && !["low", "medium", "high"].includes(String(step.risk_level))) {
      invalidRequest(`${description}.risk_level 无效`);
    }
    if (step.skill_trace !== undefined) {
      const trace = step.skill_trace;
      if (
        !isRecord(trace)
        || typeof trace.skill_id !== "string"
        || (trace.stage !== "instructions" && trace.stage !== "resource")
        || (trace.success !== undefined && typeof trace.success !== "boolean")
      ) {
        invalidRequest(`${description}.skill_trace 格式无效`);
      }
      assertOptionalStringFields(trace, ["content_hash", "path"], `${description}.skill_trace`);
    }
  }
}

function assertAppendRequest(request: unknown): asserts request is AppendMessageV1Request {
  if (
    !isRecord(request)
    || typeof request.id !== "string"
    || request.id.trim().length === 0
    || typeof request.role !== "string"
    || typeof request.content !== "string"
  ) {
    invalidRequest("append-v1 需要稳定的非空消息 ID、role 和 content");
  }
  let contentUtf8Bytes = 0;
  for (let index = 0; index < request.content.length; index += 1) {
    const codeUnit = request.content.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const nextCodeUnit = request.content.charCodeAt(index + 1);
      if (!(nextCodeUnit >= 0xdc00 && nextCodeUnit <= 0xdfff)) {
        invalidRequest("append-v1 的 content 包含未配对的 UTF-16 代理项");
      }
      contentUtf8Bytes += 4;
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      invalidRequest("append-v1 的 content 包含未配对的 UTF-16 代理项");
    } else if (codeUnit <= 0x7f) {
      contentUtf8Bytes += 1;
    } else if (codeUnit <= 0x7ff) {
      contentUtf8Bytes += 2;
    } else {
      contentUtf8Bytes += 3;
    }
    if (contentUtf8Bytes > 1_048_576) {
      invalidRequest("append-v1 的 content 超过 1,048,576 UTF-8 字节");
    }
  }
  assertRequestIncarnationId(request.expected_incarnation_id, "append-v1 的 expected_incarnation_id");
  assertAppendSources(request.sources);
  if (request.tool_calls !== undefined && request.tool_calls !== null && !Array.isArray(request.tool_calls)) {
    invalidRequest("append-v1 的 tool_calls 必须是数组或 null");
  }
  assertAppendActivity(request.activity);
}

function assertTruncateRequest(request: unknown): asserts request is TruncateMessagesV1Request {
  if (
    !isRecord(request)
    || typeof request.keep_through_id !== "string"
    || request.keep_through_id.trim().length === 0
  ) {
    invalidRequest("truncate-v1 需要非空的 keep_through_id");
  }
  assertRequestRevision(request.expected_revision, "truncate-v1 的 expected_revision");
  assertRequestIncarnationId(request.expected_incarnation_id, "truncate-v1 的 expected_incarnation_id");
}

/**
 * Save one already-produced message through append-v1. The caller supplies a
 * stable message ID and payload; this function never starts or replays chat or
 * tool execution and does not persist pending writes or retry them automatically.
 */
export async function appendMessageV1(
  sessionId: string,
  request: AppendMessageV1Request,
): Promise<AppendMessageV1Receipt> {
  assertSessionId(sessionId);
  assertAppendRequest(request);

  // Build an explicit body so retries with the same caller-owned ID use the
  // same API fields, without sending unrelated object properties. The
  // incarnation comes from the caller's owner-only lifecycle read.
  const payload: AppendMessageV1Request = {
    id: request.id,
    expected_incarnation_id: request.expected_incarnation_id,
    role: request.role,
    content: request.content,
    ...(request.sources !== undefined ? { sources: request.sources } : {}),
    ...(request.tool_calls !== undefined ? { tool_calls: request.tool_calls } : {}),
    ...(request.activity !== undefined ? { activity: request.activity } : {}),
  };
  const raw = await apiPost<unknown>(
    `sessions/${encodeURIComponent(sessionId)}/messages/append-v1`,
    payload,
  );
  if (
    !isRecord(raw)
    || raw.id !== request.id
    || (raw.status !== "saved" && raw.status !== "already_saved")
    || typeof raw.created_at !== "string"
  ) {
    invalidResponse("append-v1 收据的 id、status 或 created_at 无效");
  }
  parseTimestampMicros(raw.created_at);
  assertMessageRevision(raw.message_revision, "append-v1 收据的 message_revision");
  return {
    id: raw.id,
    status: raw.status,
    message_revision: raw.message_revision,
    created_at: raw.created_at,
  };
}

/**
 * Truncate through one existing message from the caller's pinned snapshot.
 * The server route only performs this database mutation; it does not invoke
 * chat, Agent planning, or tools. Failed calls are surfaced without replay.
 */
export async function truncateMessagesV1(
  sessionId: string,
  request: TruncateMessagesV1Request,
): Promise<TruncateMessagesV1Receipt> {
  assertSessionId(sessionId);
  assertTruncateRequest(request);

  const payload: TruncateMessagesV1Request = {
    keep_through_id: request.keep_through_id,
    expected_revision: request.expected_revision,
    expected_incarnation_id: request.expected_incarnation_id,
  };
  const raw = await apiPost<unknown>(
    `sessions/${encodeURIComponent(sessionId)}/messages/truncate-v1`,
    payload,
  );
  if (!isRecord(raw)) {
    invalidResponse("truncate-v1 收据格式无效");
  }
  assertReceiptCount(raw.kept, "truncate-v1 收据的 kept");
  assertReceiptCount(raw.deleted, "truncate-v1 收据的 deleted");
  assertMessageRevision(raw.message_revision, "truncate-v1 收据的 message_revision");
  const expectedReceiptRevision = request.expected_revision + (raw.deleted > 0 ? 1 : 0);
  if (
    !Number.isSafeInteger(expectedReceiptRevision)
    || raw.message_revision !== expectedReceiptRevision
  ) {
    invalidResponse("truncate-v1 收据的 message_revision 与删除结果不一致");
  }
  return {
    kept: raw.kept,
    deleted: raw.deleted,
    message_revision: raw.message_revision,
  };
}
