# Agent 可靠性批量修复 Spec

## Why

用户实测 Agent 路径有 4 个阻塞/体验问题：
1. search_docs 慢导致 `agent timeout: 120.2s > 120s` 截断整个流——Agent 直接失败
2. 回答无 `[srcN]` 来源引用——LLM 不知道 source 卡片 ID 命名规则
3. HITL 权限确认框点完后 Agent 不继续——前端 `resumeAgent` 状态管理缺陷
4. 工具卡片无调用时间——pending 阶段无 live timer

同类工具横评（OpenCode/OpenHands/Codex/Cline/Hermes/Claude Code）结论：
- **timeout**：6 个工具均无总 timeout，只有单工具 timeout（Claude Code 最完善：bash/mcp/api 三类）。本项目 120s 总预算是反模式。
- **source**：6 个都是 coding agent 无 RAG 标注，需自行设计 `[srcN]` 格式。
- **HITL**：Cline 的 alwaysAllow 布尔分类 + Claude Code 5 级分级最成熟。本项目已有 `_READONLY_TOOLS` 白名单，需补前端 resume 状态。
- **工具时间**：Cline 的 pending live timer + 完成 duration 是最佳参考。

## What Changes

### 后端
- **prompts.py**：`SINGLE_REQ_TIMEOUT_S = 120` 改为 `TOOL_CALL_TIMEOUT_S = 300`（单工具预算，总流不限时）；保留 `loop_detector` 反死循环
- **context_guard.py**：`check_timeout` 改为 per-tool 检查（从 `call_start_time[call_id]` 算起），不再用 `state["start_time"]` 算总流时长
- **sse_adapter.py**：`_emit_tool_call` 事件加 `timestamp` 字段；`_emit_tool_result` 事件加 `end_timestamp` 字段
- **prompts.py SYSTEM_PROMPT**：加"答案中用 `[srcN]` 引用来源，N 对应 source 卡片 ID"指令
- **search_docs.py**：`_build_search_summary` 将 `[1]`/`[2]` 改为 `[src1]`/`[src2]`，与 source 卡片 ID 对齐

### 前端
- **useChatStore.ts `resumeAgent`**：补 `isStreaming: true` + `streamingSessionId` 设置
- **useChatStore.ts `_handleResumeEvent`**：补 `thinking`/`source`/`tool_call`/`tool_result` 事件处理（当前只处理 text/error）
- **useChatStore.ts `tool_call` 处理**：记录 `step.startTime = Date.now()`
- **ActivityBlock.tsx `ToolStep`**：pending 阶段加 live timer（setInterval 200ms 更新 elapsed），完成显示 duration

## Impact

- Affected specs: `surface-agent-reasoning`（思考链 + 工具时间）、`agent-react-fullstack`（timeout 策略）、`rag-to-agent-tool-trigger`（source 引用）
- Affected code:
  - `backend/src/agent/prompts.py`（timeout 常量 + SYSTEM_PROMPT source 指令）
  - `backend/src/agent/context_guard.py`（per-tool timeout 检查）
  - `backend/src/agent/sse_adapter.py`（tool_call/tool_result 加 timestamp）
  - `backend/src/agent/tools/groups/retrieval/search_docs.py`（summary 格式 [srcN]）
  - `frontend/src/stores/useChatStore.ts`（resume 状态 + tool_call startTime）
  - `frontend/src/components/chat/ActivityBlock.tsx`（ToolStep live timer）

## ADDED Requirements

### Requirement: 单工具 timeout（替代总流 timeout）

系统 SHALL 用单工具调用 timeout（300s）替代整个 Agent 流的总 timeout（120s）。单个工具执行超 300s 时 raise `AgentTimeoutError`，Agent 流不中断（由 LangGraph 的 error recovery 处理或 fallback）。

#### Scenario: search_docs 首次查询慢但 < 300s

- **WHEN** search_docs 首次查询耗时 60s（bge-reranker CPU 推理）
- **THEN** 工具正常返回结果，Agent 继续下一轮推理
- **AND** 不触发 timeout 错误

#### Scenario: 单工具超 300s

- **WHEN** 某工具执行超 300s（如 run_command 卡死）
- **THEN** raise `AgentTimeoutError`，前端显示"工具执行超时"
- **AND** Agent 流可继续其他工具或 fallback（不整个流中断）

#### Scenario: Agent 多轮工具调用总时长 > 120s

- **WHEN** Agent 调用 3 个工具，每个 50s，总时长 150s
- **THEN** 不触发 timeout（每个工具都在 300s 内）
- **AND** Agent 正常完成

### Requirement: source 引用 [srcN] 格式

系统 SHALL 要求 LLM 在答案中用 `[srcN]` 格式引用来源，N 对应 source 卡片的 ID（src1/src2/...）。SearchDocsTool 返回给 LLM 的 summary 也用 `[srcN]` 格式。

#### Scenario: LLM 引用来源

- **WHEN** search_docs 返回 3 个 source（id: src1/src2/src3）
- **AND** summary 用 `[src1] title: content` 格式
- **THEN** LLM 在答案中输出 "ESP32 有多个系列[src1]，其中 S3 支持 USB[src2]"
- **AND** 前端 source 卡片显示 src1/src2/src3，与答案内联引用对应

#### Scenario: SYSTEM_PROMPT 指令

- **WHEN** LLM 生成答案
- **THEN** SYSTEM_PROMPT 要求"引用知识库来源时用 `[srcN]` 格式，N 对应 source 卡片 ID"
- **AND** LLM 遵守格式（若不遵守，前端 source 卡片仍正常显示，只是答案无内联引用）

### Requirement: HITL resume 前端状态正确

系统 SHALL 在用户点击权限确认后正确恢复 Agent 流，包括流式文本可见、思考卡片/工具卡片/source 卡片正常渲染。

#### Scenario: 用户点击"允许"

- **WHEN** 工具需确认，前端弹 ConfirmDialog
- **AND** 用户点击"允许一次"
- **THEN** 前端调 `resumeAgent("allow")`，设置 `isStreaming: true` + `streamingSessionId`
- **AND** 后端 resume 流的 text/thinking/tool_call/tool_result/source 事件全部被 `_handleResumeEvent` 处理
- **AND** 流式文本实时更新到 `message.content`（不只更新 `streamingContent`）
- **AND** onDone 时 `_finalizeResume` 合并最终内容

#### Scenario: 用户点击"拒绝"

- **WHEN** 用户点击"拒绝并停止"
- **THEN** 前端调 `resumeAgent("stop")`
- **AND** 后端发 `done` 事件（reason: user stopped）
- **AND** 前端清理 streaming 状态，保留已生成的部分内容

### Requirement: 工具卡片调用时间显示

系统 SHALL 在工具卡片右侧显示调用时间：pending 阶段显示 live timer（已耗时），完成显示总 duration。

#### Scenario: 工具运行中

- **WHEN** 收到 tool_call 事件，step 进入 pending
- **THEN** 前端记录 `step.startTime = Date.now()`
- **AND** ToolStep 启动 setInterval 200ms 更新 elapsed 时间显示
- **AND** 右侧显示"已耗时 3.2s"（实时更新）

#### Scenario: 工具完成

- **WHEN** 收到 tool_result 事件，step 标 done
- **THEN** 清除 setInterval
- **AND** 右侧显示 `duration_ms` 转换的"耗时 5.3s"（后端权威值）

## MODIFIED Requirements

### Requirement: Agent timeout 检查点

原实现（context_guard.py + sse_adapter.py）：`check_timeout(state)` 在每个 SSE chunk 前检查 `time.time() - state["start_time"] > SINGLE_REQ_TIMEOUT_S`（120s 总预算）。

修改后：
- `state` 不再有 `start_time`（总流开始时间）
- `state` 加 `tool_call_start: dict[str, float]`（per-call-id 开始时间，复用 `call_start_time`）
- `check_timeout` 改为 `check_tool_timeout(call_start_time, call_id)`，只检查当前活跃工具
- 在 `_handle_update_chunk` 处理 ToolMessage 前检查对应 call_id 的耗时

### Requirement: tool_call / tool_result SSE 事件 schema

原 tool_call schema：`{tool, args, call_id, step_index}`
修改后：`{tool, args, call_id, step_index, timestamp}`（timestamp = 后端 time.time()）

原 tool_result schema：`{call_id, tool, result, success, duration_ms, step_index}`
修改后：`{call_id, tool, result, success, duration_ms, step_index, end_timestamp}`

### Requirement: _build_search_summary 输出格式

原格式（search_docs.py L163-172）：
```
[1] ESP32 技术参考手册
  content preview...
[2] ESP32-S3 数据手册
  content preview...
```

修改后：
```
[src1] ESP32 技术参考手册
  content preview...
[src2] ESP32-S3 数据手册
  content preview...
```

## REMOVED Requirements

### Requirement: 总流 timeout

**Reason**：120s 总预算导致 search_docs 慢时整个 Agent 流被截断。同类工具均无总 timeout。
**Migration**：改为单工具 300s timeout，保留 loop_detector 反死循环。

## 非目标（Out of Scope）

- **不优化 search_docs 性能**：bge-reranker CPU 推理慢是已知瓶颈，优化需换 GPU 或换模型，不在本 spec 范围
- **不改 loop_detector**：反死循环机制保留现状（REPEAT_DETECT_THRESHOLD=2, NO_PROGRESS_ROUNDS=3）
- **不改后端 resume API**：后端 `resume_agent_after_user` 机制正确，只修前端状态
- **不实现 "Always allow" 持久化**：Cline 的 alwaysAllow 复选框持久化是未来增强，本次只修即时响应

## 实现风险

1. **per-tool timeout 检查时机**：`_handle_update_chunk` 处理 ToolMessage 时检查，但如果工具卡死不返回 ToolMessage，timeout 不会触发。需在 `_iter_agent_sse` 的流循环里加 wall-clock 兜底（只针对当前活跃工具）。
2. **resume 流式文本合并**：`_handleResumeEvent` 的 text 分支需同时更新 `streamingContent` 和 `message.content`，避免 onDone 前用户看不到文本。
3. **ToolStep live timer 清理**：组件卸载或 step 状态变化时必须清 setInterval，否则内存泄漏。
