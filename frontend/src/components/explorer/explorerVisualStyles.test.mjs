import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { describe, expect, it } from "vitest";

const explorerStyles = readFileSync(resolve(cwd(), "src/styles/explorer.css"), "utf8");
const treeStyles = readFileSync(resolve(cwd(), "src/styles/explorer-tree.css"), "utf8");

describe("Explorer visual layout contracts", () => {
  it("lets the toolbar shrink and wrap so refresh and the watch dot stay visible", () => {
    expect(treeStyles).toMatch(/\.explorer-toolbar\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(treeStyles).toMatch(/\.explorer-search-input\s*\{[^}]*flex:\s*1\s+1\s+120px/s);
    expect(treeStyles).toMatch(/\.explorer-search-input\s*\{[^}]*min-width:\s*0/s);
  });

  it("drops header action labels below 360px so the header stays two rows", () => {
    expect(explorerStyles).toMatch(
      /@container\s+explorer-panel\s*\(max-width:\s*360px\)[\s\S]*?\.explorer-header-button-label\s*\{[^}]*display:\s*none/s,
    );
    expect(explorerStyles).toMatch(
      /@container\s+explorer-panel\s*\(max-width:\s*360px\)[\s\S]*?\.explorer-collapse-btn::after\s*\{[^}]*content:\s*none/s,
    );
  });

  it("makes the narrow tree pane vertically resizable and scrollable", () => {
    const narrow = explorerStyles.match(
      /@container\s+explorer-panel\s*\(max-width:\s*520px\)\s*\{([\s\S]*?)\n\}/,
    )?.[1] ?? "";
    expect(narrow).toMatch(/\.explorer-tree-pane\s*\{[^}]*resize:\s*vertical/s);
    expect(narrow).toMatch(/\.explorer-tree-pane\s*\{[^}]*min-height:\s*140px/s);
    expect(narrow).toMatch(/\.explorer-tree-pane\s*\{[^}]*overflow:\s*auto/s);
  });

  it("drops a narrow-mode inline tree height once the row layout returns", () => {
    expect(explorerStyles).toMatch(
      /@container\s+explorer-panel\s*\(min-width:\s*521px\)[\s\S]*?\.explorer-tree-pane\s*\{[^}]*height:\s*100%\s*!important/s,
    );
  });
});
