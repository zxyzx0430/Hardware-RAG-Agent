import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiPost } from "../../api/client";
import { useAppStore } from "../../stores/useAppStore";
import { useWiringStore } from "../../stores/useWiringStore";
import { WiringEditor } from "./WiringEditor";

vi.mock("../../api/client", () => ({ apiPost: vi.fn() }));

const apiPostMock = vi.mocked(apiPost);

describe("WiringEditor API flow", () => {
  beforeEach(() => {
    apiPostMock.mockReset();
    useAppStore.setState({
      openFiles: [],
      activeFileId: null,
      previewTabs: [],
      activePreviewTabId: null,
    });
    useWiringStore.getState().clearAll();
  });

  afterEach(() => cleanup());

  it("extracts the active Explorer draft and sends canonical endpoints to generation", async () => {
    const code = "void setup() { pinMode(2, OUTPUT); }";
    const components = [
      { name: "MCU", type: "mcu", pins: ["GPIO2"] },
      { name: "LED", type: "led", pins: ["ANODE", "CATHODE"] },
    ];
    const connections = [
      {
        from: { component: "MCU", pin: "GPIO2" },
        to: { component: "LED", pin: "ANODE" },
        color: "#ff0000",
        line_type: "signal" as const,
      },
    ];
    const svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>GPIO2</text></svg>';

    useAppStore.setState({
      openFiles: [{
        id: "active-file",
        path: "C:/project/main.ino",
        name: "main.ino",
        content: code,
        is_text: true,
        dirty: true,
      }],
      activeFileId: "active-file",
      previewTabs: [{ id: "older-preview", label: "Old code", code: "stale preview" }],
      activePreviewTabId: "older-preview",
    });
    apiPostMock.mockImplementation(async <T,>(path: string) => {
      const result = path === "wiring/extract"
        ? { components, connections }
        : { svg, bom: [{ component: "LED", qty: 1 }] };
      return result as T;
    });

    render(<WiringEditor zoom={1} onZoomIn={() => {}} onZoomOut={() => {}} onReset={() => {}} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "从代码提取" }));
    });
    expect(apiPostMock).toHaveBeenNthCalledWith(1, "wiring/extract", { code });
    expect(useWiringStore.getState().connections).toEqual(connections);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "生成接线图" }));
    });
    expect(apiPostMock).toHaveBeenNthCalledWith(2, "wiring", {
      title: "Hardware RAG",
      components,
      connections,
    });
    expect(useWiringStore.getState().svg).toBe(svg);
    expect(useWiringStore.getState().bom).toEqual([{ component: "LED", qty: 1 }]);
  });

  it("uses the active preview code when no Explorer file is open", async () => {
    useAppStore.setState({
      previewTabs: [{ id: "preview", label: "Generated code", code: "preview code" }],
      activePreviewTabId: "preview",
    });
    apiPostMock.mockImplementation(async <T,>() => ({ components: [], connections: [] }) as T);

    render(<WiringEditor zoom={1} onZoomIn={() => {}} onZoomOut={() => {}} onReset={() => {}} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "从代码提取" }));
    });

    expect(apiPostMock).toHaveBeenCalledWith("wiring/extract", { code: "preview code" });
  });
});
