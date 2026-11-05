import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  HardwareProfile,
  HardwareProfileInput,
  MemoryCreateInput,
  MemoryEntry,
  MemoryListParams,
  MemorySearchInput,
  MemoryUpdateInput,
  WorkingMemorySnapshot,
} from "../types/memory";

const MEMORY_PATH = "memory";
const WORKING_PATH = "memory/working";
const PROPOSALS_PATH = "memory/proposals";
const HARDWARE_PROFILES_PATH = "hardware-profiles";

function queryString(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

// The contract fixes the fields but not the list envelope; accept a bare array
// or the common {items|entries|profiles} wrappers so the client stays compatible.
function listFrom<T>(raw: unknown, keys: string[]): T[] {
  if (Array.isArray(raw)) return raw as T[];
  const record = asRecord(raw);
  if (!record) return [];
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

function recordFrom<T>(raw: unknown, keys: string[]): T {
  const record = asRecord(raw);
  if (!record) return raw as T;
  if ("id" in record) return record as T;
  for (const key of keys) {
    const nested = asRecord(record[key]);
    if (nested) return nested as T;
  }
  return raw as T;
}

export async function listMemory(params: MemoryListParams = {}): Promise<MemoryEntry[]> {
  const raw = await apiGet<unknown>(`${MEMORY_PATH}${queryString({
    scope: params.scope,
    scope_key: params.scope_key,
    tier: params.tier,
    status: params.status,
    project_id: params.project_id,
    kind: params.kind,
    q: params.q,
    limit: params.limit,
  })}`);
  return listFrom<MemoryEntry>(raw, ["items", "entries", "memories", "results"]);
}

function bodyFrom(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value);
  if (record) return record;
  if (typeof value === "string" && value.trim()) {
    try {
      return asRecord(JSON.parse(value));
    } catch {
      return null;
    }
  }
  return null;
}

function snapshotFromRecord(record: Record<string, unknown>): WorkingMemorySnapshot | null {
  // Accept both the full snapshot body and the summary `fields` shape.
  const body = bodyFrom(record.body) ?? bodyFrom(record.body_json) ?? bodyFrom(record.fields);
  if (!body) return null;
  return {
    body,
    body_json: typeof record.body_json === "string" ? record.body_json : undefined,
    seal_id: typeof record.seal_id === "string" ? record.seal_id : "",
    generation: typeof record.generation === "number" && Number.isFinite(record.generation)
      ? record.generation
      : 0,
    created_at: typeof record.created_at === "string" ? record.created_at : "",
    updated_at: typeof record.updated_at === "string" ? record.updated_at : "",
  };
}

function workingSnapshotFrom(raw: unknown): WorkingMemorySnapshot | null {
  if (raw === null || raw === undefined) return null;
  const record = asRecord(raw);
  if (!record) return null;
  if ("snapshot" in record) {
    if (record.snapshot === null) return null;
    const nested = asRecord(record.snapshot);
    return nested ? snapshotFromRecord(nested) : null;
  }
  return snapshotFromRecord(record);
}

export async function getWorkingMemory(sessionId: string): Promise<WorkingMemorySnapshot | null> {
  const raw = await apiGet<unknown>(`${WORKING_PATH}/${encodeURIComponent(sessionId)}`);
  return workingSnapshotFrom(raw);
}

export async function listProposals(): Promise<MemoryEntry[]> {
  // WP3's list endpoint filters by status; older builds only register /proposals.
  try {
    const raw = await apiGet<unknown>(`${MEMORY_PATH}${queryString({ status: "proposed", limit: 200 })}`);
    return listFrom<MemoryEntry>(raw, ["proposals", "items", "entries", "memories", "results"]);
  } catch (primaryError) {
    try {
      const raw = await apiGet<unknown>(`${PROPOSALS_PATH}${queryString({ limit: 200 })}`);
      return listFrom<MemoryEntry>(raw, ["proposals", "items", "entries", "memories", "results"]);
    } catch {
      throw primaryError;
    }
  }
}

async function reviewProposal(id: string, action: "approve" | "reject"): Promise<MemoryEntry> {
  const raw = await apiPost<unknown>(`${PROPOSALS_PATH}/${encodeURIComponent(id)}/${action}`);
  return recordFrom<MemoryEntry>(raw, ["entry", "memory", "item", "proposal"]);
}

export async function approveProposal(id: string): Promise<MemoryEntry> {
  return reviewProposal(id, "approve");
}

export async function rejectProposal(id: string): Promise<MemoryEntry> {
  return reviewProposal(id, "reject");
}

export async function searchMemory(input: MemorySearchInput): Promise<MemoryEntry[]> {
  const raw = await apiPost<unknown>(`${MEMORY_PATH}/search`, input);
  return listFrom<MemoryEntry>(raw, ["items", "entries", "memories", "results"]);
}

export async function createMemory(input: MemoryCreateInput): Promise<MemoryEntry> {
  const raw = await apiPost<unknown>(MEMORY_PATH, { ...input, source: input.source ?? "user" });
  return recordFrom<MemoryEntry>(raw, ["entry", "memory", "item"]);
}

export async function updateMemory(id: string, input: MemoryUpdateInput): Promise<MemoryEntry> {
  const raw = await apiPatch<unknown>(`${MEMORY_PATH}/${encodeURIComponent(id)}`, input);
  return recordFrom<MemoryEntry>(raw, ["entry", "memory", "item"]);
}

export async function deleteMemory(id: string): Promise<void> {
  await apiDelete<unknown>(`${MEMORY_PATH}/${encodeURIComponent(id)}`);
}

export async function listHardwareProfiles(params: { q?: string; limit?: number } = {}): Promise<HardwareProfile[]> {
  const raw = await apiGet<unknown>(`${HARDWARE_PROFILES_PATH}${queryString({
    q: params.q,
    limit: params.limit,
  })}`);
  return listFrom<HardwareProfile>(raw, ["items", "profiles", "results"]);
}

export async function getHardwareProfile(id: string): Promise<HardwareProfile> {
  const raw = await apiGet<unknown>(`${HARDWARE_PROFILES_PATH}/${encodeURIComponent(id)}`);
  return recordFrom<HardwareProfile>(raw, ["profile", "item"]);
}

export async function upsertHardwareProfile(input: HardwareProfileInput): Promise<HardwareProfile> {
  const raw = await apiPost<unknown>(HARDWARE_PROFILES_PATH, input);
  return recordFrom<HardwareProfile>(raw, ["profile", "item"]);
}
