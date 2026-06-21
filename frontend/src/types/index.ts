// ── Single source of truth: re-export all domain types ──

// UI meta types (local to this file)
export type NavView = 'chat' | 'knowledge' | 'bookmarks' | 'settings';
export type RightMode = 'workbench' | 'content';
export type Lang = 'zh' | 'en';
export type Theme = 'light' | 'dark' | 'auto';
export type WbTab = 'serial' | 'flash' | 'preview' | 'wiring' | 'safety';
export type FlashState = 'idle' | 'compiling' | 'compiled' | 'flashing' | 'flashed' | 'error';
export type BuildState = 'idle' | 'compiling' | 'compiled' | 'error';

export interface PreviewTab {
  id: string;
  label: string;
  code: string;
  language?: string;
}

export interface ShortcutAction {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  meta?: boolean;
  handler: () => void;
  label: string;
}

// ── Domain re-exports ──
export type {
  Session,
  Message,
  ActivityBlock,
  ActivityStep,
  SourceRef,
} from './session';

export type {
  ProviderInfo,
  Skill,
  MCPServer,
} from './settings';

export type { KBItem } from './kb';
export type { SerialDevice, SerialState } from './serial';

export type {
  ChatRequest,
  Attachment,
  ChatSSEEvent,
  BuildRequest,
  BuildSSEEvent,
  WiringRequest,
  WiringResponse,
  WiringConnection,
  WiringComponent,
  PinAuditRequest,
  PinAuditResponse,
  PinWarning,
  DiagnoseRequest,
  DiagnoseItem,
  DiagnoseResponse,
  ModelsRequest,
  ModelsResponse,
  ToolCall,
  ToolResult,
  DevicesResponse,
} from './api';
