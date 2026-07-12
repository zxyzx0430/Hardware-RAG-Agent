import { useEffect, useId, useState } from "react";
import { useChatStore } from "../../stores/useChatStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useI18n } from "../../i18n";
import { formatDuration } from "../../utils/format";
import { apiGet, ApiError } from "../../api/client";

interface DailyStat {
  date: string;
  input: number;
  output: number;
  total: number;
}

interface ModelStat {
  model: string;
  input: number;
  output: number;
  total: number;
  calls: number;
}

interface TokenStats {
  daily: DailyStat[];
  by_model: ModelStat[];
  summary: {
    total_input: number;
    total_output: number;
    total_tokens: number;
    days: number;
  };
}

/** 简易 SVG 折线图 */
function Sparkline({ data, color, height = 40 }: { data: number[]; color: string; height?: number }) {
  const gradientId = useId().replace(/:/g, "");
  if (data.length < 2) return null;
  const max = Math.max(...data, 1);
  const w = 240;
  const step = w / (data.length - 1);
  const points = data.map((v, i) => `${i * step},${height - (v / max) * (height - 4) - 2}`).join(" ");
  const areaPoints = `0,${height} ${points} ${w},${height}`;
  return (
    <svg width={w} height={height} style={{ display: "block" }}>
      <defs>
        <linearGradient id={`grad-${gradientId}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0.05" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={`url(#grad-${gradientId})`} />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      {data.map((v, i) => (
        <circle key={i} cx={i * step} cy={height - (v / max) * (height - 4) - 2} r="2" fill={color} />
      ))}
    </svg>
  );
}

export function StatsPanel() {
  const { t } = useI18n();
  const { messages, statsOpen, hideStats, activeSessionId } = useChatStore();
  const { chatModel } = useSettingsStore();
  const sessions = useSessionStore((s) => s.sessions);
  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const model = activeSession?.model || chatModel;
  const [stats, setStats] = useState<TokenStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    if (!statsOpen) return;
    let cancelled = false;

    async function loadStats() {
      setLoading(true);
      setError(null);
      try {
        const data = await apiGet<TokenStats>(
          `token-usage/stats?session_id=${encodeURIComponent(activeSessionId)}`
        );
        if (!cancelled) setStats(data);
      } catch (err) {
        if (cancelled) return;
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : String(err);
        setError(`统计加载失败：${msg}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadStats();
    return () => {
      cancelled = true;
    };
  }, [statsOpen, activeSessionId, retryCount]);

  if (!statsOpen) return null;

  const userMsgs = messages.filter((m) => m.role === "user").length;
  const assistantMsgs = messages.filter((m) => m.role === "assistant").length;
  const srcCount = messages.reduce((sum, m) => sum + (m.sources?.length ?? 0), 0);
  const totalDur = messages.reduce((a, m) => a + (m.activity?.durationMs ?? 0), 0);

  const hasRealData = stats && stats.summary.total_tokens > 0;
  const inputTokens = stats?.summary.total_input ?? 0;
  const outputTokens = stats?.summary.total_output ?? 0;
  const totalTokens = stats?.summary.total_tokens ?? 0;
  const inputSeries = stats?.daily.map((d) => d.input) ?? [];
  const outputSeries = stats?.daily.map((d) => d.output) ?? [];

  const baseStats = [
    [t("statsMsgCount"), messages.length + ""],
    [t("statsUserMsgs"), userMsgs + ""],
    [t("statsAiMsgs"), assistantMsgs + ""],
    [t("statsSources"), srcCount + ""],
    [t("statsDuration"), formatDuration(totalDur)],
    [t("statsModel"), model],
  ];

  return (
    <>
      <div className="dropdown-backdrop" onClick={hideStats} />
      <div
        className="stats-panel"
        style={{
          position: "fixed",
          top: 60,
          right: 20,
          zIndex: 9999,
          width: 300,
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          boxShadow: "0 8px 24px rgba(0,0,0,0.15)",
          maxHeight: "80vh",
          overflowY: "auto",
        }}
      >
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", fontWeight: 600, fontSize: 13 }}>
          {t("chatStats")}
        </div>

        <div style={{ padding: 8 }}>
          {baseStats.map(([label, value]) => (
            <div
              key={label}
              style={{
                display: "flex",
                justifyContent: "space-between",
                padding: "6px 8px",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>{label}</span>
              <span style={{ fontSize: 13, fontWeight: 600, fontFamily: "var(--font-mono)" }}>{value}</span>
            </div>
          ))}
        </div>

        {loading && (
          <div style={{ padding: "12px 16px", fontSize: 12, color: "var(--muted-fg)", textAlign: "center" }}>
            加载中...
          </div>
        )}

        {error && (
          <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)" }}>
            <div style={{ fontSize: 12, color: "var(--error-fg, #ef4444)", marginBottom: 8 }}>{error}</div>
            <button
              onClick={() => setRetryCount((c) => c + 1)}
              style={{
                padding: "4px 12px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--card)",
                fontSize: 12,
                cursor: "pointer",
              }}
            >
              重试
            </button>
          </div>
        )}

        {!loading && !error && !hasRealData && (
          <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)" }}>
            <div style={{ fontSize: 12, color: "var(--muted-fg)", fontStyle: "italic" }}>
              暂无真实用量数据，发送消息并等待 LLM 返回 usage 后可查看
            </div>
          </div>
        )}

        {!loading && !error && hasRealData && (
          <>
            <div style={{ padding: 8, borderTop: "1px solid var(--border)" }}>
              {[
                ["Input Tokens", inputTokens.toLocaleString()],
                ["Output Tokens", outputTokens.toLocaleString()],
                [t("statsTokens"), totalTokens.toLocaleString() + " tokens"],
              ].map(([label, value]) => (
                <div
                  key={label}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    padding: "6px 8px",
                    borderBottom: "1px solid var(--border)",
                  }}
                >
                  <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>{label}</span>
                  <span style={{ fontSize: 13, fontWeight: 600, fontFamily: "var(--font-mono)" }}>{value}</span>
                </div>
              ))}
            </div>

            <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Token Usage</div>
              <div style={{ display: "flex", gap: 16, marginBottom: 8 }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 10, color: "var(--muted-fg)", marginBottom: 4 }}>
                    Input: {inputTokens.toLocaleString()}
                  </div>
                  <Sparkline data={inputSeries} color="var(--chart-input)" />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 10, color: "var(--muted-fg)", marginBottom: 4 }}>
                    Output: {outputTokens.toLocaleString()}
                  </div>
                  <Sparkline data={outputSeries} color="var(--chart-output)" />
                </div>
              </div>
            </div>

            {stats.by_model.length > 0 && (
              <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)" }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>By Model</div>
                {stats.by_model.map((m) => (
                  <div
                    key={m.model}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      padding: "4px 0",
                      fontSize: 11,
                      borderBottom: "1px solid var(--border)",
                    }}
                  >
                    <span style={{ color: "var(--muted-fg)", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {m.model}
                    </span>
                    <span style={{ fontFamily: "var(--font-mono)" }}>
                      {m.calls} 次 · {m.total.toLocaleString()} tokens
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        <div style={{ padding: "8px 16px", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "flex-end" }}>
          <button
            onClick={hideStats}
            style={{
              padding: "4px 12px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--card)",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {t("close")}
          </button>
        </div>
      </div>
    </>
  );
}
