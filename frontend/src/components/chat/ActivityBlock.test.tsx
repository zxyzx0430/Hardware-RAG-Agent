import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ActivityBlock as ActivityBlockData, ActivityStep } from "../../types/session";
import ActivityBlock from "./ActivityBlock";

vi.mock("../../i18n", () => ({
  useI18n: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

vi.mock("../../stores/useAppStore", () => {
  const state = {
    setExplorerOpen: () => {},
    openFile: async (_path: string) => {},
    openDiffForFile: (_path: string) => {},
    lang: "zh",
  };
  return { useAppStore: <T,>(selector: (store: typeof state) => T) => selector(state) };
});

afterEach(cleanup);

const CONTENT_HASH = "a".repeat(64);
type SkillToolName = "load_skill" | "read_skill_resource";

function makeSkillStep(
  name: SkillToolName,
  trace: unknown,
  status: unknown = "done",
  extra: Record<string, unknown> = {},
): ActivityStep {
  return {
    id: "synthetic-skill-call",
    type: "tool",
    name,
    args: JSON.stringify({ skill_id: "fixture-skill", path: "References/Pins.MD", raw: "RAW_ARGUMENT_SENTINEL" }),
    result: "RAW_RESULT_SENTINEL",
    status,
    skill_trace: trace,
    ...extra,
  } as unknown as ActivityStep;
}

function renderOpenSkillStep(
  step: ActivityStep,
  activityStatus: ActivityBlockData["status"] = "done",
) {
  const activity = { durationMs: 1, steps: [step], status: activityStatus } as ActivityBlockData;
  const rendered = render(<ActivityBlock activity={activity} msgId="synthetic-message" />);
  if (!rendered.container.querySelector(".activity-steps")) {
    fireEvent.click(screen.getAllByRole("button")[0]);
  }
  const skillButton = screen.getAllByRole("button")[1];
  fireEvent.click(skillButton);
  return { ...rendered, skillButton };
}

describe("ActivityBlock Skill trace rendering", () => {
  it("does not show raw Skill inputs or outputs", () => {
    const step = makeSkillStep("load_skill", {
      skill_id: "fixture-skill",
      content_hash: CONTENT_HASH,
      stage: "instructions",
      success: true,
    });
    const { container } = renderOpenSkillStep(step);

    expect(container.innerHTML).not.toContain("RAW_ARGUMENT_SENTINEL");
    expect(container.innerHTML).not.toContain("RAW_RESULT_SENTINEL");
  });

  it.each([
    { name: "load_skill" as const, stage: "instructions", path: undefined, label: "加载技能" },
    { name: "read_skill_resource" as const, stage: "resource", path: "References/Pins.MD", label: "读取技能资源" },
    { name: "read_skill_resource" as const, stage: "resource", path: "ASSETS/Pinout.CSV", label: "读取技能资源" },
  ])("shows complete validated metadata for $name ($path)", ({ name, stage, path, label }) => {
    const trace = {
      skill_id: "fixture-skill",
      content_hash: CONTENT_HASH,
      stage,
      ...(path ? { path } : {}),
      success: true,
      instructions: "RAW_INSTRUCTIONS_SENTINEL",
      content: "RAW_RESOURCE_CONTENT_SENTINEL",
      extra: "UNKNOWN_TRACE_SENTINEL",
    };
    const step = makeSkillStep(name, trace, "done", {
      title: "RAW_TITLE_SENTINEL",
      "aria-label": "RAW_ARIA_SENTINEL",
      "data-private": "RAW_DATA_SENTINEL",
      error: "RAW_ERROR_SENTINEL",
    });
    const { container, skillButton } = renderOpenSkillStep(step);

    expect(skillButton.getAttribute("aria-label")).toBe(`${label}，成功`);
    expect(skillButton.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("fixture-skill");
    expect(container.textContent).toContain(stage);
    expect(container.textContent).toContain(CONTENT_HASH);
    if (path) expect(container.textContent).toContain(path);
    for (const sentinel of [
      "RAW_ARGUMENT_SENTINEL", "RAW_RESULT_SENTINEL", "RAW_INSTRUCTIONS_SENTINEL",
      "RAW_RESOURCE_CONTENT_SENTINEL", "UNKNOWN_TRACE_SENTINEL", "RAW_TITLE_SENTINEL",
      "RAW_ARIA_SENTINEL", "RAW_DATA_SENTINEL", "RAW_ERROR_SENTINEL",
    ]) {
      expect(container.innerHTML).not.toContain(sentinel);
    }
  });

  it("requires a resource path to be owned by the trace", () => {
    const allowedPath = "References/Pins.MD";
    const ownTrace = {
      skill_id: "fixture-skill",
      stage: "resource",
      path: allowedPath,
      content_hash: CONTENT_HASH,
      success: true,
    };
    const ownPathRender = renderOpenSkillStep(makeSkillStep("read_skill_resource", ownTrace));
    expect(ownPathRender.skillButton.getAttribute("aria-label")).toBe("读取技能资源，成功");
    expect(ownPathRender.container.textContent).toContain(allowedPath);
    ownPathRender.unmount();

    const inheritedPath = "References/PROTO_PATH_SENTINEL.md";
    const inheritedTrace = Object.assign(Object.create({ path: inheritedPath }), {
      skill_id: "fixture-skill",
      stage: "resource",
      content_hash: CONTENT_HASH,
      success: true,
    });
    const inheritedPathRender = renderOpenSkillStep(makeSkillStep("read_skill_resource", inheritedTrace));

    expect(inheritedPathRender.skillButton.getAttribute("aria-label")).toBe("读取技能资源，无法验证");
    expect(inheritedPathRender.skillButton.getAttribute("aria-label")).not.toContain("成功");
    expect(inheritedPathRender.container.innerHTML).not.toContain(inheritedPath);
  });

  it("shows a fixed failure for a failed trace that has no content hash", () => {
    const step = makeSkillStep("read_skill_resource", {
      skill_id: "fixture-skill",
      stage: "resource",
      path: "References/Pins.MD",
      success: false,
    }, "error", { error: "RAW_ERROR_SENTINEL" });
    const { container, skillButton } = renderOpenSkillStep(step);

    expect(skillButton.getAttribute("aria-label")).toBe("读取技能资源，失败");
    expect(container.textContent).toContain("fixture-skill");
    expect(container.textContent).toContain("References/Pins.MD");
    expect(container.textContent).not.toContain("SHA-256");
    expect(container.innerHTML).not.toContain("RAW_ERROR_SENTINEL");
    expect(container.innerHTML).not.toContain("RAW_RESULT_SENTINEL");
  });

  it.each([
    { label: "pending", stepStatus: "pending", activityStatus: "done", expected: "进行中" },
    { label: "running", stepStatus: "running", activityStatus: "done", expected: "进行中" },
    { label: "cancel", stepStatus: "cancel", activityStatus: "done", expected: "已中断" },
    { label: "interrupted", stepStatus: "interrupted", activityStatus: "done", expected: "已中断" },
    { label: "unknown status", stepStatus: "future-status-sentinel", activityStatus: "done", expected: "无法验证" },
    { label: "missing step status in a running activity", stepStatus: undefined, activityStatus: "running", expected: "进行中" },
  ])("never reports success for $label", ({ stepStatus, activityStatus, expected }) => {
    const step = makeSkillStep("load_skill", {
      skill_id: "fixture-skill",
      content_hash: CONTENT_HASH,
      stage: "instructions",
      success: true,
    });
    if (stepStatus === undefined) {
      delete (step as unknown as Record<string, unknown>).status;
    } else {
      (step as unknown as Record<string, unknown>).status = stepStatus;
    }
    const { container, skillButton } = renderOpenSkillStep(step, activityStatus as ActivityBlockData["status"]);

    expect(skillButton.getAttribute("aria-label")).toBe(`加载技能，${expected}`);
    expect(skillButton.getAttribute("aria-label")).not.toContain("成功");
    expect(container.innerHTML).not.toContain("future-status-sentinel");
  });

  it.each([
    { label: "done status with failed trace", status: "done", trace: { success: false, content_hash: CONTENT_HASH } },
    { label: "error status with successful trace", status: "error", trace: { success: true, content_hash: CONTENT_HASH } },
    { label: "done status without a complete hash", status: "done", trace: { success: true } },
    { label: "error status without strict success metadata", status: "error", trace: {} },
  ])("does not infer success from $label", ({ status, trace }) => {
    const step = makeSkillStep("load_skill", {
      skill_id: "fixture-skill",
      stage: "instructions",
      ...trace,
    }, status);
    const { skillButton } = renderOpenSkillStep(step);

    expect(skillButton.getAttribute("aria-label")).toBe("加载技能，无法验证");
  });

  it.each([
    { label: "invalid skill ID", name: "load_skill" as const, trace: { skill_id: "bad id", stage: "instructions", content_hash: CONTENT_HASH, success: true } },
    { label: "overlong skill ID", name: "load_skill" as const, trace: { skill_id: "s".repeat(129), stage: "instructions", content_hash: CONTENT_HASH, success: true } },
    { label: "uppercase hash", name: "load_skill" as const, trace: { skill_id: "fixture-skill", stage: "instructions", content_hash: "A".repeat(64), success: true } },
    { label: "short hash", name: "load_skill" as const, trace: { skill_id: "fixture-skill", stage: "instructions", content_hash: "a".repeat(63), success: true } },
    { label: "mismatched stage", name: "load_skill" as const, trace: { skill_id: "fixture-skill", stage: "resource", content_hash: CONTENT_HASH, success: true } },
    { label: "non-boolean success", name: "load_skill" as const, trace: { skill_id: "fixture-skill", stage: "instructions", content_hash: CONTENT_HASH, success: "true" } },
    { label: "path on instructions trace", name: "load_skill" as const, trace: { skill_id: "fixture-skill", stage: "instructions", path: "References/Pins.MD", content_hash: CONTENT_HASH, success: true } },
    { label: "raw error in trace metadata", name: "load_skill" as const, trace: { skill_id: "fixture-skill", stage: "instructions", content_hash: CONTENT_HASH, success: false, error: "TRACE_ERROR_SENTINEL" } },
    { label: "resource traversal path", name: "read_skill_resource" as const, trace: { skill_id: "fixture-skill", stage: "resource", path: "References/../secret.md", content_hash: CONTENT_HASH, success: true } },
    { label: "resource path outside allowed roots", name: "read_skill_resource" as const, trace: { skill_id: "fixture-skill", stage: "resource", path: "Docs/Pinout.md", content_hash: CONTENT_HASH, success: true } },
    { label: "overlong resource path", name: "read_skill_resource" as const, trace: { skill_id: "fixture-skill", stage: "resource", path: `References/${"p".repeat(1025)}.md`, content_hash: CONTENT_HASH, success: true } },
    { label: "non-normalized resource path", name: "read_skill_resource" as const, trace: { skill_id: "fixture-skill", stage: "resource", path: "References/cafe\u0301.md", content_hash: CONTENT_HASH, success: true } },
    { label: "missing resource path", name: "read_skill_resource" as const, trace: { skill_id: "fixture-skill", stage: "resource", content_hash: CONTENT_HASH, success: true } },
  ])("fails closed for $label", ({ name, trace }) => {
    const step = makeSkillStep(name, trace);
    const { container, skillButton } = renderOpenSkillStep(step);

    expect(skillButton.getAttribute("aria-label")).toBe(`${name === "load_skill" ? "加载技能" : "读取技能资源"}，无法验证`);
    expect(skillButton.getAttribute("aria-label")).not.toContain("成功");
    expect(container.innerHTML).not.toContain("RAW_ARGUMENT_SENTINEL");
    expect(container.innerHTML).not.toContain("RAW_RESULT_SENTINEL");
    expect(container.innerHTML).not.toContain("TRACE_ERROR_SENTINEL");
  });

  it("keeps old untraced Skill records safe after rerender", () => {
    const oldStep = makeSkillStep("load_skill", undefined, "done", {
      title: "OLD_TITLE_SENTINEL",
      error: "OLD_ERROR_SENTINEL",
      "aria-label": "OLD_ARIA_SENTINEL",
      "data-private": "OLD_DATA_SENTINEL",
    });
    const initialActivity = { durationMs: 1, steps: [oldStep], status: "done" } as ActivityBlockData;
    const { container, skillButton, rerender } = renderOpenSkillStep(oldStep);

    expect(skillButton.getAttribute("aria-label")).toBe("加载技能，无法验证");
    for (const sentinel of ["RAW_ARGUMENT_SENTINEL", "RAW_RESULT_SENTINEL", "OLD_TITLE_SENTINEL", "OLD_ERROR_SENTINEL", "OLD_ARIA_SENTINEL", "OLD_DATA_SENTINEL"]) {
      expect(container.innerHTML).not.toContain(sentinel);
    }

    const restoredStep = makeSkillStep("load_skill", null, "done", { result: "RESTORED_RAW_SENTINEL" });
    rerender(<ActivityBlock activity={{ ...initialActivity, steps: [restoredStep] }} msgId="synthetic-message" />);
    expect(screen.getAllByRole("button")[1].getAttribute("aria-label")).toBe("加载技能，无法验证");
    expect(container.innerHTML).not.toContain("RESTORED_RAW_SENTINEL");
  });

  it("leaves ordinary tool details and reasoning text unchanged", () => {
    const steps = [
      { id: "reasoning", type: "thinking", source: "reasoning", content: "reasoning text remains visible" },
      { id: "ordinary-call", type: "tool", name: "write_file", args: "{\"path\":\"output.txt\"}", result: "ordinary tool result", status: "done" },
    ] as unknown as ActivityStep[];
    const activity = { durationMs: 1, steps, status: "done" } as ActivityBlockData;
    const { container } = render(<ActivityBlock activity={activity} msgId="synthetic-message" />);

    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(container.textContent).toContain("reasoning text remains visible");
    fireEvent.click(screen.getAllByRole("button")[1]);
    fireEvent.click(screen.getAllByRole("button")[2]);

    expect(container.textContent).toContain("{\"path\":\"output.txt\"}");
    expect(container.textContent).toContain("ordinary tool result");
    expect(container.textContent).toContain("reasoning text remains visible");
  });
});
