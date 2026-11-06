import type { TodoItem } from "../types/session";
import type { Plan } from "../types/plan";
import { normalizeTodos } from "../utils/planTodo";

/** Session-scoped plan/todo snapshot state embedded in useChatStore. */
export interface PlanTodoState {
  sessionTodos: Record<string, TodoItem[]>;
  sessionTodosLoaded: Record<string, boolean>;
  /** Bumped on every SSE todo_update so an older GET can be discarded. */
  sessionTodosRevision: Record<string, number>;
  /** True once an SSE todo_update arrived for the session (including a clear). */
  streamingTodosSeen: Record<string, boolean>;
  sessionPlans: Record<string, Plan | null>;
  sessionPlanLoaded: Record<string, boolean>;
  /** Bumped on every SSE plan_update so an older GET can be discarded. */
  sessionPlanRevision: Record<string, number>;
}

export type PlanTodoSnapshotPatch = Partial<PlanTodoState>;

function bump(revisions: Record<string, number>, sessionId: string): Record<string, number> {
  return { ...revisions, [sessionId]: (revisions[sessionId] ?? 0) + 1 };
}

/** Apply an authoritative GET snapshot (does not bump the SSE revision). */
export function todoSnapshotState<T extends PlanTodoState>(
  state: T,
  sessionId: string,
  rawTodos: unknown,
): PlanTodoSnapshotPatch {
  return {
    sessionTodos: { ...state.sessionTodos, [sessionId]: normalizeTodos(rawTodos) },
    sessionTodosLoaded: { ...state.sessionTodosLoaded, [sessionId]: true },
  };
}

/** Apply a live todo_update; marks the session as SSE-seen and invalidates older GETs. */
export function todoUpdateState<T extends PlanTodoState>(
  state: T,
  sessionId: string,
  rawTodos: unknown,
): PlanTodoSnapshotPatch {
  return {
    ...todoSnapshotState(state, sessionId, rawTodos),
    sessionTodosRevision: bump(state.sessionTodosRevision, sessionId),
    streamingTodosSeen: { ...state.streamingTodosSeen, [sessionId]: true },
  };
}

export function planSnapshotState<T extends PlanTodoState>(
  state: T,
  sessionId: string,
  plan: Plan | null,
): PlanTodoSnapshotPatch {
  return {
    sessionPlans: { ...state.sessionPlans, [sessionId]: plan },
    sessionPlanLoaded: { ...state.sessionPlanLoaded, [sessionId]: true },
  };
}

export function planUpdateState<T extends PlanTodoState>(
  state: T,
  sessionId: string,
  plan: Plan | null,
): PlanTodoSnapshotPatch {
  return {
    ...planSnapshotState(state, sessionId, plan),
    sessionPlanRevision: bump(state.sessionPlanRevision, sessionId),
  };
}
