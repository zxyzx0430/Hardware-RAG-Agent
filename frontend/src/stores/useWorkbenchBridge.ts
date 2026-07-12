// useWorkbenchBridge — bridges Agent tool SSE events to Workbench panes.
//
// Listens to tool_call / tool_result SSE events (from useChatStore) and routes
// render_data to the matching pane (wiring / safety / preview). Handles the
// "auto-switch only on first call" rule via workbenchUserOverride.
//
// T5 boundary note: useChatStore is T5-owned. We never modify it. Because
// useChatStore does NOT expose lastToolResult/lastToolCall fields and its
// tool_result branch collapses result to a string (target_pane / render_data
// are lost), subscribe alone cannot recover render_data. Therefore T5 calls
// handleToolResultEvent / handleToolCallEvent inside the onEvent callback
// (one line each). Coordination point — T1 must notify T5.

import { create } from "zustand";
import { useAppStore } from "./useAppStore";
import type { ToolResultSSEEvent, ToolCallSSEEvent } from "../types/api";

// ─── Render data shapes (asserted at runtime; backend is the source) ─────

interface WiringRenderData {
  svg: string;
  bom: { component: string; qty: number }[];
}

interface SafetyRenderData {
  safe: boolean;
  conflicts: unknown[];
  warnings: unknown[];
  pin_map: Record<string, unknown>;
}

interface CodeRenderData {
  code: string;
  language: string;
}

// Flash tool render_data: stage + result + optional binary_path / port.
// Backend BuildTool / FlashTool returns this shape (Task 6).
export interface FlashRenderData {
  stage: "compile" | "flash";
  binary_path?: string;
  port?: string;
  success: boolean;
  error?: { code: string; message: string };
}

type TargetPane = "wiring" | "safety" | "preview" | "flash";

// ─── Store ───────────────────────────────────────────────────────────────

interface WorkbenchBridgeState {
  wiringRenderData: WiringRenderData | null;
  safetyRenderData: SafetyRenderData | null;
  codeRenderData: CodeRenderData | null;
  flashRenderData: FlashRenderData | null;
  flashLiveLog: string[];
  flashProgress: number;
  lastCallId: string | null;
  setWiringRenderData: (data: WiringRenderData | null) => void;
  setSafetyRenderData: (data: SafetyRenderData | null) => void;
  setCodeRenderData: (data: CodeRenderData | null) => void;
  setFlashRenderData: (data: FlashRenderData | null) => void;
  clearFlashRenderData: () => void;
  appendFlashLiveLog: (line: string) => void;
  setFlashProgress: (percent: number) => void;
  clearFlashLiveLog: () => void;
  setLastCallId: (id: string | null) => void;
}

export const useWorkbenchBridge = create<WorkbenchBridgeState>((set) => ({
  wiringRenderData: null,
  safetyRenderData: null,
  codeRenderData: null,
  flashRenderData: null,
  flashLiveLog: [],
  flashProgress: 0,
  lastCallId: null,
  setWiringRenderData: (wiringRenderData) => set({ wiringRenderData }),
  setSafetyRenderData: (safetyRenderData) => set({ safetyRenderData }),
  setCodeRenderData: (codeRenderData) => set({ codeRenderData }),
  setFlashRenderData: (flashRenderData) => set({ flashRenderData }),
  clearFlashRenderData: () => set({ flashRenderData: null }),
  appendFlashLiveLog: (line) => set((s) => ({ flashLiveLog: [...s.flashLiveLog, line] })),
  setFlashProgress: (flashProgress) => set({ flashProgress }),
  clearFlashLiveLog: () => set({ flashLiveLog: [], flashProgress: 0 }),
  setLastCallId: (lastCallId) => set({ lastCallId }),
}));

// ─── Type guards ─────────────────────────────────────────────────────────

function isWiringRenderData(d: unknown): d is WiringRenderData {
  return typeof d === "object" && d !== null
    && typeof (d as WiringRenderData).svg === "string";
}

function isSafetyRenderData(d: unknown): d is SafetyRenderData {
  return typeof d === "object" && d !== null
    && typeof (d as SafetyRenderData).safe === "boolean";
}

function isCodeRenderData(d: unknown): d is CodeRenderData {
  return typeof d === "object" && d !== null
    && typeof (d as CodeRenderData).code === "string";
}

function isFlashRenderData(d: unknown): d is FlashRenderData {
  return typeof d === "object" && d !== null
    && typeof (d as FlashRenderData).stage === "string"
    && typeof (d as FlashRenderData).success === "boolean";
}

// ─── Helpers ─────────────────────────────────────────────────────────────

/** Switch rightMode + wbTab to target pane, unless user has overridden.
 *  source="bridge" so this auto-switch does NOT set workbenchUserOverride
 *  (only manual user clicks should lock out auto-switching). */
function autoSwitchPane(target: TargetPane): void {
  const app = useAppStore.getState();
  if (app.workbenchUserOverride) return;
  app.setRightMode("workbench");
  app.setWbTab(target, "bridge");
}

/** Reset workbenchUserOverride when a new Agent tool call_id arrives. */
function resetOverrideIfNewCallId(callId: string): void {
  const bridge = useWorkbenchBridge.getState();
  if (callId === bridge.lastCallId) return;
  useAppStore.getState().resetWorkbenchOverride();
  bridge.setLastCallId(callId);
}

/** Push wiring render_data + auto-switch pane. */
function dispatchWiringResult(data: unknown): void {
  if (!isWiringRenderData(data)) return;
  useWorkbenchBridge.getState().setWiringRenderData(data);
  autoSwitchPane("wiring");
}

/** Push safety render_data + auto-switch pane. */
function dispatchSafetyResult(data: unknown): void {
  if (!isSafetyRenderData(data)) return;
  useWorkbenchBridge.getState().setSafetyRenderData(data);
  autoSwitchPane("safety");
}

/** Push code render_data + auto-switch pane. */
function dispatchCodeResult(data: unknown): void {
  if (!isCodeRenderData(data)) return;
  useWorkbenchBridge.getState().setCodeRenderData(data);
  autoSwitchPane("preview");
}

/** Push flash render_data + auto-switch pane.
 *  Stage "compile" = build done; "flash" = upload done.
 *  FlashPane subscribes to flashRenderData to fill compile log + progress. */
function dispatchFlashResult(data: unknown): void {
  if (!isFlashRenderData(data)) return;
  useWorkbenchBridge.getState().setFlashRenderData(data);
  autoSwitchPane("flash");
}

/** Dispatch render_data by target_pane. */
function dispatchByTargetPane(target: TargetPane, data: unknown): void {
  switch (target) {
    case "wiring": dispatchWiringResult(data); break;
    case "safety": dispatchSafetyResult(data); break;
    case "preview": dispatchCodeResult(data); break;
    case "flash": dispatchFlashResult(data); break;
  }
}

// ─── Public handlers (called by useChatStore onEvent) ────────────────────

/**
 * Handle a tool_result SSE event. Routes render_data to the matching pane.
 * Caller: T5 must invoke this inside useChatStore's onEvent "tool_result"
 * branch (one line: `handleToolResultEvent(sse);`). result may be a string
 * (legacy format) — in that case this is a no-op.
 *
 * Supports both ToolResultEnvelope.data.* and legacy top-level fields.
 */
export function handleToolResultEvent(event: ToolResultSSEEvent): void {
  if (typeof event.result === "string") return;
  const top = event.result;
  const inner = typeof top.data === "object" && top.data !== null ? top.data : {};
  const target_pane = top.target_pane || inner.target_pane;
  const render_data = top.render_data !== undefined ? top.render_data : inner.render_data;
  if (!target_pane || render_data === undefined) return;
  console.info('[WorkbenchBridge] tool_result call_id=%s target_pane=%s', event.call_id, target_pane);
  dispatchByTargetPane(target_pane, render_data);
}

/**
 * Handle a tool_call SSE event. Resets workbenchUserOverride when a new
 * Agent tool call_id arrives (start of a new turn).
 * Caller: T5 must invoke this inside useChatStore's onEvent "tool_call"
 * branch (one line: `handleToolCallEvent(sse);`).
 */
export function handleToolCallEvent(event: ToolCallSSEEvent): void {
  if (!event.call_id) return;
  console.info('[WorkbenchBridge] tool_call tool=%s call_id=%s', event.tool, event.call_id);
  resetOverrideIfNewCallId(event.call_id);
  if (event.tool === "build_firmware" || event.tool === "flash_firmware") {
    useWorkbenchBridge.getState().clearFlashLiveLog();
  }
}

/**
 * Append a compile_log line for the current build/flash tool call.
 * Caller: T5 must invoke this inside useChatStore's onEvent "compile_log" branch.
 */
export function handleCompileLogEvent(call_id: string, line: string): void {
  const bridge = useWorkbenchBridge.getState();
  if (call_id !== bridge.lastCallId) return;
  bridge.appendFlashLiveLog(line);
}

/**
 * Update build/flash progress for the current tool call.
 * Caller: T5 must invoke this inside useChatStore's onEvent "progress" branch.
 */
export function handleProgressEvent(call_id: string, percent: number, message?: string): void {
  const bridge = useWorkbenchBridge.getState();
  if (call_id !== bridge.lastCallId) return;
  bridge.setFlashProgress(percent);
  if (message) {
    bridge.appendFlashLiveLog(`⏳ ${message}`);
  }
}

// NOTE: The previous `initWorkbenchBridge()` fallback subscription (which
// listened to streamingSteps to reset workbenchUserOverride) has been removed
// as dead code — T5 now calls handleToolCallEvent directly inside useChatStore's
// onEvent, making the fallback path obsolete.
