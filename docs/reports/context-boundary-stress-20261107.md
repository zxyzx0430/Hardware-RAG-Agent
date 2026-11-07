# 上下文边界压力测试（2026-11-07）

对象：`w1-baseline` 的上下文预算、累计守卫与预发送校验。方式：边界探针 + 真实 HTTP/SSE 请求。不是真实 20 万 token 的 provider 调用（见“未验证”）。

## 1. 边界探针（无 provider）

脚本 `backend/tests/context_stress/run_boundary_stress.py`，结果 `boundary-report.json`。

对 131,072 / 200,000 / 206,233 / 262,144 四种窗口，分别把累计上下文压到守卫上限的 50%、90%、99%、99.9%、100%、100.1%、110%、130%：

| 比例 | 结果 |
| --- | --- |
| 50% / 90% / 99% / 99.9% | 允许通过（`over_limit=false`） |
| 100% / 100.1% / 110% / 130% | 触发 `ContextLimitError`，可生成 `CONTEXT_LIMIT` SSE 事件 |

- 每个窗口的守卫上限 = `(窗口 - 输出预留 - 256) × 0.8`；例如 206,233 窗口为 **161,504**。
- 每次越过上限后重置计数器，再放一个小请求，计数器恢复正常；没有未捕获异常、没有计数污染。
- 预发送估算（`estimate_context_tokens`）在同一组比例上给出相同的 100% 分界。
- 用 4,700 token 的“工具输出”连续填充到 99.9%，再追加一块：累计从 169,776 越过 161,504，生成 `CONTEXT_LIMIT`；越界发生在跨过上限的那一块，最多一块输出的超出量。
- 最终 wire 校验（`validate_agent_request_budget`，200k 窗口 / 8,192 输出预留）：191,552 字节通过，191,751 字节拒绝，分界准确。

## 2. 真实 HTTP/SSE

- **远超上限**：向运行中的后端发送 500,000 字符的单条用户消息（`use_agent=true`，200k 窗口）。返回 `HTTP 200`，SSE 为 `CONTEXT_LIMIT`（不可重试），随后 `done success=false`；进程未崩溃。
  - 修复前同一请求返回 `INTERNAL_ERROR`（`Agent 调用失败`），因为预发送的 `AgentRequestBudgetError` 没有映射到 `CONTEXT_LIMIT`。本次已修复并加测试。
- **失败后仍可用**：同一进程接着发送普通小请求，`done success=true`，usage 2,524 / 102，说明进程、模型与数据库未被破坏。
- **单发路径近上限**：8,192 窗口的会话，5,800 字符（约 3,246 prompt tokens）正常回答；6,500 字符也正常回答。单发路径不套用 agent 的字节门禁，由 provider 的真实窗口决定。
- **agent 小窗口**：8,192 窗口的会话发 5,800 字符，首轮守卫在累计 71,994 tokens 时停止（初始系统提示/工具上下文已超小窗口），返回 `CONTEXT_LIMIT`；下一次请求被写入隔离保护拒绝（`AGENT_WRITER_QUARANTINED`），需新会话。这是既有 writer 保护行为，不是崩溃。
- **provider 明确超限**：用本地假 provider 返回 `context_length_exceeded`，应用返回 `INTERNAL_ERROR: LLM 调用失败`（`Error code: 502`），干净结束，没有崩溃；目前没有把 provider 的超长错误映射成 `CONTEXT_LIMIT`（既有 D10 缺口）。

## 3. 关键发现：真正的天花板是“字节门禁”，不是 200k token

`validate_agent_request_budget` 把序列化 payload 的 **UTF-8 字节数**直接和模型窗口（token 数）比较：

```text
payload_bytes + 256 + output_reserve > context_window  -> 拒绝
```

因此 200k 窗口实际允许约 191.5k 字节的 payload：

- 典型英文/JSON 约 4 字节/token → 约 **4.8 万 token**；
- 纯 ASCII 重复文本约 2 字符/token → 约 **9.6 万 token**；
- 中文约 3 字节/字、1 token/字 → 约 **6.4 万 token**。

这就是此前压力测试最大只有 **51,748 tokens** 的原因：先撞到的是字节门禁，而不是 token 守卫（161k）或模型窗口（206k）。该门禁是刻意的保守 fail-closed 设计，能保证不把超窗请求发给 provider，但只用了窗口的约 24%–48%。

## 4. 未验证

- 没有向真实 provider 发送 15–20 万 token 的请求；上面的 200k/262k 结论来自本地守卫与 wire 校验，真实 provider 的 200k 调用未执行。
- 没有验证 provider 对“接近窗口”的响应质量/延迟/计费。
- 没有做 OS 重启、并发多请求、多个会话同时顶满。

## 5. 结论

- 接近和突破上限**不会崩**：要么通过，要么干净地返回 `CONTEXT_LIMIT`；本次修复了预发送预算错误被误报成 `INTERNAL_ERROR` 的问题。
- 但当前实际可用上下文受字节门禁限制，约为模型 token 窗口的 1/4–1/2；要真正“顶满 200k token”，需要把最后一道门禁改成按 token 估算并保留安全系数，再补真实 20 万级 provider 验证。
