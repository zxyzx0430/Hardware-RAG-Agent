# Checklist

## 核心模块完整性（Task 1）

- [ ] `backend/src/agent/core/toolkit/tool_spec.py` 存在，ToolSpec 继承 BaseTool + RiskLevel(LOW/MEDIUM/HIGH) + ConfirmationRule + `async def execute(self, args: dict, ctx: ToolContext) -> dict` 抽象方法 + `_arun` 委托 ToolRouter.dispatch
- [ ] `backend/src/agent/core/toolkit/tool_result_envelope.py` 存在，ToolResultEnvelope + ErrorDetail + ResultMetadata（Pydantic model），model_dump() 返回 dict
- [ ] `backend/src/agent/core/toolkit/permission_classifier.py` 存在，check(spec, args, ctx) → "allow"/"ask"/"deny"，内部调 path_guard + risk_classifier
- [ ] `backend/src/agent/core/toolkit/loop_guard.py` 存在，per-request 实例 + check(tool_call, tool_result) → None/"LOOP_DETECTED"/"MAX_CALLS" + 冷却期 + 剔除 coverage hint 后 hash
- [ ] `backend/src/agent/core/toolkit/audit_recorder.py` 存在，record(call_id, spec, args, envelope, decision, ctx)，复用 ToolAudit 表
- [ ] `backend/src/agent/core/toolkit/tool_router.py` 存在，dispatch(call_id, tool_name, args, ctx) → envelope.model_dump()，8 步流程，**不做权限检查**，catch 非业务级异常

## 13 工具改造（Task 2）

- [ ] 13 个工具全部继承 ToolSpec，声明 input_schema + output_schema + risk_level + timeout_seconds
- [ ] 13 个工具实现 `async def execute(self, args: dict, ctx: ToolContext) -> dict` 方法
- [ ] 13 个工具的 _arun 委托 ToolRouter.dispatch（不自己写业务逻辑）
- [ ] 13 个工具的 finally 块 audit_logger 调用全部删除
- [ ] 4 个本地工具（read_file/write_file/edit_file/run_command）的 enforce_permission 调用全部删除
- [ ] 13 工具的非业务级 try/except 删除（业务级如 run_command 的 TimeoutError/OSError 保留）
- [ ] search_docs 的 output_schema 包含 output/results/truncated 字段
- [ ] run_command 的 output_schema 包含 output/exit_code/duration/timed_out 字段，execute 保留业务级 try/except
- [ ] render_wiring/render_safety_report/render_code 的 output_schema 包含 output/target_pane/render_data 字段
- [ ] risk_level 分级：10 LOW（search_docs/list_kb_docs/web_search/read_file/audit_pins/wiring/generate_code/render_*）+ 2 MEDIUM（write_file/edit_file）+ 1 HIGH（run_command）

## sse_adapter 接入（Task 3）

- [ ] `_check_loop_and_hint` 函数 + SOFT_CALL_HINT_THRESHOLD 常量已删除
- [ ] _emit_tool_call 里调 loop_guard.check(tool_call, None) 检测重复调用
- [ ] _convert_tool_message_to_sse 里调 loop_guard.check(tool_call, tool_result) 检测无进展
- [ ] LoopGuard 触发时 **break astream 循环**（不 raise 异常）+ 发 loop_detected SSE
- [ ] tool_result SSE 事件的 result 字段是 ToolResultEnvelope 格式（success/output/data/error/metadata）
- [ ] ToolMessage.content 只含 output + metadata（不含 data），data 仅走 SSE
- [ ] _try_parse_structured_content 扩展识别 success/output/error/metadata 字段
- [ ] search_docs 的 source SSE 事件从 envelope.data.results 提取

## agent_factory + hitl_handler + chat_routes 改造（Task 4）

- [ ] agent_factory.build_tools 改名为 build_tool_specs，返回 list[ToolSpec]（继承 BaseTool）
- [ ] hitl_handler 的 permission_gate.check_permission 改为 PermissionClassifier.check（pre-ToolNode 阶段）
- [ ] /api/agent-sandbox/resume 支持 decision=continue/stop/change_approach
- [ ] decision=continue 时调 reset_thread_checkpoint + 清空 LoopGuard + 注入 SystemMessage + 重启 astream
- [ ] decision=change_approach 时调 reset_thread_checkpoint + 清空 LoopGuard + 注入 SystemMessage + 重启 astream

## 前端改造（Task 5）

- [ ] `frontend/src/components/chat/LoopDetectedDialog.tsx` 存在，含"继续/停止/换思路"三按钮
- [ ] useChatStore 处理 loop_detected SSE 事件，弹 LoopDetectedDialog
- [ ] 点击按钮发 POST /api/agent-sandbox/resume（decision=continue/stop/change_approach）
- [ ] decision=stop 时前端显示 Agent 已停止
- [ ] decision=continue/change_approach 时前端恢复 streaming 状态

## 数据库迁移 + 老代码清理（Task 6）

- [ ] alembic migration：ToolAudit 表加 call_id(str)/success(bool)/error_type(str|null) 列
- [ ] 旧 tool_router.py 重命名为 tool_router_legacy.py，MCP 注册逻辑迁入新 ToolRouter
- [ ] `backend/src/agent/loop_detector.py` 已删除（逻辑迁入 LoopGuard）
- [ ] `backend/src/agent/tools/groups/file_ops/_permission.py` 的 enforce_permission 不再被任何工具调用
- [ ] permission_gate.py / risk_classifier.py / path_guard.py 保留，但只被 PermissionClassifier 调用
- [ ] audit_logger.py 的 log_tool_call 函数保留，但只被 AuditRecorder 调用
- [ ] grep 全项目无 `from src.agent.loop_detector import` 引用
- [ ] grep 全项目无 `from src.agent.tool_router import`（旧路径，应改为 tool_router_legacy 或新 toolkit）
- [ ] grep 全项目无 `from src.agent.tools.groups.file_ops._permission import enforce_permission` 引用
- [ ] grep 工具目录无 `audit_logger.log_tool_call` 直接调用（应只在 AuditRecorder 里）
- [ ] coverage hint 计数器从 search_docs PrivateAttr 迁入 ToolContext.kb_coverage_counter
- [ ] coverage hint 放 ToolResultEnvelope.metadata.kb_coverage_hint

## 端到端验证（Task 7）

- [ ] 6 模块单元测试全部通过（backend/src/agent/core/toolkit/test_*.py）
- [ ] Agent 回答 "ESP32-S3 ADC 输入范围" 时 search_docs 调用 ≤3 次（不再 9 次）
- [ ] 连续 2 次相同 search_docs 查询触发 LoopGuard → break astream → 前端弹窗
- [ ] 累计 15 次工具调用触发 MAX_CALLS → 前端弹窗
- [ ] write_file 在 default 模式触发 HITL 中断（PermissionClassifier 返回 ask）
- [ ] write_file 在 acceptEdits 模式放行（PermissionClassifier 返回 allow）
- [ ] bypassPermissions 模式所有工具放行，审计标注 decision=bypass
- [ ] run_command 执行超时返回 ToolResultEnvelope(success=false, error.error_type="TIMEOUT")
- [ ] run_command 业务级 TimeoutError 保留，返回 exit_code/timed_out 业务字段（success=true）
- [ ] 工具抛非业务级异常时返回 EXEC_ERROR，异常不传播到 LangGraph（流不崩）
- [ ] LLM 传入错误参数（如 query=123）时返回 INVALID_ARGS 错误，不执行工具
- [ ] 工具返回缺字段时返回 OUTPUT_SCHEMA_VIOLATION 错误，记审计
- [ ] 13 个工具的 audit 日志 12 字段一致（session_id/tool_name/args_summary/decision/decision_source/risk_level/exit_code/duration_ms/error/timestamp + 新增 call_id/success/error_type）
- [ ] 失败调用的 audit 日志 error 列截断到 1000 字
- [ ] DB 写异常时 AuditRecorder 静默吞错（warning log），ToolResultEnvelope 仍正常返回
- [ ] ToolMessage.content 不含 data 字段（只 output+metadata），data 仅走 SSE
- [ ] LoopGuard 冷却期：用户点"继续"后 LLM 重复同样调用，立即再触发 loop_detected
- [ ] coverage hint 迁移后仍工作：3 次低相关度后 ToolResultEnvelope.metadata.kb_coverage_hint 追加提示
- [ ] LoopGuard 不误判 coverage hint：剔除 metadata.kb_coverage_hint 后再 hash

## 代码规范（AGENTS.md）

- [ ] 每个文件 ≤300 行（超出拆模块）
- [ ] 每个函数 ≤10 行（超出拆子函数）
- [ ] 函数参数 ≤3 个（超出封装为 dataclass/dict）
- [ ] 无魔法数字（所有数字用命名常量）
- [ ] Python 函数有类型注解
- [ ] 所有外部调用（DB/API/文件）有 try/except，不静默吞异常（AuditRecorder 除外，有 warning log）
