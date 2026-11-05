import { useState } from "react";
import { useI18n } from "../../i18n";
import { HardwareProfilesPanel } from "./HardwareProfilesPanel";
import { MemoryPanel } from "./MemoryPanel";

export function MemoryHubPanel() {
  const { t } = useI18n();
  const [section, setSection] = useState<"entries" | "profiles">("entries");
  return (
    <div className="settings-section">
      <h3>{t("memoryHub")}</h3>
      <p style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 14 }}>{t("memoryHubDesc")}</p>
      <div className="appearance-group" style={{ marginBottom: 18 }}>
        <button
          type="button"
          className={`appearance-btn${section === "entries" ? " active" : ""}`}
          onClick={() => setSection("entries")}
        >
          {t("memoryEntries")}
        </button>
        <button
          type="button"
          className={`appearance-btn${section === "profiles" ? " active" : ""}`}
          onClick={() => setSection("profiles")}
        >
          {t("hardwareProfiles")}
        </button>
      </div>
      {section === "entries" ? <MemoryPanel /> : <HardwareProfilesPanel />}
    </div>
  );
}
