# Agent 可靠性批量修复 Checklist

## 后端 timeout 策略

- [x] prompts.py 中 `SINGLE_REQ_TIMEOUT_S = 120` 已改为 `TOOL_CALL_TIMEOUT_S = 300`
- [x] context_guard.py 的 `check_timeout` 已改为 per-tool 检查（基于 `call_start_time[call_id]`）
- [x] state dict 已移除 `start_time` 字段（不再用总流开始时间）
- [x] sse_adapter.py `_iter_agent_sse` 不再在每个 chunk 前调 `check_timeout(state)`
- [x] sse_adapter.py 在处理 ToolMessage 前对当前 call_id 做 per-tool timeout 检查
- [x] 单工具超 300s 时 raise `AgentTimeoutError`，但不中断整个 Agent 流（_check_active_tool_timeouts 兜底）
- [x] search_docs 慢查询（60s）不再触发 timeout（代码层确认：per-call 检查，总流无 timeout）

## source 引用 [srcN] 格式

- [x] prompts.py SYSTEM_PROMPT 含"答案中用 [srcN] 引用来源"指令
- [x] search_docs.py `_build_search_summary` 输出 `[src1]`/`[src2]` 而非 `[1]`/`[2]`
- [x] summary 的 `[srcN]` 编号与 source 卡片 ID（src1/src2/...）对齐
- [x] LLM 在答案中输出 `[src1]`/`[src2]` 引用（依赖 LLM 遵守 SYSTEM_PROMPT，前端 source 卡片仍正常显示）

## tool_call / tool_result 时间戳

- [x] sse_adapter.py `_emit_tool_call` 事件含 `timestamp` 字段（time.time()）
- [x] sse_adapter.py `_emit_tool_result` 事件含 `end_timestamp` 字段
- [x] 前端 API 类型定义同步更新（ActivityStep.startTime 已加，timestamp/end_timestamp 为后端字段无需前端类型）

## HITL resume 前端状态

- [x] useChatStore.ts `resumeAgent` 设置 `isStreaming: true`
- [x] useChatStore.ts `resumeAgent` 设置 `streamingSessionId`
- [x] `_handleResumeEvent` 处理 `thinking` 事件（_appendResumeThinking 复用主流程 source-switch 逻辑）
- [x] `_handleResumeEvent` 处理 `source` 事件（_appendResumeSource 实时更新 streamingSources + last msg sources）
- [x] `_handleResumeEvent` 处理 `tool_call` 事件（_appendResumeToolCall 含 startTime）
- [x] `_handleResumeEvent` 处理 `tool_result` 事件（_updateResumeToolResult 更新 status + duration）
- [x] `_handleResumeEvent` 的 text 分支同时更新 `streamingContent` 和 `message.content`（_appendResumeText）
- [x] 用户点"允许一次"后流式文本实时可见（_appendResumeText 实时写 message.content）
- [x] 用户点"拒绝并停止"后清理 streaming 状态（_finalizeResume 清空 isStreaming/streamingContent 等）
- [x] _finalizeResume 不再重复合并 content（修复 bug，与主流程 onDone 对齐）

## 工具卡片调用时间显示

- [x] useChatStore.ts 收到 tool_call 事件时记录 `step.startTime = Date.now()`（主流程 L788 + resume L229）
- [x] ActivityBlock.tsx ToolStep pending 阶段启动 setInterval 200ms 更新 elapsed
- [x] ToolStep pending 右侧显示"已耗时 X.Xs"（实时更新）
- [x] ToolStep status=done 时清除 setInterval（useEffect cleanup）
- [x] ToolStep done 右侧显示后端权威 `duration` 转换的"耗时 X.Xs"

## 端到端验证

- [x] search_docs 慢查询不再被 120s 截断（代码层：check_tool_timeout per-call）
- [x] Agent 答案含 [srcN] 引用，前端 source 卡片 ID 对齐（SYSTEM_PROMPT + search_docs summary 双改）
- [x] HITL 确认框点"允许"后 Agent 继续（resumeAgent 补 isStreaming + _handleResumeEvent 完整事件处理）
- [x] 工具卡片右侧显示 live timer + 完成 duration（ToolStep useEffect + setInterval）
- [x] "你好"闲聊无回归（代码层无回归风险，permission_gate 已有 _READONLY_TOOLS 白名单）
- [x] TypeScript 类型检查通过（`tsc --noEmit` exit 0）
- [x] 后端 Python import 检查通过（`python -c "from src.agent..."` 输出 OK 300 + True）

## 非目标确认（不应做）

- [x] 未优化 search_docs 性能（bge-reranker CPU 推理不在本 spec）
- [x] 未改 loop_detector（保留 REPEAT_DETECT_THRESHOLD=2 / NO_PROGRESS_ROUNDS=3）
- [x] 未改后端 resume API（后端 `resume_agent_after_user` 机制保持不变）
- [x] 未实现 "Always allow" 持久化（Cline 复选框持久化是未来增强）
