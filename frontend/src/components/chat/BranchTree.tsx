import React, { useMemo } from "react";
import { useSessionStore } from "../../stores/useSessionStore";
import { useChatStore } from "../../stores/useChatStore";
import type { Session } from "../../types/session";

interface BranchNode {
  sessionId: string;
  title: string;
  branchFromMessageId?: string;
  children: BranchNode[];
}

interface BranchTreeProps {
  currentSessionId: string;
  onSwitchSession: (sessionId: string) => void;
  onClose: () => void;
}

export const BranchTree: React.FC<BranchTreeProps> = ({ currentSessionId, onSwitchSession, onClose }) => {
  const sessions = useSessionStore(s => s.sessions);

  // 构建分支树
  const tree = useMemo(() => {
    const sessionMap = new Map(sessions.map(s => [s.id, s]));
    const roots: BranchNode[] = [];
    const childrenMap = new Map<string, BranchNode[]>();

    for (const session of sessions) {
      const node: BranchNode = {
        sessionId: session.id,
        title: session.title || "未命名对话",
        branchFromMessageId: session.branchFromMessageId,
        children: [],
      };

      const parentId = session.branchFromSessionId;
      if (parentId && sessionMap.has(parentId)) {
        if (!childrenMap.has(parentId)) childrenMap.set(parentId, []);
        childrenMap.get(parentId)!.push(node);
      } else {
        roots.push(node);
      }
    }

    // 递归设置 children
    const setChildren = (nodes: BranchNode[]) => {
      for (const node of nodes) {
        node.children = childrenMap.get(node.sessionId) || [];
        setChildren(node.children);
      }
    };
    setChildren(roots);

    return roots;
  }, [sessions]);

  // 渲染树节点
  const renderNode = (node: BranchNode, depth: number = 0) => {
    const isCurrent = node.sessionId === currentSessionId;
    return (
      <div key={node.sessionId}>
        <div
          onClick={() => onSwitchSession(node.sessionId)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 12px",
            cursor: "pointer",
            borderRadius: 6,
            background: isCurrent ? "var(--accent)" : "transparent",
            color: isCurrent ? "#fff" : "var(--fg)",
            fontWeight: isCurrent ? 600 : 400,
            marginLeft: depth * 24,
            fontSize: 13,
          }}
        >
          {/* 分支线 */}
          {depth > 0 && (
            <span style={{ color: "var(--muted-fg)", fontSize: 11 }}>├─</span>
          )}
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {node.title}
          </span>
          {isCurrent && <span style={{ fontSize: 10, opacity: 0.7 }}>●</span>}
        </div>
        {node.children.map(child => renderNode(child, depth + 1))}
      </div>
    );
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 9999, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.5)" }} onClick={onClose}>
      <div style={{ background: "var(--bg)", borderRadius: 12, padding: 24, minWidth: 360, maxWidth: 520, maxHeight: "80vh", overflow: "auto" }} onClick={e => e.stopPropagation()}>
        <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>对话分支图</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {tree.map(node => renderNode(node))}
        </div>
        <button onClick={onClose} style={{ marginTop: 16, width: "100%", padding: "8px 16px", borderRadius: 8, background: "var(--accent)", color: "#fff", border: "none", cursor: "pointer" }}>关闭</button>
      </div>
    </div>
  );
};
