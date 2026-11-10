import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RightPanel } from "./RightPanel";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({}),
  apiPost: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue(undefined),
  apiSSE: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  fetchBigChunk: vi.fn().mockResolvedValue({ text: "" }),
}));
vi.mock("../workbench/WorkbenchPanel", () => ({ WorkbenchPanel: () => null }));
vi.mock("../plan/PlanPanel", () => ({ PlanPanel: () => <div data-testid="plan-panel" /> }));
vi.mock("../changes/ChangeSetPanel", () => ({
  ChangeSetPanel: () => <div data-testid="change-set-panel" />,
}));
vi.mock("../shared/MarkdownRenderer", () => ({ useAppliedDarkMode: () => false }));
vi.mock("@monaco-editor/react", () => ({ default: () => null }));

beforeEach(() => {
  useAppStore.setState({
    lang: "zh",
    rightMode: "content",
    rightContentTabBySession: { s1: "sources" },
  });
  useChatStore.setState({
    activeSessionId: "s1",
    messages: [],
    sessionMessages: { s1: [] },
    isStreaming: false,
    streamingSessionId: null,
    streamingSources: [],
    streamingTodos: [],
    streamingTodosSeen: {},
    sessionTodos: {},
    sessionTodosLoaded: {},
    sessionFileViewerSource: {},
    sessionHighlightSourceId: {},
  });
});

afterEach(cleanup);

describe("RightPanel changes tab", () => {
  it("renders the change-set panel from the changes tab", () => {
    render(<RightPanel />);

    fireEvent.click(screen.getByRole("button", { name: "变更" }));

    expect(screen.getByTestId("change-set-panel")).toBeTruthy();
  });
});
