import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChangeSetPanel } from "./ChangeSetPanel";
import { useChatStore } from "../../stores/useChatStore";
import type { ChangeItem } from "../../types/change";

const { apiGetMock, apiPostMock, apiPatchMock } = vi.hoisted(() => ({ apiGetMock: vi.fn(), apiPostMock: vi.fn(), apiPatchMock: vi.fn() }));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPatch: apiPatchMock,
  apiDelete: vi.fn(),
  apiSSE: vi.fn(),
  AUTH_TOKEN_CHANGED_EVENT: "hwrag:auth-token-changed",
  getSessionToken: () => null,
}));

vi.mock("@monaco-editor/react", () => ({
  DiffEditor: ({ original, modified }: { original?: string; modified?: string }) => (
    <div data-testid="monaco-diff">{`${original} → ${modified}`}</div>
  ),
}));

const item: ChangeItem = {
  id: "ci_1",
  change_set_id: "cs_1",
  call_id: "call_1",
  tool_name: "edit_file",
  operation: "modify",
  path: "src/a.c",
  before_sha256: "a".repeat(64),
  after_sha256: "b".repeat(64),
  before_blob_ref: "a".repeat(64),
  after_blob_ref: "b".repeat(64),
  diff_stats: { additions: 1, deletions: 1, changed: true },
  risk: "medium",
  status: "observed",
  review_status: "pending",
  review_note: null,
  decided_at: null,
  plan_step_id: null,
  plan_step_index: null,
  created_at: "2026-11-09T00:00:00Z",
};

beforeEach(() => {
  useChatStore.setState({
    activeSessionId: "s1",
    changeSetsBySession: {},
    changeSetsLoadingBySession: {},
    workspaceContextBySession: {},
  });
  apiPostMock.mockReset().mockResolvedValue({});
  apiPatchMock.mockReset().mockResolvedValue({});
  apiGetMock.mockImplementation((path: string) => {
    if (path.includes("change-items/")) {
      return Promise.resolve({
        item,
        before_content: "old",
        after_content: "new",
        available: true,
        reason: null,
      });
    }
    return Promise.resolve({
      workspace: {
        root: "E:/project",
        is_git: true,
        branch: "main",
        head: "abc123",
        dirty: ["src/a.c"],
        untracked: [],
      },
      change_sets: [{
        id: "cs_1",
        session_id: "s1",
        run_id: "run_1",
        plan_id: null,
        plan_version: null,
        workspace_root: "E:/project",
        status: "open",
        base_ref: "main",
        base_sha: "abc123",
        summary: {},
        created_at: "2026-11-09T00:00:00Z",
        updated_at: "2026-11-09T00:00:00Z",
        items: [item],
      }],
    });
  });
});

afterEach(cleanup);

describe("ChangeSetPanel", () => {
  it("loads the session change set and shows the selected file diff", async () => {
    render(<ChangeSetPanel />);

    await waitFor(() => expect(screen.getAllByText("src/a.c").length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByTestId("monaco-diff")).toBeTruthy());
    expect(screen.getByText(/main/)).toBeTruthy();
    expect(screen.getByText(/未提交 1/)).toBeTruthy();
  });

  it("sends item review decisions", async () => {
    render(<ChangeSetPanel />);

    await waitFor(() => expect(screen.getByRole("button", { name: "接受" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "接受" }));

    await waitFor(() => expect(apiPatchMock).toHaveBeenCalledWith(
      "change-items/ci_1",
      expect.objectContaining({ review_status: "accepted" }),
    ));
  });

  it("rejects and reverts a captured run after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ChangeSetPanel />);

    await waitFor(() => expect(screen.getByRole("button", { name: "全部拒绝" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "全部拒绝" }));

    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith(
      "change-sets/cs_1/revert",
      {},
    ));
    confirm.mockRestore();
  });

  it("rejects and reverts a single item after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ChangeSetPanel />);

    await waitFor(() => expect(screen.getByRole("button", { name: "拒绝并撤销" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "拒绝并撤销" }));

    await waitFor(() => expect(apiPostMock).toHaveBeenCalledWith(
      "change-items/ci_1/revert",
      {},
    ));
    confirm.mockRestore();
  });

  it("does not render removed Git or validation controls", async () => {
    render(<ChangeSetPanel />);

    await waitFor(() => expect(screen.getAllByText("src/a.c").length).toBeGreaterThan(0));
    expect(screen.queryByText("Git / PR / CI")).toBeNull();
    expect(screen.queryByText("验证")).toBeNull();
    expect(screen.queryByPlaceholderText("例如 python -m pytest tests/foo.py")).toBeNull();
    expect(screen.queryByRole("button", { name: "撤销" })).toBeNull();
    expect(screen.queryByRole("button", { name: "拒绝" })).toBeNull();
  });

  it("shows an explicit empty state when no changes were captured", async () => {
    apiGetMock.mockResolvedValueOnce({
      workspace: {
        root: "E:/project", is_git: false, branch: null, head: null,
        dirty: [], untracked: [],
      },
      change_sets: [],
    });
    render(<ChangeSetPanel />);

    await waitFor(() => expect(screen.getByText("本会话还没有捕获到文件变更。")).toBeTruthy());
  });
});
