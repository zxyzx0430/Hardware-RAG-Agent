import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiGetMock, apiPostMock, apiPutMock, logMock, showErrorMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPutMock: vi.fn(),
  logMock: vi.fn(),
  showErrorMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPut: apiPutMock,
  apiPost: apiPostMock,
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));

vi.mock("./useLogStore", () => ({
  useLogStore: { getState: () => ({ log: logMock }) },
}));

vi.mock("./useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: showErrorMock }) },
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
    apiPostMock.mockReset();
    apiPutMock.mockReset();
    logMock.mockReset();
    showErrorMock.mockReset();
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

  it("hydrates valid retrieval settings from an older local record's missing fields", async () => {
    const store = await loadSettingsStore({ topK: "7", relevanceThreshold: "50" });

    expect(store.getState().topK).toBe(7);
    expect(store.getState().relevanceThreshold).toBe(50);
    expect(store.getState().retrievalSettingsStatus).toMatchObject({
      topK: "synced",
      relevanceThreshold: "synced",
    });
  });

  it("keeps valid browser values when backend values are invalid or differ", async () => {
    localStorage.setItem("hwrag_settings", JSON.stringify({ topK: 2, relevanceThreshold: 0 }));
    const store = await loadSettingsStore({ topK: "99", relevanceThreshold: "NaN" });

    expect(store.getState().topK).toBe(2);
    expect(store.getState().relevanceThreshold).toBe(0);
    await vi.waitFor(() => expect(apiPutMock).toHaveBeenCalledWith("settings", { topK: 2 }));
    await vi.waitFor(() => expect(apiPutMock).toHaveBeenCalledWith("settings", { relevanceThreshold: 0 }));
  });

  it("does not let a delayed settings GET overwrite a retrieval edit made while it was pending", async () => {
    let resolveGet: ((data: { settings: Record<string, unknown> }) => void) | undefined;
    apiGetMock.mockImplementationOnce(() => new Promise((resolve) => {
      resolveGet = resolve;
    }));
    const { useSettingsStore } = await import("./useSettingsStore");

    apiPutMock.mockResolvedValue({ updated: true });
    await useSettingsStore.getState().updateSetting("topK", 2);
    resolveGet?.({ settings: { topK: "7", relevanceThreshold: "50" } });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(useSettingsStore.getState().topK).toBe(2);
    expect(useSettingsStore.getState().relevanceThreshold).toBe(50);
    expect(apiPutMock).toHaveBeenCalledWith("settings", { topK: 2 });
  });

  it("reports a failed retrieval setting sync while retaining the browser value", async () => {
    const store = await loadSettingsStore({});
    apiPutMock.mockRejectedValueOnce(new Error("simulated backend rejection"));

    await store.getState().updateSetting("relevanceThreshold", 50);

    expect(store.getState().relevanceThreshold).toBe(50);
    expect(store.getState().retrievalSettingsStatus.relevanceThreshold).toBe("local_only");
    expect(JSON.parse(localStorage.getItem("hwrag_settings") || "{}").relevanceThreshold).toBe(50);
    expect(showErrorMock).toHaveBeenCalledWith("本机已保存，后台未同步");
  });

  it("reports a backend settings read failure while preserving valid local retrieval values", async () => {
    localStorage.setItem("hwrag_settings", JSON.stringify({ topK: 2, relevanceThreshold: 0 }));
    apiGetMock.mockRejectedValueOnce(new Error("simulated settings read failure"));

    const { useSettingsStore } = await import("./useSettingsStore");
    await vi.waitFor(() => expect(useSettingsStore.getState().retrievalSettingsStatus.topK).toBe("read_error"));

    expect(useSettingsStore.getState().topK).toBe(2);
    expect(useSettingsStore.getState().relevanceThreshold).toBe(0);
    expect(useSettingsStore.getState().retrievalSettingsStatus.relevanceThreshold).toBe("read_error");
    expect(showErrorMock).toHaveBeenCalledWith("加载设置失败");
  });

  it("reports a local cache write failure even when the backend accepts the setting", async () => {
    const store = await loadSettingsStore({});
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "hwrag_settings") throw new DOMException("simulated quota failure", "QuotaExceededError");
      return originalSetItem.call(this, key, value);
    });
    apiPutMock.mockResolvedValueOnce({ updated: true });

    await store.getState().updateSetting("topK", 7);

    expect(store.getState().topK).toBe(7);
    expect(store.getState().retrievalSettingsStatus.topK).toBe("synced");
    expect(store.getState().retrievalSettingsStorageIssue).toBe(true);
    expect(showErrorMock).toHaveBeenCalledWith("后台已同步，但本机缓存写入失败");
  });
});

function providerApiError(code: string, message = "private raw error") {
  return Object.assign(new Error(message), {
    code,
    details: { private: "private response detail" },
  });
}

const providerVerificationFailureCases = [
  {
    name: "classifies structured local session errors",
    error: () => providerApiError("AUTH_REQUIRED"),
    expected: { ok: false, kind: "local-auth", code: "AUTH_REQUIRED" },
  },
  {
    name: "classifies structured invalid local session errors",
    error: () => providerApiError("AUTH_INVALID"),
    expected: { ok: false, kind: "local-auth", code: "AUTH_INVALID" },
  },
  {
    name: "classifies structured model service errors",
    error: () => providerApiError("MODEL_FETCH_FAILED"),
    expected: { ok: false, kind: "model-service", code: "MODEL_FETCH_FAILED" },
  },
  {
    name: "does not infer an upstream key rejection from AUTH_FAILED",
    error: () => providerApiError("AUTH_FAILED"),
    expected: { ok: false, kind: "unknown" },
  },
  {
    name: "classifies only the fixed HTTP 5xx error shape as a model service error",
    error: () => new Error("API 503: Service Unavailable"),
    expected: { ok: false, kind: "model-service" },
  },
  {
    name: "does not infer local session state from a bare HTTP 401",
    error: () => new Error("API 401: Unauthorized"),
    expected: { ok: false, kind: "unknown" },
  },
  {
    name: "does not infer an auth category from arbitrary text containing 401",
    error: () => new Error("provider rejected a request with status 401"),
    expected: { ok: false, kind: "unknown" },
  },
  {
    name: "classifies fetch TypeError without returning its message",
    error: () => new TypeError("private network diagnostic"),
    expected: { ok: false, kind: "network" },
  },
  {
    name: "classifies AbortError as a timeout",
    error: () => new DOMException("private timeout diagnostic", "AbortError"),
    expected: { ok: false, kind: "timeout" },
  },
  {
    name: "keeps unknown error codes generic",
    error: () => providerApiError("PROVIDER_KEY_REJECTED"),
    expected: { ok: false, kind: "unknown" },
  },
  {
    name: "keeps unstructured errors generic",
    error: () => new Error("private credential detail"),
    expected: { ok: false, kind: "unknown" },
  },
  {
    name: "treats malformed successful responses as unknown",
    response: { models: "not-an-array" },
    expected: { ok: false, kind: "unknown" },
  },
];

describe("provider verification outcomes", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    apiGetMock.mockReset().mockResolvedValue({ settings: {} });
    apiPostMock.mockReset();
    apiPutMock.mockReset();
    logMock.mockReset();
    showErrorMock.mockReset();
  });

  it("returns a success result and persists credentials only after receiving models", async () => {
    const store = await loadSettingsStore({});
    const provider = store.getState().addProvider("Fixture provider", "https://provider.test/v1", "secret-provider-key");
    apiPostMock.mockImplementation(async (path: string) => (
      path === "models" ? { models: ["model-a"] } : {}
    ));

    await expect(store.getState().verifyProvider(provider.id)).resolves.toEqual({ ok: true, modelCount: 1 });

    expect(store.getState().providers[0]).toMatchObject({ verified: true, models: ["model-a"] });
    await vi.waitFor(() => expect(apiPostMock).toHaveBeenCalledWith("auth/store-key", {
      provider: provider.id,
      api_key: "secret-provider-key",
      base_url: "https://provider.test/v1",
    }));
  });

  it.each(providerVerificationFailureCases)("$name", async ({ error, response, expected }) => {
    const store = await loadSettingsStore({});
    const provider = {
      id: "provider-1",
      name: "Fixture provider name",
      baseUrl: "https://provider.test/v1",
      apiKey: "secret-provider-key",
      verified: true,
      models: ["previous-model"],
    };
    store.setState({ providers: [provider] });
    logMock.mockClear();
    if (error) {
      apiPostMock.mockRejectedValueOnce(error());
    } else {
      apiPostMock.mockResolvedValueOnce(response);
    }

    await expect(store.getState().verifyProvider(provider.id)).resolves.toEqual(expected);

    expect(store.getState().providers).toEqual([provider]);
    expect(apiPostMock.mock.calls.map(([path]) => path)).toEqual(["models"]);
    const logged = logMock.mock.calls.flat().map(String).join(" ");
    expect(logged).not.toContain("Fixture provider name");
    expect(logged).not.toContain("secret-provider-key");
    expect(logged).not.toContain("private raw error");
    expect(logged).not.toContain("private response detail");
    expect(logged).not.toContain("private network diagnostic");
    expect(logged).not.toContain("private timeout diagnostic");
  });

  it("reports an empty model list separately without changing verification state", async () => {
    const store = await loadSettingsStore({});
    const provider = {
      id: "provider-1",
      name: "Fixture",
      baseUrl: "https://provider.test/v1",
      apiKey: "secret-provider-key",
      verified: true,
      models: ["previous-model"],
    };
    store.setState({ providers: [provider] });
    apiPostMock.mockResolvedValueOnce({ models: [] });

    await expect(store.getState().verifyProvider(provider.id)).resolves.toEqual({
      ok: false,
      kind: "empty-models",
    });

    expect(store.getState().providers).toEqual([provider]);
    expect(apiPostMock.mock.calls.map(([path]) => path)).toEqual(["models"]);
  });

  it("returns an unknown result when the provider no longer exists", async () => {
    const store = await loadSettingsStore({});

    await expect(store.getState().verifyProvider("missing")).resolves.toEqual({
      ok: false,
      kind: "unknown",
    });

    expect(apiPostMock).not.toHaveBeenCalled();
  });
});
