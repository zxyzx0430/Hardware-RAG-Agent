import { describe, expect, it } from "vitest";
import { buildPlanMarkdown } from "./planMarkdown";
import type { Plan } from "../types/plan";

const LABELS = {
  summary: "摘要",
  steps: "步骤",
  verification: "验证",
  risks: "风险",
  openQuestions: "待确认问题",
};

function makePlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: "pl_1",
    session_id: "s1",
    version: 1,
    status: "pending",
    title: "Bring up the sensor",
    summary: "**结论**：先读手册。",
    steps: [{ title: "Inspect", detail: "确认 `0x68`", verification: "i2cdetect" }],
    risks: ["Board revision may differ"],
    open_questions: ["Is it externally powered?"],
    created_at: "2026-11-08T00:00:00Z",
    ...overrides,
  };
}

describe("buildPlanMarkdown", () => {
  it("renders the fixed template with heading hierarchy and bold step titles", () => {
    const markdown = buildPlanMarkdown(makePlan(), LABELS);

    expect(markdown).toContain("## Bring up the sensor");
    expect(markdown).toContain("### 摘要");
    expect(markdown).toContain("### 步骤");
    expect(markdown).toContain("1. **Inspect**");
    expect(markdown).toContain("- **验证**: i2cdetect");
    expect(markdown).toContain("### 风险");
    expect(markdown).toContain("### 待确认问题");
  });

  it("omits empty sections instead of emitting bare headings", () => {
    const markdown = buildPlanMarkdown(
      makePlan({ summary: "", steps: [], risks: [], open_questions: [] }),
      LABELS,
    );

    expect(markdown).toBe("## Bring up the sensor");
  });

  it("keeps the tenth step detail inside its ordered-list item", () => {
    const steps = Array.from({ length: 10 }, (_, index) => ({
      title: `Step ${index + 1}`,
      detail: `Detail ${index + 1}`,
      verification: "",
    }));
    const markdown = buildPlanMarkdown(makePlan({ steps }), LABELS);

    expect(markdown).toContain("10. **Step 10**\n\n    Detail 10");
  });
});
