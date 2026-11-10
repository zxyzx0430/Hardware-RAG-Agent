import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPatch, apiPost } from "../../api/client";
import { useChatStore } from "../../stores/useChatStore";
import { useLogStore } from "../../stores/useLogStore";
import { useI18n } from "../../i18n";
import type {
  ChangeItem,
  ChangeItemDiffResponse,
  ChangeSet,
  ValidationRun,
} from "../../types/change";
import { ChangeDiffViewer } from "./ChangeDiffViewer";
import { WorkspaceGitPanel } from "./WorkspaceGitPanel";

const EMPTY_CHANGE_SETS: ChangeSet[] = [];

function statusLabel(status: string, lang: string): string {
  const labels: Record<string, [string, string]> = {
    open: ["待审阅", "Open"],
    reviewing: ["审阅中", "Reviewing"],
    accepted: ["已接受", "Accepted"],
    rejected: ["已拒绝", "Rejected"],
    observed: ["已捕获", "Observed"],
    unobserved: ["未观察", "Unobserved"],
    conflict: ["冲突", "Conflict"],
    reverted: ["已撤销", "Reverted"],
    partially_reverted: ["部分撤销", "Partially reverted"],
    passed: ["通过", "Passed"],
    failed: ["失败", "Failed"],
    timeout: ["超时", "Timeout"],
    running: ["运行中", "Running"],
    not_run: ["未运行", "Not run"],
    "not-run": ["未运行", "Not run"],
  };
  return labels[status]?.[lang === "en" ? 1 : 0] ?? status;
}

function reviewLabel(status: string, lang: string): string {
  if (status === "accepted") return lang === "en" ? "Accepted" : "已接受";
  if (status === "rejected") return lang === "en" ? "Rejected" : "已拒绝";
  return lang === "en" ? "Pending review" : "待审阅";
}

function ChangeItemRow({
  item, selected, onSelect,
}: { item: ChangeItem; selected: boolean; onSelect: () => void }) {
  const { lang } = useI18n();
  const additions = item.diff_stats.additions ?? 0;
  const deletions = item.diff_stats.deletions ?? 0;
  return (
    <button
      type="button"
      className={`change-item-row${selected ? " selected" : ""}`}
      onClick={onSelect}
    >
      <span className="change-item-path" title={item.path || item.tool_name}>
        {item.path || (lang === "en" ? "(unobserved)" : "(未观察)")}
      </span>
      <span className={`change-item-status status-${item.status}`}>
        {statusLabel(item.status, lang)}
      </span>
      <span className="change-item-meta">
        {item.tool_name}
        {item.plan_step_index !== null ? ` · ${lang === "en" ? "step" : "步骤"} ${item.plan_step_index + 1}` : ""}
        {additions || deletions ? ` · +${additions} −${deletions}` : ""}
      </span>
      <span className={`change-item-review review-${item.review_status}`}>
        {reviewLabel(item.review_status, lang)}
      </span>
    </button>
  );
}

function ChangeSetGroup({
  changeSet, selectedId, onSelect, onReviewSet, onRevertSet, busy,
}: {
  changeSet: ChangeSet;
  selectedId: string | null;
  onSelect: (item: ChangeItem) => void;
  onReviewSet: (status: "accepted" | "rejected") => void;
  onRevertSet: () => void;
  busy: boolean;
}) {
  const { lang } = useI18n();
  const planLabel = changeSet.plan_id
    ? `${lang === "en" ? "Plan" : "计划"} ${changeSet.plan_id.slice(0, 10)} v${changeSet.plan_version ?? "?"}`
    : (lang === "en" ? "Unplanned changes" : "计划外变更");
  return (
    <section className="change-set-group">
      <div className="change-set-heading">
        <span>{lang === "en" ? "Run" : "运行"} {changeSet.run_id.slice(0, 14)}…</span>
        <span className="change-set-plan">{planLabel}</span>
        <span className="change-set-status">{statusLabel(changeSet.status, lang)}</span>
        <span className="change-set-actions">
          <button type="button" disabled={busy} onClick={() => onReviewSet("accepted")}>
            {lang === "en" ? "Accept all" : "全部接受"}
          </button>
          <button type="button" disabled={busy} onClick={() => onReviewSet("rejected")}>
            {lang === "en" ? "Reject all" : "全部拒绝"}
          </button>
          <button type="button" className="danger" disabled={busy} onClick={onRevertSet}>
            {lang === "en" ? "Revert run" : "撤销本轮"}
          </button>
        </span>
      </div>
      {changeSet.items.map((item) => (
        <ChangeItemRow
          key={item.id}
          item={item}
          selected={selectedId === item.id}
          onSelect={() => onSelect(item)}
        />
      ))}
    </section>
  );
}

export function ChangeSetPanel() {
  const { lang } = useI18n();
  const sessionId = useChatStore((s) => s.activeSessionId);
  const changeSets = useChatStore(
    (s) => s.changeSetsBySession[s.activeSessionId] ?? EMPTY_CHANGE_SETS,
  );
  const loading = useChatStore((s) => s.changeSetsLoadingBySession[s.activeSessionId] ?? false);
  const workspace = useChatStore((s) => s.workspaceContextBySession[s.activeSessionId]);
  const selectedId = useChatStore(
    (s) => s.selectedChangeItemBySession[s.activeSessionId] ?? null,
  );
  const setSelectedChangeItem = useChatStore((s) => s.setSelectedChangeItem);
  const loadChangeSets = useChatStore((s) => s.loadChangeSets);
  const [selected, setSelected] = useState<ChangeItem | null>(null);
  const [diff, setDiff] = useState<ChangeItemDiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validations, setValidations] = useState<ValidationRun[]>([]);
  const [validationCommand, setValidationCommand] = useState("");
  const [validationCwd, setValidationCwd] = useState("");
  const [validationBusy, setValidationBusy] = useState(false);

  useEffect(() => {
    if (sessionId) void loadChangeSets(sessionId);
  }, [sessionId, loadChangeSets]);

  const allItems = useMemo(
    () => changeSets.flatMap((set) => set.items),
    [changeSets],
  );
  const firstItem = allItems[0] ?? null;

  useEffect(() => {
    const preferred = selectedId
      ? allItems.find((item) => item.id === selectedId)
      : null;
    const next = preferred ?? selected ?? firstItem;
    if (next && (
      next.id !== selected?.id
      || next.review_status !== selected?.review_status
      || next.status !== selected?.status
    )) {
      setSelected(next);
      if (next.id !== selected?.id) setReviewNote(next.review_note || "");
      if (sessionId) setSelectedChangeItem(sessionId, next.id);
    } else if (!next && selected) {
      setSelected(null);
    }
  }, [allItems, firstItem, selected, selectedId, sessionId, setSelectedChangeItem]);

  const selectItem = (item: ChangeItem) => {
    setSelected(item);
    setReviewNote(item.review_note || "");
    if (sessionId) setSelectedChangeItem(sessionId, item.id);
  };

  const selectedSet = useMemo(
    () => changeSets.find((set) => set.id === selected?.change_set_id) ?? null,
    [changeSets, selected],
  );

  useEffect(() => {
    if (!selected) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiffLoading(true);
    apiGet<ChangeItemDiffResponse>(`change-items/${encodeURIComponent(selected.id)}/diff`)
      .then((data) => {
        if (!cancelled) setDiff(data);
      })
      .catch(() => {
        if (!cancelled) setDiff(null);
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => { cancelled = true; };
  }, [selected]);

  useEffect(() => {
    if (!selectedSet) {
      setValidations([]);
      return;
    }
    let cancelled = false;
    apiGet<{ validations: ValidationRun[] }>(
      `change-sets/${encodeURIComponent(selectedSet.id)}/validations`,
    )
      .then((data) => { if (!cancelled) setValidations(data.validations || []); })
      .catch(() => { if (!cancelled) setValidations([]); });
    return () => { cancelled = true; };
  }, [selectedSet?.id]);

  const refresh = async () => {
    if (sessionId) await loadChangeSets(sessionId);
  };

  const reviewItem = async (status: "accepted" | "rejected") => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await apiPatch(`change-items/${encodeURIComponent(selected.id)}`, {
        review_status: status,
        review_note: reviewNote || null,
      });
      useLogStore.getState().log("ok", "changes", `${status} ${selected.path || selected.id}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("warn", "changes", `review failed: ${message}`);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  const reviewSet = async (changeSet: ChangeSet, status: "accepted" | "rejected") => {
    setBusy(true);
    setError(null);
    try {
      await apiPost(`change-sets/${encodeURIComponent(changeSet.id)}/review`, {
        review_status: status,
      });
      useLogStore.getState().log("ok", "changes", `${status} set ${changeSet.run_id}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("warn", "changes", `set review failed: ${message}`);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  const revertItem = async () => {
    if (!selected) return;
    if (!window.confirm(lang === "en" ? "Revert this file change?" : "撤销这个文件变更？")) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost(`change-items/${encodeURIComponent(selected.id)}/revert`, {});
      useLogStore.getState().log("ok", "changes", `reverted ${selected.path || selected.id}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("warn", "changes", `revert failed: ${message}`);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  const revertSet = async (changeSet: ChangeSet) => {
    if (!window.confirm(lang === "en" ? "Revert all files in this run?" : "撤销本轮全部文件变更？")) return;
    setBusy(true);
    setError(null);
    try {
      await apiPost(`change-sets/${encodeURIComponent(changeSet.id)}/revert`, {});
      useLogStore.getState().log("ok", "changes", `reverted set ${changeSet.run_id}`);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("warn", "changes", `set revert failed: ${message}`);
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  const runValidation = async () => {
    if (!selectedSet || !validationCommand.trim()) return;
    setValidationBusy(true);
    setError(null);
    try {
      await apiPost(`change-sets/${encodeURIComponent(selectedSet.id)}/validations/run`, {
        command: validationCommand.trim(),
        cwd: validationCwd.trim(),
        timeout_ms: 30000,
      });
      useLogStore.getState().log("ok", "changes", `validation ${validationCommand.trim()}`);
      const data = await apiGet<{ validations: ValidationRun[] }>(
        `change-sets/${encodeURIComponent(selectedSet.id)}/validations`,
      );
      setValidations(data.validations || []);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("warn", "changes", `validation failed: ${message}`);
      setError(message);
    } finally {
      setValidationBusy(false);
    }
  };

  const dirtyCount = workspace ? workspace.dirty.length : 0;
  const untrackedCount = workspace ? workspace.untracked.length : 0;

  return (
    <div className="change-set-panel" id="changeSetPanel">
      <div className="change-workspace-card">
        <div className="change-workspace-title">
          {lang === "en" ? "Workspace baseline" : "工作区基线"}
        </div>
        {workspace ? (
          <>
            <div className="change-workspace-path" title={workspace.root}>{workspace.root}</div>
            <div className="change-workspace-meta">
              {workspace.is_git
                ? `${workspace.branch || "HEAD"} · ${workspace.head?.slice(0, 12) || "—"}`
                : (lang === "en" ? "Not a Git repository" : "非 Git 仓库")}
              {" · "}
              {lang === "en" ? "dirty" : "未提交"} {dirtyCount}
              {" · "}
              {lang === "en" ? "untracked" : "未跟踪"} {untrackedCount}
            </div>
          </>
        ) : (
          <div className="change-empty">{lang === "en" ? "Workspace unavailable" : "工作区信息不可用"}</div>
        )}
      </div>

      {error ? <div className="change-error" role="alert">{error}</div> : null}

      {sessionId ? (
        <WorkspaceGitPanel
          sessionId={sessionId}
          workspaceRoot={workspace?.root ?? null}
          changeSets={changeSets}
        />
      ) : null}

      {loading && !changeSets.length ? (
        <div className="change-empty">{lang === "en" ? "Loading changes…" : "正在加载变更…"}</div>
      ) : !changeSets.length ? (
        <div className="change-empty">
          {lang === "en"
            ? "No observed file changes in this session."
            : "本会话还没有捕获到文件变更。"}
        </div>
      ) : (
        <div className="change-set-list">
          {changeSets.map((set) => (
            <ChangeSetGroup
              key={set.id}
              changeSet={set}
              selectedId={selected?.id ?? null}
              onSelect={selectItem}
              onReviewSet={(status) => void reviewSet(set, status)}
              onRevertSet={() => void revertSet(set)}
              busy={busy}
            />
          ))}
        </div>
      )}

      {selected ? (
        <div className="change-detail">
          <div className="change-detail-heading">
            <strong>{selected.path || selected.tool_name}</strong>
            <span>{statusLabel(selected.status, lang)}</span>
            <span>{reviewLabel(selected.review_status, lang)}</span>
          </div>
          <div className="change-review-controls">
            <input
              type="text"
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
              placeholder={lang === "en" ? "Review note (optional)" : "审阅备注（可选）"}
              maxLength={2000}
            />
            <button type="button" disabled={busy} onClick={() => void reviewItem("accepted")}>
              {lang === "en" ? "Accept" : "接受"}
            </button>
            <button type="button" disabled={busy} onClick={() => void reviewItem("rejected")}>
              {lang === "en" ? "Reject" : "拒绝"}
            </button>
            <button type="button" className="danger" disabled={busy} onClick={() => void revertItem()}>
              {lang === "en" ? "Revert" : "撤销"}
            </button>
          </div>
          {diffLoading ? (
            <div className="change-empty">{lang === "en" ? "Loading diff…" : "正在加载 Diff…"}</div>
          ) : diff ? (
            <ChangeDiffViewer
              item={selected}
              beforeContent={diff.before_content}
              afterContent={diff.after_content}
              available={diff.available}
              reason={diff.reason}
            />
          ) : (
            <div className="change-empty">{lang === "en" ? "Diff unavailable" : "Diff 不可用"}</div>
          )}

          {selectedSet ? (
            <div className="change-verify">
              <div className="change-verify-heading">
                <strong>{lang === "en" ? "Validate" : "验证"}</strong>
                <span>{lang === "en" ? "Allowlisted commands only" : "仅允许测试/构建命令"}</span>
              </div>
              <div className="change-verify-form">
                <input
                  type="text"
                  value={validationCommand}
                  onChange={(e) => setValidationCommand(e.target.value)}
                  placeholder={lang === "en" ? "e.g. python -m pytest tests/foo.py" : "例如 python -m pytest tests/foo.py"}
                />
                <input
                  type="text"
                  value={validationCwd}
                  onChange={(e) => setValidationCwd(e.target.value)}
                  placeholder={lang === "en" ? "cwd (optional)" : "工作目录（可选）"}
                />
                <button type="button" disabled={validationBusy || !validationCommand.trim()} onClick={() => void runValidation()}>
                  {validationBusy ? "…" : (lang === "en" ? "Run" : "运行")}
                </button>
              </div>
              {validations.length ? (
                <div className="change-verify-list">
                  {validations.slice().reverse().map((run) => (
                    <details key={run.id} className={`change-verify-run status-${run.status}`}>
                      <summary>
                        <span className="change-verify-command">{run.command}</span>
                        <span>{statusLabel(run.status, lang)}</span>
                        {run.exit_code !== null ? <span>exit {run.exit_code}</span> : null}
                      </summary>
                      {run.stdout ? <pre>{run.stdout}</pre> : null}
                      {run.stderr ? <pre className="stderr">{run.stderr}</pre> : null}
                    </details>
                  ))}
                </div>
              ) : (
                <div className="change-empty">{lang === "en" ? "No validation runs yet." : "还没有验证记录。"}</div>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default ChangeSetPanel;
