# 修复 reasoning 提取与 source 引用 Spec

## Why

用户实测 Agent 路径有两个回归 bug：
1. **source [srcN] 不渲染成按钮**：LLM 输出用 `**esp32_datasheet.pdf**` 加粗代替 `[src1]` 引用，前端按钮逻辑健全但无 `[srcN]` 可转。根因：SYSTEM_PROMPT 指令太弱（一行 bullet + 无示例），search_docs summary 没明确要求"答案必须用这些标记引用"。
2. **思考卡片不是 API 真实 reasoning**：`reasoning_chat.py` 只提取 `delta.reasoning_content`（DeepSeek/QwQ/GLM-4.6 格式），漏了 `delta.thinking`（部分 provider）和 `delta.reasoning`（OpenAI o1 兼容）。非推理模型显示 placeholder "模型正在思考..."，用户要求"只显示真实 reasoning，无则不显示卡片"。

调研结论：
- 智谱 GLM-4.6 用 `reasoning_content`（当前已支持），但 GLM-5.x 可能改用 `thinking` 字段
- Kimi K2.7 Code 强调 `preserve thinking` 多轮保留
- langchain ChatOpenAI 官方不回传 reasoning_content（我们的子类是正确解法，但字段名需扩展）
- 同类工具（OpenCode/OpenHands/Cline）都支持多 provider reasoning 字段提取

## What Changes

### 后端
- **reasoning_chat.py `_extract_reasoning`**：从只读 `delta.reasoning_content` 扩展为读 3 个字段：`reasoning_content` / `thinking` / `reasoning`（按优先级，首个非空者返回）
- **prompts.py SYSTEM_PROMPT**：强化 `[srcN]` 引用指令——从一行 bullet 升级为独立章节 + good/bad 示例 + 强制规则
- **prompts.py SYSTEM_PROMPT**：删除"工具调用前的意图说明"章节（用户选"只显示真实 reasoning，无则不显示卡片"，placeholder 文本不再需要）
- **search_docs.py `_build_search_summary`**：summary 末尾加一句"回答时必须用 [srcN] 引用上述片段"
- **sse_adapter.py**：删除 `_maybe_emit_placeholder_thinking` 函数 + `PLACEHOLDER_THINKING` 常量 + `reasoning_step_open` 状态机（不再需要 placeholder 逻辑）

### 前端
- 无改动（前端 thinking 事件处理已区分 source，删除 placeholder 后 source="agent" 事件不再出现，前端自然不显示卡片）

## Impact

- Affected specs: `surface-agent-reasoning`（reasoning 提取 + placeholder 删除）、`agent-reliability-batch`（source [srcN] 强化）
- Affected code:
  - `backend/src/agent/reasoning_chat.py`（`_extract_reasoning` 多字段兼容）
  - `backend/src/agent/prompts.py`（SYSTEM_PROMPT 强化 [srcN] + 删除意图说明章节）
  - `backend/src/agent/tools/groups/retrieval/search_docs.py`（summary 加引用提示）
  - `backend/src/agent/sse_adapter.py`（删除 placeholder 逻辑）

## ADDED Requirements

### Requirement: 多 provider reasoning 字段兼容

系统 SHALL 从 LLM 流式响应的 delta 中提取 3 种已知 reasoning 字段名，按优先级返回首个非空值：`reasoning_content`（DeepSeek/QwQ/Qwen/GLM-4.6）→ `thinking`（部分 provider）→ `reasoning`（OpenAI o1 兼容）。

#### Scenario: DeepSeek-R1 返回 reasoning_content

- **WHEN** LLM 流式 chunk 的 delta 含 `reasoning_content: "分析用户问题..."`
- **THEN** `_extract_reasoning` 返回 `"分析用户问题..."`
- **AND** sse_adapter 发 `thinking` 事件 source="reasoning"
- **AND** 前端显示真实推理卡片

#### Scenario: GLM-5.x 返回 thinking 字段

- **WHEN** LLM 流式 chunk 的 delta 含 `thinking: "这是一个硬件问题..."`（无 reasoning_content）
- **THEN** `_extract_reasoning` 返回 `"这是一个硬件问题..."`
- **AND** 前端显示真实推理卡片

#### Scenario: 普通模型无 reasoning 字段

- **WHEN** LLM 流式 chunk 的 delta 不含任何 reasoning 字段
- **THEN** `_extract_reasoning` 返回空字符串
- **AND** sse_adapter 不发 thinking 事件
- **AND** 前端不显示思考卡片（无 placeholder）

### Requirement: SYSTEM_PROMPT 强化 [srcN] 引用

系统 SHALL 在 SYSTEM_PROMPT 中用独立章节 + good/bad 示例 + 强制规则要求 LLM 在答案中用 `[srcN]` 格式引用知识库来源。

#### Scenario: LLM 引用知识库

- **WHEN** search_docs 返回 3 个 source（src1/src2/src3）
- **AND** summary 明确要求"回答时必须用 [srcN] 引用"
- **THEN** LLM 在答案中输出 "ESP32 有多个系列[src1]，S3 支持 USB[src2]"
- **AND** 前端 MarkdownRenderer 把 `[src1]` 转成可点击按钮

#### Scenario: LLM 不引用（闲聊）

- **WHEN** 用户问 "你好"
- **AND** Agent 不调 search_docs
- **THEN** 答案中无 `[srcN]`（无知识库来源可引用）

## MODIFIED Requirements

### Requirement: _extract_reasoning 字段提取

原实现（reasoning_chat.py L39-46）：
```python
def _extract_reasoning(chunk: dict) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    rc = delta.get("reasoning_content")
    return rc if isinstance(rc, str) else ""
```

修改后：
```python
# Known reasoning field names across providers (priority order).
_REASONING_FIELDS: tuple[str, ...] = ("reasoning_content", "thinking", "reasoning")

def _extract_reasoning(chunk: dict) -> str:
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    for field in _REASONING_FIELDS:
        val = delta.get(field)
        if isinstance(val, str) and val:
            return val
    return ""
```

### Requirement: _build_search_summary 加引用提示

原格式（search_docs.py L163-172）：
```
找到 3 条相关片段：
  [src1] ESP32 技术参考手册 (相关度 85%)
  [src2] ESP32-S3 数据手册 (相关度 78%)
  ...共 3 条，当前显示前 3 条。
```

修改后（末尾加一行）：
```
找到 3 条相关片段：
  [src1] ESP32 技术参考手册 (相关度 85%)
  [src2] ESP32-S3 数据手册 (相关度 78%)
  ...共 3 条，当前显示前 3 条。
回答时必须用 [srcN] 格式引用上述片段，N 对应 src1/src2/...
```

### Requirement: SYSTEM_PROMPT [srcN] 章节

原 SYSTEM_PROMPT "回答规范" 章节只有一行 bullet：
```
- 引用知识库来源时用 [srcN] 格式，N 对应 source 卡片 ID（src1/src2/...）。例如 "ESP32 有多个系列[src1]，S3 支持 USB[src2]"。
```

修改后升级为独立章节 + 示例：
```
## 来源引用规范（必须遵守）

调用 search_docs 后，答案中必须用 [srcN] 格式引用知识库片段，N 对应 source 卡片 ID（src1/src2/...）。

<good-example>
ESP32 有多个系列[src1]，其中 S3 支持 USB OTG[src2]，C3 是 RISC-V 架构[src3]。
</good-example>

<bad-example>
ESP32 有多个系列（见 esp32_datasheet.pdf），S3 支持 USB。
</bad-example>

规则：
1. 每个 search_docs 返回的片段都用 [srcN] 引用至少一次
2. [srcN] 紧跟在被引用的信息之后（句中或句末）
3. 不要用文档名加粗（**xxx.pdf**）代替 [srcN]
4. 闲聊/通用问题（未调 search_docs）不需要 [srcN]
```

## REMOVED Requirements

### Requirement: placeholder thinking 逻辑

**Reason**：用户选"只显示真实 reasoning，无则不显示卡片"。placeholder "模型正在思考..." 对非推理模型无信息量，且让用户误以为不是真实 reasoning。
**Migration**：删除 `PLACEHOLDER_THINKING` 常量 + `_maybe_emit_placeholder_thinking` 函数 + `reasoning_step_open` 状态机。非推理模型不再显示思考卡片，推理模型显示真实 reasoning_content/thinking。

### Requirement: "工具调用前的意图说明" prompt 章节

**Reason**：该章节要求 LLM 调工具前输出一句意图文本作为思考卡片内容。用户选"只显示真实 reasoning"后，非推理模型不显示思考卡片，该 prompt 章节冗余且可能干扰 LLM。
**Migration**：删除 SYSTEM_PROMPT 末尾的"工具调用前的意图说明"章节。推理模型的 reasoning_content 自然包含意图说明。

## 非目标（Out of Scope）

- **不优化 search_docs 性能**：bge-reranker CPU 推理慢是已知瓶颈，不在本 spec
- **不改前端 thinking 事件处理**：前端已区分 source，删除 placeholder 后自然不显示卡片
- **不改 MarkdownRenderer**：前端 [srcN] → 按钮逻辑健全，无需改
- **不实现 reasoning 多轮保留**：Kimi K2.7 的 preserve thinking 是未来增强（需改 LangGraph checkpoint）
