import { describe, expect, it } from "vitest";
import {
  CHAT_COLUMN_MIN_WIDTH,
  EXPLORER_COLLAPSED_STRIP_WIDTH,
  ICON_NAV_WIDTH,
  LEFT_PANEL_MIN_WIDTH,
  PANEL_RESIZER_WIDTH,
  PANEL_STRIP_WIDTH,
  getLayoutMinimumWidth,
  getPanelsToAutoCollapse,
  getResizeReservedWidth,
  resolveLayout,
  type LayoutBudgetInput,
  type LayoutPanel,
} from "./layoutBudget";
import { EXPLORER_MIN_WIDTH, INPUT_BAR_MAX_WIDTH, RIGHT_PANEL_MIN_WIDTH } from "../../stores/appStore/persistence";

const allPanelsOpen: LayoutBudgetInput = {
  viewportWidth: 1536,
  showChatShell: true,
  leftPanelOpen: true,
  rightPanelOpen: true,
  explorerOpen: true,
  leftPanelWidth: 280,
  rightPanelWidth: 340,
  explorerWidth: 280,
  inputBarWidth: 720,
};

const panels: LayoutPanel[] = ["left", "right", "explorer"];

describe("layout width budget", () => {
  it("counts only visible panels, strips, and resizers for all open/closed combinations", () => {
    for (let mask = 0; mask < 8; mask += 1) {
      const leftPanelOpen = (mask & 1) !== 0;
      const rightPanelOpen = (mask & 2) !== 0;
      const explorerOpen = (mask & 4) !== 0;
      const actual = getLayoutMinimumWidth({
        ...allPanelsOpen,
        leftPanelOpen,
        rightPanelOpen,
        explorerOpen,
        // Closed preferences must not reserve invisible pane widths.
        leftPanelWidth: 900,
        rightPanelWidth: 900,
        explorerWidth: 900,
      });
      const expected = ICON_NAV_WIDTH
        + PANEL_STRIP_WIDTH
        + CHAT_COLUMN_MIN_WIDTH
        + PANEL_STRIP_WIDTH
        + (leftPanelOpen ? LEFT_PANEL_MIN_WIDTH + PANEL_RESIZER_WIDTH : 0)
        + (rightPanelOpen ? RIGHT_PANEL_MIN_WIDTH + PANEL_RESIZER_WIDTH : 0)
        + (explorerOpen
          ? EXPLORER_MIN_WIDTH + PANEL_RESIZER_WIDTH
          : EXPLORER_COLLAPSED_STRIP_WIDTH);

      expect(actual, `mask=${mask.toString(2).padStart(3, "0")}`).toBe(expected);
    }
  });

  it("auto-folds at exact minimum-width thresholds in the approved priority order", () => {
    const cases: Array<[number, LayoutPanel[]]> = [
      [1328, []],
      [1327, ["left"]],
      [1144, ["left"]],
      [1143, ["left", "right"]],
      [1024, ["left", "right"]],
      [800, ["left", "right"]],
      [799, ["left", "right", "explorer"]],
      [600, ["left", "right", "explorer"]],
    ];

    for (const [viewportWidth, expected] of cases) {
      expect(
        getPanelsToAutoCollapse({ ...allPanelsOpen, viewportWidth }),
        `viewport=${viewportWidth}`,
      ).toEqual(expected);
    }

    const impossible = resolveLayout({ ...allPanelsOpen, viewportWidth: 599 });
    expect(impossible.autoCollapsedPanels).toEqual(panels);
    expect(impossible.overflow).toBe(true);
    expect(impossible.requiredMinimumWidth).toBe(600);
  });

  it.each([
    ["left", ["right"]],
    ["right", ["left", "explorer"]],
    ["explorer", ["left", "right"]],
  ] as const)("preserves a just-opened %s panel before folding other panes", (protectedPanel, expected) => {
    expect(
      getPanelsToAutoCollapse({
        ...allPanelsOpen,
        viewportWidth: 1024,
        protectedPanel,
      }),
    ).toEqual(expected);
  });

  it("clamps rendered widths without overwriting stored preferences or file state", () => {
    const preferred = {
      ...allPanelsOpen,
      leftPanelWidth: 500,
      rightPanelWidth: 560,
      explorerWidth: 450,
      inputBarWidth: 720,
    };
    const resolved = resolveLayout(preferred);

    expect(resolved).toMatchObject({
      leftPanelOpen: true,
      rightPanelOpen: true,
      explorerOpen: true,
      leftPanelWidth: 180,
      rightPanelWidth: 340,
      explorerWidth: 428,
      chatColumnWidth: CHAT_COLUMN_MIN_WIDTH,
      inputBarWidth: 480,
      overflow: false,
    });
    expect(preferred).toMatchObject({ leftPanelWidth: 500, rightPanelWidth: 560, explorerWidth: 450 });
  });

  it("resolves a narrow restored layout and keeps the Explorer while retaining stored widths", () => {
    const restored = {
      ...allPanelsOpen,
      viewportWidth: 1024,
      leftPanelWidth: 500,
      rightPanelWidth: 560,
      explorerWidth: 450,
      inputBarWidth: 720,
    };
    const resolved = resolveLayout(restored);

    expect(resolved).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: false,
      explorerOpen: true,
      explorerWidth: 444,
      chatColumnWidth: CHAT_COLUMN_MIN_WIDTH,
      inputBarWidth: 480,
      overflow: false,
      autoCollapsedPanels: ["left", "right"],
    });
    expect(restored).toMatchObject({ leftPanelWidth: 500, rightPanelWidth: 560, explorerWidth: 450 });
  });

  it("renders a protected right panel as an overlay drawer when it cannot share the row", () => {
    const resolved = resolveLayout({
      ...allPanelsOpen,
      viewportWidth: 900,
      protectedPanel: "right",
    });

    expect(resolved).toMatchObject({
      leftPanelOpen: false,
      rightPanelOpen: true,
      explorerOpen: false,
      rightPanelOverlay: true,
      rightPanelWidth: 340,
      overflow: false,
    });
    // The drawer does not consume row width; the chat column stays usable.
    expect(resolved.requiredMinimumWidth).toBe(
      ICON_NAV_WIDTH + PANEL_STRIP_WIDTH + CHAT_COLUMN_MIN_WIDTH
        + PANEL_STRIP_WIDTH + EXPLORER_COLLAPSED_STRIP_WIDTH,
    );
    expect(resolved.autoCollapsedPanels).toEqual(["left", "explorer"]);
  });

  it("keeps the right panel in the row when it still fits", () => {
    const resolved = resolveLayout({
      ...allPanelsOpen,
      viewportWidth: 1147,
      protectedPanel: "right",
    });

    expect(resolved).toMatchObject({
      rightPanelOpen: true,
      rightPanelOverlay: false,
      rightPanelWidth: RIGHT_PANEL_MIN_WIDTH,
      explorerOpen: true,
      overflow: false,
    });
  });

  it("auto-fits the composer to the chat column and clamps manual widths", () => {
    const preferred = {
      ...allPanelsOpen,
      leftPanelWidth: 280,
      rightPanelWidth: 340,
      explorerWidth: 280,
      inputBarWidth: 720,
    };
    const cases: Array<[number, number]> = [
      [1710, 714],
      [1440, 492],
      [1280, 568],
      [1024, 656],
    ];

    for (const [viewportWidth, chatColumnWidth] of cases) {
      const resolved = resolveLayout({ ...preferred, viewportWidth, inputBarAuto: true });
      expect(resolved.chatColumnWidth, `viewport=${viewportWidth}`).toBe(chatColumnWidth);
      expect(resolved.inputBarWidth, `viewport=${viewportWidth}`).toBe(
        Math.min(INPUT_BAR_MAX_WIDTH, chatColumnWidth - 12),
      );
    }

    const manual = resolveLayout({
      ...preferred,
      viewportWidth: 1024,
      inputBarAuto: false,
      inputBarWidth: 900,
    });
    expect(manual.inputBarWidth).toBe(Math.min(INPUT_BAR_MAX_WIDTH, manual.chatColumnWidth - 12));

    const manualNarrow = resolveLayout({
      ...preferred,
      viewportWidth: 1710,
      inputBarAuto: false,
      inputBarWidth: 520,
    });
    expect(manualNarrow.inputBarWidth).toBe(520);
  });

  it("keeps the chat column at its minimum when the left panel is dragged to the budget max", () => {
    const base = {
      ...allPanelsOpen,
      viewportWidth: 1710,
      leftPanelWidth: 280,
      rightPanelWidth: 340,
      explorerWidth: 280,
      inputBarAuto: true,
    };
    const layout = resolveLayout(base);
    const maxLeft = base.viewportWidth - getResizeReservedWidth("left", layout);

    const dragged = resolveLayout({ ...base, leftPanelWidth: maxLeft });
    expect(dragged.leftPanelWidth).toBeLessThanOrEqual(500);
    expect(dragged.chatColumnWidth).toBeGreaterThanOrEqual(CHAT_COLUMN_MIN_WIDTH);
  });

  it("reserves only the current visible slots for each resize handle", () => {
    const collapsed = resolveLayout({
      ...allPanelsOpen,
      leftPanelOpen: false,
      rightPanelOpen: false,
      explorerOpen: false,
    });

    expect(getResizeReservedWidth("left", collapsed)).toBe(
      ICON_NAV_WIDTH + PANEL_RESIZER_WIDTH + PANEL_STRIP_WIDTH + CHAT_COLUMN_MIN_WIDTH
        + PANEL_STRIP_WIDTH + EXPLORER_COLLAPSED_STRIP_WIDTH,
    );
    expect(getResizeReservedWidth("right", collapsed)).toBe(
      CHAT_COLUMN_MIN_WIDTH + PANEL_STRIP_WIDTH + PANEL_RESIZER_WIDTH + EXPLORER_COLLAPSED_STRIP_WIDTH,
    );
    expect(getResizeReservedWidth("explorer", collapsed)).toBe(
      CHAT_COLUMN_MIN_WIDTH + PANEL_STRIP_WIDTH + PANEL_RESIZER_WIDTH,
    );
    expect(getResizeReservedWidth("inputBar", collapsed)).toBe(12);
  });
});
