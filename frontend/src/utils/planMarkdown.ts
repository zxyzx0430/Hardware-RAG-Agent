import type { Plan, PlanStep } from "../types/plan";

export interface PlanMarkdownLabels {
  summary: string;
  steps: string;
  verification: string;
  risks: string;
  openQuestions: string;
}

const SOURCE_CITATION_RE = /\[\^\d+\](?:\(src\d+\))?|\[src\d+\]/gi;

/** Plans describe actions, not evidence claims; source citations belong in chat. */
export function stripPlanSourceCitations(text?: string | null): string {
  return (text ?? "")
    .replace(SOURCE_CITATION_RE, "")
    .replace(/[ \t]+([，。；：、）])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
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
  const title = stripPlanSourceCitations(step.title);
  const detail = stripPlanSourceCitations(step.detail);
  const verification = stripPlanSourceCitations(step.verification);
  const lines = [`${index + 1}. **${title}**`, ""];
  if (detail) lines.push(indentContinuation(detail, indent), "");
  if (verification) {
    lines.push(...renderVerification(verification, indent, labels.verification));
  }
  return lines;
}

function renderHeadingSection(label: string, body: string): string[] {
  return ["", `### ${label}`, "", body];
}

function renderListSection(label: string, values: string[]): string[] {
  if (values.length === 0) return [];
  return ["", `### ${label}`, "", ...values.map((value) => `- ${stripPlanSourceCitations(value)}`)];
}

function renderSummary(plan: Plan, labels: PlanMarkdownLabels): string[] {
  const summary = stripPlanSourceCitations(plan.summary);
  return summary ? renderHeadingSection(labels.summary, summary) : [];
}

function renderSteps(plan: Plan, labels: PlanMarkdownLabels): string[] {
  if (plan.steps.length === 0) return [];
  const parts = ["", `### ${labels.steps}`, ""];
  plan.steps.forEach((step, index) => parts.push(...renderStep(step, index, labels)));
  return parts;
}

/** Build the fixed plan template the right panel renders as Markdown. */
export function buildPlanMarkdown(plan: Plan, labels: PlanMarkdownLabels): string {
  const parts = [`## ${stripPlanSourceCitations(plan.title)}`, ...renderSummary(plan, labels), ...renderSteps(plan, labels)];
  parts.push(...renderListSection(labels.risks, plan.risks));
  parts.push(...renderListSection(labels.openQuestions, plan.open_questions));
  return parts.join("\n").trimEnd();
}
