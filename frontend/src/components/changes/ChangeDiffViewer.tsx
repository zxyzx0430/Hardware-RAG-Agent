import { useEffect, useMemo, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import type { ChangeItem } from "../../types/change";
import { useAppStore } from "../../stores/useAppStore";
import { useAppliedDarkMode } from "../shared/MarkdownRenderer";
import { fileLanguage } from "../explorer/fileLanguage";
import { useI18n } from "../../i18n";

export interface ChangeDiffViewerProps {
  item: ChangeItem;
  beforeContent: string | null;
  afterContent: string | null;
  available: boolean;
  reason?: string | null;
}

export function ChangeDiffViewer({
  item, beforeContent, afterContent, available, reason,
}: ChangeDiffViewerProps) {
  const { lang } = useI18n();
  const themeMode = useAppStore((s) => s.themeMode);
  const isDark = useAppliedDarkMode();
  const [mode, setMode] = useState<"split" | "inline">("split");
  const theme = themeMode === "dark" || (themeMode === "auto" && isDark)
    ? "vs-dark"
    : "vs-light";
  const language = useMemo(() => fileLanguage(item.path), [item.path]);

  if (!available) {
    return (
      <div className="change-diff-unavailable" role="status">
        {reason || (lang === "en"
          ? "No text diff is available for this change."
          : "该变更没有可显示的文件 Diff。")}
      </div>
    );
  }

  return (
    <div className="change-diff-viewer">
      <div className="change-diff-toolbar">
        <span className="change-diff-path" title={item.path}>{item.path}</span>
        <span className="change-diff-base">
          {item.before_sha256 ? item.before_sha256.slice(0, 12) : "missing"}
          {" → "}
          {item.after_sha256 ? item.after_sha256.slice(0, 12) : "missing"}
        </span>
        <span className="change-diff-modes">
          <button
            type="button"
            className={mode === "split" ? "active" : ""}
            onClick={() => setMode("split")}
          >
            {lang === "en" ? "Split" : "左右对比"}
          </button>
          <button
            type="button"
            className={mode === "inline" ? "active" : ""}
            onClick={() => setMode("inline")}
          >
            {lang === "en" ? "Inline" : "内联对比"}
          </button>
        </span>
      </div>
      <div className="change-diff-editor">
        <DiffEditor
          height="100%"
          language={language}
          original={beforeContent ?? ""}
          modified={afterContent ?? ""}
          theme={theme}
          options={{ readOnly: true, renderSideBySide: mode === "split" }}
        />
      </div>
    </div>
  );
}
