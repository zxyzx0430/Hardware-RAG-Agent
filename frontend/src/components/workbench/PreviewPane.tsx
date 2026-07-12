import { useEffect, useMemo, useState, useRef, useCallback } from "react";
import MonacoEditor from "@monaco-editor/react";
import { useAppStore } from "../../stores/useAppStore";
import { useLogStore } from "../../stores/useLogStore";
import { useWorkbenchBridge } from "../../stores/useWorkbenchBridge";
import { useI18n } from "../../i18n";
import { copyToClipboard } from "../../utils/clipboard";
import { apiPost } from "../../api/client";
import type { DiagnoseItem, DiagnoseResponse } from "../../types/api";
import { CODE } from "./workbenchConstants";
import { useAppliedDarkMode } from "../shared/MarkdownRenderer";

// Map internal language tags to Monaco language ids.
const mapLanguage = (lang: string): string => {
  switch (lang) {
    case "cpp":
    case "c++":
    case "arduino":
      return "cpp";
    case "python":
    case "py":
      return "python";
    case "javascript":
    case "js":
    case "ts":
    case "typescript":
      return "javascript";
    default:
      return "cpp";
  }
};

export function PreviewPane() {
  const { t } = useI18n();
  const { previewTabs, activePreviewTabId, addPreviewTab, removePreviewTab, setActivePreviewTabId, updatePreviewTabCode, setWbTab, setFlashCode, flashBoard, themeMode } = useAppStore();
  const isDark = useAppliedDarkMode();
  const editorTheme = themeMode === "dark" || (themeMode === "auto" && isDark) ? "vs-dark" : "vs-light";
  const [diagnostics, setDiagnostics] = useState<DiagnoseItem[] | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);

  // Subscribe to Agent render_code pushes from the workbench bridge.
  const codeRenderData = useWorkbenchBridge((s) => s.codeRenderData);
  const setCodeRenderData = useWorkbenchBridge((s) => s.setCodeRenderData);
  const lastAgentCodeRef = useRef<string>("");

  useEffect(() => {
    if (!codeRenderData) return;
    // Dedupe by code content — guards against StrictMode double-fire and
    // re-renders that don't actually change the data.
    if (codeRenderData.code === lastAgentCodeRef.current) return;
    lastAgentCodeRef.current = codeRenderData.code;

    const newTabId = `agent-${Date.now()}`;
    addPreviewTab({
      id: newTabId,
      label: `agent_generated.${codeRenderData.language || "txt"}`,
      code: codeRenderData.code,
      language: codeRenderData.language,
    });
    setActivePreviewTabId(newTabId);
    // Clear the bridge channel so future pushes (even with the same content)
    // still trigger this effect via the null -> data transition.
    setCodeRenderData(null);
  }, [codeRenderData, addPreviewTab, setActivePreviewTabId, setCodeRenderData]);

  const fallbackTab = useMemo(
    () => ({ id: "default-preview", label: "main.cpp", code: CODE, language: "cpp" }),
    [],
  );

  const tabs = previewTabs.length ? previewTabs : [fallbackTab];
  const activeTab = tabs.find((tab) => tab.id === activePreviewTabId) || tabs.at(-1) || fallbackTab;

  const handleCopyCode = useCallback(() => {
    copyToClipboard(activeTab.code);
  }, [activeTab.code]);

  const handlePushToFlash = useCallback(() => {
    // 将当前预览的代码传递到 Flash 面板
    setFlashCode(activeTab.code);
    setWbTab("flash");
  }, [activeTab.code, setWbTab]);

  const handleDiagnose = useCallback(async () => {
    if (diagnosing) return;
    setDiagnosing(true);
    setDiagnostics(null);
    try {
      const res = await apiPost<DiagnoseResponse>("diagnose", { code: activeTab.code, chip: flashBoard });
      setDiagnostics(res.results ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("error", "preview", `诊断失败: ${msg}`);
    } finally {
      setDiagnosing(false);
    }
  }, [diagnosing, activeTab.code, flashBoard]);

  const statusColor = (status: DiagnoseItem["status"]) => {
    if (status === "PASS") return "var(--success)";
    if (status === "WARN") return "var(--warn)";
    return "var(--danger)";
  };

  return (
    <div className="code-preview-editor" style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%' }}>
      <div className="code-preview-toolbar">
        {tabs.map((tab) => {
          const isGenerated = tab.id !== fallbackTab.id;
          return (
            <span
              key={tab.id}
              className={`code-preview-file-tab${activeTab.id === tab.id ? ' active' : ''}`}
              onClick={() => setActivePreviewTabId(tab.id)}
            >
              {tab.label}
              {isGenerated ? (
                <button
                  type="button"
                  className="tab-close"
                  onClick={(event) => {
                    event.stopPropagation();
                    removePreviewTab(tab.id);
                  }}
                >
                  ×
                </button>
              ) : null}
            </span>
          );
        })}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          <button
            type="button"
            className="flash-btn"
            onClick={handleCopyCode}
          >
            {t('copy')}
          </button>
          <button
            type="button"
            className="flash-btn"
            onClick={handlePushToFlash}
          >
            {t('pushToFlash')}
          </button>
          <button
            type="button"
            className="flash-btn"
            onClick={handleDiagnose}
            disabled={diagnosing}
          >
            {diagnosing ? t('diagnosing') : t('buildDiagnose')}
          </button>
        </span>
      </div>
      {diagnostics && (
        <div style={{
          borderBottom: "1px solid var(--border)",
          padding: "6px 12px",
          background: "var(--card)",
          fontSize: 11,
        }}>
          {diagnostics.map((d, idx) => (
            <div key={idx} style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0" }}>
              <span style={{
                fontWeight: 600,
                color: statusColor(d.status),
                minWidth: 40,
              }}>
                {d.status}
              </span>
              <span style={{ color: "var(--fg)" }}>{d.name}</span>
              {d.detail && (
                <span style={{ color: statusColor(d.status), marginLeft: 4 }}>
                  — {d.detail}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="code-preview-editor" style={{ flex: 1, minHeight: 0 }}>
        <MonacoEditor
          language={mapLanguage(activeTab.language || "cpp")}
          value={activeTab.code}
          onChange={(value) => updatePreviewTabCode(activeTab.id, value || "")}
          theme={editorTheme}
          height="100%"
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            wordWrap: "on",
            tabSize: 2,
            automaticLayout: true,
          }}
        />
      </div>
    </div>
  );
}
