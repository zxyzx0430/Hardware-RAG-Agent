import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceGitPanel } from "./WorkspaceGitPanel";

const { apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  apiGet: apiGetMock,
  apiPost: apiPostMock,
}));

beforeEach(() => {
  apiGetMock.mockImplementation((path: string) => {
    if (path.includes("/git/pr")) {
      return Promise.resolve({ ok: true, pr: null, checks: [], message: "no PR" });
    }
    return Promise.resolve({
      ok: true,
      root: "E:/workspace",
      is_git: true,
      branch: "feature/test",
      head: "1234567890abcdef",
      dirty: ["src/a.c"],
      untracked: [],
      remote_url: "https://github.com/example/repo.git",
      upstream: "origin/feature/test",
    });
  });
  apiPostMock.mockReset().mockResolvedValue({ ok: true });
});

afterEach(cleanup);

describe("WorkspaceGitPanel", () => {
  it("shows branch and dirty state, and exposes Git actions", async () => {
    render(
      <WorkspaceGitPanel sessionId="s1" workspaceRoot="E:/workspace" changeSets={[]} />,
    );

    await waitFor(() => expect(screen.getAllByText("feature/test").length).toBeGreaterThan(0));
    expect(screen.getByText(/未提交/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "创建分支" })).toBeTruthy();
    expect(screen.getByText("no PR")).toBeTruthy();
  });

  it("disables commit until there is an accepted change", async () => {
    render(
      <WorkspaceGitPanel sessionId="s1" workspaceRoot="E:/workspace" changeSets={[]} />,
    );

    await waitFor(() => {
      const button = screen.getByRole("button", { name: /提交已接受/ }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
    });
  });
});
