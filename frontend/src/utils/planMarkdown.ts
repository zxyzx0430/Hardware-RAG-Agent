import type { Plan, PlanStep } from "../types/plan";

export interface PlanMarkdownLabels {
  summary: string;
  steps: string;
  verification: string;
  risks: string;
  openQuestions: string;
}

/** Indent every non-empty line so multi-line Markdown stays inside the step. */
function indentContinuation(text: string, indent: string): string {
  return text
    .split("\n")
    .map((line) => (line ? `${indent}${line}` : ""))
    .join("\n");
}

function renderVerification(verification: string, indent: string, label: string): string[] {
  const [first = "", ...rest] = verification.split("\n");
  const continuation = rest.map((line) => (line ? `${indent}  ${line}` : ""));
  return [`${indent}- **${label}**: ${first}`, ...continuation, ""];
}

function renderStep(step: PlanStep, index: number, labels: PlanMarkdownLabels): string[] {
  const indent = " ".repeat(String(index + 1).length + 2);
  const lines = [`${index + 1}. **${step.title}**`, ""];
  if (step.detail) lines.push(indentContinuation(step.detail, indent), "");
  if (step.verification) {
    lines.push(...renderVerification(step.verification, indent, labels.verification));
  }
  return lines;
}

function renderHeadingSection(label: string, body: string): string[] {
  return ["", `### ${label}`, "", body];
}

function renderListSection(label: string, values: string[]): string[] {
  if (values.length === 0) return [];
  return ["", `### ${label}`, "", ...values.map((value) => `- ${value}`)];
}

function renderSummary(plan: Plan, labels: PlanMarkdownLabels): string[] {
  return plan.summary ? renderHeadingSection(labels.summary, plan.summary) : [];
}

function renderSteps(plan: Plan, labels: PlanMarkdownLabels): string[] {
  if (plan.steps.length === 0) return [];
  const parts = ["", `### ${labels.steps}`, ""];
  plan.steps.forEach((step, index) => parts.push(...renderStep(step, index, labels)));
  return parts;
}

/** Build the fixed plan template the right panel renders as Markdown. */
export function buildPlanMarkdown(plan: Plan, labels: PlanMarkdownLabels): string {
  const parts = [`## ${plan.title}`, ...renderSummary(plan, labels), ...renderSteps(plan, labels)];
  parts.push(...renderListSection(labels.risks, plan.risks));
  parts.push(...renderListSection(labels.openQuestions, plan.open_questions));
  return parts.join("\n").trimEnd();
}
