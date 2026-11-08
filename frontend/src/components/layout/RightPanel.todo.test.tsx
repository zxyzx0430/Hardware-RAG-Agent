import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RightPanel } from "./RightPanel";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import type { Message, SourceRef, TodoItem } from "../../types/session";

const { apiDeleteMock } = vi.hoisted(() => ({ apiDeleteMock: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({}),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: apiDeleteMock,
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  parseSseEventId: vi.fn(() => null),
  ApiError: class ApiError extends Error {},
  fetchBigChunk: vi.fn().mockResolvedValue({ text: "" }),
}));

vi.mock("../workbench/WorkbenchPanel", () => ({ WorkbenchPanel: () => null }));
vi.mock("../plan/PlanPanel", () => ({ PlanPanel: () => <div data-testid="plan-panel" /> }));
vi.mock("../shared/MarkdownRenderer", () => ({ useAppliedDarkMode: () => false }));
vi.mock("@monaco-editor/react", () => ({
  default: ({ value }: { value?: string }) => <div data-testid="monaco">{value}</div>,
}));

const source: SourceRef = {
  id: "src1",
  title: "BME280 datasheet",
  doc: "bme280.pdf",
  page: 3,
  page_start: 3,
  page_end: 3,
  score: 0.91,
  excerpt: "I2C address 0x76",
  messageId: "m1",
};

const todos: TodoItem[] = [
  { id: "t1", content: "Inspect datasheet", status: "completed", priority: "high" },
  { id: "t2", content: "Flash firmware", status: "in_progress", priority: "medium" },
];

const assistantMessage: Message = {
  id: "m1",
  role: "assistant",
  content: "Answer",
  timestamp: 1,
  sources: [source],
};

function resetState(overrides: Partial<ReturnType<typeof useChatStore.getState>> = {}) {
  useAppStore.setState({
    lang: "zh",
    rightMode: "content",
    rightContentTabBySession: { s1: "sources" },
  });
  useChatStore.setState({
    activeSessionId: "s1",
    messages: [assistantMessage],
    sessionMessages: { s1: [assistantMessage] },
    isStreaming: false,
    streamingSessionId: null,
    streamingSources: [],
    streamingTodos: [],
    streamingTodosSeen: {},
    sessionTodos: { s1: todos },
    sessionTodosLoaded: { s1: true },
    sessionFileViewerSource: {},
    sessionHighlightSourceId: {},
    ...overrides,
  });
}

beforeEach(() => {
  apiDeleteMock.mockClear();
  localStorage.clear();
});

afterEach(cleanup);

describe("RightPanel todo tab", () => {
  it("keeps the tab bar usable while a source viewer is open", () => {
    resetState({ sessionFileViewerSource: { s1: { messageId: "m1", sourceId: "src1" } } });
    render(<RightPanel />);

    expect(screen.getByText("BME280 datasheet")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "待办" }));

    expect(screen.getByText("Flash firmware")).toBeTruthy();
    expect(screen.getByRole("button", { name: "来源引用" })).toBeTruthy();
  });

  it("explains the empty todo state", () => {
    resetState({
      sessionTodos: { s1: [] },
      sessionTodosLoaded: { s1: true },
    });
    useAppStore.setState({ rightContentTabBySession: { s1: "todos" } });
    render(<RightPanel />);

    expect(screen.getByText("暂无任务清单")).toBeTruthy();
    expect(screen.getByText("Agent 在执行多步骤任务时会自动生成待办清单。")).toBeTruthy();
  });

  it("switches to the sources tab when a source opens from the todos tab", () => {
    resetState({ sessionFileViewerSource: {} });
    useAppStore.setState({ rightContentTabBySession: { s1: "todos" } });
    render(<RightPanel />);

    expect(screen.getByText("Flash firmware")).toBeTruthy();

    act(() => {
      useChatStore.getState().setSessionFileViewerSource("s1", "m1", "src1");
    });

    expect(screen.getByText("BME280 datasheet")).toBeTruthy();
    expect(screen.queryByText("Flash firmware")).toBeNull();
    expect(screen.getByRole("button", { name: "来源引用" }).className).toContain("active");
  });

  it("clears the persisted list after two clicks", async () => {
    resetState();
    useAppStore.setState({ rightContentTabBySession: { s1: "todos" } });
    render(<RightPanel />);

    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    fireEvent.click(screen.getByRole("button", { name: "确认清空？" }));

    await waitFor(() => expect(apiDeleteMock).toHaveBeenCalledWith("sessions/s1/todos"));
  });
});
