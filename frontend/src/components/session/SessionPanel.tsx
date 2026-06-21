import { useState, useCallback, useEffect } from "react";
import { useSessionStore, getSessionGroup, formatSessionTime, formatCreateDate } from "../../stores/useSessionStore";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import { ContextMenu } from "../shared/ContextMenu";
import type { MenuItem } from "../shared/ContextMenu";
import { useI18n } from "../../i18n";

const COLORS = ["#3b63d4", "#22a37c", "#c4793b", "#8b5cf6", "#e05252", "#0ea5e9"];

function getProjectColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return COLORS[h % COLORS.length];
}

interface ContextMenuState {
  x: number;
  y: number;
  sessionId: string;
}

export function SessionPanel() {
  const { t } = useI18n();
  const {
    sessions, activeProject, searchQuery,
    setActiveProject, setSearchQuery, newSession,
    deleteSession, pinSession, renameSession, moveSessionToProject,
    createProject, deleteProject, createProjectInputVisible, setCreateProjectInputVisible,
    initSessions,
  } = useSessionStore();
  const { activeSession, setActiveSession, sessionGroupsCollapsed, toggleSessionGroupCollapsed } = useAppStore();
  const { setActiveSession: setChatActiveSession } = useChatStore();

  // 组件挂载时从 API 加载 sessions
  useEffect(() => {
    initSessions();
  }, [initSessions]);

  const [ctxMenu, setCtxMenu] = useState<ContextMenuState | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [newProjectName, setNewProjectName] = useState("");

  const projects = [...new Set(sessions.map((s) => s.project).filter(Boolean))];
  const filtered = sessions.filter((s) => {
    const q = searchQuery.toLowerCase();
    if (q && !s.title.toLowerCase().includes(q) && !s.preview.toLowerCase().includes(q)) return false;
    if (activeProject !== "all" && s.project !== activeProject) return false;
    return true;
  });

  // 动态计算分组
  const groupOrder: Record<string, number> = { today: 0, yesterday: 1, thisWeek: 2, earlier: 3 };
  const groupLabels: Record<string, string> = { today: t('today'), yesterday: t('yesterday'), thisWeek: t('thisWeek'), earlier: t('earlier') };
  const pinned = filtered.filter((s) => s.pinned);
  const unpinned = filtered.filter((s) => !s.pinned);
  const grouped: Record<string, typeof sessions> = {};
  unpinned.forEach((s) => {
    const g = getSessionGroup(s.createdAt);
    if (!grouped[g]) grouped[g] = [];
    grouped[g].push(s);
  });

  const selectSession = (id: string) => {
    setActiveSession(id);
    setChatActiveSession(id);
  };

  const closeCtxMenu = useCallback(() => setCtxMenu(null), []);

  const showContextMenu = (e: React.MouseEvent, sessionId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, sessionId });
  };

  const startRename = (sessionId: string) => {
    const s = sessions.find((x) => x.id === sessionId);
    setRenamingId(sessionId);
    setRenameValue(s?.title || "");
  };

  const commitRename = () => {
    if (renamingId && renameValue.trim()) {
      renameSession(renamingId, renameValue.trim());
    }
    setRenamingId(null);
    setRenameValue("");
  };

  const handleCreateProject = () => {
    if (newProjectName.trim()) {
      createProject(newProjectName.trim());
      setNewProjectName("");
    }
  };

  const getContextMenuItems = (): MenuItem[] => {
    if (!ctxMenu) return [];
    const s = sessions.find((x) => x.id === ctxMenu.sessionId);
    if (!s) return [];
    return [
      {
        label: t('rename'),
        onClick: () => startRename(s.id),
      },
      {
        label: s.pinned ? t('unpinSession') : t('pinSession'),
        onClick: () => {
          if (!s.pinned) {
            const pinnedCount = sessions.filter((x) => x.pinned).length;
            if (pinnedCount >= 5) {
              alert(t('maxPinAlert'));
              return;
            }
          }
          pinSession(s.id);
        },
      },
      { label: "---", onClick: () => {} },
      {
        label: t('moveToProject'),
        onClick: () => {},
        children: [
          ...(s.project ? [{ label: t('noProject') ?? '无项目', onClick: () => moveSessionToProject(s.id, "") }] : []),
          ...projects
            .filter((p) => p !== s.project)
            .map((p) => ({
              label: p,
              onClick: () => moveSessionToProject(s.id, p),
            })),
          { label: "---", onClick: () => {} },
          {
            label: t('newProjectBtn'),
            onClick: () => {
              const name = window.prompt(t('newProjectPlaceholder') || "输入项目名称");
              if (name && name.trim()) {
                createProject(name.trim());
                // Wait for store update, then move
                setTimeout(() => moveSessionToProject(s.id, name.trim()), 50);
              }
            },
          },
        ],
      },
      { label: "---", onClick: () => {} },
      {
        label: t('delete'),
        danger: true,
        onClick: () => {
          if (window.confirm(t('deleteSessionConfirm'))) {
            deleteSession(s.id);
          }
        },
      },
    ];
  };

  const renderSessionItem = (s: typeof sessions[0], extraClass = "") => {
    const isActive = s.id === activeSession;
    const isRenaming = renamingId === s.id;

    return (
      <div
        key={s.id}
        className={`session-item${extraClass ? ` ${extraClass}` : ""}${isActive ? " active" : ""}`}
        onClick={() => { if (!isRenaming) selectSession(s.id); }}
        onContextMenu={(e) => showContextMenu(e, s.id)}
      >
        <div className="session-item-row">
          <span className="session-item-title">
            {s.project && <span className="project-dot" style={{ background: getProjectColor(s.project) }} />}
            {isRenaming ? (
              <input
                className="rename-input"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={commitRename}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") { setRenamingId(null); setRenameValue(""); }
                }}
                onClick={(e) => e.stopPropagation()}
                autoFocus
            />) : (
              s.title
            )}
          </span>
          <button
            className="session-dot-btn"
            onClick={(e) => {
              e.stopPropagation();
              showContextMenu(e, s.id);
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="5" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="12" cy="19" r="2" />
            </svg>
          </button>
        </div>
        <div className="session-item-row2">
          <span className="session-item-time">{formatSessionTime(s.createdAt)}</span>
          <span className="session-item-meta">{s.msgCount} {t('msgCount')} · {formatCreateDate(s.createdAt)}</span>
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="session-header">
        <div className="session-header-top">
          <span>{t('sessions')}</span>
          <button className="btn-new" onClick={() => newSession()}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            {t('newBtn')}
          </button>
        </div>
        <div className="search-wrap">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input type="text" placeholder={t('searchSessions')} value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
        </div>
      </div>

      <div className="project-chips" style={{ borderBottom: "1px solid var(--border)" }}>
        <button className={`project-chip${activeProject === "all" ? " active" : ""}`} onClick={() => setActiveProject("all")}>{t('all')}</button>
        {projects.map((p) => (
          <button key={p} className={`project-chip${activeProject === p ? " active" : ""}`} onClick={() => setActiveProject(p)}>
            <span className="project-chip-dot" style={{ background: getProjectColor(p) }} />
            {p}
            <span
              style={{ fontSize: 12, lineHeight: 1 }}
              onClick={(e) => { e.stopPropagation(); deleteProject(p); }}
            >
              ×
            </span>
          </button>
        ))}
        {createProjectInputVisible ? (
          <input
            className="project-new-input"
            placeholder={t('newProjectPlaceholder')}
            value={newProjectName}
            onChange={(e) => setNewProjectName(e.target.value)}
            onBlur={() => { if (!newProjectName.trim()) setCreateProjectInputVisible(false); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateProject();
              if (e.key === "Escape") { setCreateProjectInputVisible(false); setNewProjectName(""); }
            }}
            autoFocus
          />
        ) : (
          <button className="project-chip" style={{ opacity: 0.8 }} onClick={() => setCreateProjectInputVisible(true)}>{t('newProjectBtn')}</button>
        )}
      </div>

      <div className="session-scroll">
        {pinned.length > 0 && (
          <div>
            <div className="session-group-header" style={{ cursor: "default" }}>
              <span>{t('pinnedLabel')}</span>
            </div>
            {pinned.map((s) => renderSessionItem(s, "pinned"))}
          </div>
        )}

        {Object.entries(grouped).sort(([a], [b]) => (groupOrder[a] ?? 9) - (groupOrder[b] ?? 9)).map(([g, items]) => {
          const collapsed = !!sessionGroupsCollapsed[g];
          return (
            <div key={g}>
              <div
                className="session-group-header"
                style={{ cursor: "pointer" }}
                onClick={() => toggleSessionGroupCollapsed(g)}
              >
                <span>{groupLabels[g] || g}</span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  style={{ transition: "transform 0.15s", transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)" }}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </div>
              {!collapsed && items.map((s) => renderSessionItem(s))}
            </div>
          );
        })}
      </div>

      {ctxMenu && (
        <ContextMenu
          items={getContextMenuItems()}
          x={ctxMenu.x}
          y={ctxMenu.y}
          onClose={closeCtxMenu}
        />
      )}
    </>
  );
}
