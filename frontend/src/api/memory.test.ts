import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createMemory,
  deleteMemory,
  getHardwareProfile,
  listHardwareProfiles,
  listMemory,
  searchMemory,
  updateMemory,
  upsertHardwareProfile,
} from "./memory";

const { apiGetMock, apiPostMock, apiPatchMock, apiDeleteMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
}));

vi.mock("./client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
}));

const entry = {
  id: "mem-1",
  scope: "project",
  scope_key: "E:/board",
  kind: "fact",
  content: "3.3V logic",
  structured: {},
  source: "user",
  confidence: 0.8,
  created_at: "2026-11-05T08:00:00Z",
  updated_at: "2026-11-05T08:00:00Z",
};

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPatchMock.mockReset();
  apiDeleteMock.mockReset();
});

describe("memory API client", () => {
  it("builds memory list filters and unwraps the list envelope", async () => {
    apiGetMock.mockResolvedValue({ items: [entry] });
    await expect(listMemory({ scope: "project", kind: "fact", q: "3.3V", limit: 20 }))
      .resolves.toEqual([entry]);
    expect(apiGetMock).toHaveBeenCalledWith("memory?scope=project&kind=fact&q=3.3V&limit=20");
  });

  it("omits empty filters", async () => {
    apiGetMock.mockResolvedValue([]);
    await listMemory({ scope: "", kind: "", q: "" });
    expect(apiGetMock).toHaveBeenCalledWith("memory");
  });

  it("creates a user-sourced entry and unwraps it", async () => {
    apiPostMock.mockResolvedValue({ entry });
    await expect(createMemory({ scope: "project", kind: "fact", content: "3.3V logic" })).resolves.toEqual(entry);
    expect(apiPostMock).toHaveBeenCalledWith("memory", {
      scope: "project",
      kind: "fact",
      content: "3.3V logic",
      source: "user",
    });
  });

  it("searches, patches and soft-deletes", async () => {
    apiPostMock.mockResolvedValue({ entries: [entry] });
    apiPatchMock.mockResolvedValue(entry);
    apiDeleteMock.mockResolvedValue(undefined);

    await expect(searchMemory({ query: "logic", limit: 5 })).resolves.toEqual([entry]);
    expect(apiPostMock).toHaveBeenCalledWith("memory/search", { query: "logic", limit: 5 });

    await expect(updateMemory("mem-1", { content: "5V logic" })).resolves.toEqual(entry);
    expect(apiPatchMock).toHaveBeenCalledWith("memory/mem-1", { content: "5V logic" });

    await deleteMemory("mem-1");
    expect(apiDeleteMock).toHaveBeenCalledWith("memory/mem-1");
  });

  it("wraps hardware profile list, detail and upsert", async () => {
    const profile = { id: "hw-1", name: "ESP32-S3", mcu: "ESP32-S3", aliases: [], pins: {}, peripherals: [], sources: [], rejected: [], confidence: 0.9, use_count: 1, package: "", voltage: "", clock: "", toolchain: "" };
    apiGetMock.mockResolvedValueOnce({ profiles: [profile] }).mockResolvedValueOnce({ profile });
    apiPostMock.mockResolvedValue({ profile });

    await expect(listHardwareProfiles({ q: "esp32", limit: 10 })).resolves.toEqual([profile]);
    expect(apiGetMock).toHaveBeenNthCalledWith(1, "hardware-profiles?q=esp32&limit=10");

    await expect(getHardwareProfile("hw-1")).resolves.toEqual(profile);
    expect(apiGetMock).toHaveBeenNthCalledWith(2, "hardware-profiles/hw-1");

    await expect(upsertHardwareProfile({ id: "hw-1", name: "ESP32-S3" })).resolves.toEqual(profile);
    expect(apiPostMock).toHaveBeenCalledWith("hardware-profiles", { id: "hw-1", name: "ESP32-S3" });
  });
});
