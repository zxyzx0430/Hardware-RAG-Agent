import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiGetMock, apiPutMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPutMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPut: apiPutMock,
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));

vi.mock("./useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));

vi.mock("./useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: vi.fn() }) },
}));

async function loadSettingsStore(serverSettings: Record<string, unknown>) {
  apiGetMock.mockResolvedValue({ settings: serverSettings });
  const { useSettingsStore } = await import("./useSettingsStore");
  await vi.waitFor(() => {
    expect(useSettingsStore.getState().longTermMemory).toBe(
      typeof serverSettings.longTermMemory === "string" ? serverSettings.longTermMemory : "",
    );
  });
  return useSettingsStore;
}

describe("manual long-term memory settings", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    apiGetMock.mockReset();
    apiPutMock.mockReset();
  });

  it("loads the server value and changes it only after the save request succeeds", async () => {
    const store = await loadSettingsStore({ longTermMemory: "last saved" });
    let resolvePut: (() => void) | undefined;
    apiPutMock.mockImplementationOnce(() => new Promise<void>((resolve) => {
      resolvePut = resolve;
    }));

    const save = store.getState().saveLongTermMemory("new draft");
    expect(store.getState().longTermMemory).toBe("last saved");
    expect(apiPutMock).toHaveBeenCalledWith("settings", { longTermMemory: "new draft" });

    resolvePut?.();
    await expect(save).resolves.toBe(true);
    expect(store.getState().longTermMemory).toBe("new draft");
    expect(JSON.parse(localStorage.getItem("hwrag_settings") || "{}")).not.toHaveProperty("longTermMemory");
  });

  it("does not let a settings response started before a save overwrite the saved value", async () => {
    let resolveGet: ((data: { settings: Record<string, unknown> }) => void) | undefined;
    let markGetStarted: (() => void) | undefined;
    const getStarted = new Promise<void>((resolve) => {
      markGetStarted = resolve;
    });
    apiGetMock.mockImplementationOnce(() => {
      markGetStarted?.();
      return new Promise((resolve) => {
        resolveGet = resolve;
      });
    });
    const { useSettingsStore } = await import("./useSettingsStore");
    await getStarted;
    apiPutMock.mockResolvedValueOnce({ updated: true });

    await expect(useSettingsStore.getState().saveLongTermMemory("new server value")).resolves.toBe(true);
    resolveGet?.({ settings: { longTermMemory: "stale earlier response" } });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(useSettingsStore.getState().longTermMemory).toBe("new server value");
  });

  it("keeps the last confirmed server value when saving fails", async () => {
    const store = await loadSettingsStore({ longTermMemory: "confirmed value" });
    apiPutMock.mockRejectedValueOnce(new Error("simulated network failure"));

    await expect(store.getState().saveLongTermMemory("unsaved draft")).resolves.toBe(false);

    expect(store.getState().longTermMemory).toBe("confirmed value");
  });

  it("treats the server's empty value as authoritative over an old local cache", async () => {
    localStorage.setItem("hwrag_settings", JSON.stringify({ longTermMemory: "stale local value" }));

    const store = await loadSettingsStore({ longTermMemory: "" });

    expect(store.getState().longTermMemory).toBe("");
    expect(JSON.parse(localStorage.getItem("hwrag_settings") || "{}")).not.toHaveProperty("longTermMemory");
  });

  it("clears through an explicit server write and rejects values over the API limit", async () => {
    const store = await loadSettingsStore({ longTermMemory: "confirmed value" });
    apiPutMock.mockResolvedValueOnce({ updated: true });

    await expect(store.getState().saveLongTermMemory("")).resolves.toBe(true);
    expect(apiPutMock).toHaveBeenCalledWith("settings", { longTermMemory: "" });
    expect(store.getState().longTermMemory).toBe("");

    await expect(store.getState().saveLongTermMemory("x".repeat(4001))).resolves.toBe(false);
    expect(apiPutMock).toHaveBeenCalledTimes(1);
  });
});
