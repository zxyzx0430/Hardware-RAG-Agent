# Agent ReAct 系统设计

> Date: 2026-06-30
> Status: Design (Revised after GitHub review)
> Owner: Trae
> 目标: 为字节创造力大赛 demo（7.15 提交）接入完整 LangGraph ReAct Agent。
>
> **审查修订**：参考 langchain-ai/langgraph 官方 `create_react_agent` prebuilt 实现，
> 删除原 spec 手动建 4 个节点的过度工程，改用官方 prebuilt + 自定义扩展。

---

## 1. 背景与目标

### 1.1 现状
- [chat_routes.py L176-180](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L176) 仍是 `# TODO: ReAct loop`
- [tool_router.py](file:///e:/Desktop/agent/backend/src/agent/tool_router.py) 已有 5 个工具注册（audit/wiring/search_docs 真实，build/upload v2 mock，code_executor 已有但未接入 chat 流）
- 现状是单轮 RAG 问答

### 1.2 目标
接入 LangGraph ReAct loop，让 LLM 能自主决策调用工具，多轮推理直到完成任务。覆盖 demo 三段式能力："检索 → 分析 → 代码"。

### 1.3 非目标
- LangGraph 多 Agent 协作（不做 multi-agent）
- 长期跨会话记忆（本次仅单次会话记忆 + 现有 Message 表）
- 接入 build/upload v2 真实编译/烧录（推迟 v2）

---

## 2. 架构（基于官方 create_react_agent）

### 2.1 核心：用 prebuilt 不手写节点

**修订理由**：原 spec 手动建 agent_node / tool_node / observe_node / summarize_node 4 个节点，但 langgraph.prebuilt.create_react_agent 已封装这些。手写节点属于过度工程，违背 YAGNI。

```python
from langchain_openai import ChatOpenAI          # 必须用 langchain BaseChatModel
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

# 1. 用 langchain ChatOpenAI 包装，不能直接用项目 LLMClient（它不是 BaseChatModel）
llm = ChatOpenAI(
    model=model, api_key=api_key, base_url=base_url,
    temperature=payload.temperature, max_tokens=payload.max_tokens,
)
# 2. create_react_agent 内部会 bind_tools，不要传已 bind 的 LLM
agent = create_react_agent(
    model=llm,                    # 原始 LLM（未 bind_tools）
    tools=tool_list,              # 8 个 BaseTool 实例
    prompt=SYSTEM_PROMPT,         # 系统提示词
    checkpointer=MemorySaver(),   # 会话记忆（HITL 必需）
    interrupt_before=["tools"],   # write_file HITL 中断点（配合 add_human_in_the_loop）
)
# 3. recursion_limit 在调用时传，不是构造参数
config = {"recursion_limit": 41, "configurable": {"thread_id": session_id}}
```

官方内部图（来自 langchain-ai/langgraph 文档）：
```
[*] --> Start
Start --> Agent       # 调 LLM
Agent --> Tools : continue   # AIMessage 含 tool_calls
Tools --> Agent       # ToolMessage 回到 LLM
Agent --> End : end    # AIMessage 无 tool_calls
End --> [*]
```

**为什么仍需要自定义**：
1. SSE 流式输出（官方 stream_mode 不直接 yield 我们的 SSE 事件格式）
2. 重复参数检测（官方 recursion_limit 只防轮数，不防同参数死循环）
3. write_file 用户确认（用官方 `add_human_in_the_loop` + `interrupt_before=["tools"]`）
4. 工具结果截断（在工具实现里做，不改 prebuilt）

### 2.2 接入点（修订：替换整个 LLM 流式输出块）

**修订理由**：原 spec 说替换 L176-180 的 `# TODO: ReAct loop`，但实际该区域是 LLM 流式输出块（含 queue + worker_task + idle_timeout + usage 统计），Agent 自带 loop 会与之冲突。

**正确做法**：
- 在 [chat_routes.py](file:///e:/Desktop/agent/backend/app/api/chat_routes.py) L121 `yield sse_event("thinking", {"content": "正在生成回答...", "source": "llm"})` 之后，**条件分支**：
  - `if use_agent:` → 调 `stream_agent_to_sse(agent, events, config)` 替换原 L176-213 整个 LLM 流式块
  - `else:` → 保留原逻辑作为 fallback
- `use_agent` 判断：模型支持 function calling + 用户设置开启 Agent 模式
- 现有 RAG 检索逻辑（chat_helpers._run_rag_retrieval）保留，Agent 的 `search_docs` 工具直接复用其内部 `kb_manager.search_all_enabled()`

---

## 3. 工具集（9 个，分 4 类）

参考 Claude-Code 简化版设计，本地工具所有路径必须是**绝对路径**（防止相对路径绕过目录限制）。

**修订**：原 spec 含 `sandbox_run`（Docker 隔离执行），但调研 Aider/Cline/Continue/Cursor/OpenHands 5 个成熟 AI 编程项目，4 个不用 Docker。硬件 RAG Agent 是贴身副驾场景，不需要 Docker 隔离。已删除 Docker 沙箱代码，工具集从 10 → 9 个。

| # | 工具 | 类型 | 现状 | 截断策略 | 权限 |
|---|---|---|---|---|---|
| 1 | `search_docs` | 检索 | ✅ 已有 | 用户配置 top_k / 每 chunk 800 chars | auto |
| 2 | `web_search` | 检索 | 🆕 新增 | top 5, 每条 300 chars | auto |
| 3 | `audit_pins` | 硬件 | ✅ 已有 | 完整返回 | auto |
| 4 | `wiring` | 硬件 | ✅ 已有 | 完整返回 | auto |
| 5 | `generate_code` | 代码 | 🆕 新增 | 完整返回 | auto |
| 6 | `read_file` | 本地文件 | 🆕 新增 | 默认 200 行 + offset/limit | 见 §7 |
| 7 | `write_file` | 本地文件 | 🆕 新增 | 不截断 | 见 §7 |
| 8 | `edit_file` | 本地文件 | 🆕 新增 | 不截断 | 见 §7 |
| 9 | `run_command` | 本地 shell | 🆕 新增 | stdout/stderr 各 5000 chars | 见 §7 |

### 3.1 工具实现方式

按 langchain 规范用 `BaseTool` 子类，async agent 必须同时实现 `_run` + `_arun`。

**tool_router.py 定位**：ReAct Agent 不走它，保留给非 Agent 路径（CLI 直接调工具）。标注为"非 Agent 路径"。

### 3.2 4 个本地工具参数 Schema

#### run_command（本地 shell 执行）
```python
class RunCommandArgs(BaseModel):
    command: str = Field(description="shell 命令")
    timeout_ms: int = Field(default=30000, description="超时，默认 30s，上限 300000 (5min)")
    cwd: str | None = Field(default=None, description="工作目录，必须在允许范围内")
# 返回: { stdout, stderr, exit_code, duration_ms, timed_out }
```

#### write_file（写文件，结构化参数不走 shell）
```python
class WriteFileArgs(BaseModel):
    path: str = Field(description="必填，绝对路径")
    content: str = Field(description="必填，写入内容（原样，不转义）")
# 返回: { bytes_written, path }
```

#### read_file（读文件，分页避免刷爆 token）
```python
class ReadFileArgs(BaseModel):
    path: str = Field(description="必填，绝对路径")
    offset: int = Field(default=0, description="起始行")
    limit: int = Field(default=2000, description="最多读 2000 行")
# 返回: { content, total_lines, truncated }
```

#### edit_file（精确替换，比 sed 安全）
```python
class EditFileArgs(BaseModel):
    path: str = Field(description="必填，绝对路径")
    old_string: str = Field(description="要替换的文本（必须在文件中唯一）")
    new_string: str = Field(description="替换为的文本")
    replace_all: bool = Field(default=False, description="True 时允许多处替换")
# 返回: { replacements_made, path }
```

### 3.3 其他新增工具规格

#### web_search
- 实现：调 Tavily API（免费 1000 次/月）
- 失败降级：返回 `{"output": "网页搜索失败，请基于本地知识库回答"}`，不中断 Agent

#### generate_code
- 实现：内部调用 LLM（复用 LLMClient，用同一模型；超时 30s 时降级为 deepseek-v4-flash 等小模型）+ 硬件代码模板 prompt
- 输出：完整代码字符串 + 语言标识

---

## 4. SSE 事件 Schema（基于官方 stream_mode 多模式）

### 4.1 stream_mode 选择（修订：用多模式合并）

**修订理由**：原 spec 只用 `stream_mode="messages"`，但该模式只 yield AIMessage chunks（流式 LLM 输出），拿不到 ToolMessage（工具返回结果）。必须同时用 `stream_mode=["messages", "updates"]`：

- `stream_mode="messages"` → AIMessage chunks（流式 text）
- `stream_mode="updates"` → 节点输出（含 ToolMessage，工具结果）

```python
async for mode, chunk in agent.astream(
    events, config=config, stream_mode=["messages", "updates"]
):
    if mode == "messages":
        # chunk 是 AIMessage chunk，流式 text
        ...
    elif mode == "updates":
        # chunk 是 {"tools": {"messages": [ToolMessage(...)]}}
        ...
```

### 4.2 新增 2 个事件类型

```typescript
// 工具调用前（从 AIMessage.tool_calls 提取）
{
  "type": "tool_call",
  "tool": "search_docs",
  "args": {"query": "GPIO 配置", "top_k": 5},
  "call_id": "call_1",
  "step_index": 1
}

// 工具返回后（从 ToolMessage 提取）
{
  "type": "tool_result",
  "call_id": "call_1",
  "tool": "search_docs",
  "result": {"output": "找到 5 条相关片段..."},
  "duration_ms": 234,
  "success": true,
  "step_index": 1
}
```

失败时 `success: false`，`result.error` 包含错误码和消息。

### 4.3 事件转换器
新建 `backend/src/agent/sse_adapter.py`：
```python
async def stream_agent_to_sse(
    agent, events, config: dict, call_counter: Counter
) -> AsyncIterator[str]:
    """把 langgraph stream 输出转成项目 SSE 事件协议。"""
    last_call_id: str | None = None
    call_start_time: dict[str, float] = {}

    async for mode, chunk in agent.astream(
        events, config=config, stream_mode=["messages", "updates"]
    ):
        sse = _convert_chunk_to_sse(mode, chunk, call_counter, call_start_time)
        if sse:
            yield sse
```

### 4.4 AIMessage chunk → SSE text 事件
- AIMessage chunk 的 `content` 字段 → 累积到 SSE `text` 事件
- AIMessage chunk 的 `tool_calls` 字段（非空时）→ 提取工具调用，yield `tool_call` 事件

---

## 5. JSON 约束（简化：单层 function calling）

### 5.1 修订理由
原 spec 设计两层降级（function calling + prompt+JSON），但实际：
- langchain `bind_tools()` 在模型不支持时会直接报错
- 用 `with_structured_output()` 走 Pydantic schema
- 主流模型（GPT-4o / Claude / qwen / deepseek-v4）都支持 function calling
- prompt+JSON 第二层降级幻觉率高、维护成本高、收益小

### 5.2 单层方案
- 用 `llm.bind_tools(tool_list)` 绑定工具
- 模型不支持 → 直接报错 → 用户在设置里换支持的模型
- 在 README / 设置页明确标注"Agent 需要 function calling 支持的模型"

### 5.3 工具参数校验
所有工具用 Pydantic `args_schema` 定义，langchain 自动校验。原有 `tool_router.dispatch()` 保留给 MCP 工具。

---

## 6. 工具结果截断策略（修订：检索策略由用户前端配置，不暴露给 LLM）

**修订理由**：原 spec 让 LLM 自主决定 `top_k` / `kb_ids` / `threshold`，但深入项目发现这些参数是用户在前端配置的全局检索策略：

- 前端配置源：[useSettingsStore.ts L20-22](file:///e:/Desktop/agent/frontend/src/stores/useSettingsStore.ts#L20) 的 `topK` / `relevanceThreshold`
- 前端传参：[useChatStore.ts L409-417](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.ts#L409) 把 `topK` / `relevanceThreshold` / `selectedKbIds` 一起传给后端
- 后端接收：[chat_routes.py L56-64](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L56) `ChatRequest` 字段
- 现有调用：[chat_helpers.py L321-322](file:///e:/Desktop/agent/backend/app/api/chat_helpers.py#L321) `kb_manager.search_all_enabled(..., k=payload.top_k, kb_ids=payload.kb_ids, score_threshold=payload.relevance_threshold)`

**核心原则**：LLM 只决定"查什么"（query），不决定"怎么查"（top_k/kb_ids/threshold）。后者由用户配置，工具实例化时注入。

### 6.1 search_docs 工具设计（参数注入，非 LLM 决定）

```python
class SearchDocsArgs(BaseModel):
    query: str = Field(description="检索查询文本")

class SearchDocsTool(BaseTool):
    name: str = "search_docs"
    description: str = "检索本地知识库（芯片手册 PDF）。只需提供查询语句，检索策略（top_k/知识库范围/相关度阈值）由用户前端配置。"
    args_schema: type = SearchDocsArgs  # 只含 query

    # 用户配置通过工具实例化注入（每请求新建实例）
    _top_k: int = 5
    _kb_ids: list[str] | None = None
    _threshold: float = 0.0

    def __init__(self, top_k: int, kb_ids: list[str] | None, threshold: float):
        super().__init__()
        self._top_k = top_k
        self._kb_ids = kb_ids
        self._threshold = threshold

    async def _arun(self, query: str) -> dict:
        results = await search_docs_core(
            query=query,
            top_k=self._top_k,        # 用户配置，LLM 不可改
            kb_ids=self._kb_ids,       # 用户配置，LLM 不可改
            threshold=self._threshold, # 用户配置，LLM 不可改
        )
        return _truncate_search_results(results)
```

**工具实例化**（在 `agent_factory.py` 里每个请求新建）：
```python
def build_tools(payload: ChatRequest) -> list[BaseTool]:
    return [
        SearchDocsTool(
            top_k=payload.top_k or 5,
            kb_ids=payload.kb_ids,
            threshold=payload.relevance_threshold or 0.0,
        ),
        # ... 其他工具
    ]
```

### 6.2 截断策略表

| 工具 | 截断阈值 | 截断后行为 |
|---|---|---|
| `search_docs` | 用户配置 `top_k`（默认 5）/ 每 chunk 800 chars | 追加"...共 N 条，当前显示前 M 条。如需更多结果，请在设置页增大 top_k" |
| `web_search` | `max_results`（LLM 决定，默认 5）/ 每条 300 chars | 追加"...如需更多，请增大 max_results" |
| `audit_pins` | 不截断 | 完整返回 |
| `wiring` | 不截断 | SVG 不截断，BOM 完整 |
| `generate_code` | 不截断 | 代码必须完整 |
| `sandbox_run` | stdout/stderr 各 5000 chars（参数化 `max_output_chars`，默认 5000） | 追加"...输出已截断，用 read_file 读取结果文件" |
| `read_file` | `offset` + `limit`（LLM 决定，默认 limit=200） | 追加"...共 N 行，使用 offset=K 查看更多" |
| `write_file` | 不截断 | 完整返回 |

**注意**：只有 search_docs 的检索策略参数不暴露给 LLM。其他工具的参数（如 read_file 的 offset/limit、sandbox_run 的 max_output_chars）仍由 LLM 决定，因为这些是"如何使用工具"而非"检索策略"。

### 6.3 累积上下文保护
- 单次请求累积 token > 模型 context_window × 0.8 → 强制 summarize
- 用 `model_registry.get_max_tokens(model)` 获取 context_window
- 实现：在每个 ToolMessage 后估算 token，超限抛 `ContextLimitError` 捕获后走 fallback

---

## 7. 权限门控（4 步流程，参考 Claude-Code 简化版）

**项目已有基础**：[crud.py L291](file:///e:/Desktop/agent/backend/app/api/crud.py#L291) 的 `ALLOWED_SETTINGS_KEYS` 已包含 `permissionMode`，前端设置页已预留权限模式字段。

### 7.1 3 种权限模式

| 模式 | 含义 | 适用场景 |
|---|---|---|
| `bypassPermissions` | 完全放开，所有工具直接执行 | 演示/可信环境 |
| `default` | 全部询问，每次工具调用都弹确认 | 谨慎模式（默认） |
| `acceptEdits` | 文件编辑自动通过，危险命令仍询问 | 日常开发 |

### 7.2 权限门控 4 步流程

每次工具调用按以下顺序判断（短路）：

```
工具调用进入
     ↓
① path_guard 路径校验
   ├─ 写操作 → 检查目标路径在允许目录内？不在 → DENY
   ├─ 读操作 → 检查目标路径不在强制 deny 列表？在 → DENY
   └─ 强制 deny: .git/, .vscode/, .idea/, .claude/, settings.json, .env, *.key
     ↓
② 按当前模式分流
   ├─ bypassPermissions → ALLOW，直接执行
   ├─ default → ASK，弹确认弹窗
   └─ acceptEdits → 进入 ③ 风险分级
     ↓
③ risk_classifier 风险分级
   ├─ run_command → 关键字黑名单匹配
   │    ├─ HIGH (rm -rf, format, del /s, regedit, shutdown...) → ASK
   │    ├─ MEDIUM (pip install, npm install, git push...) → ASK
   │    └─ LOW (ls, cat, dir, type, echo, python script.py...) → ALLOW
   ├─ write_file → 默认 ALLOW（对齐 acceptEdits 语义）
   ├─ read_file → 默认 ALLOW
   └─ edit_file → 默认 ALLOW
     ↓
④ 决策落地
   ├─ ALLOW → 执行工具，写审计日志
   ├─ ASK   → 挂起等待用户确认（前端弹窗）
   └─ DENY  → 拒绝，返回错误，写审计日志
```

### 7.3 风险关键字黑名单（V1）

**HIGH（自动审查模式也要问）**：
- 删除：`rm -rf` / `del /s` / `rmdir /s` / `Remove-Item -Recurse`
- 格式化：`format` / `diskpart`
- 系统：`regedit` / `shutdown` / `reboot` / `taskkill /f`
- 覆盖系统：`> C:\Windows` / `> /etc/`

**MEDIUM（自动审查模式也要问）**：
- 包管理：`pip install` / `npm install` / `yarn add` / `pnpm add`
- Git 推送：`git push` / `git reset --hard` / `git clean -f`
- 网络下载：`curl` / `wget` / `Invoke-WebRequest`

**LOW（自动审查模式直接放行）**：
- 读写已在允许目录内的文件
- `ls` / `dir` / `cat` / `type` / `echo` / `pwd`
- 跑用户项目内的脚本（`python xxx.py` / `node xxx.js`）

### 7.4 用户确认弹窗 4 按钮

弹窗 4 个按钮（参考 Claude-Code decisionClassification）：

| 按钮 | 含义 | 后续行为 |
|---|---|---|
| 允许本次 | `user_temporary` | 仅本次放行，下次同类还要问 |
| 永久允许 | `user_permanent` | 写入白名单规则，以后同类自动放行 |
| 拒绝 | `user_reject` | 拒绝本次，AI 收到拒绝消息 |
| 拒绝并停止 | `user_reject + interrupt` | 拒绝并中断整个 Agent 循环 |

### 7.5 工作目录范围（path_guard）

**默认允许目录**：
- 项目根目录 `e:\Desktop\agent`（运行时动态获取，不硬编码）
- 系统临时目录（`%TEMP%\agent-sandbox\`）
- 用户可添加白名单目录（运行时切换，下次输入生效）

**强制 deny（无论是否在允许目录内）**：
- `.git/` / `.vscode/` / `.idea/` / `.claude/`
- `settings.json` / `.env` / `*.key` / `*.pem` / `*credentials*`
- 项目根目录上级路径（`..` 跳出）

### 7.6 HITL 中断机制（基于 interrupt_before）

`create_react_agent(..., interrupt_before=["tools"], checkpointer=MemorySaver())`

1. Agent 调用工具 → Tools 节点前自动中断，`astream` yield 完当前状态后退出
2. SSE 检测中断 → yield `tool_call` 事件 + `tool_confirm_required: true` 标记
3. 前端弹确认框（4 按钮选其一）
4. 用户操作：
   - 允许 → `agent.update_state(config, values={"messages": [ToolMessage("用户允许")]})` + `agent.astream(None, config=config, ...)` 继续
   - 拒绝 → 同上，ToolMessage 内容为"用户拒绝"，Agent 继续推理
   - 拒绝并停止 → `agent.astream(None, config=config, ...)` 继续，但下次 AgentFinish 后终止

### 7.7（已删除：sandbox_run 已移除，不再需要对比章节）

---

## 8. 防死循环策略（官方 recursion_limit + 自定义重复检测）

### 8.1 官方机制
- `recursion_limit = 2 * max_iterations + 1`
- 默认 25（约 12 轮）
- 到上限抛 `langgraph.errors.GraphRecursionError`
- 捕获后降级到普通 RAG

### 8.2 我的配置（修订：30→20 轮）
- `recursion_limit = 41`（20 轮硬上限，在 `astream(config={"recursion_limit": 41})` 传，不是构造参数）
- 修订理由：Codex 默认 12 轮，demo 阶段 20 轮已足够覆盖复杂场景，30 轮过长会让单次请求耗时失控
- 捕获 `GraphRecursionError` → 走 fallback RAG 回答

### 8.3 自定义重复检测（官方不做）

langgraph 只防轮数，不防同参数死循环。自定义：

| 检测维度 | 阈值 | 触发后行为 |
|---|---|---|
| 重复调用 | 同一工具 + 同一参数 hash 连续出现 2 次 | 注入提示："不要重复调用 X，尝试基于已有信息回答" |
| 无进展 | 连续 3 轮 tool_result 的 output hash 相同，或 LLM 连续 3 次调同类工具 | 同上 |

### 8.4 软上限提示
- 累积 10 轮 → yield SSE 提示"任务复杂，已调用 N 个工具，继续尝试"，不中断
- 修订理由：原 spec 15 轮太晚，用户会以为 Agent 卡住
- 通过 `call_counter` 跟踪，不依赖 langgraph state

### 8.5 时间上限
- 单次请求 > 120s → 抛 `AgentTimeoutError`，走 fallback
- 用 `start_time` + 每轮检查

### 8.6 实现
新建 `backend/src/agent/loop_detector.py`：
```python
def _args_hash(tool: str, args: dict) -> str:
    return hashlib.md5(f"{tool}|{json.dumps(args, sort_keys=True)}".encode()).hexdigest()

def detect_loop(call_history: list[dict]) -> Optional[str]:
    """返回 None / 'repeat' / 'no_progress'"""
    # 检查最近 5 次调用是否有重复
    # 检查最近 3 次 tool_result hash 是否相同
```

---

## 9. 记忆机制

单次会话记忆（不跨会话）。

### 9.1 LangGraph 内置
- `messages` 自动累积（含 ToolMessage 作为 observation）
- `checkpointer=MemorySaver()` 跨 invoke 调用保留状态（但本次不持久化，单次请求内有效）

### 9.2 跨会话
- Agent 工具调用历史不持久化（单次请求内有效）
- Agent 最终回答走现有 `persistLastTurn` 持久化到 Message 表
- 工具调用步骤作为 `activity` 字段持久化（复用现有机制）

### 9.3 Token 估算（修订：用 contextvars 防并发串状态）
- 每次 AIMessage / ToolMessage 后用 `LLMClient._estimate_tokens()` 估算
- 累积值用 `contextvars.ContextVar` 存（模块级变量会被并发请求串状态）
- 每个 SSE 请求独立 context，请求结束自动清理

### 9.4 审计日志（SQLite）

每次工具调用决策都写入审计日志，保留 30 天，超期自动清理。

**审计日志结构**：
```python
{
  "id": str,              # UUID
  "timestamp": int,       # Unix 毫秒
  "session_id": str,      # 会话 ID
  "tool_name": str,       # run_command / write_file / read_file / edit_file / search_docs / ...
  "args_summary": str,    # 参数摘要（前 200 字符，防泄漏）
  "decision": str,        # allow / ask / deny
  "decision_source": str, # mode_bypass / mode_default / classifier_low / classifier_high
                          # / user_temporary / user_permanent / user_reject / path_deny
  "risk_level": str,      # low / medium / high / n/a
  "exit_code": int,       # 执行结果（未执行为 null）
  "duration_ms": int,     # 执行耗时
  "error": str,           # 错误信息
}
```

**实现**：新建 `backend/src/agent/audit_logger.py`，写入现有 SQLite DB（[app/db/](file:///e:/Desktop/agent/backend/app/db/) 下新增 `tool_audit` 表）。

**前端审计日志面板**：可按 session_id 查询，展示工具调用历史 + 决策来源 + 风险等级。

---

## 10. 降级策略（修订：明确 fallback 路径并存）

**修订理由**：原 spec 一边说"Agent 替换 LLM 流式输出"，一边说"现有逻辑作 fallback"，需明确两者并存。

### 10.1 主路径 vs fallback 路径

```
chat_routes.py L121 后：
  ├─ if use_agent:  → Agent loop（主路径，D5-D11 实现）
  │     ├─ 成功 → done
  │     └─ 失败 → fallback
  └─ else:          → 现有 RAG + LLM 单轮（fallback 路径，保留不动）
```

`use_agent` 判断条件：
- 用户设置开启 Agent 模式
- 当前模型在白名单（支持 function calling 的模型列表）
- langgraph + langchain 依赖 import 成功

### 10.2 降级触发场景

| 场景 | 降级行为 |
|---|---|
| 模型不支持 function calling | `bind_tools()` 报错 → 返回错误提示用户换模型（不走 fallback，直接 error 事件） |
| `GraphRecursionError`（20 轮硬上限） | yield 提示"任务过于复杂，降级为单次回答" → 走 fallback RAG |
| 工具执行异常 | langgraph 自动把异常转 ToolMessage，Agent 继续推理（不降级） |
| 工具超时 | 同上（langgraph 自动处理） |
| 累积 token > context_window × 0.8 | 抛 `ContextLimitError` → 走 fallback RAG |
| 单次请求 > 120s | 抛 `AgentTimeoutError` → 走 fallback RAG |
| 用户问"你好"等非工具型问题 | LLM 第 1 轮就 AgentFinish，无 Agent 开销（自然处理） |
| LangGraph import 失败 | `use_agent = False`，自动走 fallback |

### 10.3 fallback 路径实现
保留 [chat_routes.py](file:///e:/Desktop/agent/backend/app/api/chat_routes.py) 现有 L121-213 的 RAG + LLM 单轮流式逻辑，作为 Agent 失败时的兜底。Agent 失败时：
1. yield SSE `error` 事件，告知用户"Agent 模式失败，切换到基础模式"
2. 调用 fallback 函数（提取现有逻辑为 `_fallback_rag_chat()`）
3. 继续流式输出

---

## 11. 文件改动范围

### 11.1 后端（新建 + 修改）

| 文件 | 操作 | 内容 |
|---|---|---|
| `backend/src/agent/agent_factory.py` | 🆕 新建 | `create_hardware_agent()` 函数，调 `create_react_agent` + 配置 |
| `backend/src/agent/sse_adapter.py` | 🆕 新建 | `stream_agent_to_sse()` 把 langgraph stream 转 SSE 事件 |
| `backend/src/agent/loop_detector.py` | 🆕 新建 | `detect_loop()` + `_args_hash()` |
| `backend/src/agent/prompts.py` | 🆕 新建 | Agent 系统 prompt + 工具描述 |
| `backend/src/agent/permission_gate.py` | 🆕 新建 | 4 步权限门控核心逻辑（path_guard→模式分流→风险分级→决策落地） |
| `backend/src/agent/risk_classifier.py` | 🆕 新建 | V1 关键字黑名单 / V2 LLM 风险分级 |
| `backend/src/agent/path_guard.py` | 🆕 新建 | 工作目录范围校验（允许目录 + 强制 deny） |
| `backend/src/agent/audit_logger.py` | 🆕 新建 | SQLite 审计日志写入（30 天保留） |
| `backend/src/agent/tools/` | 🆕 新建 | 工具实现目录 |
| `backend/src/agent/tools/wrappers.py` | 🆕 新建 | 现有 4 工具包装成 BaseTool（search_docs/audit_pins/wiring） |
| `backend/src/agent/tools/web_search.py` | 🆕 新建 | Tavily web_search 工具 |
| `backend/src/agent/tools/generate_code.py` | 🆕 新建 | 代码生成工具（内部调 LLM） |
| `backend/src/agent/tools/file_ops.py` | 🆕 新建 | read_file / write_file / edit_file（BaseTool 实现） |
| `backend/src/agent/tools/run_command.py` | 🆕 新建 | 本地 shell 执行工具（含超时控制） |
| `backend/src/agent/tool_router.py` | ✏️ 修改 | 删除 CodeExecutorTool 注册（L309-334），Docker 沙箱代码已移除 |
| `backend/app/api/agent_sandbox_routes.py` | 🆕 新建 | 策略查询/切换 API + 审计日志查询 API |
| `backend/app/api/chat_routes.py` | ✏️ 修改 | L121 后条件分支 `if use_agent`，保留现有逻辑作为 fallback |
| `backend/app/api/chat_helpers.py` | ✏️ 修改 | RAG 检索逻辑保留为 fallback，不改 |
| `backend/app/db/models.py` | ✏️ 修改 | 新增 ToolAudit 表模型 |
| `backend/.env.example` | ✏️ 修改 | 新增 `TAVILY_API_KEY=` |
| `backend/sandbox_workspace/` | 🆕 新建 | 沙箱工作区（加入 .gitignore，本地工具文件操作根目录） |
| `backend/.gitignore` | ✏️ 修改 | 新增 `sandbox_workspace/` |

### 11.2 前端（修改 + 新建）

| 文件 | 操作 | 内容 |
|---|---|---|
| `frontend/src/stores/useChatStore.ts` | ✏️ 修改 | SSE 解析新增 tool_call/tool_result 事件，存入 streamingSteps |
| `frontend/src/stores/useSettingsStore.ts` | ✏️ 修改 | 新增 permissionMode 状态（bypassPermissions/default/acceptEdits） |
| `frontend/src/components/chat/ActivityBlock.tsx` | ✏️ 修改 | 渲染工具调用步骤（图标+参数+耗时+成功/失败+风险等级） |
| `frontend/src/components/chat/ChatArea.tsx` | ✏️ 修改 | 流式时把 tool 步骤传给 ActivityBlock |
| `frontend/src/components/chat/PolicyBar.tsx` | 🆕 新建 | 策略切换条（聊天输入框附近，3 种模式切换） |
| `frontend/src/components/chat/ConfirmDialog.tsx` | 🆕 新建 | 确认弹窗（4 按钮：允许本次/永久允许/拒绝/拒绝并停止） |
| `frontend/src/components/settings/AuditLogPanel.tsx` | 🆕 新建 | 审计日志面板（按 session_id 查询工具调用历史） |
| `frontend/src/types/session.ts` | ✏️ 修改 | ActivityStep 类型扩展 tool/args/result/duration_ms/call_id/risk_level |

### 11.3 依赖

`backend/requirements.txt` 新增：
- `langchain>=0.3.0`（非 langchain-core，langchain 1.0 已发布）
- `langchain-openai>=0.2.0`（用 ChatOpenAI 包装，必需）
- `langgraph>=0.2.50`（create_react_agent + interrupt_before 稳定版本）
- `tavily-python>=0.5.0`

---

## 12. 验收标准

### 12.1 功能验收
- [ ] Agent 能自主决策调工具 vs 直接回答
- [ ] 10 个工具全部可调用，Pydantic 参数校验生效
- [ ] SSE tool_call/tool_result 事件前端正确渲染
- [ ] 防死循环：同参数重复 2 次触发换思路提示
- [ ] 防死循环：20 轮硬上限触发 GraphRecursionError → 降级 RAG
- [ ] 权限门控：3 种模式（bypassPermissions/default/acceptEdits）可切换
- [ ] 权限门控：HIGH 风险命令（rm -rf）在 acceptEdits 模式仍弹确认
- [ ] 权限门控：LOW 风险命令（ls）在 acceptEdits 模式直接放行
- [ ] 权限门控：强制 deny 路径（.git/）在任何模式下都拒绝
- [ ] 用户确认弹窗 4 按钮（允许本次/永久允许/拒绝/拒绝并停止）全部生效
- [ ] 审计日志：每次工具调用都写入 SQLite，前端面板可查询
- [ ] 模型不支持 function calling 时返回明确错误

### 12.2 demo 场景验收
- [ ] "ch340g 怎么接线" → Agent 调 search_docs → 调 wiring → 生成 SVG
- [ ] "ESP32-S3 GPIO0 和 GPIO1 同时做输出会冲突吗" → Agent 调 audit_pins → 返回冲突报告
- [ ] "写一个 ESP32-S3 读取 CH340g 串口数据的代码" → Agent 调 search_docs 查 CH340g → 调 generate_code → 返回完整代码
- [ ] "你好" → Agent 第 1 轮直接回答，无工具调用开销
- [ ] "在项目里写个 main.py 跑 hello world" → Agent 调 write_file（用户确认）→ run_command（acceptEdits 直接放行）→ 返回 hello world
- [ ] "帮我读一下 backend/main.py 的前 50 行" → Agent 调 read_file → 返回内容
- [ ] "把 backend/main.py 第 10 行的 port 改成 8080" → Agent 调 edit_file（用户确认）→ 返回修改结果
- [ ] "跑一下 python --version 看看" → Agent 调 run_command（acceptEdits 模式直接放行）→ 返回版本号

### 12.3 代码规范（AGENTS.md）
- 所有新函数 ≤ 10 行，超出拆分
- 所有新文件 ≤ 300 行
- 圈复杂度 ≤ 10
- max-params ≤ 3
- 无魔法数字（用命名常量）
- Python 函数有类型注解
- 外部调用有 try/except

---

## 13. 风险与备选

### 13.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| langgraph 依赖冲突 | 低 | 高 | 锁版本 + 虚拟环境隔离 |
| Tavily API 不可用 | 低 | 中 | 失败降级为本地知识库回答 |
| LLM function calling 兼容性 | 中 | 中 | 设置页明确标注支持的模型清单 |
| 沙箱工作区权限校验漏洞 | 低 | 高 | `os.path.realpath()` + startswith 校验 + 单元测试 |
| astream stream_mode 兼容性 | 低 | 中 | fallback 到 stream_mode="values" |

### 13.2 备选方案

如果 D5-D7 `create_react_agent` 集成卡住，降级为：
- 不用 langgraph，用关键词路由工具（问题含"接线"→调 wiring）
- 保留 SSE 事件 schema
- 省 3 天但失去 Agent 自主决策亮点

---

## 14. 时间预估（参考用，不承诺）

| 阶段 | 天数 | 任务 |
|---|---|---|
| D5 | 1 | `agent_factory.py` + `sse_adapter.py` + LangGraph 跑通最简 demo（1 个工具） |
| D6 | 1 | 接入 chat_routes.py + 现有 5 工具包装成 BaseTool |
| D7 | 1 | search_docs / web_search / audit_pins / wiring 工具联动测试 |
| D8 | 1 | generate_code / sandbox_run / read_file / write_file（含 HITL）工具联动 |
| D9 | 1 | 前端 ActivityBlock 渲染 + write_file 确认弹窗 |
| D10 | 1 | 防死循环 + 降级策略 + 单元测试 |
| D11 | 1 | 集成测试 + demo 场景调优 |

总计 7 天，预留 1 天缓冲。

---

## 15. 审查修订说明

### 15.1 第一轮修订（参考 langchain-ai/langgraph 官方文档）
1. ❌ 手动建 4 个 LangGraph 节点 → ✅ 用 `create_react_agent` prebuilt
2. ❌ 自己写防死循环全套 → ✅ 用官方 `recursion_limit` + 自定义重复检测
3. ❌ write_file 自己设计确认机制 → ✅ 用官方 `add_human_in_the_loop`
4. ❌ SSE 事件自己拼 → ✅ 用官方 `stream_mode="messages"` + adapter
5. ❌ 两层降级（function calling + prompt+JSON）→ ✅ 单层 function calling
6. ❌ 用项目自定义 `ToolHandler` 协议 → ✅ 用 langchain `BaseTool` 标准
7. ❌ 文件改动 11+ 个 → ✅ 减到 9 个新建 + 5 个修改

### 15.2 第二轮修订（langchain/langgraph 规范合规审查）

| # | 问题 | 修订 |
|---|---|---|
| 1 | `LLMClient` 不是 langchain `BaseChatModel`，不能直接传给 `create_react_agent` | 用 `ChatOpenAI` 包装（第 2.1 节） |
| 2 | `create_react_agent` 内部会 `bind_tools`，传已 bind 的 LLM 会重复绑定 | 传原始 LLM（第 2.1 节） |
| 3 | `recursion_limit` 不是 `create_react_agent` 构造参数 | 在 `astream(config={"recursion_limit": N})` 传（第 2.1 + 8.2 节） |
| 4 | `BaseTool._run` 是同步，async agent 必须实现 `_arun` | 两个方法都实现（第 3.1 节） |
| 5 | `stream_mode="messages"` 拿不到 ToolMessage | 用 `stream_mode=["messages", "updates"]` 多模式（第 4.1 节） |
| 6 | `add_human_in_the_loop` 需配合 `interrupt_before` + `Command(resume)` 才能真正中断 | 简化为直接用 `interrupt_before=["tools"]`（第 7.2 节） |
| 7 | L176-180 是 LLM 流式输出区，Agent 替换会冲突 | 改为条件分支 `if use_agent`（第 2.2 节） |
| 8 | fallback 路径与主路径矛盾 | 明确两者并存，提取 `_fallback_rag_chat()`（第 10 节） |
| 9 | `tool_router.py` 定位不清 | 保留给非 Agent 路径，标注"非 Agent 路径"（第 3.1 节） |
| 10 | 30 轮硬上限过长 | 降到 20 轮（recursion_limit=41，第 8.2 节） |
| 11 | 现有 queue+worker_task+idle_timeout 与 Agent loop 冲突 | Agent 主路径不走这套，保留在 fallback 路径（第 2.2 节） |
| 12 | requirements 版本写错 | `langchain>=0.3.0` + `langgraph>=0.2.50`（第 11.3 节） |
| 13 | 模块级变量存 token 估算，并发请求会串状态 | 用 `contextvars.ContextVar`（第 9.3 节） |
| 14 | ~~search_docs 截断写死 top_k=5~~ → 原方案把 top_k/kb_ids/threshold 暴露给 LLM 决定，但深入项目发现这些是用户在前端配置的检索策略（[useSettingsStore.ts L20-22](file:///e:/Desktop/agent/frontend/src/stores/useSettingsStore.ts#L20) + [useChatStore.ts L409-417](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.ts#L409) + [chat_routes.py L56-64](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L56)），不应让 LLM 覆盖 | search_docs 工具只暴露 `query` 给 LLM，top_k/kb_ids/threshold 通过工具实例化注入（第 6.1 节） |

### 15.3 参考来源
- langchain-ai/langgraph 官方文档（https://langchain-ai.github.io/langgraph/reference/prebuilt/）
- `create_react_agent` API：含 model / tools / prompt / checkpointer / interrupt_before
- `interrupt_before=["tools"]`：在 Tools 节点前中断
- `stream_mode=["messages", "updates"]`：拿 AIMessage + ToolMessage
- `GraphRecursionError`：超 recursion_limit 抛出，可捕获降级
- `langchain_core.tools.BaseTool`：_run + _arun 双实现

---

## 16. 参考项目对照表（Codex / OpenClaw / OpenCode）

调研 OpenAI Codex CLI（88k star）、OpenClaw（38 万 star）、OpenCode（sst/opencode 2.0）后的设计借鉴。

### 16.1 值得借鉴（7 项，已融入本 spec）

| # | 设计 | 来源 | 本 spec 落地 |
|---|---|---|---|
| 1 | **Agent Loop 本质是状态机**，Context 管理才是核心挑战 | Codex | 第 6 节工具结果截断 + 第 8 节防死循环 |
| 2 | **沙箱 + 审批二元策略**：技术边界 vs 授权策略分离 | Codex | 第 7 节沙箱权限门控（auto_approve / ask 两档） |
| 3 | **Plan 模式 vs Build 模式** 通过权限切换 | OpenCode | 第 7 节预留 `run_command` ask 权限（v2 扩展为 plan/build 双 agent） |
| 4 | **ReadTool 工程细节**：DEFAULT_READ_LIMIT + 行长度截断 + 二进制保护 | OpenCode | 第 3 节 read_file 工具规格（200 行 + offset/limit） |
| 5 | **BashTool AST 解析 + 路径预扫描** 防越权 | OpenCode | 第 7 节 `os.path.realpath()` + startswith 校验 |
| 6 | **EditTool FileTime 锁 + 写后格式化** | OpenCode | 第 11 节 file_ops.py 实现（v2 加入锁机制，本次先用 realpath 校验） |
| 7 | **多服务协作而非单一 Agent 类** | OpenCode | 第 11 节文件拆分（agent_factory / sse_adapter / loop_detector / tools 独立） |

### 16.2 借鉴但简化（3 项）

| # | 业界做法 | 本 spec 简化 | 理由 |
|---|---|---|---|
| 1 | Codex **Auto-review subagent** 二级 Agent 自动审批 | 直接 ask 用户 | 7.15 demo 前不加二级 Agent，复杂度太高 |
| 2 | OpenCode **SessionCompaction / SessionSummary** 上下文摘要 | 第 6 节硬截断 + token 上限降级 | 摘要算法调优周期长，demo 用硬截断够用 |
| 3 | OpenClaw **SKILL.md 声明式开发** | 第 5 节用 langchain BaseTool + Pydantic | 项目已有 tool_router 体系，不重写 |

### 16.3 不适用（3 项）

| # | 业界做法 | 不适用理由 |
|---|---|---|
| 1 | OpenClaw **多通道网关**（微信/QQ/Telegram） | 本项目是 Web 应用，无多通道需求 |
| 2 | Codex **Responses API + previous_response_id** | 项目用 OpenAI Chat Completions API 兼容多供应商 |
| 3 | OpenCode **LSP 诊断回写** | 嵌入式代码 LSP 支持差，不在 demo 范围 |

### 16.4 关键洞察（影响设计决策）

1. **Context 管理比工具调用循环更重要**（Codex 原话）——本次 demo 单次请求最多 30 轮，但每轮工具结果必须截断，否则单次请求就能撑爆 context
2. **增量式工作**是架构选择——demo 时引导用户问小问题（"ch340g 怎么接线"而非"帮我做完整个项目"），让 Agent 自然做增量
3. **沙箱边界要从第一天设计**——`backend/sandbox_workspace/` 必须在 D5 就建好，不是事后打补丁
4. **SKILL.md 比 JSON Schema 稳定**（OpenClaw 经验）——但本项目用 langchain BaseTool 已定，不重写；可考虑在工具 description 里写更详细的自然语言说明（学 OpenClaw 思路）

### 16.5 参考来源
- langchain-ai/langgraph 官方文档（https://langchain-ai.github.io/langgraph/reference/prebuilt/）
- OpenAI Engineering Blog - "Unrolling the Codex Agent Loop" (2026-06)
- OpenAI Engineering Blog - "Running Codex safely at OpenAI" (2026-06)
- OpenClaw GitHub 仓库（github.com/openclaw/openclaw）
- sst/opencode 2.0 源码（packages/opencode/src/）

---

## 17. 整体架构图

```
┌─────────────────────────────────────────────────────┐
│ 前端 (React)                                          │
│  ├─ PolicyBar       策略切换条（聊天输入框附近）         │
│  ├─ ConfirmDialog   确认弹窗（4 按钮）                  │
│  ├─ ActivityBlock   工具调用步骤卡片                    │
│  ├─ AuditLogPanel   审计日志面板（设置页）              │
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
│  │    └─ risk_classifier.py  关键字黑名单 V1/LLM V2     │
│  ├─ audit_logger.py          SQLite 审计日志写入        │
│  ├─ tool_router.py           非 Agent 路径（保留）      │
│  └─ tools/                   9 个 BaseTool 实现         │
│       ├─ wrappers.py         search_docs/audit_pins/   │
│       │                       wiring                   │
│       ├─ web_search.py       Tavily                    │
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

### 17.1 数据流（一次工具调用的完整链路）

```
用户发消息 → ChatRequest（含 permissionMode/topK/kb_ids/...）
  ↓
chat_routes.py L121 后 → use_agent? → 是 → agent_factory.build_tools(payload)
  ↓                                                  ↓ 否
  ↓                                  fallback RAG 路径（现有逻辑）
  ↓
create_react_agent.astream(stream_mode=["messages","updates"])
  ↓
LLM 输出 tool_calls → interrupt_before=["tools"] 中断
  ↓
permission_gate 4 步检查：
  ① path_guard 路径校验 → DENY 直接拒绝
  ② 模式分流 → bypassPermissions: ALLOW / default: ASK / acceptEdits: 进入 ③
  ③ risk_classifier 分级 → HIGH/MEDIUM: ASK / LOW: ALLOW
  ④ 决策落地 → ALLOW/ASK/DENY
  ↓
ASK → SSE yield tool_call + tool_confirm_required:true
  ↓
前端 ConfirmDialog 弹窗 → 4 按钮选其一
  ↓
agent.update_state → agent.astream(None) 继续
  ↓
ALLOW → 执行工具 → audit_logger 写日志
  ↓
sse_adapter yield tool_result 事件
  ↓
前端 ActivityBlock 渲染步骤卡片
  ↓
回到 LLM 推理 → 继续/结束
```

---

## 18. 审查修订说明（第三轮 - 权限门控 + 本地工具整合）

### 18.1 新增内容

| # | 新增章节 | 内容 |
|---|---|---|
| 1 | 第 3 节工具集扩充 | 8 → 10 个工具（加 edit_file + run_command） |
| 2 | 第 3.2 节 | 4 个本地工具参数 Schema（run_command/write_file/read_file/edit_file） |
| 3 | 第 7 节重写 | 权限门控 4 步流程（path_guard→模式分流→风险分级→决策落地） |
| 4 | 第 7.1 节 | 3 种权限模式（bypassPermissions/default/acceptEdits） |
| 5 | 第 7.3 节 | 风险关键字黑名单（HIGH/MEDIUM/LOW） |
| 6 | 第 7.4 节 | 用户确认弹窗 4 按钮（允许本次/永久允许/拒绝/拒绝并停止） |
| 7 | 第 7.5 节 | 工作目录范围（允许目录 + 强制 deny） |
| 8 | 第 7.7 节 | sandbox_run 与 run_command 的关系 |
| 9 | 第 9.4 节 | 审计日志结构（SQLite，30 天保留） |
| 10 | 第 11.1 节 | 新增 4 个后端模块（permission_gate/risk_classifier/path_guard/audit_logger） |
| 11 | 第 11.2 节 | 新增 3 个前端组件（PolicyBar/ConfirmDialog/AuditLogPanel） |
| 12 | 第 17 节 | 整体架构图 + 数据流图 |

### 18.2 与现有项目的整合点

| 现有项目 | 整合方式 |
|---|---|
| [crud.py L291](file:///e:/Desktop/agent/backend/app/api/crud.py#L291) 已有 `permissionMode` 设置键 | 前端 useSettingsStore 直接读写，后端 permission_gate 读取 |
| [executor.py](file:///e:/Desktop/agent/backend/src/sandbox/executor.py) Docker 隔离执行 | 保留为 sandbox_run 工具，不走权限门控 |
| [tool_router.py](file:///e:/Desktop/agent/backend/src/agent/tool_router.py) 现有工具注册 | 标注"非 Agent 路径"，ReAct Agent 直接用 langchain BaseTool |
| [useChatStore.ts](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.ts) SSE 解析 | 新增 tool_call/tool_result 事件类型，存入 streamingSteps |
| [ActivityBlock.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ActivityBlock.tsx) | 扩展渲染风险等级 + 决策来源 |
