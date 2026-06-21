import { create } from "zustand";

interface SerialState {
  connected: boolean;
  port: string;
  baudRate: number;
  log: string[];
  autoScroll: boolean;
  dtrActive: boolean;
  rtsActive: boolean;
  filter: string;

  setConnected: (c: boolean) => void;
  setPort: (p: string) => void;
  setBaudRate: (b: number) => void;
  addLog: (line: string) => void;
  clearLog: () => void;
  setAutoScroll: (s: boolean) => void;
  toggleDtr: () => void;
  toggleRts: () => void;
  setFilter: (f: string) => void;
}

export const useSerialStore = create<SerialState>((set) => ({
  connected: false,
  port: "",
  baudRate: 115200,
  log: [],
  autoScroll: true,
  dtrActive: false,
  rtsActive: false,
  filter: "",

  setConnected: (connected) => set({ connected }),
  setPort: (port) => set({ port }),
  setBaudRate: (baudRate) => set({ baudRate }),
  addLog: (line) => set((s) => ({ log: [...s.log.slice(-4999), line] })),
  clearLog: () => set({ log: [] }),
  setAutoScroll: (autoScroll) => set({ autoScroll }),
  toggleDtr: () => set((s) => ({ dtrActive: !s.dtrActive })),
  toggleRts: () => set((s) => ({ rtsActive: !s.rtsActive })),
  setFilter: (filter) => set({ filter }),
}));
