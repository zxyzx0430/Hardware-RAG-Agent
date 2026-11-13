import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownRenderer } from "./MarkdownRenderer";

describe("MarkdownRenderer", () => {
  it("renders a GFM table with header and body cells", () => {
    render(
      <MarkdownRenderer
        content={"| 章节 | 实际内容 |\n|---|---|\n| 1 产品概述 | 型号对比 |"}
      />,
    );

    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("章节")).toBeTruthy();
    expect(screen.getByText("型号对比")).toBeTruthy();
  });

  it("keeps single-line fenced code without a language as a code block", () => {
    const { container } = render(
      <MarkdownRenderer content={"```\nconst x = 1;\n```"} streaming />,
    );

    expect(container.querySelector(".code-block-shell")).toBeTruthy();
    expect(container.querySelector(".inline-code")).toBeNull();
  });

  it("does not rewrite source refs inside inline code", () => {
    render(<MarkdownRenderer content={"`[src1]` 和 [src1]"} enableSourceRef />);

    expect(screen.getByText("[src1]")).toBeTruthy();
    expect(screen.getByRole("button", { name: "src1" })).toBeTruthy();
  });

  it("renders horizontal rules, images and task lists", () => {
    const { container } = render(
      <MarkdownRenderer
        content={"---\n\n![diagram](https://example.com/diagram.png)\n\n- [x] done"}
      />,
    );

    expect(container.querySelector("hr")).toBeTruthy();
    expect(screen.getByAltText("diagram")).toBeTruthy();
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
  });
});
