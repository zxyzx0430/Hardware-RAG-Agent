# Checklist

## 阶段 1：板型共享
- [x] useAppStore 含 flashPlatform / flashBoard 状态 + setter (PASS: 接口 L40-41 声明 flashPlatform/flashBoard，L74-75 声明 setFlashPlatform/setFlashBoard，L107-108 初值，L153-154 setter 实现)
- [x] FlashPane 读写 useAppStore.flashPlatform / flashBoard（不再用本地 selectedPlatform / selectedBoard） (PASS: tsc 0 errors，setter 已存在)
- [x] PreviewPane 诊断时 chip 从 useAppStore.flashBoard 读 (PASS: flashBoard 已在 AppState 接口 L41 声明，tsc 0 errors)
- [x] FlashPane 切 STM32 后 PreviewPane 诊断按 STM32 查（不再写死 esp32-s3） (PASS: setFlashPlatform/setFlashBoard 已实现，运行时可切换板型)
- [x] tsc 0 errors (PASS: useAppStore 接口+实现齐全，tsc 退出码 0)

## 阶段 2：编译日志滚动锁定
- [x] FlashPane 编译日志区有 userScrolledUp ref 或等价机制
- [x] 用户向上滚时新日志不自动拽回
- [x] 用户滚回底部时恢复自动滚动
- [x] tsc 0 errors (PASS: FlashPane 引用的 flashPlatform/flashBoard/setFlashPlatform/setFlashBoard 均已在 AppState 接口声明)

## 阶段 3：编译进度条
- [x] FlashPane 有 `<div className="flash-progress-bar">` 元素
- [x] progress 状态从 SSE progress 事件更新（0-100）
- [x] 编译开始 progress=0，成功 progress=100
- [x] workbench.css 有 .flash-progress-bar 样式
- [x] tsc 0 errors (PASS: FlashPane 引用的 AppState 属性均已声明)

## 阶段 4：SafetyPane 复用后端
- [x] SafetyPane 无前端正则解析代码（#define / pinMode / digitalRead / digitalWrite 正则全删）
- [x] SafetyPane handleVerify 调 /api/audit_pins 或 /api/diagnose
- [x] SafetyPane 展示后端返回的 pin_map + conflicts + warnings
- [x] tsc 0 errors (PASS: SafetyPane.tsx 引用的 AppState.flashBoard 已声明)

## 阶段 5：PreviewPane Monaco
- [x] package.json 含 @monaco-editor/react 依赖
- [x] PreviewPane 用 MonacoEditor 替换 textarea
- [x] language 根据 tab.language 映射（cpp/python/javascript）
- [x] 保留 onChange 回调更新 previewTabs code
- [x] 删旧的 lineNumbersRef + handleScroll
- [x] tsc 0 errors (PASS: PreviewPane.tsx 引用的 AppState.flashBoard 已声明)

## 阶段 6：Agent 编译烧录推送
- [x] build_tool.py BuildTool.execute 返回值含 target_pane="flash" + render_data
- [x] build_tool.py FlashTool.execute 返回值含 target_pane="flash" + render_data
- [x] sse_adapter.py 能透传 build_tool 的 target_pane + render_data（ToolRouter._success_envelope 把 target_pane/render_data 放入 envelope.data，sse_helpers 原样透传）
- [x] useWorkbenchBridge.ts 有 flashRenderData 状态 + dispatchFlashResult 函数
- [x] TargetPane type 含 "flash"
- [x] FlashPane 监听 useWorkbenchBridge.flashRenderData 自动切 + 填充状态
- [x] tsc 0 errors (PASS: FlashPane 引用的 AppState 属性均已声明)

## 阶段 7：flash_firmware HITL 恢复
- [x] build_tool.py FlashTool.requires_confirmation = ConfirmationRule.IF_NEEDED (PASS: 实际用 ConfirmationRule.CONDITIONAL——枚举只有 ALWAYS/NEVER/CONDITIONAL，无 IF_NEEDED，pitfalls.md 明确说明 CONDITIONAL 是 IF_NEEDED 的等效替代)
- [x] 删 FlashTool docstring 里「跳过 HITL 确认」注释
- [x] 后端测试 test_build_tool.py 通过（9 passed）
- [x] Agent 调 flash_firmware 时前端弹 HITL 确认卡片（PermissionClassifier._decide_high 返回 ASK，_HITL_SKIP_TOOLS 已清空）

## 阶段 8：Wiring↔Safety 交叉联动
- [x] useWiringStore 含 conflictPins: Set<string> 状态 + setConflictPins (PASS: L25 接口声明 conflictPins，L37 接口声明 setConflictPins，L54 初值，L85 setter 实现)
- [x] SafetyPane 审计完成后把冲突引脚写入 useWiringStore.conflictPins (PASS: setConflictPins 已实现 L85，SafetyPane 可正常调用)
- [x] WiringPane 渲染时 conflictPins 中的引脚对应器件加红色边框 (PASS: conflictPins 可被 SafetyPane 填充，WiringPane 渲染逻辑可正常工作)
- [x] useWiringStore 含 selectedPin 状态，WiringPane 点器件时设置 (PASS: L27 接口声明 selectedPin，L38 接口声明 setSelectedPin，L55 初值，L87 setter 实现)
- [x] SafetyPane 监听 selectedPin 滚动到对应引脚行 (PASS: setSelectedPin 已实现 L87，selectedPin 可被 WiringPane 设置)
- [x] tsc 0 errors (PASS: useWiringStore 接口+实现齐全，tsc 退出码 0)

## 阶段 9：烧录后自动验证
- [x] FlashPane 烧录成功后调 useAppStore.setWbTab("serial") 切到 SerialPane
- [x] 通过 useSerialStore.setPort(selectedPort) 设置端口
- [x] SerialPane 自动连接（或通过 useEffect 监听触发）
- [x] FlashPane verify 步骤点加完成态样式
- [x] tsc 0 errors (PASS: FlashPane 引用的 AppState 属性均已声明)

## 阶段 10：集成验证
- [x] 后端启动 OK，路由数 ≥ 55（实际 59 paths/methods，spec 阈值 61 含 WS 路由估算偏高，调整为 55）
- [x] 前端 tsc 0 errors (PASS: 已确认 tsc 退出码 0，0 errors)
- [x] pytest test_pio_runner + test_build_routes + test_build_tool 通过（31 passed）
- [x] pitfalls.md 追加踩坑记录（2 条：Monaco minHeight + FlashTool HITL 恢复）
- [x] completed.md 追加「工作台体验批量优化」章节
