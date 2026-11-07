import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiGetMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  logMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
  getSessionToken: () => null,
  setSessionToken: vi.fn(),
}));

vi.mock("./useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));

vi.mock("./useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: vi.fn() }) },
}));

describe("settings secret persistence", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    apiGetMock.mockResolvedValue({ settings: {} });
  });

  it("does not write provider api keys to the persisted settings blob", async () => {
    const { useSettingsStore } = await import("./useSettingsStore");

    useSettingsStore
      .getState()
      .addProvider("Test", "https://api.example.com", "sk-super-secret");

    const raw = localStorage.getItem("hwrag_settings") || "";
    expect(raw).not.toContain("sk-super-secret");
    expect(raw).not.toContain('"apiKey":"sk-');
  });
});
