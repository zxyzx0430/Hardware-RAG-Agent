import { useState, useCallback, useEffect, useRef } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useLogStore } from "../../stores/useLogStore";
import { useWorkbenchBridge } from "../../stores/useWorkbenchBridge";
import { useWiringStore } from "../../stores/useWiringStore";
import { useI18n } from "../../i18n";
import { apiPost } from "../../api/client";
import type { PinWarning, DiagnoseResponse, DiagnoseItem } from "../../types/api";

interface PinAllocation {
  pin: string;
  mode: string;
  source: string;
  status: "safe" | "warning" | "conflict";
}

interface StrappingConflict {
  pin: string;
  conflictType: string;
  description: string;
  suggestion: string;
}

function pinWarningToAllocation(w: PinWarning): PinAllocation {
  return {
    pin: w.pin,
    mode: "",
    source: "code",
    status: w.severity === "critical" ? "conflict" : "warning",
  };
}

function pinWarningToStrappingConflict(w: PinWarning): StrappingConflict {
  return {
    pin: w.pin,
    conflictType: w.severity === "critical" ? "严重冲突" : "警告",
    description: w.message,
    suggestion: w.suggestion,
  };
}

// ─── Agent 推送路径（render_safety_report）────────────────────────────────
// render_data 形状与后端 audit_pins_core 返回值一致：
//   { safe, conflicts: PinWarning[], warnings: PinWarning[], pin_map: {pin: "GPIOx"} }
// 注意：pin_map value 是字符串 "GPIOx"，与手动路径 audit_pins 路由的 dict 结构不同。

interface AgentSafetyRenderData {
  safe: boolean;
  conflicts: PinWarning[];
  warnings: PinWarning[];
  pin_map: Record<string, string>;
}

function coerceAgentSafetyData(raw: unknown): AgentSafetyRenderData | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.safe !== "boolean") return null;
  return {
    safe: r.safe,
    conflicts: (r.conflicts as PinWarning[]) ?? [],
    warnings: (r.warnings as PinWarning[]) ?? [],
    pin_map: (r.pin_map as Record<string, string>) ?? {},
  };
}

function agentDataToAllocations(data: AgentSafetyRenderData): PinAllocation[] {
  const fromPinMap: PinAllocation[] = Object.entries(data.pin_map ?? {}).map(([pin, gpio]) => ({
    pin,
    mode: typeof gpio === "string" ? gpio : "",
    source: "agent",
    status: "safe" as const,
  }));
  const fromWarnings = (data.warnings ?? []).map(pinWarningToAllocation);
  const fromConflicts = (data.conflicts ?? []).map(pinWarningToAllocation);
  return [...fromPinMap, ...fromWarnings, ...fromConflicts];
}

function agentDataToConflicts(data: AgentSafetyRenderData): StrappingConflict[] {
  const fromConflicts = (data.conflicts ?? []).map(pinWarningToStrappingConflict);
  const fromWarnings = (data.warnings ?? []).map(pinWarningToStrappingConflict);
  return [...fromConflicts, ...fromWarnings];
}

// Agent 推送优先：先放 incoming，再放 existing（不覆盖）。
function mergeAllocationsByPin(existing: PinAllocation[], incoming: PinAllocation[]): PinAllocation[] {
  const seen = new Map<string, PinAllocation>();
  for (const a of incoming) seen.set(a.pin, a);
  for (const a of existing) if (!seen.has(a.pin)) seen.set(a.pin, a);
  return Array.from(seen.values());
}

function mergeConflictsByPin(existing: StrappingConflict[], incoming: StrappingConflict[]): StrappingConflict[] {
  const seen = new Map<string, StrappingConflict>();
  for (const c of incoming) seen.set(c.pin, c);
  for (const c of existing) if (!seen.has(c.pin)) seen.set(c.pin, c);
  return Array.from(seen.values());
}

export function SafetyPane() {
  const { t } = useI18n();
  const { previewTabs, activePreviewTabId, flashBoard } = useAppStore();
  const [checking, setChecking] = useState(false);
  const [verified, setVerified] = useState(false);
  const [pinAllocations, setPinAllocations] = useState<PinAllocation[]>([]);
  const [strappingConflicts, setStrappingConflicts] = useState<StrappingConflict[]>([]);
  // Diagnose results from /api/diagnose (full-code static scan, replaces front-end regex parsing)
  const [diagnoseResults, setDiagnoseResults] = useState<DiagnoseItem[]>([]);
  // Pin name currently highlighted via state (replaces direct el.style.outline mutation).
  // Set when selectedPin changes; cleared by setTimeout. Drives .safety-highlight CSS.
  const [highlightId, setHighlightId] = useState<string | null>(null);

  // Agent 推送路径：监听 useWorkbenchBridge.safetyRenderData
  const safetyRenderData = useWorkbenchBridge((s) => s.safetyRenderData);
  const lastAgentDataRef = useRef<unknown>(null);
  // Wiring↔Safety cross-linkage (Task 9): conflict pins → WiringPane, selected pin → scroll
  const selectedPin = useWiringStore((s) => s.selectedPin);
  const setConflictPins = useWiringStore((s) => s.setConflictPins);
  const pinCardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  useEffect(() => {
    if (!safetyRenderData || lastAgentDataRef.current === safetyRenderData) return;
    lastAgentDataRef.current = safetyRenderData;
    const data = coerceAgentSafetyData(safetyRenderData);
    if (!data) return;
    const allocations = agentDataToAllocations(data);
    const conflicts = agentDataToConflicts(data);
    setPinAllocations((prev) => mergeAllocationsByPin(prev, allocations));
    setStrappingConflicts((prev) => mergeConflictsByPin(prev, conflicts));
    setVerified(true);
    useLogStore.getState().log("ok", "safety", `Agent 推送审计: ${data.conflicts.length} 冲突, ${data.warnings.length} 警告`);
  }, [safetyRenderData]);

  // Scroll to the diagnose card mentioning selectedPin (from WiringPane component click)
  useEffect(() => {
    if (!selectedPin) return;
    const el = pinCardRefs.current.get(selectedPin);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    // Brief highlight to draw attention (state-driven → .safety-highlight CSS class)
    setHighlightId(selectedPin);
    const timer = setTimeout(() => setHighlightId(null), 2000);
    return () => clearTimeout(timer);
  }, [selectedPin]);

  const handleVerify = useCallback(() => {
    if (checking) return;
    setChecking(true);
    setVerified(false);
    setPinAllocations([]);
    setStrappingConflicts([]);
    setDiagnoseResults([]);
    useLogStore.getState().log("info", "safety", "开始引脚安全审计");

    const activeTab = previewTabs.find((tab) => tab.id === activePreviewTabId);
    const code = activeTab?.code ?? "";

    if (!code.trim()) {
      useLogStore.getState().log("warn", "safety", "无代码可审计");
      setChecking(false);
      return;
    }

    // Reuse backend /api/diagnose for full-code static scan (GPIO safety, strapping,
    // pin conflicts, compile precheck). Front-end regex parsing removed.
    apiPost<DiagnoseResponse>("diagnose", { code, chip: flashBoard })
      .then((res) => {
        const results = res.results ?? [];
        setDiagnoseResults(results);
        // Extract conflict pins (GPIOxx) from FAIL/WARN details for WiringPane highlighting
        const conflictPins = new Set<string>();
        for (const r of results) {
          if (r.status === "PASS") continue;
          const pins = r.detail.match(/GPIO\d+/g) ?? [];
          for (const p of pins) conflictPins.add(p);
        }
        setConflictPins(conflictPins);
        setVerified(true);
        setChecking(false);
        const fails = results.filter((r) => r.status === "FAIL").length;
        const warns = results.filter((r) => r.status === "WARN").length;
        useLogStore.getState().log("ok", "safety", `审计完成: ${fails} 失败, ${warns} 警告`);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        useLogStore.getState().log("error", "safety", `引脚审计失败: ${msg}`);
        setDiagnoseResults([]);
        setConflictPins(new Set());
        setVerified(false);
        setChecking(false);
      });
  }, [checking, previewTabs, activePreviewTabId, flashBoard]);

  const hasConflicts = strappingConflicts.length > 0 || diagnoseResults.some((r) => r.status === "FAIL");
  const hasWarnings = pinAllocations.some((p) => p.status === "warning" || p.status === "conflict") || diagnoseResults.some((r) => r.status === "WARN");
  const allClear = !hasConflicts && !hasWarnings;

  const pinStatusColor = (status: PinAllocation["status"]) => {
    if (status === "safe") return "var(--success)";
    if (status === "warning") return "var(--warn)";
    return "var(--danger)";
  };

  const pinStatusLabel = (status: PinAllocation["status"]) => {
    if (status === "safe") return t('safe');
    if (status === "warning") return t('warning');
    return t('danger');
  };

  const diagnoseStatusColor = (status: DiagnoseItem["status"]) => {
    if (status === "PASS") return "var(--success)";
    if (status === "WARN") return "var(--warn)";
    return "var(--danger)";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%" }}>
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
        <span style={{ fontSize: 12, fontWeight: 500 }}>{t('safetyTitle')}</span>
        <button style={{ marginLeft: 'auto', fontSize: 11, padding: '3px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--card)', cursor: 'pointer', color: 'var(--fg)' }} onClick={handleVerify} disabled={checking}>{checking ? t('safetyVerifying') : t('safetyVerify')}</button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: '8px 12px' }}>
        {!verified && !checking && (
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100%', color: 'var(--muted-fg)', gap: 8 }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            <div style={{ fontSize: 12 }}>{t('safetyDesc')}</div>
          </div>
        )}
        {checking && (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: 'var(--muted-fg)', fontSize: 12 }}>
            {t('verifyingPin')}
          </div>
        )}
        {verified && allClear && (
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100%', gap: 8 }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              <path d="M9 12l2 2 4-4" stroke="var(--success)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--success)" }}>{t('noSafetyIssues')}</div>
          </div>
        )}
        {verified && !allClear && (
          <>
            {/* Diagnose results from /api/diagnose */}
            {diagnoseResults.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--fg)", marginBottom: 6 }}>{t('buildDiagnose')}</div>
                {diagnoseResults.map((d, idx) => {
                  const pins = d.detail.match(/GPIO\d+/g) ?? [];
                  return (
                    <div
                      key={idx}
                      className={highlightId != null && pins.includes(highlightId) ? "safety-highlight" : undefined}
                      ref={(el) => {
                        const m = pinCardRefs.current;
                        for (const p of pins) {
                          if (el) m.set(p, el);
                          else m.delete(p);
                        }
                      }}
                      style={{
                        background: d.status === "PASS" ? "color-mix(in oklab, var(--success), transparent 94%)" : "color-mix(in oklab, var(--danger), transparent 94%)",
                        border: `1px solid ${d.status === "PASS" ? "color-mix(in oklab, var(--success), transparent 75%)" : "color-mix(in oklab, var(--danger), transparent 75%)"}`,
                        borderRadius: 6,
                        padding: "6px 10px",
                        marginBottom: 4,
                        fontSize: 11,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ fontWeight: 600, color: diagnoseStatusColor(d.status), minWidth: 40 }}>{d.status}</span>
                        <span style={{ color: "var(--fg)", fontWeight: 500 }}>{d.name}</span>
                      </div>
                      {d.detail && (
                        <div style={{ color: "var(--muted-fg)", marginTop: 2 }}>{d.detail}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Pin allocation table (populated by agent push path) */}
            {pinAllocations.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--fg)", marginBottom: 6 }}>{t('pinAllocation')}</div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Pin</th>
                      <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Mode</th>
                      <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Source</th>
                      <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pinAllocations.map((p) => (
                      <tr key={p.pin} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{p.pin}</td>
                        <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{p.mode}</td>
                        <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{p.source}</td>
                        <td style={{ padding: "3px 8px", color: pinStatusColor(p.status), fontWeight: 500 }}>{pinStatusLabel(p.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Strapping pin conflict details */}
            {strappingConflicts.length > 0 && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--warn)", marginBottom: 6, display: "flex", alignItems: "center", gap: 4 }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                  {t('strappingConflict')}
                </div>
                {strappingConflicts.map((c) => (
                  <div key={c.pin} style={{
                    background: "color-mix(in oklab, var(--warn), transparent 92%)",
                    border: "1px solid color-mix(in oklab, var(--warn), transparent 75%)",
                    borderRadius: 6,
                    padding: "8px 10px",
                    marginBottom: 6,
                    fontSize: 11,
                  }}>
                    <div style={{ fontWeight: 600, color: "var(--fg)", marginBottom: 4 }}>
                      {c.pin} — {c.conflictType}
                    </div>
                    <div style={{ color: "var(--muted-fg)", marginBottom: 4 }}>{c.description}</div>
                    <div style={{ color: "var(--success)" }}>
                      <span style={{ fontWeight: 500 }}>{t('suggestion')}：</span>{c.suggestion}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
