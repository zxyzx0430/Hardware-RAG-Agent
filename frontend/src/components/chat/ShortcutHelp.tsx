import React from "react";
import { useI18n } from "../../i18n";

const SHORTCUTS = [
  { keys: "Ctrl+K", desc: "搜索" },
  { keys: "Ctrl+,", desc: "设置" },
  { keys: "Ctrl+N", desc: "新建对话" },
  { keys: "Ctrl+↑/↓", desc: "切换会话" },
  { keys: "Escape", desc: "关闭弹窗" },
  { keys: "Ctrl+/", desc: "快捷键帮助" },
  { keys: "Enter", desc: "发送消息" },
  { keys: "Shift+Enter", desc: "换行" },
];

export const ShortcutHelp: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const { t } = useI18n();
  return (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <div
        style={{ background: "var(--bg)", borderRadius: 12, padding: 24, minWidth: 320, maxWidth: 480 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>{t("shortcutHelpTitle", "键盘快捷键")}</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {SHORTCUTS.map((s) => (
            <div key={s.keys} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 13 }}>{s.desc}</span>
              <kbd style={{ background: "var(--hover-bg)", padding: "2px 8px", borderRadius: 4, fontSize: 12, fontFamily: "monospace" }}>{s.keys}</kbd>
            </div>
          ))}
        </div>
        <button
          onClick={onClose}
          style={{ marginTop: 16, width: "100%", padding: "8px 16px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", cursor: "pointer" }}
        >
          {t("close", "关闭")}
        </button>
      </div>
    </div>
  );
};
