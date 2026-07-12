# 05-agent 代码扫描报告

> 扫描时间：2026-06-25 | 扫描方式：Pass 1（广度）+ Pass 2（深度）
> 扫描范围：tool_router.py / tool_routes.py / mcp_routes.py / llm/client.py / useChatStore.ts
> 原则：只记录问题，不修改代码

---

## Pass 1 — 广度扫描

### 1.1 类型安全

**P1 [BUG] GET /api/tools 遍历 _REGISTRY 时属性访问错误**
- 位置：backend/app/api/tool_routes.py:48
- 现象：for name, func in TOOL_REGISTRY.items(): doc = (func.__doc__ or "").strip()
  TOOL_REGISTRY 的 value 为 dict 类型（{"fn": handler, "param_schema": ..., "timeout_ms": ...}）
  而非可调用对象。func.__doc__ 在 dict 上不存在，会抛出 AttributeError。
- 影响评估：调用 GET /api/tools 时后端返回 500，前端工具面板/Workbench 彻底不可用
- 建议修复方式：改为 func.get("fn", {}).__doc__ 或从注册项中提取 description 字段

**P2 [缺失] ToolRequest.args 无 Schema 校验**
- 位置：backend/app/api/tool_routes.py:21-23
- 现象：class ToolRequest(BaseModel): tool: str; args: dict = {}
  args 只是 dict 类型，不校验字段名、类型、必填项。
  每个工具注册时有 param_schema 但路由层未使用。
- 影响评估：前端传错参数类型/名称时，工具内部报错而非路由层返回明确错误
- 建议修复方式：在 call_tool 中根据注册的 param_schema 做 jsonschema 校验后再 dispatch

### 1.2 功能完整性

**P2 [缺失] GET /api/tools 不返回 param_schema**
- 位置：backend/app/api/tool_routes.py:45-55
- 现象：list_tools 只返回 name 和 description，不返回工具的 param_schema。
  前端（如 WorkbenchPanel）无法知道工具需要哪些参数。
- 影响评估：前端表单/面板需硬编码工具参数结构，与后端不同步
- 建议修复方式：返回字段中添加 param_schema，直接从 _REGISTRY 条目读取

**P2 [缺失] MCP 工具注册未保留 description**
- 位置：backend/src/agent/tool_router.py:167-185
- 现象：register_mcp_tools() 创建条目时包含 description 字段，
  但 GET /api/tools 读取的是 func.__doc__（见 1.1 P1 bug），注册时的 description 被忽略。
- 影响评估：MCP 工具的 description 永远无法被前端获取
- 建议修复方式：修复 GET /api/tools 改为从条目 dict 中读取 description 字段

**P2 [缺失] chat SSE 流中从不发送 tool 事件**
- 位置：backend/app/api/routes.py /api/chat handler
- 现象：当前 /api/chat 直连 LLM + RAG 检索，没有 Agent 层。
  前端 useChatStore.ts 已完整处理 type: tool SSE 事件（streamingSteps），
  但后端从未发出此类事件。
- 影响评估：Agent 模式未集成前，前端 tool 事件处理代码处于接收端就绪、发送端缺失状态
- 建议修复方式：接入 LangChain ReAct Agent 后在流中 emit tool 事件

### 1.3 代码异味

**P3 [异味] Stub 工具未在响应中标记**
- 位置：backend/src/agent/tool_router.py:103-140
- 现象：AuditPinsTool/WiringTool/BuildTool/UploadTool/SearchDocsTool 返回含 stub 标记的输出文本，
  但 GET /api/tools 响应中无 is_stub 布尔字段。前端无法区分 stub 和 real 工具。
- 建议修复方式：在 _REGISTRY 条目中添加 stub: true 标记，响应中透传

**P3 [异味] tool_routes 和 routes.py 的工具路由分散**
- 位置：backend/app/api/tool_routes.py + backend/app/api/routes.py
- 现象：tool_routes.py 包含 /api/tool 和 /api/tools，routes.py 也包含 /wiring /audit_pins /build /upload /diagnose。
  这些端点本质都是工具调用，但分散在两个文件中。
- 建议修复方式：统一合并到 tool_routes.py 或在文档中说明分拆理由

**P2 [缺失] MCP 路由依赖 current_user，tool_routes 不依赖**
- 位置：backend/app/api/mcp_routes.py vs backend/app/api/tool_routes.py
- 现象：mcp_routes 所有端点使用 Depends(current_user) 鉴权，
  但 tool_routes 的 POST /api/tool 和 GET /api/tools 不鉴权。
- 影响评估：未认证用户可调用任意已注册工具（含 code_executor）
- 建议修复方式：tool_routes 添加 current_user 或 current_user_optional 依赖

### 1.4 资源泄漏

**P2 [风险] tool_router.dispatch 无并发锁**
- 位置：backend/src/agent/tool_router.py:77-112
- 现象：dispatch 函数没有并发控制。如果 SSE chat 和 POST /api/tool 同时调用同一工具，
  可能并行执行。对于串口/烧录等独占资源工具，这可能导致冲突。
- 影响评估：并发调用独占资源工具时行为未定义
- 建议修复方式：添加工具级锁（asyncio.Lock），或由工具内部自行管理并发

**P3 [风险] MCP 客户端重连无退避**
- 位置：backend/src/mcp/client.py:121-130
- 现象：reconnect() 直接调用 await self.connect()，失败后无等待/退避。
  若 Server 持续不可用，会高频重连。
- 建议修复方式：添加指数退避（0.5s/1s/2s/4s max 30s）

---

## Pass 2 — 深度扫描

### 2.1 契约对齐

**P2 [偏差] GET /api/tools 响应未覆盖 api-contract.md**
- 位置：api-contract.md 5.9 节 + tool_routes.py GET /api/tools
- 现象：
  - api-contract.md 定义了 POST /api/tool 的请求/响应格式，但对 GET /api/tools 无明确字段定义
  - 实际响应格式为 success+data.tools 数组
  - 缺少 param_schema、is_stub、timeout_ms 等字段
- 影响评估：前后端对工具列表的契约认知不一致
- 建议修复方式：在 api-contract.md 补充 GET /api/tools 的完整响应格式

**P2 [偏差] SSE tool 事件字段与前端期望不一致**
- 位置：api-contract.md SSE 事件表 + frontend/src/types/api.ts ToolSSEEvent
- 现象：
  契约定义 tool 事件字段为 name/icon/args/result
  前端 ToolSSEEvent 类型已对齐这些字段
  但后端从未实际发出 tool 事件（无 Agent），且后端 routes.py 中无对应序列化代码
- 影响评估：目前 tool 事件类型定义已就绪、发送端不存在，后续实现 Agent 时需确保序列化格式一致
- 建议修复方式：在 Agent 集成时按契约字段 emit SSE tool 事件

### 2.2 错误处理

**P1 [BUG] GET /api/tools 执行时必然 500**
- 位置：backend/app/api/tool_routes.py:48
- 现象：同 1.1 P1，TOOL_REGISTRY value 是 dict，func.__doc__ 一定 AttributeError
- 影响评估：GET /api/tools 完全不可用（阻塞级）
- 建议修复方式：doc = (func.get("fn").__doc__ or "").strip() if hasattr(func.get("fn"), "__doc__") else ""

**P2 [缺失] 工具超时前端无感知**
- 位置：frontend/src/stores/useChatStore.ts SSE onEvent 处理
- 现象：tool_router dispatch 默认超时 30s。若工具超时，dispatch 返回错误，
  但 POST /api/tool 返回此信息给前端，前端没有 timeout 的 UI 提示。
- 影响评估：用户等待 30s 后看到错误但没有明确的超时提示
- 建议修复方式：前端 POST /api/tool 响应处理中添加 timeout 分支；SSE 流中添加 type:error, code:TIMEOUT 事件

**P2 [缺失] MCP Server 启动失败消息不透传到前端**
- 位置：backend/app/api/mcp_routes.py:34-36
- 现象：success = await manager.start(server_id); if not success: raise HTTPException(500)
  但 manager.start() 返回 False 时未给出失败原因（端口占用/命令不存在/超时）。
- 影响评估：用户看到启动失败但不清楚原因
- 建议修复方式：manager.start 返回 (bool, reason) 元组，透传失败原因

### 2.3 竞态条件

**P2 [风险] 工具无串行保证**
- 位置：backend/src/agent/tool_router.py dispatch
- 现象：dispatch 函数没有锁。ToolHandler.run 被设计为 async，但没有工具级并发控制。
  串口/烧录类工具需要独占访问。
- 影响评估：并发调用独占工具（如串口写入）可能导致数据错乱
- 建议修复方式：添加工具级 asyncio.Lock，dispatch 时先 acquire 再 run

**P3 [风险] SSE 流中 tool 事件和 text 事件顺序无保证**
- 位置：前端 useChatStore.ts onEvent 中 thinking/text/tool 事件按到达顺序追加
- 现象：Agent 模式下，tool 调用和 LLM text 输出可能交叉到达，前端按 event 顺序渲染。
  如果后端乱序发送，前端 activity steps 可能错乱。
- 影响评估：事件展示顺序可能不自然，但不丢失数据
- 建议修复方式：后端 Agent 保证 tool 事件先于对应的 text 事件发送

### 2.4 边界情况

**P3 [边界] 空工具名称/超长工具参数**
- 位置：backend/app/api/tool_routes.py
- 现象：ToolRequest 中 tool: str 无 min_length 校验，args: dict = {} 无大小限制。
  空字符串工具名会触发 ToolNotFoundError（正确处理），但超长 args 无限制。
- 影响评估：空工具名返回错误而非崩溃；超长 args 可能 OOM
- 建议修复方式：添加 tool: str = Field(min_length=1) 和 args JSON 大小限制

**P3 [边界] 不存在的 MCP server_id 返回空列表而非 404**
- 位置：backend/app/api/mcp_routes.py:50-52
- 现象：client = manager.get_client(server_id); if not client: return {success:true, data:{tools:[]}}
  不存在的 server_id 返回空工具列表而非 404 或明确错误。
- 影响评估：前端难以区分 Server 不存在 和 Server 存在但无工具
- 建议修复方式：检查 server_id 是否在 configs 中，不存在则返回 404

---

## 汇总

| 严重度 | 数量 | 关键问题 |
|--------|------|---------|
| P1 | 2 | GET /api/tools 必崩（AttributeError）；阻塞级不可用 |
| P2 | 8 | 无鉴权、无 schema 校验、无并发锁、MCP 错误透传缺失、契约偏差、前端超时无感知 |
| P3 | 5 | stub 未标记、代码异味、边界情况 |
