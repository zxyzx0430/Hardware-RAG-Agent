import type { SkillTrace, SkillTraceStage } from "../types/session";

export type SkillToolName = "load_skill" | "read_skill_resource";

export interface SkillToolCallProjection {
  args: string;
  trace?: SkillTrace;
}

export interface SkillToolResultProjection {
  success: boolean;
  result: string;
  trace?: SkillTrace;
}

const SKILL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const CONTENT_HASH_PATTERN = /^[0-9a-f]{64}$/;
const TEXT_RESOURCE_EXTENSIONS = new Set([".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".toml"]);
const WINDOWS_DEVICE_NAME_PATTERN = /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/i;

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function parseArguments(value: unknown): Record<string, unknown> | null {
  if (typeof value === "string") {
    try {
      return asRecord(JSON.parse(value) as unknown);
    } catch {
      return null;
    }
  }
  return asRecord(value);
}

export function isSkillToolName(value: unknown): value is SkillToolName {
  return value === "load_skill" || value === "read_skill_resource";
}

export function isValidSkillId(value: unknown): value is string {
  return typeof value === "string" && SKILL_ID_PATTERN.test(value);
}

export function isValidSkillResourcePath(value: unknown): value is string {
  if (
    typeof value !== "string"
    || value.length < 1
    || value.length > 1024
    || value.includes("\0")
    || value.includes("\\")
    || value.includes(":")
    || value.startsWith("/")
  ) {
    return false;
  }

  const parts = value.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) return false;
  for (const part of parts) {
    if (
      part.normalize("NFC") !== part
      || part.endsWith(" ")
      || part.endsWith(".")
      || WINDOWS_DEVICE_NAME_PATTERN.test(part)
      || [...part].some((character) => {
        const codePoint = character.codePointAt(0) ?? 0;
        return codePoint < 32 || '<>"|?*'.includes(character);
      })
    ) {
      return false;
    }
  }

  const rootDirectory = parts[0].toLowerCase();
  if (rootDirectory !== "references" && rootDirectory !== "assets") return false;
  const filename = parts[parts.length - 1];
  const extensionIndex = filename.lastIndexOf(".");
  const extension = extensionIndex > 0 ? filename.slice(extensionIndex).toLowerCase() : "";
  return TEXT_RESOURCE_EXTENSIONS.has(extension);
}

function stageForTool(toolName: SkillToolName): SkillTraceStage {
  return toolName === "load_skill" ? "instructions" : "resource";
}

export function projectSkillToolCall(toolName: unknown, args: unknown): SkillToolCallProjection | null {
  if (!isSkillToolName(toolName)) return null;

  const parsedArgs = parseArguments(args);
  const skillId = parsedArgs?.skill_id;
  if (!isValidSkillId(skillId)) return { args: "{}" };

  if (toolName === "load_skill") {
    const safeArgs = { skill_id: skillId };
    return { args: JSON.stringify(safeArgs), trace: { skill_id: skillId, stage: "instructions" } };
  }

  const path = parsedArgs?.path;
  if (!isValidSkillResourcePath(path)) return { args: "{}" };
  const safeArgs = { skill_id: skillId, path };
  return {
    args: JSON.stringify(safeArgs),
    trace: { skill_id: skillId, stage: "resource", path },
  };
}

export function projectSkillTraceForStep(
  toolName: unknown,
  args: unknown,
  value: unknown,
): SkillTrace | undefined {
  const call = projectSkillToolCall(toolName, args);
  if (!call?.trace) return undefined;

  const base = call.trace;
  const candidate = asRecord(value);
  if (!candidate) return base;

  const matchesCall = candidate.skill_id === base.skill_id
    && candidate.stage === base.stage
    && (toolName !== "read_skill_resource" || candidate.path === base.path);
  if (!matchesCall) return { ...base, success: false };

  const hasHash = Object.prototype.hasOwnProperty.call(candidate, "content_hash");
  const contentHash = candidate.content_hash;
  const validHash = isValidContentHash(contentHash);
  const hasSuccess = Object.prototype.hasOwnProperty.call(candidate, "success");
  const candidateSuccess = candidate.success;
  const hasError = candidate.error !== undefined && candidate.error !== null;
  let success: boolean | undefined;

  if (hasSuccess) {
    success = typeof candidateSuccess === "boolean" ? candidateSuccess : false;
    if (success && (!validHash || hasError)) success = false;
  }

  return {
    ...base,
    ...(hasHash && validHash ? { content_hash: contentHash } : {}),
    ...(success !== undefined ? { success } : {}),
  };
}

function isValidContentHash(value: unknown): value is string {
  return typeof value === "string" && CONTENT_HASH_PATTERN.test(value);
}

function hasOwn(value: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

export function projectSkillToolResult(
  toolName: unknown,
  args: unknown,
  previousTrace: unknown,
  result: unknown,
  eventSuccess: unknown,
): SkillToolResultProjection | null {
  if (!isSkillToolName(toolName)) return null;

  const call = projectSkillToolCall(toolName, args);
  const base = call?.trace;
  const envelope = asRecord(result);
  const data = asRecord(envelope?.data);
  const hash = data?.content_hash;
  const previous = projectSkillTraceForStep(toolName, args, previousTrace);
  const previousRecord = asRecord(previousTrace);
  const fieldsMatchCall = Boolean(
    base
    && data
    && data.skill_id === base.skill_id
    && data.stage === stageForTool(toolName)
    && (toolName !== "read_skill_resource" || (
      isValidSkillResourcePath(data.path) && data.path === base.path
    )),
  );
  const previousHashIsValid = Boolean(
    previousRecord
    && (!hasOwn(previousRecord, "content_hash") || isValidContentHash(previousRecord.content_hash)),
  );
  const previousSuccessIsValid = Boolean(
    previousRecord
    && (!hasOwn(previousRecord, "success") || typeof previousRecord.success === "boolean"),
  );
  const traceMatchesCall = Boolean(
    base
    && previousRecord
    && previous?.success !== false
    && previousHashIsValid
    && previousSuccessIsValid
    && (previousRecord.error === undefined || previousRecord.error === null)
    && previous?.skill_id === base.skill_id
    && previousRecord.skill_id === base.skill_id
    && previousRecord.stage === base.stage
    && (toolName !== "read_skill_resource"
      || (previous?.path === base.path && previousRecord.path === base.path)),
  );
  const success = eventSuccess === true
    && envelope?.success === true
    && data !== null
    && hasOwn(data, "success")
    && data.success === true
    && (envelope.error === undefined || envelope.error === null)
    && fieldsMatchCall
    && traceMatchesCall
    && isValidContentHash(hash);

  const matchingHash = fieldsMatchCall && isValidContentHash(hash) ? hash : undefined;
  const trace = base
    ? {
        ...base,
        ...(matchingHash ? { content_hash: matchingHash } : {}),
        success,
      }
    : undefined;
  return {
    success,
    result: success
      ? (toolName === "load_skill" ? "Skill instructions loaded" : "Skill resource loaded")
      : "Skill operation failed",
    ...(trace ? { trace } : {}),
  };
}

function projectSkillActivityStep(value: unknown): unknown {
  const step = asRecord(value);
  if (!step) return value;

  if (step.type !== "tool" || !isSkillToolName(step.name)) {
    if (!hasOwn(step, "skill_trace")) return value;
    const projected = { ...step };
    delete projected.skill_trace;
    return projected;
  }

  const call = projectSkillToolCall(step.name, step.args);
  const trace = projectSkillTraceForStep(step.name, step.args, step.skill_trace);
  const status = step.status;
  const isSuccessful = trace?.success === true && status === "done";
  const terminalFailure = (status === "done" || status === "error")
    && !isSuccessful
    && trace?.success !== false;
  const prematureSuccess = trace?.success === true && !isSuccessful;
  const safeTrace = trace && (terminalFailure || prematureSuccess)
    ? { ...trace, success: false }
    : trace;
  const safeStatus = status === "done" && !isSuccessful ? "error" : status;
  const shouldSetResult = hasOwn(step, "result") || status === "done" || status === "error";
  const safeResult = isSuccessful
    ? (step.name === "load_skill" ? "Skill instructions loaded" : "Skill resource loaded")
    : "Skill operation failed";
  const projected: Record<string, unknown> = {
    ...step,
    args: call?.args ?? "{}",
    ...(safeStatus !== undefined ? { status: safeStatus } : {}),
    ...(safeTrace ? { skill_trace: safeTrace } : {}),
  };
  if (!safeTrace) delete projected.skill_trace;
  if (shouldSetResult) projected.result = safeResult;
  return projected;
}

export function projectSkillTraceActivity(value: unknown): unknown {
  const activity = asRecord(value);
  if (!activity || !Array.isArray(activity.steps)) return value;

  let changed = false;
  const steps = activity.steps.map((step) => {
    const projected = projectSkillActivityStep(step);
    if (projected !== step) changed = true;
    return projected;
  });
  return changed ? { ...activity, steps } : value;
}

function projectMessages(value: unknown): unknown {
  if (!Array.isArray(value)) return value;

  let changed = false;
  const messages = value.map((message) => {
    const record = asRecord(message);
    if (!record || !hasOwn(record, "activity")) return message;
    const activity = projectSkillTraceActivity(record.activity);
    if (activity === record.activity) return message;
    changed = true;
    return { ...record, activity };
  });
  return changed ? messages : value;
}

export function projectSkillTracesInMessages(value: unknown): unknown {
  if (Array.isArray(value)) return projectMessages(value);
  const record = asRecord(value);
  if (!record) return value;
  if (hasOwn(record, "activity")) {
    const projected = projectMessages([record]) as unknown[];
    return projected[0] === value ? value : projected[0];
  }

  let changed = false;
  const projected: Record<string, unknown> = { ...record };
  for (const [sessionId, messages] of Object.entries(record)) {
    const safeMessages = projectMessages(messages);
    if (safeMessages !== messages) {
      projected[sessionId] = safeMessages;
      changed = true;
    }
  }
  return changed ? projected : value;
}
