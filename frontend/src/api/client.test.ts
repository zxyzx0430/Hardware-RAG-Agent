import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock fetch globally
const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

// Mock useSettingsStore
vi.mock('../stores/useSettingsStore', () => ({
  useSettingsStore: {
    getState: () => ({
      providerKeys: {},
      activeProvider: '',
      model: '',
      getBaseUrl: () => '',
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
    const { apiPost, ApiError } = await import('./client');
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
