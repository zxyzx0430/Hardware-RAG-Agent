# 接入上下文压缩到 Agent 主循环 + 可配置上下文窗口 Spec

## Why

当前 Agent 路径的上下文管理是"硬熔断"模式：累计 token 超 `context_window × 0.8` 就抛 `ContextLimitError` 直接断流，用户看到"上下文超长"报错。已有的 `autocompact.py`（LLM 摘要压缩）写好了但没接入主循环。用户需要：长对话时自动压缩历史而非断流，且支持 256K/1M 两档上下文窗口配置，切换窗口时自动处理超限历史。

## What Changes

### 后端
- **接入 autocompact 到 sse_adapter 主循环**：每次工具调用后检查 token，超窗口 80% 时调 LLM 摘要旧消息，压缩完继续循环
- **autocompact 阈值改为动态比例**：从固定 13000 改为 `context_window × 0.8`，适配不同窗口大小
- **新增 snip 截断兜底**：autocompact（LLM 摘要）失败 3 次后，降级为 snip（删除旧消息 + 留存在标记），不调 LLM
- **新增 SSE context_compressing 事件**：压缩开始时发 SSE 事件，前端显示"正在压缩上下文..."
- **chat_routes 发送前检查**：用户发消息时，先检查当前 session 历史是否超窗口 80%，超了先压缩再发给 LLM
- **context_guard 支持用户配置窗口**：`compute_token_limit` 优先用 session 级 context_window，其次 model_registry
- **session 表加 context_window 字段**：每个会话独立配置 256K 或 1M

### 前端
- **设置页加上下文窗口下拉框**：256K / 1M 两档，带说明文字告知影响和切换注意事项
- **session 级 contextWindow 状态**：切换会话时读取该会话的窗口配置
- **SSE context_compressing 事件处理**：显示"正在压缩上下文..."加载状态

### 移除
- **microcompact 不接入**：占位符替换会丢信息，用户明确要求只用 LLM 摘要压缩

## Impact
- Affected code: `backend/src/agent/sse_adapter.py`, `backend/src/agent/compact/autocompact.py`, `backend/src/agent/context_guard.py`, `backend/app/api/chat_routes.py`, `backend/app/db/models.py`, `backend/app/db/crud.py`, `frontend/src/components/settings/SettingsPage.tsx`, `frontend/src/stores/useSessionStore.ts`, `frontend/src/stores/useSettingsStore.ts`
- Affected specs: agent-react-fullstack (§6.3 token budget), agent-reliability-batch (context guard)

## 摘要压缩比例设计（基于业界调研）

### 调研结论
| 项目 | 摘要目标长度 | keep_recent | 每条消息截断 | 保留信息项数 |
|------|------------|-------------|-------------|-------------|
| Claude Code | 200-500 词 | 动态 | — | 7 项 |
| OpenHands | 200-300 词 | 10 事件 | 500 字符 | 4 项 |
| LangChain | 渐进增长 | max_token_limit 内 | — | LLM 决定 |
| **本项目当前** | 200 字 | 10 条 | 500 字符 | 4 项 |
| **本 spec 改为** | **500 字** | **10 条** | **500 字符** | **7 项** |

### 摘要 prompt 改进
当前 prompt（`autocompact.py` L36-39）：
```
用 200 字以内摘要以下对话的关键信息（芯片型号、接线方案、用户需求、已做的工作）
```

改为（参考 Claude Code 7 项要素）：
```
你的任务是对以下对话创建详细摘要，重点保留：
1. 用户的原始请求和意图
2. 关键技术决策及理由
3. 涉及的芯片型号、接线方案、文件路径
4. 重要代码片段或配置
5. 遇到的错误及解决方式
6. 当前任务状态（做到哪一步）
7. 待办事项和下一步
用 500 字以内输出摘要。
```

理由：200 字对于含芯片型号、接线方案、文件路径等技术信息的对话太短，容易丢失关键实体。500 字是 Claude Code / OpenHands 的主流范围。

## ADDED Requirements

### Requirement: 摘要 prompt 改进
系统 SHALL 将 autocompact 的摘要 prompt 从"200 字 + 4 项要素"改为"500 字 + 7 项要素"（用户意图、技术决策、芯片/接线/文件路径、代码片段、错误及解决、当前状态、待办事项）。

### Requirement: 工具调用后自动压缩检查
系统 SHALL 在 Agent 每次工具调用完成后，检查累计 token 是否超过 `context_window × 0.8`。超限时自动触发 autocompact（LLM 摘要），压缩完成后 Agent 继续下一轮循环。

#### Scenario: 工具调用后 token 未超限
- **WHEN** Agent 调用工具完成后，累计 token < 窗口 80%
- **THEN** 不做任何压缩，继续正常循环

#### Scenario: 工具调用后 token 超限
- **WHEN** Agent 调用工具完成后，累计 token ≥ 窗口 80%
- **THEN** 发送 SSE `context_compressing` 事件 → 调 LLM 摘要旧消息（保留最近 10 条）→ 压缩完成 → Agent 继续循环

### Requirement: autocompact 阈值动态化
系统 SHALL 将 autocompact 触发阈值从固定 13000 token 改为 `context_window × 0.8`，根据用户配置的窗口大小自动计算。

### Requirement: snip 截断兜底
系统 SHALL 在 autocompact LLM 摘要连续失败 3 次后，降级为 snip 截断：删除早期消息但保留"已删除 N 条消息"存在标记，不调 LLM。

#### Scenario: autocompact 成功
- **WHEN** LLM 摘要调用成功
- **THEN** 返回 `[历史摘要]: <摘要>` + 最近 10 条消息

#### Scenario: autocompact 失败 3 次
- **WHEN** LLM 摘要连续失败 3 次
- **THEN** 返回 `[已删除 N 条早期消息]` + 最近 10 条消息，不调 LLM

### Requirement: SSE context_compressing 事件
系统 SHALL 在压缩开始时发送 SSE 事件 `{"type": "context_compressing", "message": "正在压缩上下文..."}`，压缩完成后自动继续流式输出。

### Requirement: 发送前历史检查
系统 SHALL 在用户发送消息时，先检查当前 session 历史是否超窗口 80%。超限时先压缩历史，再发给 LLM。

#### Scenario: 1M 切换 256K 后历史超限
- **WHEN** 用户将窗口从 1M 切换到 256K，当前历史 token > 256K × 80%
- **AND** 用户发送下一条消息
- **THEN** 发送 SSE `context_compressing` → 压缩历史 → 压缩完成后发给 LLM → 正常流式回复

### Requirement: 可配置上下文窗口
系统 SHALL 在设置界面提供 256K / 1M 两个上下文窗口选项，配置为 session 级（每个会话独立）。

#### Scenario: 切换窗口
- **WHEN** 用户在设置界面将上下文窗口从 1M 切换到 256K
- **THEN** 当前 session 的 context_window 更新为 256K
- **AND** 下次发消息时按 256K 窗口检查历史

#### Scenario: 下拉框说明文字
- **WHEN** 用户打开上下文窗口下拉框
- **THEN** 显示说明："上下文窗口决定 AI 能记住的对话长度。256K 适合大多数场景，1M 适合超长对话。切换到更小窗口时，如果当前对话已超出，系统会自动压缩历史。"

### Requirement: session 级 context_window 存储
系统 SHALL 在 session 表中存储 context_window 字段（值为 262144 或 1048576），默认 262144（256K）。

## MODIFIED Requirements

### Requirement: context_guard token_limit 计算
`compute_token_limit` SHALL 优先使用 session 级 context_window 配置。当 session 未配置时，回退到 model_registry 的模型默认窗口。

### Requirement: autocompact 触发逻辑
`should_autocompact` SHALL 使用动态阈值 `context_window × 0.8` 替代固定 13000。阈值通过参数传入，不再用模块级常量。

## REMOVED Requirements

### Requirement: microcompact 接入主循环
**Reason**: 用户明确要求不用占位符替换（会丢关键信息），只用 LLM 摘要压缩。
**Migration**: microcompact.py 文件保留但不在主循环调用。autocompact 承担全部压缩职责。
