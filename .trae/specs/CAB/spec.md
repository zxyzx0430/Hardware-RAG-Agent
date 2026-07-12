# CAB：独立第四栏文件资源管理器 + 可编辑代码预览 Spec

## Why

当前 Hardware RAG Agent 的前端无法让用户实时查看和编辑本地文件。Agent 生成或修改代码后，用户只能依赖聊天消息查看结果，无法直接浏览项目结构、检查改动、实时跟踪文件状态。通过在界面最右侧增加独立的资源管理器第四栏，并在第四栏内提供可编辑的代码编辑器，可以把应用从纯聊天工具升级为类 IDE 的硬件开发工作台。

## What Changes

- **前端布局升级**：在现有三栏（IconNav + 会话 + 聊天 + 右侧面板）右侧新增独立第四栏「资源管理器」。
- **文件树组件**：支持浏览文件夹、展开/折叠、搜索过滤、右键新建/重命名/删除/复制路径、拖拽移动。
- **可编辑编辑器**：第四栏内部打开文件，顶部 tab 切换多文件，支持 Monaco 代码编辑、语法高亮、行号、快捷键、手动保存。
- **实时文件监听**：后端使用 watchdog 监听用户打开的文件夹，通过 SSE/WS 推送变更到前端，文件树和当前编辑器实时同步。
- **Agent 联动**：Agent 生成代码时提供「在编辑器中打开」按钮；用户可手动开启「跟随模式」实时观察本地文件变化；文件 tab 显示外部变更标记，点击可看 diff。
- **持久化**：跨会话恢复已打开的文件 tab、pin 状态、第四栏宽度；保存最近 5-10 个打开的文件夹。

## Impact

- **受影响能力**：前端布局、文件操作、Agent 代码生成结果展示、实时状态同步、diff 查看。
- **受影响代码**：
  - 前端：`frontend/src/components/layout/AppRoot.tsx`、`RightPanel.tsx`、新增 `explorer/` 组件目录、`useAppStore.ts`。
  - 后端：新增 `backend/app/api/explorer_routes.py`、注册到 `main.py`、可能新增文件系统监听模块。

## ADDED Requirements

### Requirement: 独立第四栏资源管理器

The system SHALL 在应用最右侧提供独立的资源管理器面板（第四栏）。

#### Scenario: 正常展开
- **WHEN** 用户打开应用
- **THEN** 第四栏默认展开，显示文件树（若用户已选择过文件夹则显示该文件夹内容）

#### Scenario: 折叠与展开
- **WHEN** 用户点击第四栏左侧的窄边条
- **THEN** 第四栏折叠为窄边条；再次点击恢复为上次宽度

#### Scenario: 宽度拖拽
- **WHEN** 用户拖拽第四栏左侧 resizer
- **THEN** 宽度在 200px 到 50% 屏幕宽度之间变化

### Requirement: 文件树浏览与操作

The system SHALL 在第四栏提供完整的文件树操作能力。

#### Scenario: 打开文件夹
- **WHEN** 用户点击「打开文件夹」按钮
- **THEN** 弹出系统文件选择对话框，用户选择后加载该文件夹树

#### Scenario: 文件树右键操作
- **WHEN** 用户在文件/文件夹上右键
- **THEN** 显示菜单：新建文件、新建文件夹、重命名、删除、复制路径

#### Scenario: 拖拽移动
- **WHEN** 用户拖拽文件/文件夹到另一个文件夹
- **THEN** 该文件/文件夹被移动到目标目录

#### Scenario: 搜索过滤
- **WHEN** 用户在文件树顶部搜索框输入关键字
- **THEN** 只显示匹配的文件和文件夹（保留路径上下文）

### Requirement: 可编辑文件编辑器

The system SHALL 在第四栏内部打开文件并提供可编辑能力。

#### Scenario: 打开文件
- **WHEN** 用户点击文件树中的文本文件
- **THEN** 第四栏切换为编辑器视图，顶部出现该文件 tab

#### Scenario: 多文件 tab 切换
- **WHEN** 用户点击顶部某个已打开文件的 tab
- **THEN** 编辑器显示对应文件内容

#### Scenario: 编辑与保存
- **WHEN** 用户修改文件内容并按下 Ctrl+S 或点击保存按钮
- **THEN** 内容被写回本地磁盘，tab 上未保存标记消失

#### Scenario: 关闭未保存 tab
- **WHEN** 用户关闭带有未保存修改的 tab
- **THEN** 弹出对话框询问「保存 / 不保存 / 取消」

#### Scenario: pin tab
- **WHEN** 用户右键 tab 选择 pin 或点击 pin 图标
- **THEN** 该 tab 固定在最左侧，关闭时需要二次确认

### Requirement: 实时文件状态同步

The system SHALL 通过后端监听实时推送本地文件变化到前端。

#### Scenario: 外部文件修改
- **WHEN** 用户或 Agent 在外部修改了已打开文件夹中的文件
- **THEN** 文件树自动刷新，对应文件 tab 显示变更标记

#### Scenario: 当前编辑文件被外部修改
- **WHEN** 用户正在编辑的文件被外部修改
- **THEN** 弹出提示「文件已在外部修改，是否重新加载？」

### Requirement: Agent 联动

The system SHALL 让 Agent 的代码生成结果能方便地在编辑器中查看和编辑。

#### Scenario: Agent 生成代码后打开
- **WHEN** Agent 调用 generate_code 或 render_code 输出代码
- **THEN** 代码块旁显示「在编辑器中打开」按钮；点击后在第四栏打开未保存 buffer

#### Scenario: 跟随模式
- **WHEN** 用户手动开启「跟随模式」
- **THEN** 第四栏持续监听本地文件变化，实时反映 Agent 对本地文件的修改

### Requirement: diff 查看改动

The system SHALL 提供 diff 视图查看文件改动。

#### Scenario: 查看文件变更
- **WHEN** 用户点击带有变更标记的文件 tab 上的 diff 按钮
- **THEN** 显示 diff 视图，对比当前文件与 Git HEAD（优先）或打开时的内存快照

## MODIFIED Requirements

### Requirement: 前端布局

原三栏布局扩展为四栏布局。右侧面板保留「工作台」和「对话内容」两个 tab，**不再**在右侧面板内放置「文件」tab。文件相关功能全部迁移到最右侧第四栏。

## REMOVED Requirements

### Requirement: 右侧面板「文件」tab
**Reason**：用户明确要求文件不在右侧面板展示，而是在独立第四栏内部打开编辑。
**Migration**：已有的 `file` rightMode 如果存在则移除，相关文件查看逻辑迁移到第四栏编辑器。
