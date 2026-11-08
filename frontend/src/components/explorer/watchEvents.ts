/** File-watcher path filtering and toast throttling. */

// SQLite WAL/SHM sidecar files change on every database write (including the
// app's own history writes). They are never meaningful project files.
const IGNORED_SUFFIXES = [
  ".db-wal",
  ".db-shm",
  ".db-journal",
  ".sqlite-wal",
  ".sqlite-shm",
  ".sqlite-journal",
];

export function isIgnoredWatchPath(path: string | null | undefined): boolean {
  if (!path) return false;
  const normalized = path.replace(/\\/g, "/").toLowerCase();
  return IGNORED_SUFFIXES.some((suffix) => normalized.endsWith(suffix));
}

export interface FollowToastThrottle {
  /** Returns true when a toast may be shown now; false while still in the cooldown. */
  shouldShow(now?: number): boolean;
  /** Per-path variant: one create/modify burst for the same file yields one toast. */
  shouldShowFor(path: string, now?: number): boolean;
}

/**
 * At most one follow-mode toast per interval. A watcher storm is coalesced
 * into a single visible message instead of covering the right panel.
 */
export function createFollowToastThrottle(intervalMs = 2000): FollowToastThrottle {
  let lastShownAt = Number.NEGATIVE_INFINITY;
  const lastShownByPath = new Map<string, number>();
  const allowed = (last: number, now: number) => now - last >= intervalMs;
  return {
    shouldShow(now = Date.now()) {
      if (!allowed(lastShownAt, now)) return false;
      lastShownAt = now;
      return true;
    },
    shouldShowFor(path, now = Date.now()) {
      const key = path.replace(/\\/g, "/").toLowerCase();
      const last = lastShownByPath.get(key) ?? Number.NEGATIVE_INFINITY;
      if (!allowed(last, now)) return false;
      lastShownByPath.set(key, now);
      return true;
    },
  };
}
