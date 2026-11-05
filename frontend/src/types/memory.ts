// Memory v1 and hardware profile types — see docs memory-v1 contract.

export const MEMORY_SCOPES = ["global", "project", "session"] as const;
export type MemoryScope = (typeof MEMORY_SCOPES)[number];

export const MEMORY_KINDS = ["preference", "fact", "constraint", "decision", "procedure"] as const;
export type MemoryKind = (typeof MEMORY_KINDS)[number];

export const MEMORY_SOURCES = ["user", "agent", "skill", "import"] as const;
export type MemorySource = (typeof MEMORY_SOURCES)[number];

export interface MemoryEntry {
  id: string;
  scope: MemoryScope;
  scope_key: string;
  kind: MemoryKind;
  content: string;
  structured: Record<string, unknown>;
  source: MemorySource | string;
  confidence: number;
  created_at: string;
  updated_at: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  deleted_at?: string | null;
  supersedes_id?: string | null;
}

export interface MemoryListParams {
  scope?: MemoryScope | "";
  scope_key?: string;
  kind?: MemoryKind | "";
  q?: string;
  limit?: number;
}

export interface MemoryCreateInput {
  scope: MemoryScope;
  scope_key?: string;
  kind: MemoryKind;
  content: string;
  structured?: Record<string, unknown>;
  source?: MemorySource;
  confidence?: number;
  expires_at?: string | null;
}

export interface MemoryUpdateInput {
  content?: string;
  structured?: Record<string, unknown>;
  confidence?: number;
  expires_at?: string | null;
}

export interface MemorySearchInput {
  query: string;
  scope?: MemoryScope;
  scope_key?: string;
  kind?: MemoryKind;
  limit?: number;
}

export interface HardwareProfile {
  id: string;
  name: string;
  aliases: string[];
  mcu: string;
  package: string;
  voltage: string;
  clock: string;
  pins: Record<string, unknown>;
  peripherals: unknown[];
  toolchain: string;
  sources: unknown[];
  rejected: unknown[];
  confidence: number;
  use_count: number;
  last_used_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface HardwareProfileInput {
  id?: string;
  name: string;
  aliases?: string[];
  mcu?: string;
  package?: string;
  voltage?: string;
  clock?: string;
  pins?: Record<string, unknown>;
  peripherals?: unknown[];
  toolchain?: string;
  sources?: unknown[];
  rejected?: unknown[];
  confidence?: number;
}
