import type { SourceRef } from "../types/session";

/** Source cards are ranked by score; inline citations use the same order. */
export function sortSourcesForDisplay(sources?: SourceRef[]): SourceRef[] {
  return [...(sources ?? [])].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
}

export function sourceDisplayNumber(sourceId: string, sources?: SourceRef[]): number {
  const index = sortSourcesForDisplay(sources).findIndex((source) => source.id === sourceId);
  if (index >= 0) return index + 1;
  const match = /^src(\d+)$/.exec(sourceId);
  return match ? Number(match[1]) : 0;
}
