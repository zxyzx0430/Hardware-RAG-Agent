import { describe, expect, it } from "vitest";
import { normalizePlan, planFromEnvelope } from "./planTodo";

const basePlan = {
  id: "pl_1",
  session_id: "s1",
  version: 2,
  status: "approved",
  title: "Plan",
  summary: "",
  steps: [],
  risks: [],
  open_questions: [],
  created_at: "2026-11-06T10:00:00Z",
};

describe("plan execution state normalization", () => {
  it("preserves valid execution_state and execution_note fields", () => {
    const plan = normalizePlan({
      ...basePlan,
      execution_state: "failed",
      execution_note: "write_file denied",
    });

    expect(plan).toMatchObject({
      execution_state: "failed",
      execution_note: "write_file denied",
    });
  });

  it("keeps older payloads without execution fields unchanged", () => {
    const plan = normalizePlan(basePlan);

    expect(plan).not.toBeNull();
    expect(plan).not.toHaveProperty("execution_state");
    expect(plan).not.toHaveProperty("execution_note");
  });

  it("drops unknown execution states instead of rendering an empty label", () => {
    const plan = normalizePlan({ ...basePlan, execution_state: "exploded" });

    expect(plan).not.toHaveProperty("execution_state");
  });

  it("unwraps execution state from the envelope form", () => {
    const plan = planFromEnvelope({
      plan: { ...basePlan, execution_state: "rejected", execution_note: "user denied the tool call" },
    });

    expect(plan?.execution_state).toBe("rejected");
    expect(plan?.execution_note).toBe("user denied the tool call");
  });
});
