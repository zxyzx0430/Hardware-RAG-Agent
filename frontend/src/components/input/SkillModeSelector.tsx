import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSkillsStore, type SkillsMode } from "../../stores/useSkillsStore";

type PopupPosition = { top: number; left: number; maxHeight: number };

export function SkillModeSelector({ sessionId, lang }: { sessionId: string; lang: string }) {
  const isChinese = lang === "zh";
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<PopupPosition | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const selection = useSkillsStore((state) => state.chatSkillSelections[sessionId]) ?? { mode: "off" as const, skillIds: [] };
  const skills = useSkillsStore((state) => state.skills);
  const loaded = useSkillsStore((state) => state.loaded);
  const loading = useSkillsStore((state) => state.loading);
  const error = useSkillsStore((state) => state.error);
  const fetchSkills = useSkillsStore((state) => state.fetchSkills);
  const setChatSkillSelection = useSkillsStore((state) => state.setChatSkillSelection);
  const enabledSkills = skills.filter((skill) => skill.enabled);
  const selectedEnabledIds = selection.skillIds.filter((id) => enabledSkills.some((skill) => skill.id === id));

  useEffect(() => {
    if (open && !loaded && !loading && !error) void fetchSkills();
  }, [open, loaded, loading, error, fetchSkills]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setPosition(null);
      return;
    }
    const rect = triggerRef.current.getBoundingClientRect();
    setPosition({ top: rect.top - 4, left: rect.left, maxHeight: Math.max(120, rect.top - 16) });
  }, [open]);

  const popupLeft = position?.left;
  useLayoutEffect(() => {
    if (!open || popupLeft === undefined || !popupRef.current) return;
    const rect = popupRef.current.getBoundingClientRect();
    setPosition((current) => current ? { ...current, top: Math.max(8, current.top - rect.height - 4) } : current);
  }, [open, popupLeft]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!triggerRef.current?.contains(target) && !popupRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const chooseMode = (mode: SkillsMode) => {
    setChatSkillSelection(sessionId, { mode, skillIds: selection.skillIds });
  };

  const toggleSkill = (id: string) => {
    const skillIds = selectedEnabledIds.includes(id)
      ? selectedEnabledIds.filter((selectedId) => selectedId !== id)
      : [...selectedEnabledIds, id];
    setChatSkillSelection(sessionId, { mode: "manual", skillIds });
  };

  const modeLabel = selection.mode === "off"
    ? (isChinese ? "关闭" : "Off")
    : selection.mode === "auto"
      ? (isChinese ? "自动" : "Auto")
      : (isChinese ? `手动 ${selectedEnabledIds.length}/3` : `Manual ${selectedEnabledIds.length}/3`);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`permission-selector${selection.mode !== "off" ? " active" : ""}`}
        aria-label={isChinese ? `技能模式：${modeLabel}` : `Skills mode: ${modeLabel}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">✦</span>
        <span>{isChinese ? `技能：${modeLabel}` : `Skills: ${modeLabel}`}</span>
        <svg className="permission-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && position && createPortal(
        <div
          ref={popupRef}
          role="dialog"
          aria-label={isChinese ? "聊天技能选择" : "Chat skill selection"}
          className="permission-dropdown"
          style={{ position: "fixed", top: position.top, left: position.left, width: 300, maxHeight: Math.min(340, position.maxHeight), overflowY: "auto", zIndex: 1200 }}
        >
          <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
            <strong style={{ fontSize: 12 }}>{isChinese ? "只读 Skills 模式" : "Read-only Skills mode"}</strong>
            <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--muted-fg)" }}>
              {isChinese ? "技能只提供说明和只读资料，不会执行其中的脚本。" : "Skills provide instructions and read-only references; their scripts are not executed."}
            </p>
          </div>
          {([
            ["off", isChinese ? "关闭" : "Off", isChinese ? "保持当前 Agent 行为" : "Keep the current Agent behavior"],
            ["auto", isChinese ? "自动选择" : "Auto-select", isChinese ? "由 Agent 从已启用技能中选择" : "Let the Agent choose among enabled skills"],
            ["manual", isChinese ? "手动指定" : "Choose manually", isChinese ? "只使用你勾选的技能，最多 3 个" : "Use only checked skills, up to 3"],
          ] as const).map(([mode, label, description]) => (
            <label key={mode} style={{ display: "flex", gap: 8, padding: "9px 12px", cursor: "pointer", borderBottom: "1px solid var(--border)" }}>
              <input type="radio" name={`skills-mode-${sessionId}`} value={mode} checked={selection.mode === mode} onChange={() => chooseMode(mode)} />
              <span>
                <strong style={{ display: "block", fontSize: 12 }}>{label}</strong>
                <span style={{ fontSize: 11, color: "var(--muted-fg)" }}>{description}</span>
              </span>
            </label>
          ))}
          {selection.mode === "manual" && (
            <div style={{ padding: "8px 12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 5 }}>
                <strong style={{ fontSize: 11 }}>{isChinese ? "已启用技能" : "Enabled skills"}</strong>
                <button type="button" className="verify-btn" onClick={() => void fetchSkills()} disabled={loading}>
                  {loading ? (isChinese ? "加载中…" : "Loading…") : (isChinese ? "刷新" : "Refresh")}
                </button>
              </div>
              {loading && <p role="status" style={{ fontSize: 11, color: "var(--muted-fg)" }}>{isChinese ? "正在加载技能…" : "Loading skills…"}</p>}
              {!loading && error && <p role="alert" style={{ fontSize: 11, color: "var(--danger)" }}>{error}</p>}
              {!loading && !error && enabledSkills.length === 0 && <p style={{ fontSize: 11, color: "var(--muted-fg)" }}>{isChinese ? "没有已启用的技能。请先到设置中检查并启用。" : "No enabled skills. Review and enable skills in Settings first."}</p>}
              {enabledSkills.map((skill) => {
                const checked = selectedEnabledIds.includes(skill.id);
                return (
                  <label key={skill.id} style={{ display: "flex", gap: 7, alignItems: "flex-start", padding: "5px 0", cursor: "pointer" }}>
                    <input type="checkbox" checked={checked} disabled={!checked && selectedEnabledIds.length >= 3} onChange={() => toggleSkill(skill.id)} />
                    <span>
                      <strong style={{ display: "block", fontSize: 11 }}>{skill.name}</strong>
                      <span style={{ fontSize: 10, color: "var(--muted-fg)" }}>{skill.description}</span>
                    </span>
                  </label>
                );
              })}
              {selectedEnabledIds.length === 0 && enabledSkills.length > 0 && (
                <p role="status" style={{ margin: "6px 0 0", fontSize: 11, color: "var(--warning)" }}>
                  {isChinese ? "手动模式至少选择一个技能后才能发送。" : "Choose at least one skill before sending in manual mode."}
                </p>
              )}
            </div>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}
