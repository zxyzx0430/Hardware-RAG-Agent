## 任务：全面代码扫描（两轮，不修）

目标线程：02-chat（SSE 流式聊天）

范围文件：
- frontend/src/components/chat/ChatArea.tsx
- frontend/src/components/chat/InputBar.tsx
- frontend/src/stores/useChatStore.ts
- frontend/src/hooks/useSSE.ts
- backend/app/api/chat_routes.py
- backend/src/llm/client.py

方法：做两轮扫描，每轮把结果追加到 `docs/review/02-chat-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 20-30 分钟通读全部范围文件，找以下问题：

1. 类型安全
   - TS strict 模式报错
   - 变量/参数可能为 null 但未做守卫
   - Python 类型注解缺失或 mismatch

2. 功能完整性
   - SSE 事件类型是否全部在前端有处理
   - AbortController 是否在停止流式时正确清理
   - streaming state 的进入/退出时机是否正确

3. 代码异味
   - 不再使用的 import、变量
   - 重复的 SSE 事件处理逻辑
   - 硬编码的字符串/数字

4. 资源泄漏
   - EventSource / fetch 请求在组件卸载时关闭
   - SSE 重连是否有指数退避

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 3-5 个最可疑的路径做深度检查：

1. 契约对齐
   - Sse 事件字段名和 api-contract.md 是否一致
   - POST /api/chat 请求体字段和文档一致吗

2. 错误处理
   - SSE 连接中断前端显示什么
   - 后端 LLM 调用失败时 SSE 是否发送 error 事件
   - 用户消息为空 / 超长 有处理吗

3. 竞态条件
   - 快速发多条消息是否导致流式数据乱序
   - 停止流式并重新发送时 state 是否正确重置

4. 边界情况
   - 消息内容包含特殊字符 / HTML / Markdown 注入
   - 对话历史超长时 token 截断逻辑是否生效

### 输出格式

每个问题按这个格式写：

```
## [P0/P1/P2] 简短标题

- 位置：文件路径:行号
- 现象：
- 影响评估（出故障时用户看到什么）：
- 建议修复方式（一句话）：
```

优先级含义：
- P0 = 上线就炸（空引用、未捕获异常、接口不匹配）
- P1 = 体验劣化（UI 异常、加载失败、操作无反馈）
- P2 = 代码质量（可优化、可抽取、可清理）

### 完成后

两轮都做完后，通知 00-control：「02-chat 扫描完成，结果在 docs/review/02-chat-scan.md」

注意：不要修，只记录。
