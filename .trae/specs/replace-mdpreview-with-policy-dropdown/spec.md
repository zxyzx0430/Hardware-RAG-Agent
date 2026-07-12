# 删除 Markdown 预览并在输入栏加入 Agent 权限下拉选择 Spec

## Why

当前输入栏（InputBar）底部操作区有一个 Markdown 预览按钮，点击后会在输入框下方展开实时预览区域。该功能使用频率低且与当前产品主路径（硬件问答 + Agent 工具调用）关联不大。

用户希望在同一区域换成 Agent 权限模式切换入口，方便在聊天过程中快速调整 Agent 工具调用的权限策略（全部放行 / 每次询问 / 自动放行低风险）。

## What Changes

### 前端改动

- **InputBar.tsx**
  - 删除 Markdown 预览按钮（`mdPreviewBtn`）及其 `showPreview` 状态、预览区域（`input-preview`）和相关 i18n 引用。
  - 在底部操作区（模型选择器与发送按钮之间或左侧）新增 Agent 权限下拉选择器。
  - 下拉选择器绑定 `useSettingsStore.permissionMode`，选项为：
    - `bypassPermissions` — 全部放行
    - `default` — 每次询问
    - `acceptEdits` — 自动放行低风险
  - 选择后调用 `updateSetting("permissionMode", mode)` 更新全局状态。

- **PolicyBar.tsx（现有组件）**
  - 保持现有逻辑不变，本次不删除。未来可作为设置页或备用入口复用。

- **样式文件**
  - 清理 `misc.css` 中不再使用的 `.preview-toggle`、`.preview-pane` 等样式（若仅此处使用）。
  - 新增/复用下拉选择器样式，保持与模型选择器视觉一致。

- **i18n**
  - 删除 `mdPreviewBtn`、`mdPreviewHint` 的 zh/en 词条。
  - 新增权限模式相关词条（如不存在）：`permissionBypass`、`permissionAsk`、`permissionAuto` 或复用现有 PolicyBar 词条。

### 后端改动

无。`permission_mode` 已随聊天请求发送到后端。

## Impact

- Affected specs：无
- Affected code：
  - `frontend/src/components/input/InputBar.tsx`
  - `frontend/src/styles/misc.css`
  - `frontend/src/i18n/zh.ts`
  - `frontend/src/i18n/en.ts`

## ADDED Requirements

### Requirement: 输入栏提供 Agent 权限下拉切换

系统 SHALL 在输入栏底部操作区提供一个下拉选择器，用于切换当前 Agent 工具调用的权限模式。

#### Scenario: 用户查看输入栏

- **WHEN** 用户处于聊天视图并看向输入栏底部
- **THEN** 能看到一个权限模式选择器（紧邻模型选择器或发送按钮）
- **AND** 选择器显示当前模式名称

#### Scenario: 用户切换权限模式

- **WHEN** 用户点击权限选择器
- **THEN** 下拉展开三个选项：全部放行 / 每次询问 / 自动放行低风险
- **AND** 点击某选项后，下拉收起
- **AND** 选择器显示新选中的模式
- **AND** 后续发送的聊天请求使用新的 `permission_mode`

### Requirement: 删除 Markdown 预览功能

系统 SHALL 从输入栏移除 Markdown 预览按钮及其预览区域。

#### Scenario: 用户查看输入栏

- **WHEN** 用户处于聊天视图
- **THEN** 输入栏底部不再显示 Markdown 预览按钮
- **AND** 输入框下方不会因点击按钮展开 Markdown 预览区域
- **AND** 输入框外的操作区保持原有模板、附件、模型选择、发送按钮功能正常

## MODIFIED Requirements

无

## REMOVED Requirements

### Requirement: 输入栏 Markdown 实时预览

**Reason**：用户要求移除该入口，用 Agent 权限切换替代。
**Migration**：Markdown 渲染仍由 `MarkdownRenderer` 在消息气泡中处理；输入阶段不再提供实时预览。
