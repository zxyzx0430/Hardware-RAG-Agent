import { MEMORY_STATUSES, MEMORY_TIERS } from "../../types/memory";
import type { MemoryEntry, MemoryStatus, MemoryTier, WorkingMemoryField } from "../../types/memory";

const TIER_BY_SCOPE: Record<string, MemoryTier> = {
  global: "user",
  project: "project",
  session: "working",
};

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function formatDate(value: string | null | undefined, lang: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { hour12: false });
}

export function entryTier(entry: Pick<MemoryEntry, "tier" | "scope">): MemoryTier {
  const tier = typeof entry.tier === "string" ? entry.tier : "";
  if ((MEMORY_TIERS as readonly string[]).includes(tier)) return tier as MemoryTier;
  return TIER_BY_SCOPE[entry.scope] ?? "project";
}

export function entryStatus(entry: Pick<MemoryEntry, "status">): MemoryStatus {
  const status = typeof entry.status === "string" ? entry.status : "";
  if ((MEMORY_STATUSES as readonly string[]).includes(status)) return status as MemoryStatus;
  return "active";
}

export function tierLabelKey(tier: string): string {
  return `memoryTier_${tier}`;
}

export function statusLabelKey(status: string): string {
  return `memoryStatus_${status}`;
}

export function fieldLabelKey(field: WorkingMemoryField): string {
  return `workingMemoryField_${field}`;
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Evidence is rendered as plain text only — never as HTML. */
export function formatEvidence(evidence: MemoryEntry["evidence"] | unknown): string {
  if (evidence === null || evidence === undefined) return "";
  if (typeof evidence === "string") return evidence.trim();
  if (typeof evidence !== "object") return displayValue(evidence);
  if (Array.isArray(evidence)) {
    return evidence.map(displayValue).filter(Boolean).join(" · ");
  }
  return Object.entries(evidence as Record<string, unknown>)
    .map(([key, value]) => {
      const shown = displayValue(value).trim();
      return shown ? `${key}: ${shown}` : "";
    })
    .filter(Boolean)
    .join(" · ");
}

function claimText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value.map(claimText).filter(Boolean).join("; ");
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["text", "claim", "content", "statement", "value"]) {
      if (typeof record[key] === "string") return (record[key] as string).trim();
    }
  }
  return "";
}

/** Flatten one checkpoint field (string, claim list or claim object) into bullet lines. */
export function claimTexts(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(claimText).filter(Boolean);
  }
  const text = claimText(value);
  return text ? [text] : [];
}
