import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SkillsManager } from "./SkillsManager";
import { useSkillsStore } from "../../stores/useSkillsStore";

const { apiGetMock, apiPostMock, apiPatchMock, apiDeleteMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
}));

afterEach(cleanup);

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPatchMock.mockReset();
  apiDeleteMock.mockReset();
  useSkillsStore.setState({
    skills: [], loading: false, loaded: false, error: null, preview: null, previewLoading: false, importing: false, chatSkillSelections: {},
  });
});

describe("SkillsManager", () => {
  it("requires an explicit candidate selection before GitHub import", async () => {
    const source = { owner: "example", repo: "skills", commit_sha: "a".repeat(40), path: "" };
    const candidate = { path: "skills/review", name: "review", description: "Review hardware", compatibility_status: "partial", issues: ["No write tools"] };
    const imported = { id: "review", name: "review", description: "Review hardware", content: "# Review", enabled: false };
    apiGetMock.mockResolvedValueOnce({ skills: [] }).mockResolvedValueOnce({ skills: [imported] });
    apiPostMock
      .mockResolvedValueOnce({ source, candidates: [candidate] })
      .mockResolvedValueOnce({ skills: [imported] });
    render(<SkillsManager lang="zh" />);

    fireEvent.click(screen.getByRole("button", { name: "从 GitHub 导入" }));
    fireEvent.change(screen.getByRole("textbox", { name: "仓库或技能目录链接" }), {
      target: { value: "https://github.com/example/skills" },
    });
    fireEvent.click(screen.getByRole("button", { name: "预览候选技能" }));
    await screen.findByText("skills/review");

    const installButton = screen.getByRole("button", { name: /安装选中技能/ });
    expect((installButton as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: "选择: review" }) as HTMLInputElement).checked).toBe(false);
    expect(document.body.textContent).toContain("部分支持");

    fireEvent.click(screen.getByRole("checkbox", { name: "选择: review" }));
    fireEvent.click(screen.getByRole("button", { name: /安装选中技能/ }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("默认保持停用"));
    expect(apiPostMock).toHaveBeenNthCalledWith(1, "skills/github/preview", { url: "https://github.com/example/skills" }, 70_000);
    expect(apiPostMock).toHaveBeenNthCalledWith(2, "skills/github/import", { source, paths: ["skills/review"] }, 70_000);
  });

  it("shows the skill content and compatibility notes before enabling it", async () => {
    const skill = {
      id: "review",
      name: "review",
      description: "Review hardware",
      content: "# Review instructions",
      enabled: false,
      compatibility_status: "partial",
      issues: ["Read-only references only"],
    };
    apiGetMock
      .mockResolvedValueOnce({ skills: [skill] })
      .mockResolvedValueOnce(skill)
      .mockResolvedValueOnce({ skills: [{ ...skill, enabled: true }] });
    apiPatchMock.mockResolvedValueOnce({ updated: true });
    render(<SkillsManager lang="zh" />);
    await screen.findByText("Review hardware");

    fireEvent.click(screen.getByRole("button", { name: "检查并启用: review" }));
    expect(await screen.findByText("# Review instructions")).toBeTruthy();
    expect(screen.getByText("Read-only references only")).toBeTruthy();
    expect(apiPatchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认启用" }));
    await waitFor(() => expect(apiPatchMock).toHaveBeenCalledWith("skills/review", { enabled: true }));
  });

  it("blocks confirmation for review-required skills and explains their resource checks", async () => {
    const skill = {
      id: "needs-review",
      name: "needs-review",
      description: "Needs compatibility review",
      content: "# Review first",
      enabled: false,
      compatibility_status: "review_required",
      issues: [{ code: "UNSUPPORTED_TOOL", severity: "warning", message: "This skill asks for an unsupported tool." }],
      resources: [
        { path: "references/chip.md", size: 120, supported: true },
        { path: "scripts/install.py", size: 450, supported: false },
      ],
    };
    apiGetMock.mockResolvedValueOnce({ skills: [skill] }).mockResolvedValueOnce(skill);
    render(<SkillsManager lang="zh" />);
    await screen.findByText("Needs compatibility review");

    fireEvent.click(screen.getByRole("button", { name: "检查并启用: needs-review" }));
    await screen.findByText("# Review first");
    expect(document.body.textContent).toContain("This skill asks for an unsupported tool.");
    expect(document.body.textContent).toContain("warning · UNSUPPORTED_TOOL");
    expect(document.body.textContent).toContain("references/chip.md");
    expect(document.body.textContent).toContain("支持读取");

    const confirmButton = screen.getByRole("button", { name: "确认启用" }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
    fireEvent.click(confirmButton);
    expect(apiPatchMock).not.toHaveBeenCalled();
  });

  it("keeps the selected candidate checked when GitHub import fails", async () => {
    const source = { owner: "example", repo: "skills", commit_sha: "b".repeat(40), path: "" };
    const candidate = { path: "skills/review", name: "review", description: "Review hardware" };
    apiGetMock.mockResolvedValueOnce({ skills: [] });
    apiPostMock
      .mockResolvedValueOnce({ source, candidates: [candidate] })
      .mockRejectedValueOnce(new Error("import timed out"));
    render(<SkillsManager lang="zh" />);

    fireEvent.click(screen.getByRole("button", { name: "从 GitHub 导入" }));
    fireEvent.change(screen.getByRole("textbox", { name: "仓库或技能目录链接" }), {
      target: { value: "https://github.com/example/skills" },
    });
    fireEvent.click(screen.getByRole("button", { name: "预览候选技能" }));
    const candidateCheckbox = await screen.findByRole("checkbox", { name: "选择: review" }) as HTMLInputElement;
    fireEvent.click(candidateCheckbox);
    fireEvent.click(screen.getByRole("button", { name: /安装选中技能/ }));

    await screen.findByText("import timed out");
    expect((screen.getByRole("checkbox", { name: "选择: review" }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("button", { name: /安装选中技能 \(1\)/ })).toBeTruthy();
  });

  it("keeps the edited Markdown draft open when saving fails", async () => {
    const skill = {
      id: "review",
      name: "review",
      description: "Review hardware",
      content: "# Original",
      enabled: false,
    };
    apiGetMock.mockResolvedValueOnce({ skills: [skill] }).mockResolvedValueOnce(skill);
    apiPatchMock.mockRejectedValueOnce(new Error("save failed"));
    render(<SkillsManager lang="zh" />);
    await screen.findByText("Review hardware");

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const editor = await screen.findByRole("textbox", { name: "技能 Markdown 内容" }) as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "# User draft" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await screen.findByText("save failed");
    expect((screen.getByRole("textbox", { name: "技能 Markdown 内容" }) as HTMLTextAreaElement).value).toBe("# User draft");
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("binds deletion confirmation to the shown skill ID and lets the user cancel", async () => {
    const skill = {
      id: "fixture-brand-guidelines",
      name: "brand-guidelines",
      description: "Fixture skill only",
      content: "# Brand guidelines",
      enabled: false,
    };
    apiGetMock.mockResolvedValueOnce({ skills: [skill] });
    render(<SkillsManager lang="zh" />);
    await screen.findByText("Fixture skill only");

    fireEvent.click(screen.getByRole("button", { name: "删除技能: brand-guidelines" }));
    const confirmation = await screen.findByRole("alertdialog");
    expect(confirmation.textContent).toContain("brand-guidelines");
    expect(confirmation.textContent).toContain("fixture-brand-guidelines");
    fireEvent.click(screen.getByRole("button", { name: "取消删除" }));

    expect(apiDeleteMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "删除技能: brand-guidelines" })).toBeTruthy();
  });

  it("keeps the skill listed and the confirmation open when DELETE fails", async () => {
    const skill = {
      id: "fixture-brand-guidelines",
      name: "brand-guidelines",
      description: "Fixture skill only",
      content: "# Brand guidelines",
      enabled: false,
    };
    apiGetMock.mockResolvedValueOnce({ skills: [skill] });
    apiDeleteMock.mockRejectedValueOnce(new Error("fixture delete failed"));
    render(<SkillsManager lang="zh" />);
    await screen.findByText("Fixture skill only");

    fireEvent.click(screen.getByRole("button", { name: "删除技能: brand-guidelines" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await screen.findByText("fixture delete failed");
    expect(screen.getByRole("alertdialog").textContent).toContain("fixture-brand-guidelines");
    expect(screen.getByRole("button", { name: "删除技能: brand-guidelines" })).toBeTruthy();
    expect(apiDeleteMock).toHaveBeenCalledWith("skills/fixture-brand-guidelines");
  });

  it("removes the fixture from the list after DELETE succeeds", async () => {
    const skill = {
      id: "fixture-brand-guidelines",
      name: "brand-guidelines",
      description: "Fixture skill only",
      content: "# Brand guidelines",
      enabled: false,
    };
    apiGetMock.mockResolvedValueOnce({ skills: [skill] });
    apiDeleteMock.mockResolvedValueOnce({ id: skill.id });
    render(<SkillsManager lang="zh" />);
    await screen.findByText("Fixture skill only");

    fireEvent.click(screen.getByRole("button", { name: "删除技能: brand-guidelines" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));

    await screen.findByText("还没有 Markdown Skill。你可以从公共 GitHub 仓库预览并导入。");
    expect(apiDeleteMock).toHaveBeenCalledWith("skills/fixture-brand-guidelines");
    expect(screen.queryByRole("button", { name: "删除技能: brand-guidelines" })).toBeNull();
  });
});
