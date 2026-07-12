import { useEffect, useState } from "react";
import { apiGet } from "../../api/client";

// v3-T3: Agent tool-call audit log panel.

interface AuditLog {
  id: number;
  timestamp: string | null;
  session_id: string | null;
  tool_name: string;
  args_summary: string;
  decision: string;
  decision_source: string;
  risk_level: string;
  exit_code: number | null;
  duration_ms: number | null;
  error: string | null;
}

interface AuditResponse {
  logs: AuditLog[];
  count: number;
}

const DEFAULT_LIMIT = 50;
const TIME_FMT: Intl.DateTimeFormatOptions = {
  hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
};

const DECISION_LABEL: Record<string, string> = {
  allow: "允许", ask: "询问", deny: "拒绝", executed: "已执行",
};

/** Risk badge — reuse .risk-badge risk-${level} from chat.css. */
function RiskBadge({ level }: { level: string }) {
  if (!level || level === "n/a") return null;
  const cls = ["low", "medium", "high"].includes(level) ? level : "low";
  return <span className={`risk-badge risk-${cls}`}>{level.toUpperCase()}</span>;
}

/** Decision badge with color coding. */
function DecisionBadge({ decision }: { decision: string }) {
  const label = DECISION_LABEL[decision] || decision;
  return <span className={`audit-decision audit-decision-${decision}`}>{label}</span>;
}

/** One audit log row. */
function LogRow({ log }: { log: AuditLog }) {
  const ts = log.timestamp ? new Date(log.timestamp).toLocaleString("zh-CN", TIME_FMT) : "—";
  return (
    <div className="audit-row">
      <div className="audit-row-header">
        <span className="audit-ts">{ts}</span>
        <span className="audit-tool">{log.tool_name}</span>
        <DecisionBadge decision={log.decision} />
        <RiskBadge level={log.risk_level} />
        {log.duration_ms != null && (
          <span className="audit-duration">{log.duration_ms}ms</span>
        )}
        {log.exit_code != null && (
          <span className={`audit-exit${log.exit_code === 0 ? "" : " error"}`}>
            exit={log.exit_code}
          </span>
        )}
      </div>
      {log.args_summary && (
        <pre className="audit-args">{log.args_summary}</pre>
      )}
      {log.error && (
        <pre className="audit-error">{log.error}</pre>
      )}
    </div>
  );
}

/** Filter controls. */
function FilterBar({ filter, setFilter, onRefresh }: {
  filter: { session_id: string; tool_name: string; decision: string; risk_level: string };
  setFilter: (f: typeof filter) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="audit-filter-bar">
      <input
        className="audit-filter-input"
        placeholder="按 session_id 过滤"
        value={filter.session_id}
        onChange={(e) => setFilter({ ...filter, session_id: e.target.value })}
      />
      <input
        className="audit-filter-input"
        placeholder="按 tool_name 过滤"
        value={filter.tool_name}
        onChange={(e) => setFilter({ ...filter, tool_name: e.target.value })}
      />
      <select
        className="audit-filter-select"
        value={filter.decision}
        onChange={(e) => setFilter({ ...filter, decision: e.target.value })}
      >
        <option value="">全部决策</option>
        <option value="allow">允许</option>
        <option value="ask">询问</option>
        <option value="deny">拒绝</option>
        <option value="executed">已执行</option>
      </select>
      <select
        className="audit-filter-select"
        value={filter.risk_level}
        onChange={(e) => setFilter({ ...filter, risk_level: e.target.value })}
      >
        <option value="">全部风险</option>
        <option value="low">LOW</option>
        <option value="medium">MEDIUM</option>
        <option value="high">HIGH</option>
      </select>
      <button className="audit-refresh-btn" onClick={onRefresh}>刷新</button>
    </div>
  );
}

export function AuditLogPanel() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState({
    session_id: "", tool_name: "", decision: "", risk_level: "",
  });

  const fetchLogs = async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(DEFAULT_LIMIT));
      if (filter.session_id) params.set("session_id", filter.session_id);
      if (filter.decision) params.set("decision", filter.decision);
      if (filter.risk_level) params.set("risk_level", filter.risk_level);
      if (filter.tool_name) params.set("tool_name", filter.tool_name);
      const data = await apiGet<AuditResponse>(`agent-sandbox/audit?${params.toString()}`);
      setLogs(data.logs || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  // Auto-load on mount so the panel isn't empty until user clicks refresh.
  useEffect(() => {
    fetchLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="settings-section">
      <h3>Agent 工具调用审计</h3>
      <p style={{ fontSize: 13, color: "var(--muted-fg)", marginBottom: 12 }}>
        记录每次 Agent 工具调用的决策、风险等级、执行结果。日志保留 30 天。
      </p>
      <FilterBar filter={filter} setFilter={setFilter} onRefresh={fetchLogs} />
      {error && <div className="audit-error-msg">{error}</div>}
      <div className="audit-list">
        {loading ? (
          <div className="audit-empty">加载中…</div>
        ) : logs.length === 0 ? (
          <div className="audit-empty">暂无审计日志，点击"刷新"加载</div>
        ) : (
          logs.map((log) => <LogRow key={log.id} log={log} />)
        )}
      </div>
    </div>
  );
}
