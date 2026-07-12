import { useAppStore } from "../../stores/useAppStore";
import { TopBar } from "../topbar/TopBar";
import { ChatArea } from "../chat/ChatArea";
import { InputBar } from "../input/InputBar";
import PolicyBar from "../chat/PolicyBar";

export function MainArea() {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        overflow: "hidden",
      }}
    >
      <TopBar />
      <div
        style={{
          flex: 1,
          display: "flex",
          minHeight: 0,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
            overflow: "hidden",
          }}
        >
          <ChatArea />
          <PolicyBar />
          <InputBar />
        </div>
      </div>
    </div>
  );
}
