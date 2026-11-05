import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

async function importRecovery() {
  return await import('./runRecovery');
}

describe('runRecovery storage', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('persists per-session run records and reloads them after a refresh', async () => {
    const recovery = await importRecovery();
    recovery.startActiveRun('s1', 'r_1');
    recovery.updateRunSeq('s1', 'r_1', 3);
    recovery.flushRunRecords();

    const stored = JSON.parse(localStorage.getItem(recovery.RUN_RECORDS_STORAGE_KEY) ?? 'null');
    expect(stored).toMatchObject({
      s1: { runId: 'r_1', sessionId: 's1', lastEventSeq: 3, status: 'running' },
    });

    // Simulate a page reload: a fresh module instance reads localStorage.
    vi.resetModules();
    const reloaded = await importRecovery();
    expect(reloaded.getActiveRun('s1')).toMatchObject({ runId: 'r_1', lastEventSeq: 3 });
  });

  it('rejects sequences that belong to a different (stale) run', async () => {
    const recovery = await importRecovery();
    recovery.startActiveRun('s1', 'r_1');
    expect(recovery.updateRunSeq('s1', 'r_2', 5)).toBeNull();
    expect(recovery.getActiveRun('s1')?.runId).toBe('r_1');
  });

  it('tracks status changes and clears terminal runs', async () => {
    const recovery = await importRecovery();
    recovery.startActiveRun('s1', 'r_1');
    recovery.setRunStatus('s1', 'r_1', 'awaiting_confirmation');
    expect(recovery.getActiveRun('s1')?.status).toBe('awaiting_confirmation');

    recovery.clearActiveRun('s1');
    expect(recovery.getActiveRun('s1')).toBeUndefined();
    expect(localStorage.getItem(recovery.RUN_RECORDS_STORAGE_KEY)).toBeNull();
  });

  it('ignores corrupted storage instead of throwing', async () => {
    localStorage.setItem('hwrag_active_runs_v1', '{not-json');
    const recovery = await importRecovery();
    expect(recovery.loadRunRecords()).toEqual({});
  });
});

describe('run event metadata and approvals', () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  it('prefers payload run_id/seq over id-line metadata', async () => {
    const recovery = await importRecovery();
    const meta = recovery.parseRunEventMeta(
      { type: 'text', content: 'x', run_id: 'r_payload', seq: 7 } as never,
      { runId: 'r_idline', seq: 6, lastEventId: 'r_idline:6' },
    );
    expect(meta).toEqual({ runId: 'r_payload', seq: 7 });
  });

  it('falls back to id-line metadata when the payload has no run fields', async () => {
    const recovery = await importRecovery();
    const meta = recovery.parseRunEventMeta(
      { type: 'text', content: 'x' } as never,
      { runId: 'r_idline', seq: 6, lastEventId: 'r_idline:6' },
    );
    expect(meta).toEqual({ runId: 'r_idline', seq: 6 });
  });

  it('restores only pending, unexpired approvals and parses string args', async () => {
    const recovery = await importRecovery();
    const calls = recovery.pendingApprovalsToCalls([
      {
        id: 'a1',
        run_id: 'r_1',
        call_id: 'call-1',
        tool_name: 'write_file',
        args: '{"path":"main.c"}',
        risk_level: 'high',
        status: 'pending',
      },
      {
        id: 'a2',
        run_id: 'r_1',
        call_id: 'call-2',
        tool_name: 'run_command',
        args: { cmd: 'ls' },
        risk_level: 'medium',
        status: 'allowed',
      },
      {
        id: 'a3',
        run_id: 'r_1',
        call_id: 'call-3',
        tool_name: 'flash_firmware',
        args: {},
        risk_level: 'high',
        status: 'pending',
        expires_at: '2000-01-01T00:00:00Z',
      },
    ]);

    expect(calls).toEqual([
      {
        name: 'write_file',
        args: { path: 'main.c' },
        call_id: 'call-1',
        risk_level: 'high',
      },
    ]);
  });

  it('does not fabricate a confirmation when no approval is pending', async () => {
    const recovery = await importRecovery();
    expect(recovery.pendingApprovalsToCalls(undefined)).toEqual([]);
    expect(recovery.pendingApprovalsToCalls([{
      id: 'a1',
      run_id: 'r_1',
      call_id: 'call-1',
      tool_name: 'write_file',
      args: {},
      status: 'denied',
    }])).toEqual([]);
  });
});
