import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_TOKEN_CHANGED_EVENT } from "../../api/client";
import { useExplorerWatch } from "./useExplorerWatch";

vi.mock("../../api/client", () => ({
  AUTH_TOKEN_CHANGED_EVENT: "hwrag:auth-token-changed",
  getSessionToken: () => sessionStorage.getItem("session_token"),
  getAuthHeaders: () => {
    const token = sessionStorage.getItem("session_token");
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
  sessionStorage.clear();
  fetchMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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

    sessionStorage.setItem("session_token", "token-1");
    act(() => {
      window.dispatchEvent(new Event(AUTH_TOKEN_CHANGED_EVENT));
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({
      Authorization: "Bearer token-1",
    });
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
