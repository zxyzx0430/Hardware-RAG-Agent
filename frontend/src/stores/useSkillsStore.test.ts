import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiGetMock, apiPostMock, apiPatchMock, apiDeleteMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiPatchMock: vi.fn(),
  apiDeleteMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
  apiPatch: apiPatchMock,
  apiDelete: apiDeleteMock,
}));

async function loadStore() {
  const { useSkillsStore } = await import("./useSkillsStore");
  return useSkillsStore;
}

const source = {
  owner: "example",
  repo: "skills",
  commit_sha: "a".repeat(40),
  path: "",
};

describe("skills API state", () => {
  beforeEach(() => {
    vi.resetModules();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiPatchMock.mockReset();
    apiDeleteMock.mockReset();
  });

  it("loads installed Markdown skills from the server", async () => {
    const skills = [{ id: "review", name: "review", description: "Review hardware", content: "# Review", enabled: false }];
    apiGetMock.mockResolvedValueOnce({ skills });
    const store = await loadStore();

    await expect(store.getState().fetchSkills()).resolves.toBe(true);

    expect(apiGetMock).toHaveBeenCalledWith("skills");
    expect(store.getState().skills).toEqual(skills);
  });

  it("previews a GitHub source without importing it", async () => {
    const preview = {
      source,
      candidates: [{ path: "skills/review", name: "review", description: "Review hardware", compatibility_status: "pending review", issues: [] }],
    };
    apiPostMock.mockResolvedValueOnce(preview);
    const store = await loadStore();

    await expect(store.getState().previewGitHubImport("https://github.com/example/skills")).resolves.toBe(true);

    expect(apiPostMock).toHaveBeenCalledWith("skills/github/preview", { url: "https://github.com/example/skills" }, 70_000);
    expect(store.getState().preview).toEqual(preview);
    expect(apiPostMock).toHaveBeenCalledTimes(1);
  });

  it("imports only the selected paths from the frozen preview source", async () => {
    const store = await loadStore();
    store.setState({
      preview: {
        source,
        candidates: [
          { path: "skills/review", name: "review", description: "Review" },
          { path: "skills/flash", name: "flash", description: "Flash" },
        ],
      },
    });
    const imported = [{ id: "review", name: "review", description: "Review", content: "# Review", enabled: false }];
    apiPostMock.mockResolvedValueOnce({ skills: imported });
    apiGetMock.mockResolvedValueOnce({ skills: imported });

    await expect(store.getState().importGitHubSkills(["skills/review"])).resolves.toBe(true);

    expect(apiPostMock).toHaveBeenCalledWith("skills/github/import", {
      source,
      paths: ["skills/review"],
    }, 70_000);
    expect(store.getState().skills).toEqual(imported);
    expect(store.getState().preview).toBeNull();
  });

  it("refreshes server status after an edited skill is saved", async () => {
    const editedSkill = {
      id: "external-review",
      name: "review",
      description: "Review",
      content: "# Updated",
      enabled: false,
      locally_modified: true,
    };
    const store = await loadStore();
    apiPatchMock.mockResolvedValueOnce({ updated: true });
    apiGetMock.mockResolvedValueOnce({ skills: [editedSkill] });

    await expect(store.getState().updateSkillContent("external-review", "# Updated")).resolves.toBe(true);

    expect(apiPatchMock).toHaveBeenCalledWith("skills/external-review", { content: "# Updated" });
    expect(store.getState().skills).toEqual([editedSkill]);
    expect(store.getState().skills[0].enabled).toBe(false);
  });

  it("keeps the preview available when an import request fails", async () => {
    const store = await loadStore();
    const preview = {
      source,
      candidates: [{ path: "skills/review", name: "review", description: "Review" }],
    };
    store.setState({ preview });
    apiPostMock.mockRejectedValueOnce(new Error("network timed out"));

    await expect(store.getState().importGitHubSkills(["skills/review"])).resolves.toBe(false);

    expect(apiPostMock).toHaveBeenCalledWith("skills/github/import", { source, paths: ["skills/review"] }, 70_000);
    expect(store.getState().preview).toEqual(preview);
    expect(store.getState().error).toContain("network timed out");
    expect(store.getState().importing).toBe(false);
  });

  it("removes a skill and its session selections only after DELETE succeeds", async () => {
    const skill = { id: "fixture review", name: "Review", description: "Fixture", content: "# Review", enabled: false };
    const store = await loadStore();
    store.setState({
      skills: [skill],
      chatSkillSelections: { "fixture-session": { mode: "manual", skillIds: [skill.id, "other"] } },
    });
    apiDeleteMock.mockResolvedValueOnce({ id: skill.id });

    await expect(store.getState().deleteSkill(skill.id)).resolves.toBe(true);

    expect(apiDeleteMock).toHaveBeenCalledWith("skills/fixture%20review");
    expect(store.getState().skills).toEqual([]);
    expect(store.getState().chatSkillSelections["fixture-session"]).toEqual({ mode: "manual", skillIds: ["other"] });
  });

  it("retains the installed list when DELETE fails", async () => {
    const skill = { id: "fixture review", name: "Review", description: "Fixture", content: "# Review", enabled: false };
    const store = await loadStore();
    store.setState({ skills: [skill] });
    apiDeleteMock.mockRejectedValueOnce(new Error("delete failed"));

    await expect(store.getState().deleteSkill(skill.id)).resolves.toBe(false);

    expect(apiDeleteMock).toHaveBeenCalledWith("skills/fixture%20review");
    expect(store.getState().skills).toEqual([skill]);
    expect(store.getState().error).toContain("delete failed");
  });
});
