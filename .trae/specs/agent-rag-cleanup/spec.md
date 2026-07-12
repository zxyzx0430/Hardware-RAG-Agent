# Agent RAG 路径残留清理 Spec

## Why

项目已完成 RAG 改造——检索由 Agent 动态调用 `search_docs` 工具触发，pre-RAG 流程已删除。但改造时只删除了 pre-RAG 的调用链路，**残留的旧函数、旧参数、旧事件分支和旧类型字面量**仍散落在后端 chat_helpers.py、前端 useChatStore.ts、sse_helpers.py 和 types 定义中。

这些残留不影响功能（都是死代码或恒空参数），但让代码意图变模糊——新开发者读到 `source === "rag"` 分支会以为后端还会发 `rag` 事件，读到 `_build_rag_context` 会以为还有 RAG 上下文构建。清理后代码自文档化，降低维护成本。

同时顺手简化两个**因 pre-RAG 改造而变得冗余的条件**：
- `_should_use_agent` 重复检查 langgraph（chat_routes.py 已有模块级守卫）
- `chat_routes.py` L197 同时用 `_AGENT_PATH_AVAILABLE` 和 `_should_use_agent` 双重守卫

## What Changes

### 后端死代码清理（chat_helpers.py）
- **删除** `_classify_relevance`（L33-39）—— grep 验证零外部调用者，search_docs.py 有独立同名实现
- **删除** `_build_source_event`（L113-141）—— 标注 [DEPRECATED]，零调用者，search_docs.py 有 `build_source_event_from_dict`
- **删除** `_build_rag_context`（L144-161）—— 标注"废弃"，零调用者
- **修改** `_build_system_prompt`（L99-110）—— 删除 `rag_context` 参数 + L108-109 的 `if rag_context:` 拼接逻辑（chat_routes.py L186 恒传空串）
- **同步更新** chat_helpers.py 顶部 docstring 删除对这 3 个函数的提及

### 前端死代码清理（useChatStore.ts + ActivityBlock.tsx + types）
- **删除** useChatStore.ts 中 4 处 `lastBeforeTool.source === "rag"` 收尾分支（L767 / L792 / L811 / L836）—— 后端 Agent 路径已不 emit `source: "rag"` 的 thinking 事件
- **简化** ActivityBlock.tsx L69 的三元分支 `step.source === 'rag' ? '知识库检索' : '思考中'` —— 删除 `'rag'` 分支
- **修改** types/session.ts L80 + types/api.ts L49 —— 从 `source?: "rag" | "llm" | "reasoning" | "agent"` 中删除 `"rag"` 字面量
- **更新** useChatStore.ts mock 数据（L1384 / L1408-1409 / L1436）+ useChatStore.test.ts（L107 / L113）—— 把 `source: "rag"` 改为 `source: "reasoning"` 或移除

### sse legacy 分支清理（sse_helpers.py）
- **修改** `_extract_search_results`（L114-121）—— 删除 legacy shape 分支（`results` 顶层字段解析），SearchDocsTool 已统一走 envelope（`data.results`），legacy 分支永不命中

### Agent 触发条件简化（agent_factory.py + chat_routes.py）
- **修改** agent_factory.py `_should_use_agent`（L264-269）—— 删除内部 `try: import langgraph` 检查，统一由 chat_routes.py 模块级 `_AGENT_PATH_AVAILABLE` 守卫
- **保留** chat_routes.py L197 的 `_AGENT_PATH_AVAILABLE` 守卫——删掉 `_should_use_agent` 内部 langgraph 检查后，`_AGENT_PATH_AVAILABLE` 成为 langgraph 可用性的唯一守卫，必须保留。L197 条件不变（`if _AGENT_PATH_AVAILABLE and _should_use_agent(...)`）

### prompt 精简（prompts.py）
- **修改** SYSTEM_PROMPT L97-98 —— 删除"请如实告诉用户知识库可能未覆盖该内容"文案（与 search_docs.py `_KB_COVERAGE_HINT` 功能重复，`_KB_COVERAGE_HINT` 已由 ToolRouter 自动注入 tool result）
- **合并** L83-89"工具选择规则"与 L31-51"文档定位策略"的重叠部分 —— 文档定位策略只保留 good/bad example，规则统一到工具选择规则

## Impact
- Affected specs: `rag-to-agent-tool-trigger`（本 spec 是其收尾清理）、`implement-react-agent-fullstack`
- Affected code:
  - `backend/app/api/chat_helpers.py` — 删 3 函数 + 改 1 函数签名
  - `backend/app/api/chat_routes.py` — 简化 L197 条件 + L186 调用点同步
  - `backend/src/agent/agent_factory.py` — `_should_use_agent` 删 langgraph 检查
  - `backend/src/agent/sse_helpers.py` — `_extract_search_results` 删 legacy 分支
  - `backend/src/agent/prompts.py` — SYSTEM_PROMPT 精简
  - `frontend/src/stores/useChatStore.ts` — 删 4 处 `source === "rag"` 分支 + 更新 mock
  - `frontend/src/components/chat/ActivityBlock.tsx` — 删三元分支
  - `frontend/src/types/session.ts` + `frontend/src/types/api.ts` — 删 `"rag"` 字面量
  - `frontend/src/stores/useChatStore.test.ts` — 更新测试数据

## ADDED Requirements

（本 spec 无新增需求，纯清理 + 简化）

## MODIFIED Requirements

### Requirement: _build_system_prompt 签名简化

`_build_system_prompt(payload, attachment_texts, rag_context)` 简化为 `_build_system_prompt(payload, attachment_texts)`。删除 `rag_context` 参数及 L108-109 的 `if rag_context:` 拼接逻辑。

#### Scenario: chat_routes 调用点同步
- **WHEN** chat_routes.py L186 调用 `_build_system_prompt(payload, attachment_texts, "")`
- **THEN** 改为 `_build_system_prompt(payload, attachment_texts)`
- **AND** system prompt 构建逻辑不变（rag_context 本就恒空串）

### Requirement: _should_use_agent 删除重复 langgraph 检查

`_should_use_agent` 不再内部检查 langgraph 是否可导入（删除 L264-269 的 `try: import langgraph` 块），langgraph 可用性统一由 chat_routes.py 模块级 `_AGENT_PATH_AVAILABLE` 守卫。chat_routes.py L197 条件 `if _AGENT_PATH_AVAILABLE and _should_use_agent(...)` 保持不变——`_AGENT_PATH_AVAILABLE` 负责 langgraph 可用性，`_should_use_agent` 负责 use_agent 标志 + 模型工具支持检查。

#### Scenario: langgraph 未安装
- **WHEN** langgraph 未安装
- **THEN** chat_routes.py 模块级 `_AGENT_PATH_AVAILABLE = False`
- **AND** L197 `if _AGENT_PATH_AVAILABLE and ...` 短路为 False
- **AND** 走 fallback 路径

#### Scenario: langgraph 已安装但模型不支持工具调用
- **WHEN** langgraph 已安装（`_AGENT_PATH_AVAILABLE = True`）但模型不在 FUNCTION_CALLING_MODELS 白名单
- **THEN** `_should_use_agent` 返回 False
- **AND** 走 fallback 路径

### Requirement: SYSTEM_PROMPT 精简重复文案

删除 SYSTEM_PROMPT 中与 `_KB_COVERAGE_HINT` 功能重复的"知识库未覆盖"提示文案，合并"工具选择规则"与"文档定位策略"的重叠部分。

#### Scenario: prompt 不再重复覆盖提示
- **WHEN** Agent 连续 3 次低相关度检索
- **THEN** search_docs.py `_KB_COVERAGE_HINT` 自动注入"知识库可能未覆盖该内容"到 tool result
- **AND** SYSTEM_PROMPT 不再重复这句话（删 L97-98）
- **AND** Agent 仍能从 tool result 看到提示并停止

## REMOVED Requirements

### Requirement: chat_helpers.py 的 pre-RAG 残留函数

**Reason**: pre-RAG 流程已删除，这 3 个函数零调用者。
**Migration**:
- `_classify_relevance`（L33-39）—— search_docs.py L275 有独立同名实现，直接删除
- `_build_source_event`（L113-141）—— search_docs.py L244 `build_source_event_from_dict` 已替代，直接删除
- `_build_rag_context`（L144-161）—— 无替代（RAG 上下文构建由 Agent 自主完成），直接删除

### Requirement: 前端 source: "rag" 事件处理

**Reason**: 后端 Agent 路径已不 emit `source: "rag"` 的 thinking 事件，4 处收尾分支永不命中。
**Migration**:
- useChatStore.ts L767 / L792 / L811 / L836 —— 删除 `source === "rag"` 分支
- ActivityBlock.tsx L69 —— 删除三元中的 `'rag'` 分支
- types/session.ts L80 + types/api.ts L49 —— 从枚举删除 `"rag"` 字面量
- mock 数据 + 测试 —— 把 `source: "rag"` 改为 `source: "reasoning"`

### Requirement: sse_helpers.py legacy search results 解析

**Reason**: SearchDocsTool 已统一走 ToolSpec envelope（`data.results`），legacy shape（顶层 `results`）永不命中。
**Migration**: `_extract_search_results` 删除 L119-120 的 legacy 分支，只保留 envelope 解析。
