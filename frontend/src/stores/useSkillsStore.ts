import { create } from "zustand";
import { apiDelete, apiGet, apiPatch, apiPost } from "../api/client";

const GITHUB_IMPORT_TIMEOUT_MS = 70_000;

export interface SkillSource {
  owner: string;
  repo: string;
  commit_sha: string;
  path: string;
}

export interface InstalledSkill {
  id: string;
  name: string;
  description: string;
  content: string;
  enabled: boolean;
  format?: string;
  compatibility_status?: unknown;
  issues?: unknown;
  content_hash?: string;
  source?: SkillSource | null;
  locally_modified?: boolean;
}

export interface SkillCandidate {
  path: string;
  name: string;
  description: string;
  compatibility_status?: unknown;
  issues?: unknown;
}

export interface GitHubSkillPreview {
  source: SkillSource;
  candidates: SkillCandidate[];
}

export interface SkillDetail extends InstalledSkill {
  resources?: Array<{ path: string; size: number; supported: boolean }>;
}

export type SkillsMode = "off" | "auto" | "manual";

export interface ChatSkillsSelection {
  mode: SkillsMode;
  skillIds: string[];
}

interface SkillsState {
  skills: InstalledSkill[];
  loading: boolean;
  loaded: boolean;
  error: string | null;
  preview: GitHubSkillPreview | null;
  previewLoading: boolean;
  importing: boolean;
  chatSkillSelections: Record<string, ChatSkillsSelection>;
  fetchSkills: () => Promise<boolean>;
  getSkill: (id: string) => Promise<SkillDetail | null>;
  setSkillEnabled: (id: string, enabled: boolean) => Promise<boolean>;
  updateSkillContent: (id: string, content: string) => Promise<boolean>;
  deleteSkill: (id: string) => Promise<boolean>;
  previewGitHubImport: (url: string, ref?: string, path?: string) => Promise<boolean>;
  importGitHubSkills: (paths: string[]) => Promise<boolean>;
  setChatSkillSelection: (sessionId: string, selection: ChatSkillsSelection) => void;
  clearPreview: () => void;
  clearError: () => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useSkillsStore = create<SkillsState>((set, get) => ({
  skills: [],
  loading: false,
  loaded: false,
  error: null,
  preview: null,
  previewLoading: false,
  importing: false,
  chatSkillSelections: {},

  fetchSkills: async () => {
    set({ loading: true, error: null });
    try {
      const data = await apiGet<{ skills: InstalledSkill[] }>("skills");
      set({ skills: Array.isArray(data?.skills) ? data.skills : [], loading: false, loaded: true });
      return true;
    } catch (error) {
      set({ loading: false, error: errorMessage(error) });
      return false;
    }
  },

  getSkill: async (id) => {
    set({ error: null });
    try {
      return await apiGet<SkillDetail>(`skills/${encodeURIComponent(id)}`);
    } catch (error) {
      set({ error: errorMessage(error) });
      return null;
    }
  },

  setSkillEnabled: async (id, enabled) => {
    set({ error: null });
    try {
      await apiPatch(`skills/${encodeURIComponent(id)}`, { enabled });
      await get().fetchSkills();
      return true;
    } catch (error) {
      set({ error: errorMessage(error) });
      return false;
    }
  },

  updateSkillContent: async (id, content) => {
    set({ error: null });
    try {
      await apiPatch(`skills/${encodeURIComponent(id)}`, { content });
      await get().fetchSkills();
      return true;
    } catch (error) {
      set({ error: errorMessage(error) });
      return false;
    }
  },

  deleteSkill: async (id) => {
    if (!id) return false;
    set({ error: null });
    try {
      await apiDelete(`skills/${encodeURIComponent(id)}`);
      set((state) => ({
        skills: state.skills.filter((skill) => skill.id !== id),
        chatSkillSelections: Object.fromEntries(
          Object.entries(state.chatSkillSelections).map(([sessionId, selection]) => [
            sessionId,
            { ...selection, skillIds: selection.skillIds.filter((skillId) => skillId !== id) },
          ])
        ),
      }));
      return true;
    } catch (error) {
      set({ error: errorMessage(error) });
      return false;
    }
  },

  previewGitHubImport: async (url, ref, path) => {
    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      set({ error: "请先输入 GitHub 仓库或技能目录链接。" });
      return false;
    }
    const body = {
      url: trimmedUrl,
      ...(ref?.trim() ? { ref: ref.trim() } : {}),
      ...(path?.trim() ? { path: path.trim() } : {}),
    };
    set({ previewLoading: true, error: null, preview: null });
    try {
      const preview = await apiPost<GitHubSkillPreview>("skills/github/preview", body, GITHUB_IMPORT_TIMEOUT_MS);
      set({ preview, previewLoading: false });
      return true;
    } catch (error) {
      set({ previewLoading: false, error: errorMessage(error) });
      return false;
    }
  },

  importGitHubSkills: async (paths) => {
    const preview = get().preview;
    const allowedPaths = new Set(preview?.candidates.map((candidate) => candidate.path) ?? []);
    const selectedPaths = [...new Set(paths)].filter((path) => allowedPaths.has(path));
    if (!preview || selectedPaths.length === 0) {
      set({ error: "请先预览并选择至少一个技能。" });
      return false;
    }
    set({ importing: true, error: null });
    try {
      const data = await apiPost<{ skills: InstalledSkill[] }>("skills/github/import", {
        source: preview.source,
        paths: selectedPaths,
      }, GITHUB_IMPORT_TIMEOUT_MS);
      set((state) => ({
        skills: Array.isArray(data?.skills) ? mergeSkills(state.skills, data.skills) : state.skills,
        preview: null,
        importing: false,
      }));
      void get().fetchSkills();
      return true;
    } catch (error) {
      set({ importing: false, error: errorMessage(error) });
      return false;
    }
  },

  setChatSkillSelection: (sessionId, selection) => {
    if (!sessionId) return;
    const skillIds = [...new Set(selection.skillIds.filter((id) => typeof id === "string" && id))].slice(0, 3);
    set((state) => ({
      chatSkillSelections: {
        ...state.chatSkillSelections,
        [sessionId]: { mode: selection.mode, skillIds },
      },
    }));
  },

  clearPreview: () => set({ preview: null }),
  clearError: () => set({ error: null }),
}));

function mergeSkills(current: InstalledSkill[], imported: InstalledSkill[]): InstalledSkill[] {
  const byId = new Map(current.map((skill) => [skill.id, skill]));
  for (const skill of imported) byId.set(skill.id, skill);
  return [...byId.values()];
}
