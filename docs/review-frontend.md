# 前端代码质量审查报告

审查范围：`E:\Desktop\agent\frontend`
重点文件：
- `src/types/api.ts`
- `src/api/client.ts`
- `src/stores/useChatStore.ts`
- `src/components/chat/ChatArea.tsx`

> 说明：本次审查以 `docs/api-contract.md` 为接口契约唯一来源，同时对照后端实际实现（`backend/app/api/routes.py`、`backend/src/llm/client.py`）做交叉验证。仅做审查，未修改代码。

---

## 1. `E:\Desktop\agent\frontend\src\types\api.ts`

- **问题位置**：`src/types/api.ts` 第 68–74 行 `ChatSSEEvent` 联合类型
  - **具体问题**：`ChatSSEEvent` 当前仅包含 `thinking | text | tool | source | done | error`，缺少任务要求覆盖的 `plan` 与 `progress` 类型。虽然后端当前聊天流暂未发送这两类事件，但类型定义不完整，导致 `useChatStore` 的 `switch` 分支也无法处理。
  - **建议修复方式**：
    1. 在 `api-contract.md` 中补充 `plan`/`progress` 事件的定义、触发时机与字段；
    2. 前端新增 `PlanSSEEvent`、`ProgressSSEEvent` 并加入 `ChatSSEEvent` 联合类型；
    3. 在 `useChatStore` 中补充分支处理（或至少记录日志/警告）。
  - **优先级**：medium

- **问题位置**：`src/types/api.ts` 第 16–20 行 `ChatRequest`
  - **具体问题**：类型声明为 `{ messages; model; settings?: Record<string, unknown> }`，但实际 `useChatStore.sendMessage` 构建的请求体是扁平结构（`top_k`、`temperature`、`max_tokens`、`system_prompt`、`long_term_memory`、`model`），后端 `ChatRequest` 也按扁平字段解析。类型声明与实现、契约均不一致，形同虚设。
  - **建议修复方式**：将 `ChatRequest` 改为与契约/实现一致的扁平字段结构，删除 `settings` 包层；或在代码中显式使用该类型约束 `requestBody`。
  - **优先级**：medium

- **问题位置**：`src/types/api.ts` 第 22–26 行 `TokenUsageSSE`
  - **具体问题**：字段 `prompt_tokens / completion_tokens / total_tokens` 为 `number` 类型，但后端 `usage_data` 中可能出现 `None`（OpenAI SDK 的 `or 0` 已在后端处理），类型上未体现可空性。当前实现中 `useChatStore` 做了 `|| 0` 兜底，类型侧可更严谨。
  - **建议修复方式**：将字段声明为 `number`（因后端已兜底）并补充注释说明后端保证非空；或声明为 `number | null` 并在消费处强制校验。
  - **优先级**：low

- **问题位置**：`src/types/api.ts` 第 28–31 行 `ThinkingSSEEvent`
  - **具体问题**：字段名为 `content`，而 `docs/api-contract.md` 第 5.1 节将 `thinking` 事件的前端消费字段写作 `message`。代码与后端实际发送一致（`content`），但与契约文档冲突。
  - **建议修复方式**：按 PLUR 规则，先更新 `docs/api-contract.md` 中的字段说明为 `content`，再统一前后端；若坚持契约，则后端应改为 `message`。
  - **优先级**：medium

- **问题位置**：`src/types/api.ts` 第 39–45 行 `ToolSSEEvent`
  - **具体问题**：`args` 声明为 `string?`，但契约 5.1 节示例中 `args` 为对象（`{ "query": "..." }`）。后端实际发送的是格式化字符串（`f'query="..." · top_k=...'`），前端类型只能接受字符串，若后端未来改为对象会触发类型/展示错误。
  - **建议修复方式**：在契约中明确 `args` 的传输类型（对象或字符串），并在前端做统一序列化/展示兼容；例如声明为 `string | Record<string, unknown>`。
  - **优先级**：medium

- **问题位置**：`src/types/api.ts` 第 47–55 行 `SourceSSEEvent`
  - **具体问题**：字段名为 `id`，而 `docs/api-contract.md` 第 5.1 节描述为 `chunk_id`。代码与后端实际一致（`id`），但与契约冲突。
  - **建议修复方式**：更新契约文档中的字段名为 `id`，保持与实现一致。
  - **优先级**：medium

- **问题位置**：`src/types/api.ts` 第 84–93 行 `ToolCall` / `ToolResult`
  - **具体问题**：`ToolResult` 声明了 `success: boolean`，但契约 5.9 节 `/api/tool` 成功响应仅返回 `{ output, duration_ms? }`。额外的 `success` 字段未在契约中登记，可能导致前端依赖一个后端不存在的字段。
  - **建议修复方式**：要么在契约中补充 `success` 字段，要么将 `ToolResult.success` 改为可选或移除，并在消费处仅通过 HTTP/ApiError 判断失败。
  - **优先级**：low

- **问题位置**：`src/types/api.ts` 第 144–148 行 `PinAuditResponse`
  - **具体问题**：比契约 5.6 节多出 `safe: boolean` 字段。若后端实际不返回该字段，则前端依赖它会导致误判；若已约定但未写入契约，则违反“先写文档再写代码”的 PLUR 约束。
  - **建议修复方式**：确认后端是否返回 `safe`，并同步更新契约；若未返回则删除该字段。
  - **优先级**：low

---

## 2. `E:\Desktop\agent\frontend\src\api\client.ts`

- **问题位置**：`src/api/client.ts` 第 105–179 行 `apiSSE`
  - **具体问题**：`connTimer`（60s 连接超时）仅在响应头到达后 `clearTimeout`（第 127 行）。若 `fetch` 在响应头前抛错（网络断开、DNS 失败等），`catch` 块会直接返回/报错，但 `connTimer` 仍可能在 60s 后 `controller.abort()`。当使用外部 `AbortController` 时，这会污染调用方控制器。
  - **建议修复方式**：将 `clearTimeout(connTimer)` 放入 `try...finally` 或在 `catch` 入口立即清理。
  - **优先级**：high

- **问题位置**：`src/api/client.ts` 第 139–168 行 SSE 解析循环
  - **具体问题**：每次处理完 `data:` 行后立即 `currentEvent = ""`（第 165 行）。SSE 标准允许一个事件包含多行 `data:`，此时只有第一行能拿到 `event:` 类型，后续行会丢失事件名。当前后端每事件单行数据，暂未触发，但解析器不健壮。
  - **建议修复方式**：仅在遇到空行或新的 `event:` 行时才重置 `currentEvent`；多行 `data` 拼接为一个 payload。
  - **优先级**：low

- **问题位置**：`src/api/client.ts` 第 104–180 行 `apiSSE`
  - **具体问题**：仅有连接超时（60s），缺乏“读超时 / 空闲超时”。契约 2.17 节提到后端聊天 SSE 建议 5 分钟无 token 自动断开，但前端未主动检测长空闲，完全依赖后端。弱网/后端异常挂起时用户体验差。
  - **建议修复方式**：增加基于最后收到数据时间的读超时（例如 5 分钟无数据则主动断开并触发 `onError`），并按契约实现指数退避重连策略（1s→2s→4s…）。
  - **优先级**：medium

- **问题位置**：`src/api/client.ts` 第 158–161 行 `done` 事件处理
  - **具体问题**：`done` 事件在 `onEvent` 回调中直接触发 `onDone?.()`，没有等待 body 读取完成。若 `done` 事件之后还有额外数据（如规范要求空行或后续 error），`onDone` 可能过早执行。
  - **建议修复方式**：`onDone` 在 body 自然结束（`reader.read()` 返回 `done`）后调用，而非在收到 `type: done` 事件时立即调用。
  - **优先级**：low

- **问题位置**：`src/api/client.ts` 第 40–52 行 `unwrapResponse`
  - **具体问题**：对没有 `success` 字段的响应做“后向兼容原样返回”。契约 2.6 节强制要求所有 JSON 接口使用 `{success, data/error}`，该兼容逻辑会掩盖未按契约实现的接口，导致前端在错误场景下拿不到 `ApiError`。
  - **建议修复方式**：移除后向兼容分支，对缺失 `success` 的 JSON 响应直接抛错；若确有历史接口，先补契约再单独处理。
  - **优先级**：low

- **问题位置**：`src/api/client.ts` 第 104–179 行 `apiSSE`（直接请求第三方 API fallback 检查）
  - **具体问题**：经检查，`client.ts` 中所有 `fetch` 均只请求 `/api/*` 前缀，未发现直接调用第三方 LLM/OpenAI 接口的 fallback 代码。该检查项通过。
  - **建议修复方式**：无。
  - **优先级**：low（通过项，仅记录）

- **问题位置**：`src/api/client.ts` 第 192–194 行 `apiWS`
  - **具体问题**：WebSocket URL 使用 `window.location.port`，开发模式下 Vite 端口（如 5173）与后端端口（58080）不同，除非 devServer 代理了 WebSocket，否则连接会指向 Vite 而非后端。契约 2.1 节写明本地后端 WS 地址为 `ws://127.0.0.1:58080/api`。
  - **建议修复方式**：通过环境变量或 Vite 配置区分开发/生产 WS 目标地址；开发环境显式指向后端 58080 端口。
  - **优先级**：low

---

## 3. `E:\Desktop\agent\frontend\src\stores\useChatStore.ts`

- **问题位置**：`src/stores/useChatStore.ts` 第 197–329 行 SSE `onEvent` switch
  - **具体问题**：`switch (sse.type)` 仅处理 `thinking / text / tool / source / done / error`，缺少 `plan` 与 `progress` 分支。虽然当前后端聊天流不发送这两类事件，但 `ChatSSEEvent` 联合类型也未包含它们，未知事件直接进入 `default` 被静默忽略。
  - **建议修复方式**：与 `types/api.ts` 联动，补充 `plan`/`progress` 类型与处理分支；暂时无法处理时至少输出警告日志。
  - **优先级**：medium

- **问题位置**：`src/stores/useChatStore.ts` 第 188–330 行 `onEvent` 回调
  - **具体问题**：未发现 `(sse as any)` 类型断言。事件对象直接赋值给 `const sse = event`，依靠 TypeScript 联合类型推断。该检查项通过。
  - **建议修复方式**：无。
  - **优先级**：low（通过项，仅记录）

- **问题位置**：`src/stores/useChatStore.ts` 第 181–182 行 `requestSessionId` 闭包
  - **具体问题**：`requestSessionId` 在 `sendMessage` 开始时捕获 `activeSessionId`，并在 `onEvent / onDone / onError` 中始终使用该 ID 写入 `sessionMessages`。逻辑正确，能避免会话切换导致数据写入当前活跃会话的问题。
  - **建议修复方式**：无。可在注释中补充说明闭包用途，便于后续维护。
  - **优先级**：low

- **问题位置**：`src/stores/useChatStore.ts` 第 427–441 行 `stopStreaming` 与 `apiSSE` `onDone`/`onError`
  - **具体问题**：存在竞态条件。`stopStreaming` 调用 `controller.abort()` 后，会设置 `isStreaming=false` 并保存当前部分消息；但 `apiSSE` 在 abort 前可能已收到 `done` 事件并将 `onDone` 加入 Zustand 更新队列，`onDone` 随后可能覆盖 `stopStreaming` 写入的最终状态（如 `currentSseRequest`、`streamingContent`）。
  - **建议修复方式**：
    1. 在 `stopStreaming` 中设置一个“已手动停止”标记，并在 `onDone`/`onError` 中检查该标记以决定是否覆盖；
    2. 或让 `apiSSE` 在因用户主动 abort 时通过回调通知，而不是直接吞掉错误。
  - **优先级**：medium

- **问题位置**：`src/stores/useChatStore.ts` 第 427–441 行 `stopStreaming`
  - **具体问题**：`stopStreaming` 在 `set` 回调外部通过 `get()` 获取 `messages`、`streamingContent`、`streamingSteps`，并在回调内使用这些闭包值。虽然发送消息期间不会有新消息插入，但严格来说应使用 `set` 回调中的最新 state（`s.messages` 等），避免潜在的状态覆盖。
  - **建议修复方式**：将 `messages / streamingContent / streamingSteps` 的读取移入 `set((s) => { ... })` 回调内部。
  - **优先级**：low

- **问题位置**：`src/stores/useChatStore.ts` 第 312–325 行 `error` 事件处理
  - **具体问题**：当请求会话不在前台（`isActive=false`）时，`error` 分支仅更新 `sessionMessages`，未重置全局 `isStreaming / currentSseRequest` 等状态。虽然切会话时已清空前台状态，但如果用户快速切回原会话，可能看到不一致的流式 UI。
  - **建议修复方式**：在 `error` 与 `onError` 中，无论是否活跃会话，只要当前请求就是 `currentSseRequest` 所指向的请求，就统一清理全局流式状态。
  - **优先级**：low

- **问题位置**：`src/stores/useChatStore.ts` 第 478–497 行 `branchThread`
  - **具体问题**：直接调用 `useSessionStore.getState().newSession()`（其内部会切换 active session），然后通过 `setTimeout(..., 100)` 异步设置分支消息。依赖 100ms 延迟不可靠，且 `newSession` 可能生成与预期不同的 ID。
  - **建议修复方式**：让 `newSession` 返回新会话 ID，或提供一个原子性的“创建会话并设置消息”接口，避免依赖 setTimeout 竞态。
  - **优先级**：low

---

## 4. `E:\Desktop\agent\frontend\src\components\chat\ChatArea.tsx`

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 47–54 行自动滚动 `useEffect`
  - **具体问题**：依赖数组只有 `[messages.length]`，且明确在 `isStreaming` 时不滚动。虽然注释称这是 intentional，但流式输出期间长回答不会自动跟随，用户需要手动滚动，体验不佳。
  - **建议修复方式**：
    1. 增加“用户未手动上滚”检测，仅在未主动上滚时对流式内容自动滚动；
    2. 或将 `streamingContent` 加入依赖并在适当条件下平滑滚动。
  - **优先级**：low

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 228–243 行 `ActivityBlock` 计时 `useEffect`
  - **具体问题**：依赖数组 `[isRunning, startTime]` 完整，未遗漏。当 `isRunning` 变化时会清理/重建定时器。该检查项通过。
  - **建议修复方式**：无。
  - **优先级**：low（通过项，仅记录）

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 228 行 `ActivityBlock` props 类型
  - **具体问题**：`activity.steps` 被声明为 `any[]`，`ThinkingStep`、`ToolStep` 的 `step` 参数也是 `any`。由于实际数据结构来自 `ActivityStep` 类型，使用 `any` 会丢失类型安全，隐藏字段名拼写等错误。
  - **建议修复方式**：将 `ActivityBlock`、`ThinkingStep`、`ToolStep` 的参数类型改为 `ActivityBlock` / `ActivityStep`。
  - **优先级**：medium

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 315–347 行 `ToolStep`
  - **具体问题**：`stepStatus = step.status || 'done'`。流式期间 `useChatStore` 创建 tool step 时未设置 `status`，因此运行中的 tool 卡片会显示为“done”，没有 `running` 样式或 spinner；只有外层 `ActivityBlock` 显示 running。
  - **建议修复方式**：在 `useChatStore` 生成 tool step 时设置 `status: 'running'`，在 `onDone` 时统一改为 `'done'`；或在 `ToolStep` 中结合 `ActivityBlock` 的 `status` 判断子步骤状态。
  - **优先级**：medium

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 100–101 行 `lastUserMsgId`
  - **具体问题**：在 `messages.map` 的渲染过程中通过副作用修改外部变量 `lastUserMsgId`。React 的并发渲染可能导致 render 执行多次，从而在该变量上产生不可预期的结果。
  - **建议修复方式**：改用 `useMemo` 预先计算每条 assistant 消息对应的“上一条用户消息 ID”，或从消息数组反向查找，不在 render 阶段产生副作用。
  - **优先级**：low

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 197–201 行代码推送检测
  - **具体问题**：使用 `msg.content.includes('#include')` 判断消息是否包含 C++ 代码并显示“推送到预览”按钮。该规则极不健壮：没有 `#include` 的代码不会被识别；普通文本中包含 `#include` 会被误判。
  - **建议修复方式**：基于消息中的代码块（```cpp / ```c）或 `msg.activity` 中工具返回的代码类型做判断。
  - **优先级**：low

- **问题位置**：`src/components/chat/ChatArea.tsx` 第 176–196 行消息操作栏
  - **具体问题**：消息操作栏对当前正在流式输出的最后一条 assistant 消息隐藏（`!isCurrentlyStreaming`），符合设计预期；对历史 assistant 消息始终显示。未发现可见性错误。该检查项通过。
  - **建议修复方式**：无。
  - **优先级**：low（通过项，仅记录）

---

## 5. 契约/实现不一致汇总（需优先对齐文档）

按 PLUR 约束 `[ENG-2026-0619-004]`，接口字段不一致时应先改 `docs/api-contract.md`，再改代码。

| 文件 | 字段/事件 | 当前实现 | 契约文档 | 建议动作 |
|------|-----------|----------|----------|----------|
| `types/api.ts` | `thinking` 事件 | `content` | `message` | 更新契约为 `content` |
| `types/api.ts` | `source` 事件 | `id` | `chunk_id` | 更新契约为 `id` |
| `types/api.ts` | `tool.args` | `string` | 对象示例 | 契约中明确传输类型 |
| `types/api.ts` | `ChatRequest` | 扁平字段 | `settings` 包层 | 更新契约为扁平字段 |
| `types/api.ts` | `ToolResult.success` | 存在 | 未登记 | 补登记或删除 |
| `types/api.ts` | `PinAuditResponse.safe` | 存在 | 未登记 | 补登记或删除 |

---

## 6. 优先级分布

- **high**：1 项（`apiSSE` 连接超时定时器未清理）
- **medium**：11 项（类型缺失、字段名/契约不一致、SSE 解析健壮性、竞态、类型安全等）
- **low**：13 项（体验优化、代码健壮性、通过项记录等）
