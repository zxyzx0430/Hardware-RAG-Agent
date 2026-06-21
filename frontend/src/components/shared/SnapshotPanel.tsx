import { useState, useEffect, useCallback } from "react";
import type { Message, ContentPart } from "../../types/session";
import { useChatStore } from "../../stores/useChatStore";
import { useAppStore } from "../../stores/useAppStore";
import { useKnowledgeStore } from "../../stores/useKnowledgeStore";

interface Snapshot {
  name: string;
  time: string;
  messages: Message[];
  model: string;
  kbConfig?: { id: string; enabled: boolean }[];
}

const STORAGE_KEY = "hwrag_snapshots";

function loadSnapshots(): Snapshot[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveSnapshots(snapshots: Snapshot[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshots));
  } catch {
    // storage full or unavailable
  }
}

interface DiffLine {
  type: "add" | "remove" | "change" | "same";
  oldContent?: string;
  newContent?: string;
  role?: string;
}

/** Extract plain text from Message content (string | ContentPart[]) */
function contentToText(content: string | ContentPart[]): string {
  if (typeof content === "string") return content;
  return content.map((p) => ("text" in p ? p.text : "")).join("");
}

function computeDiff(oldMsgs: Message[], newMsgs: Message[]): DiffLine[] {
  const result: DiffLine[] = [];
  const maxLen = Math.max(oldMsgs.length, newMsgs.length);

  for (let i = 0; i < maxLen; i++) {
    const oldMsg = oldMsgs[i];
    const newMsg = newMsgs[i];

    if (!oldMsg && newMsg) {
      result.push({ type: "add", newContent: contentToText(newMsg.content), role: newMsg.role });
    } else if (oldMsg && !newMsg) {
      result.push({ type: "remove", oldContent: contentToText(oldMsg.content), role: oldMsg.role });
    } else if (oldMsg && newMsg) {
      if (oldMsg.content === newMsg.content && oldMsg.role === newMsg.role) {
        result.push({ type: "same", oldContent: contentToText(oldMsg.content), role: oldMsg.role });
      } else {
        result.push({ type: "change", oldContent: contentToText(oldMsg.content), newContent: contentToText(newMsg.content), role: newMsg.role });
      }
    }
  }

  return result;
}

export function SnapshotPanel() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>(loadSnapshots);
  const [diffSnapshotIdx, setDiffSnapshotIdx] = useState<number | null>(null);
  const [snapshotName, setSnapshotName] = useState("");
  const [saving, setSaving] = useState(false);

  const { messages } = useChatStore();
  const { setSnapshotPanelOpen } = useAppStore();
  const { items: kbItems, setItems: setKbItems, toggleItem: toggleKbItem } = useKnowledgeStore();

  useEffect(() => {
    saveSnapshots(snapshots);
  }, [snapshots]);

  const handleSave = useCallback(() => {
    if (messages.length === 0) return;
    if (saving) {
      if (!snapshotName.trim()) return;
      const now = new Date();
      const timeStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
      const kbConfig = kbItems.map((item) => ({ id: item.id, enabled: item.enabled }));
      setSnapshots((prev) => [
        ...prev,
        {
          name: snapshotName.trim(),
          time: timeStr,
          messages: [...messages],
          model: "current",
          kbConfig,
        },
      ]);
      setSnapshotName("");
      setSaving(false);
    } else {
      setSaving(true);
      const now = new Date();
      setSnapshotName(`快照 ${now.getMonth() + 1}-${now.getDate()} ${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}`);
    }
  }, [messages, saving, snapshotName, kbItems]);

  const handleDelete = useCallback((idx: number) => {
    setSnapshots((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const handleRestore = useCallback((idx: number) => {
    const snapshot = snapshots[idx];
    if (!snapshot) return;
    const confirmed = window.confirm(`确定要恢复快照「${snapshot.name}」吗？当前对话将被替换。`);
    if (!confirmed) return;
    useChatStore.getState().setMessages([...snapshot.messages]);
    // Restore KB config if available
    if (snapshot.kbConfig && snapshot.kbConfig.length > 0) {
      const currentItems = useKnowledgeStore.getState().items;
      const configMap = new Map(snapshot.kbConfig.map((c) => [c.id, c.enabled]));
      const updatedItems = currentItems.map((item) => {
        const enabled = configMap.get(item.id);
        return enabled !== undefined ? { ...item, enabled } : item;
      });
      setKbItems(updatedItems);
    }
  }, [snapshots, setKbItems]);

  const handleDiff = useCallback((idx: number) => {
    setDiffSnapshotIdx(idx);
  }, []);

  const handleCloseDiff = useCallback(() => {
    setDiffSnapshotIdx(null);
  }, []);

  const diffLines = diffSnapshotIdx !== null ? computeDiff(snapshots[diffSnapshotIdx].messages, messages) : null;

  return (
    <div className="snapshot-overlay" onClick={(e) => { if (e.target === e.currentTarget) setSnapshotPanelOpen(false); }}>
      <div className="snapshot-panel">
        <div className="snapshot-header">
          <h3>对话快照</h3>
          <button className="snapshot-close-btn" onClick={() => setSnapshotPanelOpen(false)}>✕</button>
        </div>

        {diffLines ? (
          <div className="snapshot-diff-view">
            <div className="snapshot-diff-header">
              <span>对比：{snapshots[diffSnapshotIdx!]?.name} ↔ 当前对话</span>
              <button className="snapshot-diff-close" onClick={handleCloseDiff}>关闭</button>
            </div>
            <div className="snapshot-diff-body">
              {diffLines.map((line, i) => {
                if (line.type === "add") {
                  return (
                    <div key={i} className="diff-line diff-add">
                      <span className="diff-prefix">+</span>
                      <span className="diff-role">[{line.role}]</span>
                      <span className="diff-content">{line.newContent?.slice(0, 120)}</span>
                    </div>
                  );
                }
                if (line.type === "remove") {
                  return (
                    <div key={i} className="diff-line diff-remove">
                      <span className="diff-prefix">-</span>
                      <span className="diff-role">[{line.role}]</span>
                      <span className="diff-content">{line.oldContent?.slice(0, 120)}</span>
                    </div>
                  );
                }
                if (line.type === "change") {
                  return (
                    <div key={i} className="diff-line diff-change">
                      <div className="diff-old">
                        <span className="diff-prefix">-</span>
                        <span className="diff-role">[{line.role}]</span>
                        <span className="diff-content">{line.oldContent?.slice(0, 120)}</span>
                      </div>
                      <div className="diff-new">
                        <span className="diff-prefix">+</span>
                        <span className="diff-role">[{line.role}]</span>
                        <span className="diff-content">{line.newContent?.slice(0, 120)}</span>
                      </div>
                    </div>
                  );
                }
                return (
                  <div key={i} className="diff-line diff-same">
                    <span className="diff-prefix"> </span>
                    <span className="diff-role">[{line.role}]</span>
                    <span className="diff-content">{line.oldContent?.slice(0, 120)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <>
            <div className="snapshot-list">
              {snapshots.length === 0 && (
                <div className="snapshot-empty">暂无快照</div>
              )}
              {snapshots.map((s, i) => (
                <div key={i} className="snapshot-card">
                  <div className="snapshot-card-info">
                    <div className="snapshot-card-name">{s.name}</div>
                    <div className="snapshot-card-meta">
                      <span>{s.time}</span>
                      <span>{s.messages.length} 条消息</span>
                      {s.kbConfig && <span>{s.kbConfig.filter((k) => k.enabled).length}/{s.kbConfig.length} 知识库</span>}
                    </div>
                  </div>
                  <div className="snapshot-card-actions">
                    <button className="snapshot-action-btn diff" onClick={() => handleDiff(i)}>对比</button>
                    <button className="snapshot-action-btn restore" onClick={() => handleRestore(i)}>恢复</button>
                    <button className="snapshot-action-btn delete" onClick={() => handleDelete(i)}>删除</button>
                  </div>
                </div>
              ))}
            </div>

            <div className="snapshot-save-area">
              {saving ? (
                <div className="snapshot-save-inline">
                  <input
                    className="snapshot-name-input"
                    value={snapshotName}
                    onChange={(e) => setSnapshotName(e.target.value)}
                    placeholder="快照名称"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSave();
                      if (e.key === "Escape") { setSaving(false); setSnapshotName(""); }
                    }}
                  />
                  <button className="snapshot-confirm-btn" onClick={handleSave}>保存</button>
                  <button className="snapshot-cancel-btn" onClick={() => { setSaving(false); setSnapshotName(""); }}>取消</button>
                </div>
              ) : (
                <button className="snapshot-save-btn" onClick={handleSave} disabled={messages.length === 0}>
                  保存快照
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
