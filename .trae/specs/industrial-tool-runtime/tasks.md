# Tasks

> 注：Task 6 为 BREAKING 改动，执行前需做回归测试。

- [ ] Task 1: 建 6 模块 Tool Runtime 核心抽象（backend/src/agent/core/toolkit/）
  - [ ] SubTask 1.1: tool_spec.py —— ToolSpec 继承 BaseTool + RiskLevel/ConfirmationRule 枚举 + `async def execute(self, args: dict, ctx: ToolContext) -> dict` 抽象方法 + `_arun` 委托 ToolRouter.dispatch
  - [ ] SubTask 1.2: tool_result_envelope.py —— ToolResultEnvelope + ErrorDetail + ResultMetadata（Pydantic model），model_dump() 返回 dict
  - [ ] SubTask 1.3: permission_classifier.py —— check(spec, args, ctx) → "allow"/"ask"/"deny"，内部调 path_guard + risk_classifier，**在 hitl_handler pre-ToolNode 阶段调用**
  - [ ] SubTask 1.4: loop_guard.py —— per-request 实例 + check(tool_call, tool_result) → None/"LOOP_DETECTED"/"MAX_CALLS" + 冷却期机制 + 剔除 coverage hint 后 hash
  - [ ] SubTask 1.5: audit_recorder.py —— 统一审计记录，复用 ToolAudit 表，record(call_id, spec, args, envelope, decision, ctx)
  - [ ] SubTask 1.6: tool_router.py —— dispatch(call_id, tool_name, args, ctx) → envelope.model_dump()，8 步流程（校验→超时→执行→循环检测→校验→包装→审计），**不做权限检查**，catch 非业务级异常
- [ ] Task 2: 改造 13 个工具为 ToolSpec（单 PR，不并行切换）
  - [ ] SubTask 2.1: retrieval 组（search_docs/list_kb_docs/web_search）—— 继承 ToolSpec + 声明 output_schema + 删 finally audit
  - [ ] SubTask 2.2: hardware 组（audit_pins/wiring）—— 继承 ToolSpec + 删 finally audit
  - [ ] SubTask 2.3: code 组（generate_code）—— 继承 ToolSpec + 删 finally audit
  - [ ] SubTask 2.4: file_ops 组（read_file/write_file/edit_file）—— 继承 ToolSpec + 删 enforce_permission
  - [ ] SubTask 2.5: execution 组（run_command）—— 继承 ToolSpec + 删 enforce_permission + **保留业务级 try/except**（TimeoutError/OSError 返回 exit_code/timed_out 业务 dict）
  - [ ] SubTask 2.6: workbench 组（render_wiring/render_safety_report/render_code）—— 继承 ToolSpec + 删 finally audit
  - [ ] SubTask 2.7: 为每个工具声明 output_schema（Pydantic model）+ risk_level + timeout_seconds + 实现 execute(args, ctx) → dict
  - [ ] SubTask 2.8: 删除 13 工具 _arun 内的**非业务级** try/except（业务级恢复如 run_command 的 TimeoutError 保留）
- [ ] Task 3: 改造 sse_adapter 接入 LoopGuard + ToolResultEnvelope + break astream 恢复
  - [ ] SubTask 3.1: 移除 _check_loop_and_hint + SOFT_CALL_HINT_THRESHOLD
  - [ ] SubTask 3.2: 在 _emit_tool_call / _convert_tool_message_to_sse 接入 LoopGuard.check
  - [ ] SubTask 3.3: LoopGuard 触发时 **break astream 循环**（不 raise 异常）+ 发 loop_detected SSE
  - [ ] SubTask 3.4: tool_result SSE 事件的 result 改为 ToolResultEnvelope 格式，data 仅走 SSE 不进 ToolMessage.content
  - [ ] SubTask 3.5: _try_parse_structured_content 扩展识别 success/output/error/metadata 字段
  - [ ] SubTask 3.6: search_docs 的 source SSE 事件从 envelope.data.results 提取
- [ ] Task 4: 改造 agent_factory + hitl_handler + chat_routes
  - [ ] SubTask 4.1: agent_factory.build_tools 改为 build_tool_specs，返回 list[ToolSpec]（继承 BaseTool，LangGraph 调用链不变）
  - [ ] SubTask 4.2: hitl_handler 的 permission_gate.check_permission 改为 PermissionClassifier.check（pre-ToolNode 阶段）
  - [ ] SubTask 4.3: chat_routes 的 /api/agent-sandbox/resume 支持 decision=continue/stop/change_approach
  - [ ] SubTask 4.4: decision=continue/change_approach 时调 reset_thread_checkpoint + 清空 LoopGuard + 注入 SystemMessage + 重启 astream
- [ ] Task 5: 前端 loop_detected 弹窗 + resume 处理
  - [ ] SubTask 5.1: 新建 LoopDetectedDialog.tsx（继续/停止/换思路 三按钮）
  - [ ] SubTask 5.2: useChatStore 处理 loop_detected SSE 事件，弹窗 + 发 resume 请求
  - [ ] SubTask 5.3: resume API 调用支持 decision=continue/stop/change_approach
- [ ] Task 6: 数据库迁移 + 旧代码清理（BREAKING）
  - [ ] SubTask 6.1: alembic migration：ToolAudit 表加 call_id(str)/success(bool)/error_type(str|null) 列
  - [ ] SubTask 6.2: 旧 tool_router.py 重命名为 tool_router_legacy.py，MCP 注册逻辑迁入新 ToolRouter
  - [ ] SubTask 6.3: 删除 sse_adapter._check_loop_and_hint + _check_loop_and_hint 函数
  - [ ] SubTask 6.4: 删除 loop_detector.py（逻辑迁入 LoopGuard）
  - [ ] SubTask 6.5: 删除 13 工具 finally 块的 audit_logger 调用
  - [ ] SubTask 6.6: 删除 4 本地工具的 enforce_permission 调用
  - [ ] SubTask 6.7: permission_gate/risk_classifier/path_guard/_permission 降级为 PermissionClassifier 内部组件（保留逻辑，删独立调用点）
  - [ ] SubTask 6.8: coverage hint 计数器从 search_docs PrivateAttr 迁入 ToolContext.kb_coverage_counter，hint 放 envelope.metadata.kb_coverage_hint
- [ ] Task 7: 端到端验证
  - [ ] SubTask 7.1: 单元测试 6 模块（toolkit/test_*.py）
  - [ ] SubTask 7.2: 集成测试：Agent 正常回答 ESP32-S3 ADC 问题（≤3 次 search_docs）
  - [ ] SubTask 7.3: 集成测试：连续 2 次相同 search_docs → LoopGuard 触发 → 前端弹窗
  - [ ] SubTask 7.4: 集成测试：累计 15 次工具调用 → MAX_CALLS 触发
  - [ ] SubTask 7.5: 集成测试：write_file 在 default 模式触发 HITL（PermissionClassifier 返回 ask）
  - [ ] SubTask 7.6: 集成测试：write_file 在 acceptEdits 模式放行（PermissionClassifier 返回 allow）
  - [ ] SubTask 7.7: 集成测试：bypassPermissions 模式所有工具放行，审计标注 decision=bypass
  - [ ] SubTask 7.8: 集成测试：run_command 超时返回 ToolResultEnvelope(success=false, error_type=TIMEOUT)
  - [ ] SubTask 7.9: 集成测试：run_command 业务级 TimeoutError 保留，返回 exit_code/timed_out 业务字段
  - [ ] SubTask 7.10: 验证 audit 日志 12 字段一致（含新增 call_id/success/error_type）
  - [ ] SubTask 7.11: 验证 LLM 传入错误参数返回 INVALID_ARGS，不执行工具
  - [ ] SubTask 7.12: 验证 ToolMessage.content 不含 data（只 output+metadata），data 仅走 SSE
  - [ ] SubTask 7.13: 验证 LoopGuard 冷却期（继续后重触发）
  - [ ] SubTask 7.14: 验证 coverage hint 迁移后仍工作（3 次低相关度后提示）

# Task Dependencies

- Task 2 depends on Task 1（工具改造依赖 ToolSpec 基类）
- Task 3 depends on Task 1（sse_adapter 接入依赖 LoopGuard + ToolResultEnvelope）
- Task 4 depends on Task 1 + Task 2（agent_factory 改造依赖 ToolSpec + PermissionClassifier）
- Task 5 depends on Task 3（前端弹窗依赖 loop_detected SSE 事件 schema）
- Task 6 depends on Task 1 + Task 2 + Task 3 + Task 4（删老代码前新链路必须打通）
- Task 7 depends on Task 6（验证在老代码删除后做）

# Parallelizable Work

- Task 1 内部 6 个 SubTask 可并行（6 模块互相独立）
- Task 5 可与 Task 4 并行（前端不依赖后端 agent_factory 改造，只依赖 SSE 事件 schema）
- Task 2 内部不可并行（单 PR 一次性切换，避免混合模式）
