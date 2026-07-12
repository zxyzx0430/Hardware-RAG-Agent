# 05-agent — LangGraph / 工具 / 召回 / 记忆 / 检查点 / 多 Agent 编排

> 版本 1.1 | 最后更新：2026-06-21 | 归属阶段：V1
> 前置条件：V1 后端骨架已搭建，tool_router 已实现，6 个工具已注册（含 5 stub），src/agent/__init__.py 惰性导出就绪
> TODO 文件：docs/todos/05-agent.md
> 关联线程：02-chat（SSE 流式）、03-knowledge（RAG 检索）、04-session（会话持久化）、06-sandbox（沙箱执行）、07-hardware（V2 硬件工具）

---

## 负责范围

| 领域 | 具体内容 |
|------|---------|
| Agent 核心 | LangChain ReAct Agent 编排、意图识别、任务规划与分解 |
| 工具系统 | 注册/调度/参数校验/超时控制、MCP 工具动态注册、5 个 V1 硬件工具真实实现 |
| 记忆系统 | ConversationSummaryBufferMemory 多轮上下文、Agent 检查点持久化 |
| 知识库召回 | search_hardware_kb 工具对接 vector_store RAG 检索 |
| 降级策略 | Agent 不可用时自动降级到纯 RAG + LLM 直连 |
| 可观测性 | Agent 执行日志、X-Request-Id 链路追踪、结构化日志（V1 可选） |

## 不做的事情

- 不直接实现前端 UI（Thread 01/02）
- 不直接管理知识库入库管线（Thread 03）
- 不直接实现串口通信/硬件烧录逻辑（Thread 06/07）
- 不直接做 Docker/CI/CD/可观测性基建（Thread 08）
- 不直接处理用户设置/API Key 加密存储（Thread 04）

## 架构概览

```
用户输入 -> /api/chat
              |
              v
       +-----------------------+
       |   AgentExecutor       |
       |   (LangChain ReAct)   |
       +-----------+-----------+
                   |
            +------+------+
            |             |
            v             v
       +--------+   +-----------+
       | Tools  |   | Memory    |
       +----+---+   +-----------+
            |
            +--> search_hardware_kb  -> src/rag/vector_store
            +--> generate_hardware_code -> LLM + RAG context
            +--> review_hardware_code -> LLM + rules
            +--> compare_hardware_components -> LLM + RAG compare
            +--> diagnose_hardware_problem -> LLM + RAG triage
            +--> code_executor  -> src/sandbox/executor (Docker)
            +--> mcp_X_*  -> src/mcp/manager (dynamic)
```

## 当前完成状态

| 模块 | 状态 | 备注 |
|------|------|------|
| tool_router.py | DONE | register/dispatch/param-schema/timeout/MCP dynamic-reg |
| src/agent/__init__.py | DONE | lazy-export, align with RAG module style |
| built-in stub tools | DONE | audit_pins/wiring/build/upload/search_docs/code_executor |
| ReAct Agent core | TODO | import langchain, build AgentExecutor |
| search_hardware_kb | STUB | connect to vector_store real search |
| generate_hardware_code | TODO | LLM + hardware context code gen |
| review_hardware_code | TODO | LLM review + rule validation |
| compare_hardware_components | TODO | RAG multi-doc compare |
| diagnose_hardware_problem | TODO | multi-turn triage guide |
| ConversationSummaryBufferMemory | TODO | multi-turn context memory |
| Agent checkpoint persistence | TODO | state snapshot & restore |
| fallback strategy | TODO | Agent -> raw RAG direct |
| /api/chat integration | TODO | from direct to Agent routing |
| Agent tests | NONE | tests/ has no agent test files yet |

## 接口契约

涉及 docs/api-contract.md：需要新增 Agent 调度相关章节

| 接口 | Method | 状态 | 说明 |
|------|--------|------|------|
| /api/chat | POST | implemented | 当前直连 LLM+RAG，需改为 Agent 路由 |
| /api/chat (Agent 模式) | POST | draft | Agent 模式下走 ReAct 调度 |
| /api/chat (降级模式) | POST | draft | Agent 不可用时降级纯 RAG |

## V1 里程碑

- [ ] LangChain ReAct Agent 核心搭建（引入 langchain + AgentExecutor）
- [ ] 5 个硬件工具的 RAG 增强真实实现（非 stub）
- [ ] ConversationSummaryBufferMemory 集成
- [ ] Agent 降级策略（Agent 不可用时 -> 纯 RAG）
- [ ] Agent 检查点持久化
- [ ] 与 /api/chat 路由集成

## V2 前瞻

- compile_and_flash（USB 烧录）-- Thread 07 协作
- read_serial（串口读取）-- Thread 07 协作
- ota_flash（WiFi OTA 烧录）-- Thread 07 协作
- hardware_guardrails（输出安全校验）-- Thread 06 协作
- 外设元数据注入（config.yaml -> prompt）

## 关键文件

| 文件/目录 | 说明 | 状态 |
|-----------|------|------|
| backend/src/agent/tool_router.py | 工具注册/调度/参数校验/超时/MCP 动态注册 | DONE |
| backend/src/agent/__init__.py | 模块惰性导出入口 | DONE |
| backend/app/api/routes.py | /api/chat 路由（当前直连，待改 Agent） | TODO |
| backend/src/rag/vector_store.py | 知识库检索（search_hardware_kb 对接目标） | DONE |
| backend/src/sandbox/executor.py | Docker 沙箱执行（code_executor 后端） | SKELETON |
| backend/src/mcp/manager.py | MCP Server 管理（工具动态注册来源） | SKELETON |
| backend/tests/ | Agent 测试文件（暂无） | NONE |
| docs/todos/05-agent.md | TODO 任务清单 | EXISTS |
| docs/api-contract.md | 接口契约（需补充 Agent 章节） | NEEDS_UPDATE |
| docs/pitfalls.md | 踩坑记录（所有线程共用） | ACTIVE |

## 决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-06-21 | Agent 基于现有 tool_router 调度而非另起炉灶 | tool_router 已实现健壮的 register/dispatch/schema/timeout 系统 |
| 2026-06-21 | V1 使用 LangChain ReAct（非 LangGraph） | V1 计划已决定砍掉 LangGraph，ReAct 足够覆盖 V1 需求 |
| 2026-06-21 | /api/chat 改 Agent 模式时保留降级通道 | 用户 API Key 受限或无 Agent 可用时降级纯 RAG 不中断服务 |

## 踩坑记录

本项目所有踩坑统一记录在 docs/pitfalls.md，不按线程分散。修复 bug 后直接追加到 docs/pitfalls.md。

---

## 下次开工先看

1. **读 PLUR**：执行 `plur inject` 加载长期记忆
2. **读 TODO**：打开 docs/todos/05-agent.md（只看前 2 项）
3. **读踩坑**：翻 docs/pitfalls.md（跳过已看过的）
4. **查状态**：看上方「当前完成状态」表，确认前置条件就绪
5. **聚焦**：只做前 2 项 TODO，不提前做后续内容

---

## Changelog

| 日期 | 版本 | 修改内容 |
|------|------|---------|
| 2026-06-21 | 1.1 | 补充完成状态表、接口契约、关键文件列表、决策记录、架构图、下次开工先看；对齐 thread-map 职责描述；引用 docs/todos/05-agent.md |
| 2026-06-21 | 1.0 | 初始创建 |
