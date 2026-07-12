import { useState, useCallback } from "react";
import { useI18n } from "../../i18n";
import { copyToClipboard } from "../../utils/clipboard";

// 错误样式：边框/背景统一用 CSS 变量，自动适配明暗主题
const ERROR_STYLES: Record<string, { border: string; bg: string; icon: string }> = {
  AUTH_FAILED:    { border: "var(--warn)",   bg: "var(--warn-soft)",   icon: "warning" },
  MODEL_NOT_FOUND:{ border: "var(--warn)",   bg: "var(--warn-soft)",   icon: "warning" },
  TIMEOUT:        { border: "var(--warn)",   bg: "var(--warn-soft)",   icon: "clock" },
  RATE_LIMITED:   { border: "var(--warn)",   bg: "var(--warn-soft)",   icon: "clock" },
  NETWORK_ERROR:  { border: "var(--danger)", bg: "var(--danger-soft)", icon: "link-off" },
  RAG_FAILED:     { border: "var(--primary)",bg: "var(--activity-bg)", icon: "info" },
  INTERNAL_ERROR: { border: "var(--danger)", bg: "var(--danger-soft)", icon: "warning" },
  UNKNOWN:        { border: "var(--muted-fg)",bg: "var(--hover-bg)",   icon: "question" },
};

function ErrorIcon({ type }: { type: string }) {
  const size = 16;
  // 用 style 传递 stroke，让 var() 在 SVG 中生效（attribute 不解析 CSS 变量）
  const color = ERROR_STYLES[type]?.border ?? "var(--muted-fg)";
  switch (ERROR_STYLES[type]?.icon) {
    case "warning":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2">
          <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
      );
    case "clock":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2">
          <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
        </svg>
      );
    case "link-off":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2">
          <path d="M18.84 12.25l1.72-1.71a4 4 0 00-5.66-5.66l-3.53 3.53"/>
          <path d="M5.21 12.79l-1.72 1.71a4 4 0 005.66 5.66l3.53-3.53"/>
          <line x1="2" y1="2" x2="22" y2="22"/>
        </svg>
      );
    case "info":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2">
          <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
        </svg>
      );
    default:
      return (
        <svg width={size} height={size} viewBox="0 0 24 24" fill="none" style={{ stroke: color }} strokeWidth="2">
          <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
      );
  }
}

interface ErrorBlockProps {
  code: string;
  message: string;
  detail?: string;
  onRetry?: () => void;
}

export default function ErrorBlock({ code, message, detail, onRetry }: ErrorBlockProps) {
  const { t } = useI18n();
  const style = ERROR_STYLES[code] ?? ERROR_STYLES.UNKNOWN;
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    const text = [code, message, detail].filter(Boolean).join("\n");
    copyToClipboard(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [code, message, detail]);

  return (
    <div
      className="error-block"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "10px 14px",
        borderRadius: 10,
        border: `1px solid ${style.border}`,
        background: style.bg,
        marginTop: 6,
        fontSize: 13,
        lineHeight: 1.5,
        position: "relative",
      }}
    >
      <span style={{ flexShrink: 0, marginTop: 1 }}>
        <ErrorIcon type={code} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, color: "var(--fg)" }}>{message}</div>
        {detail && (
          <div style={{ color: "var(--muted-fg)", fontSize: 12, marginTop: 2 }}>
            {detail}
          </div>
        )}
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              marginTop: 6,
              padding: "4px 12px",
              borderRadius: 6,
              border: `1px solid ${style.border}`,
              background: "transparent",
              color: style.border,
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            {t('retry') ?? '重试'}
          </button>
        )}
      </div>
      <button
        onClick={handleCopy}
        aria-label="Copy error details"
        title="Copy error details"
        style={{
          flexShrink: 0,
          padding: 4,
          background: "transparent",
          border: "none",
          cursor: "pointer",
          color: copied ? "var(--success)" : "var(--muted-fg)",
          borderRadius: 4,
          display: "flex",
          alignItems: "center",
        }}
      >
        {copied ? (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        )}
      </button>
    </div>
  );
}
