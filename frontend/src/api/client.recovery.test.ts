import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

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

vi.mock('../stores/useLogStore', () => ({
  useLogStore: {
    getState: () => ({
      log: () => {},
    }),
  },
}));

type SseResponseInit = {
  chunks: string[];
  headers?: Record<string, string>;
  status?: number;
  statusText?: string;
};

function sseResponse({ chunks, headers = {}, status = 200, statusText = 'OK' }: SseResponseInit): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    body,
    headers: new Headers(headers),
  } as unknown as Response;
}

function event(id: string, payload: Record<string, unknown>): string {
  return `id: ${id}\ndata: ${JSON.stringify(payload)}\n\n`;
}

describe('apiSSE recovery protocol', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it('parses id lines and exposes lastEventId / runId / seq to the callback', async () => {
    const { apiSSE } = await import('./client');
    mockFetch.mockResolvedValueOnce(sseResponse({
      chunks: [
        event('r_1:1', { type: 'run_started', run_id: 'r_1', seq: 1 }),
        event('r_1:2', { type: 'text', content: 'hello', run_id: 'r_1', seq: 2 }),
        event('r_1:3', { type: 'done', success: true, run_id: 'r_1', seq: 3 }),
      ],
      headers: { 'X-Run-Id': 'r_1' },
    }));

    const onEvent = vi.fn();
    const onDone = vi.fn();
    const onRunId = vi.fn();
    const result = await apiSSE('chat', {}, { onEvent, onDone }, undefined, { onRunId });

    expect(onRunId).toHaveBeenCalledWith('r_1');
    expect(onEvent).toHaveBeenCalledTimes(3);
    expect(onEvent.mock.calls[0][1]).toEqual({ lastEventId: 'r_1:1', runId: 'r_1', seq: 1 });
    expect(onEvent.mock.calls[1][1]).toEqual({ lastEventId: 'r_1:2', runId: 'r_1', seq: 2 });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({ lastEventId: 'r_1:3', runId: 'r_1', lastSeq: 3, done: true });
  });

  it('sends after and Last-Event-ID on a GET replay request', async () => {
    const { apiSSE } = await import('./client');
    mockFetch.mockResolvedValueOnce(sseResponse({
      chunks: [
        event('r_1:3', { type: 'done', success: true, run_id: 'r_1', seq: 3 }),
      ],
    }));

    await apiSSE('runs/r_1/events', undefined, { onEvent: vi.fn() }, undefined, {
      method: 'GET',
      after: 2,
      lastEventId: 'r_1:2',
    });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/runs/r_1/events?after=2');
    expect(init.method).toBe('GET');
    expect(init.body).toBeUndefined();
    expect(init.headers['Last-Event-ID']).toBe('r_1:2');
    expect(init.headers['Content-Type']).toBeUndefined();
  });

  it('reconnects with exponential backoff and resumes after the last applied seq', async () => {
    const { apiSSE } = await import('./client');
    mockFetch
      .mockResolvedValueOnce(sseResponse({
        chunks: [
          event('r_1:1', { type: 'run_started', run_id: 'r_1', seq: 1 }),
          event('r_1:2', { type: 'text', content: 'partial', run_id: 'r_1', seq: 2 }),
        ],
      }))
      .mockResolvedValueOnce(sseResponse({
        chunks: [
          event('r_1:3', { type: 'text', content: ' more', run_id: 'r_1', seq: 3 }),
          event('r_1:4', { type: 'done', success: true, run_id: 'r_1', seq: 4 }),
        ],
      }));

    const onEvent = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    const result = await apiSSE('chat', {}, { onEvent, onDone, onError }, undefined, {
      reconnect: { maxAttempts: 5, baseDelayMs: 1, maxDelayMs: 4 },
    });

    expect(onError).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledTimes(4);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    const [replayUrl, replayInit] = mockFetch.mock.calls[1];
    expect(replayUrl).toBe('/api/runs/r_1/events?after=2');
    expect(replayInit.method).toBe('GET');
    expect(replayInit.headers['Last-Event-ID']).toBe('r_1:2');
    expect(result).toMatchObject({ lastEventId: 'r_1:4', lastSeq: 4, done: true });
  });

  it('gives up after the configured reconnect attempts', async () => {
    const { apiSSE } = await import('./client');
    // Every connection closes without a terminal done event.
    mockFetch.mockImplementation(async () => sseResponse({
      chunks: [event('r_1:1', { type: 'text', content: 'x', run_id: 'r_1', seq: 1 })],
    }));

    const onError = vi.fn();
    await apiSSE('chat', {}, { onEvent: vi.fn(), onError }, undefined, {
      reconnect: { maxAttempts: 5, baseDelayMs: 1, maxDelayMs: 2 },
      after: 1,
    });

    expect(mockFetch).toHaveBeenCalledTimes(6); // initial + 5 retries
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].message).toMatch(/已重连 5 次/);
  });

  it('drops duplicated sequences received on a replay stream', async () => {
    const { apiSSE } = await import('./client');
    mockFetch.mockResolvedValueOnce(sseResponse({
      chunks: [
        event('r_1:3', { type: 'text', content: 'a', run_id: 'r_1', seq: 3 }),
        event('r_1:3', { type: 'text', content: 'duplicate', run_id: 'r_1', seq: 3 }),
        event('r_1:4', { type: 'done', success: true, run_id: 'r_1', seq: 4 }),
      ],
    }));

    const onEvent = vi.fn();
    await apiSSE('runs/r_1/events', undefined, { onEvent }, undefined, {
      method: 'GET',
      after: 2,
      lastEventId: 'r_1:2',
    });

    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls.map(([, meta]) => meta?.seq)).toEqual([3, 4]);
  });
});
