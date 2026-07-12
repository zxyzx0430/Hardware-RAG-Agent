import { useSettingsStore } from "../../stores/useSettingsStore";

// v2-T5: Permission policy switcher — controls Agent tool-call gating mode.
// bypassPermissions = allow all (risky) / default = ask every time / acceptEdits = auto-allow low risk.

type PermissionMode = "bypassPermissions" | "default" | "acceptEdits";

interface ModeOption {
  value: PermissionMode;
  label: string;
  title: string;
  variant: "danger" | "warn" | "safe";
}

const MODE_OPTIONS: ModeOption[] = [
  { value: "bypassPermissions", label: "全部放行", title: "所有工具调用直接执行，不询问（风险最高，仅信任场景用）", variant: "danger" },
  { value: "default", label: "每次询问", title: "每次工具调用都弹窗确认（最安全）", variant: "warn" },
  { value: "acceptEdits", label: "自动放行低风险", title: "LOW 风险自动放行，MEDIUM/HIGH 仍弹窗确认（推荐）", variant: "safe" },
];

function PolicyButton({ option, active, onClick }: {
  option: ModeOption;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`policy-btn policy-btn-${option.variant}${active ? " active" : ""}`}
      title={option.title}
      onClick={onClick}
      aria-pressed={active}
    >
      {option.label}
    </button>
  );
}

export default function PolicyBar() {
  const permissionMode = useSettingsStore((s) => s.permissionMode);
  const updateSetting = useSettingsStore((s) => s.updateSetting);

  const handleSelect = (mode: PermissionMode) => updateSetting("permissionMode", mode);

  return (
    <div className="policy-bar" role="group" aria-label="权限策略">
      <span className="policy-bar-label" title="Agent 工具调用的权限策略">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
        权限
      </span>
      <div className="policy-bar-buttons">
        {MODE_OPTIONS.map((opt) => (
          <PolicyButton
            key={opt.value}
            option={opt}
            active={permissionMode === opt.value}
            onClick={() => handleSelect(opt.value)}
          />
        ))}
      </div>
    </div>
  );
}
