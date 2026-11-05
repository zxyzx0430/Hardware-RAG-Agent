import { useState } from "react";
import { useI18n } from "../../i18n";
import { useAppStore } from "../../stores/useAppStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { HardwareProfilesPanel } from "./HardwareProfilesPanel";
import { MemoryPanel } from "./MemoryPanel";
import { MemoryProposalsPanel } from "./MemoryProposalsPanel";
import { WorkingMemoryPanel } from "./WorkingMemoryPanel";

type MemorySection = "working" | "entries" | "proposals" | "profiles";

const SECTIONS: Array<{ id: MemorySection; label: string }> = [
  { id: "working", label: "workingMemory" },
  { id: "entries", label: "memoryEntries" },
  { id: "proposals", label: "proposals" },
  { id: "profiles", label: "hardwareProfiles" },
];

function projectNameFromPath(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

export function MemoryHubPanel() {
  const { t } = useI18n();
  const explorerRootPath = useAppStore((state) => state.explorerRootPath);
  const autoMemoryEnabled = useSettingsStore((state) => state.autoMemoryEnabled);
  const updateSetting = useSettingsStore((state) => state.updateSetting);
  const [section, setSection] = useState<MemorySection>("working");
  const [manualProjectId, setManualProjectId] = useState("");

  return (
    <div className="settings-section">
      <h3>{t("memoryHub")}</h3>
      <p style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 14 }}>{t("memoryHubDesc")}</p>
      <p style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 14 }}>{t("memoryNoSecrets")}</p>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "10px 14px",
          marginBottom: 14,
        }}
      >
        <div>
          <div className="field-label" style={{ marginBottom: 2 }}>{t("memoryAutoExtract")}</div>
          <div style={{ fontSize: 12, color: "var(--muted-fg)" }}>{t("memoryAutoExtractDesc")}</div>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={autoMemoryEnabled}
          aria-label={t("memoryAutoExtract")}
          className={`mini-toggle ${autoMemoryEnabled ? "on" : "off"}`}
          style={{ marginLeft: "auto" }}
          onClick={() => void updateSetting("autoMemoryEnabled", !autoMemoryEnabled)}
        >
          <span className="mini-toggle-knob" />
        </button>
      </div>

      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "10px 14px",
          marginBottom: 16,
        }}
      >
        <div className="field-label" style={{ marginBottom: 4 }}>{t("memoryProjectContext")}</div>
        {explorerRootPath ? (
          <>
            <div style={{ fontSize: 13 }}>{projectNameFromPath(explorerRootPath)}</div>
            <div style={{ fontSize: 11, color: "var(--muted-fg)", fontFamily: "var(--font-mono)", wordBreak: "break-all" }}>
              {explorerRootPath}
            </div>
            <div style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 4 }}>
              {t("memoryProjectHint")}
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 6 }}>
              {t("memoryProjectUnknown")}
            </div>
            <input
              className="form-input"
              aria-label={t("memoryProjectId")}
              placeholder={t("memoryProjectIdPlaceholder")}
              value={manualProjectId}
              onChange={(event) => setManualProjectId(event.target.value)}
            />
            <div style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 4 }}>
              {t("memoryProjectIdHint")}
            </div>
          </>
        )}
      </div>

      <div className="appearance-group" style={{ marginBottom: 18 }}>
        {SECTIONS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`appearance-btn${section === item.id ? " active" : ""}`}
            onClick={() => setSection(item.id)}
          >
            {t(item.label)}
          </button>
        ))}
      </div>

      {section === "working" && <WorkingMemoryPanel />}
      {section === "entries" && <MemoryPanel projectId={manualProjectId.trim()} />}
      {section === "proposals" && <MemoryProposalsPanel />}
      {section === "profiles" && <HardwareProfilesPanel />}
    </div>
  );
}
