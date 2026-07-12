# 硬件工作台 × Agent 联动 Spec

> Change-ID: `workbench-agent-bridge`
> Owner: Trae T6-硬件工作台线程
> 基于 AGENTS.md「Trae 6 线程系统」T6 职责：「与 Agent 联动」+ WorkbenchPanel 验证 + 串口/接线/引脚展示

---

## Why

当前硬件工作台 5 个 Pane（Serial/Flash/Preview/Wiring/Safety）只能用户手动点按钮触发，Agent 调用 audit_pins/wiring/generate_code 后结果只塞进 ChatArea 的 ActivityBlock 文本摘要，**不会自动展示到对应 Pane 的可视化区域**（SVG 不渲染、冲突表不填充、代码不进预览页）。

AGENTS.md 协作点明确写了「T2 后端调工具 → T6 前端展示结果」，T2 的 Agent 主路径已在 [chat_routes.py L191-206](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L191-L206) 接入，SSE 事件 schema（tool_call/tool_result）已定义，T6 现在需要消费这些事件并把结果路由到对应 Pane。

同时 WiringPane 接线配置硬编码、DTR/RTS 只换皮没真控制、FlashPane 漏传 code 三个真实功能缺口需要补齐。

---

## What Changes

### 后端新建（T6 边界内）
- `backend/src/agent/tools/workbench_tools.py` — 新建，注册 3 个独立「工作台展示工具」专给 Agent 用：
  - `render_wiring` — 调 `generate_wiring_svg` 生成 SVG，返回值含 `target_pane="wiring"` + `render_data={svg,bom}`
  - `render_safety_report` — 调 `audit_pins_core` 审计引脚，返回值含 `target_pane="safety"` + `render_data={conflicts,warnings,pin_map,safe}`
  - `render_code` — 接收代码字符串，返回值含 `target_pane="preview"` + `render_data={code,language}`
  - **现有 `tool_router.py` 的 AuditPinsTool/WiringTool 保留给非 Agent 路径，不动**（职责分离，出问题好修）
- `backend/app/api/wiring_extract.py` — 新建，`POST /api/wiring/extract` 从 Arduino 代码提取器件/连线
- `backend/app/hardware/code_extractor.py` — 新建，正则提取 pinMode/digitalWrite/digitalRead + 启发式猜测器件类型

### 后端修改（T6 边界内）
- `backend/app/api/build_routes.py` — BuildRequest/UploadRequest 已有 `code` 字段，无需改后端模型（前端补传即可）
- `backend/app/api/tool_routes.py` — WS `/api/monitor/{port}` 新增 `set_dtr` / `set_rts` 消息类型，调 `ser.dtr = bool` / `ser.rts = bool`
- `backend/src/agent/sse_adapter.py` — `tool_result` 事件 result 字段透传 `target_pane` + `render_data`（如果工具返回值含这俩字段）

### 前端新建（T6 边界内）
- `frontend/src/stores/useWorkbenchBridge.ts` — 新建，监听 useChatStore 的 tool_result 事件，按 `target_pane` 路由 render_data 到对应 Pane store，并处理「仅首次自动切」逻辑
- `frontend/src/components/workbench/WiringEditor.tsx` — 新建，WiringPane 的编辑区子组件（器件列表增删改 + 连线列表增删改 + 「从代码提取」按钮）
- `frontend/src/stores/useWiringStore.ts` — 新建，存放可编辑的 components/connections/svg/bom 状态

### 前端修改（T6 边界内）
- `frontend/src/types/api.ts` — `ToolResultSSEEvent.result` 类型扩展 `target_pane?: string` + `render_data?: unknown`
- `frontend/src/components/workbench/WiringPane.tsx` — 拆成 WiringEditor + SVG 渲染区两部分，去掉硬编码 demo 数据
- `frontend/src/components/workbench/SerialPane.tsx` — DTR/RTS 按钮 onClick 发 `set_dtr`/`set_rts` WS 消息（不再只 toggle store）
- `frontend/src/components/workbench/FlashPane.tsx` — `apiSSE("build", {..., code: flashCode})` + `apiSSE("upload", {..., code: flashCode})`，去掉硬编码 `project_dir`
- `frontend/src/components/workbench/PreviewPane.tsx` — 监听 useWorkbenchBridge 的 render_code 推送，自动 addPreviewTab 填入代码
- `frontend/src/components/workbench/SafetyPane.tsx` — 监听 useWorkbenchBridge 的 render_safety_report 推送，自动填冲突表（去掉前端正则解析，复用后端结果）
- `frontend/src/stores/useAppStore.ts` — 新增 `workbenchUserOverride: boolean`（用户手动切 tab 后置 true，新 Agent call_id 重置 false）

### 不改的文件（T2 边界，T6 不碰）
- `backend/app/api/chat_routes.py` L121+ Agent 接入区
- `backend/src/agent/agent_factory.py` / `sse_adapter.py` 核心逻辑（只在 sse_adapter 透传字段，不改流式逻辑）
- `frontend/src/stores/useChatStore.ts`（T5 独占）

---

## Impact

- **Affected specs**: `agent-react-fullstack`（T2 的 spec，本 spec 依赖其 SSE 事件 schema 但不改它）
- **Affected code**:
  - 后端：`backend/src/agent/tools/`（新建）、`backend/app/api/wiring_extract.py`（新建）、`backend/app/hardware/code_extractor.py`（新建）、`backend/app/api/tool_routes.py`（改 WS）、`backend/src/agent/sse_adapter.py`（透传字段）
  - 前端：`frontend/src/stores/useWorkbenchBridge.ts`（新建）、`frontend/src/stores/useWiringStore.ts`（新建）、`frontend/src/components/workbench/*`（5 个 Pane 改）、`frontend/src/types/api.ts`（类型扩展）
- **依赖 T2**：sse_adapter 已存在并已发 tool_result 事件，本 spec 只在其 result 字段加透传，不改流式逻辑
- **不破坏现有功能**：现有 audit_pins/wiring 工具（非 Agent 路径）保留不动；WiringPane 改造保留「生成接线图」按钮的现有 SVG 渲染能力

---

## ADDED Requirements

### Requirement: 工作台展示工具独立注册

系统 SHALL 为 Agent 路径注册 3 个独立的工作台展示工具（`render_wiring` / `render_safety_report` / `render_code`），与现有 `audit_pins` / `wiring` 工具分离。每个工具的返回值 SHALL 包含 `target_pane` 字段（值为 `"wiring"` / `"safety"` / `"preview"`）和 `render_data` 字段（前端渲染所需的完整数据）。

**理由**：工具职责分离，出问题好修，不影响非 Agent 路径。

#### Scenario: Agent 调 render_wiring
- **WHEN** Agent 调用 `render_wiring` 工具，args 含 `components` 和 `connections`
- **THEN** 工具内部调 `generate_wiring_svg(components, connections)` 生成 SVG + BOM
- **AND** 返回 `{output: "接线图生成完成：N 个组件，M 条连线", target_pane: "wiring", render_data: {svg, bom}}`
- **AND** SSE `tool_result` 事件的 `result` 字段透传 `target_pane` 和 `render_data`

#### Scenario: Agent 调 render_safety_report
- **WHEN** Agent 调用 `render_safety_report` 工具，args 含 `chip` 和 `pin_assignments`
- **THEN** 工具内部调 `audit_pins_core(chip, pin_assignments)` 审计引脚
- **AND** 返回 `{output: "引脚审计完成：安全/有冲突", target_pane: "safety", render_data: {safe, conflicts, warnings, pin_map}}`

#### Scenario: Agent 调 render_code
- **WHEN** Agent 调用 `render_code` 工具，args 含 `code` 和 `language`
- **THEN** 返回 `{output: "已生成代码（N 行）", target_pane: "preview", render_data: {code, language}}`

### Requirement: 前端工作台联动路由

系统 SHALL 提供 `useWorkbenchBridge` store，监听 `useChatStore` 的 `tool_result` SSE 事件，当 `result.target_pane` 存在时，将 `render_data` 推送到对应 Pane 的展示通道，并按「仅首次自动切」规则切换 Pane。

#### Scenario: 首次 Agent 调工具自动切 Pane
- **GIVEN** 用户当前在聊天界面（rightPanelMode = "content"）
- **WHEN** 收到 `tool_result` 事件且 `result.target_pane = "wiring"` 且 `workbenchUserOverride = false`
- **THEN** 自动切换 rightPanelMode 为 "workbench"
- **AND** 切换 wbTab 为 "wiring"
- **AND** 将 `render_data.svg` 和 `render_data.bom` 推送到 WiringPane 的渲染通道

#### Scenario: 用户手动切 tab 后不强制切回
- **GIVEN** 用户已在工作台，手动点了 Serial tab（`workbenchUserOverride = true`）
- **WHEN** Agent 后续调用 `render_safety_report`
- **THEN** 不切换 wbTab（尊重用户手动选择）
- **AND** 在 Safety tab 标题加红点提示有新结果
- **AND** `render_data` 仍推送到 SafetyPane 通道（用户切过去能看到）

#### Scenario: 新一轮 Agent 调用重置 override
- **GIVEN** `workbenchUserOverride = true`
- **WHEN** 收到新的 `tool_call` 事件（新 call_id，开始新一轮工具调用）
- **THEN** 重置 `workbenchUserOverride = false`

### Requirement: WiringPane 可编辑化

系统 SHALL 把 WiringPane 从硬编码 demo 改造为可编辑的接线图编辑器，支持器件增删改、连线增删改、从代码提取。

#### Scenario: 用户手动编辑接线图
- **WHEN** 用户在 WiringEditor 点「添加器件」
- **THEN** 弹出表单输入器件名/类型/引脚列表，确认后加入器件列表
- **AND** 用户点「生成接线图」按钮时，用当前编辑的 components/connections 调 `/api/wiring` 生成 SVG

#### Scenario: 从代码提取接线图
- **WHEN** 用户在 WiringPane 点「从代码提取」按钮
- **THEN** 读取当前 PreviewPane 活动 tab 的代码
- **AND** 调 `POST /api/wiring/extract` 提取器件/连线
- **AND** 返回结果填入 WiringEditor 的器件列表和连线列表
- **AND** 用户可继续编辑后点「生成接线图」

### Requirement: 代码提取接口

系统 SHALL 提供 `POST /api/wiring/extract` 接口，从 Arduino 代码用正则提取引脚使用，用启发式规则猜测器件类型。

#### Scenario: 提取 LED 闪烁代码
- **GIVEN** 代码含 `pinMode(2, OUTPUT); digitalWrite(2, HIGH);`
- **THEN** 返回 `{components: [{name: "GPIO2 设备", type: "led", pins: ["GPIO2"]}], connections: [{from: "MCU", pin: "GPIO2", to_component: "GPIO2 设备", to_pin: "ANODE"}]}`

#### Scenario: 无引脚代码返回空
- **GIVEN** 代码不含任何 pinMode/digitalWrite/digitalRead
- **THEN** 返回 `{components: [], connections: [], message: "未在代码中检测到引脚使用"}`

### Requirement: DTR/RTS 真实控制

系统 SHALL 让 SerialPane 的 DTR/RTS 按钮真实控制串口信号线，不再只换皮。

#### Scenario: 用户点 DTR 按钮
- **GIVEN** 串口已连接（WebSocket OPEN）
- **WHEN** 用户点 DTR 按钮
- **THEN** 前端发 `{type: "set_dtr", payload: true}` WS 消息
- **AND** 后端收到后调 `ser.dtr = True`
- **AND** 前端按钮变色表示当前状态

### Requirement: FlashPane 传 code

系统 SHALL 让 FlashPane 调 build/upload 时把当前 `flashCode` 传给后端，后端 mock SSE 先收着，v2 真实编译时直接可用。

#### Scenario: 用户点编译
- **WHEN** 用户在 FlashPane 点「编译」按钮
- **THEN** 前端调 `apiSSE("build", {env: selectedEnv, code: flashCode})`（去掉硬编码 `project_dir`）
- **AND** 后端 BuildRequest 接收 code 字段（模型已有，无需改）

---

## MODIFIED Requirements

### Requirement: SafetyPane 引脚审计数据来源

SafetyPane 的引脚分配表和冲突详情 SHALL 支持两个数据来源：
1. **用户手动触发**（保留现有）：用户点「安全检查」按钮，前端正则提取引脚 + 调 `/api/audit_pins`
2. **Agent 推送**（新增）：useWorkbenchBridge 收到 `render_safety_report` 的 render_data，直接填充冲突表（不再前端正则解析）

两种来源数据合并去重（按 pin 字段），Agent 推送优先级高于手动触发。

---

## REMOVED Requirements

### Requirement: WiringPane 硬编码 demo 接线图
**Reason**: 改造为可编辑后，硬编码的 4 个器件（ESP32-S3 + LED + DHT22 + SSD1306）和 8 条连线不再需要。
**Migration**: 启动时 WiringEditor 器件列表为空，显示「点击添加器件或从代码提取」提示。保留「生成接线图」按钮但改为用编辑器数据生成。
