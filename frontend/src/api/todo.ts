import { apiDelete, apiGet } from "./client";
import type { TodoItem } from "../types/session";
import { normalizeTodos } from "../utils/planTodo";

export interface SessionTodosSnapshot {
  todos: TodoItem[];
  updatedAt: string | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** GET /api/sessions/{id}/todos — server-authoritative todo snapshot. */
export async function fetchSessionTodos(sessionId: string): Promise<SessionTodosSnapshot> {
  const raw = await apiGet<unknown>(`sessions/${encodeURIComponent(sessionId)}/todos`);
  const record = asRecord(raw);
  const todosRaw = record && "todos" in record ? record.todos : raw;
  return {
    todos: normalizeTodos(todosRaw),
    updatedAt: record && typeof record.updated_at === "string" ? record.updated_at : null,
  };
}

/** DELETE /api/sessions/{id}/todos — clears the persisted snapshot. */
export async function clearSessionTodos(sessionId: string): Promise<void> {
  await apiDelete<unknown>(`sessions/${encodeURIComponent(sessionId)}/todos`);
}
