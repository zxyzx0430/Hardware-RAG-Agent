// API 端点常量，对齐 docs/api-contract.md
export const API_BASE = "/api";
export const WS_BASE = "ws://127.0.0.1:58080";
export const API_PORT = 58080;

export const ENDPOINTS = {
  chat: "/api/chat",           // POST SSE
  models: "/api/models",       // POST JSON
  kbUpload: "/api/kb/upload",  // POST multipart
  devices: "/api/devices",     // GET JSON
  wiring: "/api/wiring",       // POST JSON
  auditPins: "/api/audit_pins",// POST JSON
  build: "/api/build",         // POST SSE
  upload: "/api/upload",       // POST SSE
  tool: "/api/tool",           // POST JSON
  monitor: (port: string, baud?: number) =>
    `/monitor/${port}${baud ? `?baud=${baud}` : ""}`, // WS
} as const;

export const SSE_EVENTS = {
  progress: "progress",
  done: "done",
  thinking: "thinking",
  text: "text",
  tool: "tool",
  source: "source",
} as const;
