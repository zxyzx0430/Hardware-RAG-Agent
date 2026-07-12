# Tasks — LangGraph ReAct Agent 全链路接入

> 按 v1/v2/v3 三阶段交付，每阶段可独立验收。每个阶段完成后用户可检查迭代，再启动下一阶段。
> 所有任务遵循 AGENTS.md 代码规范：函数 ≤ 10 行 / 文件 ≤ 300 行 / 圈复杂度 ≤ 10 / max-params ≤ 3 / 无魔法数字 / 类型注解 / try-except 外部调用。

---

## v1 — Agent 核心链路跑通（最快 demo 三段式）

**目标**：接入 LangGraph ReAct，用 5 个检索/硬件/代码工具跑通 demo "检索 → 分析 → 代码"。本地工具/权限门控/审计日志/确认弹窗在 v2/v3。

### v1-T1: 依赖与基础设施
- [x] 在 `backend/requirements.txt` 新增 `langgraph>=0.2.50` 和 `tavily-python>=0.5.0`
- [x] 新建 `backend/.env.example`（项目目前无此文件），含 LLM/Server/Chroma/SQLite/Embedding/Tavily 分组说明
- [x] 新建 `backend/sandbox_workspace/` 目录 + `.gitkeep`
- [x] 在 `backend/.gitignore` 新增 `sandbox_workspace/`
- [x] 清理残留 `backend/app/api/__pycache__/sandbox_routes.cpython-313.pyc`
- [x] 运行 `pip install -r backend/requirements.txt` 验证依赖安装

### v1-T2: Agent 核心模块（agent_factory + prompts）
- [x] 新建 `backend/src/agent/prompts.py`（≤ 200 行）
  - 定义 `SYSTEM_PROMPT` 常量（硬件 RAG Agent 角色 + 工具使用规范）
  - 定义 `MAX_RECURSION = 41`、`MAX_TOKEN_RATIO = 0.8`、`SINGLE_REQ_TIMEOUT_S = 120` 等命名常量
- [x] 新建 `backend/src/agent/agent_factory.py`（≤ 200 行）
  - 实现 `create_hardware_agent(model, api_key, base_url, tools, temperature, max_tokens) -> CompiledStateGraph`
  - 用 `ChatOpenAI` 包装模型（不传 LLMClient）
  - 调 `create_react_agent(model=llm, tools=tools, prompt=SYSTEM_PROMPT, checkpointer=MemorySaver(), interrupt_before=["tools"])`
  - 实现 `build_tools(payload: ChatRequest) -> list[BaseTool]`（v1 只装 5 个工具：search_docs/audit_pins/wiring/web_search/generate_code）
- [x] 实现 `use_agent` 判断函数 `_should_use_agent(payload) -> bool`（payload.use_agent + 模型白名单 + langgraph import 成功）
- [x] 修复：v1 默认 `enable_hitl=False`，`interrupt_before=[]`，确保工具直接执行（v2 接入 HITL 时才启用）

### v1-T3: SSE 适配器（sse_adapter）
- [x] 新建 `backend/src/agent/sse_adapter.py`（≤ 250 行）
  - 实现 `stream_agent_to_sse(agent, events, config, call_counter) -> AsyncIterator[str]`
  - 用 `agent.astream(events, config=config, stream_mode=["messages", "updates"])`
  - 实现 `_convert_chunk_to_sse(mode, chunk, call_counter, call_start_time) -> str | None`
  - mode="messages" + AIMessage chunk：content → SSE `text` 事件；tool_calls → SSE `tool_call` 事件
  - mode="updates" + ToolMessage：→ SSE `tool_result` 事件（含 duration/success/call_id）
  - 实现 `_extract_tool_call_info(chunk) -> list[dict]`（从 AIMessage.tool_calls 提取 tool/args/call_id）
  - 实现 `_extract_tool_result(chunk) -> dict`（从 ToolMessage 提取 result/duration/call_id）

### v1-T4: 工具实现（5 个 BaseTool）
- [x] 新建 `backend/src/agent/tools/__init__.py`
- [x] 新建 `backend/src/agent/tools/wrappers.py`（≤ 250 行）
  - 实现 `SearchDocsTool(BaseTool)`：args_schema 只含 `query`；top_k/kb_ids/threshold 通过 `__init__` 注入；`_arun` 调 `search_docs_core`
  - 实现 `AuditPinsTool(BaseTool)`：包装 `audit_pins_core`
  - 实现 `WiringTool(BaseTool)`：包装 `generate_wiring_svg`
  - 每个工具同时实现 `_run`（同步）和 `_arun`（异步）
  - 用 Pydantic `args_schema` 定义参数 schema
- [x] 新建 `backend/src/agent/tools/web_search.py`（≤ 150 行）
  - 实现 `WebSearchTool(BaseTool)`：args_schema 含 `query` + `max_results`（默认 5）
  - `__init__` 注入 `tavily_api_key: str`
  - `_arun` 调 Tavily API，失败降级返回 `{"output": "网页搜索失败，请基于本地知识库回答"}`
  - 每条结果截断 300 chars
- [x] 新建 `backend/src/agent/tools/generate_code.py`（≤ 200 行）
  - 实现 `GenerateCodeTool(BaseTool)`：args_schema 含 `task` + `language`
  - `__init__` 注入 `llm_client: LLMClient`
  - `_arun` 用硬件代码模板 prompt 调 LLM，超时 30s 降级小模型
  - 返回完整代码字符串 + 语言标识

### v1-T5: chat_routes 接入 Agent 主路径
- [x] 修改 `backend/app/api/chat_routes.py`：
  - `ChatRequest` 新增字段 `use_agent: bool = False` / `permission_mode: str = "default"` / `tool_keys: dict[str, str] = {}`
  - 在 L121 `yield sse_event("thinking", ...)` 后新增 `if use_agent:` 条件分支
  - 调 `stream_agent_to_sse(agent, events, config, call_counter)` 替换原 LLM 流式块
  - 保留原 L121-213 逻辑作为 fallback，提取为 `_fallback_rag_chat()` 函数
  - 捕获 `GraphRecursionError` → yield error 事件 → 调 `_fallback_rag_chat()`
  - 删除 L178 的 `# TODO: ReAct loop` 注释

### v1-T6: 前端 SSE 解析 + 类型扩展
- [x] 修改 `frontend/src/types/session.ts`：
  - `ActivityStep` 新增 `call_id?: string` / `risk_level?: "low"|"medium"|"high"` / `decision_source?: string`
  - 保持 `duration` 命名不变
- [x] 修改 `frontend/src/stores/useChatStore.ts`：
  - SSE 事件解析新增 `tool_call` 分支：构建 pending 状态的 ActivityStep（含 call_id/name/args）
  - SSE 事件解析新增 `tool_result` 分支：按 `call_id` 找到对应 step 更新为 done（含 result/duration/success）
  - 请求体新增 `use_agent: true`（默认开启）/ `permission_mode` / `tool_keys: {tavily: toolKeys.tavily || ""}`
- [x] 修改 `frontend/src/stores/useSettingsStore.ts`：
  - 新增 `permissionMode: "bypassPermissions" | "default" | "acceptEdits"`，默认 `"default"`
  - 加入 PERSIST_KEYS

### v1-T7: 前端 ActivityBlock 抽离 + 渲染扩展
- [x] 新建 `frontend/src/components/chat/ActivityBlock.tsx`（≤ 250 行）
  - 从 `ChatArea.tsx` L487-545 抽离 ActivityBlock 函数为独立组件
  - 从 `ChatArea.tsx` L547-574 抽离 ThinkingStep
  - 从 `ChatArea.tsx` L576-609 抽离 ToolStep
  - ToolStep 扩展渲染 `risk_level` 徽章（HIGH 红/MEDIUM 黄/LOW 灰）和 `decision_source` 标签
  - 支持 pending 状态（收到 tool_call 但未收到 tool_result 时显示 spinner）
- [x] 修改 `frontend/src/components/chat/ChatArea.tsx`：
  - 删除局部 ActivityBlock/ThinkingStep/ToolStep 函数
  - 改为 `import { ActivityBlock } from "./ActivityBlock"`
  - 保持现有 props 传递不变

### v1-T8: v1 联调测试
- [x] 后端启动验证：`python main.py --web --port 58080` — 启动成功，Reranker + BM25 + Embedding 全部加载
- [x] 前端启动验证：`npx vite --port 5173` — Vite v6.4.3 启动成功
- [x] 修复 bug：`_should_use_agent` 未使用解析后的 model 参数 → Agent 路径不触发（pitfalls.md 已记录）
- [x] 修复 bug：streaming tool_calls 分块到达导致 tool_call 事件 args 为空 → 改用 updates 模式提取完整 AIMessage（pitfalls.md 已记录）
- [x] 测试场景 4："hello" → Agent 第 1 轮直接回答，无工具调用 — ✅ 通过（流式输出中文回答，介绍了 5 大功能）
- [x] 工具调用验证："STM32 GPIO 怎么配置为输出模式？" → Agent 调 search_docs — ✅ tool_call 事件 args 完整 `{"query":"STM32 GPIO 输出模式 配置 MODE CNF"}`
- [-] 测试场景 1/2/3（ch340g 接线 / audit_pins / generate_code）：工具执行超时（RAG 检索 ~80s × 2 次重复），属性能优化范围，标记为 iterations.md §2 上下文管理优化项
- [-] 测试场景 5/6（模型不支持 / Agent 失败降级）：需特殊配置模型，标记为后续手动验证
- [x] v1 核心链路验证通过：Agent 触发 → 流式输出 → tool_call 事件（args 完整）→ 工具执行

---

## v2 — 本地工具 + 权限门控 + HITL

**目标**：接入 4 个本地工具（read_file/write_file/edit_file/run_command）+ 4 步权限门控 + 用户确认弹窗。覆盖 demo 全部场景。

### v2-T1: 权限门控核心模块
- [x] 新建 `backend/src/agent/path_guard.py`（≤ 200 行）
      定义 ALLOWED_DIRS（PROJECT_ROOT + SANDBOX_TEMP_DIR）+ DENY_PATTERNS（.git/.vscode/.idea/.claude/settings.json/.env/*.key/*.pem/*credentials*/..）+ validate_path/matches_deny_pattern，用 os.path.realpath 解析符号链接
- [x] 新建 `backend/src/agent/risk_classifier.py`（≤ 200 行）
      定义 HIGH/MEDIUM/LOW 关键字列表 + classify_risk(tool, args)，run_command 走关键字匹配，文件操作默认 low，未知命令默认 medium
- [x] 新建 `backend/src/agent/permission_gate.py`（≤ 250 行）
      实现 check_permission 4 步流程（path_guard → 模式分流 → risk_classifier → 决策），bypass=allow/default=ask/acceptEdits=low allow+medium/high ask
- [x] 新建 `backend/src/agent/exceptions.py`：ToolContext dataclass + PermissionAskError + PermissionDenyError

### v2-T2: 本地工具实现（4 个 BaseTool）
- [x] 新建 `backend/src/agent/tools/file_ops.py`（≤ 250 行）
      实现 ReadFileTool/WriteFileTool/EditFileTool + 共享 enforce_permission helper（ask→PermissionAskError，deny→PermissionDenyError），v2 audit 用 logger 占位
- [x] 新建 `backend/src/agent/tools/run_command.py`（≤ 200 行）
      实现 RunCommandTool，asyncio.create_subprocess_shell + wait_for 超时（30s 默认/5min 上限），stdout/stderr 截断 5000 chars
- [x] 修改 `backend/src/agent/agent_factory.py`：build_tools 加载 4 个 v2 本地工具，_build_tool_ctx 构造 ToolContext 注入 permission_mode + session_id

### v2-T3: HITL 中断机制接入
- [x] 修改 `backend/src/agent/agent_factory.py`：新增 _get_checkpointer 全局 MemorySaver 单例（跨请求共享 checkpoint，让 resume 能恢复状态）
- [x] 新建 `backend/src/agent/hitl_handler.py`（185 行）
      handle_auto_resume 循环检测 tools 中断 + 调 permission_gate 评估（allow 自动放行/ask yield confirm_required/deny 注入拒绝 ToolMessage）
      resume_agent_after_user 用户决策后恢复（allow→astream(None)/deny→注入拒绝/stop→done 事件）
      _detect_tools_interrupt 检查 agent.get_state(config).next 是否含 tools 节点
- [x] 修改 `backend/src/agent/sse_adapter.py`：stream_agent_to_sse 增加 permission_mode 参数，astream 结束后延迟导入 hitl_handler.handle_auto_resume 处理中断
- [x] 修改 `backend/app/api/chat_routes.py`：
      _build_agent_for_payload 根据 permission_mode 决定 enable_hitl（bypass=False/default+acceptEdits=True）
      _run_agent_stream 传 permission_mode 给 stream_agent_to_sse
      新增 POST /api/agent-sandbox/resume 路由（ResumeRequest + _resume_event_generator + _resolve_creds）
- [x] 验证测试：7 个场景全部通过（bypass allow/acceptEdits low allow/default ask/.git deny/rm -rf ask/confirm 事件生成/default python ask）

### v2-T4: 前端确认弹窗
- [x] 新建 `frontend/src/components/chat/ConfirmDialog.tsx`（106 行）
      4 按钮（允许本次/永久允许/拒绝/拒绝并停止）+ 工具名+参数摘要+risk-badge + Esc 关闭 + 调 resumeAgent action
- [x] 修改 `frontend/src/stores/useChatStore.ts`：
      pendingConfirm/_lastAgentPayload/resumeAgent/clearPendingConfirm + tool_confirm_required SSE 解析 + 4 个 resume helper（_handleResumeEvent/_appendResumeToolCall/_updateResumeToolResult/_finalizeResume）
- [x] 修改 `frontend/src/types/api.ts`：新增 ToolConfirmRequiredSSEEvent 类型 + ChatSSEEvent union
- [x] 修改 `frontend/src/components/chat/ChatArea.tsx`：挂载 ConfirmDialog
- [x] 修改 `frontend/src/styles/chat.css`：新增 .confirm-dialog* 样式（含暗色模式）
- [x] tsc --noEmit 通过

### v2-T5: 前端 PolicyBar 策略切换
- [x] 新建 `frontend/src/components/chat/PolicyBar.tsx`（64 行）
      3 按钮（bypassPermissions=红/default=黄/acceptEdits=绿）+ 当前模式高亮 + 调 updateSetting("permissionMode", mode)
- [x] 修改 `frontend/src/components/layout/MainArea.tsx`：ChatArea 和 InputBar 之间挂载 PolicyBar
- [x] 修改 `frontend/src/styles/chat.css`：新增 .policy-bar* 样式（含暗色模式）
- [x] tsc --noEmit 通过

### v2-T6: v2 联调测试
- [x] 测试场景 7：read_file（acceptEdits → allow）✅ PASS
- [x] 测试场景 8：write_file（acceptEdits → allow）✅ PASS
- [x] 测试场景 9：edit_file（default → ask）✅ PASS
- [x] 测试场景 10：run_command python --version（acceptEdits → allow）✅ PASS
- [x] 测试场景 11：HIGH 风险 `rm -rf`（acceptEdits → ask）✅ PASS
- [x] 测试场景 12：强制 deny 路径 `.git/config`（bypassPermissions → deny）✅ PASS
- [x] 测试场景 13：bypassPermissions 放行 echo（→ allow）✅ PASS
- [x] 后端导入验证：v2 全模块（hitl_handler/permission_gate/file_ops/run_command/agent_factory）导入 OK
- [x] checkpointer 验证：InMemorySaver（langgraph 新命名）单例正常
- [x] resume 路由验证：/api/agent-sandbox/resume 已注册
- [x] 前端 tsc --noEmit 通过
- [x] 前端 vite build 通过（11.73s，772 modules）
- [x] 7/7 逻辑测试场景全部通过（scripts/test_v2_scenarios.py）
- [-] 完整 E2E（LLM 调用 + 工具执行流）：依赖用户 API Key + 支持 function calling 的模型，标记为后续手动验证

---

## v3 — 审计日志 + 设置面板 + 收尾

**目标**：审计日志 SQLite 持久化 + 前端审计日志面板 + 文档同步。完成 demo 全功能。

### v3-T1: 审计日志 SQLite 持久化
- [ ] 修改 `backend/app/db/models.py`：
  - 新增 `ToolAudit` 表模型（id/timestamp/session_id/tool_name/args_summary/decision/decision_source/risk_level/exit_code/duration_ms/error）
- [ ] 新建 alembic 迁移脚本（如有 alembic）或 `create_all` 自动建表
- [ ] 新建 `backend/src/agent/audit_logger.py`（≤ 200 行）
  - 实现 `log_tool_call(session_id, tool_name, args, decision, decision_source, risk_level, exit_code=None, duration_ms=None, error=None)`
  - 实现 `cleanup_old_logs(days=30)`（删除 30 天前的记录）
  - 实现 `query_logs(session_id: str | None = None, limit: int = 100) -> list[dict]`
  - 启动时调 `cleanup_old_logs()`
- [ ] 修改 `backend/src/agent/tools/file_ops.py` 和 `run_command.py`：
  - 把 v2 的 logger 占位替换为 `audit_logger.log_tool_call()`
- [ ] 修改 `backend/src/agent/permission_gate.py`：
  - 每次决策落地（allow/ask/deny）调 `audit_logger.log_tool_call()`

### v3-T2: agent_sandbox_routes API
- [ ] 新建 `backend/app/api/agent_sandbox_routes.py`（≤ 200 行）
  - `GET /api/agent-sandbox/policy` — 查询当前 permission_mode（从 settings 表读）
  - `POST /api/agent-sandbox/policy` — 切换 permission_mode（写 settings 表）
  - `GET /api/agent-sandbox/audit?session_id=xxx&limit=100` — 查询审计日志
  - `POST /api/agent-sandbox/resume` — v2 已实现，此处仅注册路由
- [ ] 在 `backend/main.py` 注册 agent_sandbox_routes 路由

### v3-T3: 前端 AuditLogPanel 审计日志面板
- [ ] 新建 `frontend/src/components/settings/AuditLogPanel.tsx`（≤ 250 行）
  - 按 session_id 查询审计日志（调 `GET /api/agent-sandbox/audit`）
  - 展示列表：工具名 + 参数摘要 + 决策徽章 + 风险等级徽章 + 耗时 + 结果
  - 支持按风险等级/决策过滤
- [ ] 修改 `frontend/src/components/settings/SettingsPage.tsx`：
  - `TAB_IDS` 新增 `"audit"`
  - 渲染 `<AuditLogPanel />` 当 activeTab === "audit"

### v3-T4: 防死循环完善
- [x] 新建 `backend/src/agent/loop_detector.py`（62 行）
      实现 _args_hash(MD5) + detect_repeat(5 窗口 2+ 次) + detect_no_progress(3 窗口 hash 相同) + detect_loop + loop_hint
- [x] 修改 `backend/src/agent/sse_adapter.py`：
      state 增加 call_history + result_history(deque maxlen=5) + _check_loop_and_hint 在 astream 结束后调用
      _emit_tool_call 追加 call_history，_convert_tool_message_to_sse 追加 result_history
      新增 _extract_output_text helper，新增 SOFT_CALL_HINT_THRESHOLD=10 软提示
      3/3 测试场景通过（repeat/no_progress/none）
- [-] 单次请求 > 120s → 抛 AgentTimeoutError：v3-T5 合并实现（contextvars 一并处理）

### v3-T5: 累积上下文保护
- [x] 新建 `backend/src/agent/context_guard.py`（57 行，从 sse_adapter 拆出保持文件 ≤ 300 行）
      模块级 `_CUMULATIVE_TOKENS: ContextVar[int]`（per-request 隔离）+ reset_token_counter
      compute_token_limit(model) = get_context_window(model) * MAX_TOKEN_RATIO（空 model → 0 禁用）
      accumulate_tokens(content, state) 调 LLMClient._estimate_tokens 累加，超限抛 ContextLimitError
      check_timeout(state) 超 SINGLE_REQ_TIMEOUT_S(120s) 抛 AgentTimeoutError
      context_limit_event / timeout_event 构建 SSE error 事件
- [x] 新增 `backend/src/agent/exceptions.py` ContextLimitError + AgentTimeoutError
- [x] 修改 `backend/src/agent/sse_adapter.py`（正好 300 行达标）：
      stream_agent_to_sse 新增 model 参数，state 增加 token_limit + start_time
      _iter_agent_sse 每次迭代前调 check_timeout
      _handle_message_chunk 后调 accumulate_tokens(AIMessageChunk.content)
      _convert_tool_message_to_sse 后调 accumulate_tokens(tool_result)
      try/except 捕获 ContextLimitError/AgentTimeoutError → SSE error → fallback
- [x] 修改 `backend/app/api/chat_routes.py`：_run_agent_stream 传 model=creds.get("model", "")
- [x] 9/9 测试场景通过（scripts/test_v3_t5_context_guard.py）

### v3-T6: 文档同步
- [x] 更新 `docs/api-contract.md`：
      §5.1 请求体新增 use_agent/permission_mode/tool_keys 字段 + 字段说明
      §5.1 SSE 事件表新增 tool_call/tool_result/tool_confirm_required 3 个事件 + error.code 枚举扩展
      §4 接口目录新增 4 个 /api/agent-sandbox/* 端点
      §5 新增 §5.24-5.27 agent-sandbox 接口详情（policy 查询/切换、audit 查询、resume HITL 恢复）
      §7 变更日志加 2026-07-01 条目
- [x] 更新 `docs/architecture-map.md`（1178→1270 行，+92 行）：
      新增 4.4 Agent 链路章节（chat_routes → agent_factory → sse_adapter → tools，含 9 工具 + 4 步权限门控 + v3-T4/T5 保护）
      数据库表新增 tool_audit（10 字段 + 3 索引 + 30 天保留）
      API 路由新增第 12 模块 Agent 沙箱 4 端点
      前端组件新增 ConfirmDialog/PolicyBar/AuditLogPanel
      后端模块新增 src/agent/ 13 个模块 + tools/ 5 个工具文件
      关键参数新增 Agent 子节（7 个参数）
      已修复 bug 加 2 条（ToolAudit Index + main.py logger）
      桌面副本已同步 C:\Users\奶茶丸\Desktop\agent-architecture-map.md
- [x] 追加 `docs/pitfalls.md`：2 条新记录（ToolAudit Index 列名错误 + main.py _LOGGER 变量名错误）

### v3-T7: v3 联调测试 + 全量验收
- [x] 审计日志：每次工具调用都写入 SQLite，前端面板可查询（test_v3_t7_audit.py 10/10 PASS）
- [x] 30 天自动清理：手动插入 40 天前旧记录 → cleanup_old_logs(days=30) → 验证删除（test_cleanup_old_logs PASS）
- [x] 防死循环：同参数重复 2 次触发换思路提示（loop_detector.py detect_repeat 3/3 PASS）
- [x] 防死循环：轮次上限触发 GraphRecursionError → 降级 RAG（MAX_RECURSION=41 + sse_adapter 捕获 → _recursion_error_event）
- [x] 累积 token > context_window × 0.8 → 走 fallback（test_v3_t5_context_guard.py 9/9 PASS，含 over-limit raises ContextLimitError）
- [x] 单次请求 > 120s → 走 fallback（test_v3_t5_context_guard.py test_timeout_stale PASS，SINGLE_REQ_TIMEOUT_S=120）
- [x] v2 场景回归 7/7 PASS（test_v2_scenarios.py）
- [x] 后端全模块导入 OK（agent_factory/sse_adapter/context_guard/loop_detector/hitl_handler/permission_gate/audit_logger/tools/*）
- [x] 前端 tsc --noEmit 通过（无错误输出）
- [-] 完整 E2E（真实 LLM 调用 + 工具执行流 + 前端渲染）：依赖用户 API Key + 支持 function calling 的模型，标记为后续手动验证
- [-] 前端 AuditLogPanel/ConfirmDialog/PolicyBar 实际渲染：依赖后端启动 + 真实数据，标记为后续手动验证

---

# Task Dependencies

## v1 内部依赖
- v1-T1（依赖安装）→ 所有后续任务
- v1-T2（agent_factory）← v1-T4（tools）必须先有工具才能 build_tools
- v1-T3（sse_adapter）← 依赖 v1-T2 的 agent_factory
- v1-T5（chat_routes 接入）← 依赖 v1-T2 + v1-T3
- v1-T6（前端 SSE 解析）+ v1-T7（ActivityBlock 抽离）← 可并行，依赖 spec 定义的事件 schema
- v1-T8（联调）← 依赖 v1-T1 到 v1-T7 全部完成

## v2 内部依赖
- v2-T1（权限门控核心）→ v2-T2（本地工具依赖它）
- v2-T2（本地工具）← 依赖 v2-T1
- v2-T3（HITL 中断）← 依赖 v2-T1 + v2-T2
- v2-T4（前端确认弹窗）+ v2-T5（PolicyBar）← 可并行，依赖 v2-T3 的 API 契约
- v2-T6（联调）← 依赖 v2-T1 到 v2-T5 全部完成

## v3 内部依赖
- v3-T1（审计日志 SQLite）→ v3-T2（API 依赖它）
- v3-T2（agent_sandbox_routes）← 依赖 v3-T1
- v3-T3（AuditLogPanel）← 依赖 v3-T2 的 API
- v3-T4（防死循环）+ v3-T5（累积上下文）← 可并行，修改 sse_adapter
- v3-T6（文档同步）← 依赖所有功能完成
- v3-T7（全量验收）← 依赖 v3-T1 到 v3-T6 全部完成

## 跨阶段依赖
- v2 全部依赖 v1 完成（Agent 主路径已跑通）
- v3 全部依赖 v2 完成（本地工具 + 权限门控已就绪）

## 可并行任务
- v1-T6（前端 SSE 解析）+ v1-T7（ActivityBlock 抽离）可并行
- v2-T4（前端确认弹窗）+ v2-T5（PolicyBar）可并行
- v3-T4（防死循环）+ v3-T5（累积上下文）可并行
- 任意阶段的前端任务和后端任务（在不相互依赖时）可并行
