# RAG 改造为 Agent 工具触发 + 工具按领域分组 Spec

## Why

当前两个核心问题：

1. **知识库检索慢（用户实测 1 分半）**：根因是「双重 RAG」——`chat_sse` 在进入 Agent 前先跑一遍 pre-RAG（query rewrite 调 LLM 3-8s + `search_all_enabled` 串行遍历所有 KB 60-80s），Agent 启动后可能再调一次 `SearchDocsTool`。即使 Agent 不调工具，pre-RAG 也对每条消息（包括「你好」）都跑一遍。

2. **工具代码组织混乱**：12 个工具散落在 `tools/wrappers.py`（5 个）、`tools/file_ops.py`（3 个）、`tools/run_command.py`（1 个）、`tools/workbench_tools.py`（3 个），没有按领域分目录，未来扩展到 30+ 工具时不可维护。`build_tools()` 全量实例化是对的（参考 Claude Code 40+ 工具全量注入），但代码组织需要按领域分组。

**调研结论**（5 个项目对比）：

| 项目 | 工具注入方式 | 工具代码组织 | 工具+技能分层 |
|------|------------|------------|--------------|
| **Claude Code** | 40+ 工具全量注册 + 9400 tokens 工具 prompt + 4 种权限模式过滤 | `src/tools/` 按领域分子目录（file/execution/web/agent/git/lsp） | 低层(Bash/Read/Write) + 中层(Edit/Grep/Glob) + 高层(Task/WebFetch) |
| **Codex CLI** | 全量注入到 Responses API `tools` 字段（JSON Schema） | 工具定义在 API 请求里，含 shell/update_plan/web_search + MCP 工具 | 无 Skill 层 |
| **OpenCode** | 全量注入，Agent 自己决定 | `internal/llm/tools/` 按文件分（bash.go / diagnostics.go / mcp-tools.go） | 无 Skill 层 |
| **OpenClaw** | Agent 自动识别并调用匹配技能 | 按四大类分（文件系统/浏览器/系统/API）+ SKILL.md 声明式技能 | Tool（原子操作）+ Skill（流程编排，可调多个 Tool） |
| **Hermes** | `HermesAgent(tools=[...])` 全量注入 | `@tool` 装饰器，自动解析 docstring 生成 Schema | Tool（原子）+ Skill（流程编排） |

**5 个项目共识**：
1. **全部都是全量注入**：所有工具 schema 绑定到 LLM，Agent 自己决定调哪个
2. **都不做意图分类器**：后端不预先决定激活哪些工具
3. **工具代码组织方式各异**，但都按领域/类别分组（不是扁平堆放）：
   - Claude Code：`src/tools/` 按领域分子目录
   - OpenCode：`internal/llm/tools/` 按文件分
   - OpenClaw：按四大类分（文件系统/浏览器/系统/API）
   - Hermes：`@tool` 装饰器
4. **OpenClaw 和 Hermes 有 Tool + Skill 两层抽象**：Tool 是原子操作，Skill 是流程编排（可调多个 Tool）——本项目未来可扩展方向
5. **System prompt 描述工具使用策略**：Claude Code 9400 tokens 工具 prompt + Codex CLI 权限/沙箱说明

**目标**：
- 去掉 pre-RAG，改为 Agent 按需触发 `SearchDocsTool`（Agent 自己决定是否检索）
- 优化单次检索性能（并行 + 缓存），技术问题检索从 90s 降到 5-8s，闲聊 0s 检索
- 工具按领域分 6 组（代码组织层面，retrieval / hardware / workbench / code / file_ops / execution），为未来扩展到 30+ 工具预留目录结构
- System prompt 加详尽工具使用策略指导（参考 Claude Code 的工具 prompt 设计）

## What Changes

### RAG 改造
- **chat_sse** 去掉 `_run_rag_retrieval` 调用，所有请求直接进 Agent 主路径；Agent 不可用时 fallback 到纯 LLM 流式（不检索知识库）
- **SearchDocsTool** 内部去掉 `_rewrite_query_for_rag` 调用（Agent 本身是 LLM，通过 system prompt 指导自己输出检索词）
- **search_docs_core / search_all_enabled** 改为 `asyncio.gather` 并行检索每个 KB，加 LRU 缓存（query → results，5 分钟 TTL，256 条）
- **sse_adapter** 拦截 `SearchDocsTool` 调用，解析工具返回的 `results`，实时 yield `source` 事件到前端（格式与 pre-RAG 路径一致）
- **fallback 路径** 前端提示「当前模型不支持工具调用，无法检索知识库」

### 工具按领域分组（代码组织，全量注入不变）
- **新建 `tools/groups/` 目录**：按 6 个领域分子目录组织工具代码（retrieval / hardware / workbench / code / file_ops / execution），参考 Claude Code 的 `src/tools/` 按领域分子目录
- **`build_tools` 保持全量注入**：仍然实例化全部 12 个工具，Agent 自己决定调哪个（5 个项目共识）
- **System prompt 加工具使用策略**：参考 Claude Code 的 9400 tokens 工具 prompt，写详尽的使用策略（何时用 search_docs vs web_search、何时用 generate_code、检索策略、风险工具使用规则）
- **不搞内部 Skill 编排**：不实现 OpenClaw/Hermes 那种 Tool + Skill 两层抽象。Agent 通过 system prompt 策略自己编排多工具调用。Claude Code 生态的 skills/ 目录（可下载可复用的 Skill 包）是另一回事，不在本 spec 范围

## Impact

- Affected specs: `implement-react-agent-fullstack`（Agent 主路径）、`chat-credential-pipeline-fix`（chat_sse 结构）、`workbench-agent-bridge`（工作台工具）
- Affected code:
  - `backend/app/api/chat_routes.py` — 删除 pre-RAG 调用，Agent 路径变为唯一检索入口
  - `backend/app/api/chat_helpers.py` — `_run_rag_retrieval` / `_rewrite_query_for_rag` / `_build_source_event` / `_build_rag_context` 部分函数迁移或废弃
  - `backend/src/agent/tools/wrappers.py` — `SearchDocsTool._arun` 改造，返回值含完整 source 元数据
  - `backend/src/rag/search.py` — `search_all_enabled` 改并行 + 缓存
  - `backend/src/agent/sse_adapter.py` — 拦截 `search_docs` 工具调用，发 `source` 事件
  - `backend/src/agent/prompts.py` — `SYSTEM_PROMPT` 新增工具使用策略指导（参考 Claude Code 工具 prompt 设计）
  - **`backend/src/agent/tools/groups/`**（新目录）— 按领域分组的工具代码
  - `backend/src/agent/agent_factory.py` — `build_tools` 从新目录导入工具（全量注入不变）
  - `frontend/src/stores/useChatStore.ts` — fallback 路径提示

## ADDED Requirements

### Requirement: Agent 按需触发知识库检索

The system SHALL route all chat requests through the Agent path, where the Agent decides whether to call `search_docs` based on user intent (technical question → call; chitchat → skip). Agent 自己根据 system prompt 策略决定，后端不做意图分类。

#### Scenario: 技术问题触发检索
- **WHEN** 用户问「STM32F4 的 DMA 怎么配置」
- **THEN** Agent 根据 system prompt 策略判断这是技术问题，调用 `search_docs` 工具，传入精炼检索词（如「STM32F4 DMA 配置 传输」）
- **AND** sse_adapter 实时 yield `source` 事件到前端，展示引用卡片
- **AND** Agent 基于检索结果回答，正文标注 `[srcN]`

#### Scenario: 闲聊不触发检索
- **WHEN** 用户问「你好」或「你是谁」
- **THEN** Agent 根据 system prompt 策略判断这是闲聊，直接回复，不调用 `search_docs`
- **AND** 前端不展示引用卡片

#### Scenario: Agent 不可用 fallback
- **WHEN** langgraph 未安装 或 模型不支持 function calling
- **THEN** 走纯 LLM 流式回复，不检索知识库
- **AND** 前端显示提示「当前模型不支持工具调用，无法检索知识库」

### Requirement: 并行检索多个知识库

The system SHALL retrieve from multiple knowledge bases in parallel using `asyncio.gather`, instead of serial iteration.

#### Scenario: 多 KB 并行检索
- **WHEN** Agent 调 `search_docs`，启用 3 个知识库
- **THEN** 3 个 KB 的检索并行执行（总耗时 ≈ 最慢的 KB，而非三者之和）

### Requirement: 检索结果 LRU 缓存

The system SHALL cache `search_docs` results by query text (5 分钟 TTL, 256 条 LRU)，相同查询在 TTL 内直接返回缓存结果。

#### Scenario: 缓存命中
- **WHEN** Agent 5 分钟内用相同 query 再次调 `search_docs`
- **THEN** 直接返回缓存结果，不执行实际检索

#### Scenario: 缓存过期
- **WHEN** 缓存超过 5 分钟
- **THEN** 重新检索并更新缓存

### Requirement: sse_adapter 实时发送 source 事件

The system SHALL intercept `search_docs` tool calls in `sse_adapter`, parse the tool's return value, and yield `source` SSE events in real-time (format identical to the pre-RAG path).

#### Scenario: 工具调用时发 source 事件
- **WHEN** Agent 调用 `search_docs` 并返回结果
- **THEN** sse_adapter 解析返回值的 `results` 数组
- **AND** 对每个 result yield 一个 `source` 事件（含 id/title/score/excerpt/citation 等字段）
- **AND** 前端立即展示引用卡片

### Requirement: 工具按领域分组（代码组织）

The system SHALL organize all 12 tools into 6 domain groups under `tools/groups/` directory. Each group is a subdirectory containing related tool implementations. `build_tools` still instantiates ALL tools (全量注入)，Agent 自己决定调哪个。

**6 个领域分组（代码目录结构）：**

| 目录 | 领域 | 包含工具 | 工具层 | 参考来源 |
|------|------|---------|--------|---------|
| `tools/groups/retrieval/` | 检索 | search_docs, web_search | 高层（封装检索策略） | Claude Code: WebFetch/Grep |
| `tools/groups/hardware/` | 硬件诊断与接线 | audit_pins, wiring | 高层（领域专用） | Claude Code: mcp__ide__getDiagnostics |
| `tools/groups/workbench/` | 工作台渲染 | render_wiring, render_code, render_safety_report | 高层（前端推送） | — |
| `tools/groups/code/` | 代码生成 | generate_code | 高层（封装 LLM 调用） | Claude Code: Task |
| `tools/groups/file_ops/` | 文件操作 | read_file, write_file, edit_file | 低层（原子操作） | Claude Code: Read/Write/Edit |
| `tools/groups/execution/` | 命令执行 | run_command | 低层（原子操作） | Claude Code: Bash |

**未来扩展预留：**
- `retrieval/` 可扩展：search_datasheet, search_application_note, search_github
- `hardware/` 可扩展：audit_power, audit_clock, audit_signal_integrity, render_schematic, render_pcb
- `workbench/` 可扩展：render_pin_table, render_bom, render_timing_diagram
- `code/` 可扩展：generate_test, generate_config, generate_hal
- `file_ops/` 可扩展：list_files, search_files, diff_files
- `execution/` 可扩展：run_python, run_cmake, run_make
- 新领域（如 `tools/groups/debug/`）：debugger_attach, breakpoint_set, stack_inspect
- **内部 Skill 编排（未来扩展方向，不在本 spec 范围）**：参考 OpenClaw/Hermes 的 Tool + Skill 两层抽象，把多工具调用封装成预设流程（如「生成代码并烧录」= generate_code + run_command + audit_pins）

#### Scenario: 工具代码按领域分目录
- **WHEN** 开发者查看 `tools/groups/` 目录
- **THEN** 看到 6 个子目录（retrieval / hardware / workbench / code / file_ops / execution）
- **AND** 每个子目录内有对应领域的工具实现文件

#### Scenario: 新增工具到现有领域
- **WHEN** 开发者新增 `search_datasheet` 工具
- **THEN** 在 `tools/groups/retrieval/search_datasheet.py` 创建文件
- **AND** 在 `tools/groups/retrieval/__init__.py` 导出
- **AND** 在 `build_tools` 中追加实例化（全量注入）

#### Scenario: 新建领域分组
- **WHEN** 开发者需要「电源分析」领域
- **THEN** 新建 `tools/groups/power/` 目录
- **AND** 实现 `audit_power.py` 等工具
- **AND** 在 `build_tools` 中追加实例化

### Requirement: Agent system prompt 工具使用策略（参考 Claude Code）

The system SHALL include a detailed «工具使用策略» section in `SYSTEM_PROMPT` (参考 Claude Code 的 9400 tokens 工具 prompt 设计)，指导 Agent 何时用哪个工具、检索策略、风险工具使用规则。Agent 根据 prompt 自己决定，后端不预先分类。

#### Scenario: System prompt 含工具使用策略
- **WHEN** Agent 创建时
- **THEN** `SYSTEM_PROMPT` 包含以下章节：
  - 知识库检索策略（何时调 search_docs、传什么检索词）
  - 工具分层说明（低层原子工具 vs 高层封装工具）
  - 检索工具选择策略（search_docs vs web_search）
  - 风险工具使用规则（write_file / run_command 需谨慎）
  - 工具调用示例（good-example / bad-example）

#### Scenario: Agent 根据策略自己决定
- **WHEN** 用户问「STM32F4 的 DMA 怎么配置」
- **THEN** Agent 看到 system prompt 中「技术问题先调 search_docs」策略
- **AND** Agent 自己决定调用 `search_docs`，传入精炼检索词
- **AND** 后端不做任何意图分类

## MODIFIED Requirements

### Requirement: Agent system prompt 检索策略 + 工具使用策略

`SYSTEM_PROMPT` 新增以下指导（参考 Claude Code 的工具 prompt 设计，含 IMPORTANT/NEVER 强调 + 示例）：

```
## 知识库检索策略

IMPORTANT: 技术问题（芯片参数/接线/寄存器/外设配置）必须先调 search_docs 检索，再基于结果回答。
IMPORTANT: 闲聊/通用问题（问候/身份/非硬件）绝不调 search_docs，直接回复。
IMPORTANT: 调用 search_docs 时，传入精炼检索词：芯片型号 + 外设名 + 协议名。

<good-example>
search_docs(query="STM32F4 DMA 配置 传输")
</good-example>
<bad-example>
search_docs(query="这个芯片的DMA怎么用")
</bad-example>

检索结果未覆盖问题时，明确声明「知识库未找到相关文档」，再基于通用知识回答。

## 工具使用策略

系统提供以下工具（按领域分组）：

### 检索类（retrieval）
- search_docs: 检索本地芯片手册知识库（高频，技术问题首选）
- web_search: 网络搜索（知识库未覆盖时补充）

### 硬件类（hardware）
- audit_pins: 检查引脚冲突（引脚分配问题专用）
- wiring: 生成接线 SVG 图

### 工作台渲染类（workbench）
- render_wiring: 推送接线图到工作台
- render_code: 推送代码到工作台
- render_safety_report: 推送安全报告到工作台

### 代码生成类（code）
- generate_code: 生成驱动代码

### 文件操作类（file_ops）
- read_file: 读取本地文件（低层原子工具）
- write_file: 写入本地文件（高风险，需用户确认）
- edit_file: 编辑本地文件（高风险，需用户确认）

### 命令执行类（execution）
- run_command: 执行 shell 命令（高风险，需用户确认）

## 工具选择规则
1. 技术问题 → 先 search_docs，未覆盖再 web_search
2. 引脚冲突问题 → audit_pins
3. 需要接线图 → wiring + render_wiring
4. 需要生成代码 → generate_code
5. 需要读写文件 → read_file / write_file / edit_file（高风险，谨慎使用）
6. 需要执行命令 → run_command（高风险，谨慎使用）

IMPORTANT: 优先使用高层封装工具（search_docs / audit_pins / wiring / generate_code），
低层工具（read_file / write_file / run_command）仅在高层工具无法满足时使用。
```

### Requirement: SearchDocsTool 返回值扩展

`SearchDocsTool._arun` 返回值从 `{"output", "results", "truncated"}` 扩展为包含完整 source 元数据：

```python
{
    "output": "找到 5 条相关片段...",  # LLM 可读摘要
    "results": [
        {
            "doc": "...", "score": 0.85, "content": "...",  # 原有字段
            # 新增 source 事件所需元数据
            "id": "src1", "title": "...", "chunk_index": 27,
            "page_start": 1, "page_end": 1, "section_title": "...",
            "source_url": "", "category": "user_upload",
            "chunk_method": "hybrid", "kb_id": "...", "kb_name": "...",
            "small_chunk_id": "...", "citation": "..."
        }
    ],
    "truncated": False
}
```

## REMOVED Requirements

### Requirement: pre-RAG 检索流程

**Reason**: 双重 RAG 导致性能问题；Agent 触发后 pre-RAG 冗余。
**Migration**:
- `chat_sse` 删除 `_run_rag_retrieval` 调用
- `_run_rag_retrieval` / `_build_source_event` / `_build_rag_context` 函数迁移到 `wrappers.py` / `sse_adapter.py`（复用 source 事件构建逻辑）
- `_rewrite_query_for_rag` 及其 LRU 缓存废弃（Agent 自己改写 query）
- `_QUERY_REWRITE_SYSTEM` prompt 废弃

### Requirement: query rewrite LLM 调用

**Reason**: Agent 本身是 LLM，调用工具时已理解 query 语义，再调一次 LLM rewrite 是冗余（浪费 3-8s）。
**Migration**: Agent system prompt 指导其输出精炼检索词；`SearchDocsTool` 内部直接用 Agent 传入的 query 检索。
