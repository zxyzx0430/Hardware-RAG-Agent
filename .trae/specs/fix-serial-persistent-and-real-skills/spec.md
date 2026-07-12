# 串口持久连接 + 技能管理真实数据 + 同类问题修复 Spec

## Why

用户发现串口监视器切换界面就断开、技能管理显示假数据。深入扫描发现这两类问题都是系统性的：
- **组件卸载丢状态**：WorkbenchPanel 5 个 Pane 全部用条件渲染，切换 Tab 丢失编译日志/审计结果/Monaco 编辑器/串口连接；AppRoot 关闭右栏/切换主导航也整体卸载，丢失 ChatArea 编辑状态和 InputBar 草稿
- **硬编码假数据**：技能管理 7 个硬编码 skill（3 个后端不存在）、mcpServers 3 个假服务器、`useSettingsStore.model` 引用不存在的字段、前端 settings 从未同步后端、about 版本信息过时

## What Changes

### A 组：WorkbenchPanel 5 个 Pane 改为 CSS 隐藏

- **WorkbenchPanel.tsx**：5 个 Pane 从 `&&` 条件渲染改为 `display:none` CSS 隐藏，切换 Tab 不卸载组件
  - SerialPane：WebSocket 不再被 cleanup 关闭
  - FlashPane：编译日志/进度/编译状态保留，SSE 不中断
  - PreviewPane：Monaco 编辑器实例保留，诊断结果不丢
  - WiringPane：zoom/pan 视图状态保留
  - SafetyPane：审计结果/冲突详情保留

### B 组：AppRoot 右栏/主导航改为 CSS 隐藏

- **AppRoot.tsx**：`rightPanelOpen` 关闭时改为 `display:none` 隐藏 RightPanel，不卸载
- **AppRoot.tsx**：`activeNav` 切换时 ChatArea/InputBar/RightPanel 改为 CSS 隐藏（Knowledge/Bookmark/Settings 也常驻或保持现有条件渲染但 store 提升）
- **SerialPane.tsx**：移除 cleanup useEffect 中的 `wsRef.current?.close()`（因为组件不再卸载）

### C 组：技能管理接入真实数据

- **后端 main.py**：注册 skill_router
- **后端新增 GET /api/tools**：返回 agent_factory 26 个 LangChain Tool 的 `{name, description, group, enabled}`
- **后端新增 PATCH /api/tools/{name}/toggle**：切换工具启用状态，持久化到 `data/tool_config.json`
- **后端 agent_factory.py**：`_assemble_all_tools` 支持按禁用列表过滤
- **前端 useSettingsStore.ts**：删除 7 条硬编码 skills，新增 `fetchTools()` 从 `/api/tools` 拉取
- **前端 SettingsPage.tsx**：skills tab 挂载时调用 `fetchTools()`
- **前端 toggleSkill**：调用 `PATCH /api/tools/{name}/toggle`

### D 组：其他硬编码假数据修复

- **useSettingsStore.ts**：mcpServers 默认值改为 `[]`
- **useSettingsStore.ts**：修复 `getState().model` → `getState().chatModel`（6 处）
- **useSettingsStore.ts**：定义 `DEFAULT_MODEL` 常量替换硬编码 `"GPT-4o"`
- **i18n/zh.ts + en.ts**：about tab 版本信息改为从后端拉取或移除 buildDate

### E 组：前端 settings 同步后端

- **useSettingsStore.ts**：初始化时调 `apiGet("settings")` 加载
- **useSettingsStore.ts**：`updateSetting` 时异步调 `apiPut("settings", { [key]: value })` 同步

## Impact

- Affected specs：`fix-fake-interactions-batch`（已完成）
- Affected code：
  - `frontend/src/components/workbench/WorkbenchPanel.tsx`
  - `frontend/src/components/layout/AppRoot.tsx`
  - `frontend/src/components/workbench/SerialPane.tsx`
  - `backend/app/main.py`
  - `backend/app/api/tool_routes.py`
  - `backend/src/agent/agent_factory.py`
  - `frontend/src/stores/useSettingsStore.ts`
  - `frontend/src/components/settings/SettingsPage.tsx`
  - `frontend/src/stores/useChatStore.ts`
  - `frontend/src/stores/useSessionStore.ts`
  - `frontend/src/i18n/zh.ts`
  - `frontend/src/i18n/en.ts`
  - `docs/api-contract.md`

## ADDED Requirements

### Requirement: 工作台 Tab 切换不丢状态

WorkbenchPanel 的 5 个 Pane SHALL 用 CSS 隐藏切换，切换 Tab 时组件不卸载，所有 local state 保留。

#### Scenario: 切换 Flash Tab 后编译日志保留

- **WHEN** 用户在 FlashPane 编译中切换到 Preview Tab 再切回
- **THEN** 编译日志完整保留，进度条继续更新

#### Scenario: 切换 Safety Tab 后审计结果保留

- **WHEN** 用户在 SafetyPane 查看冲突后切换到 Wiring Tab 再切回
- **THEN** 引脚分配表和冲突详情完整保留

### Requirement: 串口连接跨界面持久

串口 WebSocket 连接 SHALL 在切换工作台 Tab、关闭右侧面板、切换主导航时保持不断开。

#### Scenario: 关闭右侧面板后串口保持连接

- **WHEN** 用户关闭右侧面板再重新打开
- **THEN** 串口连接未断开，日志区显示连接期间的累计数据

### Requirement: 技能管理显示真实工具

技能管理页面 SHALL 显示后端 26 个真实 LangChain Tool，不再显示硬编码假数据。

#### Scenario: 打开技能管理页面

- **WHEN** 用户进入 Settings → 技能 tab
- **THEN** 显示 26 个真实工具的 name + description + group + 开关状态

#### Scenario: 禁用工具后影响 Agent

- **WHEN** 用户关闭某工具的开关
- **THEN** 后端持久化该状态
- **AND** 下次 Agent 调用时该工具不可用

### Requirement: 后端 /api/tools 端点

后端 SHALL 提供 `GET /api/tools` 返回工具元数据，`PATCH /api/tools/{name}/toggle` 切换状态。

### Requirement: MCP 服务器默认值不硬编码

useSettingsStore 的 mcpServers 默认值 SHALL 为空数组，由 fetchMCPServers 填充。

### Requirement: settings 跨设备同步

useSettingsStore SHALL 在初始化时从后端加载，更新时同步到后端。

## MODIFIED Requirements

### Requirement: useSettingsStore.model 字段引用

所有 `useSettingsStore.getState().model` SHALL 改为 `useSettingsStore.getState().chatModel`。

## REMOVED Requirements

### Requirement: useSettingsStore 硬编码 skills

**Reason**：7 条硬编码 skill 中 3 条后端不存在，1 条名称不符，且从未调用后端 API。
**Migration**：删除硬编码，改为从 /api/tools 拉取。

### Requirement: mcpServers 硬编码默认值

**Reason**：3 个假服务器首次加载闪烁，且 fetchMCPServers 会覆盖。
**Migration**：默认值改为空数组。

### Requirement: 硬编码 "GPT-4o" 默认模型

**Reason**：用户可能用 DeepSeek/GLM 等其他模型，硬编码 "GPT-4o" 不准确。
**Migration**：改为从 useSettingsStore.chatModel 取或定义 DEFAULT_MODEL 常量。
