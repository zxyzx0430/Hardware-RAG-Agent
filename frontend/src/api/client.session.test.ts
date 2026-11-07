import { beforeEach, describe, expect, it, vi } from "vitest";

const creds = vi.hoisted(() => ({ providerId: "", apiKey: "" }));

vi.mock("../stores/useSettingsStore", () => ({
  useSettingsStore: {
    getState: () => ({
      resolveChatCreds: () => ({
        providerId: creds.providerId,
        model: "",
        baseUrl: "",
        apiKey: creds.apiKey,
      }),
    }),
  },
}));

vi.mock("../stores/useLogStore", () => ({
  useLogStore: { getState: () => ({ log: () => {} }) },
}));

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

function okJson(data: unknown) {
  return { ok: true, json: async () => ({ success: true, data }) };
}

function authError() {
  return {
    ok: false,
    status: 401,
    statusText: "Unauthorized",
    json: async () => ({
      success: false,
      error: { code: "AUTH_INVALID", message: "token 无效或已过期" },
    }),
  };
}

describe("ensureAuthSession", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    localStorage.clear();
    sessionStorage.clear();
    creds.providerId = "";
    creds.apiKey = "";
  });

  it("keeps a valid session token without logging in again", async () => {
    const { ensureAuthSession } = await import("./client");
    creds.providerId = "ccgo";
    creds.apiKey = "secret-key";
    localStorage.setItem("session_token", "valid-token");
    mockFetch.mockResolvedValueOnce(okJson({ providers: [] }));

    await expect(ensureAuthSession()).resolves.toBe(true);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0][0])).toContain("auth/keys");
  });

  it("exchanges a stale token for a fresh one with the saved API key", async () => {
    const { ensureAuthSession, getSessionToken } = await import("./client");
    creds.providerId = "ccgo";
    creds.apiKey = "secret-key";
    localStorage.setItem("session_token", "stale-token");
    mockFetch.mockResolvedValueOnce(authError());
    mockFetch.mockResolvedValueOnce(okJson({ session_token: "fresh-token" }));

    await expect(ensureAuthSession()).resolves.toBe(true);

    expect(getSessionToken()).toBe("fresh-token");
    const loginCall = mockFetch.mock.calls.find((call) => String(call[0]).includes("auth/login"));
    expect(loginCall).toBeTruthy();
    expect(JSON.parse(String(loginCall?.[1]?.body))).toEqual({
      provider: "ccgo",
      api_key: "secret-key",
    });
  });

  it("logs in with the saved key when no token exists", async () => {
    const { ensureAuthSession, getSessionToken } = await import("./client");
    creds.apiKey = "secret-key";
    mockFetch.mockResolvedValueOnce(okJson({ session_token: "first-token" }));

    await expect(ensureAuthSession()).resolves.toBe(true);

    expect(getSessionToken()).toBe("first-token");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0][0])).toContain("auth/login");
  });

  it("obtains a local session when no API key is stored in the browser", async () => {
    const { ensureAuthSession, getSessionToken } = await import("./client");
    mockFetch.mockResolvedValueOnce(okJson({ session_token: "local-token" }));

    await expect(ensureAuthSession()).resolves.toBe(true);

    expect(getSessionToken()).toBe("local-token");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0][0])).toContain("auth/local-session");
  });

  it("falls back to the local session when the key no longer matches", async () => {
    const { ensureAuthSession, getSessionToken } = await import("./client");
    creds.providerId = "ccgo";
    creds.apiKey = "rotated-key";
    mockFetch.mockResolvedValueOnce(authError());
    mockFetch.mockResolvedValueOnce(okJson({ session_token: "fallback-token" }));

    await expect(ensureAuthSession(true)).resolves.toBe(true);

    expect(getSessionToken()).toBe("fallback-token");
    expect(mockFetch.mock.calls.map((call) => String(call[0]))).toEqual([
      expect.stringContaining("auth/login"),
      expect.stringContaining("auth/local-session"),
    ]);
  });

  it("reports failure when neither the key nor the local bootstrap works", async () => {
    const { ensureAuthSession } = await import("./client");
    mockFetch.mockRejectedValueOnce(new Error("FORBIDDEN"));

    await expect(ensureAuthSession(true)).resolves.toBe(false);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(String(mockFetch.mock.calls[0][0])).toContain("auth/local-session");
  });
});
