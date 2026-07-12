// WiringPane 可编辑状态 store
// 存放器件列表 / 连线列表 / 生成的 SVG / BOM 清单
// 供 WiringEditor（增删改）和 WiringPane 渲染区（展示 SVG/BOM）共同消费

import { create } from "zustand";
import type { WiringComponent, WiringConnection } from "../types/api";

export interface BomEntry {
  component: string;
  qty: number;
}

export interface WiringExtractData {
  components: WiringComponent[];
  connections: WiringConnection[];
}

interface WiringState {
  // 可编辑状态
  components: WiringComponent[];
  connections: WiringConnection[];
  svg: string;
  bom: BomEntry[];
  // Wiring↔Safety cross-linkage: pins flagged as conflict by SafetyPane audit
  conflictPins: Set<string>;
  // Pin selected from WiringPane (clicking a component) — SafetyPane scrolls to it
  selectedPin: string | null;

  // Actions
  addComponent: (comp: WiringComponent) => void;
  removeComponent: (name: string) => void;
  updateComponent: (name: string, patch: Partial<WiringComponent>) => void;
  addConnection: (conn: WiringConnection) => void;
  removeConnection: (idx: number) => void;
  setSvg: (svg: string) => void;
  setBom: (bom: BomEntry[]) => void;
  setConflictPins: (pins: Set<string>) => void;
  setSelectedPin: (pin: string | null) => void;
  loadFromExtract: (data: WiringExtractData) => void;
  clearAll: () => void;
}

// 判断连线是否引用了指定器件名（用于级联删除）
const connReferencesName = (
  conn: WiringConnection,
  name: string,
): boolean => conn.from.component === name || conn.to.component === name;

export const useWiringStore = create<WiringState>((set) => ({
  components: [],
  connections: [],
  svg: "",
  bom: [],
  conflictPins: new Set<string>(),
  selectedPin: null,

  addComponent: (comp) =>
    set((s) => ({ components: [...s.components, comp] })),

  removeComponent: (name) =>
    set((s) => ({
      components: s.components.filter((c) => c.name !== name),
      connections: s.connections.filter((c) => !connReferencesName(c, name)),
    })),

  updateComponent: (name, patch) =>
    set((s) => ({
      components: s.components.map((c) =>
        c.name === name ? { ...c, ...patch } : c,
      ),
    })),

  addConnection: (conn) =>
    set((s) => ({ connections: [...s.connections, conn] })),

  removeConnection: (idx) =>
    set((s) => ({
      connections: s.connections.filter((_, i) => i !== idx),
    })),

  setSvg: (svg) => set({ svg }),

  setBom: (bom) => set({ bom }),

  setConflictPins: (conflictPins) => set({ conflictPins }),

  setSelectedPin: (selectedPin) => set({ selectedPin }),

  loadFromExtract: (data) =>
    set({ components: data.components, connections: data.connections }),

  clearAll: () =>
    set({
      components: [],
      connections: [],
      svg: "",
      bom: [],
      conflictPins: new Set<string>(),
      selectedPin: null,
    }),
}));
