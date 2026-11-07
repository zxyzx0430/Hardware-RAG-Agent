# 上下文边界压力测试（2026-11-07）

对象：`w1-baseline` 的上下文预算、累计守卫与预发送校验。包含本地边界探针和真实 DeepSeek v4.1 Flash 长上下文请求。

## 1. 先修掉的天花板

原实现把序列化 payload 的 **UTF-8 字节数**直接当 token 数比较，导致 200K 窗口实际只能装约 4.8–9.6 万 token（之前的压力最大值 51,748 就是撞在这里）。

本轮改动：

- `request_budget.validate_agent_request_budget`、`LLMClient._check_request_budget` 改为本地 tokenizer 估算，并留 10% 安全余量（`WIRE_TOKEN_SAFETY = 1.1`）。
- `ReasoningChatOpenAI._guard_provider_payload` 改为 token 估算，与守卫共用同一阈值。
- `AGENT_CONTEXT_WINDOW_CAP` 从 262,144 放开到 **1,048,576**。
- 前端 `normalizeContextWindow` 不再把窗口截到 256K；设置页和输入栏都提供 64K/128K/256K/512K/1M，选择后通过 PATCH 持久化到会话。
- 预发送预算错误（`AgentRequestBudgetError`）映射为 `CONTEXT_LIMIT`，不再误报 `INTERNAL_ERROR`。

## 2. 边界探针（无 provider）

脚本 `backend/tests/context_stress/run_boundary_stress.py`，结果 `boundary-report.json`。

131,072 / 200,000 / 206,233 / 262,144 / 1,048,576 五种窗口，各压到守卫上限的 50%、90%、99%、99.9%、100%、100.1%、110%、130%：

| 比例 | 结果 |
| --- | --- |
| 50% – 99.9% | 允许通过 |
| 100% – 130% | 触发 `ContextLimitError`，生成 `CONTEXT_LIMIT` 事件 |

- 守卫上限 = `(窗口 - 输出预留 - 256) × 0.8`；1M 窗口为 **835,379**。
- 每轮越界后重置计数器，再放一个小请求，计数恢复正常；没有未捕获异常。
- 最终 wire 校验（200k 模型）：191,552 token 通过，191,743 token 拒绝，分界准确。
- 工具输出填充到 99.9% 再追加一块，正确越过守卫并生成 `CONTEXT_LIMIT`。

## 3. 真实长上下文（DeepSeek v4.1 Flash）

会话窗口通过 `context_window` 设置，模型 `ccgo/deepseek/deepseek-v4.1-flash`：

| 请求 | 估算用户 token | 窗口 | 结果 |
| --- | --- | --- | --- |
| 160,000 字符 | ~80,000 | 206,233 | **成功**，回复 `ok`（约 13s） |
| 80,000 字符（服务端历史） | ~40,000 | 206,233 | **成功**，初始计数正确，无重复 |
| 360,000 字符 | ~180,000 | 1,048,576 | **成功**，回复 `ok`（约 14s） |
| 600,000 字符 | ~300,000 | 1,048,576 | **成功**，回复 `ok`（约 28s） |
| 1,400,000 字符 | ~700,000 | 1,048,576 | **成功**，回复 `ok`（约 57s） |
| 2,200,000 字符 | ~1,100,000 | 1,048,576 | **干净返回 `CONTEXT_LIMIT`**，不可重试，无崩溃 |

- 300K 和 700K 真实 token 的请求都成功完成，已经明显越过 200K。
- 1.1M 请求在本地被拒绝（`模型窗口 1048576 tokens`），进程、会话和后续请求均正常。
- 200K 窗口下用户内容实测约 8–9 万 token；1M 窗口下实测约 70 万 token。剩余预算由系统提示、工具 schema 和输出预留占用，所以会话窗口必须先设为 1M（默认仍是 256K）。

## 4. 真实 HTTP 失败路径

- 500,000 字符的单条消息（旧 200k 窗口）：HTTP 200，SSE `CONTEXT_LIMIT`，之后普通聊天正常（usage 2,524 / 102）。
- 修复前同一路径是 `INTERNAL_ERROR: Agent 调用失败`，已修复并加回归测试。

## 5. 未验证

- 未对真实 provider 做 1M token 的质量/延迟评测；只验证了请求被接受并返回。
- 未测试多轮长上下文、多个会话并发顶满、OS 重启。
- 真实硬件/串口/烧录无关。
## 6. 结论

- 接近和突破上限不会崩：本地守卫按 token 计算，越界返回 `CONTEXT_LIMIT`。
- 真实 DeepSeek v4.1 Flash 已验证 **700K token** 成功、1.1M token 干净拒绝。
- 1M 窗口已在设置页和输入栏暴露；选中后会话持久化为 1,048,576，用户内容实测约 70 万 token。默认会话仍是 256K，需要手动切换。
- 200K 窗口的用户内容上限约 8–9 万 token，差额是系统提示/工具 schema/输出预留；这不是单位错误，而是同一窗口内的固定开销。
