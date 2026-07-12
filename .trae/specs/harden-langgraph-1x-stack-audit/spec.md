# harden-langgraph-1x-stack-audit Spec

## Why

langchain/langgraph 全家桶已升级到 1.x（langchain 1.3.4 / langchain-openai 1.2.2 / langgraph 1.2.4 / langchain-chroma 1.1.0），前次 `upgrade-langgraph-1x-stack` spec 完成了基础兼容性修复（InMemorySaver / max_completion_tokens / SqliteSaver / Chroma 迁移）。但全量代码审计发现：仍有 11 项必修复的 1.x 兼容性 bug / 死代码、8 项可用 1.x 新特性显著简化的代码、9 项 1.x 新能力可做的扩展、以及几十项违反项目代码规范（函数 ≤10 行 / 文件 ≤300 行 / 参数 ≤3）的质量问题。

本 spec 一次性全量修复所有发现，分 4 阶段实施，每阶段一个 commit 保证回滚性。

**核心约束（用户强约束）**：
1. **保持现有功能完整性** — 所有修改严禁丢失原有功能逻辑，包括但不限于：chunk 分块逻辑（multimodal/agent/hybrid 三种 chunker 的边界条件处理）、前端 SSE 来源引用机制（[srcN] 编号 + source 卡片 + score 展示）、代码高亮显示、Markdown 渲染、HITL 权限门控、审计日志、工具调用链、TodoCard 实时更新、串口/编译/烧录工作台等所有已实现功能
2. **全面回归测试** — 每阶段修改完成后，必须对变更内容执行全面回归测试，覆盖所有受影响的功能路径
3. **双 subagent 验证** — 每阶段验证时启动两个独立 subagent：
   - **完整性审查 subagent**：对照 spec/tasks/checklist 验证所有需求点是否均已正确实现
   - **功能测试 subagent**：系统性功能测试，确保所有原有功能在修改后仍能正常工作
4. **修复循环** — 若任一 subagent 发现问题，立即根据反馈针对性修复，重新执行审查与测试，直至所有问题解决

## What Changes

### 阶段 0：git 回滚点
- 清理临时垃圾文件（`.fix-*.py` / `backend/_*.json` / `data/benchmark/*.log` 等），加入 `.gitignore`
- 提交当前所有源代码改动作为"升级前回滚点"

### 阶段 1：必修复（11 项 1.x 兼容性 bug / 死代码）— **BREAKING for HITL**
- HITL 中断模式统一：评估后二选一（路线 A：`interrupt()` 主动中断 / 路线 B：保留 `interrupt_before` + 改 `Command(goto=...)`）
- `InMemorySaver` 导入路径迁移到 `from langgraph.checkpoint import InMemorySaver`
- `ReasoningChatOpenAI` 私有方法重写验证 + 适配 1.x chunk 处理
- `create_react_agent` 参数迁移（`interrupt_before` 标记 legacy，评估 `pre_model_hook`/`state_schema`）
- `vector_store.py` 三处 `_collection` 私有属性替换为构造时 `collection_metadata`
- `kb_manager.py:553` `__import__("sqlalchemy")` 反模式修复
- 前端 `HeartbeatSSEEvent` 类型补齐 + `useChatStore` 增加 `case "heartbeat"` 分支
- 前端 `tool_call`/`tool_result` 事件 `timestamp`/`end_timestamp` 字段补齐
- 前端 `risk_level`/`decision_source` 死 UI 清理（后端补发或前端移除）
- 前端 `tool` 事件死代码分支删除
- `MAX_RECURSION = 200` 注释/数值修正

### 阶段 2：1.x 新特性优化（8 项）— **BREAKING for SSE adapter**
- `astream_events(version="v2")` 替换 `astream(stream_mode=["messages","updates"])`
- `adispatch_custom_event` 替换 `streaming_event_bus` 队列合并（删除 `streaming_event_bus.py` + `_merge_agent_and_tool_events`）
- `InjectedToolArg` 替换 `PrivateAttr` 注入 `ToolContext`
- `pre_model_hook`/`post_model_hook` 替换 SSE 层 token 计数
- `PluginManager` 接入 `ToolRouter` 或删除
- RAG `as_retriever()` + `EnsembleRetriever` 替换自定义 RRF 融合
- Reranker 封装为 `ContextualCompressionRetriever`
- 前端 SSE 心跳驱动 UI（"Agent 已运行 X 秒"实时指示器）

### 阶段 3：扩展新功能（9 项）
- `interrupt()` 实现工具内细粒度 HITL（LOW 风险工具跳过 HITL）
- `Send` API 实现 search_docs 多查询并行
- LangGraph Subgraph 实现复合工具（build_firmware 子图）
- `Store` API 实现跨会话长期记忆（替换自建 FTS5）
- LangGraph Studio 调试支持（暴露 `langgraph.json`）
- `StreamReader` 实现流式工具结果（build_firmware 编译日志增量推送）
- `MultiQueryRetriever`（硬件同义词查询提升召回）
- `ParentDocumentRetriever`（小块检索 + 大块返回，替代 `big_chunk_text` metadata）
- `SelfQueryRetriever`（自然语言自动提取过滤条件）

### 阶段 4：代码质量重构（按文件拆分 + 函数拆分 + 参数封装）
- `useChatStore.ts` 1828 行 → 拆为 `useChatStream` / `useAgentEvents` / `useChatPersistence` / `useChatActions`
- `multimodal_chunker.py` 2126 行 → 拆为 4 个文件（主类 + vision + toc + merge）
- `agent_chunker.py` 1488 行 → 拆为 3 个文件（主类 + voting + fallback）
- `kb_manager.py` 1190 行 → 拆为 4 个文件（crud + search + bm25 + rrf）
- `agent_factory.py` 628 行 → 拆为 4 个文件（builder + checkpointer + tool_registry + tool_config）
- 函数超长拆分（`sendMessage` 613 行 / `chunk` 367 行 / `_merge_agent_and_tool_events` 59 行等几十项）
- 参数过多封装 dataclass（`log_tool_call` 9 参 / `MultimodalChunker.__init__` 16 参 / `AssistantMessageRow` 18 props）
- 魔法数字提取常量（`MAX_RECURSION` / `[:500]` / `[:100]` 等）

## Impact

- **Affected specs**:
  - `upgrade-langgraph-1x-stack`（前次基础兼容性修复，本 spec 是其后续全量整改）
  - `industrial-tool-runtime`（HITL 流程变化，interrupt() 细粒度门控）
  - `agent-react-fullstack`（Agent 构造方式变化，pre_model_hook 接入）
  - `implement-react-agent-fullstack`（SSE 事件流变化，astream_events v2）
  - `rag-to-agent-tool-trigger`（RAG 检索接口变化，EnsembleRetriever）

- **Affected code**:
  - 后端 Agent：`agent_factory.py` / `reasoning_chat.py` / `sse_adapter.py` / `hitl_handler.py` / `tool_spec.py` / `tool_router.py` / `context_guard.py` / `streaming_event_bus.py`（删除）/ `prompts.py` / `audit_recorder.py` / `plugins/`
  - 后端 RAG：`vector_store.py` / `kb_manager.py` / `reranker.py` / `search.py` / `chunking/multimodal_chunker.py` / `chunking/agent_chunker.py`
  - 后端配置：`settings.py`（可能新增 LangGraph Studio 配置）/ `langgraph.json`（新增）
  - 前端：`useChatStore.ts`（拆分）/ `types/api.ts`（SSE 事件类型补齐）/ `ActivityBlock.tsx`（死 UI 清理 + 心跳指示器）/ `ChatArea.tsx`（拆分）
  - 构建：`requirements.txt`（可能新增 langgraph-sdk） / `.gitignore`（清理临时文件）

## ADDED Requirements

### Requirement: 阶段 0 — git 回滚点
The system SHALL 在开始全量修复前，先清理临时垃圾文件并提交当前所有源代码改动作为"升级前回滚点"，保证后续每阶段都可独立回滚。

#### Scenario: 临时文件清理
- **WHEN** 阶段 0 开始
- **THEN** `.fix-*.py` / `backend/_*.json` / `backend/_*.png` / `data/benchmark/*.log` / `data/benchmark/*.checkpoint.json` / `data/test_results/golden_eval_*` 等临时文件被加入 `.gitignore`
- **AND** 真正的源代码改动（`M` 状态文件）被 `git add` + `git commit` 提交
- **AND** commit message 为 `chore: langchain 1.x 升级前回滚点（全量审计前快照）`

#### Scenario: 回滚点验证
- **WHEN** 阶段 0 完成
- **THEN** `git status` 显示工作区干净（除 `.gitignore` 新增的临时文件）
- **AND** `git log --oneline -1` 显示回滚点 commit

### Requirement: 双 subagent 验证机制（贯穿所有阶段）
The system SHALL 在每阶段修改完成后，启动两个独立 subagent 进行验证，发现问题立即修复并重新验证，直至全部通过。

#### Scenario: 完整性审查 subagent
- **WHEN** 任一阶段修改完成
- **THEN** 启动完整性审查 subagent，对照 `spec.md` / `tasks.md` / `checklist.md` 逐项验证
- **AND** 检查所有 ADDED Requirements 的 Scenario 是否全部满足
- **AND** 检查所有 REMOVED Requirements 的 Migration 是否已执行
- **AND** 输出审查报告（通过项 / 失败项 / 待修复项）

#### Scenario: 功能测试 subagent
- **WHEN** 任一阶段修改完成
- **THEN** 启动功能测试 subagent，对原有功能执行系统性回归测试
- **AND** 必测功能清单（严禁丢失）：
  - chunk 分块逻辑（multimodal/agent/hybrid 三种 chunker 的边界条件：跨页表格合并、PAGE 标记保护、tiny chunk 合并、TOC 提取、Vision LLM 调用）
  - 前端 SSE 来源引用机制（[srcN] 编号递增、source 卡片 score 展示、多 KB 检索不冲突、重复 srcN 处理）
  - 代码高亮显示 + Markdown 渲染 + "推到预览"按钮
  - HITL 权限门控（4 步：permission_classifier → permission_gate → user_confirm → audit_log）+ deny/stop 主动 yield tool_result SSE（PLUR [ENG-2026-0702-001]）
  - 审计日志（30 天清理 + 8 个 decision_source enum + SQLite 持久化）
  - 工具调用链（26 个工具 + 工具卡片耗时显示 + TodoCard 实时更新）
  - Agent 流式输出（thinking card 保留 + text 流式 + 工具并行调用）
  - 硬件工作台（串口扫描/收发 + 编译日志实时推送 + 烧录 HITL + 接线图渲染 + 引脚审计）
  - RAG 检索（多 KB 并行 + LRU 缓存 + reranker + 180s 超时）
  - 会话持久化（SqliteSaver + 重启恢复 + thread_id 清理）
- **AND** 输出测试报告（通过项 / 失败项 / 回归问题）

#### Scenario: 修复循环
- **WHEN** 任一 subagent 发现问题
- **THEN** 立即根据反馈针对性修复
- **AND** 修复后重新启动两个 subagent 重新验证
- **AND** 循环直至两个 subagent 全部通过
- **AND** 阶段 commit 只在双 subagent 全部通过后执行

### Requirement: 阶段 1 — 必修复 1.x 兼容性 bug
The system SHALL 修复所有 1.x 升级后的兼容性 bug、死代码、行为不稳定问题，保证 Agent / RAG / 前端三大模块在 1.x 下行为正确。

#### Scenario: HITL 中断模式统一
- **WHEN** Agent 执行到需要 HITL 确认的工具
- **THEN** 中断模式符合 1.x 规范（评估后二选一：路线 A `interrupt()` 主动中断 + `Command(resume=...)` 恢复 / 路线 B 保留 `interrupt_before` + 改用 `Command(goto=...)` 恢复）
- **AND** 不再混用 0.x `interrupt_before` + 1.x `Command(resume=...)` 的不稳定组合

#### Scenario: Chroma 私有属性消除
- **WHEN** `vector_store.py` 设置 HNSW `ef_search` 或执行 `export_data`/`import_data`
- **THEN** 不再访问 `self.db._collection` 私有属性
- **AND** HNSW 参数通过构造时 `collection_metadata={"hnsw:search_ef": value}` 设置
- **AND** `export_data`/`import_data` 用 `self.db.get(include=[...])` 或 try/except AttributeError 降级

#### Scenario: 前端 SSE 事件类型完整
- **WHEN** 后端发出 `heartbeat` / `tool_call` / `tool_result` 事件
- **THEN** 前端 `ChatSSEEvent` union 类型包含所有事件类型
- **AND** `tool_call` 事件类型包含 `timestamp` 字段
- **AND** `tool_result` 事件类型包含 `end_timestamp` 字段
- **AND** `useChatStore` 的 `case "heartbeat":` 分支更新 `streamingStartTime` 或独立心跳状态

#### Scenario: 死代码清除
- **WHEN** 阶段 1 完成
- **THEN** `useChatStore.ts` 的 `case "tool":` 死代码分支已删除
- **AND** `ToolSSEEvent` interface 已删除或标记 `@deprecated`
- **AND** `ActivityBlock.tsx` 中永不渲染的 `risk-badge` / `decision-source` span 已处理（后端补发 risk_level 或前端移除）
- **AND** `MAX_RECURSION` 数值与注释一致

### Requirement: 阶段 2 — 1.x 新特性优化
The system SHALL 用 langchain/langgraph 1.x 新特性简化代码，删除自维护的并发合并逻辑和 hack 注入方式。

#### Scenario: astream_events v2 迁移
- **WHEN** Agent 流式执行
- **THEN** 后端用 `agent.astream_events(version="v2")` 替换 `agent.astream(stream_mode=["messages","updates"])`
- **AND** 事件分发基于 `on_chat_model_stream` / `on_tool_start` / `on_tool_end` / `on_custom_event` 等结构化事件名
- **AND** `_merge_agent_and_tool_events` 函数（59 行）被删除或大幅简化

#### Scenario: adispatch_custom_event 替换 streaming_event_bus
- **WHEN** 工具内部需要推送流式事件（如 build_firmware 编译日志）
- **THEN** 工具用 `adispatch_custom_event("compile_log", {"line": ...})` 触发事件
- **AND** 事件自动出现在 `astream_events(version="v2")` 的 `on_custom_event` 中
- **AND** `streaming_event_bus.py` 模块被删除
- **AND** `_drain_stream` 函数不再通过 `emit_tool_event` 推送

#### Scenario: InjectedToolArg 替换 PrivateAttr
- **WHEN** 工具需要访问 `ToolContext`
- **THEN** 工具签名用 `InjectedToolArg` 注解声明 `ctx: ToolContext`
- **AND** LangGraph 自动从 state 注入，无需 `_inject_ctx_and_register` 手动注入
- **AND** `ToolSpec._ctx: ToolContext | None = PrivateAttr(default=None)` 字段删除

#### Scenario: RAG EnsembleRetriever 替换自定义 RRF
- **WHEN** RAG 检索执行多 KB 融合
- **THEN** 用 `EnsembleRetriever` 组合 Chroma retriever + BM25 retriever
- **AND** 自定义 `rrf_fusion` 函数（135 行）被删除或大幅精简
- **AND** 自定义 `BM25Index` 的存储/加载逻辑委托给 `BM25Retriever`（保留 jieba 分词定制）

### Requirement: 阶段 3 — 1.x 新能力扩展
The system SHALL 用 1.x 新能力扩展 Agent 功能上限，包括细粒度 HITL、并行查询、复合工具、长期记忆、调试支持、流式工具结果、高级检索。

#### Scenario: 细粒度 HITL
- **WHEN** Agent 调用工具
- **THEN** 工具根据 `risk_level` 决定是否调用 `interrupt()` 暂停
- **AND** LOW 风险工具（search_docs / read_file / list_files 等只读工具）跳过 HITL 直接执行
- **AND** MEDIUM/HIGH 风险工具（write_file / build_firmware / flash_firmware 等）触发 HITL 确认

#### Scenario: LangGraph Studio 调试
- **WHEN** 开发者运行 `langgraph dev`
- **THEN** 项目根目录有 `langgraph.json` 配置文件
- **AND** LangGraph Studio 可视化每个会话的 state 演化、工具调用顺序、HITL 中断点

#### Scenario: MultiQueryRetriever 提升召回
- **WHEN** 用户查询包含硬件同义词（"推挽" / "push-pull" / "PP"）
- **THEN** `MultiQueryRetriever` 用 LLM 生成 3-5 个查询变体
- **AND** 分别检索后 RRF 融合，提升召回率

### Requirement: 阶段 4 — 代码质量重构
The system SHALL 拆分超长文件/函数，封装过多参数为 dataclass，提取魔法数字为命名常量，符合 AGENTS.md 代码规范。

#### Scenario: 超长文件拆分
- **WHEN** 阶段 4 完成
- **THEN** `useChatStore.ts` 拆为 ≤4 个文件（每个 ≤300 行）
- **AND** `multimodal_chunker.py` 拆为 ≤4 个文件
- **AND** `agent_chunker.py` 拆为 ≤3 个文件
- **AND** `kb_manager.py` 拆为 ≤4 个文件
- **AND** `agent_factory.py` 拆为 ≤4 个文件

#### Scenario: 函数超长拆分
- **WHEN** 阶段 4 完成
- **THEN** 所有新写/修改的函数 ≤10 行（复杂业务逻辑允许 ≤20 行但需注释说明）
- **AND** `sendMessage` 拆为 `_handleTextEvent` / `_handleThinkingEvent` / `_handleToolCallEvent` 等子函数
- **AND** `_merge_agent_and_tool_events`（59 行）在阶段 2 删除，无需拆分

#### Scenario: 参数封装 dataclass
- **WHEN** 阶段 4 完成
- **THEN** `log_tool_call`（9 参）封装为 `AuditRecord` dataclass
- **AND** `MultimodalChunker.__init__`（16 参）封装为 `MultimodalChunkerConfig` dataclass
- **AND** `AssistantMessageRow`（18 props）通过 context 或 compose 拆分

## MODIFIED Requirements

### Requirement: Agent 构造方式
Agent 构造从 0.x `interrupt_before=["tools"]` + `PrivateAttr` 注入 + SSE 层 token 计数，迁移到 1.x `pre_model_hook`/`post_model_hook` + `InjectedToolArg` + `astream_events v2`。

### Requirement: RAG 检索流程
RAG 检索从自定义 RRF 融合 + 手动 reranker 调用，迁移到 `EnsembleRetriever` + `ContextualCompressionRetriever` 组合，支持 `MultiQueryRetriever` / `ParentDocumentRetriever` / `SelfQueryRetriever` 扩展。

### Requirement: 前端 SSE 事件处理
前端 SSE 处理从 `astream(stream_mode=...)` 的 chunk 类型判断，迁移到 `astream_events v2` 的结构化事件分发，支持 heartbeat 驱动 UI 和工具调用链可视化。

## REMOVED Requirements

### Requirement: `streaming_event_bus.py` 模块
**Reason**: 1.x 的 `adispatch_custom_event` + `astream_events v2` 的 `on_custom_event` 原生支持工具内自定义事件，无需自维护队列合并。
**Migration**: 工具内 `emit_tool_event("compile_log", ...)` 改为 `adispatch_custom_event("compile_log", ...)`；`_merge_agent_and_tool_events` 函数删除；`streaming_event_bus.py` 文件删除。

### Requirement: `_merge_agent_and_tool_events` 函数
**Reason**: 59 行的并发流合并逻辑在 `astream_events v2` 下不再需要，事件流自动包含所有事件源。
**Migration**: `stream_agent_to_sse` 直接迭代 `astream_events(version="v2")`，按事件名分发。

### Requirement: `interrupt_before=["tools"]` HITL 模式（如选路线 A）
**Reason**: 1.x 推荐 `interrupt()` 函数在工具内部主动中断，支持细粒度 HITL（LOW 风险工具跳过）。
**Migration**: `ToolSpec._arun` 根据 `risk_level` 决定是否 `interrupt({...})`；`agent_factory.py` 移除 `interrupt_before=["tools"]`；`hitl_handler.py` 的 `Command(resume=...)` 配合 `interrupt()` 使用。

### Requirement: 前端 `tool` 事件分支 + `ToolSSEEvent` 类型
**Reason**: 1.x 后端只发 `tool_call` + `tool_result`，不再发 `tool` 事件，前端 `case "tool":` 分支永不触发。
**Migration**: 删除 `useChatStore.ts:977-1015` 的 `case "tool":` 分支；删除 `types/api.ts:57-64` 的 `ToolSSEEvent` interface。

### Requirement: `__import__("sqlalchemy").func.sum` 反模式
**Reason**: 内联 `__import__` 是反模式，应顶部 `from sqlalchemy import func`。
**Migration**: `kb_manager.py` 顶部加 `from sqlalchemy import func`；`:553` 改为 `func.sum(KnowledgeDoc.chunk_count)`。

### Requirement: Chroma `_collection` 私有属性访问
**Reason**: langchain-chroma 1.1.0 后 `_collection` 是私有属性，随时可能改名/移除。
**Migration**: HNSW 参数构造时 `collection_metadata={"hnsw:search_ef": value}` 设置；`export_data`/`import_data` 用 `self.db.get(include=[...])` 或 try/except AttributeError 降级。
