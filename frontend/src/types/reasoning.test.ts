import { describe, expect, it } from "vitest";
import { normalizeModelCapabilities, reasoningModesFor } from "./reasoning";

describe("reasoning capabilities", () => {
  it("normalizes provider capability payloads", () => {
    const capabilities = normalizeModelCapabilities({
      glm: {
        reasoning: true,
        can_disable: true,
        effort_supported: true,
        efforts: ["low", "high", "unexpected"],
        format: "zai",
      },
      combo: {},
    });

    expect(capabilities.glm).toMatchObject({
      reasoning: true,
      canDisable: true,
      effortSupported: true,
      efforts: ["low", "high"],
      format: "zai",
    });
    expect(capabilities.combo?.reasoning).toBe(false);
  });

  it("exposes effort modes only when the model declares support", () => {
    expect(reasoningModesFor(undefined)).toEqual(["auto"]);
    expect(reasoningModesFor({
      reasoning: true,
      canDisable: false,
      effortSupported: false,
      efforts: [],
      format: "deepseek",
    })).toEqual(["auto"]);
    expect(reasoningModesFor({
      reasoning: true,
      canDisable: true,
      effortSupported: true,
      efforts: ["low", "high"],
      format: "zai",
    })).toEqual(["auto", "low", "high"]);
  });
});
