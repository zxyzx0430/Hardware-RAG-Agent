import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_TOKEN_CHANGED_EVENT } from "../../api/client";
import { useExplorerWatch } from "./useExplorerWatch";

vi.mock("../../api/client", () => ({
  AUTH_TOKEN_CHANGED_EVENT: "hwrag:auth-token-changed",
  getSessionToken: () => localStorage.getItem("session_token"),
  getAuthHeaders: () => {
    const token = localStorage.getItem("session_token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  },
}));

vi.mock("../../stores/useLogStore", () => ({
  useLogStore: { getState: () => ({ log: vi.fn() }) },
}));

const fetchMock = vi.fn();

type FetchCall = { headers: Record<string, string> };

function installFetch(unauthorizedFirst: boolean): FetchCall[] {
  const calls: FetchCall[] = [];
  fetchMock.mockImplementation((_url: string, init: RequestInit) => {
    calls.push({ headers: (init.headers ?? {}) as Record<string, string> });
    if (unauthorizedFirst && calls.length === 1) {
      return Promise.resolve({ ok: false, status: 401, statusText: "Unauthorized" });
    }
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        init.signal?.addEventListener("abort", () => {
          controller.error(new DOMException("Aborted", "AbortError"));
        });
      },
    });
    return Promise.resolve({ ok: true, body });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  fetchMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  sessionStorage.clear();
});

describe("useExplorerWatch auth handling", () => {
  it("waits for login after a 401 and reconnects with the new token", async () => {
    installFetch(true);
    const onAuthRequired = vi.fn();
    const onError = vi.fn();
    const { unmount } = renderHook(() => useExplorerWatch(
      "C:/project",
      undefined,
      onError,
      undefined,
      onAuthRequired,
    ));

    await waitFor(() => expect(onAuthRequired).toHaveBeenCalledTimes(1));
    expect(onError).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    localStorage.setItem("session_token", "token-1");
    act(() => {
      window.dispatchEvent(new Event(AUTH_TOKEN_CHANGED_EVENT));
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({
      Authorization: "Bearer token-1",
    });
    unmount();
  });

  it("uses a token that arrives while the failing request is in flight", async () => {
    let resolveFirst!: (value: unknown) => void;
    const calls: FetchCall[] = [];
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      calls.push({ headers: (init.headers ?? {}) as Record<string, string> });
      if (calls.length === 1) {
        return new Promise((resolve) => { resolveFirst = resolve; });
      }
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          init.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          });
        },
      });
      return Promise.resolve({ ok: true, body });
    });
    vi.stubGlobal("fetch", fetchMock);

    const onAuthRequired = vi.fn();
    const { unmount } = renderHook(() => useExplorerWatch(
      "C:/project",
      undefined,
      undefined,
      undefined,
      onAuthRequired,
    ));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // The token arrives before the request that lacks it fails.
    localStorage.setItem("session_token", "token-race");
    act(() => {
      window.dispatchEvent(new Event(AUTH_TOKEN_CHANGED_EVENT));
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst({ ok: false, status: 401, statusText: "Unauthorized" });
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(calls[1].headers).toMatchObject({ Authorization: "Bearer token-race" });
    expect(onAuthRequired).not.toHaveBeenCalled();
    unmount();
  });

  it("does not treat an auth failure as a generic watch error", async () => {
    installFetch(true);
    const onError = vi.fn();
    const onAuthRequired = vi.fn();
    const statuses: boolean[] = [];
    const { unmount } = renderHook(() => useExplorerWatch(
      "C:/project",
      undefined,
      onError,
      (connected) => statuses.push(connected),
      onAuthRequired,
    ));

    await waitFor(() => expect(onAuthRequired).toHaveBeenCalledTimes(1));
    expect(onError).not.toHaveBeenCalled();
    await waitFor(() => expect(statuses).toContain(false));
    unmount();
  });
});

describe("useExplorerWatch heartbeats", () => {
  it("reconnects when the heartbeat stream goes silent", async () => {
    let calls = 0;
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      calls += 1;
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          init.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          });
        },
      });
      return Promise.resolve({ ok: true, body });
    });
    vi.stubGlobal("fetch", fetchMock);

    const statuses: boolean[] = [];
    const { unmount } = renderHook(() => useExplorerWatch(
      "C:/project",
      undefined,
      undefined,
      (connected) => statuses.push(connected),
      undefined,
      undefined,
      0,
      20,
    ));

    await waitFor(() => expect(statuses).toContain(false), { timeout: 1000 });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), { timeout: 2500 });
    expect(calls).toBe(2);
    unmount();
  });
});

describe("useExplorerWatch folder grant handling", () => {
  it("waits for a reopen after 403 instead of asking for a login or retrying", async () => {
    let calls = 0;
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      calls += 1;
      if (calls === 1) {
        return Promise.resolve({ ok: false, status: 403, statusText: "Forbidden" });
      }
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          init.signal?.addEventListener("abort", () => {
            controller.error(new DOMException("Aborted", "AbortError"));
          });
        },
      });
      return Promise.resolve({ ok: true, body });
    });
    vi.stubGlobal("fetch", fetchMock);

    const onAuthRequired = vi.fn();
    const onForbidden = vi.fn();
    const onError = vi.fn();
    const statuses: boolean[] = [];
    const { rerender, unmount } = renderHook(
      ({ reconnectKey }) => useExplorerWatch(
        "C:/project",
        undefined,
        onError,
        (connected) => statuses.push(connected),
        onAuthRequired,
        onForbidden,
        reconnectKey,
      ),
      { initialProps: { reconnectKey: 0 } },
    );

    await waitFor(() => expect(onForbidden).toHaveBeenCalledTimes(1));
    expect(onAuthRequired).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    rerender({ reconnectKey: 1 });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(statuses.at(-1)).toBe(true));
    unmount();
  });
});
