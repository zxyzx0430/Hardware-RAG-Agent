# TopBar 精简 + 对话统计接入真实数据 Spec

## Why

当前聊天页 TopBar 右侧存在"快照"、"来源"、"汉堡菜单"三个入口区域。用户要求只保留汉堡菜单内的"切换深色模式"、"导出对话"、"对话统计"三项，删除"快照"和"来源"两个按钮，使顶部操作区更简洁。

同时，"对话统计"目前基于前端消息内容做 token 估算，当 LLM 未返回 usage 时会显示"~ 估算值"，用户要求接入真实数据。

## What Changes

### 前端改动

- **TopBar.tsx** 删除"快照"按钮（`snapshotToggleBtn`）和"来源"按钮（`sourceToggleBtn`）及其分隔线，仅保留右侧汉堡菜单入口。
- **HamburgerMenu.tsx** 确认菜单内只保留三项：切换深色模式、导出对话、对话统计。
- **StatsPanel.tsx** 改为从后端拉取当前会话的真实 token 用量统计；无真实数据时显示"暂无真实用量数据"提示，不再用前端估算值冒充。
- **api-contract.md** 同步更新 `/api/token-usage/stats` 接口说明。

### 后端改动

- **chat_routes.py** 的 `GET /api/token-usage/stats` 增加可选 `session_id` 查询参数，按会话过滤 TokenUsage 记录。
- 返回结构保持现有 `{ daily, by_model, summary }` 不变，只是数据来源限定为当前会话。

## Impact

- Affected specs：无（本批次为前端交互优化 + 单接口扩展）
- Affected code：
  - `frontend/src/components/topbar/TopBar.tsx`
  - `frontend/src/components/shared/HamburgerMenu.tsx`
  - `frontend/src/components/shared/StatsPanel.tsx`
  - `frontend/src/stores/useChatStore.ts`（新增/复用 sessionId 状态）
  - `backend/app/api/chat_routes.py`
  - `docs/api-contract.md`

## ADDED Requirements

### Requirement: TopBar 右侧只保留汉堡菜单

系统 SHALL 在聊天页 TopBar 右侧仅显示汉堡菜单按钮，不再显示"快照"和"来源"按钮。

#### Scenario: 用户进入聊天页

- **WHEN** 用户处于聊天视图
- **THEN** TopBar 右侧只看到汉堡菜单按钮（三条横线图标）
- **AND** 不再显示相机图标"快照"按钮
- **AND** 不再显示文档图标"来源"按钮

#### Scenario: 用户打开右侧面板

- **WHEN** 用户点击消息中的来源引用或知识库 badge 等已有入口
- **THEN** 右侧面板仍可正常打开/关闭
- **AND** TopBar 不因此新增按钮

### Requirement: 汉堡菜单只保留三项

系统 SHALL 在汉堡菜单下拉面板中只保留"切换深色模式"、"导出对话"、"对话统计"三个功能入口。

#### Scenario: 用户点击汉堡菜单

- **WHEN** 用户点击右上角汉堡菜单按钮
- **THEN** 下拉面板只显示三个菜单项
- **AND** 第一项为"切换深色模式"（含太阳/月亮图标）
- **AND** 第二项为"导出对话"（含下载图标），悬停/点击后展开格式子菜单
- **AND** 第三项为"对话统计"（含柱状图图标）

### Requirement: 对话统计接入后端真实数据

系统 SHALL 在用户打开"对话统计"面板时，调用后端 `/api/token-usage/stats?session_id=<当前会话ID>` 获取当前会话的真实 token 用量数据。

#### Scenario: 当前会话有真实用量记录

- **WHEN** 用户打开"对话统计"
- **THEN** 面板显示当前会话的真实消息数、用户消息数、AI 消息数、来源引用数
- **AND** 显示真实的 Input Tokens / Output Tokens / Total Tokens
- **AND** 显示按天的 token 折线图（基于后端返回的 `daily`）
- **AND** 显示按模型分组的用量（基于后端返回的 `by_model`）
- **AND** 不显示"~ 估算值"提示

#### Scenario: 当前会话无真实用量记录

- **WHEN** 用户打开"对话统计"
- **AND** 后端返回空数据或该会话尚无 TokenUsage 记录
- **THEN** 面板显示"暂无真实用量数据，发送消息并等待 LLM 返回 usage 后可查看"
- **AND** 不显示估算 token 数

#### Scenario: 后端统计接口失败

- **WHEN** 用户打开"对话统计"
- **AND** 后端 `/api/token-usage/stats` 返回 `success: false`
- **THEN** 面板显示错误提示"统计加载失败：{error.message}"
- **AND** 提供"重试"按钮

### Requirement: 后端接口支持按会话过滤

系统 SHALL 在 `GET /api/token-usage/stats` 支持可选查询参数 `session_id`。

#### Scenario: 提供 session_id

- **WHEN** 请求带 `session_id=<id>`
- **THEN** 只统计该会话的 TokenUsage 记录
- **AND** `daily` 按该会话记录的发生日期聚合
- **AND** `by_model` 按该会话使用的模型聚合
- **AND** `summary` 基于该会话记录汇总

#### Scenario: 不提供 session_id

- **WHEN** 请求不带 `session_id`
- **THEN** 保持现有行为，统计全部记录

## MODIFIED Requirements

无

## REMOVED Requirements

### Requirement: TopBar"快照"按钮

**Reason**：用户明确要求删除，快照功能非当前核心路径。
**Migration**：`SnapshotPanel` 组件保留，如需未来可通过汉堡菜单或其他入口触发。

### Requirement: TopBar"来源"按钮

**Reason**：用户明确要求删除，来源面板仍可通过消息内引用、右侧 SourcePanel 其他入口打开。
**Migration**：`rightPanelOpen` / `setRightMode` 状态保留，供其他入口复用。

### Requirement: 前端估算 token 数作为对话统计展示

**Reason**：用户要求接入真实数据，估算值不可作为真实统计展示。
**Migration**：无真实数据时显示空状态提示，不再展示估算值。
