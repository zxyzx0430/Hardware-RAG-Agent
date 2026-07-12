import { useAppStore } from "../../stores/useAppStore";
import { useI18n } from "../../i18n";
import { SerialPane } from "./SerialPane";
import { FlashPane } from "./FlashPane";
import { PreviewPane } from "./PreviewPane";
import { WiringPane } from "./WiringPane";
import { SafetyPane } from "./SafetyPane";

const TAB_IDS = ["serial", "flash", "preview", "wiring", "safety"] as const;
type TabId = (typeof TAB_IDS)[number];

export function WorkbenchPanel() {
  const { t } = useI18n();
  const { wbTab, setWbTab } = useAppStore();
  const tabLabels: Record<TabId, string> = {
    serial: t('serialMonitor'),
    flash: t('flash'),
    preview: t('codePreview'),
    wiring: t('wiringDiagram'),
    safety: t('safetyGuard'),
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%" }}>
      <div className="wb-tabbar workbench-tabs">
        {TAB_IDS.map((id) => (
          <button key={id} className={`wb-tab${wbTab === id ? " active" : ""}`} data-wbtab={id} onClick={() => setWbTab(id)}>
            {tabLabels[id]}
          </button>
        ))}
      </div>
      <div className="wb-content">
        <div style={{ display: wbTab === "serial" ? "block" : "none", flex: 1, minHeight: 0, overflow: "hidden", width: "100%" }}>
          <SerialPane />
        </div>
        <div style={{ display: wbTab === "flash" ? "block" : "none", flex: 1, minHeight: 0, overflow: "hidden", width: "100%" }}>
          <FlashPane />
        </div>
        <div style={{ display: wbTab === "preview" ? "block" : "none", flex: 1, minHeight: 0, overflow: "hidden", width: "100%" }}>
          <PreviewPane />
        </div>
        <div style={{ display: wbTab === "wiring" ? "block" : "none", flex: 1, minHeight: 0, overflow: "hidden", width: "100%" }}>
          <WiringPane />
        </div>
        <div style={{ display: wbTab === "safety" ? "block" : "none", flex: 1, minHeight: 0, overflow: "hidden", width: "100%" }}>
          <SafetyPane />
        </div>
      </div>
    </div>
  );
}
