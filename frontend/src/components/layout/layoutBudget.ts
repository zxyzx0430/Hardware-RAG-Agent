import {
  CHAT_MIN_WIDTH,
  EXPLORER_MIN_WIDTH,
  INPUT_BAR_MAX_WIDTH,
  INPUT_BAR_MIN_WIDTH,
  RIGHT_PANEL_MAX_WIDTH,
  RIGHT_PANEL_MIN_WIDTH,
} from "../../stores/appStore/persistence";

export const ICON_NAV_WIDTH = 48;
export const PANEL_STRIP_WIDTH = 18;
export const EXPLORER_COLLAPSED_STRIP_WIDTH = 24;
export const PANEL_RESIZER_WIDTH = 4;
export const INPUT_BAR_RESIZER_WIDTH = 12;
export const LEFT_PANEL_MIN_WIDTH = 180;
export const LEFT_PANEL_MAX_WIDTH = 500;
export const CHAT_COLUMN_MIN_WIDTH = Math.max(
  CHAT_MIN_WIDTH,
  INPUT_BAR_MIN_WIDTH + INPUT_BAR_RESIZER_WIDTH,
);

export type LayoutPanel = "left" | "right" | "explorer";

export interface LayoutBudgetInput {
  viewportWidth: number;
  showChatShell: boolean;
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  explorerOpen: boolean;
  leftPanelWidth: number;
  rightPanelWidth: number;
  explorerWidth: number;
  inputBarWidth: number;
  protectedPanel?: LayoutPanel | null;
}

export interface ResolvedLayout {
  showChatShell: boolean;
  leftPanelOpen: boolean;
  rightPanelOpen: boolean;
  explorerOpen: boolean;
  leftPanelWidth: number;
  rightPanelWidth: number;
  explorerWidth: number;
  inputBarWidth: number;
  chatColumnWidth: number;
  requiredMinimumWidth: number;
  overflow: boolean;
  autoCollapsedPanels: LayoutPanel[];
}

const COLLAPSE_PRIORITY: LayoutPanel[] = ["left", "right", "explorer"];
const WIDTH_ALLOCATION_PRIORITY: LayoutPanel[] = ["explorer", "right", "left"];

function getPanelOpen(input: LayoutBudgetInput, panel: LayoutPanel): boolean {
  if (panel === "left") return input.leftPanelOpen;
  if (panel === "right") return input.rightPanelOpen;
  return input.explorerOpen;
}

function getPanelMinimum(panel: LayoutPanel): number {
  if (panel === "left") return LEFT_PANEL_MIN_WIDTH;
  if (panel === "right") return RIGHT_PANEL_MIN_WIDTH;
  return EXPLORER_MIN_WIDTH;
}

function getPanelMaximum(input: LayoutBudgetInput, panel: LayoutPanel): number {
  if (panel === "left") return LEFT_PANEL_MAX_WIDTH;
  if (panel === "right") return RIGHT_PANEL_MAX_WIDTH;
  return Math.max(EXPLORER_MIN_WIDTH, input.viewportWidth / 2);
}

function getPreferredWidth(input: LayoutBudgetInput, panel: LayoutPanel): number {
  if (panel === "left") return input.leftPanelWidth;
  if (panel === "right") return input.rightPanelWidth;
  return input.explorerWidth;
}

function withPanelOpen(
  input: LayoutBudgetInput,
  panel: LayoutPanel,
  open: boolean,
): LayoutBudgetInput {
  if (panel === "left") return { ...input, leftPanelOpen: open };
  if (panel === "right") return { ...input, rightPanelOpen: open };
  return { ...input, explorerOpen: open };
}

export function getLayoutMinimumWidth(input: LayoutBudgetInput): number {
  if (!input.showChatShell) return 0;

  let width = ICON_NAV_WIDTH + PANEL_STRIP_WIDTH + CHAT_COLUMN_MIN_WIDTH + PANEL_STRIP_WIDTH;
  width += input.leftPanelOpen ? LEFT_PANEL_MIN_WIDTH + PANEL_RESIZER_WIDTH : 0;
  width += input.rightPanelOpen ? RIGHT_PANEL_MIN_WIDTH + PANEL_RESIZER_WIDTH : 0;
  width += input.explorerOpen
    ? EXPLORER_MIN_WIDTH + PANEL_RESIZER_WIDTH
    : EXPLORER_COLLAPSED_STRIP_WIDTH;
  return width;
}

export function getPanelsToAutoCollapse(input: LayoutBudgetInput): LayoutPanel[] {
  if (!input.showChatShell || getLayoutMinimumWidth(input) <= input.viewportWidth) {
    return [];
  }

  const candidates = input.protectedPanel
    ? [...COLLAPSE_PRIORITY.filter((panel) => panel !== input.protectedPanel), input.protectedPanel]
    : COLLAPSE_PRIORITY;
  let candidateState = input;
  const collapsed: LayoutPanel[] = [];

  for (const panel of candidates) {
    if (!getPanelOpen(candidateState, panel)) continue;
    if (panel === input.protectedPanel) continue;
    candidateState = withPanelOpen(candidateState, panel, false);
    collapsed.push(panel);
    if (getLayoutMinimumWidth(candidateState) <= input.viewportWidth) break;
  }

  return collapsed;
}

function setPanelWidth(
  widths: Record<LayoutPanel, number>,
  panel: LayoutPanel,
  width: number,
): Record<LayoutPanel, number> {
  return { ...widths, [panel]: width };
}

export function resolveLayout(input: LayoutBudgetInput): ResolvedLayout {
  if (!input.showChatShell) {
    return {
      showChatShell: false,
      leftPanelOpen: false,
      rightPanelOpen: input.rightPanelOpen,
      explorerOpen: input.explorerOpen,
      leftPanelWidth: 0,
      rightPanelWidth: input.rightPanelWidth,
      explorerWidth: input.explorerWidth,
      inputBarWidth: input.inputBarWidth,
      chatColumnWidth: 0,
      requiredMinimumWidth: 0,
      overflow: false,
      autoCollapsedPanels: [],
    };
  }

  const autoCollapsedPanels = getPanelsToAutoCollapse(input);
  const leftPanelOpen = input.leftPanelOpen && !autoCollapsedPanels.includes("left");
  const rightPanelOpen = input.rightPanelOpen && !autoCollapsedPanels.includes("right");
  const explorerOpen = input.explorerOpen && !autoCollapsedPanels.includes("explorer");
  const collapsedInput: LayoutBudgetInput = {
    ...input,
    leftPanelOpen,
    rightPanelOpen,
    explorerOpen,
  };
  const requiredMinimumWidth = getLayoutMinimumWidth(collapsedInput);
  let remainingWidth = Math.max(0, input.viewportWidth - requiredMinimumWidth);
  let widths: Record<LayoutPanel, number> = {
    left: leftPanelOpen ? LEFT_PANEL_MIN_WIDTH : 0,
    right: rightPanelOpen ? RIGHT_PANEL_MIN_WIDTH : 0,
    explorer: explorerOpen ? EXPLORER_MIN_WIDTH : 0,
  };

  const allocationOrder = input.protectedPanel
    ? [input.protectedPanel, ...WIDTH_ALLOCATION_PRIORITY.filter((panel) => panel !== input.protectedPanel)]
    : WIDTH_ALLOCATION_PRIORITY;
  for (const panel of allocationOrder) {
    if (!getPanelOpen(collapsedInput, panel)) continue;
    const preferred = Math.min(
      getPanelMaximum(input, panel),
      Math.max(getPanelMinimum(panel), getPreferredWidth(input, panel)),
    );
    const extra = Math.min(remainingWidth, preferred - getPanelMinimum(panel));
    widths = setPanelWidth(widths, panel, getPanelMinimum(panel) + extra);
    remainingWidth -= extra;
  }

  const outerWidth = ICON_NAV_WIDTH
    + PANEL_STRIP_WIDTH
    + (leftPanelOpen ? widths.left + PANEL_RESIZER_WIDTH : 0)
    + PANEL_STRIP_WIDTH
    + (rightPanelOpen ? widths.right + PANEL_RESIZER_WIDTH : 0)
    + (explorerOpen
      ? widths.explorer + PANEL_RESIZER_WIDTH
      : EXPLORER_COLLAPSED_STRIP_WIDTH);
  const chatColumnWidth = Math.max(CHAT_COLUMN_MIN_WIDTH, input.viewportWidth - outerWidth);
  const inputBarWidth = Math.max(
    INPUT_BAR_MIN_WIDTH,
    Math.min(INPUT_BAR_MAX_WIDTH, input.inputBarWidth, chatColumnWidth - INPUT_BAR_RESIZER_WIDTH),
  );

  return {
    showChatShell: true,
    leftPanelOpen,
    rightPanelOpen,
    explorerOpen,
    leftPanelWidth: widths.left,
    rightPanelWidth: widths.right,
    explorerWidth: widths.explorer,
    inputBarWidth,
    chatColumnWidth,
    requiredMinimumWidth,
    overflow: requiredMinimumWidth > input.viewportWidth,
    autoCollapsedPanels,
  };
}

export function getResizeReservedWidth(
  panel: LayoutPanel | "inputBar",
  layout: ResolvedLayout,
): number {
  if (!layout.showChatShell) return 0;

  const rightSlot = layout.rightPanelOpen
    ? layout.rightPanelWidth + PANEL_RESIZER_WIDTH
    : 0;
  const explorerSlot = layout.explorerOpen
    ? layout.explorerWidth + PANEL_RESIZER_WIDTH
    : EXPLORER_COLLAPSED_STRIP_WIDTH;

  if (panel === "inputBar") return INPUT_BAR_RESIZER_WIDTH;
  if (panel === "left") {
    return ICON_NAV_WIDTH
      + PANEL_RESIZER_WIDTH
      + PANEL_STRIP_WIDTH
      + CHAT_COLUMN_MIN_WIDTH
      + PANEL_STRIP_WIDTH
      + rightSlot
      + explorerSlot;
  }
  if (panel === "right") {
    return CHAT_COLUMN_MIN_WIDTH
      + PANEL_STRIP_WIDTH
      + PANEL_RESIZER_WIDTH
      + explorerSlot;
  }
  return CHAT_COLUMN_MIN_WIDTH
    + PANEL_STRIP_WIDTH
    + rightSlot
    + PANEL_RESIZER_WIDTH;
}
