import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useSkillsStore, type GitHubSkillPreview, type InstalledSkill, type SkillDetail } from "../../stores/useSkillsStore";

type SkillDialog = { skill: SkillDetail; mode: "view" | "edit" | "enable" };
type SkillDeleteTarget = { id: string; name: string };

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function issueLines(issues: unknown): string[] {
  if (Array.isArray(issues)) {
    return issues.map((issue) => {
      if (!issue || typeof issue !== "object" || Array.isArray(issue)) return displayValue(issue);
      const record = issue as Record<string, unknown>;
      const message = displayValue(record.message);
      const details = [record.severity, record.code].map(displayValue).filter(Boolean).join(" · ");
      return message ? `${message}${details ? ` (${details})` : ""}` : displayValue(issue);
    }).filter(Boolean);
  }
  const value = displayValue(issues);
  return value ? [value] : [];
}

function compatibilityLabel(status: unknown, isChinese: boolean): string {
  const value = displayValue(status);
  const labels: Record<string, [string, string]> = {
    supported: ["支持", "Supported"],
    partial: ["部分支持", "Partially supported"],
    review_required: ["需审核", "Review required"],
  };
  return labels[value]?.[isChinese ? 0 : 1] ?? (value || (isChinese ? "未提供" : "not provided"));
}

export function SkillsManager({ lang }: { lang: string }) {
  const isChinese = lang === "zh";
  const skills = useSkillsStore((state) => state.skills);
  const loading = useSkillsStore((state) => state.loading);
  const error = useSkillsStore((state) => state.error);
  const preview = useSkillsStore((state) => state.preview);
  const previewLoading = useSkillsStore((state) => state.previewLoading);
  const importing = useSkillsStore((state) => state.importing);
  const fetchSkills = useSkillsStore((state) => state.fetchSkills);
  const getSkill = useSkillsStore((state) => state.getSkill);
  const setSkillEnabled = useSkillsStore((state) => state.setSkillEnabled);
  const updateSkillContent = useSkillsStore((state) => state.updateSkillContent);
  const deleteSkill = useSkillsStore((state) => state.deleteSkill);
  const previewGitHubImport = useSkillsStore((state) => state.previewGitHubImport);
  const importGitHubSkills = useSkillsStore((state) => state.importGitHubSkills);
  const clearPreview = useSkillsStore((state) => state.clearPreview);
  const clearError = useSkillsStore((state) => state.clearError);

  const [importOpen, setImportOpen] = useState(false);
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [dialog, setDialog] = useState<SkillDialog | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SkillDeleteTarget | null>(null);
  const [dialogLoading, setDialogLoading] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [savingSkill, setSavingSkill] = useState(false);
  const [enablingSkill, setEnablingSkill] = useState(false);
  const [deletingSkillId, setDeletingSkillId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    void fetchSkills();
  }, [fetchSkills]);

  const openSkillDialog = async (skill: InstalledSkill, mode: SkillDialog["mode"]) => {
    clearError();
    setFeedback(null);
    setDialogLoading(skill.id);
    const detail = await getSkill(skill.id);
    setDialogLoading(null);
    if (!detail) return;
    setEditContent(detail.content || "");
    setDialog({ skill: detail, mode });
  };

  const toggleSkill = async (skill: InstalledSkill) => {
    clearError();
    setFeedback(null);
    if (skill.enabled) {
      if (await setSkillEnabled(skill.id, false)) {
        setFeedback(isChinese ? "技能已停用。" : "Skill disabled.");
      }
      return;
    }
    await openSkillDialog(skill, "enable");
  };

  const confirmEnable = async () => {
    if (!dialog || dialog.mode !== "enable") return;
    if (displayValue(dialog.skill.compatibility_status) === "review_required") return;
    setEnablingSkill(true);
    try {
      if (await setSkillEnabled(dialog.skill.id, true)) {
        setDialog(null);
        setFeedback(isChinese ? "技能已启用。" : "Skill enabled.");
      }
    } finally {
      setEnablingSkill(false);
    }
  };

  const saveEdit = async () => {
    if (!dialog || dialog.mode !== "edit") return;
    setSavingSkill(true);
    if (await updateSkillContent(dialog.skill.id, editContent)) {
      setDialog(null);
      setFeedback(isChinese
        ? "技能已保存；修改外部来源技能后需重新确认启用。"
        : "Skill saved. Edited imported skills require a new enable confirmation.");
    }
    setSavingSkill(false);
  };

  const confirmDelete = async () => {
    if (!deleteTarget || deletingSkillId) return;
    const target = deleteTarget;
    setDeletingSkillId(target.id);
    try {
      if (await deleteSkill(target.id)) {
        setDeleteTarget(null);
        if (dialog?.skill.id === target.id) setDialog(null);
        setFeedback(isChinese ? `技能“${target.name}”已删除。` : `Skill “${target.name}” was deleted.`);
      }
    } finally {
      setDeletingSkillId(null);
    }
  };

  const previewGitHub = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSelectedPaths([]);
    setFeedback(null);
    await previewGitHubImport(sourceUrl, sourceRef || undefined, sourcePath || undefined);
  };

  const confirmImport = async () => {
    if (await importGitHubSkills(selectedPaths)) {
      setImportOpen(false);
      setSourceUrl("");
      setSourceRef("");
      setSourcePath("");
      setSelectedPaths([]);
      setFeedback(isChinese
        ? "所选技能已导入，默认保持停用。"
        : "Selected skills were imported and remain disabled by default.");
    }
  };

  const toggleCandidate = (path: string) => {
    setSelectedPaths((current) => current.includes(path)
      ? current.filter((item) => item !== path)
      : [...current, path]);
  };

  return (
    <div className="settings-section">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h3>{isChinese ? "Agent Skills" : "Agent Skills"}</h3>
          <p style={{ fontSize: 13, color: "var(--muted-fg)", marginBottom: 12 }}>
            {isChinese
              ? "管理 Markdown 技能。导入不会自动启用；启用前请检查来源、内容和兼容提示。"
              : "Manage Markdown skills. Imports stay disabled until you review their source, content, and compatibility notes."}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button type="button" className="verify-btn" onClick={() => void fetchSkills()} disabled={loading}>
            {isChinese ? "刷新" : "Refresh"}
          </button>
        <button type="button" className="verify-btn primary" onClick={() => { setImportOpen((open) => !open); clearPreview(); clearError(); }} disabled={previewLoading || importing}>
            {isChinese ? "从 GitHub 导入" : "Import from GitHub"}
          </button>
        </div>
      </div>

      {error && <div role="alert" style={{ color: "var(--danger)", fontSize: 12, margin: "8px 0" }}>{error}</div>}
      {feedback && <div role="status" style={{ color: "var(--success)", fontSize: 12, margin: "8px 0" }}>{feedback}</div>}

      {importOpen && (
        <form onSubmit={(event) => void previewGitHub(event)} style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, margin: "12px 0" }}>
          <h4 style={{ marginBottom: 8 }}>{isChinese ? "预览公共 GitHub 技能" : "Preview public GitHub skills"}</h4>
          <label className="field-label" htmlFor="skillGithubUrl">{isChinese ? "仓库或技能目录链接" : "Repository or skill directory URL"}</label>
          <input id="skillGithubUrl" className="form-input" type="url" required value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} />
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 8 }}>
            <div>
              <label className="field-label" htmlFor="skillGithubRef">{isChinese ? "分支、标签或提交（可选）" : "Branch, tag, or commit (optional)"}</label>
              <input id="skillGithubRef" className="form-input" value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} />
            </div>
            <div>
              <label className="field-label" htmlFor="skillGithubPath">{isChinese ? "子目录（可选）" : "Subdirectory (optional)"}</label>
              <input id="skillGithubPath" className="form-input" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="submit" className="verify-btn primary" disabled={previewLoading || !sourceUrl.trim()}>
              {previewLoading ? (isChinese ? "正在预览…" : "Previewing…") : (isChinese ? "预览候选技能" : "Preview candidates")}
            </button>
            <button type="button" className="verify-btn" onClick={() => { setImportOpen(false); clearPreview(); }}>
              {isChinese ? "取消" : "Cancel"}
            </button>
          </div>
        </form>
      )}

      {preview && (
        <GitHubPreview
          preview={preview}
          selectedPaths={selectedPaths}
          isChinese={isChinese}
          importing={importing}
          onToggle={toggleCandidate}
          onImport={() => void confirmImport()}
          onCancel={() => { clearPreview(); setSelectedPaths([]); }}
        />
      )}

      {loading && skills.length === 0 ? (
        <p role="status" style={{ color: "var(--muted-fg)", fontSize: 13 }}>{isChinese ? "正在加载技能…" : "Loading skills…"}</p>
      ) : skills.length === 0 ? (
        <div style={{ padding: 20, color: "var(--muted-fg)", fontSize: 13, textAlign: "center" }}>
          {isChinese ? "还没有 Markdown Skill。你可以从公共 GitHub 仓库预览并导入。" : "No Markdown skills yet. Preview and import one from a public GitHub repository."}
        </div>
      ) : (
        <div aria-label={isChinese ? "已安装技能" : "Installed skills"}>
          {skills.map((skill) => (
            <div key={skill.id} style={{ padding: "12px 0", borderBottom: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <strong style={{ fontSize: 13 }}>{skill.name}</strong>
                  {skill.locally_modified && <span style={{ marginLeft: 8, color: "var(--warning)", fontSize: 11 }}>{isChinese ? "本地已修改" : "Locally modified"}</span>}
                  <p style={{ fontSize: 12, color: "var(--muted-fg)", marginTop: 3 }}>{skill.description}</p>
                  <p style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 3 }}>
                    {isChinese ? "兼容状态：" : "Compatibility: "}{compatibilityLabel(skill.compatibility_status, isChinese)}
                  </p>
                  {issueLines(skill.issues).map((issue, index) => (
                    <p key={`${skill.id}-issue-${index}`} style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 2 }}>• {issue}</p>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button type="button" className="verify-btn" onClick={() => void openSkillDialog(skill, "view")} disabled={dialogLoading === skill.id || deletingSkillId !== null}>
                    {isChinese ? "查看" : "View"}
                  </button>
                  <button type="button" className="verify-btn" onClick={() => void openSkillDialog(skill, "edit")} disabled={dialogLoading === skill.id || deletingSkillId !== null}>
                    {isChinese ? "编辑" : "Edit"}
                  </button>
                  <button
                    type="button"
                    className={`mini-toggle ${skill.enabled ? "on" : "off"}`}
                    aria-label={`${skill.enabled ? (isChinese ? "停用" : "Disable") : (isChinese ? "检查并启用" : "Review and enable")}: ${skill.name}`}
                    aria-pressed={skill.enabled}
                    onClick={() => void toggleSkill(skill)}
                    disabled={dialogLoading === skill.id || deletingSkillId !== null}
                  >
                    <span className="mini-toggle-knob"></span>
                  </button>
                  <button
                    type="button"
                    className="verify-btn danger"
                    aria-label={`${isChinese ? "删除技能" : "Delete skill"}: ${skill.name}`}
                    onClick={() => { clearError(); setFeedback(null); setDeleteTarget({ id: skill.id, name: skill.name }); }}
                    disabled={dialogLoading === skill.id || deletingSkillId !== null}
                  >
                    {isChinese ? "删除" : "Delete"}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {dialog && (
        <SkillDialogView
          dialog={dialog}
          editContent={editContent}
          isChinese={isChinese}
          saving={savingSkill}
          onClose={() => setDialog(null)}
          onEdit={(value) => setEditContent(value)}
          onSave={() => void saveEdit()}
          onEnable={() => void confirmEnable()}
          enabling={enablingSkill}
        />
      )}

      {deleteTarget && (
        <div role="alertdialog" aria-modal="true" aria-labelledby="deleteSkillTitle" style={{ position: "fixed", inset: 0, zIndex: 1100, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", padding: 20 }}>
          <div style={{ width: "min(440px, 100%)", padding: 18, borderRadius: 10, border: "1px solid var(--border)", background: "var(--bg)" }}>
            <h3 id="deleteSkillTitle" style={{ marginBottom: 8 }}>{isChinese ? "确认删除 Skill" : "Delete this Skill?"}</h3>
            <p style={{ fontSize: 13 }}>{isChinese ? "将从本机技能库删除：" : "This will delete the locally installed skill:"} <strong>{deleteTarget.name}</strong></p>
            <p style={{ fontSize: 11, color: "var(--muted-fg)", overflowWrap: "anywhere" }}>
              {isChinese ? "技能 ID：" : "Skill ID: "}<code>{deleteTarget.id}</code>
            </p>
            <p style={{ fontSize: 12, color: "var(--muted-fg)" }}>
              {isChinese ? "此操作不可撤销。" : "This action cannot be undone."}
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
              <button type="button" className="verify-btn" onClick={() => setDeleteTarget(null)} disabled={deletingSkillId !== null}>
                {isChinese ? "取消删除" : "Cancel"}
              </button>
              <button type="button" className="verify-btn danger" onClick={() => void confirmDelete()} disabled={deletingSkillId !== null}>
                {deletingSkillId === deleteTarget.id
                  ? (isChinese ? "正在删除…" : "Deleting…")
                  : (isChinese ? "确认删除" : "Confirm delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function GitHubPreview({
  preview,
  selectedPaths,
  isChinese,
  importing,
  onToggle,
  onImport,
  onCancel,
}: {
  preview: GitHubSkillPreview;
  selectedPaths: string[];
  isChinese: boolean;
  importing: boolean;
  onToggle: (path: string) => void;
  onImport: () => void;
  onCancel: () => void;
}) {
  return (
    <section aria-label={isChinese ? "GitHub 技能预览" : "GitHub skill preview"} style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 8, margin: "12px 0" }}>
      <h4>{isChinese ? "预览结果" : "Preview result"}</h4>
      <p style={{ fontSize: 11, color: "var(--muted-fg)", overflowWrap: "anywhere" }}>
        {preview.source.owner}/{preview.source.repo} · {preview.source.commit_sha}
        {preview.source.path ? ` · ${preview.source.path}` : ""}
      </p>
      <p style={{ fontSize: 12, color: "var(--muted-fg)", margin: "8px 0" }}>
        {isChinese ? "候选默认不选；只会安装你勾选的项目。" : "No candidates are selected by default; only checked skills will be installed."}
      </p>
      {preview.candidates.map((candidate) => (
        <label key={candidate.path} style={{ display: "flex", alignItems: "flex-start", gap: 8, padding: "8px 0", borderTop: "1px solid var(--border)", cursor: "pointer" }}>
          <input
            type="checkbox"
            aria-label={`${isChinese ? "选择" : "Select"}: ${candidate.name}`}
            checked={selectedPaths.includes(candidate.path)}
            disabled={importing}
            onChange={() => onToggle(candidate.path)}
          />
          <span>
            <strong style={{ fontSize: 13 }}>{candidate.name}</strong>
            <span style={{ marginLeft: 8, color: "var(--muted-fg)", fontSize: 11 }}>{candidate.path}</span>
            <span style={{ display: "block", fontSize: 12, color: "var(--muted-fg)", marginTop: 2 }}>{candidate.description}</span>
            <span style={{ display: "block", fontSize: 11, color: "var(--muted-fg)", marginTop: 2 }}>
              {isChinese ? "兼容状态：" : "Compatibility: "}{compatibilityLabel(candidate.compatibility_status, isChinese)}
            </span>
            {issueLines(candidate.issues).map((issue, index) => (
              <span key={`${candidate.path}-issue-${index}`} style={{ display: "block", fontSize: 11, color: "var(--muted-fg)" }}>• {issue}</span>
            ))}
          </span>
        </label>
      ))}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button type="button" className="verify-btn primary" onClick={onImport} disabled={selectedPaths.length === 0 || importing}>
          {importing ? (isChinese ? "正在导入…" : "Importing…") : (isChinese ? `安装选中技能 (${selectedPaths.length})` : `Install selected (${selectedPaths.length})`)}
        </button>
        <button type="button" className="verify-btn" onClick={onCancel} disabled={importing}>{isChinese ? "取消预览" : "Cancel preview"}</button>
      </div>
    </section>
  );
}

function SkillDialogView({
  dialog,
  editContent,
  isChinese,
  saving,
  onClose,
  onEdit,
  onSave,
  onEnable,
  enabling,
}: {
  dialog: SkillDialog;
  editContent: string;
  isChinese: boolean;
  saving: boolean;
  onClose: () => void;
  onEdit: (content: string) => void;
  onSave: () => void;
  onEnable: () => void;
  enabling: boolean;
}) {
  const { skill, mode } = dialog;
  const issues = issueLines(skill.issues);
  const reviewRequired = displayValue(skill.compatibility_status) === "review_required";
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="skillDialogTitle" style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", padding: 20 }}>
      <div style={{ width: "min(760px, 100%)", maxHeight: "90vh", overflow: "auto", padding: 18, borderRadius: 10, border: "1px solid var(--border)", background: "var(--bg)" }}>
        <h3 id="skillDialogTitle" style={{ marginBottom: 8 }}>
          {mode === "enable" ? (isChinese ? "启用前检查" : "Review before enabling") : mode === "edit" ? (isChinese ? "编辑技能" : "Edit skill") : (isChinese ? "技能内容" : "Skill content")}: {skill.name}
        </h3>
        <p style={{ fontSize: 12, color: "var(--muted-fg)" }}>{skill.description}</p>
        <p style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 6 }}>
          {isChinese ? "兼容状态：" : "Compatibility: "}{compatibilityLabel(skill.compatibility_status, isChinese)}
        </p>
        {skill.source && (
          <p style={{ fontSize: 11, color: "var(--muted-fg)", overflowWrap: "anywhere" }}>
            {skill.source.owner}/{skill.source.repo} · {skill.source.commit_sha} · {skill.source.path || "."}
          </p>
        )}
        {issues.length > 0 && (
          <ul style={{ fontSize: 12, color: "var(--muted-fg)", paddingLeft: 20 }}>
            {issues.map((issue, index) => <li key={`issue-${index}`}>{issue}</li>)}
          </ul>
        )}
        {mode === "edit" ? (
          <textarea className="settings-textarea" rows={14} value={editContent} onChange={(event) => onEdit(event.target.value)} disabled={saving} aria-label={isChinese ? "技能 Markdown 内容" : "Skill Markdown content"} />
        ) : (
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "48vh", overflow: "auto", padding: 12, borderRadius: 6, background: "var(--thinking-bg)", fontSize: 12 }}>{skill.content}</pre>
        )}
        {Array.isArray(skill.resources) && skill.resources.length > 0 && (
          <details style={{ marginTop: 10 }}>
            <summary>{isChinese ? `附加资料 (${skill.resources.length})` : `Additional resources (${skill.resources.length})`}</summary>
            <ul style={{ fontSize: 12, color: "var(--muted-fg)", paddingLeft: 20 }}>
              {skill.resources.map((resource) => (
                <li key={resource.path}>
                  {resource.path} · {resource.size} bytes · {resource.supported
                    ? (isChinese ? "支持读取" : "Readable format")
                    : (isChinese ? "暂不支持读取" : "Unsupported format")}
                </li>
              ))}
            </ul>
          </details>
        )}
        {mode === "enable" && reviewRequired && (
          <p role="alert" style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>
            {isChinese ? "此技能需要审核，修复兼容问题并重新预览后才能启用。" : "This skill requires review. Resolve its compatibility issues and preview it again before enabling."}
          </p>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button type="button" className="verify-btn" onClick={onClose} disabled={saving || enabling}>{isChinese ? "关闭" : "Close"}</button>
          {mode === "edit" && (
            <button type="button" className="verify-btn primary" onClick={onSave} disabled={saving}>
              {saving ? (isChinese ? "正在保存…" : "Saving…") : (isChinese ? "保存修改" : "Save changes")}
            </button>
          )}
          {mode === "enable" && (
            <button type="button" className="verify-btn primary" onClick={onEnable} disabled={enabling || reviewRequired}>
              {enabling ? (isChinese ? "正在启用…" : "Enabling…") : (isChinese ? "确认启用" : "Confirm enable")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
