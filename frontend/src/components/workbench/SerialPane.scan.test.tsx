import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SerialPane } from "./SerialPane";
import { useSerialStore } from "../../stores/useSerialStore";
import { useLogStore } from "../../stores/useLogStore";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiWS: vi.fn(),
}));

beforeEach(() => {
  apiGetMock.mockReset();
  useLogStore.getState().clear();
  useSerialStore.setState({
    connected: false,
    port: "COM5",
    baudRate: 115200,
    log: [],
    autoScroll: true,
    dtrActive: false,
    rtsActive: false,
    filter: "",
    lineEnding: "\r\n",
    autoConnectRequest: null,
  });
});

afterEach(cleanup);

function logMessages(): string {
  return useLogStore.getState().buffer.map((entry) => entry.msg).join("\n");
}

describe("SerialPane device scan", () => {
  it("keeps the selected port while connected", async () => {
    useSerialStore.setState({ connected: true });
    apiGetMock.mockResolvedValue({ devices: [{ port: "COM9", description: "USB serial", manufacturer: "" }] });
    render(<SerialPane />);

    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());
    await waitFor(() => expect(logMessages()).toContain("扫描到 1 个串口设备"));
    expect(useSerialStore.getState().port).toBe("COM5");
  });

  it("does not clear the port when a scan finds no devices", async () => {
    apiGetMock.mockResolvedValue({ devices: [] });
    render(<SerialPane />);

    await waitFor(() => expect(apiGetMock).toHaveBeenCalled());
    await waitFor(() => expect(logMessages()).toContain("未扫描到串口设备"));
    expect(useSerialStore.getState().port).toBe("COM5");
  });

  it("selects the first device when disconnected and nothing is selected", async () => {
    useSerialStore.setState({ port: "" });
    apiGetMock.mockResolvedValue({ devices: [{ port: "COM9", description: "USB serial", manufacturer: "" }] });
    render(<SerialPane />);

    await waitFor(() => expect(useSerialStore.getState().port).toBe("COM9"));
  });
});
