# LangGraph/LangChain 1.x 升级全量修复 Spec

## Why

用户已将 langchain 全家桶（langchain 0.3.0→1.3.4、langchain-core→1.4.7、langgraph 0.2.61→1.x、langchain-openai 最新）升级到 1.x，但代码层面仍有大量 0.x 写法：

- `requirements.txt` 仍 pin 旧版本号
- `MemorySaver` 在 1.x 已重命名为 `InMemorySaver`（旧名作为兼容别名保留，但官方文档推荐新名）
- `reset_thread_checkpoint` 直接访问 `cp.storage`/`cp.writes` 私有属性，1.x 内部 key 结构从 `thread_id` 变为元组 `(thread_id, ...)`，会静默失效
- `max_tokens` 参数在 langchain-openai 1.x 已弃用，应改用 `max_completion_tokens`
- `langchain_community.vectorstores.Chroma` 已弃用，应迁移到 `langchain_chroma.Chroma`
- HITL 用旧 `interrupt_before=["tools"]`，未用 1.x 原生 `interrupt()` API
- 上下文保护 / 反死循环逻辑硬编码在 sse_adapter / hitl_handler 中，未抽离为 Middleware
- Agent 会话状态用 MemorySaver（进程内存），服务重启即丢，无法恢复

本 spec 一次性解决以上全部问题，并采用 1.x 的 3 项新特性：`interrupt()` + `Command(resume=...)` 原生 HITL、Middleware 系统、SqliteSaver 持久化。

## What Changes

### 兼容性修复（HIGH PRIORITY）

- 更新 `backend/requirements.txt`：langchain==1.3.4、langchain-core==1.4.7、langchain-openai 最新 1.x、langgraph==1.x、langchain-chroma 新增、langchain-community 移除
- `agent_factory.py`：
  - `MemorySaver` → `InMemorySaver`（导入路径 `langgraph.checkpoint.memory`）
  - `reset_thread_checkpoint` 改用 checkpointer 公开 API（`agets` / `aput` 或遍历 `storage` 时按元组 key 第一项过滤），不再硬编码 `k[0] == thread_id`
  - `_build_llm` 的 `max_tokens=` 参数 → `max_completion_tokens=`
  - 删除 `interrupt_before=["tools"]` 参数（HITL 改用 `interrupt()` 后不需要）
- `reasoning_chat.py`：验证 `_convert_chunk_to_generation_chunk` override 在 langchain-openai 1.x 是否仍生效；若签名/行为变化，迁移到 `chat_models._convert_chunk_to_generation_chunk` 或改用 `additional_kwargs` 透传
- `tool_spec.py` L90：`_ctx: ToolContext = PrivateAttr(default=None)` → `_ctx: ToolContext | None = PrivateAttr(default=None)`
- `vector_store.py` L20：`from langchain_community.vectorstores import Chroma` → `from langchain_chroma import Chroma`

### HITL 改用 interrupt() + Command(resume=...) **BREAKING for hitl_handler**

- 删除 `agent_factory.py` 中 `interrupt_before=["tools"]` 配置
- `hitl_handler.py` 重构：
  - 不再依赖 `agent.get_state(config).next == ["tools"]` 检测中断
  - 在工具节点内部用 `langgraph.types.interrupt({...})` 主动暂停，payload 包含 `tool_name`/`args`/`risk_level`/`call_id`
  - resume 时仍用 `Command(resume={"action": "allow" | "deny" | "stop"})`，但消费逻辑改为读取 `Command.resume` 的返回值
  - **PLUR 约束 [ENG-2026-0702-001]**：deny/stop 分支注入 ToolMessage 后，必须在 resume 流中主动 yield 对应的 `tool_result` SSE 事件，否则前端工具卡片永久卡 pending

### Middleware 抽离

- 新增 `backend/src/agent/middlewares/` 目录：
  - `context_guard_middleware.py`：上下文保护（移植 `compact/autocompact.py` + `compact/microcompact.py` 的触发逻辑），在 messages-tick 边界检查 token 占用
  - `loop_guard_middleware.py`：反死循环（连续相同工具调用检测、recursion_limit 接近时警告）
- `agent_factory.py` `create_react_agent(...)` 新增 `middleware=[ContextGuardMiddleware(), LoopGuardMiddleware()]` 参数
- 旧的 autocompact/microcompact 调用点从 `sse_adapter.py` 移除（Middleware 接管）

### SqliteSaver 持久化 **BREAKING for checkpoint**

- `_get_checkpointer()` 改用 `langgraph.checkpoint.sqlite.SqliteSaver`
- 数据库路径：`backend/data/agent_checkpoints.sqlite`
- `reset_thread_checkpoint` 改用 SqliteSaver 公开 API（不再访问私有 `storage`/`writes`）
- 配置项：新增 `settings.agent_checkpointer_type`（"sqlite" | "memory"），默认 "sqlite"，便于调试时切换

## Impact

### 受影响代码

| 文件 | 改动类型 |
|------|---------|
| `backend/requirements.txt` | 版本 pin 更新 + 删除 langchain-community + 新增 langchain-chroma |
| `backend/src/agent/agent_factory.py` | MemorySaver→InMemorySaver、max_tokens、reset_thread_checkpoint、删除 interrupt_before、加 middleware 参数 |
| `backend/src/agent/reasoning_chat.py` | 验证/迁移 `_convert_chunk_to_generation_chunk` override |
| `backend/src/agent/core/toolkit/tool_spec.py` | _ctx 类型注解 |
| `backend/src/rag/vector_store.py` | Chroma 导入路径迁移 |
| `backend/src/agent/hitl_handler.py` | 重构为 interrupt() 模式 + 主动 yield tool_result SSE |
| `backend/src/agent/sse_adapter.py` | 移除 autocompact/microcompact 调用（交给 Middleware） |
| `backend/src/agent/middlewares/context_guard_middleware.py` | 新增 |
| `backend/src/agent/middlewares/loop_guard_middleware.py` | 新增 |
| `backend/src/config/settings.py` | 新增 agent_checkpointer_type 配置 |
| `backend/data/agent_checkpoints.sqlite` | 运行时生成 |

### 受影响 specs

- `industrial-tool-runtime`（ToolSpec 不变，但 HITL 流程变化）
- `agent-react-fullstack`（Agent 构造方式变化：middleware + 新 checkpointer）

## ADDED Requirements

### Requirement: Middleware 系统用于上下文保护与反死循环

The system SHALL provide a Middleware layer that intercepts Agent graph execution between nodes to enforce cross-cutting concerns (context length protection, loop detection) without polluting business logic in sse_adapter.py.

#### Scenario: 上下文 token 超过阈值时自动压缩
- **WHEN** Agent 流式执行中 messages 总 token 数超过阈值（如 100k）
- **THEN** ContextGuardMiddleware 触发 autocompact，压缩历史消息
- **AND** 压缩后继续执行，不中断用户感知的流式输出

#### Scenario: 检测到连续相同工具调用时中断
- **WHEN** LoopGuardMiddleware 检测到同一工具被连续调用 ≥3 次且 args 相同
- **THEN** Middleware 注入提示消息让 LLM 换方法
- **AND** 不阻塞流程，agent 继续推理

### Requirement: SqliteSaver 持久化 Agent 会话状态

The system SHALL persist Agent checkpoint state to SQLite so that server restart does not lose in-flight Agent conversations.

#### Scenario: 服务重启后恢复 Agent 会话
- **GIVEN** 用户发起 Agent 请求，Agent 调用工具后服务重启
- **WHEN** 用户重新发送请求（同一 session_id）
- **THEN** Agent 从 SqliteSaver 恢复上次的 checkpoint
- **AND** 不出现 INVALID_CHAT_HISTORY 错误

#### Scenario: 调试模式切换回 InMemorySaver
- **GIVEN** settings.agent_checkpointer_type == "memory"
- **WHEN** _get_checkpointer() 被调用
- **THEN** 返回 InMemorySaver 实例（不落盘，便于干净复现）

### Requirement: 原生 interrupt() HITL

The system SHALL use langgraph 1.x native `interrupt()` API for Human-in-the-Loop tool confirmation, replacing the legacy `interrupt_before=["tools"]` approach.

#### Scenario: 工具调用触发 HITL 中断
- **WHEN** Agent 调用 risk_level=MEDIUM/HIGH 工具且 permission_mode=default
- **THEN** 工具节点内部调用 `interrupt({tool_name, args, risk_level, call_id})`
- **AND** 前端收到 `tool_confirm_required` SSE 事件展示 ConfirmDialog

#### Scenario: 用户拒绝后前端工具卡片正确终结
- **WHEN** 用户点击"拒绝"
- **THEN** resume 流注入 deny ToolMessage
- **AND** resume 流主动 yield `tool_result` SSE 事件（PLUR 约束 [ENG-2026-0702-001]）
- **AND** 前端工具卡片状态变为"已拒绝"，不卡 pending

## MODIFIED Requirements

### Requirement: Agent 构造使用 1.x 兼容 API

Agent factory MUST use langchain/langgraph 1.x compatible APIs:

- `InMemorySaver` 代替 `MemorySaver`
- `max_completion_tokens` 代替 `max_tokens`
- `langchain_chroma.Chroma` 代替 `langchain_community.vectorstores.Chroma`
- checkpointer 通过公开 API 操作（不访问 `.storage`/`.writes` 私有属性）

## REMOVED Requirements

### Requirement: interrupt_before=["tools"] HITL 模式
**Reason**: langgraph 1.x 推荐 `interrupt()` 主动暂停，更细粒度且支持复杂决策树
**Migration**: `hitl_handler.py` 重写为 interrupt() 模式；`agent_factory.py` 删除 `interrupt_before` 参数

### Requirement: langchain_community.vectorstores 依赖
**Reason**: langchain 1.x 拆分了 vectorstores 到独立包 `langchain_chroma`
**Migration**: `vector_store.py` 改用 `from langchain_chroma import Chroma`；`requirements.txt` 移除 `langchain-community`，新增 `langchain-chroma`
