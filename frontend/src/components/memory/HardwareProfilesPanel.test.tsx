import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HardwareProfilesPanel } from "./HardwareProfilesPanel";
import { useAppStore } from "../../stores/useAppStore";
import type { HardwareProfile } from "../../types/memory";

const { listHardwareProfilesMock, getHardwareProfileMock, upsertHardwareProfileMock } = vi.hoisted(() => ({
  listHardwareProfilesMock: vi.fn(),
  getHardwareProfileMock: vi.fn(),
  upsertHardwareProfileMock: vi.fn(),
}));

vi.mock("../../api/memory", () => ({
  listHardwareProfiles: listHardwareProfilesMock,
  getHardwareProfile: getHardwareProfileMock,
  upsertHardwareProfile: upsertHardwareProfileMock,
}));

// useSettingsStore calls fetchSettings at module load; keep the real client
// (and its store cycle) out of these unit tests.
vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({ settings: {} }),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  parseSseEventId: vi.fn(() => null),
  ApiError: class ApiError extends Error {},
  fetchBigChunk: vi.fn(),
}));

function hardwareProfile(overrides: Partial<HardwareProfile> = {}): HardwareProfile {
  return {
    id: "hw-1",
    name: "ESP32-S3 DevKit",
    aliases: ["ESP32S3", "S3 DevKit"],
    mcu: "ESP32-S3",
    package: "QFN56",
    voltage: "3.3V",
    clock: "240MHz",
    pins: { GPIO1: "UART TX", GPIO2: "UART RX" },
    peripherals: ["Wi-Fi", "BLE"],
    toolchain: "ESP-IDF",
    sources: ["datasheet.pdf"],
    rejected: ["GPIO19 (USB D-)"],
    confidence: 0.9,
    use_count: 2,
    last_used_at: "2026-11-04T10:00:00Z",
    created_at: "2026-11-01T10:00:00Z",
    updated_at: "2026-11-04T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
  listHardwareProfilesMock.mockReset().mockResolvedValue([]);
  getHardwareProfileMock.mockReset();
  upsertHardwareProfileMock.mockReset();
});

afterEach(cleanup);

describe("HardwareProfilesPanel", () => {
  it("shows the no-auto-injection notice and empty state", async () => {
    render(<HardwareProfilesPanel />);

    expect(screen.getByRole("note").textContent).toContain("不会自动注入");
    expect(await screen.findByText("暂无硬件档案")).toBeTruthy();
  });

  it("lists profiles, loads details and renders every documented field", async () => {
    const profile = hardwareProfile();
    listHardwareProfilesMock.mockResolvedValue([profile]);
    getHardwareProfileMock.mockResolvedValue(profile);
    render(<HardwareProfilesPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /ESP32-S3 DevKit/ }));
    await waitFor(() => expect(getHardwareProfileMock).toHaveBeenCalledWith("hw-1"));

    const heading = await screen.findByRole("heading", { level: 4, name: "ESP32-S3 DevKit" });
    const detail = heading.closest("div")?.parentElement as HTMLElement;
    expect(within(detail).getByText("ESP32-S3")).toBeTruthy();
    expect(within(detail).getByText("QFN56")).toBeTruthy();
    expect(within(detail).getByText("3.3V")).toBeTruthy();
    expect(within(detail).getByText("240MHz")).toBeTruthy();
    expect(within(detail).getByText("ESP-IDF")).toBeTruthy();
    expect(within(detail).getByText("ESP32S3")).toBeTruthy();
    expect(within(detail).getByText("S3 DevKit")).toBeTruthy();
    expect(within(detail).getByText("GPIO1")).toBeTruthy();
    expect(within(detail).getByText("UART TX")).toBeTruthy();
    expect(within(detail).getByText("Wi-Fi")).toBeTruthy();
    expect(within(detail).getByText("BLE")).toBeTruthy();
    expect(within(detail).getByText("datasheet.pdf")).toBeTruthy();
    expect(within(detail).getByText("GPIO19 (USB D-)")).toBeTruthy();
    expect(within(detail).getByText(/置信度 0\.90 · 使用次数 2/)).toBeTruthy();
  });

  it("searches hardware profiles", async () => {
    listHardwareProfilesMock.mockResolvedValue([hardwareProfile()]);
    render(<HardwareProfilesPanel />);
    await screen.findByRole("button", { name: /ESP32-S3 DevKit/ });

    fireEvent.change(screen.getByLabelText("搜索硬件档案…"), { target: { value: "s3" } });
    await waitFor(
      () => expect(listHardwareProfilesMock).toHaveBeenLastCalledWith({ q: "s3", limit: 100 }),
      { timeout: 2000 },
    );
  });

  it("edits a profile and upserts structured fields", async () => {
    const profile = hardwareProfile();
    const updated = hardwareProfile({ mcu: "ESP32-S3-R8", confidence: 0.95, use_count: 3 });
    listHardwareProfilesMock.mockResolvedValue([profile]);
    getHardwareProfileMock.mockResolvedValue(profile);
    upsertHardwareProfileMock.mockResolvedValue(updated);
    render(<HardwareProfilesPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /ESP32-S3 DevKit/ }));
    await screen.findByRole("heading", { level: 4, name: "ESP32-S3 DevKit" });
    fireEvent.click(screen.getByRole("button", { name: "修改" }));

    fireEvent.change(screen.getByLabelText("MCU"), { target: { value: "ESP32-S3-R8" } });
    fireEvent.change(screen.getByLabelText("置信度"), { target: { value: "0.95" } });
    fireEvent.click(screen.getByRole("button", { name: "保存档案" }));

    await screen.findByText("硬件档案已保存。");
    expect(upsertHardwareProfileMock).toHaveBeenCalledWith(expect.objectContaining({
      id: "hw-1",
      name: "ESP32-S3 DevKit",
      aliases: ["ESP32S3", "S3 DevKit"],
      mcu: "ESP32-S3-R8",
      package: "QFN56",
      voltage: "3.3V",
      clock: "240MHz",
      toolchain: "ESP-IDF",
      pins: { GPIO1: "UART TX", GPIO2: "UART RX" },
      peripherals: ["Wi-Fi", "BLE"],
      sources: ["datasheet.pdf"],
      rejected: ["GPIO19 (USB D-)"],
      confidence: 0.95,
    }));
    expect(await screen.findByText(/置信度 0\.95 · 使用次数 3/)).toBeTruthy();
  });

  it("rejects invalid JSON before calling the API", async () => {
    listHardwareProfilesMock.mockResolvedValue([hardwareProfile()]);
    getHardwareProfileMock.mockResolvedValue(hardwareProfile());
    render(<HardwareProfilesPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /ESP32-S3 DevKit/ }));
    await screen.findByRole("heading", { level: 4, name: "ESP32-S3 DevKit" });
    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    fireEvent.change(screen.getByLabelText("引脚 (JSON)"), { target: { value: "not json" } });
    fireEvent.click(screen.getByRole("button", { name: "保存档案" }));

    expect(screen.getByRole("alert").textContent).toContain("JSON 格式无效");
    expect(upsertHardwareProfileMock).not.toHaveBeenCalled();
  });
});
