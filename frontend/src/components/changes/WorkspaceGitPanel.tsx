import { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useI18n } from "../../i18n";
import { useLogStore } from "../../stores/useLogStore";
import type { ChangeSet } from "../../types/change";

export interface GitContext {
  ok: boolean;
  root: string;
  is_git: boolean;
  branch: string | null;
  head: string | null;
  dirty: string[];
  untracked: string[];
  remote_url: string | null;
  upstream: string | null;
}

interface GitCheck {
  name?: string;
  state?: string;
  bucket?: string;
  link?: string;
}

interface PrStatus {
  ok: boolean;
  code?: string;
  message?: string;
  pr?: {
    number?: number;
    url?: string;
    isDraft?: boolean;
    state?: string;
    mergeable?: string;
    headRefName?: string;
    baseRefName?: string;
  } | null;
  checks?: GitCheck[];
}

export function WorkspaceGitPanel({
  sessionId, workspaceRoot, changeSets,
}: {
  sessionId: string;
  workspaceRoot: string | null;
  changeSets: ChangeSet[];
}) {
  const { lang } = useI18n();
  const [context, setContext] = useState<GitContext | null>(null);
  const [pr, setPr] = useState<PrStatus | null>(null);
  const [branchName, setBranchName] = useState("");
  const [commitMessage, setCommitMessage] = useState("");
  const [baseBranch, setBaseBranch] = useState("main");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const acceptedPaths = useMemo(
    () => changeSets.flatMap((set) => set.items)
      .filter((item) => item.review_status === "accepted" && item.status === "observed")
      .map((item) => item.path)
      .filter(Boolean),
    [changeSets],
  );

  const loadContext = useCallback(async () => {
    if (!sessionId) return;
    try {
      const data = await apiGet<GitContext>(`sessions/${encodeURIComponent(sessionId)}/git`);
      setContext(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [sessionId]);

  const loadPr = useCallback(async () => {
    if (!sessionId) return;
    try {
      const data = await apiGet<PrStatus>(`sessions/${encodeURIComponent(sessionId)}/git/pr`);
      setPr(data);
    } catch (err) {
      setPr({ ok: false, message: err instanceof Error ? err.message : String(err) });
    }
  }, [sessionId]);

  useEffect(() => { void loadContext(); }, [loadContext, workspaceRoot]);
  useEffect(() => { void loadPr(); }, [loadPr, workspaceRoot]);

  const run = async (kind: string, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      await action();
      await loadContext();
      useLogStore.getState().log("ok", "git", `${kind} completed`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("warn", "git", `${kind} failed: ${message}`);
      setError(message);
    } finally {
      setBusy(null);
    }
  };

  const createBranch = () => {
    const name = branchName.trim();
    if (!name) return;
    if (!window.confirm(lang === "en" ? `Create branch ${name}?` : `创建分支 ${name}？`)) return;
    void run("branch", async () => {
      await apiPost(`sessions/${encodeURIComponent(sessionId)}/git/branch`, { name });
      setBranchName("");
    });
  };

  const commitAccepted = () => {
    const message = commitMessage.trim();
    if (!message || acceptedPaths.length === 0) return;
    if (!window.confirm(lang === "en" ? `Commit ${acceptedPaths.length} accepted file(s)?` : `提交 ${acceptedPaths.length} 个已接受文件？`)) return;
    void run("commit", async () => {
      await apiPost(`sessions/${encodeURIComponent(sessionId)}/git/commit`, {
        message, paths: acceptedPaths,
      });
      setCommitMessage("");
    });
  };

  const createPr = () => {
    if (!window.confirm(lang === "en" ? "Push the current branch and create a draft PR?" : "推送当前分支并创建草稿 PR？")) return;
    void run("pr", async () => {
      await apiPost(`sessions/${encodeURIComponent(sessionId)}/git/pr`, {
        base: baseBranch.trim() || "main",
        title: `Draft: ${context?.branch || "changes"}`,
        body: "Created from Hardware RAG Agent change review.",
        push: true,
      });
      await loadPr();
    });
  };

  return (
    <details className="workspace-git-panel" open>
      <summary>
        <strong>Git / PR / CI</strong>
        {context?.is_git ? <span>{context.branch || "HEAD"}</span> : null}
      </summary>
      {error ? <div className="change-error" role="alert">{error}</div> : null}
      {!context ? (
        <div className="change-empty">{lang === "en" ? "Loading Git context…" : "正在加载 Git 信息…"}</div>
      ) : !context.is_git ? (
        <div className="change-empty">{lang === "en" ? "This workspace is not a Git repository." : "当前工作区不是 Git 仓库。"}</div>
      ) : (
        <>
          <div className="workspace-git-context">
            <span>{context.branch}</span>
            <span>{context.head?.slice(0, 12) || "—"}</span>
            <span>{lang === "en" ? "dirty" : "未提交"} {context.dirty.length}</span>
            <span>{lang === "en" ? "untracked" : "未跟踪"} {context.untracked.length}</span>
            {context.remote_url ? <span title={context.remote_url}>{context.remote_url}</span> : null}
          </div>

          <div className="workspace-git-form">
            <input
              type="text"
              value={branchName}
              onChange={(e) => setBranchName(e.target.value)}
              placeholder={lang === "en" ? "new branch name" : "新分支名"}
            />
            <button type="button" disabled={busy !== null || !branchName.trim()} onClick={createBranch}>
              {busy === "branch" ? "…" : (lang === "en" ? "Create branch" : "创建分支")}
            </button>
          </div>

          <div className="workspace-git-form">
            <input
              type="text"
              value={commitMessage}
              onChange={(e) => setCommitMessage(e.target.value)}
              placeholder={lang === "en" ? "commit message" : "提交信息"}
            />
            <button type="button" disabled={busy !== null || !commitMessage.trim() || acceptedPaths.length === 0} onClick={commitAccepted}>
              {busy === "commit" ? "…" : (lang === "en" ? `Commit accepted (${acceptedPaths.length})` : `提交已接受 (${acceptedPaths.length})`)}
            </button>
          </div>

          <div className="workspace-git-form">
            <input
              type="text"
              value={baseBranch}
              onChange={(e) => setBaseBranch(e.target.value)}
              placeholder={lang === "en" ? "PR base branch" : "PR 目标分支"}
            />
            <button type="button" disabled={busy !== null} onClick={createPr}>
              {busy === "pr" ? "…" : (lang === "en" ? "Draft PR" : "草稿 PR")}
            </button>
            <button type="button" disabled={busy !== null} onClick={() => void loadPr()}>
              {lang === "en" ? "Refresh PR" : "刷新 PR"}
            </button>
          </div>

          {pr ? (
            <div className="workspace-pr-status">
              {pr.ok && pr.pr ? (
                <>
                  <a href={pr.pr.url} target="_blank" rel="noreferrer">
                    PR #{pr.pr.number} {pr.pr.isDraft ? "(draft)" : ""}
                  </a>
                  <span>{pr.pr.state} · {pr.pr.mergeable}</span>
                  {pr.checks?.length ? (
                    <ul>
                      {pr.checks.map((check, index) => (
                        <li key={`${check.name}-${index}`}>
                          <span>{check.name}</span>
                          <span>{check.bucket || check.state}</span>
                        </li>
                      ))}
                    </ul>
                  ) : <span>{lang === "en" ? "No CI checks" : "暂无 CI 检查"}</span>}
                </>
              ) : (
                <span>{pr.message || pr.code || (lang === "en" ? "No PR status" : "暂无 PR 状态")}</span>
              )}
            </div>
          ) : null}
        </>
      )}
    </details>
  );
}

export default WorkspaceGitPanel;
