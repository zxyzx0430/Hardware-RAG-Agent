import { describe, expect, it } from "vitest";
import { formatDuration } from "./format";

describe("formatDuration", () => {
  it("always renders milliseconds", () => {
    expect(formatDuration(0)).toBe("0ms");
    expect(formatDuration(6.9)).toBe("7ms");
    expect(formatDuration(999)).toBe("999ms");
    expect(formatDuration(1000)).toBe("1000ms");
    expect(formatDuration(61_234)).toBe("61234ms");
  });
});
