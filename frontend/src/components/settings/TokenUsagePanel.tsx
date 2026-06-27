/**
 * Token Usage Panel — displays token usage statistics with animated charts.
 *
 * Features:
 * - Daily token usage (input/output) over last 30 days
 * - Toggle between line chart and bar chart
 * - Per-model token breakdown
 * - Animated SVG with CSS transitions
 */
import { useState, useEffect, useMemo, useRef } from "react";
import { apiGet } from "../../api/client";
import { useI18n } from "../../i18n";

interface DailyEntry {
  date: string;
  input: number;
  output: number;
  total: number;
}

interface ModelEntry {
  model: string;
  input: number;
  output: number;
  total: number;
  calls: number;
}

interface TokenStats {
  daily: DailyEntry[];
  by_model: ModelEntry[];
  summary: {
    total_input: number;
    total_output: number;
    total_tokens: number;
    days: number;
  };
}

type ChartType = "line" | "bar";

const CHART_W = 720;
const CHART_H = 240;
const CHART_PAD = { top: 20, right: 20, bottom: 30, left: 50 };

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toString();
}

export function TokenUsagePanel() {
  const { t } = useI18n();
  const [stats, setStats] = useState<TokenStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chartType, setChartType] = useState<ChartType>("line");
  const [days, setDays] = useState(30);
  const [animated, setAnimated] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [pathLength, setPathLength] = useState(2000);
  const abortRef = useRef<AbortController | null>(null);
  const inputPathRef = useRef<SVGPathElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    apiGet<TokenStats>(`token-usage/stats?days=${days}`)
      .then((data) => {
        if (abortRef.current !== controller) return;
        setStats(data);
        setAnimated(false);
        // Trigger animation after render
        requestAnimationFrame(() => requestAnimationFrame(() => setAnimated(true)));
      })
      .catch((e) => {
        if (abortRef.current !== controller) return;
        setStats(null);
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (abortRef.current !== controller) return;
        setLoading(false);
      });
    return () => { controller.abort(); abortRef.current = null; };
  }, [days, reloadKey]);

  const daily = stats?.daily ?? [];
  const byModel = stats?.by_model ?? [];
  const summary = stats?.summary;

  // Chart geometry
  const chartW = CHART_W - CHART_PAD.left - CHART_PAD.right;
  const chartH = CHART_H - CHART_PAD.top - CHART_PAD.bottom;

  const maxTokens = useMemo(() => {
    if (daily.length === 0) return 100;
    return Math.max(...daily.map((d) => Math.max(d.input, d.output)), 100);
  }, [daily]);

  // Compute per-model max once (avoid O(n²) recomputation inside map)
  const maxModelTokens = useMemo(() => Math.max(...byModel.map((m) => m.total), 1), [byModel]);

  // Generate x positions for each day
  const xStep = daily.length > 1 ? chartW / (daily.length - 1) : chartW;
  const xPos = (i: number) => CHART_PAD.left + i * xStep;
  const yPos = (val: number) => CHART_PAD.top + chartH - (val / maxTokens) * chartH;

  // Build SVG path for line chart
  const inputPath = useMemo(() => {
    if (daily.length === 0) return "";
    return daily.map((d, i) => `${i === 0 ? "M" : "L"} ${xPos(i)} ${yPos(d.input)}`).join(" ");
  }, [daily, maxTokens]);

  const outputPath = useMemo(() => {
    if (daily.length === 0) return "";
    return daily.map((d, i) => `${i === 0 ? "M" : "L"} ${xPos(i)} ${yPos(d.output)}`).join(" ");
  }, [daily, maxTokens]);

  // Measure actual SVG path length for accurate stroke-dasharray animation
  useEffect(() => {
    const len = inputPathRef.current?.getTotalLength?.();
    if (len && len > 0) {
      setPathLength(len);
    }
  }, [inputPath, outputPath, chartType]);

  // Y axis ticks
  const yTicks = useMemo(() => {
    const ticks: { val: number; y: number }[] = [];
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const val = (maxTokens / steps) * i;
      ticks.push({ val, y: yPos(val) });
    }
    return ticks;
  }, [maxTokens]);

  // X axis labels (show every N days to avoid crowding)
  const xLabelInterval = Math.max(1, Math.floor(daily.length / 6));

  // Colors
  const inputColor = "var(--primary, #4f7fff)";
  const outputColor = "#16a085";

  if (loading) {
    return (
      <div className="settings-section">
        <h3>{t('tokenUsage')}</h3>
        <div style={{ textAlign: "center", padding: 40, color: "var(--muted-fg)" }}>...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="settings-section">
        <h3>{t('tokenUsage')}</h3>
        <div style={{ textAlign: "center", padding: 40, color: "#e74c3c", fontSize: 13 }}>
          {error}
          <div style={{ marginTop: 12 }}>
            <button className="verify-btn" onClick={() => setReloadKey((k) => k + 1)}>{t('retry')}</button>
          </div>
        </div>
      </div>
    );
  }

  if (!stats || daily.length === 0) {
    return (
      <div className="settings-section">
        <h3>{t('tokenUsage')}</h3>
        <div style={{ textAlign: "center", padding: 40, color: "var(--muted-fg)", fontSize: 13 }}>
          {t('noTokenData')}
        </div>
      </div>
    );
  }

  return (
    <div className="settings-section">
      <h3 style={{ fontSize: 16, fontWeight: 500, marginBottom: 18 }}>{t('tokenUsage')}</h3>

      {/* ─── Summary cards ─── */}
      <div className="stats-grid" style={{ marginBottom: 24 }}>
        <div className="stat-card">
          <div className="stat-label">{t('inputTokens')}</div>
          <div className="stat-value" style={{ color: inputColor }}>{formatTokens(summary?.total_input ?? 0)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">{t('outputTokens')}</div>
          <div className="stat-value" style={{ color: outputColor }}>{formatTokens(summary?.total_output ?? 0)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">{t('totalTokens')}</div>
          <div className="stat-value">{formatTokens(summary?.total_tokens ?? 0)}</div>
        </div>
      </div>

      {/* ─── Chart controls ─── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className={`verify-btn ${chartType === "line" ? "primary" : ""}`}
            onClick={() => { setChartType("line"); setAnimated(false); requestAnimationFrame(() => requestAnimationFrame(() => setAnimated(true))); }}
            style={chartType === "line" ? { background: "var(--primary)", color: "white" } : {}}
          >
            📈 {t('lineChart')}
          </button>
          <button
            className={`verify-btn ${chartType === "bar" ? "primary" : ""}`}
            onClick={() => { setChartType("bar"); setAnimated(false); requestAnimationFrame(() => requestAnimationFrame(() => setAnimated(true))); }}
            style={chartType === "bar" ? { background: "var(--primary)", color: "white" } : {}}
          >
            📊 {t('barChart')}
          </button>
        </div>
        <select
          className="form-select"
          value={days}
          onChange={(e) => setDays(parseInt(e.target.value))}
          style={{ width: "auto", fontSize: 12 }}
        >
          <option value={7}>7 {t('daysUnit')}</option>
          <option value={14}>14 {t('daysUnit')}</option>
          <option value={30}>30 {t('daysUnit')}</option>
        </select>
      </div>

      {/* ─── Chart ─── */}
      <div className="chart-card" style={{ padding: 16, overflow: "hidden" }}>
        <svg width="100%" height={CHART_H} viewBox={`0 0 ${CHART_W} ${CHART_H}`} preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
          {/* Y axis grid lines + labels */}
          {yTicks.map((tick, i) => (
            <g key={i}>
              <line
                x1={CHART_PAD.left} y1={tick.y}
                x2={CHART_W - CHART_PAD.right} y2={tick.y}
                stroke="var(--border)" strokeWidth={1} strokeDasharray="3 3"
              />
              <text
                x={CHART_PAD.left - 8} y={tick.y + 4}
                textAnchor="end" fontSize={10} fill="var(--muted-fg)"
              >
                {formatTokens(tick.val)}
              </text>
            </g>
          ))}

          {/* X axis labels */}
          {daily.map((d, i) => {
            if (i % xLabelInterval !== 0 && i !== daily.length - 1) return null;
            const date = d.date.slice(5); // MM-DD
            return (
              <text
                key={i} x={xPos(i)} y={CHART_H - CHART_PAD.bottom + 16}
                textAnchor="middle" fontSize={10} fill="var(--muted-fg)"
              >
                {date}
              </text>
            );
          })}

          {/* Line chart */}
          {chartType === "line" && (
            <>
              {/* Input line */}
              <path
                ref={inputPathRef}
                d={inputPath}
                fill="none" stroke={inputColor} strokeWidth={2}
                strokeLinejoin="round" strokeLinecap="round"
                style={{
                  strokeDasharray: pathLength,
                  strokeDashoffset: animated ? 0 : pathLength,
                  transition: "stroke-dashoffset 1.2s ease-in-out",
                }}
              />
              {/* Output line */}
              <path
                d={outputPath}
                fill="none" stroke={outputColor} strokeWidth={2}
                strokeLinejoin="round" strokeLinecap="round"
                style={{
                  strokeDasharray: 2000,
                  strokeDashoffset: animated ? 0 : 2000,
                  transition: "stroke-dashoffset 1.2s ease-in-out 0.2s",
                }}
              />
              {/* Data points */}
              {daily.map((d, i) => (
                <g key={i}>
                  <circle
                    cx={xPos(i)} cy={yPos(d.input)} r={3}
                    fill={inputColor}
                    style={{
                      opacity: animated ? 1 : 0,
                      transition: `opacity 0.3s ease ${0.8 + i * 0.02}s`,
                    }}
                  />
                  <circle
                    cx={xPos(i)} cy={yPos(d.output)} r={3}
                    fill={outputColor}
                    style={{
                      opacity: animated ? 1 : 0,
                      transition: `opacity 0.3s ease ${0.8 + i * 0.02}s`,
                    }}
                  />
                </g>
              ))}
            </>
          )}

          {/* Bar chart */}
          {chartType === "bar" && (
            <>
              {daily.map((d, i) => {
                const barW = Math.min(28, Math.max(4, xStep * 0.28));
                const gap = 2;
                const inputH = (d.input / maxTokens) * chartH;
                const outputH = (d.output / maxTokens) * chartH;
                return (
                  <g key={i}>
                    {/* Input bar */}
                    <rect
                      x={xPos(i) - barW - gap / 2} y={CHART_PAD.top + chartH - (animated ? inputH : 0)}
                      width={barW} height={animated ? inputH : 0}
                      fill={inputColor} rx={3}
                      style={{ transition: `all 0.6s ease ${i * 0.02}s` }}
                    />
                    {/* Output bar */}
                    <rect
                      x={xPos(i) + gap / 2} y={CHART_PAD.top + chartH - (animated ? outputH : 0)}
                      width={barW} height={animated ? outputH : 0}
                      fill={outputColor} rx={3}
                      style={{ transition: `all 0.6s ease ${i * 0.02 + 0.1}s` }}
                    />
                  </g>
                );
              })}
            </>
          )}

          {/* Legend */}
          <g transform={`translate(${CHART_W - CHART_PAD.right - 120}, ${CHART_PAD.top - 8})`}>
            <rect x={0} y={0} width={10} height={10} fill={inputColor} rx={2} />
            <text x={14} y={9} fontSize={11} fill="var(--fg)">{t('inputTokens')}</text>
            <rect x={0} y={16} width={10} height={10} fill={outputColor} rx={2} />
            <text x={14} y={25} fontSize={11} fill="var(--fg)">{t('outputTokens')}</text>
          </g>
        </svg>
      </div>

      {/* ─── Per-model breakdown ─── */}
      {byModel.length > 0 && (
        <div className="chart-card" style={{ marginTop: 16, padding: 16 }}>
          <div className="chart-title" style={{ marginBottom: 12 }}>{t('modelDistribution')}</div>
          {byModel.map((row) => {
            const pct = (row.total / maxModelTokens) * 100;
            return (
              <div className="model-bar-row" key={row.model}>
                <span className="model-bar-name" style={{ fontSize: 12 }}>{row.model}</span>
                <div className="model-bar-track">
                  <div
                    className="model-bar-fill"
                    style={{
                      width: animated ? `${pct}%` : "0%",
                      transition: "width 0.8s ease",
                      background: `linear-gradient(90deg, ${inputColor}, ${outputColor})`,
                    }}
                  />
                </div>
                <span className="model-bar-tokens" style={{ fontSize: 11 }}>
                  {formatTokens(row.total)} · {row.calls} {t('calls')}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
