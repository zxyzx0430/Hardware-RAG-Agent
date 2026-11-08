import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  getAuthHeaders: () => ({}),
}));

import {
  assessInterruptedRun,
  fetchActiveRun,
  normalizeRunStatePayload,
  runStateRunId,
} from "./runRecovery";

describe("active run contract", () => {
  beforeEach(() => {
    apiGetMock.mockReset();
  });

  it("normalizes wrapped, raw and list payloads", () => {
    expect(normalizeRunStatePayload({
      run: { id: "r_1", status: "awaiting_confirmation", last_event_seq: 4 },
    })).toMatchObject({ id: "r_1", status: "awaiting_confirmation", last_event_seq: 4 });

    expect(normalizeRunStatePayload({
      active: { run_id: "r_2", status: "running", checkpoint_id: "c_2" },
    })).toMatchObject({ run_id: "r_2", status: "running", checkpoint_id: "c_2" });

    expect(normalizeRunStatePayload({
      runs: [
        { id: "r_old", status: "completed" },
        { id: "r_new", status: "running" },
      ],
    })).toMatchObject({ id: "r_old", status: "completed" });

    expect(normalizeRunStatePayload({ id: "r_raw", status: "running" }))
      .toMatchObject({ id: "r_raw", status: "running" });
  });

  it("returns null for empty or malformed payloads instead of inventing a run", () => {
    expect(normalizeRunStatePayload(null)).toBeNull();
    expect(normalizeRunStatePayload({})).toBeNull();
    expect(normalizeRunStatePayload({ active: null })).toBeNull();
    expect(normalizeRunStatePayload({ run: null })).toBeNull();
    expect(normalizeRunStatePayload({ runs: [] })).toBeNull();
    expect(normalizeRunStatePayload("not-a-run")).toBeNull();
  });

  it("fetches the per-session active run and tolerates an unsupported route", async () => {
    apiGetMock.mockResolvedValueOnce({ run: { id: "r_1", status: "running" } });
    await expect(fetchActiveRun("s 1")).resolves.toMatchObject({ id: "r_1", status: "running" });
    expect(apiGetMock).toHaveBeenCalledWith("sessions/s%201/runs/active");

    apiGetMock.mockRejectedValueOnce(new Error("API 404: Not Found"));
    await expect(fetchActiveRun("s1")).resolves.toBeNull();
  });

  it("uses either run id field", () => {
    expect(runStateRunId({ run_id: "r_a", id: "r_b", status: "running" })).toBe("r_a");
    expect(runStateRunId({ id: "r_b", status: "running" })).toBe("r_b");
    expect(runStateRunId(null)).toBeNull();
  });
});

describe("interrupted run assessment", () => {
  it("reports a resumable checkpoint without claiming success", () => {
    const assessment = assessInterruptedRun({
      id: "r_1",
      status: "interrupted",
      checkpoint_id: "cp_1",
      error_code: "PROCESS_RESTART",
    });
    expect(assessment.resumable).toBe(true);
    expect(assessment.resultUnknown).toBe(false);
    expect(assessment.message).toContain("检查点");
  });

  it("flags a tool left pending in flight as an unknown result", () => {
    const assessment = assessInterruptedRun(
      { id: "r_1", status: "interrupted" },
      { name: "write_file", status: "pending" },
    );
    expect(assessment.resultUnknown).toBe(true);
    expect(assessment.hardwareUnknown).toBe(false);
    expect(assessment.message).toContain("结果未知");
  });

  it("calls out hardware side effects explicitly", () => {
    const assessment = assessInterruptedRun(
      { id: "r_1", status: "interrupted" },
      { name: "flash_firmware", status: "running" },
    );
    expect(assessment.resultUnknown).toBe(true);
    expect(assessment.hardwareUnknown).toBe(true);
    expect(assessment.message).toContain("核对设备");
  });

  it("honors a backend result_unknown flag even without a local step", () => {
    const assessment = assessInterruptedRun({
      id: "r_1",
      status: "interrupted",
      result_unknown: true,
      last_tool: "serial_send",
    });
    expect(assessment.resultUnknown).toBe(true);
    expect(assessment.hardwareUnknown).toBe(true);
  });

  it("does not treat a completed tool step as unknown", () => {
    const assessment = assessInterruptedRun(
      { id: "r_1", status: "interrupted" },
      { name: "flash_firmware", status: "done" },
    );
    expect(assessment.resultUnknown).toBe(false);
    expect(assessment.hardwareUnknown).toBe(false);
  });
});
