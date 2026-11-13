export interface WorkspaceContext {
  root: string;
  is_git: boolean;
  branch: string | null;
  head: string | null;
  dirty: string[];
  untracked: string[];
}

export type ChangeReviewStatus = "pending" | "accepted" | "rejected";
export type ChangeItemStatus = "observed" | "unobserved" | "conflict" | "reverted";

export interface ChangeDiffStats {
  additions?: number;
  deletions?: number;
  changed?: boolean;
}

export interface ChangeItem {
  id: string;
  change_set_id: string;
  call_id: string | null;
  tool_name: string;
  operation: string;
  path: string;
  before_sha256: string | null;
  after_sha256: string | null;
  before_blob_ref: string | null;
  after_blob_ref: string | null;
  diff_stats: ChangeDiffStats;
  risk: string;
  status: ChangeItemStatus;
  review_status: ChangeReviewStatus;
  review_note: string | null;
  decided_at: string | null;
  plan_step_id: string | null;
  plan_step_index: number | null;
  created_at: string;
}

export interface ChangeSet {
  id: string;
  session_id: string;
  run_id: string;
  plan_id: string | null;
  plan_version: number | null;
  workspace_root: string;
  status: string;
  base_ref: string | null;
  base_sha: string | null;
  summary: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  items: ChangeItem[];
}

export interface ChangeSetsResponse {
  workspace: WorkspaceContext;
  change_sets: ChangeSet[];
}

export interface ChangeItemDiffResponse {
  item: ChangeItem;
  before_content: string | null;
  after_content: string | null;
  available: boolean;
  reason: string | null;
}
