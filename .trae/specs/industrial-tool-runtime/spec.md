# Industrial Tool Runtime Spec

## Why

当前 13 个工具的调用链路存在 5 个工业级差距：schema 单向（无 output_schema）、错误格式 5 种风格、循环检测事后才跑、权限只覆盖 4 个工具、审计分散在 13 个 finally 块里。这导致 Agent 在 ESP32-S3 ADC 这类问题上空转 9 次、工具返回格式 LLM 难以预测、新增工具容易漏记审计。本 spec 引入 6 模块的工业级 Tool Runtime，参考 OpenHands AgentController / Codex Agent Loop / Claude Code permission classifier / OpenCode tool.schema 的成熟实践，把横切逻辑（校验/权限/超时/重试/审计/循环检测）统一收口。

## What Changes

- **新增** `backend/src/agent/core/toolkit/` 6 模块工业级 Tool Runtime
  - ToolSpec：继承 LangChain BaseTool，双向 schema + risk_level + timeout + retry + confirmation 规格表。`_arun` 内部委托 ToolRouter.dispatch
  - ToolRouter：统一 dispatch 入口（校验→超时→执行→循环检测→校验→包装→审计），**不做权限检查**（权限由 PermissionClassifier 在 pre-ToolNode 阶段做）
  - ToolResultEnvelope：统一返回格式（success/output/data/error/metadata），Pydantic model，dispatch 返回 `envelope.model_dump()`
  - PermissionClassifier：13 工具全分级（LOW/MEDIUM/HIGH）+ 三态决策（allow/ask/deny），**在 hitl_handler 的 pre-ToolNode 阶段调用**，保持 `interrupt_before=["tools"]` 机制兼容
  - LoopGuard：流中实时循环检测，**不 raise 异常**，改为 sse_adapter break astream + 发 loop_detected SSE + resume 时 reset_thread_checkpoint 恢复
  - AuditRecorder：ToolRouter 层统一审计，复用现有 ToolAudit 表 + alembic 加 call_id/success/error_type 列
- **BREAKING** 13 个工具一次性全切换为 ToolSpec（单 PR，不并行切换），下线老 BaseTool 直接调用
- **BREAKING** sse_adapter 移除 `_check_loop_and_hint` 事后诊断，改用 LoopGuard 流中检测
- **BREAKING** 工具内部删除 finally 块的 audit_logger 调用，统一由 ToolRouter 记录
- **BREAKING** 旧 `backend/src/agent/tool_router.py` 重命名为 `tool_router_legacy.py`（MCP 动态工具注册逻辑迁入新 ToolRouter）
- **新增** 前端 `loop_detected` SSE 事件处理 + 用户选择弹窗（继续/停止/换思路）
- **保留** LangGraph create_react_agent 架构，ToolSpec 继承 BaseTool 让 LangGraph 调用链不变

## Impact

- Affected specs: agent-reliability-batch（per-tool timeout 保留但迁入 ToolSpec）、fix-search-docs-empty-result-loop（coverage hint 保留，状态迁入 ToolContext，hint 放 ToolResultEnvelope.metadata）、rag-to-agent-tool-trigger（source SSE 保留，从 ToolResultEnvelope.data 提取）
- Affected code:
  - 新建：`backend/src/agent/core/toolkit/{tool_spec,tool_router,tool_result_envelope,permission_classifier,loop_guard,audit_recorder}.py`
  - 改造：`backend/src/agent/tools/groups/` 下 13 个工具文件（全部改为继承 ToolSpec）
  - 改造：`backend/src/agent/sse_adapter.py`（接入 LoopGuard + ToolResultEnvelope + break astream 恢复机制）
  - 改造：`backend/src/agent/agent_factory.py`（build_tools 改为 build_tool_specs，返回 ToolSpec 列表）
  - 改造：`backend/src/agent/hitl_handler.py`（permission_gate.check_permission 改为 PermissionClassifier.check）
  - 改造：`backend/src/agent/permission_gate.py`（降级为 PermissionClassifier 内部组件）
  - 改造：`backend/src/agent/loop_detector.py`（迁入 LoopGuard，旧文件删除）
  - 改造：`backend/src/agent/audit_logger.py`（迁入 AuditRecorder，ToolAudit 表加字段）
  - 重命名：`backend/src/agent/tool_router.py` → `tool_router_legacy.py`（MCP 注册逻辑迁出）
  - 新增 alembic migration：ToolAudit 表加 call_id/success/error_type 列
  - 新增前端：`frontend/src/components/chat/LoopDetectedDialog.tsx`
  - 改造前端：`frontend/src/stores/useChatStore.ts`（处理 loop_detected SSE）
  - 改造后端：`backend/app/api/chat_routes.py`（resume 支持 continue/stop/change_approach）

---

## ADDED Requirements

### Requirement: ToolSpec 统一工具规格表（继承 BaseTool）

系统 SHALL 为每个工具提供 ToolSpec 规格表，**继承 LangChain BaseTool**，声明 input_schema、output_schema、error_schema、timeout_seconds、max_retries、risk_level、requires_confirmation、audit 字段。ToolSpec 的 `_arun` 方法内部委托 `ToolRouter.dispatch(call_id, self.name, args, ctx)`，LangGraph 调用链不变。ToolSpec 是工具的唯一注册凭证，未继承 ToolSpec 的工具不能被 Agent 调用。

#### Scenario: 工具注册时声明完整规格
- **WHEN** 开发者新增工具
- **THEN** 必须继承 ToolSpec（其继承 BaseTool）并声明 output_schema（Pydantic model）
- **AND** 必须声明 risk_level（LOW/MEDIUM/HIGH）
- **AND** timeout_seconds 默认 300，可按工具特性覆盖
- **AND** 必须实现 `async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict` 方法（业务逻辑，返回 dict）

#### Scenario: ToolSpec._arun 委托 ToolRouter.dispatch
- **WHEN** LangGraph ToolNode 调用 `tool._arun(**args)`
- **THEN** ToolSpec._arun 内部调 `ToolRouter.dispatch(call_id, self.name, args, self._ctx)`
- **AND** call_id 从 LangGraph RunContext 获取
- **AND** 返回 `envelope.model_dump()`（dict 格式，LangGraph json.dumps 进 ToolMessage.content）

#### Scenario: LLM 传入参数不符合 input_schema
- **WHEN** LLM 调用 search_docs 但 query 是数字而非字符串
- **THEN** ToolRouter 在校验阶段返回 ToolResultEnvelope(success=false, error.error_type="INVALID_ARGS")
- **AND** 不执行工具业务逻辑
- **AND** error.suggestion 提示 LLM "请检查参数类型"

#### Scenario: 工具返回值不符合 output_schema
- **WHEN** search_docs 返回的 dict 缺少 output 字段
- **THEN** ToolRouter 包装为 ToolResultEnvelope(success=false, error.error_type="OUTPUT_SCHEMA_VIOLATION")
- **AND** 记录 audit 日志标注 schema 违规

### Requirement: ToolRouter 统一调度入口（不做权限检查）

系统 SHALL 提供唯一的 ToolRouter.dispatch() 作为工具执行入口，按"找工具→校验输入→超时包裹→执行→循环检测→校验输出→包装结果→审计记录"8 步流程执行。**ToolRouter 不做权限检查**（权限由 PermissionClassifier 在 hitl_handler pre-ToolNode 阶段做）。ToolSpec._arun 委托 ToolRouter.dispatch。

#### Scenario: 正常工具调用
- **WHEN** LLM 发起 search_docs(query="ESP32-S3 ADC")
- **THEN** ToolSpec._arun 委托 ToolRouter.dispatch
- **AND** ToolRouter 校验 input_schema 通过
- **AND** asyncio.wait_for 包裹执行，超时 60s
- **AND** LoopGuard.check 返回 None（无循环）
- **AND** 校验 output_schema 通过
- **AND** 返回 ToolResultEnvelope(success=true, output=summary, data=results)
- **AND** AuditRecorder 记录完整调用信息

#### Scenario: 工具超时
- **WHEN** search_docs 执行 65 秒未返回（timeout=60s）
- **THEN** asyncio.wait_for 抛 TimeoutError
- **AND** 若 max_retries>0 且错误可重试，重试一次
- **AND** 重试仍失败则返回 ToolResultEnvelope(success=false, error.error_type="TIMEOUT", error.retryable=true)
- **AND** error.suggestion 提示 "请缩小查询范围后重试"

#### Scenario: 工具执行抛异常
- **WHEN** 工具 execute 方法抛非业务级异常（如数据库连接失败）
- **THEN** ToolRouter catch 异常
- **AND** 返回 ToolResultEnvelope(success=false, error.error_type="EXEC_ERROR", error.error_message=str(exc))
- **AND** 不让异常向上传播到 LangGraph（避免流崩溃）

#### Scenario: 工具内部业务级错误恢复保留
- **WHEN** run_command 的 execute 方法内部 catch TimeoutError/OSError 返回带 exit_code/timed_out/stderr 的业务 dict
- **THEN** ToolRouter 不拦截此 dict（视为业务级恢复）
- **AND** ToolResultEnvelope.success=true（工具执行本身没崩）
- **AND** ToolResultEnvelope.data 携带 exit_code/timed_out/stderr 业务字段
- **AND** run_command 的 execute 方法保留内部 try/except（业务级恢复例外）

#### Scenario: 单 AIMessage 多 tool_calls 逐个 dispatch
- **WHEN** LLM 一个 AIMessage 含 3 个 tool_calls
- **THEN** LangGraph ToolNode 逐个调 tool._arun（LangGraph 默认行为）
- **AND** 每个 tool_call 独立走 ToolRouter.dispatch
- **AND** LoopGuard.call_history 累计 3 条
- **AND** AuditRecorder 记 3 条审计

### Requirement: ToolResultEnvelope 统一返回格式（Pydantic model）

系统 SHALL 所有工具返回值统一为 ToolResultEnvelope 格式（Pydantic model），包含 success、output、data、error、metadata 五个字段。**ToolMessage.content 只放 output + metadata（不含 data）**，data 仅通过 SSE tool_result 事件给前端，避免大 data 爆 token。ToolRouter.dispatch 返回 `envelope.model_dump()`，ToolSpec._arun 返回该 dict。

#### Scenario: 成功返回
- **WHEN** 工具执行成功
- **THEN** ToolResultEnvelope.success = true
- **AND** output 是给 LLM 的人话总结字符串（进 ToolMessage.content）
- **AND** data 是给前端用的完整数据 dict（不进 ToolMessage.content，仅走 SSE）
- **AND** error = null
- **AND** metadata 含 tool_name/duration_ms/call_id/timestamp（进 ToolMessage.content）

#### Scenario: 失败返回
- **WHEN** 工具执行失败
- **THEN** ToolResultEnvelope.success = false
- **AND** output = ""（空字符串，不混淆 LLM）
- **AND** data = null
- **AND** error 含 error_type/error_message/suggestion/retryable
- **AND** metadata 仍记录 duration_ms（即使失败也要记耗时）

#### Scenario: ToolMessage.content 格式
- **WHEN** ToolRouter.dispatch 返回 envelope.model_dump()
- **THEN** ToolSpec._arun 返回该 dict
- **AND** LangGraph ToolNode json.dumps 该 dict 进 ToolMessage.content
- **AND** ToolMessage.content 含 success/output/error/metadata（不含 data）
- **AND** sse_adapter._try_parse_structured_content 扩展识别 success/output/error/metadata 字段

#### Scenario: search_docs 大结果不爆 token
- **WHEN** search_docs 返回 100 条结果（data.results 很大）
- **THEN** ToolMessage.content 只含 output（summary）+ metadata
- **AND** data.results 仅通过 SSE tool_result 事件发给前端
- **AND** LLM 看不到 data.results 完整数组（只看 output summary）
- **AND** sse_adapter 从 envelope.data.results 提取 source SSE 事件

### Requirement: PermissionClassifier 全工具分级（pre-ToolNode 决策）

系统 SHALL 对所有 13 个工具自动进行风险分级和权限决策，返回 allow/ask/deny 三态。**PermissionClassifier 在 hitl_handler 的 pre-ToolNode 阶段调用**（即 `interrupt_before=["tools"]` 中断后、ToolNode 执行前），保持 LangGraph interrupt 机制兼容。ToolRouter.dispatch 不做权限检查。

#### Scenario: HITL 模式权限决策流程
- **WHEN** enable_hitl=True（permission_mode ≠ bypassPermissions）
- **AND** LangGraph 在 tools 节点前 interrupt
- **THEN** hitl_handler 调 PermissionClassifier.check(spec, args, ctx) 评估每个 pending tool_call
- **AND** allow → 继续流（ToolNode 执行）
- **AND** ask → 发 tool_confirm_required SSE，等用户回复
- **AND** deny → 注入 deny ToolMessage，继续流

#### Scenario: 非 HITL 模式（bypassPermissions）
- **WHEN** enable_hitl=False（permission_mode = bypassPermissions）
- **THEN** 不 interrupt，ToolNode 直接执行
- **AND** ToolRouter.dispatch 不做权限检查
- **AND** AuditRecorder 记录 decision=bypass

#### Scenario: LOW 风险工具自动放行
- **WHEN** 调用 search_docs（risk_level=LOW）
- **AND** permission_mode 为 default 或 acceptEdits
- **THEN** PermissionClassifier 返回 allow
- **AND** 不触发 HITL 中断（hitl_handler 仍 interrupt 但立即 allow 继续）

#### Scenario: MEDIUM 风险工具在 default 模式问用户
- **WHEN** 调用 write_file（risk_level=MEDIUM）
- **AND** permission_mode = default
- **THEN** PermissionClassifier 先调 path_guard.validate 检查路径
- **AND** 路径安全则返回 ask，触发 HITL 中断
- **AND** 路径危险则返回 deny，不执行

#### Scenario: MEDIUM 风险工具在 acceptEdits 模式放行
- **WHEN** 调用 write_file（risk_level=MEDIUM）
- **AND** permission_mode = acceptEdits
- **THEN** PermissionClassifier 调 path_guard.validate
- **AND** 路径安全则返回 allow，不中断

#### Scenario: HIGH 风险工具总是问
- **WHEN** 调用 run_command（risk_level=HIGH）
- **AND** permission_mode ≠ bypassPermissions
- **THEN** PermissionClassifier 调 risk_classifier 评估命令本身
- **AND** 低风险命令（如 ls）返回 ask（仍问，但风险标注 LOW）
- **AND** 高风险命令（如 rm -rf）返回 ask（风险标注 HIGH）

#### Scenario: 13 工具 risk_level 分级表
- **THEN** LOW（10 个）：search_docs / list_kb_docs / web_search / read_file / audit_pins / wiring / generate_code / render_wiring / render_safety_report / render_code
- **AND** MEDIUM（2 个）：write_file / edit_file
- **AND** HIGH（1 个）：run_command

### Requirement: LoopGuard 流中实时循环检测（不 raise 异常）

系统 SHALL 在 Agent 流执行过程中实时检测循环，发现"同一工具+同一参数连续 2 次"或"最近 3 次工具结果完全相同"或"总调用次数≥15"时，**不 raise 异常**，改为 sse_adapter break astream 循环 + 发 loop_detected SSE + 前端弹窗问用户。LoopGuard 是 per-request 实例，生命周期挂在 sse_adapter.state 上。

#### Scenario: LoopGuard per-request 生命周期
- **WHEN** 一个 chat 请求开始
- **THEN** sse_adapter.state 新建 LoopGuard 实例
- **AND** 该请求内所有 dispatch 调用共享同一 LoopGuard
- **AND** 请求结束后 LoopGuard 销毁（不跨 session）

#### Scenario: 检测到重复调用
- **WHEN** Agent 连续第 2 次调用 search_docs(query="ESP32-S3 ADC")
- **THEN** LoopGuard.check 返回 "LOOP_DETECTED"
- **AND** sse_adapter break astream 循环（不 raise 异常）
- **AND** 发 loop_detected SSE（含 type/call_count/recent_calls/message）
- **AND** 前端弹窗问用户

#### Scenario: 检测到无进展（排除 coverage hint 干扰）
- **WHEN** 最近 3 次 tool_result.output 的 hash 完全相同
- **THEN** LoopGuard.check 返回 "LOOP_DETECTED"
- **AND** hash 计算时剔除 ToolResultEnvelope.metadata.kb_coverage_hint 后缀（避免 coverage hint 被误判为无进展）
- **AND** 同上中断 + 问用户

#### Scenario: 检测到调用次数超限
- **WHEN** Agent 累计调用工具 15 次
- **THEN** LoopGuard.check 返回 "MAX_CALLS"
- **AND** 同上中断 + 问用户

#### Scenario: 用户选择继续
- **WHEN** 用户在弹窗点"继续"
- **THEN** 前端 POST /api/agent-sandbox/resume（decision=continue）
- **AND** 后端调 reset_thread_checkpoint 清理脏 checkpoint（break astream 留下的 mid-tool-execution 状态）
- **AND** 清空 LoopGuard 历史
- **AND** 注入 SystemMessage("用户选择继续，请换一种思路或基于已有信息回答") 提示 LLM
- **AND** 重启 astream（新 dispatch 不受历史影响）

#### Scenario: 用户选择停止
- **WHEN** 用户点"停止"
- **THEN** 前端 POST resume（decision=stop）
- **AND** 后端调 reset_thread_checkpoint 清理脏 checkpoint
- **AND** 发 done(success=false) SSE
- **AND** 结束 Agent 流

#### Scenario: 用户选择换思路
- **WHEN** 用户点"换思路"
- **THEN** 前端 POST resume（decision=change_approach）
- **AND** 后端调 reset_thread_checkpoint 清理脏 checkpoint
- **AND** 清空 LoopGuard 历史
- **AND** 注入 SystemMessage("检测到循环，请换一种思路或基于已有信息回答")
- **AND** 重启 astream

#### Scenario: 继续后冷却期防重触发
- **WHEN** 用户点"继续"后 LLM 立即又重复同样的 search_docs(query="ESP32-S3 ADC")
- **THEN** LoopGuard 检测到该 tool+args 组合在冷却期内（默认 3 轮）
- **AND** 返回 "LOOP_DETECTED"（立即再触发，不让 LLM 无限重试）
- **AND** 前端再次弹窗（用户可再选停止）

### Requirement: AuditRecorder 统一审计记录（复用 ToolAudit 表 + alembic migration）

系统 SHALL 在 ToolRouter.dispatch 的最后一步统一记录审计日志，工具内部不再自记。**复用现有 ToolAudit 表**，通过 alembic migration 新增 call_id/success/error_type 列，保留现有 exit_code/decision_source 列。

#### Scenario: 审计记录字段（复用 ToolAudit 表 + 新增列）
- **THEN** AuditRecorder 记录以下字段：
  - 现有：session_id / tool_name / args_summary / decision / decision_source / risk_level / exit_code / duration_ms / error / timestamp
  - 新增：call_id（str）/ success（bool）/ error_type（str|null）
- **AND** error_message 字段沿用现有 error 列（截断到 1000 字）

#### Scenario: 成功调用的审计
- **WHEN** search_docs 执行成功
- **THEN** AuditRecorder 在 dispatch 末尾记录一条 ToolAudit
- **AND** decision=allow（或 bypass），success=true，error_type=null，exit_code=0
- **AND** duration_ms 是实际耗时

#### Scenario: 失败调用的审计
- **WHEN** run_command 执行失败
- **THEN** AuditRecorder 记录 decision=ask（或 deny），success=false，error_type=EXEC_ERROR
- **AND** error 列截断到 1000 字

#### Scenario: 审计写入失败不阻塞工具
- **WHEN** 数据库写入异常
- **THEN** AuditRecorder 静默吞掉异常（只 warning log）
- **AND** 不影响 ToolResultEnvelope 返回给 LLM

### Requirement: coverage hint 状态迁移（迁入 ToolContext）

系统 SHALL 保留 fix-search-docs-empty-result-loop spec 的 coverage hint 功能，但有状态计数器从 search_docs 实例的 PrivateAttr 迁入 ToolContext（per-request 共享），hint 放 ToolResultEnvelope.metadata.kb_coverage_hint。

#### Scenario: coverage hint 计数状态载体
- **WHEN** search_docs 被调用
- **THEN** 计数器从 ToolContext.kb_coverage_counter 读取（per-request）
- **AND** max score < 0.8 时计数器 +1，≥ 0.8 时归零
- **AND** 计数器达 3 时在 ToolResultEnvelope.metadata.kb_coverage_hint 追加提示

#### Scenario: LoopGuard 不误判 coverage hint
- **WHEN** LoopGuard 计算 tool_result.output hash
- **THEN** 剔除 metadata.kb_coverage_hint 后再 hash
- **AND** 避免 coverage hint 导致 no_progress 误报

### Requirement: 旧 tool_router.py 迁移

系统 SHALL 将现有 `backend/src/agent/tool_router.py` 重命名为 `tool_router_legacy.py`，MCP 动态工具注册逻辑（register_mcp_tools/unregister_mcp_tools）迁入新 ToolRouter。

#### Scenario: 旧模块重命名
- **WHEN** 新 ToolRouter 上线
- **THEN** 旧 tool_router.py 重命名为 tool_router_legacy.py
- **AND** MCP 动态工具的 ToolSpec 工厂生成逻辑迁入新 ToolRouter
- **AND** grep 全项目无 `from src.agent.tool_router import` 引用（改为 tool_router_legacy 或新 toolkit）

---

## MODIFIED Requirements

### Requirement: 工具调用全链路

改造前：LangGraph ToolNode 直接调 tool._arun(**args)，工具内部各自处理 try/except/audit/timeout。

改造后：ToolSpec 继承 BaseTool，_arun 内部委托 ToolRouter.dispatch(call_id, tool_name, args, ctx)。ToolRouter 统一处理校验/超时/循环检测/校验/包装/审计，工具内部只实现 execute(args, ctx) → dict 业务逻辑。

#### Scenario: 改造后调用链
- **WHEN** LLM 发起 tool_call
- **THEN** LangGraph ToolNode 调 tool_spec._arun(**args)
- **AND** _arun 内部调 ToolRouter.dispatch(call_id, self.name, args, self._ctx)
- **AND** ToolRouter 8 步流程执行
- **AND** 返回 envelope.model_dump()
- **AND** LangGraph json.dumps 进 ToolMessage.content

### Requirement: SSE 事件转发

改造前：sse_adapter 在流结束后调 _check_loop_and_hint 发 hint SSE。

改造后：sse_adapter 在每次 tool_call/tool_result 时调 LoopGuard.check，检测到循环立即 break astream + 发 loop_detected SSE。tool_result 事件的 result 字段改为 ToolResultEnvelope 格式。

#### Scenario: LoopGuard 触发时 sse_adapter 行为
- **WHEN** LoopGuard.check 返回非 None
- **THEN** sse_adapter break astream 循环（不 raise 异常）
- **AND** 发 loop_detected SSE
- **AND** 不调 _check_loop_and_hint（已删除）

### Requirement: HITL 权限中断

改造前：LangGraph interrupt_before=["tools"] + hitl_handler 在流结束后检查 pending tool_calls + permission_gate.check_permission。

改造后：保留 interrupt_before=["tools"]，hitl_handler 的 permission_gate.check_permission 改为 PermissionClassifier.check。ToolRouter.dispatch 不做权限检查。

#### Scenario: HITL 模式权限决策
- **WHEN** enable_hitl=True
- **AND** LangGraph 在 tools 节点前 interrupt
- **THEN** hitl_handler 调 PermissionClassifier.check(spec, args, ctx)
- **AND** allow → 继续流
- **AND** ask → 发 tool_confirm_required SSE，等用户
- **AND** deny → 注入 deny ToolMessage

### Requirement: resume API 扩展

改造前：/api/agent-sandbox/resume 支持 decision=allow/deny/stop。

改造后：新增 decision=continue/change_approach，用于 LoopGuard 触发后的恢复。

#### Scenario: decision=continue
- **WHEN** 前端 POST resume（decision=continue）
- **THEN** 后端调 reset_thread_checkpoint 清理脏 checkpoint
- **AND** 清空 LoopGuard 历史
- **AND** 注入 SystemMessage("用户选择继续，请换一种思路或基于已有信息回答")
- **AND** 重启 astream

#### Scenario: decision=change_approach
- **WHEN** 前端 POST resume（decision=change_approach）
- **THEN** 后端调 reset_thread_checkpoint 清理脏 checkpoint
- **AND** 清空 LoopGuard 历史
- **AND** 注入 SystemMessage("检测到循环，请换一种思路或基于已有信息回答")
- **AND** 重启 astream

---

## REMOVED Requirements

### Requirement: 工具内部 audit_logger 调用

**Reason**: 13 个工具各自在 finally 块调 audit_logger，风格不统一，新增工具容易漏记。
**Migration**: 删除所有工具 finally 块的 audit_logger 调用，统一由 ToolRouter.dispatch 末尾的 AuditRecorder.record 记录。

### Requirement: 工具内部 enforce_permission 调用

**Reason**: 只 4 个本地工具有 enforce_permission，9 个 readonly 工具无权限检查。权限逻辑分散在 _permission.py + permission_gate.py + risk_classifier.py + path_guard.py。
**Migration**: 删除工具内部 enforce_permission 调用，统一由 PermissionClassifier 在 hitl_handler pre-ToolNode 阶段决策。permission_gate/risk_classifier/path_guard 降级为 PermissionClassifier 的内部组件保留。

### Requirement: sse_adapter._check_loop_and_hint 事后诊断

**Reason**: 流结束后才检测循环，LLM 已经空转 20 轮才发 hint，用户白等几分钟。
**Migration**: 删除 _check_loop_and_hint，改用 LoopGuard 流中实时检测 + break astream + 问用户。

### Requirement: 工具内部非业务级 try/except 错误处理

**Reason**: 5 种错误处理风格（re-raise / 返回错误 dict / sync helper 内 catch 返回错误字符串 / ToolMessage status=error / 直接 raise），LLM 看到的错误格式五花八门。
**Migration**: 工具内部只实现 execute(args, ctx) → dict 业务逻辑。**非业务级异常**（如数据库连接失败、意外错误）不写 try/except，由 ToolRouter 统一 catch。**业务级错误恢复**（如 run_command 的 TimeoutError/OSError 返回 exit_code/timed_out 业务 dict）保留 try/except，ToolResultEnvelope.data 携带业务字段。判定标准：如果异常是工具业务领域内的预期错误（如命令超时是 run_command 的预期场景），属于业务级恢复，保留 try/except。

### Requirement: 旧 tool_router.py 独立存在

**Reason**: 与新 ToolRouter 命名冲突，且 MCP 动态工具注册逻辑应迁入新 ToolRouter。
**Migration**: 重命名为 tool_router_legacy.py，MCP 注册逻辑迁入新 ToolRouter。
