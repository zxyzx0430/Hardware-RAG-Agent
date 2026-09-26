import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock fetch globally
const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

// Mock useSettingsStore
vi.mock('../stores/useSettingsStore', () => ({
  useSettingsStore: {
    getState: () => ({
      providers: [],
      chatProviderId: '',
      chatModel: '',
      resolveChatCreds: () => ({ providerId: '', model: '', baseUrl: '', apiKey: '' }),
    }),
  },
}));

// Mock useLogStore
vi.mock('../stores/useLogStore', () => ({
  useLogStore: {
    getState: () => ({
      log: () => {},
    }),
  },
}));

describe('apiPost unwrapResponse', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it('unwraps {success: true, data: ...} format', async () => {
    const { apiPost } = await import('./client');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true, data: { results: [1, 2] } }),
    });
    const result = await apiPost<{ results: number[] }>('test');
    expect(result).toEqual({ results: [1, 2] });
  });

  it('throws ApiError on {success: false, error: ...}', async () => {
    const { apiPost } = await import('./client');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: false, error: { code: 'TEST_ERR', message: 'test error' } }),
    });
    await expect(apiPost('test')).rejects.toThrow();
  });

  it('returns raw json for legacy responses without success field', async () => {
    const { apiGet } = await import('./client');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ sessions: [{ id: '1' }] }),
    });
    const result = await apiGet<{ sessions: { id: string }[] }>('sessions');
    expect(result.sessions).toHaveLength(1);
  });
});

describe('apiSSE connection loss', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it('reports a stalled chat stream after heartbeats stop', async () => {
    vi.useFakeTimers();
    const { apiSSE } = await import('./client');
    const externalController = new AbortController();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    mockFetch.mockImplementationOnce((_url, options: RequestInit) => {
      options.signal?.addEventListener('abort', () => {
        streamController.error(new Error('aborted'));
      });
      return Promise.resolve({ ok: true, body });
    });
    const onError = vi.fn();
    const onDone = vi.fn();

    try {
      const request = apiSSE('chat', {}, { onEvent: vi.fn(), onError, onDone }, externalController);
      await vi.advanceTimersByTimeAsync(45_001);
      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError.mock.calls[0][0].message).toMatch(/超时|断开/);
      expect(onDone).not.toHaveBeenCalled();
      await request;
    } finally {
      externalController.abort();
      vi.useRealTimers();
    }
  });

  it('keeps an active chat stream open when heartbeats arrive', async () => {
    vi.useFakeTimers();
    const { apiSSE } = await import('./client');
    const externalController = new AbortController();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
      },
    });
    mockFetch.mockImplementationOnce((_url, options: RequestInit) => {
      options.signal?.addEventListener('abort', () => {
        streamController.error(new Error('aborted'));
      });
      return Promise.resolve({ ok: true, body });
    });
    const onError = vi.fn();
    const onEvent = vi.fn();

    try {
      const request = apiSSE('chat', {}, { onEvent, onError }, externalController);
      await vi.advanceTimersByTimeAsync(30_000);
      streamController.enqueue(new TextEncoder().encode('event: heartbeat\ndata: {}\n\n'));
      await vi.advanceTimersByTimeAsync(20_000);
      expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'heartbeat' }));
      expect(onError).not.toHaveBeenCalled();
      externalController.abort();
      await request;
    } finally {
      externalController.abort();
      vi.useRealTimers();
    }
  });
});
