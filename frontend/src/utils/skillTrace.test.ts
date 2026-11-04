import { describe, expect, it } from "vitest";
import {
  isValidSkillId,
  isValidSkillResourcePath,
  projectSkillToolCall,
  projectSkillToolResult,
  projectSkillTraceActivity,
} from "./skillTrace";

const hash = "e".repeat(64);

function validLoadResult(overrides: {
  eventSuccess?: unknown;
  envelopeSuccess?: unknown;
  innerSuccess?: unknown;
  omitInnerSuccess?: boolean;
  skillId?: unknown;
  contentHash?: unknown;
  stage?: unknown;
  error?: unknown;
} = {}) {
  const data: Record<string, unknown> = {
    success: overrides.innerSuccess ?? true,
    skill_id: overrides.skillId ?? "fixture-skill",
    content_hash: overrides.contentHash ?? hash,
    stage: overrides.stage ?? "instructions",
  };
  if (overrides.omitInnerSuccess) delete data.success;
  return {
    eventSuccess: overrides.eventSuccess ?? true,
    result: {
      success: overrides.envelopeSuccess ?? true,
      output: "RAW_INSTRUCTIONS_SENTINEL",
      data,
      ...(overrides.error !== undefined ? { error: overrides.error } : { error: null }),
    },
  };
}

describe("Skill tool call projection", () => {
  it("keeps only a valid Skill ID and preserves resource path casing", () => {
    expect(projectSkillToolCall("load_skill", {
      skill_id: "Fixture_Skill-1",
      instructions: "RAW_INSTRUCTIONS_SENTINEL",
      extra: "RAW_ARGS_SENTINEL",
    })).toEqual({
      args: JSON.stringify({ skill_id: "Fixture_Skill-1" }),
      trace: { skill_id: "Fixture_Skill-1", stage: "instructions" },
    });
    expect(projectSkillToolCall("read_skill_resource", {
      skill_id: "fixture-skill",
      path: "References/Pins.MD",
      content: "RAW_RESOURCE_SENTINEL",
    })).toEqual({
      args: JSON.stringify({ skill_id: "fixture-skill", path: "References/Pins.MD" }),
      trace: { skill_id: "fixture-skill", stage: "resource", path: "References/Pins.MD" },
    });
  });

  it.each([
    ["empty ID", ""],
    ["leading punctuation", "_fixture"],
    ["overlong ID", `a${"b".repeat(128)}`],
  ])("rejects %s", (_label, skillId) => {
    expect(isValidSkillId(skillId)).toBe(false);
    expect(projectSkillToolCall("load_skill", { skill_id: skillId })).toEqual({ args: "{}" });
  });

  it.each([
    "../secret.md",
    "/references/secret.md",
    "References\\Pins.md",
    "References/../secret.md",
    "references//secret.md",
    "references/CON.md",
    "references/Pins.",
    "references/no-extension",
    "references/payload.py",
    "scripts/payload.md",
    "references/secret?.md",
    "references/e\u0301.md",
    `references/${"a".repeat(1011)}.md`,
  ])("rejects unsafe or unsupported resource path %s", (path) => {
    expect(isValidSkillResourcePath(path)).toBe(false);
    expect(projectSkillToolCall("read_skill_resource", { skill_id: "fixture-skill", path })).toEqual({ args: "{}" });
  });

  it("does not project lookalike or ordinary tool names", () => {
    expect(projectSkillToolCall("load_skill_backup", { skill_id: "fixture-skill" })).toBeNull();
    expect(projectSkillToolCall("search_docs", { query: "keep ordinary args" })).toBeNull();
  });
});

describe("Skill tool result projection", () => {
  it("requires strict outer and inner success plus a complete matching trace", () => {
    const call = projectSkillToolCall("load_skill", { skill_id: "fixture-skill" })!;
    const valid = validLoadResult();
    expect(projectSkillToolResult("load_skill", call.args, call.trace, valid.result, valid.eventSuccess)).toEqual({
      success: true,
      result: "Skill instructions loaded",
      trace: {
        skill_id: "fixture-skill",
        content_hash: hash,
        stage: "instructions",
        success: true,
      },
    });
    expect(JSON.stringify(projectSkillToolResult("load_skill", call.args, call.trace, valid.result, valid.eventSuccess)))
      .not.toContain("RAW_INSTRUCTIONS_SENTINEL");
  });

  it.each([
    ["non-boolean SSE success", { eventSuccess: "true" }],
    ["non-boolean envelope success", { envelopeSuccess: "true" }],
    ["non-boolean inner success", { innerSuccess: "true" }],
    ["integer inner success", { innerSuccess: 1 }],
    ["inner failure conflicts with outer success", { innerSuccess: false }],
    ["missing inner success", { omitInnerSuccess: true }],
    ["outer failure", { eventSuccess: false }],
    ["error envelope", { error: { error_message: "RAW_ERROR_SENTINEL" } }],
    ["mismatched Skill ID", { skillId: "other-skill" }],
    ["mismatched stage", { stage: "resource" }],
    ["uppercase hash", { contentHash: "E".repeat(64) }],
    ["short hash", { contentHash: "e".repeat(63) }],
  ])("fails closed for %s", (_label, overrides) => {
    const call = projectSkillToolCall("load_skill", { skill_id: "fixture-skill" })!;
    const invalid = validLoadResult(overrides);
    const projected = projectSkillToolResult(
      "load_skill",
      call.args,
      call.trace,
      invalid.result,
      invalid.eventSuccess,
    );
    expect(projected).toMatchObject({
      success: false,
      result: "Skill operation failed",
      trace: { skill_id: "fixture-skill", stage: "instructions", success: false },
    });
    expect(JSON.stringify(projected)).not.toContain("RAW_");
  });

  it("requires resource path and tool-call trace to match exactly", () => {
    const call = projectSkillToolCall("read_skill_resource", {
      skill_id: "fixture-skill",
      path: "References/Pins.MD",
    })!;
    const result = {
      success: true,
      output: "RAW_RESOURCE_SENTINEL",
      data: {
        success: true,
        skill_id: "fixture-skill",
        content_hash: hash,
        stage: "resource",
        path: "references/Pins.MD",
      },
      error: null,
    };
    expect(projectSkillToolResult("read_skill_resource", call.args, call.trace, result, true)).toMatchObject({
      success: false,
      result: "Skill operation failed",
      trace: { path: "References/Pins.MD", success: false },
    });
    expect(projectSkillToolResult("read_skill_resource", call.args, undefined, result, true)).toMatchObject({
      success: false,
      result: "Skill operation failed",
    });
  });

  it("keeps failures safe when legacy results omit trace metadata", () => {
    const call = projectSkillToolCall("load_skill", { skill_id: "fixture-skill" })!;
    const projected = projectSkillToolResult(
      "load_skill",
      call.args,
      call.trace,
      { success: false, output: "RAW_ERROR_SENTINEL", error: { error_message: "RAW_ERROR_SENTINEL" } },
      false,
    );
    expect(projected).toEqual({
      success: false,
      result: "Skill operation failed",
      trace: { skill_id: "fixture-skill", stage: "instructions", success: false },
    });
    expect(projectSkillToolResult("load_skill", call.args, call.trace, "RAW_JSON_SENTINEL", false)).toMatchObject({
      success: false,
      result: "Skill operation failed",
    });
  });
});

describe("Skill activity projection", () => {
  it("filters Skill bodies without mutating input or changing ordinary steps", () => {
    const input = {
      durationMs: 10,
      steps: [
        { type: "thinking", id: "reasoning", source: "reasoning", content: "reasoning remains" },
        {
          type: "tool",
          id: "skill-call",
          call_id: "skill-call",
          name: "load_skill",
          args: JSON.stringify({ skill_id: "fixture-skill", content: "RAW_ARGS_SENTINEL" }),
          result: "RAW_BODY_SENTINEL",
          status: "done",
          skill_trace: {
            skill_id: "fixture-skill",
            content_hash: hash,
            stage: "instructions",
            success: true,
            instructions: "RAW_TRACE_SENTINEL",
          },
        },
        {
          type: "tool",
          id: "ordinary-call",
          call_id: "ordinary-call",
          name: "write_file",
          args: "{\"path\":\"output.txt\"}",
          result: "ordinary result",
          status: "done",
        },
      ],
    };

    const projected = projectSkillTraceActivity(input) as typeof input;
    expect(projected).toMatchObject({ durationMs: 10 });
    expect(projected.steps[0]).toEqual(input.steps[0]);
    expect(projected.steps[1]).toMatchObject({
      args: JSON.stringify({ skill_id: "fixture-skill" }),
      result: "Skill instructions loaded",
      status: "done",
      skill_trace: {
        skill_id: "fixture-skill",
        content_hash: hash,
        stage: "instructions",
        success: true,
      },
    });
    expect(projected.steps[2]).toEqual(input.steps[2]);
    expect(JSON.stringify(projected)).not.toContain("RAW_");
    expect(input.steps[1].result).toBe("RAW_BODY_SENTINEL");
    expect(projectSkillTraceActivity(projected)).toEqual(projected);
  });

  it("does not let a pending or legacy Skill step claim success", () => {
    const activity = {
      steps: [
        {
          type: "tool",
          id: "pending-call",
          call_id: "pending-call",
          name: "load_skill",
          args: JSON.stringify({ skill_id: "fixture-skill" }),
          status: "pending",
          skill_trace: {
            skill_id: "fixture-skill",
            content_hash: hash,
            stage: "instructions",
            success: true,
          },
        },
        {
          type: "tool",
          id: "legacy-call",
          call_id: "legacy-call",
          name: "read_skill_resource",
          args: JSON.stringify({ skill_id: "fixture-skill", path: "references/legacy.md" }),
          result: "{\"output\":\"RAW_LEGACY_SENTINEL\"}",
          status: "done",
        },
      ],
    };
    const projected = projectSkillTraceActivity(activity) as typeof activity;
    expect(projected.steps[0]).toMatchObject({ status: "pending", skill_trace: { success: false } });
    expect(projected.steps[1]).toMatchObject({ status: "error", result: "Skill operation failed" });
    expect(JSON.stringify(projected)).not.toContain("RAW_LEGACY_SENTINEL");
  });
});
