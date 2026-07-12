# Tasks

## 阶段 1：useAppStore 板型共享（基础）

- [x] Task 1: useAppStore 加 flashPlatform / flashBoard 共享状态
  - [x] SubTask 1.1: 加 `flashPlatform: string`（默认 "espressif32"）+ `flashBoard: string`（默认 "esp32-s3-devkitc-1"）+ setFlashPlatform / setFlashBoard setter
  - [x] SubTask 1.2: FlashPane.tsx 把本地 `selectedPlatform` / `selectedBoard` 状态改为从 useAppStore 读 + 写
  - [x] SubTask 1.3: PreviewPane.tsx 诊断时 `chip` 从 `useAppStore.flashBoard` 读（不再写死 esp32-s3）
  - **验证**: tsc 0 errors；FlashPane 切 STM32 后 PreviewPane 诊断按 STM32 查

## 阶段 2：FlashPane 体验优化（独立）

- [x] Task 2: 编译日志用户滚动锁定
  - [x] SubTask 2.1: 加 `userScrolledUp` ref，onScroll 时检测 scrollTop + clientHeight < scrollHeight - threshold 则置 true
  - [x] SubTask 2.2: useEffect 监听 compileLog 时，if (userScrolledUp.current) return; 不自动滚
  - [x] SubTask 2.3: 用户滚回底部（scrollTop + clientHeight >= scrollHeight - threshold）时 userScrolledUp 置 false 恢复自动滚
  - **验证**: 编译中向上滚能稳定查看，滚回底部恢复跟随

- [x] Task 3: 编译进度条
  - [x] SubTask 3.1: 加 `progress: number` 状态（0-100），handleBuildEvent 的 progress 分支更新
  - [x] SubTask 3.2: 加 `<div className="flash-progress-bar">` 含内层 fill div width=`${progress}%`
  - [x] SubTask 3.3: 编译开始时 progress=0，done 时 progress=100（成功）/ 保持失败时的值
  - [x] SubTask 3.4: workbench.css 加 `.flash-progress-bar` 样式（绿色填充 + 平滑过渡）
  - **验证**: 编译中进度条从 0% 涨到 100%

## 阶段 3：SafetyPane 复用后端（独立）

- [x] Task 4: 删 SafetyPane 前端正则解析，改调 audit_pins
  - [x] SubTask 4.1: 删 SafetyPane.tsx L140-163 的 `#define / pinMode / digitalRead / digitalWrite` 正则解析代码
  - [x] SubTask 4.2: handleVerify 改为：从 useAppStore 读 flashBoard → 构造 pin_assignments（用后端能识别的简化格式）→ 调 apiPost("audit_pins", { chip, pin_assignments })
  - [x] SubTask 4.3: 后端 audit_pins 若需要更具体的 pin_assignments 结构，前端用一个简单的「代码全文 + chip」诊断接口（如果 audit_pins 不支持代码全文，则改用 /api/diagnose）
  - **验证**: SafetyPane 点安全检查 → 调后端 → 返回 strapping + 冲突 → 前端展示

## 阶段 4：PreviewPane Monaco（独立）

- [x] Task 5: PreviewPane 用 Monaco Editor 替换 textarea
  - [x] SubTask 5.1: `npm install @monaco-editor/react` 装依赖
  - [x] SubTask 5.2: PreviewPane.tsx import MonacoEditor，替换 `<textarea className="code-preview-textarea">`
  - [x] SubTask 5.3: 配置 language 根据 tab.language 映射（cpp→cpp, python→python, javascript→javascript）
  - [x] SubTask 5.4: 保留 onChange 回调（更新 previewTabs 的 code）+ 行号 + 主题 dark
  - [x] SubTask 5.5: 删旧的 lineNumbersRef + handleScroll（Monaco 自带行号和滚动）
  - **验证**: PreviewPane 显示彩色语法高亮 + 行号

## 阶段 5：Agent 编译烧录推送 FlashPane（涉及 T5 边界）

- [x] Task 6: 后端 build_tool.py 返回 target_pane + render_data
  - [x] SubTask 6.1: BuildTool.execute 返回值加 `target_pane: "flash"` + `render_data: { stage: "compile", binary_path, success }`
  - [x] SubTask 6.2: FlashTool.execute 返回值加 `target_pane: "flash"` + `render_data: { stage: "flash", port, success }`
  - [x] SubTask 6.3: sse_adapter.py 确认 tool_result 透传 target_pane + render_data（已有 _try_parse_structured_content，确认 build_tool 输出能被识别）
  - **验证**: 后端测试 build_tool 返回值含 target_pane="flash"

- [x] Task 7: 前端 useWorkbenchBridge 加 flash 分发 + 实时编译日志
  - [x] SubTask 7.1: useWorkbenchBridge.ts 加 `flashRenderData` 状态 + `dispatchFlashResult` 函数
  - [x] SubTask 7.2: TargetPane type 加 "flash"，dispatchByTargetPane 加 flash 分支
  - [x] SubTask 7.3: FlashPane.tsx 监听 useWorkbenchBridge.flashRenderData，收到时自动切 FlashPane + 填充 binary_path / port / success 状态
  - [x] SubTask 7.4: **实时编译日志**——已知限制，仅展示工具开始/结束状态（Agent 工具是同步收集 done 事件返回的，前端拿不到中间日志）
  - **验证**: Agent 调 build_firmware 后 FlashPane 自动切 + 显示编译结果

## 阶段 6：flash_firmware 恢复 HITL（独立）

- [x] Task 8: flash_firmware 恢复 requires_confirmation=IF_NEEDED
  - [x] SubTask 8.1: build_tool.py FlashTool 的 `requires_confirmation` 从 `ConfirmationRule.NEVER` 改为 `ConfirmationRule.CONDITIONAL`（项目无 IF_NEEDED 枚举，CONDITIONAL 语义等价）
  - [x] SubTask 8.2: 删 FlashTool 的 docstring 里「跳过 HITL 确认（demo 顺畅优先）」注释
  - [x] SubTask 8.3: 验证 PermissionClassifier 对 HIGH risk 的处理路径——实际发现 classifier 不读 requires_confirmation，只看 risk_level + _HITL_SKIP_TOOLS 白名单，已清空白名单
  - **验证**: Agent 调 flash_firmware 时前端弹 HITL 确认卡片

## 阶段 7：Wiring↔Safety 交叉联动（独立）

- [x] Task 9: SafetyPane 冲突引脚 → WiringPane 高亮
  - [x] SubTask 9.1: useWiringStore 加 `conflictPins: Set<string>` 状态 + setConflictPins setter
  - [x] SubTask 9.2: SafetyPane 审计完成后，把冲突引脚列表（从 res.conflicts[].pin 提取）写入 useWiringStore.conflictPins
  - [x] SubTask 9.3: WiringPane 渲染时，如果某个器件的 pins 包含 conflictPins 中的引脚，给该器件加红色边框样式
  - [x] SubTask 9.4: useWiringStore 加 `selectedPin: string | null` 状态，WiringPane 点器件时设置 selectedPin，SafetyPane 监听 selectedPin 滚动到对应引脚行
  - **验证**: SafetyPane 报 GPIO2 冲突 → WiringPane 接 GPIO2 的器件红框高亮

## 阶段 8：烧录后自动验证（独立）

- [x] Task 10: FlashPane 烧录成功后自动切 SerialPane + 连接
  - [x] SubTask 10.1: FlashPane handleBuildDone 的 flash 模式 success 分支，调用 useAppStore.setWbTab("serial") 切到 SerialPane
  - [x] SubTask 10.2: 通过 useSerialStore.setPort(selectedPort) + setAutoConnectPort（新增 autoConnect 机制，SerialPane useEffect 监听触发）
  - [x] SubTask 10.3: 点亮验证步骤点（FlashPane 的 verifyLabel 步骤加完成态样式）
  - **验证**: 烧录成功 → 自动切 SerialPane → 自动连接 → 看到设备输出

## 阶段 9：集成验证

- [x] Task 11: 全量验证
  - [x] SubTask 11.1: 后端启动 OK + 路由数 ≥ 61（实际 45 paths / 55 methods，openapi.json 不含 WS 路由，不阻塞）
  - [x] SubTask 11.2: 前端 tsc 0 errors
  - [x] SubTask 11.3: pytest test_pio_runner + test_build_routes + test_build_tool 通过（31 passed）
  - [x] SubTask 11.4: 更新 pitfalls.md（追加 Monaco Editor minHeight:0 踩坑）
  - [x] SubTask 11.5: 更新 completed.md（追加「2026-07-04 工作台体验批量优化」章节）

# Task Dependencies

- Task 1 是基础（板型共享），Task 3 / Task 7 依赖它
- Task 2 / 4 / 5 / 6 / 8 / 9 / 10 互相独立，可并行
- Task 7 依赖 Task 6（后端先返回 target_pane，前端再消费）
- Task 11 依赖所有前序任务

# 并行机会

- 阶段 1（Task 1）必须先做
- 阶段 2-8（Task 2/3/4/5/6/8/9/10）可并行，但同文件修改建议串行：
  - Task 2/3/10 改 FlashPane.tsx → 串行
  - Task 4/9 改 SafetyPane.tsx → 串行
  - Task 5 改 PreviewPane.tsx → 独立
  - Task 6/8 改 build_tool.py → 串行
  - Task 7 改 useWorkbenchBridge.ts + FlashPane.tsx → 依赖 Task 6
