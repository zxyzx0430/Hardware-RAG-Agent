import { describe, expect, it } from "vitest";
import { formatDuration } from "./format";

describe("formatDuration", () => {
  it("always renders seconds with two decimals", () => {
    expect(formatDuration(0)).toBe("0.00秒");
    expect(formatDuration(6.9)).toBe("0.00秒");
    expect(formatDuration(999)).toBe("0.99秒");
    expect(formatDuration(1000)).toBe("1.00秒");
    expect(formatDuration(4020)).toBe("4.02秒");
    expect(formatDuration(61_234)).toBe("61.23秒");
  });
});
