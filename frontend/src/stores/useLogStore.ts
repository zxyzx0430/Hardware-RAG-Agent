import { create } from "zustand";

interface LogEntry {
  ts: number;
  level: "error" | "warn" | "info" | "ok" | "debug";
  tag: string;
  msg: string;
}

interface LogState {
  buffer: LogEntry[];
  filter: { levels: Record<string, boolean>; timeRange: number };
  maxSize: number;

  log: (level: LogEntry["level"], tag: string, msg: string) => void;
  clear: () => void;
  setFilter: (f: Partial<LogState["filter"]>) => void;
  getFiltered: () => LogEntry[];
}

export const useLogStore = create<LogState>((set, get) => ({
  buffer: [],
  filter: { levels: { error: true, warn: true, info: true, ok: true, debug: false }, timeRange: 0 },
  maxSize: 2000,

  log: (level, tag, msg) =>
    set((s) => {
      const entry: LogEntry = { ts: Date.now(), level, tag, msg };
      const buffer = [...s.buffer, entry];
      if (buffer.length > s.maxSize) buffer.splice(0, buffer.length - s.maxSize);
      return { buffer };
    }),

  clear: () => set({ buffer: [] }),

  setFilter: (f) => set((s) => ({ filter: { ...s.filter, ...f } })),

  getFiltered: () => {
    const { buffer, filter } = get();
    const now = Date.now();
    const cutoff = filter.timeRange > 0 ? now - filter.timeRange * 60000 : 0;
    return buffer.filter((e) => {
      if (!filter.levels[e.level]) return false;
      if (cutoff && e.ts < cutoff) return false;
      return true;
    });
  },
}));
