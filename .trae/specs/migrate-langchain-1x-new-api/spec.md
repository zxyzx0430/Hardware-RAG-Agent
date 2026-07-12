# LangChain 1.x 新 API 全量迁移 Spec

## Why

langchain 1.x 升级审计（commit 62b1920）已完成，但 30 项降级中 13 项是"高风险/中风险"迁移延后。当前项目仍用 langgraph 0.x 时代的 `create_react_agent` + `interrupt_before` + `astream(stream_mode)` + `PrivateAttr`，这些 API 在 langgraph 2.x 可能被弃用。现在不迁移，未来技术债窗口关闭后迁移成本更高。本 spec 执行全量迁移到 langchain 1.x 推荐新 API，同时严格保留所有定制化实现（rrf_fusion 算法 / FTS5 词法检索 / 手动 rerank 跨 KB 批量优化 / HybridChunker 边界处理 / HITL 4 步门控 / 审计日志 / [srcN] 来源引用 / "推到预览"按钮）。

## What Changes

### 阶段 0：技术验证 + git 回滚点
- 验证 langchain 1.3.4 中所有新 API 可用性（create_agent / stream_events v3 / context_schema / InjectedToolArg / AgentMiddleware / Store API / StreamWriter）
- 验证发现：`interrupt_on` / `adispatch_custom_event` / `@agent_middleware` 装饰器在 langchain 1.3.4 不存在，但有可用替代方案（详见 pitfalls.md）
- git commit 回滚点

### 阶段 1：低风险迁移（RAG 包装层，不替换核心）
- **Task 2**: rrf_fusion 包装为 `RRFEnsembleRetriever(BaseRetriever)` — 保留 BM25 penalty / 软归一化 / 自定义 dedup 算法，仅实现 BaseRetriever 接口
- **Task 3**: 手动 rerank 包装为 `BgeRerankerCompressor(BaseDocumentCompressor)` — 保留跨 KB 批量 rerank 优化，仅实现标准接口
- **Task 4**: langgraph-cli 安装 + `langgraph dev` 跑通 + langgraph.json wrapper factory

### 阶段 2：中风险迁移（新增并存，不替换）
- **Task 5**: Store API 与 FTS5 并存 — SearchHistoryTool 先用 FTS5 词法检索，降级到 Store API 语义检索
- **Task 6**: SSE 层 token 计数迁移到 `AgentMiddleware` 子类 — 用 `AgentMiddleware.before_model`/`after_model` 钩子实现，保留 accumulate_tokens 作为 fallback
- **Task 7**: `StreamWriter` + `CustomStreamPart` 替换 `streaming_event_bus` 队列合并 — 工具函数签名注入 `writer: StreamWriter`，保留 _merge_agent_and_tool_events 作为 fallback

### 阶段 3：高风险迁移（核心 API 替换）
- **Task 8**: `InjectedToolArg` + `context_schema` 替换 `PrivateAttr` — 26 个工具签名修改，保留 ToolContext 所有字段
- **Task 9**: HITL 链路迁移到 `create_agent` + 保留 `interrupt_before=["tools"]` + `PermissionClassifier` — 因 `interrupt_on` 不存在，HITL 4 步门控逻辑零修改保留，仅 Agent 构造入口从 `create_react_agent` 改为 `create_agent`
- **Task 10**: `stream_events(version="v3")` 替换 `astream(stream_mode=["messages","updates"])` — SSE 层重写，保留 thinking/text/tool_call/tool_result/source 5 类事件
- **Task 11**: `create_agent` (from `langchain.agents`) 替换 `create_react_agent` (from `langgraph.prebuilt`) — agent_factory 重写，保留 checkpointer / system prompt / tools 全量注入 / interrupt_before

### 阶段 4：RAG 增强（可选功能）
- **Task 12**: `MultiQueryRetriever` — 保留 Agent 自主改写，新增 LLM 改写工具作为可选增强
- **Task 13**: `ParentDocumentRetriever` — 保留 big_chunk_text 前端 UI，新增自动注入 parent 选项
- **Task 14**: `SelfQueryRetriever` — 保留 doc_filter，新增自然语言 metadata filter

### 阶段 5：文档同步
- pitfalls.md / architecture-map.md / completed.md / checklist.md 更新

## Impact

### 受影响的代码（按风险分层）

**高风险（核心链路重写）：**
- `backend/src/agent/agent_factory.py` — create_react_agent → create_agent
- `backend/src/agent/sse_adapter.py` — astream(stream_mode) → stream_events v3
- `backend/src/agent/sse_helpers.py` — 事件结构适配
- `backend/src/agent/hitl_handler.py` — interrupt_before → interrupt_on
- `backend/src/agent/core/toolkit/permission_classifier.py` — 适配 interrupt_on
- `backend/src/agent/core/toolkit/tool_spec.py` — PrivateAttr → InjectedToolArg
- `backend/src/agent/core/toolkit/tool_router.py` — 调用点更新
- 26 个工具文件 — 签名修改

**中风险（架构增强）：**
- `backend/src/agent/streaming_event_bus.py` — adispatch_custom_event
- `backend/src/agent/context_guard.py` — middleware 机制
- `backend/src/agent/session_search.py` — Store API 并存
- `backend/src/agent/tools/groups/retrieval/search_history.py` — Store API 降级

**低风险（包装层）：**
- `backend/src/rag/rrf_retriever.py` (新增) — RRFEnsembleRetriever
- `backend/src/rag/reranker_compressor.py` (新增) — BgeRerankerCompressor
- `backend/src/rag/kb_manager.py` — 调用 RRFEnsembleRetriever
- `backend/src/rag/retriever.py` (如果存在) — 调用 BgeRerankerCompressor

**前端（仅适配）：**
- `frontend/src/stores/useChatStore.ts` — SSE 事件适配（如果 stream_events v3 事件结构不同）
- `frontend/src/types/api.ts` — SSE 事件类型适配
- `frontend/src/api/client.ts` — SSE 解析适配

### 受影响的 spec
- `harden-langgraph-1x-stack-audit`（前序 spec，已完成）
- `rag-to-agent-tool-trigger`（RAG 触发机制）
- `industrial-tool-runtime`（工具运行时）

## ADDED Requirements

### Requirement: LangChain 1.x 新 API 全量迁移
The system SHALL 迁移到 langchain 1.x 推荐的新 API，包括 `create_agent` / `stream_events v3` / `InjectedToolArg` / `context_schema` / `AgentMiddleware` / `Store API` / `StreamWriter`。`interrupt_on` / `adispatch_custom_event` / `@agent_middleware` 在 langchain 1.3.4 不存在，使用替代方案（详见 pitfalls.md 阶段 0 验证）。

#### Scenario: 技术验证通过
- **WHEN** Task 0 执行 `python -c "from langchain.agents import create_agent; ..."` 验证
- **THEN** 5 个核心 API 可用（create_agent / InjectedToolArg / InMemoryStore / AgentMiddleware / StreamWriter），3 个不存在的 API 已记录 pitfalls.md 并采用替代方案

#### Scenario: 定制化算法保留
- **WHEN** 迁移 rrf_fusion 到 RRFEnsembleRetriever
- **THEN** BM25 penalty / 软归一化 / 自定义 dedup 算法逻辑保留，检索结果与迁移前一致

#### Scenario: HITL 4 步门控保留
- **WHEN** 迁移到 create_agent + 保留 interrupt_before=["tools"]
- **THEN** permission_classifier → permission_gate → user_confirm → audit_log 4 步门控完整，8 个 decision_source enum 值正确，deny/stop 主动 yield tool_result SSE（PLUR [ENG-2026-0702-001]）

#### Scenario: SSE 来源引用机制保留
- **WHEN** 迁移到 stream_events v3
- **THEN** [srcN] 编号递增正确，source 卡片 score 展示正确，重复 srcN 处理正确，source 卡片默认全展开，思考卡输出完成后保留

#### Scenario: 双 subagent 验证通过
- **WHEN** 每个阶段完成后启动双 subagent
- **THEN** 完整性审查 subagent + 功能测试 subagent 全部通过，无阻塞问题

## MODIFIED Requirements

### Requirement: Agent 构造
[从 create_react_agent 迁移到 create_agent，保留 checkpointer / system prompt / tools 全量注入 / interrupt_before / state_schema / context_schema]

### Requirement: HITL 权限门控
[保留 interrupt_before=["tools"] + PermissionClassifier pre-ToolNode 判定模式（interrupt_on 不存在，HITL 链路零修改），保留 4 步门控 / 8 个 decision_source enum / deny/stop yield SSE / 审计日志]

### Requirement: SSE 流式输出
[从 astream(stream_mode) 迁移到 stream_events v3，保留 thinking/text/tool_call/tool_result/source 5 类事件 / [srcN] 来源引用 / 推到预览按钮]

### Requirement: 工具上下文注入
[从 PrivateAttr 迁移到 InjectedToolArg + context_schema，保留 ToolContext 所有字段]

## REMOVED Requirements

### Requirement: streaming_event_bus 队列合并
**Reason**: 迁移到 StreamWriter + CustomStreamPart + stream_events v3 原生投影
**Migration**: 保留 _merge_agent_and_tool_events 作为 fallback，待 stream_events v3 验证稳定后删除

## 核心约束（来自用户）

1. **严格保持现有全部功能完整性**：chunk 分块逻辑 / 边界条件处理 / 前端 SSE 来源引用机制 / 代码高亮 / HITL / 审计日志 / Agent 流式输出 / RAG 检索 / 会话持久化 / 硬件工作台全部不能丢
2. **定制化实现也要迁移**：rrf_fusion 算法 / FTS5 词法检索 / 手动 rerank 跨 KB 批量优化 / HybridChunker 边界处理必须保留
3. **双 subagent 验证**：每阶段完成后启动完整性审查 + 功能测试双 subagent，发现问题修复循环直至全部通过
4. **保持回滚性**：每阶段独立 commit，可独立回滚

## Fallback 方案

- **阶段 3 Task 8-11 任一失败**：保留旧 API + pitfalls.md 记录失败原因，不影响其他 task
- **stream_events v3 事件结构不兼容**：保留 astream(stream_mode) + 适配层转换 v3 事件
- **interrupt_before 在 create_agent 中行为不一致**：保留 create_react_agent + 仅迁移 stream_events v3
- **create_agent 不支持 checkpointer**：保留 create_react_agent + 用 middleware 接入新特性
