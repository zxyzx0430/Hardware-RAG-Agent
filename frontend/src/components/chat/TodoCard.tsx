import { useEffect, useRef, useState } from "react";
import type { TodoItem } from "../../types/session";
import { useI18n } from "../../i18n";

const STATUS_ORDER: Record<TodoItem["status"], number> = {
  in_progress: 0,
  pending: 1,
  completed: 2,
};

const STATUS_LABEL_KEYS: Record<TodoItem["status"], string> = {
  in_progress: "todoStatusInProgress",
  pending: "todoStatusPending",
  completed: "todoStatusCompleted",
};

const PRIORITY_LABEL_KEYS: Record<NonNullable<TodoItem["priority"]>, string> = {
  high: "todoPriorityHigh",
  medium: "todoPriorityMedium",
  low: "todoPriorityLow",
};

type TodoGroup = { status: TodoItem["status"]; items: TodoItem[] };

/** Group by status in workflow order; submission order is kept inside a group. */
function groupTodos(todos: TodoItem[]): TodoGroup[] {
  const groups = new Map<TodoItem["status"], TodoItem[]>();
  for (const todo of todos) {
    const bucket = groups.get(todo.status) ?? [];
    bucket.push(todo);
    groups.set(todo.status, bucket);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => STATUS_ORDER[a] - STATUS_ORDER[b])
    .map(([status, items]) => ({ status, items }));
}

/** Agent 任务清单卡片：渲染 TodoWriteTool 推送的 todo_update 事件。
 *  同会话新 TODO 由 useChatStore 直接覆盖 message.todos，本组件只做纯展示。
 *  传入 onClear 时显示"清空"按钮（两次点击确认）。 */
export default function TodoCard({ todos, onClear }: {
  todos: TodoItem[];
  onClear?: () => Promise<unknown> | void;
}) {
  const { t } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
  }, []);

  // LLM 偶尔会生成 content 为空的 todo；过滤掉避免计数和显示不一致。
  const visibleTodos = todos.filter((todo) => typeof todo.content === "string" && todo.content.trim() !== "");
  if (visibleTodos.length === 0) return null;
  const completed = countCompleted(visibleTodos);
  const progress = Math.round((completed / visibleTodos.length) * 100);
  const groups = groupTodos(visibleTodos);
  let rowIndex = 0;

  const handleClear = async () => {
    if (!onClear || clearing) return;
    if (!confirming) {
      setConfirming(true);
      if (confirmTimer.current) clearTimeout(confirmTimer.current);
      confirmTimer.current = setTimeout(() => setConfirming(false), 3000);
      return;
    }
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setConfirming(false);
    setClearing(true);
    try {
      await onClear();
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="todo-card">
      <div className="todo-card-header">
        <span className="todo-card-title">
          <ListIcon />
          {t('todoCardTitle')}
        </span>
        <div className="todo-card-header-right">
          <span className="todo-card-progress">{completed}/{visibleTodos.length} {t('todoCompletedSuffix')}</span>
          {onClear ? (
            <button
              type="button"
              className={`todo-clear-btn${confirming ? " confirming" : ""}`}
              onClick={() => void handleClear()}
              disabled={clearing}
              title={confirming ? t('todoClearConfirm') : t('todoClear')}
            >
              {clearing ? t('todoClearing') : confirming ? t('todoClearConfirm') : t('todoClear')}
            </button>
          ) : null}
        </div>
      </div>
      <div className="todo-progress-bar">
        <div className="todo-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="todo-list">
        {groups.map((group) => (
          <div className="todo-group" key={group.status}>
            <div className="todo-group-label">
              {t(STATUS_LABEL_KEYS[group.status])}
              <span className="todo-group-count">{group.items.length}</span>
            </div>
            {group.items.map((todo) => {
              const index = rowIndex++;
              return <TodoItemRow key={todo.id || `todo-${index}`} todo={todo} index={index} t={t} />;
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function countCompleted(todos: TodoItem[]): number {
  return todos.filter((t) => t.status === "completed").length;
}

function TodoItemRow({ todo, index, t }: {
  todo: TodoItem;
  index: number;
  t: (key: string, fallback?: string) => string;
}) {
  const priority = todo.priority ?? "medium";
  return (
    <div className={`todo-item status-${todo.status}`}>
      <span className="todo-number">{String(index + 1).padStart(2, "0")}</span>
      <TodoStatusIcon status={todo.status} />
      <span className={`todo-content${todo.status === "completed" ? " done" : ""}`}>
        {todo.content}
      </span>
      <span className={`todo-priority priority-${priority}`}>{t(PRIORITY_LABEL_KEYS[priority])}</span>
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
