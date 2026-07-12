# 修复 search_docs 不相关结果死循环 Spec

## Why

用户实测：Agent 调 search_docs 返回不相关结果（score 低），LLM 以为是查询词不对，不断改写查询继续调用，导致死循环。实际是知识库根本没覆盖该内容，但 LLM 无法区分"我的查询词不对"和"知识库没这个内容"。

用户明确要求：**提示应该在调用工具的时候返回（tool result 里），而不是注入系统提示词**——直接注入 SystemMessage 会不断堆叠，要按需调用（只在检测到连续多次低相关度时才附加提示）。

## What Changes

### 后端
- **search_docs.py**（**主要修复**）：SearchDocsTool 工具内部追踪连续低相关度调用次数，第 3 次时在 tool result 的 `output` 字段末尾追加"知识库可能未覆盖该内容"提示。工具实例是 per-request 的（`agent_factory.build_tools` 每次请求创建新实例），所以天然按会话隔离，无需外部状态。
- **prompts.py SYSTEM_PROMPT**（**辅助**）：保留现有"调用纪律"章节，追加一行提示让 LLM 自己也能判断"知识库可能不全"。SYSTEM_PROMPT 是常驻 prompt 不会堆叠。

### 不做的事
- **不注入 SystemMessage**：不通过 sse_adapter 实时注入 SystemMessage 到 messages 列表（会堆叠）
- **不 raise LoopDetectedError 中断流**：不新增异常类，不中断 Agent 流
- **不改 loop_detector**：现有 detect_repeat / detect_no_progress 保留作兜底，本 spec 不扩展
- **不改 sse_adapter**：现有 `_check_loop_and_hint` 流结束后兜底保留，本 spec 不扩展

## Impact

- Affected specs: 无（独立修复）
- Affected code:
  - `backend/src/agent/tools/groups/retrieval/search_docs.py`（SearchDocsTool 工具内部状态 + output 追加提示——主要）
  - `backend/src/agent/prompts.py`（SYSTEM_PROMPT 调用纪律追加一行——辅助）

## ADDED Requirements

### Requirement: search_docs 工具按需返回知识库覆盖提示

系统 SHALL 在 search_docs 工具内部追踪连续低相关度调用次数，当连续第 3 次（含）以上返回最高 score < 0.8 的结果时，在 tool result 的 `output` 字段末尾追加"知识库可能未覆盖该内容"提示。提示按需返回（仅在第 3 次及以后触发），不注入 SystemMessage，不会堆叠。

#### Scenario: 连续 3 次低相关度触发提示

- **WHEN** Agent 第 1 次 search_docs 返回最高 score 0.6
- **AND** Agent 改写查询，第 2 次 search_docs 返回最高 score 0.5
- **AND** Agent 再次改写，第 3 次 search_docs 返回最高 score 0.7
- **THEN** 第 3 次 tool result 的 `output` 末尾追加"知识库可能未覆盖该内容，建议如实告诉用户并基于通用知识回答或建议上传相关文档"
- **AND** 不注入任何 SystemMessage
- **AND** Agent 流不被中断（Agent 自己根据提示决定下一步）

#### Scenario: 第 4 次仍然低相关度继续追加提示

- **WHEN** 连续低相关度已达 3 次，第 4 次仍然低相关度
- **THEN** 第 4 次 tool result 的 `output` 末尾继续追加提示（每次都追加，提醒 LLM）

#### Scenario: 中途出现高相关度重置计数

- **WHEN** Agent 第 1 次低相关度（score 0.6），第 2 次改写后返回高相关度（score 0.9）
- **THEN** 计数器重置为 0
- **AND** 第 2 次 tool result 不追加提示
- **AND** 后续若再次连续低相关度，从 0 重新计数

#### Scenario: 空结果视为低相关度

- **WHEN** search_docs 返回空结果（results 为空）
- **THEN** 视为 max_score = 0.0（< 0.8），计数器 +1
- **AND** 连续 3 次空结果也触发提示

#### Scenario: 工具实例 per-request 隔离

- **WHEN** 用户发起一个新请求（新 session_id）
- **THEN** `agent_factory.build_tools` 创建新的 SearchDocsTool 实例
- **AND** 新实例的连续低相关度计数器从 0 开始
- **AND** 不会跨请求累积计数

## MODIFIED Requirements

### Requirement: SYSTEM_PROMPT 调用纪律追加知识库覆盖提示

原 SYSTEM_PROMPT "调用纪律" 章节（prompts.py L69-71）：
```
## 调用纪律
- 代码生成（generate_code）前必须先 search_docs 查相关硬件手册，确保基于真实参数。
- 同一工具 + 同一参数不要连续调用 2 次以上。如果工具结果不够，换查询语句或换思路。
```

修改后追加一行：
```
- 知识库内容可能不全，并非所有硬件问题都能在手册中找到答案。如果改写 3 次查询词仍然没有高度相关结果（相关度 < 80%），请如实告诉用户"知识库可能未覆盖该内容"，基于通用知识回答或建议用户上传相关文档。不要无限改写查询。
```

### Requirement: SearchDocsTool 工具内部追踪连续低相关度

原 SearchDocsTool（search_docs.py L50-108）无内部状态，每次 _arun 调用独立。

修改后：
- 新增 `_consecutive_low_relevance_count: int = PrivateAttr(default=0)` 私有属性
- 新增 `_LOW_RELEVANCE_THRESHOLD: float = 0.8` 模块常量（复用 `_RELEVANCE_HIGH_THRESHOLD`）
- 新增 `_KB_COVERAGE_HINT_TRIGGER: int = 3` 模块常量
- 新增 `_KB_COVERAGE_HINT: str` 模块常量（提示文案）
- `_arun` 调用 `_format_search_output` 后：
  1. 从 results 提取 max_score（空结果视为 0.0）
  2. max_score < 0.8 → `_consecutive_low_relevance_count += 1`；否则重置为 0
  3. `_consecutive_low_relevance_count >= 3` → 在 `output["output"]` 末尾追加 `_KB_COVERAGE_HINT`
- 新增 `_extract_max_score(results: list) -> float` 辅助函数
- 新增 `_append_kb_coverage_hint(output_text: str) -> str` 辅助函数

## REMOVED Requirements

无（本 spec 不删除任何已有功能）

## 非目标（Out of Scope）

- **不改 MAX_RECURSION**：41（20 轮硬上限）保留作兜底
- **不改 search_docs 性能**：不相关结果是正常返回
- **不改相关度阈值**：HIGH=0.8 / MEDIUM=0.5 保留
- **不改前端**：tool result 的 output 字段前端已正常展示
- **不改 loop_detector**：现有 detect_repeat / detect_no_progress 保留作兜底
- **不改 sse_adapter**：现有 `_check_loop_and_hint` 流结束后兜底保留
- **不新增 LoopDetectedError 异常**：不中断 Agent 流
- **不注入 SystemMessage**：不动态注入系统提示词（避免堆叠）

## 实现方案

### 方案选择：tool result 按需追加提示（唯一修复方式）

- **主**：search_docs 工具内部追踪连续低相关度调用次数，第 3 次起在 tool result 的 output 末尾追加提示
- **辅**：SYSTEM_PROMPT 追加一行让 LLM 自己也能判断
- **不选 SystemMessage 注入**：用户明确反对（会堆叠）
- **不选 raise + fallback**：不需要中断流，工具返回的提示已足够引导 LLM
- **不选 loop_detector 扩展**：工具内部状态更简单直接，不需要外部检测

### 为什么工具实例可以 per-request 追踪

`agent_factory.build_tools(payload)` 每次请求都创建新的 `SearchDocsTool(top_k=..., kb_ids=..., threshold=...)` 实例（agent_factory.py L185）。所以工具实例的 `_consecutive_low_relevance_count` 天然 per-request 隔离，不需要 contextvar 或 session_id 映射。

### 提示文案

```
\n\n⚠️ 知识库可能未覆盖该内容：已连续 3 次搜索未找到高度相关结果（相关度 < 80%）。建议如实告诉用户"知识库可能未覆盖该内容"，基于通用知识回答或建议用户上传相关文档，不要再继续改写查询。
```
