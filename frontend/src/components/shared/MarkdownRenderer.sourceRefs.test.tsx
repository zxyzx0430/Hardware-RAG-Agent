import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type { SourceRef } from "../../types/session";

afterEach(cleanup);

const sources: SourceRef[] = [
  { id: "src6", title: "Low score source", doc: "a.pdf", page: 1, score: 0.2, excerpt: "" },
  { id: "src9", title: "High score source", doc: "b.pdf", page: 2, score: 0.9, excerpt: "" },
];

describe("MarkdownRenderer source citations", () => {
  it("numbers inline citations by the same score order as source cards", () => {
    const onClick = vi.fn();
    render(
      <MarkdownRenderer
        content="first[src6] second[src9]"
        enableSourceRef
        sources={sources}
        onSourceClick={onClick}
      />,
    );

    const buttons = screen.getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual(["[2]", "[1]"]);

    fireEvent.click(buttons[0]);
    expect(onClick).toHaveBeenCalledWith("src6");
  });
});
