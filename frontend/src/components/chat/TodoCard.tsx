import type { TodoItem } from "../../types/session";

/** Agent 任务清单卡片：渲染 TodoWriteTool 推送的 todo_update 事件。
 *  同会话新 TODO 由 useChatStore 直接覆盖 message.todos，本组件只做纯展示。 */
export default function TodoCard({ todos }: { todos: TodoItem[] }) {
  // LLM 偶尔会生成 content 为空的 todo；过滤掉避免计数和显示不一致。
  const visibleTodos = todos.filter((t) => typeof t.content === "string" && t.content.trim() !== "");
  if (visibleTodos.length === 0) return null;
  const completed = countCompleted(visibleTodos);
  const progress = visibleTodos.length ? Math.round((completed / visibleTodos.length) * 100) : 0;
  return (
    <div className="todo-card">
      <div className="todo-card-header">
        <span className="todo-card-title">
          <ListIcon />
          任务清单
        </span>
        <span className="todo-card-progress">{completed}/{visibleTodos.length} 完成</span>
      </div>
      <div className="todo-progress-bar">
        <div className="todo-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="todo-list">
        {visibleTodos.map((todo, idx) => (
          <TodoItemRow
            key={todo.id || `todo-${idx}`}
            todo={todo}
            index={idx}
          />
        ))}
      </div>
    </div>
  );
}

function countCompleted(todos: TodoItem[]): number {
  return todos.filter((t) => t.status === "completed").length;
}

function TodoItemRow({ todo, index }: { todo: TodoItem; index: number }) {
  return (
    <div className={`todo-item status-${todo.status}`}>
      <span className="todo-number">{String(index + 1).padStart(2, "0")}</span>
      <TodoStatusIcon status={todo.status} />
      <span className={`todo-content${todo.status === "completed" ? " done" : ""}`}>
        {todo.content}
      </span>
    </div>
  );
}

function TodoStatusIcon({ status }: { status: TodoItem["status"] }) {
  if (status === "pending") return <CircleIcon />;
  if (status === "in_progress") return <SpinnerIcon />;
  return <CheckIcon />;
}

function ListIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  );
}

function CircleIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="todo-icon-pending">
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="todo-icon-spin">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="todo-icon-done">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}
