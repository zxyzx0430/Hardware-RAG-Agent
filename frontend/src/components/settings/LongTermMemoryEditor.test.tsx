import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LongTermMemoryEditor } from "./LongTermMemoryEditor";

afterEach(cleanup);

function renderEditor(value: string, onSave: (value: string) => Promise<boolean>) {
  return render(
    <LongTermMemoryEditor
      value={value}
      lang="zh"
      label="长期记忆"
      description="手动维护的偏好和项目背景。"
      onSave={onSave}
    />,
  );
}

describe("LongTermMemoryEditor", () => {
  it("saves only after an explicit click and shows confirmation after the request succeeds", async () => {
    let resolveSave: ((saved: boolean) => void) | undefined;
    const onSave = vi.fn(() => new Promise<boolean>((resolve) => {
      resolveSave = resolve;
    }));
    renderEditor("服务器保存值", onSave);

    const textarea = screen.getByRole("textbox", { name: "长期记忆" }) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "编辑草稿" } });
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));
    expect(onSave).toHaveBeenCalledWith("编辑草稿");
    expect(screen.getByRole("status").textContent).toBe("正在保存…");

    resolveSave?.(true);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("✓ 已保存"));
  });

  it("keeps the draft after a failed save and allows retry", async () => {
    const onSave = vi.fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    renderEditor("上次确认内容", onSave);

    const textarea = screen.getByRole("textbox", { name: "长期记忆" }) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "仍需保存的草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("草稿已保留"));
    expect(textarea.value).toBe("仍需保存的草稿");

    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("✓ 已保存"));
    expect(onSave).toHaveBeenNthCalledWith(2, "仍需保存的草稿");
  });

  it("clears only through an explicit clear-and-save action", async () => {
    const onSave = vi.fn().mockResolvedValue(true);
    renderEditor("要清除的记忆", onSave);

    fireEvent.click(screen.getByRole("button", { name: "清空并保存" }));

    expect(onSave).toHaveBeenCalledWith("");
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("✓ 已保存"));
    expect((screen.getByRole("textbox", { name: "长期记忆" }) as HTMLTextAreaElement).value).toBe("");
  });
});
