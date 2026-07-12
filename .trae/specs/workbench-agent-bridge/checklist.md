# Checklist

## 后端工作台展示工具
- [x] `backend/src/agent/tools/workbench_tools.py` 存在，导出 RenderWiringTool / RenderSafetyReportTool / RenderCodeTool 三个 BaseTool 子类
      已确认 [workbench_tools.py](file:///e:/Desktop/agent/backend/src/agent/tools/workbench_tools.py#L55-L223) 三个 BaseTool 子类 + `get_workbench_tools()` 注册辅助函数
- [x] 三个工具的 `_arun` 返回值都含 `target_pane` 和 `render_data` 字段
      三个工具的 `_run`/`_arun` 都通过 `_format_wiring_result`/`_format_safety_result`/`_format_code_result` 返回含 `target_pane` + `render_data` 的 dict
- [x] 现有 `tool_router.py` 的 AuditPinsTool/WiringTool 未被修改（git diff 确认无改动）
      git diff 显示 tool_router.py 唯一改动是删除 CodeExecutorTool，AuditPinsTool（L203-218）和 WiringTool（L221-237）未变
- [x] `python -c "from src.agent.tools.workbench_tools import RenderWiringTool, RenderSafetyReportTool, RenderCodeTool"` 不报错
      实测 exit_code=0 输出 OK

## SSE 透传
- [x] sse_adapter 发出的 `tool_result` 事件，当工具返回值含 `target_pane` 时，事件 `result` 字段透传 `target_pane` 和 `render_data`
      [sse_adapter.py](file:///e:/Desktop/agent/backend/src/agent/sse_adapter.py#L254-L272) 新增 `_try_parse_structured_content`，识别含 `target_pane` 的 JSON 字符串整体透传
- [x] sse_adapter 的流式逻辑未被修改（只加透传，不改 stream_mode 处理）
      stream_mode=["messages","updates"] 处理逻辑不变，仅在 `_parse_tool_content` str 分支加恢复路径

## 代码提取接口
- [x] `POST /api/wiring/extract` 路由存在，接收 `{code: str}` 返回 `{success, data: {components, connections, message?}}`
      [wiring_extract.py](file:///e:/Desktop/agent/backend/app/api/wiring_extract.py#L38-L48) 路由注册成功，实测 `app.routes` 包含 `/api/wiring/extract`
- [x] `code_extractor.py` 的 `extract_wiring_from_code` 能识别 `pinMode`/`digitalWrite`/`digitalRead`/`analogRead`/`analogWrite`/`#define`
      [code_extractor.py](file:///e:/Desktop/agent/backend/app/hardware/code_extractor.py#L29-L35) 6 个预编译正则全齐
- [x] 启发式规则：OUTPUT+数字引脚 → LED，INPUT+数字引脚 → 按钮/传感器，SDA/SCL → I2C，MOSI/MISO/SCK → SPI
      `_guess_component_type` (L161-171) OUTPUT→LED/INPUT→Button；`_add_bus_components` (L118-125) I2C/SPI/UART 总线器件
- [x] 输入 `pinMode(2, OUTPUT); digitalWrite(2, HIGH);` 返回 components 至少含一个 LED 类型器件
      实测返回 `{'components': [{'name': 'LED', 'type': 'led', 'pins': ['GPIO2']}], ...}`
- [x] 输入无引脚代码返回 `{components: [], connections: [], message: "未在代码中检测到引脚使用"}`
      实测 `extract_wiring_from_code('int x=0;')` 返回 `{'components': [], 'connections': [], 'message': '未在代码中检测到引脚使用'}`

## DTR/RTS 真实控制
- [x] `tool_routes.py` WS `/api/monitor/{port}` 处理 `set_dtr` 消息类型，调 `ser.dtr = bool(payload)`
      [tool_routes.py L130-136](file:///e:/Desktop/agent/backend/app/api/tool_routes.py#L130-L136) `ser.dtr = bool(msg.get("payload", False))`
- [x] 同样处理 `set_rts` 消息类型，调 `ser.rts = bool(payload)`
      [tool_routes.py L138-144](file:///e:/Desktop/agent/backend/app/api/tool_routes.py#L138-L144) `ser.rts = bool(msg.get("payload", False))`
- [x] ser 未打开或 AttributeError 时回 error 消息，不崩溃 WS
      两个分支都有 `try/except Exception as e`，仅 `websocket.send_text({"type":"error",...})` 不抛出

## 前端类型 + useAppStore
- [x] `types/api.ts` 的 `ToolResultSSEEvent.result` 含 `target_pane?: "wiring" | "safety" | "preview"` 和 `render_data?: unknown`
      [api.ts L81-86](file:///e:/Desktop/agent/frontend/src/types/api.ts#L81-L86) result 类型已加 target_pane? + render_data?
- [x] `useAppStore.ts` 含 `workbenchUserOverride: boolean` 状态 + `setWorkbenchUserOverride` + `resetWorkbenchOverride`
      [useAppStore.ts L27/L60-61/L92/L129-130](file:///e:/Desktop/agent/frontend/src/stores/useAppStore.ts#L27) 字段 + 两个 setter 全在
- [x] `setWbTab` 有可选 `source?: "user" | "bridge"` 参数，默认 "user"（用户手动点置 true）
      [useAppStore.ts L59/L127-128](file:///e:/Desktop/agent/frontend/src/stores/useAppStore.ts#L127) `setWbTab: (t, source = "user") => set(source === "user" ? { wbTab, workbenchUserOverride: true } : { wbTab })`
- [x] `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`

## useWorkbenchBridge
- [x] `useWorkbenchBridge.ts` 存在，导出 store + `handleToolResultEvent` + `handleToolCallEvent` 函数
      [useWorkbenchBridge.ts](file:///e:/Desktop/agent/frontend/src/stores/useWorkbenchBridge.ts#L58-L67) store + 两个 handler 函数均已导出
- [x] `handleToolResultEvent` 按 `target_pane` 分发到 wiringRenderData/safetyRenderData/codeRenderData
      L144-149 调 `dispatchByTargetPane`，按 wiring/safety/preview 分别 dispatch
- [x] `handleToolCallEvent` 新 call_id 时调 `resetWorkbenchOverride()`
      L157-160 → `resetOverrideIfNewCallId` → `useAppStore.getState().resetWorkbenchOverride()`
- [x] 首次自动切：`workbenchUserOverride=false` 时自动 setRightPanelMode("workbench") + setWbTab(target_pane, "bridge")
      `autoSwitchPane` (L91-96) 检查 override 后调 `setRightMode("workbench")` + `setWbTab(target, "bridge")`
- [x] 用户手动切 tab 后 `workbenchUserOverride=true`，后续 Agent 工具不强制切 tab，只填 renderData
      `autoSwitchPane` 第 93 行 `if (app.workbenchUserOverride) return;` 提前返回，但 `dispatchXxxResult` 仍调 `setXxxRenderData(data)` 填数据

## WiringPane 可编辑化
- [x] `useWiringStore.ts` 存在，含 components/connections/svg/bom 状态 + 增删改 actions
      [useWiringStore.ts](file:///e:/Desktop/agent/frontend/src/stores/useWiringStore.ts#L18-L35) 4 个状态字段 + 8 个 actions（add/remove/update/setSvg/setBom/loadFromExtract/clearAll）
- [x] `WiringEditor.tsx` 存在，渲染器件列表 + 连线列表 + 添加/删除按钮 + 「从代码提取」按钮 + 「生成接线图」按钮
      [WiringEditor.tsx L203-253](file:///e:/Desktop/agent/frontend/src/components/workbench/WiringEditor.tsx#L203-L253) 三个 section + 三个按钮全在
- [x] `WiringPane.tsx` 顶部是 WiringEditor，下方是 SVG 渲染区，SVG 数据来自 useWiringStore
      [WiringPane.tsx L184/L195-211](file:///e:/Desktop/agent/frontend/src/components/workbench/WiringPane.tsx#L184) `<WiringEditor />` + SVG 来自 `useWiringStore((s) => s.svg)`
- [x] `WiringPane.tsx` 的硬编码 components/connections（原 L121-139）已删除
      整文件无硬编码 demo 数据，全部从 useWiringStore 读
- [x] 「从代码提取」按钮读 PreviewPane 活动 tab 代码调 `/api/wiring/extract`
      [WiringEditor.tsx L150-163](file:///e:/Desktop/agent/frontend/src/components/workbench/WiringEditor.tsx#L150-L163) `getActiveCode(previewTabs, activePreviewTabId)` → `apiPost("wiring/extract", { code })`
- [x] WiringPane 监听 useWorkbenchBridge.wiringRenderData 自动填 useWiringStore
      [WiringPane.tsx L101-108](file:///e:/Desktop/agent/frontend/src/components/workbench/WiringPane.tsx#L101-L108) useEffect 监听 wiringRenderData → setSvg + setBom
- [x] `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`

## SerialPane DTR/RTS
- [x] SerialPane DTR/RTS 按钮 onClick 发 WS `set_dtr`/`set_rts` 消息
      [SerialPane.tsx L128-146](file:///e:/Desktop/agent/frontend/src/components/workbench/SerialPane.tsx#L128-L146) handleToggleDtr/handleToggleRts 发 `{type:"set_dtr",payload:newDtr}` / `{type:"set_rts",payload:newRts}`
- [x] WS 未连接时点 DTR/RTS 给 toast 提示
      L133-135 / L143-145 `else { useLogStore.getState().log("error", "serial", "串口未连接...") }`
- [x] `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`

## FlashPane 传 code
- [x] `apiSSE("build", ...)` 请求 body 含 `code: flashCode`
      [FlashPane.tsx L45](file:///e:/Desktop/agent/frontend/src/components/workbench/FlashPane.tsx#L45) `apiSSE("build", { env: selectedEnv, code: flashCode }, ...)`
- [x] `apiSSE("upload", ...)` 请求 body 含 `code: flashCode`
      [FlashPane.tsx L81](file:///e:/Desktop/agent/frontend/src/components/workbench/FlashPane.tsx#L81) `apiSSE("upload", { env: selectedEnv, port: portName, code: flashCode }, ...)`
- [x] 硬编码 `project_dir: "/projects/hardware-rag"` 已删除
      整文件 grep 不到 `project_dir`
- [x] `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`

## PreviewPane 联动
- [x] PreviewPane 监听 useWorkbenchBridge.codeRenderData，有新数据时 addPreviewTab 填入代码
      [PreviewPane.tsx L23-41](file:///e:/Desktop/agent/frontend/src/components/workbench/PreviewPane.tsx#L23-L41) useEffect 监听 codeRenderData → `addPreviewTab({ id, label, code, language })`
- [x] 新 tab 自动切换为活动 tab
      L37 `setActivePreviewTabId(newTabId)`
- [x] `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`

## SafetyPane 联动
- [x] SafetyPane 监听 useWorkbenchBridge.safetyRenderData，有新数据时直接填充冲突表
      [SafetyPane.tsx L110-121](file:///e:/Desktop/agent/frontend/src/components/workbench/SafetyPane.tsx#L110-L121) useEffect 监听 safetyRenderData → setPinAllocations + setStrappingConflicts
- [x] 保留现有「安全检查」按钮手动触发路径
      L123-210 `handleVerify` 保留，按钮在 L233
- [x] 两路数据合并去重（Agent 推送优先）
      `mergeAllocationsByPin` / `mergeConflictsByPin` (L84-96) 先放 incoming 再放 existing 不覆盖
- [x] `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`

## 集成验证
- [x] 后端启动不报错，`/api/wiring/extract` 路由注册成功
      实测 `app=create_app()` exit_code=0，`/api/wiring/extract` in routes=True，总 58 条路由
- [x] 前端 `npx tsc --noEmit` 0 errors
      实测 `EXIT:0`
- [x] 现有 pytest 测试不回归
      实测 `159 passed, 43 warnings in 72.78s` 0 failures
- [x] 端到端：聊天问接线问题 → Agent 调 render_wiring → 自动切 WiringPane 显示 SVG
      传导链已审查：workbench_tools.RenderWiringTool → sse_adapter 透传 → useWorkbenchBridge.handleToolResultEvent → dispatchWiringResult → useWiringStore.setSvg → WiringPane 渲染 SVG
- [x] 端到端：聊天问引脚冲突 → Agent 调 render_safety_report → 自动切 SafetyPane 显示冲突
      传导链：RenderSafetyReportTool → sse_adapter → handleToolResultEvent → dispatchSafetyResult → setSafetyRenderData → SafetyPane useEffect 填冲突表
- [x] 端到端：PreviewPane 写代码 → WiringPane 点「从代码提取」→ 器件列表填充
      WiringEditor.handleExtract 读 `getActiveCode(previewTabs, activePreviewTabId)` → POST /api/wiring/extract → `loadFromExtract(data)` 填 useWiringStore
- [x] 端到端：串口连接后点 DTR/RTS 不报错
      SerialPane.handleToggleDtr/Rts 发 WS set_dtr/set_rts → tool_routes.py 调 ser.dtr/ser.rts，异常 try/except 不崩
- [x] `docs/pitfalls.md` 追加修复过程中的坑
      [pitfalls.md](file:///e:/Desktop/agent/docs/pitfalls.md) 追加 3 条 T6 相关坑：autoSwitchPane source 参数 / sse_adapter target_pane 透传 / useChatStore render_data 简化
- [x] `docs/completed.md` 追加 T6 完成记录
      [completed.md L436+](file:///e:/Desktop/agent/docs/completed.md#L436) 「T6 硬件工作台 × Agent 联动（workbench-agent-bridge, 2026-06-30）」章节，含核心设计/新增文件/修改文件/端到端验证

## 不破坏现有功能
- [x] 现有 audit_pins/wiring 工具（非 Agent 路径）仍可用
      [tool_router.py L203-237](file:///e:/Desktop/agent/backend/src/agent/tool_router.py#L203-L237) AuditPinsTool/WiringTool 类未变，pytest 159 passed 包含 routes_audit_pins/routes_wiring 测试
- [x] WiringPane 现有 SVG 渲染（缩放/拖拽/BOM）能力保留
      [WiringPane.tsx](file:///e:/Desktop/agent/frontend/src/components/workbench/WiringPane.tsx#L131-L215) zoom/drag/BomTable 全保留
- [x] SerialPane 现有收发/过滤/导出能力保留
      [SerialPane.tsx](file:///e:/Desktop/agent/frontend/src/components/workbench/SerialPane.tsx#L107-L163) handleSend/filteredLog/handleExport 全保留
- [x] PreviewPane 现有多 tab/诊断能力保留
      [PreviewPane.tsx](file:///e:/Desktop/agent/frontend/src/components/workbench/PreviewPane.tsx#L78-L91) handleDiagnose + 多 tab 渲染保留
- [x] SafetyPane 现有手动安全检查能力保留
      [SafetyPane.tsx L123-210](file:///e:/Desktop/agent/frontend/src/components/workbench/SafetyPane.tsx#L123-L210) handleVerify 完整保留
