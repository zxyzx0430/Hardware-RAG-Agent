import { useAppStore } from "../../stores/useAppStore";
import { SessionPanel } from "../session/SessionPanel";

export function LeftPanel() {
  const { activeNav, leftPanelOpen, leftPanelWidth } = useAppStore();
  const showSessionPanel = activeNav === "chat";

  return (
    <div
      className="left-panel-wrap"
      id="leftPanelWrap"
      style={{
        width: showSessionPanel && leftPanelOpen ? leftPanelWidth : 0,
        minWidth: showSessionPanel && leftPanelOpen ? leftPanelWidth : 0,
      }}
    >
      <div className="session-panel" id="sessionPanel" style={{ display: showSessionPanel ? "flex" : "none" }}>
        <SessionPanel />
      </div>
    </div>
  );
}
