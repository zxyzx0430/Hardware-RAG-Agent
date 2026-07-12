# LangGraph ReAct Agent 全链路接入 Spec

> 基于 `docs/superpowers/specs/2026-06-30-agent-react-design.md`（已经过三轮审查修订）
> 范围：后端 Agent 核心 + 9 工具 + 权限门控 + 审计日志 + 前端 3 组件，按 v1/v2/v3 三阶段交付
> 目标：7.15 字节创造力大赛 demo 前接入完整 LangGraph ReAct Agent，覆盖「检索 → 分析 → 代码」三段式

---

## Why

当前 `/api/chat` 是单轮 RAG + LLM 直连（[chat_routes.py L178](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L178) 仍是 `# TODO: ReAct loop`），LLM 无法自主决策调工具、无法多轮推理。需要接入 LangGraph ReAct loop，让 Agent 自主决定「查什么、调哪个工具、何时结束」，覆盖 demo 场景：

- "ch340g 怎么接线" → Agent 调 search_docs 查手册 → 调 wiring 生成 SVG
- "ESP32-S3 GPIO0 和 GPIO1 同时做输出会冲突吗" → Agent 调 audit_pins 返回冲突报告
- "写一个 ESP32-S3 读取 CH340g 串口数据的代码" → Agent 调 search_docs + generate_code 返回完整代码
- "在项目里写个 main.py 跑 hello world" → Agent 调 write_file（用户确认）→ run_command → 返回结果

---

## What Changes

### 后端（新建 + 修改）

- 🆕 `backend/src/agent/agent_factory.py` — `create_hardware_agent()` 封装 `create_react_agent`
- 🆕 `backend/src/agent/sse_adapter.py` — `stream_agent_to_sse()` 把 langgraph stream 转 SSE 事件
- 🆕 `backend/src/agent/loop_detector.py` — 重复检测 + 无进展检测
- 🆕 `backend/src/agent/prompts.py` — Agent 系统 prompt + 工具描述
- 🆕 `backend/src/agent/permission_gate.py` — 4 步权限门控核心（path_guard→模式分流→风险分级→决策落地）
- 🆕 `backend/src/agent/risk_classifier.py` — V1 关键字黑名单分级
- 🆕 `backend/src/agent/path_guard.py` — 工作目录范围校验（允许目录 + 强制 deny）
- 🆕 `backend/src/agent/audit_logger.py` — SQLite 审计日志写入（30 天保留）
- 🆕 `backend/src/agent/tools/` — 9 个 BaseTool 实现目录
  - `wrappers.py` — search_docs/audit_pins/wiring 包装成 BaseTool
  - `web_search.py` — Tavily web_search（key 从前端 toolKeys.tavily 注入）
  - `generate_code.py` — 内部调 LLM 生成硬件代码
  - `file_ops.py` — read_file/write_file/edit_file
  - `run_command.py` — 本地 shell 执行（含超时控制）
- 🆕 `backend/app/api/agent_sandbox_routes.py` — 策略查询/切换 API + 审计日志查询 API
- ✏️ `backend/app/api/chat_routes.py` — ChatRequest 新增 `use_agent` / `permission_mode` / `tool_keys` 字段；L121 后条件分支 `if use_agent`
- ✏️ `backend/app/db/models.py` — 新增 ToolAudit 表模型
- ✏️ `backend/requirements.txt` — 新增 `langgraph>=0.2.50` + `tavily-python>=0.5.0`
- ✏️ `backend/.env.example` — 新建（项目目前无此文件），含 TAVILY_API_KEY 说明
- 🆕 `backend/sandbox_workspace/` — 沙箱工作目录（加入 .gitignore）
- ✏️ `backend/.gitignore` — 新增 `sandbox_workspace/`
- 🧹 清理残留 `backend/app/api/__pycache__/sandbox_routes.cpython-313.pyc`

### 前端（修改 + 新建）

- ✏️ `frontend/src/stores/useChatStore.ts` — SSE 解析新增 `tool_call`/`tool_result` 事件类型；请求体新增 `use_agent`/`permission_mode`/`tool_keys.tavily`
- ✏️ `frontend/src/stores/useSettingsStore.ts` — 新增 `permissionMode` 状态字段（bypassPermissions/default/acceptEdits），默认 `default`
- ✏️ `frontend/src/types/session.ts` — ActivityStep 扩展 `call_id`/`risk_level`/`decision_source` 字段（`duration` 命名保持不变）
- ✏️ `frontend/src/components/chat/ChatArea.tsx` — ActivityBlock 从局部函数抽离为独立组件文件
- 🆕 `frontend/src/components/chat/ActivityBlock.tsx` — 独立工具调用步骤卡片组件（支持风险等级/决策来源渲染）
- 🆕 `frontend/src/components/chat/PolicyBar.tsx` — 策略切换条（3 种模式切换）
- 🆕 `frontend/src/components/chat/ConfirmDialog.tsx` — 确认弹窗（4 按钮：允许本次/永久允许/拒绝/拒绝并停止）
- 🆕 `frontend/src/components/settings/AuditLogPanel.tsx` — 审计日志面板（按 session_id 查询工具调用历史）
- ✏️ `frontend/src/components/settings/SettingsPage.tsx` — TAB_IDS 新增 `audit` tab

### 关键修订点（与设计文档的差异）

1. **字段命名对齐**：SSE 事件和 ActivityStep 统一用 `duration`（非设计文档的 `duration_ms`），保持前端向后兼容
2. **Tavily key 传输方式**：不走后端 `.env`，走前端 `toolKeys.tavily` 配置，Chat 请求时随 `tool_keys` 字段传给后端，web_search 工具实例化时注入（与 search_docs 的 top_k/kb_ids 注入模式一致）
3. **范围拆分**：spec 覆盖设计文档全部内容，tasks 按 v1/v2/v3 三阶段拆分（详见 tasks.md）

---

## Impact

- **Affected specs**：无（本 spec 是新增能力，不修改已有 spec）
- **Affected code**：
  - 后端核心：`backend/src/agent/`（新增 9 个文件 + tools/ 目录）、`backend/app/api/chat_routes.py`、`backend/app/api/agent_sandbox_routes.py`
  - 后端数据：`backend/app/db/models.py`（新增 ToolAudit 表）
  - 前端核心：`frontend/src/stores/useChatStore.ts`、`frontend/src/stores/useSettingsStore.ts`、`frontend/src/types/session.ts`
  - 前端组件：`frontend/src/components/chat/`（新增 3 个 + 抽离 1 个）、`frontend/src/components/settings/`（新增 1 个）
- **Affected docs**：`docs/api-contract.md`（新增 Agent 调度 + agent_sandbox_routes 章节）、`docs/architecture-map.md`（同步全景图）、`docs/pitfalls.md`（实施中追加踩坑）

---

## ADDED Requirements

### Requirement: LangGraph ReAct Agent 核心

系统 SHALL 用 `langgraph.prebuilt.create_react_agent` 构建 ReAct Agent，封装在 `backend/src/agent/agent_factory.py` 的 `create_hardware_agent()` 函数中。

- 用 `langchain_openai.ChatOpenAI` 包装模型（不能直接传项目 `LLMClient`，因为它不是 `BaseChatModel`）
- 传原始 LLM 给 `create_react_agent`（不要传已 `bind_tools` 的 LLM，prebuilt 内部会 bind）
- `recursion_limit=41`（20 轮硬上限）在 `astream(config={...})` 传，不是构造参数
- `interrupt_before=["tools"]` 配合 `checkpointer=MemorySaver()` 实现 HITL 中断
- `stream_mode=["messages", "updates"]` 同时拿 AIMessage chunks 和 ToolMessage

#### Scenario: Agent 自主决策调工具

- **WHEN** 用户问 "ch340g 怎么接线"
- **THEN** Agent 第 1 轮调 `search_docs` 查手册 → 第 2 轮调 `wiring` 生成 SVG → 第 3 轮 AgentFinish 返回结果
- **AND** 前端收到 `tool_call` → `tool_result` → `tool_call` → `tool_result` → `text` 事件序列

#### Scenario: Agent 直接回答非工具型问题

- **WHEN** 用户问 "你好"
- **THEN** Agent 第 1 轮就 AgentFinish，无工具调用开销
- **AND** 前端只收到 `text` 事件

#### Scenario: 防死循环 - 同参数重复

- **WHEN** Agent 连续 2 次用相同参数调用同一工具
- **THEN** `loop_detector.detect_loop()` 返回 `"repeat"`
- **AND** 注入提示 "不要重复调用 X，尝试基于已有信息回答"

#### Scenario: 防死循环 - 20 轮硬上限

- **WHEN** Agent 调用轮数达到 `recursion_limit=41`
- **THEN** 抛 `GraphRecursionError`
- **AND** 捕获后降级到 fallback RAG 路径，yield SSE `error` 事件提示用户

---

### Requirement: SSE 事件 Schema 扩展

系统 SHALL 新增 2 个 SSE 事件类型 `tool_call` 和 `tool_result`，替换现有的单 `tool` 事件（fallback 路径仍用 `tool` 事件）。

#### Scenario: tool_call 事件

- **WHEN** Agent 决定调用工具（从 AIMessage.tool_calls 提取）
- **THEN** yield SSE 事件 `{"type":"tool_call", "tool":"search_docs", "args":{"query":"GPIO 配置"}, "call_id":"call_1", "step_index":1}`

#### Scenario: tool_result 事件

- **WHEN** 工具执行完成（从 ToolMessage 提取）
- **THEN** yield SSE 事件 `{"type":"tool_result", "call_id":"call_1", "tool":"search_docs", "result":{"output":"找到 5 条相关片段..."}, "duration":234, "success":true, "step_index":1}`

#### Scenario: 工具失败

- **WHEN** 工具执行抛异常
- **THEN** yield `tool_result` 事件，`success:false`，`result.error` 包含错误码和消息
- **AND** Agent 收到 ToolMessage 后继续推理（不降级）

---

### Requirement: 9 个工具集

系统 SHALL 提供 9 个 BaseTool 实现，分 4 类。所有本地工具路径必须是绝对路径（防止相对路径绕过目录限制）。

| # | 工具 | 类型 | 实现方式 | 权限 |
|---|---|---|---|---|
| 1 | `search_docs` | 检索 | 包装现有 `search_docs_core`，top_k/kb_ids/threshold 通过工具实例化注入（LLM 不可改） | auto |
| 2 | `web_search` | 检索 | 调 Tavily API，key 从前端 `toolKeys.tavily` 注入；失败降级返回提示 | auto |
| 3 | `audit_pins` | 硬件 | 包装现有 `audit_pins_core` | auto |
| 4 | `wiring` | 硬件 | 包装现有 `generate_wiring_svg` | auto |
| 5 | `generate_code` | 代码 | 内部调 LLM（复用 LLMClient，超时降级小模型）+ 硬件代码模板 | auto |
| 6 | `read_file` | 本地文件 | 默认 2000 行 + offset/limit，LLM 决定分页 | 见权限门控 |
| 7 | `write_file` | 本地文件 | 结构化参数（path+content），不走 shell | 见权限门控 |
| 8 | `edit_file` | 本地文件 | 精确替换（old_string→new_string），比 sed 安全 | 见权限门控 |
| 9 | `run_command` | 本地 shell | 含超时控制（默认 30s，上限 5min），stdout/stderr 各 5000 chars 截断 | 见权限门控 |

#### Scenario: search_docs 参数注入

- **WHEN** 用户前端配置 `topK=5` / `relevanceThreshold=70` / `selectedKbIds=["kb1"]`
- **THEN** `SearchDocsTool` 实例化时注入 `top_k=5` / `kb_ids=["kb1"]` / `threshold=0.7`
- **AND** LLM 只能传 `query` 参数，无法覆盖检索策略

#### Scenario: web_search 失败降级

- **WHEN** Tavily API 调用失败（网络/限流/key 无效）
- **THEN** 返回 `{"output": "网页搜索失败，请基于本地知识库回答"}`
- **AND** 不中断 Agent 循环

#### Scenario: run_command 超时

- **WHEN** 命令执行超过 `timeout_ms`
- **THEN** 终止进程，返回 `{"exit_code": -1, "timed_out": true, "stderr": "执行超时"}`
- **AND** 写审计日志

---

### Requirement: 权限门控 4 步流程

系统 SHALL 对 4 个本地工具（read_file/write_file/edit_file/run_command）实施 4 步权限门控，封装在 `backend/src/agent/permission_gate.py`。

**4 步流程**（短路）：
1. `path_guard` 路径校验 — 写操作检查目标在允许目录内；读操作检查不在强制 deny 列表
2. 按当前 `permission_mode` 分流 — bypassPermissions: ALLOW / default: ASK / acceptEdits: 进入步骤 3
3. `risk_classifier` 风险分级 — run_command 关键字黑名单匹配（HIGH/MEDIUM/LOW）；文件操作默认 ALLOW
4. 决策落地 — ALLOW 执行+写日志 / ASK 挂起等用户确认 / DENY 拒绝+写日志

**3 种权限模式**：
- `bypassPermissions` — 完全放开（演示/可信环境）
- `default` — 全部询问（谨慎模式，默认值）
- `acceptEdits` — 文件编辑自动通过，危险命令仍询问（日常开发）

**强制 deny 路径**（任何模式下都拒绝）：`.git/` / `.vscode/` / `.idea/` / `.claude/` / `settings.json` / `.env` / `*.key` / `*.pem` / `*credentials*` / 项目根目录上级路径（`..` 跳出）

**风险关键字黑名单 V1**：
- HIGH（自动审查模式也要问）：`rm -rf` / `del /s` / `rmdir /s` / `Remove-Item -Recurse` / `format` / `diskpart` / `regedit` / `shutdown` / `reboot` / `taskkill /f` / `> C:\Windows` / `> /etc/`
- MEDIUM（自动审查模式也要问）：`pip install` / `npm install` / `yarn add` / `pnpm add` / `git push` / `git reset --hard` / `git clean -f` / `curl` / `wget` / `Invoke-WebRequest`
- LOW（自动审查模式直接放行）：`ls` / `dir` / `cat` / `type` / `echo` / `pwd` / `python xxx.py` / `node xxx.js`

#### Scenario: HIGH 风险命令在 acceptEdits 仍弹确认

- **WHEN** permission_mode=acceptEdits，Agent 调 `run_command("rm -rf /tmp/foo")`
- **THEN** risk_classifier 匹配 HIGH 关键字 `rm -rf`
- **AND** 决策为 ASK，前端弹 ConfirmDialog

#### Scenario: LOW 风险命令在 acceptEdits 直接放行

- **WHEN** permission_mode=acceptEdits，Agent 调 `run_command("python --version")`
- **THEN** risk_classifier 匹配 LOW
- **AND** 决策为 ALLOW，直接执行不弹窗

#### Scenario: 强制 deny 路径

- **WHEN** Agent 调 `write_file(path="e:/Desktop/agent/.git/config", content="...")`
- **THEN** path_guard 检测到 `.git/` 在强制 deny 列表
- **AND** 决策为 DENY，返回错误，写审计日志

---

### Requirement: HITL 用户确认弹窗

系统 SHALL 在工具调用需要用户确认时，通过 `interrupt_before=["tools"]` 中断 Agent，前端弹 ConfirmDialog。

**4 按钮**：
- 允许本次（`user_temporary`）— 仅本次放行，下次同类还要问
- 永久允许（`user_permanent`）— 写入白名单规则，以后同类自动放行
- 拒绝（`user_reject`）— 拒绝本次，Agent 收到拒绝消息继续推理
- 拒绝并停止（`user_reject + interrupt`）— 拒绝并中断整个 Agent 循环

#### Scenario: 用户允许

- **WHEN** 前端收到 `tool_call` 事件含 `tool_confirm_required:true`
- **THEN** 弹 ConfirmDialog
- **AND** 用户点"允许本次"
- **AND** 前端调 `agent.update_state` 注入 ToolMessage("用户允许") + `agent.astream(None)` 继续

#### Scenario: 用户拒绝并停止

- **WHEN** 用户点"拒绝并停止"
- **THEN** Agent 循环中断
- **AND** 前端 yield `done` 事件，`success:false`，`reason:"user_stopped"`

---

### Requirement: 审计日志 SQLite 持久化

系统 SHALL 把每次工具调用决策写入 SQLite `tool_audit` 表，保留 30 天，超期自动清理。

**表结构**：
| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | UUID |
| timestamp | INTEGER | Unix 毫秒 |
| session_id | TEXT | 会话 ID |
| tool_name | TEXT | run_command / write_file / ... |
| args_summary | TEXT | 参数摘要（前 200 字符，防泄漏） |
| decision | TEXT | allow / ask / deny |
| decision_source | TEXT | mode_bypass / mode_default / classifier_low / classifier_high / user_temporary / user_permanent / user_reject / path_deny |
| risk_level | TEXT | low / medium / high / n/a |
| exit_code | INTEGER NULL | 执行结果（未执行为 null） |
| duration_ms | INTEGER | 执行耗时（字段名用 duration_ms，数据库列名） |
| error | TEXT | 错误信息 |

#### Scenario: 写审计日志

- **WHEN** 工具调用决策落地（ALLOW/ASK/DENY）
- **THEN** 写入 `tool_audit` 表，包含上述字段
- **AND** ALLOW/ASK 记录执行结果（exit_code/duration_ms/error）

#### Scenario: 30 天自动清理

- **WHEN** 启动时检查 `tool_audit` 表
- **THEN** 删除 `timestamp < now - 30 days` 的记录

---

### Requirement: 前端 ActivityBlock 独立组件

系统 SHALL 把 `ChatArea.tsx` 内的局部 `ActivityBlock` 函数抽离为独立组件 `frontend/src/components/chat/ActivityBlock.tsx`，并扩展渲染风险等级、决策来源、call_id 字段。

#### Scenario: 渲染工具调用步骤

- **WHEN** 收到 `tool_call` 事件
- **THEN** ActivityBlock 渲染一个 pending 状态的 ToolStep（图标+工具名+参数预览+spinner）
- **AND** 收到对应 `call_id` 的 `tool_result` 后，ToolStep 变为 done 状态（显示 duration+success/fail）

#### Scenario: 渲染风险等级徽章

- **WHEN** ToolStep 含 `risk_level: "high"`
- **THEN** 渲染红色 HIGH 徽章
- **AND** `risk_level: "low"` 渲染灰色 LOW 徽章

---

### Requirement: 前端 PolicyBar 策略切换

系统 SHALL 在聊天输入框附近新增 `PolicyBar.tsx` 组件，支持 3 种权限模式切换。

#### Scenario: 切换权限模式

- **WHEN** 用户点击 PolicyBar 的 "自动审查" 按钮
- **THEN** `useSettingsStore.permissionMode` 更新为 `acceptEdits`
- **AND** 下次 Chat 请求的 `permission_mode` 字段传 `acceptEdits`

---

### Requirement: 前端 AuditLogPanel 审计日志面板

系统 SHALL 在设置页新增 `audit` tab，展示 `AuditLogPanel.tsx` 组件，按 session_id 查询工具调用历史。

#### Scenario: 查询审计日志

- **WHEN** 用户打开设置页 audit tab
- **THEN** 调 `GET /api/agent-sandbox/audit?session_id=xxx` 查询当前会话审计日志
- **AND** 展示工具调用历史列表（工具名+参数摘要+决策+风险等级+耗时+结果）

---

### Requirement: 降级策略

系统 SHALL 在 Agent 失败时降级到现有 RAG + LLM 单轮逻辑，保留 [chat_routes.py](file:///e:/Desktop/agent/backend/app/api/chat_routes.py) L121-213 作为 fallback 路径。

**`use_agent` 判断条件**：
- 用户设置开启 Agent 模式（ChatRequest.use_agent=true）
- 当前模型支持 function calling（白名单）
- langgraph + langchain 依赖 import 成功

**降级触发场景**：
| 场景 | 降级行为 |
|---|---|
| 模型不支持 function calling | `bind_tools()` 报错 → 返回错误提示用户换模型（不走 fallback） |
| `GraphRecursionError`（20 轮硬上限） | yield 提示 → 走 fallback RAG |
| 工具执行异常 | langgraph 自动转 ToolMessage，Agent 继续推理（不降级） |
| 累积 token > context_window × 0.8 | 抛 `ContextLimitError` → 走 fallback RAG |
| 单次请求 > 120s | 抛 `AgentTimeoutError` → 走 fallback RAG |
| LangGraph import 失败 | `use_agent=False`，自动走 fallback |

#### Scenario: Agent 失败降级

- **WHEN** Agent 抛 `GraphRecursionError`
- **THEN** yield SSE `error` 事件 "Agent 模式失败，切换到基础模式"
- **AND** 调用 `_fallback_rag_chat()` 继续流式输出
- **AND** 用户看到错误提示 + 正常回答

---

### Requirement: 工具结果截断策略

系统 SHALL 对工具结果实施截断，防止累积 token 撑爆 context。

| 工具 | 截断阈值 | 截断后行为 |
|---|---|---|
| search_docs | 用户配置 top_k / 每 chunk 800 chars | 追加"...共 N 条，当前显示前 M 条" |
| web_search | max_results=5 / 每条 300 chars | 追加"...如需更多，请增大 max_results" |
| audit_pins | 不截断 | 完整返回 |
| wiring | 不截断 | SVG 不截断，BOM 完整 |
| generate_code | 不截断 | 代码必须完整 |
| read_file | offset + limit（LLM 决定，默认 2000） | 追加"...共 N 行，使用 offset=K 查看更多" |
| write_file | 不截断 | 完整返回 |
| edit_file | 不截断 | 完整返回 |
| run_command | stdout/stderr 各 5000 chars | 追加"...输出已截断，用 read_file 读取结果文件" |

**累积上下文保护**：单次请求累积 token > 模型 context_window × 0.8 → 强制走 fallback。用 `contextvars.ContextVar` 存累积值（防并发请求串状态）。

---

## MODIFIED Requirements

### Requirement: ChatRequest 字段

[chat_routes.py L51-64](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L51) 的 `ChatRequest` 新增字段：

```python
class ChatRequest(BaseModel):
    # ... 现有字段 ...
    use_agent: bool = False                    # 是否启用 Agent 模式
    permission_mode: str = "default"           # bypassPermissions / default / acceptEdits
    tool_keys: dict[str, str] = {}             # 工具密钥（如 {"tavily": "sk-xxx"}）
```

#### Scenario: Agent 模式请求

- **WHEN** 前端开启 Agent 模式
- **THEN** ChatRequest 含 `use_agent=true` / `permission_mode="acceptEdits"` / `tool_keys={"tavily":"sk-xxx"}`
- **AND** chat_routes 走 Agent 主路径

---

### Requirement: useSettingsStore 字段

[useSettingsStore.ts L9-61](file:///e:/Desktop/agent/frontend/src/stores/useSettingsStore.ts#L9) 新增 `permissionMode` 字段：

```typescript
interface SettingsState {
  // ... 现有字段 ...
  permissionMode: "bypassPermissions" | "default" | "acceptEdits";
}
```

默认值 `"default"`，加入 PERSIST_KEYS 持久化。

---

### Requirement: ActivityStep 类型

[session.ts L67-81](file:///e:/Desktop/agent/frontend/src/types/session.ts#L67) 的 `ActivityStep` 扩展字段（保持 `duration` 命名不变）：

```typescript
export interface ActivityStep {
  type: "thinking" | "tool";        // 保持不变
  id: string;                       // 保持不变
  content?: string;                 // 保持不变
  name?: string;                    // 保持不变
  icon?: string;                    // 保持不变
  args?: string;                    // 保持不变
  result?: string;                  // 保持不变
  duration?: number;                // 保持不变（命名不改为 duration_ms）
  status?: "pending" | "running" | "done" | "error";  // 保持不变
  source?: "rag" | "llm" | "reasoning";               // 保持不变
  // 新增字段
  call_id?: string;                 // 工具调用 ID（关联 tool_call 和 tool_result）
  risk_level?: "low" | "medium" | "high";             // 风险等级
  decision_source?: string;         // 决策来源（mode_bypass/user_temporary/...）
}
```

---

### Requirement: tool_router.py 定位

[tool_router.py](file:///e:/Desktop/agent/backend/src/agent/tool_router.py) 保留给非 Agent 路径（CLI 直接调工具），标注为"非 Agent 路径"。ReAct Agent 不走它，直接用 langchain BaseTool。

---

## REMOVED Requirements

### Requirement: code_executor 工具

**Reason**：设计文档第 11.1 节提到要删除 `tool_router.py L309-334` 的 CodeExecutorTool 注册，但实际探索发现该工具已被完全删除（Grep 验证 0 匹配，L309-334 实际是 `register_mcp_tools` 函数）。本 spec 无需再删除。
**Migration**：无（已删除）。

### Requirement: Docker 沙箱隔离执行

**Reason**：调研 Aider/Cline/Continue/Cursor/OpenHands 5 个成熟 AI 编程项目，4 个不用 Docker。硬件 RAG Agent 是贴身副驾场景，不需要 Docker 隔离。`backend/src/sandbox/` 目录已删除。
**Migration**：本地工具用 `path_guard` + `risk_classifier` 权限门控替代 Docker 隔离。

---

## 架构图

```
┌─────────────────────────────────────────────────────┐
│ 前端 (React)                                          │
│  ├─ PolicyBar       策略切换条（聊天输入框附近）         │
│  ├─ ConfirmDialog   确认弹窗（4 按钮）                  │
│  ├─ ActivityBlock   工具调用步骤卡片（独立组件）         │
│  ├─ AuditLogPanel   审计日志面板（设置页 audit tab）     │
│  └─ useSettingsStore  permissionMode 状态管理           │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP/SSE
┌──────────────────────┴──────────────────────────────┐
│ 后端 (FastAPI)                                       │
│  ├─ agent_sandbox_routes.py  策略查询/切换 + 审计查询   │
│  ├─ chat_routes.py           L121 后条件分支 use_agent │
│  ├─ agent_factory.py         create_react_agent 封装   │
│  ├─ sse_adapter.py           langgraph stream→SSE     │
│  ├─ loop_detector.py         重复检测 + 无进展检测      │
│  ├─ permission_gate.py       4 步权限门控核心           │
│  │    ├─ path_guard.py       路径校验（允许目录+deny）   │
│  │    └─ risk_classifier.py  关键字黑名单 V1            │
│  ├─ audit_logger.py          SQLite 审计日志写入        │
│  ├─ tool_router.py           非 Agent 路径（保留）      │
│  └─ tools/                   9 个 BaseTool 实现         │
│       ├─ wrappers.py         search_docs/audit_pins/   │
│       │                       wiring                   │
│       ├─ web_search.py       Tavily（key 前端注入）     │
│       ├─ generate_code.py     内部调 LLM                │
│       ├─ file_ops.py          read_file/write_file/     │
│       │                       edit_file                │
│       └─ run_command.py      本地 shell 执行            │
└──────────────────────┬──────────────────────────────┘
                       │ subprocess
┌──────────────────────┴──────────────────────────────┐
│ 用户本机 (Windows)                                    │
│  ├─ 项目根目录 e:\Desktop\agent（可读写）                │
│  ├─ 白名单目录（用户配置）                              │
│  ├─ %TEMP%\agent-sandbox\（临时目录）                  │
│  │  强制 deny: .git/, .vscode/, settings.json,        │
│  │             .env, *.key, *.pem, *credentials*       │
│  └─ SQLite（审计日志，30 天保留）                       │
└─────────────────────────────────────────────────────┘
```

---

## 参考文档

- 设计原文：[docs/superpowers/specs/2026-06-30-agent-react-design.md](file:///e:/Desktop/agent/docs/superpowers/specs/2026-06-30-agent-react-design.md)
- langgraph 官方文档：https://langchain-ai.github.io/langgraph/reference/prebuilt/
- OpenAI Codex Agent Loop：https://openai.com/engineering/unrolling-the-codex-agent-loop
