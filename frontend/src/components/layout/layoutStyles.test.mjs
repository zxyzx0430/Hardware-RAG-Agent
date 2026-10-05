import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cwd } from "node:process";
import { describe, expect, it } from "vitest";

const chatStyles = readFileSync(resolve(cwd(), "src/styles/chat.css"), "utf8");
const explorerStyles = readFileSync(resolve(cwd(), "src/styles/explorer.css"), "utf8");
const layoutStyles = readFileSync(resolve(cwd(), "src/styles/layout.css"), "utf8");

describe("responsive layout style contracts", () => {
  it("gives the input-bar resizer its own non-overlapping 12px slot", () => {
    expect(chatStyles).toMatch(/\.inputbar-resizer\s*\{[^}]*width:\s*12px/s);
    expect(chatStyles).toMatch(/\.inputbar-resizer::after\s*\{[^}]*inset:\s*0(?:;|\s)/s);
    expect(chatStyles).not.toMatch(/\.inputbar-resizer::after\s*\{[^}]*inset:\s*0\s+-3px/s);
    expect(layoutStyles).not.toMatch(/\.panel-btn-strip(?:\.right)?\s*\{[^}]*pointer-events:\s*none/s);
  });

  it("wraps input actions as whole controls and keeps send/stop labels intact", () => {
    expect(chatStyles).toMatch(/container-name:\s*inputbar-actions/);
    expect(chatStyles).toMatch(/@container\s+inputbar-actions\s*\([^)]*\)[\s\S]*?\.input-actions\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(chatStyles).toMatch(/\.input-left\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(chatStyles).toMatch(/\.send-btn\s*,\s*\.stop-btn\s*\{[^}]*flex:\s*0\s+0\s+auto[^}]*white-space:\s*nowrap/s);
  });

  it("keeps Explorer action names visible and lets the controls wrap near 220px", () => {
    expect(explorerStyles).toMatch(/@container\s+explorer-panel\s*\(max-width:\s*520px\)[\s\S]*?\.explorer-header-actions\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(explorerStyles).toMatch(/@container\s+explorer-panel\s*\(max-width:\s*520px\)[\s\S]*?\.explorer-header-button-label\s*\{[^}]*display:\s*inline/s);
    expect(explorerStyles).toMatch(/\.explorer-follow-btn::after\s*\{[^}]*content:\s*attr\(aria-label\)/s);
    expect(explorerStyles).toMatch(/\.explorer-collapse-btn::after\s*\{[^}]*content:\s*attr\(aria-label\)/s);
  });
});
