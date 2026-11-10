import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChangeDiffViewer } from "./ChangeDiffViewer";
import type { ChangeItem } from "../../types/change";

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
  path: "src/firmware.c",
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

afterEach(cleanup);

describe("ChangeDiffViewer", () => {
  it("renders before and after content for an available diff", () => {
    render(
      <ChangeDiffViewer
        item={item}
        beforeContent="old"
        afterContent="new"
        available={true}
      />,
    );

    expect(screen.getByTestId("monaco-diff").textContent).toContain("old → new");
    expect(screen.getByText("src/firmware.c")).toBeTruthy();
  });

  it("explains why binary or oversized changes have no diff", () => {
    render(
      <ChangeDiffViewer
        item={{ ...item, path: "assets/logo.png" }}
        beforeContent={null}
        afterContent={null}
        available={false}
        reason="binary content is not diffable"
      />,
    );

    expect(screen.getByText("binary content is not diffable")).toBeTruthy();
    expect(screen.queryByTestId("monaco-diff")).toBeNull();
  });
});
