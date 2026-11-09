export type ReasoningMode = "auto" | "off" | "low" | "medium" | "high";
export type ReasoningEffort = "low" | "medium" | "high";

export interface ModelReasoningCapability {
  reasoning: boolean;
  canDisable: boolean;
  effortSupported: boolean;
  efforts: ReasoningEffort[];
  format: string | null;
}

export const EFFORT_MODES: ReasoningEffort[] = ["low", "medium", "high"];

export const REASONING_LABELS: Record<ReasoningMode, { zh: string; en: string }> = {
  auto: { zh: "自动", en: "Auto" },
  off: { zh: "关闭", en: "Off" },
  low: { zh: "低", en: "Low" },
  medium: { zh: "中", en: "Medium" },
  high: { zh: "高", en: "High" },
};

export function normalizeModelCapabilities(
  raw: unknown,
): Record<string, ModelReasoningCapability> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const result: Record<string, ModelReasoningCapability> = {};
  for (const [model, value] of Object.entries(raw)) {
    if (!model || !value || typeof value !== "object" || Array.isArray(value)) continue;
    const item = value as Record<string, unknown>;
    const efforts = Array.isArray(item.efforts)
      ? item.efforts.filter((effort): effort is ReasoningEffort =>
          EFFORT_MODES.includes(effort as ReasoningEffort))
      : [];
    result[model] = {
      reasoning: item.reasoning === true,
      canDisable: item.can_disable === true,
      effortSupported: item.effort_supported === true,
      efforts,
      format: typeof item.format === "string" ? item.format : null,
    };
  }
  return result;
}

export function reasoningModesFor(
  capability: ModelReasoningCapability | undefined,
): ReasoningMode[] {
  if (!capability?.reasoning) return ["auto"];
  if (!capability.effortSupported) return ["auto"];
  return ["auto", ...EFFORT_MODES.filter((mode) => capability.efforts.includes(mode))];
}

export function reasoningLabel(mode: ReasoningMode, lang: string): string {
  return REASONING_LABELS[mode][lang === "en" ? "en" : "zh"];
}
