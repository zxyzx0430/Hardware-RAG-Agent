import { useEffect, useRef } from "react";
import { apiGet } from "../../api/client";
import type { OpenFileItem } from "../../types";

export interface ContextMenuPos {
  x: number;
  y: number;
  fileId: string;
}

interface ReadFileResponse {
  name: string;
  path: string;
  content?: string;
  is_text?: boolean;
  data_url?: string;
}

// On mount, re-fetch content for files recovered from localStorage that have
// no content yet. Binary files are skipped (they render via data_url).
export function useRestoreFileContents(
  openFiles: OpenFileItem[],
  setFileContent: (id: string, content: string) => void,
): void {
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const toRestore = openFiles.filter(
      (f) => f.content === undefined && !f.isBuffer && !f.path.startsWith("buffer://"),
    );
    if (toRestore.length === 0) return;
    void Promise.all(
      toRestore.map(async (f) => {
        try {
          const data = await apiGet<ReadFileResponse>(
            `explorer/read?path=${encodeURIComponent(f.path)}`,
          );
          if (data.is_text === false) return;
          setFileContent(f.id, data.content ?? "");
        } catch {
          // File may have been deleted or be unreadable; skip silently.
        }
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

// Pinned tabs stay first (in pinnedIds order), the rest follow `order`.
export function orderedTabs(
  files: OpenFileItem[],
  pinnedIds: string[],
  order: string[],
): OpenFileItem[] {
  const orderIndex = new Map(order.map((id, i) => [id, i]));
  const pinnedSet = new Set(pinnedIds);
  const pinned = pinnedIds
    .map((id) => files.find((f) => f.id === id))
    .filter((f): f is OpenFileItem => f !== undefined);
  const others = files
    .filter((f) => !pinnedSet.has(f.id))
    .sort((a, b) => (orderIndex.get(a.id) ?? 0) - (orderIndex.get(b.id) ?? 0));
  return [...pinned, ...others];
}

// Move dragId to the position of targetId within the order array.
export function reorder(prev: string[], dragId: string, targetId: string): string[] {
  const withoutDrag = prev.filter((id) => id !== dragId);
  const idx = withoutDrag.indexOf(targetId);
  if (idx === -1) return prev;
  const next = [...withoutDrag];
  next.splice(idx, 0, dragId);
  return next;
}
