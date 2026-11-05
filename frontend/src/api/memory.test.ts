import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  approveProposal,
  createMemory,
  deleteMemory,
  getHardwareProfile,
  getWorkingMemory,
  listHardwareProfiles,
  listMemory,
  listProposals,
  rejectProposal,
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

  it("sends v2 tier, status and project filters", async () => {
    apiGetMock.mockResolvedValue({ entries: [entry] });
    await expect(listMemory({ tier: "user", status: "proposed", project_id: "proj-1" })).resolves.toEqual([entry]);
    expect(apiGetMock).toHaveBeenCalledWith("memory?tier=user&status=proposed&project_id=proj-1");
  });

  it("unwraps working-memory snapshots from envelope, direct and null shapes", async () => {
    const snapshot = {
      body: { goal: "Fix I2C", completed: [] },
      seal_id: "seal-1",
      generation: 2,
      created_at: "2026-11-06T01:00:00Z",
      updated_at: "2026-11-06T02:00:00Z",
    };
    apiGetMock
      .mockResolvedValueOnce({ snapshot })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ ...snapshot, body: JSON.stringify({ goal: "Serialized" }) });

    await expect(getWorkingMemory("sess-1")).resolves.toEqual(snapshot);
    await expect(getWorkingMemory("sess-2")).resolves.toBeNull();
    await expect(getWorkingMemory("sess-3")).resolves.toEqual({
      ...snapshot,
      body: { goal: "Serialized" },
    });
    expect(apiGetMock).toHaveBeenNthCalledWith(1, "memory/working/sess-1");
  });

  it("lists proposals and falls back to the dedicated route", async () => {
    apiGetMock.mockResolvedValueOnce({ entries: [entry] });
    await expect(listProposals()).resolves.toEqual([entry]);
    expect(apiGetMock).toHaveBeenLastCalledWith("memory?status=proposed&limit=200");

    apiGetMock
      .mockRejectedValueOnce(new Error("404"))
      .mockResolvedValueOnce({ proposals: [entry] });
    await expect(listProposals()).resolves.toEqual([entry]);
    expect(apiGetMock).toHaveBeenLastCalledWith("memory/proposals?limit=200");
  });

  it("approves and rejects proposals", async () => {
    const rejected = { ...entry, status: "rejected" };
    apiPostMock.mockResolvedValueOnce({ entry }).mockResolvedValueOnce(rejected);

    await expect(approveProposal("mem-1")).resolves.toEqual(entry);
    expect(apiPostMock).toHaveBeenNthCalledWith(1, "memory/proposals/mem-1/approve");

    await expect(rejectProposal("mem-1")).resolves.toEqual(rejected);
    expect(apiPostMock).toHaveBeenNthCalledWith(2, "memory/proposals/mem-1/reject");
  });

  it("forwards v2 tier and project id on create", async () => {
    apiPostMock.mockResolvedValue({ entry });
    await createMemory({ scope: "project", kind: "decision", content: "Use SPI", tier: "project", project_id: "proj-1" });
    expect(apiPostMock).toHaveBeenCalledWith("memory", {
      scope: "project",
      kind: "decision",
      content: "Use SPI",
      tier: "project",
      project_id: "proj-1",
      source: "user",
    });
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
