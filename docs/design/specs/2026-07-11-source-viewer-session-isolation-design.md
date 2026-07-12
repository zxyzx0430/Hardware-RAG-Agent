# Source Viewer Session Isolation + Right Panel Full-Height Fix

> Date: 2026-07-11
> Scope: frontend chat / source viewer
> Status: spec ready for review

## 1. Background

用户报告两个关联 bug：

1. **跨会话来源污染**：新建或切换到另一个对话后，右侧"对话内容"面板仍显示旧对话的来源列表/卡片，且点击无响应（"点不开"）。
2. **右侧面板未占满**：点击新对话检索出的来源后，右侧来源详情只显示上半截，下半部分空白，没有占满右侧整栏。

## 2. Root Cause

- `fileViewerSource` 与 `highlightSourceId` 当前存储在全局 `useAppStore` 中，切换会话时不会被重置。旧会话的"当前查看来源"ID 残留，导致新会话右侧面板仍尝试定位/高亮旧来源。
- `RightPanel.tsx` 中 `.source-fv-content` 被硬编码为 `style={{ height: 400 }}`，父容器 `.source-fv-scroll` 也未启用 flex 纵向填充，因此 MonacoEditor 被锁死在 400px 高度。

## 3. Goals

1. 根除跨会话来源查看器状态污染。
2. 修复右侧来源详情区域占满整栏。
3. 保持现有交互不变：消息内 [srcN] 点击、来源卡片点击、高亮、返回列表、KB 文档查看。

## 4. Non-Goals

- 不改后端接口或 SSE 事件格式。
- 不重写 MarkdownRenderer 的引用解析逻辑。
- 不做来源持久化（刷新后丢失当前查看状态可接受）。

## 5. Design

### 5.1 数据模型：来源查看器状态会话隔离

**从 `useAppStore` 删除：**

```ts
fileViewerSource: string | null;
highlightSourceId: string | null;
setFileViewerSource: (src: string | null) => void;
setHighlightSourceId: (id: string | null) => void;
```

**在 `useChatStore` 新增（按 `sessionId` 隔离）：**

```ts
// key = sessionId
sessionFileViewerSource: Record<string, string | null>;
sessionHighlightSourceId: Record<string, string | null>;

setSessionFileViewerSource: (sessionId: string, src: string | null) => void;
setSessionHighlightSourceId: (sessionId: string, id: string | null) => void;
```

新增 action 实现：

```ts
setSessionFileViewerSource: (sessionId, src) =>
  set((s) => ({
    sessionFileViewerSource: { ...s.sessionFileViewerSource, [sessionId]: src },
  })),
setSessionHighlightSourceId: (sessionId, id) =>
  set((s) => ({
    sessionHighlightSourceId: { ...s.sessionHighlightSourceId, [sessionId]: id },
  })),
```

初始值均为 `{}`。不持久化到 localStorage，刷新后丢失可接受。

### 5.2 组件改造

| 文件 | 改造点 |
|------|--------|
| `frontend/src/stores/appStore/types.ts` | 删除 `fileViewerSource` / `highlightSourceId` 相关类型字段 |
| `frontend/src/stores/useAppStore.ts` | 删除状态字段与 setter |
| `frontend/src/stores/useChatStore.ts` | 新增 `sessionFileViewerSource` / `sessionHighlightSourceId` 及 setter；初始化值为 `{}` |
| `frontend/src/components/layout/RightPanel.tsx` | 从 `useChatStore` 取当前 `activeSessionId` 对应的查看状态；所有 `setFileViewerSource` / `setHighlightSourceId` 调用改为带 `activeSessionId` 的会话版本 |
| `frontend/src/components/chat/ChatArea.tsx` | `openSource` 使用 `activeSessionId` 写入会话级别的查看状态；移除对 `useAppStore.fileViewerSource` 的读取（当前已不直接读取） |
| `frontend/src/components/chat/AssistantMessageRow.tsx` | 高亮判断改为 `sessionHighlightSourceId[activeSessionId]` |
| `frontend/src/components/knowledge/KnowledgePanel.tsx` | 打开 KB 文档到右侧时，写入当前 `activeSessionId` 对应的 `sessionFileViewerSource` |

### 5.3 右侧面板占满修复

`frontend/src/components/layout/RightPanel.tsx`：

```tsx
{/* 移除固定高度 */}
<div className="source-fv-content" style={{ flex: 1, minHeight: 0 }}>
  <MonacoEditor height="100%" ... />
</div>
```

`frontend/src/styles/workbench.css`：

```css
.source-fv-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

.source-fv-content {
  flex: 1;
  min-height: 0;
  border-radius: var(--radius-sm);
  background: var(--bg);
  border: 1px solid var(--border);
  overflow: hidden;
}
```

### 5.4 删除入口统一

`useAppStore` 不再暴露 `setFileViewerSource` / `setHighlightSourceId`。所有消费侧统一通过 `useChatStore.getState().setSessionFileViewerSource(activeSessionId, ...)` 写入。

## 6. Migration / Compatibility

- 旧全局状态未持久化，直接删除不会影响用户数据。
- 如果某个组件仍通过旧 API 引用，TypeScript 编译会直接报错，可在编译阶段发现。

## 7. Testing Plan

### 7.1 手动回归

1. 会话 A 提问并产生来源。
2. 在会话 A 点击一个来源 → 右侧占满整栏显示详情。
3. 新建会话 B。
4. 会话 B 右侧"对话内容"应显示空来源列表，不应出现会话 A 的来源。
5. 在会话 B 提问并产生新来源 → 点击新来源 → 右侧显示 B 的来源详情，且占满整栏。
6. 切回会话 A → 右侧应恢复显示 A 的来源列表；若 A 之前有打开的来源，应恢复 A 的查看状态。
7. 删除会话 A 后，确认会话 B 不受影响。

### 7.2 自动化

- 更新 `useChatStore.test.ts`：验证切换会话后 `sessionFileViewerSource` / `sessionHighlightSourceId` 按 sessionId 隔离。
- 运行前端类型检查 `cd frontend && npx tsc --noEmit`。

## 8. Rollback

若重构后出现问题，直接回滚到本次开工前提交的 checkpoint：

```bash
git reset --hard 4ec7a510
```

## 9. Subagent Plan

实现完成后启动 3 个 subagent：

1. **完成度审查子代理**：检查是否所有 `fileViewerSource` / `highlightSourceId` 旧引用已迁移、类型是否干净、是否有遗漏文件。
2. **端到端测试子代理**：按 7.1 手动回归步骤，使用 Playwright / webapp-testing 工具跑通多会话来源切换场景。
3. **缺陷发现与修复子代理**：专门扫描跨会话状态污染、布局异常、 MonacoEditor 高度、KB 文档打开路径等回归风险点。

## 10. Open Questions

- 是否需要在删除会话时清理对应 `sessionFileViewerSource` / `sessionHighlightSourceId` 条目？建议实现：在 `deleteSession` 成功后删除对应 key，避免内存中遗留无用状态。
