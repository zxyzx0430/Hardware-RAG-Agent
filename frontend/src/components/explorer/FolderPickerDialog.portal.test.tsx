import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FolderPickerDialog } from "./FolderPickerDialog";

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({
    current: "C:\\fixture",
    parent: null,
    directories: [],
    files: [],
  }),
}));

afterEach(() => cleanup());

describe("FolderPickerDialog portal", () => {
  it("renders the modal on document.body instead of inside its mount container", async () => {
    const { container } = render(
      <div className="host-with-containment">
        <FolderPickerDialog onConfirm={() => {}} onCancel={() => {}} />
      </div>,
    );

    await vi.waitFor(() => {
      expect(document.querySelector(".folder-picker-embed")).toBeTruthy();
    });

    const overlay = document.querySelector(".folder-picker-embed")!;
    expect(overlay.parentElement).toBe(document.body);
    expect(overlay.closest(".host-with-containment")).toBeNull();
    expect(container.querySelector(".folder-picker-embed")).toBeNull();
  });
});
