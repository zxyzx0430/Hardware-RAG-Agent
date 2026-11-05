import { apiDelete, apiGet, apiPatch, apiPost } from "./client";
import type {
  HardwareProfile,
  HardwareProfileInput,
  MemoryCreateInput,
  MemoryEntry,
  MemoryListParams,
  MemorySearchInput,
  MemoryUpdateInput,
} from "../types/memory";

const MEMORY_PATH = "memory";
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
    kind: params.kind,
    q: params.q,
    limit: params.limit,
  })}`);
  return listFrom<MemoryEntry>(raw, ["items", "entries", "memories", "results"]);
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
