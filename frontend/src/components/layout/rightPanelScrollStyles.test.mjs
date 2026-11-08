import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { describe, expect, it } from "vitest";

const workbenchStyles = readFileSync(resolve(cwd(), "src/styles/workbench.css"), "utf8");

describe("right panel source list scroll styles", () => {
  it("keeps source groups at their natural height inside the scrolling column", () => {
    expect(workbenchStyles).toMatch(/\.source-scroll\s*\{[^}]*flex:\s*1(?:;|\s)[^}]*overflow-y:\s*auto/s);
    expect(workbenchStyles).toMatch(/\.source-scroll\s*\{[^}]*display:\s*flex[^}]*flex-direction:\s*column/s);
    // Source groups clip their own content, which zeroes their flex auto
    // min-size: without flex-shrink: 0 a long list compresses instead of scrolling.
    expect(workbenchStyles).toMatch(/\.source-scroll\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0/s);
  });
});
