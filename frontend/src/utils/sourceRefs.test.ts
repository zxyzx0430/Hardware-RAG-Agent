import { describe, expect, it } from "vitest";
import { sortSourcesForDisplay, sourceDisplayNumber } from "./sourceRefs";
import type { SourceRef } from "../types/session";

function source(id: string, score: number): SourceRef {
  return { id, title: id, doc: "", page: 0, score, excerpt: "" };
}

describe("sourceRefs display numbering", () => {
  it("ranks by score and maps raw source ids to card numbers", () => {
    const sources = [source("src6", 0.2), source("src9", 0.9), source("src2", 0.5)];

    expect(sortSourcesForDisplay(sources).map((item) => item.id)).toEqual([
      "src9",
      "src2",
      "src6",
    ]);
    expect(sourceDisplayNumber("src9", sources)).toBe(1);
    expect(sourceDisplayNumber("src6", sources)).toBe(3);
  });

  it("falls back to the raw src number when no matching source is loaded", () => {
    expect(sourceDisplayNumber("src9", [])).toBe(9);
  });
});
