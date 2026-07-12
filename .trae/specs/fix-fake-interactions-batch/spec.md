# 修复假交互/假按键/数据流断点 Spec

## Why

用户发现重命名会话后顶部栏标题不更新，由此引出系统性的"假交互"问题：UI 上看起来有功能但实际没接通真实逻辑。经 3 个 subagent 全量扫描，共发现 15+ 个假交互、假按键、静默失败、死代码问题，集中在三类根因：

1. **跨 store 订阅缺失**：TopBar/StatsPanel/BookmarkPanel 不订阅会话级状态，显示全局或硬编码值
2. **静默失败**：useSessionStore 的 rename/pin/move/delete 全部 `.catch(() => {})`，后端失败用户无感知
3. **假按钮/假数据**：ConfirmDialog "永久允许"不传 scope、SerialPane 注入硬编码假串口数据、About 链接指向占位域名

本 spec 覆盖高优先级和中优先级问题，低优先级死代码清理一并处理。

## What Changes

### A 组：标题/状态不跟随（高优先级）

- **TopBar.tsx**：sessionTitle 改为订阅 `useSessionStore.sessions[].title`（按 activeSessionId 查找），不再从 `messages[0]` 派生
- **TopBar.tsx**：sources 徽章改用最后一条 assistant 消息的 `msg.sources`，不再读全局 `useChatStore.sources`
- **useBookmarkStore.ts**：创建 BookmarkEntry 时注入真实 `sessionTitle` 和 `sessionId`（从 useChatStore + useSessionStore 读取），不再硬编码 "当前对话"
- **StatsPanel.tsx**：model 字段改为读取当前会话的 model（`session.model || chatModel`），不再只读全局默认

### B 组：静默失败（高优先级）

- **useSessionStore.ts**：renameSession / pinSession / moveSession / deleteSession 四处 `.catch(() => {})` 改为：
  - 失败时回滚乐观更新（恢复旧值）
  - 向调用方返回成功/失败状态
  - 通过 console.warn 记录错误（不弹 toast，避免干扰）
- **useChatStore.ts**：clearMessages 的 `.catch(() => {})` 同样改为回滚 + 记录

### C 组：假按钮/假数据（中优先级）

- **ConfirmDialog.tsx**："永久允许" 按钮暂时移除 `scope` 参数逻辑，改为只在 UI 上区分文案，行为与"允许本次"一致（因为后端白名单未实现）。按钮 footer 文案改为"永久允许功能开发中"，或直接移除"永久允许"按钮只保留"允许本次"/"拒绝"/"停止"
- **SerialPane.tsx**：删除 `seed()` 函数和 ⟳ 演示数据按钮，或改为 `title="注入演示数据（非真实串口数据）"` 并在日志前加 `[DEMO]` 前缀
- **SettingsPage.tsx**：About 标签页的 `docs.example.com` 和 `github.com/example/...` 链接改为隐藏（项目未开源前），或改为真实文档地址
- **AuditLogPanel.tsx**：FilterBar 补充 tool_name 输入框；挂载时自动 fetchLogs

### D 组：死代码清理（低优先级）

- **endpoints.ts**：整个文件未被引用（`grep "ENDPOINTS\."` 返回 0 命中），删除文件
- **IconNav.tsx**：4 个 `nav-dim` 装饰按钮改为 `<div>` 或加 `disabled` + `title="功能开发中"`

## Impact

- Affected specs：`topbar-cleanup-stats-realdata`（已完成，本 spec 在其基础上继续修复 TopBar 标题订阅问题）
- Affected code：
  - `frontend/src/components/topbar/TopBar.tsx`
  - `frontend/src/stores/useBookmarkStore.ts`
  - `frontend/src/components/shared/StatsPanel.tsx`
  - `frontend/src/stores/useSessionStore.ts`
  - `frontend/src/stores/useChatStore.ts`
  - `frontend/src/components/chat/ConfirmDialog.tsx`
  - `frontend/src/components/workbench/SerialPane.tsx`
  - `frontend/src/components/settings/SettingsPage.tsx`
  - `frontend/src/components/settings/AuditLogPanel.tsx`
  - `frontend/src/api/endpoints.ts`（删除）
  - `frontend/src/components/layout/IconNav.tsx`

## ADDED Requirements

### Requirement: TopBar 标题跟随会话重命名

TopBar 标题 SHALL 订阅 `useSessionStore.sessions` 中与 `useChatStore.activeSessionId` 匹配的会话标题，在会话重命名后立即更新。

#### Scenario: 用户重命名会话

- **WHEN** 用户在 SessionPanel 重命名会话为 "ESP32 接线方案"
- **THEN** TopBar 标题立即显示 "ESP32 接线方案"

#### Scenario: 切换到无标题会话

- **WHEN** 用户切换到一个没有 title 的会话
- **THEN** TopBar 显示本地化的 "未命名对话" 占位文案

### Requirement: TopBar 来源徽章跟随当前消息

TopBar 来源徽章 SHALL 显示最后一条 assistant 消息的 `msg.sources.length`，不再使用全局 `useChatStore.sources`。

#### Scenario: 切换会话后徽章归零

- **WHEN** 用户从有来源的会话 A 切换到无来源的会话 B
- **THEN** TopBar 来源徽章隐藏（sources.length === 0）

### Requirement: 书签记录真实会话信息

useBookmarkStore 创建 BookmarkEntry 时 SHALL 注入当前活跃会话的真实 `sessionTitle` 和 `sessionId`。

#### Scenario: 在会话 A 中收藏消息

- **WHEN** 用户在会话 "ESP32 接线方案" 中收藏一条消息
- **THEN** BookmarkPanel 中该书签显示 "ESP32 接线方案" 作为所属对话

### Requirement: 会话操作失败回滚

useSessionStore 的 renameSession / pinSession / moveSession / deleteSession SHALL 在后端 API 失败时回滚乐观更新。

#### Scenario: 重命名失败

- **WHEN** 用户重命名会话但后端 PUT /api/sessions/:id 返回 500
- **THEN** 会话标题恢复为旧值
- **AND** 控制台输出 warn 级别日志

### Requirement: 删除演示数据注入

SerialPane SHALL 不再注入硬编码的假串口数据，或明确标注为演示数据。

#### Scenario: 用户打开串口面板

- **WHEN** 用户点击 ⟳ 按钮
- **THEN** 注入的每条日志前缀 `[DEMO]`，或按钮被移除

## MODIFIED Requirements

### Requirement: ConfirmDialog 永久允许按钮

当前"永久允许"按钮与"允许本次"行为完全一致（scope 参数被丢弃）。修改为：移除"永久允许"按钮，只保留"允许本次"/"拒绝"/"停止"三个按钮，避免误导用户。

### Requirement: AuditLogPanel 过滤器

FilterBar 补充 tool_name 输入框，挂载时自动加载日志。

## REMOVED Requirements

### Requirement: endpoints.ts 死代码

**Reason**：整个 `ENDPOINTS` 常量对象和 `SSE_EVENTS` 从未被任何文件 import 使用，所有 API 调用直接硬编码字符串路径。
**Migration**：直接删除文件，无影响。
