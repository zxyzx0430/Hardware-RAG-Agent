## 任务：全面代码扫描（两轮，不修）

目标线程：05-agent（LangGraph Agent / 工具调用）

范围文件：
- backend/src/agent/tool_router.py
- backend/app/api/tool_routes.py
- backend/app/api/mcp_routes.py
- backend/src/llm/client.py
- frontend/src/stores/useChatStore.ts (tool 相关部分)

方法：做两轮扫描，每轮把结果追加到 `docs/review/05-agent-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 20-30 分钟通读全部范围文件，找以下问题：

1. 类型安全
   - 工具调用的入参校验是否覆盖所有字段
   - 工具返回类型和前端期望是否一致

2. 功能完整性
   - 工具注册表是否存在，新工具是否自动注册
   - MCP 客户端重连逻辑是否完整
   - tool_router 的 dispatch 是否正确路由

3. 代码异味
   - 硬编码的工具列表/描述
   - 重复的工具参数 Schema 定义
   - stub 工具是否标记清楚

4. 资源泄漏
   - 工具执行超时后是否正确取消
   - MCP 连接池是否有限制

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 3-5 个最可疑的路径做深度检查：

1. 契约对齐
   - /api/tool 请求体字段和 api-contract.md 一致吗
   - SSE tool 事件字段和文档一致吗

2. 错误处理
   - 工具执行失败时前端显示什么
   - 工具调用超时（默认 30s）用户感知
   - MCP 服务器连接失败

3. 竞态条件
   - 同一个工具被多次调用时是否串行
   - LLM 并发生成时工具调用顺序

4. 边界情况
   - 工具参数为空 / 类型不对
   - 不存在/未注册的工具名
   - 工具返回数据超大

### 输出格式

每个问题按这个格式写：

```
## [P0/P1/P2] 简短标题

- 位置：文件路径:行号
- 现象：
- 影响评估（出故障时用户看到什么）：
- 建议修复方式（一句话）：
```

### 完成后

两轮都做完后，通知 00-control：「05-agent 扫描完成，结果在 docs/review/05-agent-scan.md」

注意：不要修，只记录。
