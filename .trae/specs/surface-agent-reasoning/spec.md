# Agent 思考链可见性 Spec

## Why

当前 Agent 路径下，用户看不到 LLM 在工具调用前后的推理过程——既看不到"我需要调用 search_docs 查询知识库"的前置思考，也看不到"检索结果提到 GPIO 复位模式是..."的后置分析。链路断在后端 [sse_adapter.py](file:///e:/Desktop/agent/backend/src/agent/sse_adapter.py)：它只发 `text` / `tool_call` / `tool_result` 事件，零处发 `thinking` 事件。前端展示能力（useChatStore + ActivityBlock.ThinkingStep）反而齐全，就等后端喂数据。

同类工具（OpenCode / OpenHands / Codex CLI / Cline / Hermes）横评结论：所有工具都实现了"工具调用前思考 + 工具调用后思考"双段展示，这是 ReAct 标准做法。本项目选 **推理模型原生派**——优先读模型 API 返回的 `reasoning_content`（DeepSeek-R1 / QwQ / o1 等推理模型自带），普通模型无 reasoning 时回退到占位事件。

## What Changes

- **后端 sse_adapter.py**：在 `_handle_message_chunk` 中读取 `AIMessageChunk.additional_kwargs.reasoning_content`，逐 chunk 增量发 `sse_event("thinking", {"content": delta, "source": "reasoning"})`
- **后端 sse_adapter.py**：普通模型无 reasoning 时，在工具调用前后自动补发占位 `sse_event("thinking", {"content": "模型正在思考...", "source": "agent"})`
- **后端 agent_factory.py**：`_build_llm` 验证 `ChatOpenAI` 是否自动捕获 DeepSeek/QwQ 的 `reasoning_content`；若未捕获，通过 `model_kwargs` 或 `extra_body` 强制透传
- **后端 prompts.py**：SYSTEM_PROMPT 不强制 `<thinking>` 标签（原生派），但加一句可选指令让普通模型在调工具前简述意图，作为占位文案的来源（替代静态"模型正在思考..."）
- **前端 useChatStore.ts**：已处理 `thinking` 事件，无需改动（`source: "agent"` 走通用分支，显示"思考中"标签）
- **前端 ActivityBlock.tsx**：ThinkingStep 已支持三种 source（reasoning/rag/其他），无需改动

## Impact

- Affected specs: `agent-react-fullstack`（Agent SSE 事件 schema 扩展）、`rag-to-agent-tool-trigger`（思考链穿插在工具调用前后）
- Affected code:
  - `backend/src/agent/sse_adapter.py`（核心改动，新增 reasoning 读取 + 占位逻辑）
  - `backend/src/agent/agent_factory.py`（`_build_llm` 透传 reasoning 字段）
  - `backend/src/agent/prompts.py`（SYSTEM_PROMPT 加可选前置意图指令）
  - `frontend/src/stores/useChatStore.ts`（只读，确认兼容 `source: "agent"`）
  - `frontend/src/components/chat/ActivityBlock.tsx`（只读，确认 ThinkingStep 渲染）

## ADDED Requirements

### Requirement: 推理模型思考链透传

系统 SHALL 读取 LLM 通过 LangGraph stream 返回的 `AIMessageChunk.additional_kwargs.reasoning_content` 字段，并以独立 SSE `thinking` 事件（`source: "reasoning"`）增量推送给前端。

#### Scenario: 推理模型产出 reasoning

- **WHEN** 用户使用 DeepSeek-R1 / QwQ / o1 等推理模型发起 Agent 请求
- **AND** LLM 在工具调用前产出 `reasoning_content: "用户问 ESP32 配置，我应该调 search_docs..."`
- **THEN** 后端逐 chunk 发送 `event: thinking` SSE 事件，`content` 为 reasoning 增量文本，`source: "reasoning"`
- **AND** 前端在 ActivityBlock 内新建 ThinkingStep（标签"推理思考"，默认展开）展示该 reasoning

#### Scenario: 工具调用后第二轮 reasoning

- **WHEN** search_docs 返回结果后，LLM 进入第二轮推理
- **AND** LLM 产出 `reasoning_content: "检索结果提到 GPIO 复位模式是..."`
- **THEN** 后端再次发送 `thinking` 事件
- **AND** 前端在 ActivityBlock 内**新建**第二个 ThinkingStep（不覆盖第一个），穿插在 tool_result 之后

### Requirement: 普通模型思考占位

系统 SHALL 在普通模型（无原生 `reasoning_content`）发起工具调用前后，自动补发占位 `thinking` 事件（`source: "agent"`），保证用户始终看到思考卡片。

#### Scenario: 普通模型调用工具前

- **WHEN** LLM 产出带 `tool_calls` 的 AIMessage 但前面未发送过 thinking 事件
- **THEN** 后端在 tool_call 事件前补发 `sse_event("thinking", {"content": "模型正在思考...", "source": "agent"})`
- **AND** 前端展示 ThinkingStep（标签"思考中"，默认折叠）

#### Scenario: 普通模型工具返回后

- **WHEN** tool_result 事件发送后，LLM 进入下一轮推理但无 reasoning_content
- **THEN** 后端在下一轮 text/tool_call 事件前补发占位 thinking 事件
- **AND** 前端新建第二个 ThinkingStep

### Requirement: 思考链状态机

系统 SHALL 维护"当前是否处于 thinking step 开启状态"，避免重复开卡片或漏关卡片。

#### Scenario: text 事件关闭 thinking

- **WHEN** thinking 事件发送后，紧接着收到 text 事件（最终答案开始流式）
- **THEN** 后端无需特殊处理（前端 useChatStore 已实现：text 流式开始时自动关闭未完成的 thinking step）

#### Scenario: tool_call 事件关闭 thinking

- **WHEN** thinking 事件发送后，紧接着收到 tool_call 事件
- **THEN** 前端自动关闭当前 thinking step（标 `status: "done"`），新建 ToolStep

## MODIFIED Requirements

### Requirement: Agent SSE 事件协议

原协议（sse_adapter.py 注释）：`text / tool_call / tool_result / error`。

修改后协议：`text / tool_call / tool_result / error / thinking`。

`thinking` 事件 schema：
```json
{
  "content": "<增量 reasoning 文本或占位文案>",
  "source": "reasoning" | "agent" | "rag" | "llm"
}
```

- `reasoning`：模型原生 reasoning_content（推理模型）
- `agent`：普通模型无 reasoning 时的占位
- `rag`：保留（历史遗留，当前后端不发）
- `llm`：fallback LLM 路径的提示（chat_routes.py L212 已有）

### Requirement: _build_llm LLM 包装

原实现（agent_factory.py L135-147）：`ChatOpenAI` 默认参数，不处理 reasoning 字段。

修改后：验证 `ChatOpenAI` 是否自动捕获 DeepSeek/QwQ 的 `reasoning_content`；若未捕获（`additional_kwargs.reasoning_content` 为空），通过 `model_kwargs` 或自定义 `extra_body` 强制上游 API 返回 reasoning。若上游 API 本身不返回 reasoning（如 GPT-4o），则回退到占位逻辑。

## REMOVED Requirements

无删除项。本 spec 纯增量扩展，不破坏现有 `text` / `tool_call` / `tool_result` / `error` 事件。

## 非目标（Out of Scope）

- **不实现 `<thinking>` 标签解析**：用户选了原生派，不强制普通模型输出标签文本
- **不修改 fallback LLM 路径**：chat_routes.py L212/L257 的 `thinking` 事件保持原样
- **不持久化 reasoning 到数据库**：reasoning 是临时展示，不存 `messages.tool_calls` 字段
- **不实现 reasoning token 计费**：usage 统计仍只算 input/output tokens

## 实现风险

1. **LangChain 版本兼容**：`ChatOpenAI` 对 `reasoning_content` 的捕获行为依赖 `langchain-openai` 版本。若版本过旧不自动捕获，需要 fallback 到 `extra_body` 或自定义 `BaseChatOpenAI` 子类。
2. **流式增量边界**：`reasoning_content` 是逐 chunk 增量，需像 text 一样累加发送，不能整体缓冲（否则用户看不到流式效果）。
3. **占位事件时序**：普通模型补发占位时，需在 tool_call 事件**之前**发，不能之后。sse_adapter 需要在检测到 `tool_calls` 即将到来时抢先发占位。
