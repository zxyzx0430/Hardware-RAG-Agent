import { describe, expect, it } from "vitest";
import { buildModelGroups, filterModelGroups } from "./modelOptions";

const providers = [
  { id: "router-a", name: "LOCAL ROUTER", models: ["ccgo/gpt-6-luna", "ccgo/gpt-6.1-sol"] },
  { id: "router-b", name: "CLEAN ENV ROUTER", models: ["ccgo/gpt-6-luna", "ccgo/gpt-6.1-sol"] },
  { id: "router-c", name: "FIX SETUP ROUTER", models: ["ccgo/gpt-6-luna", "other/model"] },
];

describe("model dropdown options", () => {
  it("deduplicates model ids repeated across routers", () => {
    const groups = buildModelGroups(providers, "router-a");
    const rows = groups.flatMap((group) => group.models);

    expect(rows).toEqual(["ccgo/gpt-6-luna", "ccgo/gpt-6.1-sol", "other/model"]);
    expect(groups.map((group) => group.providerId)).toEqual(["router-a", "router-c"]);
  });

  it("keeps the selected provider as the owner of its models", () => {
    const groups = buildModelGroups(providers, "router-b");

    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({
      providerId: "router-b",
      models: ["ccgo/gpt-6-luna", "ccgo/gpt-6.1-sol"],
    });
    expect(groups[1]).toMatchObject({ providerId: "router-c", models: ["other/model"] });
  });

  it("filters by model id and keeps full groups for provider-name matches", () => {
    const groups = buildModelGroups(providers, "router-a");

    expect(filterModelGroups(groups, "luna").flatMap((group) => group.models))
      .toEqual(["ccgo/gpt-6-luna"]);
    expect(filterModelGroups(groups, "fix setup")[0]).toMatchObject({
      providerId: "router-c",
      models: ["other/model"],
    });
    expect(filterModelGroups(groups, "missing")).toEqual([]);
    expect(filterModelGroups(groups, "  ")).toBe(groups);
  });
});
