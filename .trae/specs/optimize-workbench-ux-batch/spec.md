# 工作台体验批量优化 Spec

## Why
工作台 5 个 Pane（Serial / Preview / Wiring / Safety / Flash）虽然功能闭环，但有 10 个真实缺口影响"能用"和"好用"：编译日志拽回滚动、SafetyPane 重复解析、PreviewPane 板型写死、Agent 编译烧录不推 FlashPane、flash_firmware 跳过 HITL、代码无高亮、无进度条、Wiring↔Safety 不联动、烧录后无验证。

## What Changes
- **MODIFIED** FlashPane 编译日志滚动：用户向上滚时停止自动滚（尊重 user_profile 流式输出原则）
- **MODIFIED** SafetyPane 删前端正则解析，复用后端 `/api/audit_pins`（消除重复逻辑）
- **MODIFIED** PreviewPane 诊断 env 从 useAppStore 读（不再写死 esp32-s3）
- **ADDED** useAppStore 加 `flashPlatform` / `flashBoard` 共享状态，FlashPane + PreviewPane 共用
- **ADDED** Agent build_firmware / flash_firmware 工具返回 `target_pane="flash"` + `render_data`，前端实时流式展示编译日志到 FlashPane
- **MODIFIED** flash_firmware 恢复 `requires_confirmation=IF_NEEDED`（HIGH risk 走 HITL）
- **ADDED** PreviewPane 用 Monaco Editor 替换 textarea（语法高亮 + 行号）
- **ADDED** FlashPane 编译进度条（可视化 percent）
- **ADDED** WiringPane 器件引脚 → SafetyPane 交叉联动（点器件高亮冲突引脚）
- **ADDED** FlashPane 烧录成功后自动切 SerialPane + 连接同端口（验证步骤）

## Impact
- Affected specs: workbench-agent-bridge / agent-build-flash-esp32 / fix-serial-real-hardware
- Affected code:
  - 前端：`FlashPane.tsx` / `PreviewPane.tsx` / `SafetyPane.tsx` / `WiringPane.tsx` / `useAppStore.ts` / `useWorkbenchBridge.ts`
  - 后端：`build_tool.py`（Agent 工具返回 target_pane + HITL 恢复）/ `sse_adapter.py`（build 事件透传）
  - 新增依赖：`@monaco-editor/react`

## ADDED Requirements

### Requirement: 编译日志用户滚动锁定
The system SHALL stop auto-scrolling the compile log when the user scrolls up, and resume auto-scroll when the user scrolls back to the bottom.

#### Scenario: 用户向上滚查看早期报错
- **WHEN** 用户在编译日志区向上滚动
- **THEN** 新日志到达时不自动滚到底，用户能稳定查看早期内容

#### Scenario: 用户滚回底部
- **WHEN** 用户滚回日志底部
- **THEN** 恢复自动滚动，新日志到达时自动跟随

### Requirement: SafetyPane 复用后端 audit_pins
The system SHALL call `/api/audit_pins` for safety verification instead of frontend regex parsing.

#### Scenario: 用户点安全检查
- **WHEN** 用户在 SafetyPane 点「安全检查」按钮
- **THEN** 调用 `/api/audit_pins`（含 strapping + INPUT/OUTPUT 冲突 + 多处使用检测），前端不再写正则

### Requirement: 板型/platform 全局共享
The system SHALL share `flashPlatform` / `flashBoard` in useAppStore so FlashPane and PreviewPane use the same chip context.

#### Scenario: FlashPane 切 STM32
- **WHEN** 用户在 FlashPane 选 platform=ststm32
- **THEN** PreviewPane 诊断也按 STM32 查 strapping 表（不再写死 esp32-s3）

### Requirement: Agent 编译烧录实时推送 FlashPane
The system SHALL route Agent build_firmware / flash_firmware tool events to FlashPane in real-time, showing compile logs as they stream.

#### Scenario: Agent 编译代码
- **WHEN** Agent 调 build_firmware 工具
- **THEN** FlashPane 自动切换显示 + 实时滚动展示编译日志 + 进度条更新

### Requirement: flash_firmware 恢复 HITL 确认
The system SHALL require user confirmation before flashing firmware to a device (HIGH risk operation).

#### Scenario: Agent 调 flash_firmware
- **WHEN** Agent 调 flash_firmware 工具
- **THEN** 弹 HITL 确认卡片，用户点「允许」后才执行烧录

### Requirement: PreviewPane Monaco 语法高亮
The system SHALL render code with Monaco Editor (syntax highlighting + line numbers) instead of plain textarea.

#### Scenario: 用户查看代码
- **WHEN** 用户在 PreviewPane 查看/编辑代码
- **THEN** 看到 cpp/python/js 语法高亮 + 行号

### Requirement: FlashPane 编译进度条
The system SHALL show a visual progress bar (0-100%) during compilation, driven by SSE progress events.

#### Scenario: 编译中
- **WHEN** 收到 progress SSE 事件含 percent=45
- **THEN** 进度条显示 45%，文案显示「编译中... 45%」

### Requirement: Wiring↔Safety 交叉联动
The system SHALL highlight conflicting pins in WiringPane when SafetyPane reports conflicts, and let users click a component in WiringPane to jump to its pin in SafetyPane.

#### Scenario: SafetyPane 报告 GPIO2 冲突
- **WHEN** SafetyPane 审计出 GPIO2 冲突
- **THEN** WiringPane 中接 GPIO2 的器件高亮红色边框

### Requirement: 烧录后自动验证
The system SHALL auto-switch to SerialPane and connect to the same port after a successful flash, so users see device boot output immediately.

#### Scenario: 烧录成功
- **WHEN** flash_firmware 返回 success=true
- **THEN** 自动切到 SerialPane + 连接同端口 + 点亮验证步骤点

## MODIFIED Requirements

### Requirement: flash_firmware 权限门控
[Original: requires_confirmation=NEVER, demo 顺畅优先跳过 HITL]
**Modified**: requires_confirmation=IF_NEEDED，HIGH risk 走 HITL 确认卡片。审计日志仍记录 HIGH。用户点「允许」后执行，点「拒绝」返回 cancelled 错误。

### Requirement: SafetyPane 手动审计
[Original: 前端正则解析 #define/pinMode/digitalRead/digitalWrite 提取引脚 + 调 audit_pins]
**Modified**: 删前端正则解析，直接把活动 tab 代码传给 `/api/audit_pins` 的 `pin_assignments`（后端已有更全面解析）。前端只负责展示后端返回的 pin_map + conflicts + warnings。

## REMOVED Requirements

### Requirement: SafetyPane 前端正则解析
**Reason**: 与后端 `/api/diagnose` + `/api/audit_pins` 逻辑重复，前端解析是简化版会漏报（缺 strapping + INPUT/OUTPUT 冲突检测）
**Migration**: 删除 SafetyPane.tsx L140-163 的正则解析代码，改用 `apiPost("audit_pins", { chip, pin_assignments })` 后端解析
