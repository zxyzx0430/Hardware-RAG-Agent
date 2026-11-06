import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TodoCard from "./TodoCard";
import { useAppStore } from "../../stores/useAppStore";
import type { TodoItem } from "../../types/session";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({ settings: {} }),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue({}),
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  parseSseEventId: vi.fn(() => null),
  ApiError: class ApiError extends Error {},
  fetchBigChunk: vi.fn(),
}));

const todos: TodoItem[] = [
  { id: "t1", content: "Inspect datasheet", status: "completed" },
  { id: "t2", content: "Flash firmware", status: "in_progress" },
];

beforeEach(() => {
  useAppStore.setState({ lang: "zh" });
});

afterEach(cleanup);

describe("TodoCard", () => {
  it("renders progress and items", () => {
    render(<TodoCard todos={todos} />);
    expect(screen.getByText("1/2 完成")).toBeTruthy();
    expect(screen.getByText("Inspect datasheet")).toBeTruthy();
    expect(screen.getByText("Flash firmware")).toBeTruthy();
  });

  it("requires a second click before clearing", async () => {
    const onClear = vi.fn().mockResolvedValue(undefined);
    render(<TodoCard todos={todos} onClear={onClear} />);

    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(onClear).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "确认清空？" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "确认清空？" }));
    await waitFor(() => expect(onClear).toHaveBeenCalledTimes(1));
  });

  it("hides the clear button when no handler is supplied", () => {
    render(<TodoCard todos={todos} />);
    expect(screen.queryByRole("button", { name: "清空" })).toBeNull();
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(<TodoCard todos={[]} onClear={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });
});
