# Checklist — LangGraph ReAct Agent 全链路接入

> 验收检查点。每完成一个阶段，对照本清单逐项核验。所有项必须通过才能进入下一阶段。

---

## v1 验收 — Agent 核心链路跑通

### 依赖与基础设施
- [ ] `backend/requirements.txt` 含 `langgraph>=0.2.50` 和 `tavily-python>=0.5.0`
- [ ] `backend/.env.example` 已创建，含 TAVILY_API_KEY 配置项说明
- [ ] `backend/sandbox_workspace/` 目录存在，含 `.gitkeep`
- [ ] `backend/.gitignore` 含 `sandbox_workspace/`
- [ ] 残留的 `backend/app/api/__pycache__/sandbox_routes.cpython-313.pyc` 已清理
- [ ] `pip install -r backend/requirements.txt` 成功，无依赖冲突

### Agent 核心模块
- [ ] `backend/src/agent/prompts.py` 存在，含 `SYSTEM_PROMPT` 常量和命名常量（MAX_RECURSION 等）
- [ ] `backend/src/agent/agent_factory.py` 存在，含 `create_hardware_agent()` 函数
- [ ] `create_hardware_agent` 用 `ChatOpenAI` 包装模型（非 LLMClient）
- [ ] `create_hardware_agent` 传原始 LLM 给 `create_react_agent`（未 bind_tools）
- [ ] `create_react_agent` 配置 `checkpointer=MemorySaver()` + `interrupt_before=["tools"]`
- [ ] `agent_factory.py` 含 `build_tools(payload)` 函数，v1 装 5 个工具
- [ ] `agent_factory.py` 含 `_should_use_agent(payload)` 判断函数
- [ ] `recursion_limit=41` 在 `astream(config={...})` 传，不是构造参数

### SSE 适配器
- [ ] `backend/src/agent/sse_adapter.py` 存在，含 `stream_agent_to_sse()` 函数
- [ ] 用 `stream_mode=["messages", "updates"]` 多模式
- [ ] AIMessage chunk 的 content → SSE `text` 事件
- [ ] AIMessage chunk 的 tool_calls → SSE `tool_call` 事件（含 tool/args/call_id/step_index）
- [ ] ToolMessage → SSE `tool_result` 事件（含 call_id/tool/result/duration/success/step_index）
- [ ] 工具失败时 `tool_result` 的 `success:false`，`result.error` 含错误信息

### 工具实现
- [ ] `backend/src/agent/tools/__init__.py` 存在
- [ ] `backend/src/agent/tools/wrappers.py` 含 SearchDocsTool / AuditPinsTool / WiringTool
- [ ] SearchDocsTool 的 args_schema 只含 `query`（top_k/kb_ids/threshold 通过 `__init__` 注入，LLM 不可改）
- [ ] 每个工具同时实现 `_run` 和 `_arun`
- [ ] `backend/src/agent/tools/web_search.py` 含 WebSearchTool，`__init__` 注入 tavily_api_key
- [ ] WebSearchTool 失败降级返回 `{"output": "网页搜索失败，请基于本地知识库回答"}`
- [ ] `backend/src/agent/tools/generate_code.py` 含 GenerateCodeTool，内部调 LLM
- [ ] 每条 web_search 结果截断 300 chars

### chat_routes 接入
- [ ] `ChatRequest` 新增 `use_agent: bool = False` / `permission_mode: str = "default"` / `tool_keys: dict[str, str] = {}`
- [ ] L121 后有 `if use_agent:` 条件分支
- [ ] Agent 主路径调 `stream_agent_to_sse()`
- [ ] 原 LLM 流式逻辑提取为 `_fallback_rag_chat()` 函数
- [ ] 捕获 `GraphRecursionError` → yield error 事件 → 调 `_fallback_rag_chat()`
- [ ] L178 的 `# TODO: ReAct loop` 注释已删除

### 前端 SSE 解析 + 类型
- [ ] `frontend/src/types/session.ts` 的 ActivityStep 含 `call_id?` / `risk_level?` / `decision_source?`
- [ ] `frontend/src/types/session.ts` 的 ActivityStep 保持 `duration` 命名（未改为 duration_ms）
- [ ] `useChatStore.ts` SSE 解析含 `tool_call` 分支，构建 pending 状态的 step
- [ ] `useChatStore.ts` SSE 解析含 `tool_result` 分支，按 call_id 更新 step 为 done
- [ ] `useChatStore.ts` 请求体含 `use_agent` / `permission_mode` / `tool_keys.tavily`
- [ ] `useSettingsStore.ts` 含 `permissionMode` 字段，默认 `"default"`
- [ ] `permissionMode` 在 PERSIST_KEYS 中

### 前端 ActivityBlock 抽离
- [ ] `frontend/src/components/chat/ActivityBlock.tsx` 存在为独立组件
- [ ] `ChatArea.tsx` 不再含局部 ActivityBlock/ThinkingStep/ToolStep 函数
- [ ] `ChatArea.tsx` 通过 `import { ActivityBlock } from "./ActivityBlock"` 引用
- [ ] ToolStep 渲染 `risk_level` 徽章（HIGH 红/MEDIUM 黄/LOW 灰）
- [ ] ToolStep 支持 pending 状态（收到 tool_call 未收到 tool_result 时 spinner）

### v1 联调测试
- [ ] 后端启动无报错：`python main.py --web --port 58080`
- [ ] 前端启动无报错：`npx vite --port 5173`
- [ ] "ch340g 怎么接线" → Agent 调 search_docs + wiring → 返回 SVG
- [ ] "ESP32-S3 GPIO0 和 GPIO1 同时做输出会冲突吗" → Agent 调 audit_pins
- [ ] "写一个 ESP32-S3 读取 CH340g 串口数据的代码" → Agent 调 search_docs + generate_code
- [ ] "你好" → Agent 第 1 轮直接回答，无工具调用
- [ ] 模型不支持 function calling → 返回明确错误
- [ ] Agent 失败 → 降级 fallback RAG

### v1 代码规范
- [ ] 所有新函数 ≤ 10 行
- [ ] 所有新文件 ≤ 300 行
- [ ] 圈复杂度 ≤ 10
- [ ] max-params ≤ 3
- [ ] 无魔法数字（用命名常量）
- [ ] Python 函数有类型注解
- [ ] 外部调用有 try/except

---

## v2 验收 — 本地工具 + 权限门控 + HITL

### 权限门控核心
- [ ] `backend/src/agent/path_guard.py` 存在
- [ ] `ALLOWED_DIRS` 含项目根 + %TEMP%\agent-sandbox\ + 用户白名单
- [ ] `DENY_PATTERNS` 含 .git/ / .vscode/ / .idea/ / .claude/ / settings.json / .env / *.key / *.pem / *credentials* / ..
- [ ] `validate_path(path, is_write)` 用 `os.path.realpath()` + startswith 校验
- [ ] `backend/src/agent/risk_classifier.py` 存在
- [ ] HIGH_RISK_KEYWORDS 含 rm -rf / del /s / format / regedit / shutdown 等
- [ ] MEDIUM_RISK_KEYWORDS 含 pip install / npm install / git push / curl / wget 等
- [ ] LOW_RISK_KEYWORDS 含 ls / dir / cat / echo / python xxx.py 等
- [ ] `classify_risk(tool, args)` 返回 "high"/"medium"/"low"/"n/a"
- [ ] `backend/src/agent/permission_gate.py` 存在
- [ ] `check_permission(tool, args, mode)` 返回 `{decision, reason, risk_level}`
- [ ] 4 步流程顺序正确：path_guard → 模式分流 → risk_classifier → 返回

### 本地工具
- [ ] `backend/src/agent/tools/file_ops.py` 含 ReadFileTool / WriteFileTool / EditFileTool
- [ ] ReadFileTool args_schema 含 path + offset(默认 0) + limit(默认 2000)
- [ ] WriteFileTool args_schema 含 path + content
- [ ] EditFileTool args_schema 含 path + old_string + new_string + replace_all(默认 false)
- [ ] 每个工具 `_arun` 先调 `permission_gate.check_permission()`
- [ ] 决策 allow → 执行 + 写审计日志（v2 可用 logger 占位）
- [ ] 决策 ask → 抛 `PermissionAskError`
- [ ] 决策 deny → 抛 `PermissionDenyError`
- [ ] `backend/src/agent/tools/run_command.py` 含 RunCommandTool
- [ ] RunCommandTool args_schema 含 command + timeout_ms(默认 30000) + cwd
- [ ] RunCommandTool 用 `asyncio.create_subprocess_shell` + `asyncio.wait_for` 超时控制
- [ ] stdout/stderr 各截断 5000 chars
- [ ] 返回 `{stdout, stderr, exit_code, duration, timed_out}`

### HITL 中断
- [ ] `sse_adapter.py` 检测 `interrupt_before=["tools"]` 中断
- [ ] 中断时 yield `tool_call` 事件含 `tool_confirm_required: true`
- [ ] `resume_agent_after_user(agent, config, decision)` 函数存在
- [ ] 用户允许 → `agent.update_state` + `agent.astream(None)` 继续
- [ ] 用户拒绝 → ToolMessage("用户拒绝")
- [ ] 用户拒绝并停止 → 中断循环
- [ ] `POST /api/agent-sandbox/resume` 接口存在

### 前端确认弹窗
- [ ] `frontend/src/components/chat/ConfirmDialog.tsx` 存在
- [ ] 4 按钮：允许本次 / 永久允许 / 拒绝 / 拒绝并停止
- [ ] 显示工具名 + 参数摘要 + 风险等级徽章
- [ ] 点击按钮调 `POST /api/agent-sandbox/resume`
- [ ] `useChatStore.ts` 含 `pendingConfirm` 状态
- [ ] 收到 `tool_confirm_required: true` → 设置 pendingConfirm
- [ ] 用户决策后 → 调 resume API + 清除 pendingConfirm

### 前端 PolicyBar
- [ ] `frontend/src/components/chat/PolicyBar.tsx` 存在
- [ ] 3 个按钮：bypassPermissions / default / acceptEdits
- [ ] 当前模式高亮
- [ ] 点击更新 `useSettingsStore.permissionMode`
- [ ] PolicyBar 挂载在聊天输入框附近

### v2 联调测试
- [ ] "帮我读一下 backend/main.py 的前 50 行" → read_file（acceptEdits 放行）
- [ ] "在项目里写个 main.py 跑 hello world" → write_file（弹确认）→ run_command（acceptEdits 放行）
- [ ] "把 backend/main.py 第 10 行的 port 改成 8080" → edit_file（弹确认）
- [ ] "跑一下 python --version" → run_command（acceptEdits 放行）
- [ ] HIGH 风险命令 `rm -rf` 在 acceptEdits 仍弹确认
- [ ] 强制 deny 路径 `.git/config` 任何模式都拒绝
- [ ] 用户点"拒绝并停止" → Agent 循环中断

### v2 代码规范
- [ ] 所有新函数 ≤ 10 行
- [ ] 所有新文件 ≤ 300 行
- [ ] 无魔法数字
- [ ] Python 函数有类型注解
- [ ] 外部调用有 try/except

---

## v3 验收 — 审计日志 + 设置面板 + 收尾

### 审计日志 SQLite
- [ ] `backend/app/db/models.py` 含 `ToolAudit` 表模型
- [ ] ToolAudit 表含 id/timestamp/session_id/tool_name/args_summary/decision/decision_source/risk_level/exit_code/duration_ms/error 字段
- [ ] alembic 迁移脚本存在或 `create_all` 自动建表
- [ ] `backend/src/agent/audit_logger.py` 存在
- [ ] `log_tool_call()` 函数存在，参数完整
- [ ] `cleanup_old_logs(days=30)` 函数存在
- [ ] `query_logs(session_id, limit)` 函数存在
- [ ] 启动时调 `cleanup_old_logs()`
- [ ] `file_ops.py` 和 `run_command.py` 的 logger 占位已替换为 `audit_logger.log_tool_call()`
- [ ] `permission_gate.py` 每次决策落地调 `audit_logger.log_tool_call()`

### agent_sandbox_routes API
- [ ] `backend/app/api/agent_sandbox_routes.py` 存在
- [ ] `GET /api/agent-sandbox/policy` 返回当前 permission_mode
- [ ] `POST /api/agent-sandbox/policy` 切换 permission_mode
- [ ] `GET /api/agent-sandbox/audit?session_id=xxx&limit=100` 返回审计日志
- [ ] `POST /api/agent-sandbox/resume` v2 已实现，此处路由注册
- [ ] `backend/main.py` 注册了 agent_sandbox_routes

### 前端 AuditLogPanel
- [ ] `frontend/src/components/settings/AuditLogPanel.tsx` 存在
- [ ] 按 session_id 查询审计日志
- [ ] 展示列表：工具名 + 参数摘要 + 决策徽章 + 风险等级徽章 + 耗时 + 结果
- [ ] 支持按风险等级/决策过滤
- [ ] `frontend/src/components/settings/SettingsPage.tsx` 的 TAB_IDS 含 `"audit"`
- [ ] activeTab === "audit" 时渲染 `<AuditLogPanel />`

### 防死循环完善
- [ ] `backend/src/agent/loop_detector.py` 存在
- [ ] `_args_hash(tool, args)` 用 MD5
- [ ] `detect_loop(call_history)` 返回 None/"repeat"/"no_progress"
- [ ] 重复检测：同工具+同参数 hash 连续 2 次 → "repeat"
- [ ] 无进展检测：3 次 tool_result output hash 相同 → "no_progress"
- [ ] `sse_adapter.py` 维护 call_history
- [ ] 检测到循环 → 注入提示 ToolMessage
- [ ] 累积 10 轮 → yield SSE 提示（不中断）
- [ ] 单次请求 > 120s → 抛 `AgentTimeoutError` → 走 fallback

### 累积上下文保护
- [ ] `sse_adapter.py` 用 `contextvars.ContextVar` 存累积 token
- [ ] 每次 AIMessage/ToolMessage 后用 `LLMClient._estimate_tokens()` 估算
- [ ] 累积 > `model_registry.get_max_tokens(model) * 0.8` → 抛 `ContextLimitError` → 走 fallback

### 文档同步
- [ ] `docs/api-contract.md` 含 ChatRequest 新字段说明
- [ ] `docs/api-contract.md` 含 SSE `tool_call` / `tool_result` 事件 schema
- [ ] `docs/api-contract.md` 含 `/api/agent-sandbox/*` 接口章节
- [ ] `docs/architecture-map.md` 含 Agent 链路
- [ ] `docs/architecture-map.md` 含 ToolAudit 表
- [ ] `docs/architecture-map.md` 含前端 PolicyBar/ConfirmDialog/AuditLogPanel 组件
- [ ] 桌面副本 `C:\Users\奶茶丸\Desktop\agent-architecture-map.md` 已同步
- [ ] 实施中的踩坑已追加到 `docs/pitfalls.md`

### v3 联调测试 + 全量验收
- [ ] 审计日志：每次工具调用都写入 SQLite，前端面板可查询
- [ ] 30 天自动清理：手动插入旧记录 → 重启 → 验证删除
- [ ] 防死循环：同参数重复 2 次触发换思路提示
- [ ] 防死循环：20 轮硬上限触发 GraphRecursionError → 降级 RAG
- [ ] 累积 token > context_window × 0.8 → 走 fallback
- [ ] 单次请求 > 120s → 走 fallback
- [ ] v1 全部 8 个测试场景回归通过
- [ ] v2 全部 7 个测试场景回归通过
- [ ] v3 全部 6 个测试场景通过

### v3 代码规范
- [ ] 所有新函数 ≤ 10 行
- [ ] 所有新文件 ≤ 300 行
- [ ] 无魔法数字
- [ ] Python 函数有类型注解
- [ ] 外部调用有 try/except
- [ ] 所有路径校验用 `os.path.realpath()`（防符号链接绕过）
