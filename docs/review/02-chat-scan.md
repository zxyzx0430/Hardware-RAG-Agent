# 02-chat 代码扫描结果

> 扫描日期：2026-06-24  | 范围：ChatArea.tsx / InputBar.tsx / useChatStore.ts / useSSE.ts / chat_routes.py / llm/client.py  | 方法：Pass 1 广度扫描 + Pass 2 深度扫描  | 状态：只记录，不修改  

---

## Pass 1 广度扫描

### [P1] ChatArea isAtBottomRef 定义了但未被消费

- 位置：frontend/src/components/chat/ChatArea.tsx:51
- 现象：isAtBottomRef 在 handleScroll 中被更新（第80行），但自动滚动逻辑实际使用 userScrolledRef 做判断。isAtBottomRef 的值从未被读取。
- 影响评估：代码冗余，不影响功能。但如果后续修改滚动逻辑，可能错误依赖一个未更新的值。
- 建议修复方式：删除 isAtBottomRef 及其相关代码，统一使用 userScrolledRef。

### [P1] chat_routes.py 附件文本提取的缩进 bug

- 位置：backend/app/api/chat_routes.py:74-75
- 现象：if text: 块内做了 truncation，但 attachment_texts.append(...) 在 if text: 块之外（同一缩进级别）。当 text 为空字符串时（extract_attachment_text 返回空），附件仍会被追加为空内容。
- 影响评估：空的附件内容会被拼入 system_prompt，产生无意义的附件片段。轻微浪费 token，不破坏功能。
- 建议修复方式：将 attachment_texts.append(...) 移入 if text: 块内。

### [P1] SSE 连接中断时前端错误提示不够具体

- 位置：frontend/src/api/client.ts:233-242
- 现象：当 fetch 抛出异常时，错误信息为通用字符串。abortedByTimeout 区分了空闲超时，但其他网络错误（如502）没有区分。
- 影响评估：用户看到连接超时但实际可能是后端崩溃（500/502）。误导排障方向。
- 建议修复方式：根据 res.status 或 err.name 输出不同提示。

### [P1] 用户消息为空/超长的边界处理

- 位置：frontend/src/stores/useChatStore.ts:sendMessage
- 现象：sendMessage 入口有守卫，空消息被静默忽略，无 UI 反馈。超长消息无拦截，直接发送给后端。
- 影响评估：用户按 Enter 没反应但没有任何提示，体验不好。超长消息可能导致 token 预算提前耗尽。
- 建议修复方式：空消息时在输入框下方显示提示。超长（>10000字符）时截断并提示。

### [P2] useChatStore 尾部嵌入 200+ 行 mock 数据

- 位置：frontend/src/stores/useChatStore.ts 末尾（约850-1069行）
- 现象：loadMockData 函数和 mock 数据硬编码在 store 文件末尾，条件守卫为 DEV。生产构建时不会挂到 window 上，但代码仍会被打包。
- 影响评估：增加约200行的 bundle 体积。不直接影响功能。
- 建议修复方式：将 mock 数据移到 src/utils/mock.ts，按需 import。

### [P2] useChatStore sessionMessages 加载逻辑在 store 初始化时同步执行

- 位置：frontend/src/stores/useChatStore.ts:84-97
- 现象：sessionMessages 的初始值通过 IIFE 同步从 localStorage 加载所有会话的消息。如果会话数多（>50），首次渲染会卡顿。
- 影响评估：大多数用户会话数少，不会触发。Edge case：大量会话时白屏时间延长。
- 建议修复方式：改为懒加载，只在切换到对应会话时从分片加载消息。

### [P2] llm/client.py _summarize_messages 方法可能未被调用

- 位置：backend/src/llm/client.py:150-172
- 现象：_summarize_messages 是 async private method，但在 chat_stream 中未调用。历史截断依赖于简单预算估算。
- 影响评估：长对话的早期消息直接丢弃而非压缩为摘要。不触发功能错误，但丢失上下文。
- 建议修复方式：在 chat_stream 中检测 budget 不足时调用 _summarize_messages 替代直接截断。

### [P2] InputBar PROVIDER_DISPLAY_NAMES 列表膨胀

- 位置：frontend/src/components/input/InputBar.tsx:32-52
- 现象：硬编码20个 provider 显示名，包括罕见供应商。构建时被全量打包。
- 影响评估：约1KB打包体积。功能正确但维护成本高。
- 建议修复方式：改为从后端返回 provider 元数据，或留空兜底。

---

## Pass 2 深度扫描

### [P0] SSE 事件字段与 api-contract.md 对齐状况

- 位置：frontend/src/types/api.ts + chat_routes.py
- 现象：
  - thinking: content + source 与 contract 一致
  - source: id/title/doc/page/score/excerpt 与 contract 一致
  - tool args: 已改为 JSON 对象，与 contract 一致
  - done + usage: 已实现
- 评估：SSE 事件字段契约对齐状况良好，早期不匹配已修复。

### [P1] 后端 LLM 调用失败时 SSE 错误事件合规

- 位置：backend/app/api/chat_routes.py:162-167
- 现象：LLM 异常被外层的 except Exception 捕获，先 yield error 再 yield done，符合契约要求。
- 影响评估：合规，前端 onError + onDone 都能正常触发。

### [P2] 快速多发消息的竞态处理

- 位置：frontend/src/stores/useChatStore.ts:sendMessage + stopStreaming
- 现象：sendMessage 入口有 isStreaming 守卫，流式输出中不允许发新消息。状态机逻辑正确。
- 影响评估：闭环正确，无已知竞态。

### [P2] 特殊字符 / Markdown 注入风险

- 位置：frontend/src/components/chat/ChatArea.tsx 使用 MarkdownRenderer
- 现象：用户消息中的 Markdown 语法由 react-markdown 渲染，默认不转义 raw HTML。InputBar 的 Markdown 预览使用 DOMPurify。
- 影响评估：react-markdown 默认安全策略足够的低风险。
- 建议修复方式：如需额外安全保障，可在 MarkdownRenderer 中添加 rehype-sanitize。

---

## 总结

| 优先级 | 数量 | 关键发现 |
|--------|------|----------|
| P0 | 0 | SSE 契约已对齐，无阻断问题 |
| P1 | 4 | 附件缩进 bug / isAtBottomRef 未消费 / SSE 错误提示不具体 / 空消息无反馈 |
| P2 | 6 | mock 数据打包 / 懒加载 / _summarize_messages 未调用 / provider 列表膨胀 / 长消息无截断 / XSS 风险（低） |

两轮扫描完成。建议优先修 P1 项，尤其是附件缩进 bug 和空消息提示。
