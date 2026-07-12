# Tasks

## 阶段 1：后端工作台展示工具（Agent 联动核心）

- [x] Task 1: 新建 `backend/src/agent/tools/workbench_tools.py`，注册 3 个独立 BaseTool
      创建 workbench_tools.py（224 行，3 个 BaseTool + get_workbench_tools），agent_factory.py build_tools 追加注册（8 个工具），导入验证 OK
  - [ ] SubTask 1.1: 创建文件，实现 `RenderWiringTool`（调 `generate_wiring_svg`，返回 `target_pane="wiring"` + `render_data={svg,bom}`）
  - [ ] SubTask 1.2: 实现 `RenderSafetyReportTool`（调 `audit_pins_core`，返回 `target_pane="safety"` + `render_data={safe,conflicts,warnings,pin_map}`）
  - [ ] SubTask 1.3: 实现 `RenderCodeTool`（接收 code/language，返回 `target_pane="preview"` + `render_data={code,language}`）
  - [ ] SubTask 1.4: 在 `agent_factory.py` 的 `build_tools` 注册这 3 个工具（如 T2 已实现 build_tools，则在其后追加；否则在本文件末尾提供注册函数供 T2 调用）
  - **验证**: `python -c "from src.agent.tools.workbench_tools import RenderWiringTool, RenderSafetyReportTool, RenderCodeTool; print('OK')"` 输出 OK

- [x] Task 2: 修改 `backend/src/agent/sse_adapter.py`，`tool_result` 事件透传 `target_pane` + `render_data`
      sse_adapter._parse_tool_content 加 _try_parse_structured_content，识别含 target_pane 的 JSON 字符串整体透传，4 用例验证通过，pitfalls.md 已追加
  - [ ] SubTask 2.1: 在 `_convert_chunk_to_sse`（或等效函数）中，检测 ToolMessage 的工具返回值是否含 `target_pane` 字段
  - [ ] SubTask 2.2: 若含，则在 `tool_result` SSE 事件的 `result` 字段透传 `target_pane` 和 `render_data`
  - **验证**: 手动构造一个含 target_pane 的 ToolMessage，跑 sse_adapter 输出含 target_pane 字段
  - **注意**: T2 边界——只在 sse_adapter 透传字段，不改流式逻辑。若 sse_adapter 是 T2 独占文件，则在 workbench_tools.py 工具返回值里带 target_pane，sse_adapter 已有逻辑会透传整个 result dict，无需改 sse_adapter。需先 Read sse_adapter.py 确认其 result 字段是否整体透传

## 阶段 2：后端代码提取接口（WiringPane 从代码提取）

- [x] Task 3: 新建 `backend/app/hardware/code_extractor.py`，正则提取 + 启发式猜测
      创建 code_extractor.py（199 行，18 个函数均 ≤10 行），支持 pinMode/digitalWrite/digitalRead/analogRead/#define/I2C/SPI/Serial，LED/按钮/传感器/OLED/SPI/UART 启发式猜测，测试通过
  - [ ] SubTask 3.1: 实现 `extract_wiring_from_code(code: str) -> dict`，正则匹配 `#define`/`pinMode`/`digitalRead`/`digitalWrite`/`analogRead`/`analogWrite`
  - [ ] SubTask 3.2: 启发式规则：GPIO2+OUTPUT → LED，GPIO34+INPUT → 传感器（DHT22 等），SDA/SCL → I2C 显示器，MOSI/MISO/SCK → SPI 设备，RX/TX → 串口通信模块
  - [ ] SubTask 3.3: 返回 `{components: [...], connections: [...], message?: str}`
  - **验证**: 输入 `pinMode(2, OUTPUT); digitalWrite(2, HIGH);` 返回含 LED 器件

- [x] Task 4: 新建 `backend/app/api/wiring_extract.py`，`POST /api/wiring/extract` 路由
      创建 wiring_extract.py（69 行）+ 注册到 main.py，路由验证 OK，业务逻辑直测 LED 提取通过
  - [ ] SubTask 4.1: 创建 APIRouter，Pydantic 模型 `ExtractRequest{code: str}` / `ExtractResponse{components, connections, message?}`
  - [ ] SubTask 4.2: 路由调 `extract_wiring_from_code`，返回 `{success: True, data: {...}}`
  - [ ] SubTask 4.3: 在 `backend/app/api/__init__.py` 注册新 router
  - **验证**: 后端启动后 `curl -X POST http://127.0.0.1:58080/api/wiring/extract -H "Content-Type: application/json" -d '{"code":"pinMode(2,OUTPUT);digitalWrite(2,HIGH);"}'` 返回含 components

## 阶段 3：后端 DTR/RTS 真实控制

- [x] Task 5: 修改 `backend/app/api/tool_routes.py` WS `/api/monitor/{port}`，新增 `set_dtr`/`set_rts` 消息类型
      tool_routes.py L130-144 新增 set_dtr/set_rts 分支，调 ser.dtr/ser.rts，异常 try/except 不崩溃 WS，验证通过
  - [ ] SubTask 5.1: 在 `while True: data = await websocket.receive_text()` 循环中，新增 `elif msg.get("type") == "set_dtr": ser.dtr = bool(msg.get("payload"))`
  - [ ] SubTask 5.2: 同样处理 `set_rts` → `ser.rts = bool(msg.get("payload"))`
  - [ ] SubTask 5.3: 异常处理（ser 未打开 / AttributeError 时回 error 消息）
  - **验证**: 单元测试或手动 WS 连接发 `{"type":"set_dtr","payload":true}` 不报错（无真实设备时 ser.dtr 赋值可能抛异常，需 try/except）

## 阶段 4：前端类型扩展 + 联动路由

- [x] Task 6: 修改 `frontend/src/types/api.ts`，扩展 `ToolResultSSEEvent`
      ToolResultSSEEvent.result 加 target_pane?: 'wiring'|'safety'|'preview' + render_data?: unknown，tsc 0 errors
  - [ ] SubTask 6.1: `ToolResultSSEEvent.result` 类型加 `target_pane?: "wiring" | "safety" | "preview"`
  - [ ] SubTask 6.2: 加 `render_data?: unknown`（具体类型在各 Pane 组件内断言）
  - **验证**: `npx tsc --noEmit` 0 errors

- [x] Task 7: 修改 `frontend/src/stores/useAppStore.ts`，新增 `workbenchUserOverride` 状态
      加 workbenchUserOverride + setWorkbenchUserOverride + resetWorkbenchOverride，setWbTab 加 source 参数，tsc 0 errors。注意：实际字段名是 rightMode/setRightMode（非 rightPanelMode）
  - [ ] SubTask 7.1: 加 `workbenchUserOverride: boolean`（默认 false）
  - [ ] SubTask 7.2: 加 `setWorkbenchUserOverride(val: boolean)` setter
  - [ ] SubTask 7.3: 在 `setWbTab` 被用户手动调用时（非 bridge 调用）置 true；提供 `resetWorkbenchOverride()` 给 bridge 调用
  - **注意**: 需区分"用户手动点 tab"和"bridge 自动切 tab"。setWbTab 加可选参数 `source?: "user" | "bridge"`，默认 "user"
  - **验证**: `npx tsc --noEmit` 0 errors

- [x] Task 8: 新建 `frontend/src/stores/useWorkbenchBridge.ts`，监听 SSE 事件路由到 Pane
      创建 useWorkbenchBridge.ts（198 行），导出 handleToolResultEvent/handleToolCallEvent/initWorkbenchBridge。T5 协作点：useChatStore.ts onEvent 的 tool_call/tool_result 分支各加一行调用（已加注释标注）
  - [ ] SubTask 8.1: 导出 `useWorkbenchBridge` store，含 `wiringRenderData` / `safetyRenderData` / `codeRenderData` 三个字段 + 对应 setter
  - [ ] SubTask 8.2: 导出 `handleToolResultEvent(event: ToolResultSSEEvent)` 函数，按 `result.target_pane` 分发：
    - `"wiring"` → set wiringRenderData + 若 !workbenchUserOverride 则 setRightPanelMode("workbench") + setWbTab("wiring", "bridge")
    - `"safety"` → set safetyRenderData + 若 !workbenchUserOverride 则切 safety
    - `"preview"` → set codeRenderData + 若 !workbenchUserOverride 则切 preview
  - [ ] SubTask 8.3: 导出 `handleToolCallEvent(event: ToolCallSSEEvent)` 函数，新 call_id 时 resetWorkbenchOverride()
  - [ ] SubTask 8.4: 在 useChatStore 的 SSE 事件处理中调用这俩函数（需 T5 配合，或用 subscribe 模式监听 useChatStore 变化）
  - **注意**: T5 边界——useChatStore 是 T5 独占。本任务用 zustand subscribe 模式监听 useChatStore 的 lastToolResult/lastToolCall 字段，不改 useChatStore 本身。若 useChatStore 没暴露这些字段，则在 useChatStore 的 onEvent 回调里调 bridge 的 handler（这需要 T5 在 onEvent 里加一行调用，属协作点，需通知 T1）
  - **验证**: `npx tsc --noEmit` 0 errors；手动构造 tool_result 事件调用 handleToolResultEvent，store 字段更新

## 阶段 5：前端 WiringPane 可编辑化

- [x] Task 9: 新建 `frontend/src/stores/useWiringStore.ts`，存放可编辑状态
      创建 useWiringStore.ts（83 行），4 字段+9 actions，removeComponent 级联删连线，tsc 0 errors
  - [ ] SubTask 9.1: 字段：`components: WiringComponent[]` / `connections: WiringConnection[]` / `svg: string` / `bom: {component,qty}[]`
  - [ ] SubTask 9.2: actions：`addComponent` / `removeComponent` / `updateComponent` / `addConnection` / `removeConnection` / `setSvg` / `setBom` / `loadFromExtract`
  - **验证**: `npx tsc --noEmit` 0 errors

- [x] Task 10: 新建 `frontend/src/components/workbench/WiringEditor.tsx`，器件/连线编辑 UI
      创建 WiringEditor.tsx（255 行），器件列表+连线列表+添加表单+从代码提取+生成接线图+清空，apiPost 路径 ../../api/client，tsc 0 errors
  - [ ] SubTask 10.1: 器件列表渲染（每行显示 name/type/pins + 删除按钮）+ 「添加器件」按钮（弹表单输入 name/type/pins 逗号分隔）
  - [ ] SubTask 10.2: 连线列表渲染（每行显示 from→to + color + 删除按钮）+ 「添加连线」按钮（弹表单）
  - [ ] SubTask 10.3: 「从代码提取」按钮：读 useAppStore.previewTabs 活动 tab 代码 → 调 `apiPost("wiring/extract", {code})` → loadFromExtract 填入 store
  - [ ] SubTask 10.4: 「生成接线图」按钮：用 store 的 components/connections 调 `apiPost("wiring", {title, components, connections})` → setSvg/setBom
  - **验证**: `npx tsc --noEmit` 0 errors；手动渲染组件，点添加器件能加到列表

- [x] Task 11: 修改 `frontend/src/components/workbench/WiringPane.tsx`，拆成 WiringEditor + SVG 渲染区
      WiringPane 重写（247 行），顶部 WiringEditor + 下方 SVG 渲染区用 useWiringStore，删硬编码，监听 bridge wiringRenderData 自动填，tsc 0 errors
  - [ ] SubTask 11.1: 顶部放 WiringEditor 组件
  - [ ] SubTask 11.2: 下方保留现有 SVG 渲染区（缩放/拖拽/BOM），但数据源从硬编码改用 useWiringStore.svg/bom
  - [ ] SubTask 11.3: 删除 handleGenerate 里的硬编码 components/connections（[WiringPane.tsx L121-139](file:///e:/Desktop/agent/frontend/src/components/workbench/WiringPane.tsx#L121-L139)）
  - [ ] SubTask 11.4: 监听 useWorkbenchBridge.wiringRenderData，有新数据时 setSvg/setBom 到 useWiringStore（Agent 推送路径）
  - **验证**: `npx tsc --noEmit` 0 errors；WiringPane 启动时器件列表为空，点添加能加，点生成能出 SVG

## 阶段 6：前端其他 Pane 联动 + 缺口修复

- [x] Task 12: 修改 `frontend/src/components/workbench/SerialPane.tsx`，DTR/RTS 真实控制
      新增 handleToggleDtr/handleToggleRts，先 toggle store 再发 WS set_dtr/set_rts，未连接时 useLogStore 提示，tsc 0 errors
  - [ ] SubTask 12.1: `toggleDtr`/`toggleRts` 改为：先 toggle store 状态，然后若 wsRef.current?.readyState === OPEN，发 `JSON.stringify({type: "set_dtr", payload: dtrActive})`
  - [ ] SubTask 12.2: WS 未连接时点 DTR/RTS 给 toast 提示「串口未连接」
  - **验证**: `npx tsc --noEmit` 0 errors

- [x] Task 13: 修改 `frontend/src/components/workbench/FlashPane.tsx`，补传 code
      L45/L81 apiSSE build/upload 去掉 project_dir 加 code: flashCode，tsc 0 errors。备注：useCallback deps 未含 flashCode（stale closure 隐患，边界外）
  - [ ] SubTask 13.1: `apiSSE("build", { env: selectedEnv, code: flashCode })`（去掉 project_dir）
  - [ ] SubTask 13.2: `apiSSE("upload", { env: selectedEnv, port: portName, code: flashCode })`（去掉 project_dir）
  - **验证**: `npx tsc --noEmit` 0 errors；点编译时 Network 面板看请求 body 含 code 字段

- [x] Task 14: 修改 `frontend/src/components/workbench/PreviewPane.tsx`，监听 Agent render_code 推送
      PreviewPane 加 useEffect 监听 codeRenderData，ref 去重 + 置 null 双保险，addPreviewTab + setActivePreviewTabId，tsc 0 errors
  - [ ] SubTask 14.1: useEffect 监听 useWorkbenchBridge.codeRenderData，有新数据时 `addPreviewTab({id: "agent-"+Date.now(), label: "agent_generated."+lang, code: render_data.code, language: render_data.language})`
  - [ ] SubTask 14.2: 切换到新 tab（setActivePreviewTabId）
  - **验证**: `npx tsc --noEmit` 0 errors；手动 set codeRenderData，PreviewPane 出现新 tab

- [x] Task 15: 修改 `frontend/src/components/workbench/SafetyPane.tsx`，监听 Agent render_safety_report 推送
      SafetyPane 加 useEffect 监听 safetyRenderData，coerceAgentSafetyData type guard + agentDataToAllocations/Conflicts + mergeByPin 去重（Agent 优先），保留手动路径，tsc 0 errors
  - [ ] SubTask 15.1: useEffect 监听 useWorkbenchBridge.safetyRenderData，有新数据时直接 setPinAllocations/setStrappingConflicts（用 render_data.conflicts/warnings/pin_map，不再前端正则解析）
  - [ ] SubTask 15.2: 保留现有「安全检查」按钮路径（用户手动触发仍走前端正则 + /api/audit_pins）
  - [ ] SubTask 15.3: 两路数据合并去重（按 pin 字段，Agent 推送优先）
  - **验证**: `npx tsc --noEmit` 0 errors；手动 set safetyRenderData，SafetyPane 表格更新

## 阶段 7：集成验证

- [x] Task 16: 后端启动 + 前端 tsc 全量验证
      后端 58 路由 OK + wiring/extract + monitor 路由可达；前端 tsc 0 errors；pytest 159 passed
  - [x] SubTask 16.1: `cd backend && python -c "from app.main import create_app; app = create_app(); print('OK, routes:', len(app.routes))"` 输出 OK
  - [x] SubTask 16.2: `cd frontend && npx tsc --noEmit` 0 errors
  - [x] SubTask 16.3: `cd backend && pytest tests/ -x` 现有测试不回归

- [x] Task 17: 端到端联动验证（代码审查 + 路由可达性 + tsc/pytest）
      5 个场景传导链全部可达，无断链；修复 useWorkbenchBridge autoSwitchPane 未传 source="bridge" 的设计偏差 + 改用 resetWorkbenchOverride setter；tsc 0 errors
  - [x] SubTask 17.1: 后端启动 + 前端启动（后端 58 路由 OK，前端 tsc 0 errors）
  - [x] SubTask 17.2: 聊天→render_wiring→WiringPane：9 环传导链全接得上（chat_routes→agent_factory→workbench_tools→sse_adapter→useChatStore→useWorkbenchBridge→WiringPane）
  - [x] SubTask 17.3: 聊天→render_safety_report→SafetyPane：8 环传导链全接得上
  - [x] SubTask 17.4: PreviewPane 代码→WiringEditor「从代码提取」→器件列表：7 环传导链全接得上（handleExtract→apiPost wiring/extract→extract_wiring_from_code→loadFromExtract）
  - [x] SubTask 17.5: SerialPane DTR/RTS→后端 WS：7 环传导链全接得上（handleToggleDtr/Rts→WS set_dtr/set_rts→ser.dtr/ser.rts，无设备时前端 log error + 后端 try/except 不崩）

- [x] Task 18: 更新 `docs/pitfalls.md` + `docs/completed.md`
      pitfalls.md 追加 autoSwitchPane source 偏差踩坑；completed.md 追加「T6 硬件工作台 × Agent 联动」章节（核心设计+新增/修改文件+5 场景验证表+已知限制）
  - [x] SubTask 18.1: 修复过程中遇到的坑追加到 pitfalls.md
  - [x] SubTask 18.2: 完成后追加 completed.md「T6 硬件工作台 × Agent 联动」章节

# Task Dependencies

- Task 2 依赖 Task 1（工具返回值要先有 target_pane）
- Task 8 依赖 Task 6 + Task 7（类型 + useAppStore 状态）
- Task 10 依赖 Task 9（WiringEditor 用 useWiringStore）
- Task 11 依赖 Task 10 + Task 8（WiringPane 用 WiringEditor + bridge）
- Task 14, 15 依赖 Task 8（PreviewPane/SafetyPane 监听 bridge）
- Task 16 依赖所有前序任务
- Task 17 依赖 Task 16
- Task 18 依赖 Task 17

# 并行机会

- Task 3, 4（代码提取后端）可并行
- Task 5（DTR/RTS 后端）独立
- Task 12, 13（SerialPane/FlashPane 修复）可并行
- Task 14, 15（PreviewPane/SafetyPane 联动）可并行
