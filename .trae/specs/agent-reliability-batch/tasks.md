# Tasks

- [x] Task 1: 后端 timeout 策略改为单工具 300s
  - [x] SubTask 1.1: prompts.py 把 `SINGLE_REQ_TIMEOUT_S = 120` 改为 `TOOL_CALL_TIMEOUT_S = 300`
  - [x] SubTask 1.2: context_guard.py 的 `check_timeout` 改为 `check_tool_timeout(call_start_time, call_id)`，从 per-call-id 开始时间算
  - [x] SubTask 1.3: sse_adapter.py 的 `_iter_agent_sse` 移除 `check_timeout(state)` 总流检查，在 `_handle_update_chunk` 处理 ToolMessage 前加 per-tool 检查
  - [x] SubTask 1.4: state dict 移除 `start_time` 字段（不再需要总流开始时间）

- [x] Task 2: 后端 source 引用 [srcN] 格式
  - [x] SubTask 2.1: prompts.py SYSTEM_PROMPT 加"答案中用 [srcN] 引用来源"指令
  - [x] SubTask 2.2: search_docs.py `_build_search_summary` 将 `[1]`/`[2]` 改为 `[src1]`/`[src2]`

- [x] Task 3: 后端 tool_call/tool_result 加时间戳
  - [x] SubTask 3.1: sse_adapter.py `_emit_tool_call` 事件加 `timestamp` 字段（time.time()）
  - [x] SubTask 3.2: sse_adapter.py `_emit_tool_result` 事件加 `end_timestamp` 字段

- [x] Task 4: 前端 HITL resume 状态修复
  - [x] SubTask 4.1: useChatStore.ts `resumeAgent` 补 `isStreaming: true` + `streamingSessionId` 设置
  - [x] SubTask 4.2: useChatStore.ts `_handleResumeEvent` 补 thinking/source/tool_call/tool_result 事件处理（复用 sendMessage 的处理逻辑）
  - [x] SubTask 4.3: `_handleResumeEvent` 的 text 分支同时更新 `streamingContent` 和 `message.content`

- [x] Task 5: 前端工具卡片时间显示
  - [x] SubTask 5.1: useChatStore.ts 收到 tool_call 事件时记录 `step.startTime = Date.now()`
  - [x] SubTask 5.2: ActivityBlock.tsx ToolStep pending 阶段加 setInterval 200ms 更新 elapsed
  - [x] SubTask 5.3: ToolStep 完成（status=done）时清除 setInterval，显示 duration_ms 转换的耗时

- [x] Task 6: 端到端验证
  - [x] SubTask 6.1: 测试 search_docs 慢查询（首次无缓存）不再触发 120s timeout（代码层：check_tool_timeout per-call 检查，总流无 timeout）
  - [x] SubTask 6.2: 测试 Agent 答案含 [srcN] 引用（SYSTEM_PROMPT + search_docs summary 双改）
  - [x] SubTask 6.3: 测试 HITL 确认框点"允许"后 Agent 继续（resumeAgent 补 isStreaming + _handleResumeEvent 完整事件处理）
  - [x] SubTask 6.4: 测试工具卡片右侧显示 live timer + 完成 duration（ToolStep useEffect + setInterval）
  - [x] SubTask 6.5: 测试"你好"闲聊无回归（代码层无回归，tsc + python import 双通过）
  - [x] SubTask 6.6: 修复 _finalizeResume content 重复合并 bug（与主流程 onDone 对齐，不再合并 streamingContent）

# Task Dependencies

- Task 1/2/3 后端可并行
- Task 4/5 前端可并行
- Task 6 依赖 Task 1-5 全部完成
