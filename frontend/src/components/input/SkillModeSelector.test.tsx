import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SkillModeSelector } from "./SkillModeSelector";
import { useSkillsStore } from "../../stores/useSkillsStore";

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
}));

afterEach(cleanup);

beforeEach(() => {
  apiGetMock.mockReset();
  useSkillsStore.setState({
    skills: [], loading: false, loaded: false, error: null, preview: null,
    previewLoading: false, importing: false, chatSkillSelections: {},
  });
});

describe("SkillModeSelector", () => {
  it("loads enabled skills on demand and limits manual selection to three per session", async () => {
    apiGetMock.mockResolvedValueOnce({ skills: [
      { id: "s1", name: "Review", description: "Review a design", enabled: true },
      { id: "s2", name: "Datasheet", description: "Read datasheets", enabled: true },
      { id: "s3", name: "Checklist", description: "Check pins", enabled: true },
      { id: "s4", name: "Extra", description: "Extra review", enabled: true },
      { id: "disabled", name: "Disabled", description: "Not available", enabled: false },
    ] });
    render(<SkillModeSelector sessionId="session-a" lang="zh" />);

    expect(screen.getByRole("button", { name: "技能模式：关闭" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "技能模式：关闭" }));
    fireEvent.click(screen.getByRole("radio", { name: /自动选择/ }));
    expect(useSkillsStore.getState().chatSkillSelections["session-a"]).toMatchObject({ mode: "auto", skillIds: [] });
    fireEvent.click(screen.getByRole("radio", { name: /手动指定/ }));

    const checkboxes = await screen.findAllByRole("checkbox");
    expect(checkboxes).toHaveLength(4);
    checkboxes.slice(0, 3).forEach((checkbox) => fireEvent.click(checkbox));
    expect((checkboxes[3] as HTMLInputElement).disabled).toBe(true);
    expect(useSkillsStore.getState().chatSkillSelections["session-a"]).toEqual({ mode: "manual", skillIds: ["s1", "s2", "s3"] });
    expect(apiGetMock).toHaveBeenCalledTimes(1);
  });

  it("does not loop failed list requests and leaves a manual refresh available", async () => {
    apiGetMock.mockRejectedValue(new Error("skills unavailable"));
    render(<SkillModeSelector sessionId="session-a" lang="zh" />);

    fireEvent.click(screen.getByRole("button", { name: "技能模式：关闭" }));
    fireEvent.click(screen.getByRole("radio", { name: /手动指定/ }));
    await screen.findByRole("alert");
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(apiGetMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByRole("alert");
    expect(apiGetMock).toHaveBeenCalledTimes(2);
  });
});
