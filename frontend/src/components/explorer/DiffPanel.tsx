import { useEffect, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { useAppStore } from "../../stores/useAppStore";
import { useI18n } from "../../i18n";
import { useAppliedDarkMode } from "../shared/MarkdownRenderer";
import { apiGet } from "../../api/client";
import { fileLanguage } from "./fileLanguage";
import type { OpenFileItem } from "../../types";

interface DiffPanelProps {
  file: OpenFileItem;
  onClose: () => void;
}

interface DiffResponse {
  base_content: string | null;
  current_content: string;
  base: "git" | null;
  has_changes: boolean | null;
}

type DiffMode = "split" | "inline";

export function DiffPanel({ file, onClose }: DiffPanelProps) {
  const { t } = useI18n();
  const themeMode = useAppStore((s) => s.themeMode);
  const isDark = useAppliedDarkMode();
  const editorTheme =
    themeMode === "dark" || (themeMode === "auto" && isDark) ? "vs-dark" : "vs-light";

  const [mode, setMode] = useState<DiffMode>("split");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DiffResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiGet<DiffResponse>(`explorer/diff?path=${encodeURIComponent(file.path)}`)
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [file.path]);

  const original =
    data?.base === "git" ? (data.base_content ?? "") : (file.snapshot ?? "");
  const modified = file.content ?? "";
  const baseLabel =
    data?.base === "git"
      ? t("diffBaseGit", "Git HEAD")
      : t("diffBaseSnapshot", "打开时快照");

  return (
    <div className="diff-panel" onClick={(e) => e.stopPropagation()}>
      <div className="diff-panel-header">
        <div className="diff-panel-title">
          <span>{t("diffView", "Diff 视图")}</span>
          <span className="diff-panel-file">{file.name}</span>
          <span className="diff-panel-base">{baseLabel}</span>
        </div>
        <div className="diff-panel-actions">
          <button
            className={`diff-panel-toggle${mode === "split" ? " active" : ""}`}
            onClick={() => setMode("split")}
            title={t("diffSplit", "左右对比")}
          >
            {t("diffSplit", "左右对比")}
          </button>
          <button
            className={`diff-panel-toggle${mode === "inline" ? " active" : ""}`}
            onClick={() => setMode("inline")}
            title={t("diffInline", "内联对比")}
          >
            {t("diffInline", "内联对比")}
          </button>
          <button
            className="diff-panel-close"
            onClick={onClose}
            title={t("close", "关闭")}
          >
            ×
          </button>
        </div>
      </div>
      <div className="diff-panel-body">
        {loading && (
          <div className="diff-panel-status">{t("loadingContext", "加载中...")}</div>
        )}
        {!loading && error && (
          <div className="diff-panel-status error">
            {t("requestFailed", "请求失败")}: {error}
          </div>
        )}
        {!loading && !error && (
          <DiffEditor
            height="100%"
            language={fileLanguage(file.path)}
            original={original}
            modified={modified}
            theme={editorTheme}
            options={{ readOnly: true, renderSideBySide: mode === "split" }}
          />
        )}
      </div>
    </div>
  );
}
