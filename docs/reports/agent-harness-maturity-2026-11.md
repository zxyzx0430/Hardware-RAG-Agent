# Agent Harness 成熟度对标与升级路线

> 调研日期：2026-11-04
> 代码基线：本地 `E:\Desktop\agent`，master `eab62d2` + 7,428 行新增 / 536 行删除的未提交改动（含上下文压缩、工具观察、writer 生命周期等新模块）
> 对标对象：Claude Code、OpenAI Codex、OpenCode、Cursor、Cline、Aider 这类 Agent Harness；RAGFlow/Dify 仅作为知识库链路参考
> 证据边界：本轮只读源码与官方公开资料，未运行完整测试、真实模型、浏览器或硬件；下文“已实现”只表示代码存在，不等于已验收
>
> 配套文档：[Agent 功能全景与项目对照](agent-feature-atlas-2026-11.md) —— 24 个能力域、70 项功能速查，回答“常见 Agent 有哪些功能、你有哪些/缺哪些”。
> 开发排期：[Agent 能力开发优先级与实施方案](agent-development-plan-2026-11.md) —— 上下文/恢复/编排/Memory/Skills/Subagent 的具体表结构、接口和分阶段任务。

---

## 0. 一句话结论

这个项目在**工具系统、RAG 链路、权限/审计、工程文档纪律**上已经超过大多数个人 Agent 项目，部分设计（结构化摘要、工具观察归档、来源引用）接近成熟 harness 的做法。

离“成熟项目”的差距不在功能数量，而在四件事：

1. **长期记忆**仍是单一手写文本，没有自动提取、分层、检索和治理；
2. **上下文压缩**设计很复杂，但预算单位有 bug、压缩结果不跨轮持久化、运行中漏计工具结果，生产闭环没闭合；
3. **可恢复性与可观测性**不足：流式断线不能续、HITL 快照重启即丢、没有 trace/token/成本视图、评测覆盖不完整；
4. **交付纪律**：7,428 行新增 / 536 行删除的本地改动未提交、未统一测试，主树与候选树分叉——这是成熟度最大的风险，不是代码能力。

建议顺序：**先收口，再补记忆，再做恢复/可观测/评测，最后做沙箱与高级能力。**

---

## 1. 成熟度记分卡

评级：1 = Demo，2 = 原型，3 = 可用，4 = 产品级，5 = 参考级。

| 能力域 | 当前 | 目标 | 判断 |
| --- | ---: | ---: | --- |
| Agent 循环 / 工具调用 | 4 | 4.5 | LangGraph `create_agent`，并行 ToolNode、超时、审计、HITL 都在；缺循环检测和失败 replan |
| 计划 / Todo | 3.5 | 4.5 | 已有 `todo_write` + 右栏进度；缺 plan mode（先出计划、用户确认再执行） |
| 上下文压缩 | 3 | 4.5 | 设计强（结构化摘要、fail-closed、观察引用），但预算单位、持久化、运行中计数有实质缺陷 |
| 长期记忆 | 1.5 | 4 | 只有 4,000 字手动记忆；FTS5 索引未接线，语义存储是进程内 `InMemoryStore` |
| 会话 / 恢复 | 2.5 | 4.5 | 消息持久化较扎实；普通请求每轮重置 checkpoint，重启后 HITL/未完成轮次不可恢复 |
| 工具系统 | 4.5 | 4.5 | ToolSpec/Router/envelope/审计/观察归档完整；输出无上限、同步写库会阻塞事件循环 |
| 权限 / HITL | 4 | 4.5 | 分级、逐调用确认、一次性授权、审计齐全；`path_guard` 只剩黑名单，无 OS 沙箱 |
| Skills / MCP | 4 | 4.5 | 标准 Skill 包、固定 SHA、请求快照、stdio MCP；MCP 配置重启即丢、无远程/OAuth |
| 子代理 / 并行 | 1 | 3.5 | 无 subagent、无 handoff、无 worktree 隔离 |
| RAG 检索 | 4 | 4.5 | 三种分块、BM25+向量、RRF、rerank、父子块、来源注册器；单 KB 串行、doc_filter 后置、无 contextual retrieval |
| 引用可信度 | 3 | 4.5 | `[srcN]` 有真实来源卡片；但后端不校验模型是否编造 srcN，页码字段有别名混用 |
| 可观测性 | 2 | 4 | 自研 timing + SQLite 审计；无 OpenTelemetry/Langfuse、无 span 树、无 token/成本面板 |
| 评测 | 2 | 4 | 35 题黄金集仅 7 题四指标完整；无 Agent 轨迹评测、无 Memory 评测、无 CI 门禁 |
| 前端 / UX | 3.5 | 4.5 | 会话、来源、HITL、工作台都可用；无流式续传、无消息虚拟化、无 E2E |
| 开源工程 | 2 | 4.5 | README/截图/文档很强；无 CI、无 release/changelog、无 issue 模板、无贡献指南 |

**综合：约 3.1 / 5，处于“可用、工程化中期”，尚未达到成熟开源 Agent 项目。**

---

## 2. 你已经做对的，不要推翻

### 2.1 工具与执行边界

- `ToolSpec` 统一声明 schema、风险等级、超时、重试、MCP 信息（`backend/src/agent/core/toolkit/tool_spec.py:78-90`）。
- `ToolRouter.dispatch` 做参数校验、超时、错误/成功 envelope、审计、观察归档（`tool_router.py:129-216, 261-338, 402-442`）。
- HITL 逐调用确认、一次性授权、拒绝后继续、审计脱敏（`mcp_authorization.py:155-179`、`audit_recorder.py:81-130`）。
- 这套东西比多数开源 Agent 的“裸 tool loop”成熟，是项目最大的工程资产。

### 2.2 RAG 链路

- 三种分块策略真实存在：hybrid、agent、multimodal；2 阶段 / 2 轮投票 / 4 级降级在 `multimodal_chunker.py:318-448,450-527,1114-1176`。
- 父子块、RRF(k=60)、BM25-only 惩罚、bge-reranker、来源注册器都已实现。
- 这已经超过“能跑”的 RAG Demo，接近 RAGFlow/Dify 的简化版。

### 2.3 上下文压缩的防护思路

- 失败不再 snipping，而是 fail-closed 保留原文（`autocompact.py:314-329`）。
- 结构化摘要包含 user_goal / confirmed_facts / decisions / open_tasks / uncertainties / evidence_locators（`autocompact.py:330-346`）。
- 工具结果归档为 opaque ref，并可用 `read_tool_observation` 回读（`app/api/tool_observations.py:84-174`、`read_tool_observation.py:24-71`）。
- 这些方向与 Claude Code 的“工具结果外置 + 可回读”一致，不要因为复杂就回退。

### 2.4 工程文档纪律

- AGENTS.md、能力基线、测试边界、API 契约、验收记录都很完整，明确区分“实现 / 测试 / 验收”。
- 这是很多所谓“成熟项目”都没有的。保持。

---

## 3. 明显落后的能力（按影响排序）

### 3.1 长期记忆：从“一个字符串”到“分层记忆系统”

**现状**

- 只有 `Settings.longTermMemory` 一个字符串，最多 4,000 字，每轮全量注入（`backend/app/api/crud.py:389-396,432-439`、`prompts.py:174-186`）。
- FTS5 会话检索 `init_session_db()` 与 `index_session_message()` 全仓**没有调用者**，`search_history` 实际返回空（`session_search.py:24,42-45,58-62`）；现有库只有 1 条旧数据。
- 语义存储是 `InMemoryStore`，进程重启即丢（`store_adapter.py:49,67`）。
- 删除会话只删 ORM，不删 FTS/Store（`crud.py:252-257`）。
- 无条目级 scope、类型、来源、置信度、TTL、冲突处理、逐条删除。

**成熟做法**

- Claude Code：CLAUDE.md（组织/用户/项目/本地分层）+ auto memory + 会话 resume；Memory tool 用文件式 `/memories`，支持 view/create/str_replace/insert/delete/rename。
- Codex：AGENTS.md + 会话历史 + resume。
- OpenCode：AGENTS.md/instructions + session。
- Mem0：抽取 → 查重 → ADD/UPDATE/DELETE/NOOP → 向量 + 实体图；SQL 为真源。
- Letta：core memory（常驻、带 label/limit/read_only）+ archival memory（向量、Agent 主动写）+ recall memory；sleep-time 后台重整。
- LangGraph Store / LangMem：namespace + semantic/episodic/procedural 三层。

**差距**

| 层 | 成熟方案 | 本项目 |
| --- | --- | --- |
| 写入 | 自动抽取、用户纠正即时写、后台合并 | 用户手动全量保存 |
| 组织 | scope/type/来源/置信度/过期 | 单一全局字符串 |
| 检索 | 按 query 混合检索 + rerank + 预算 | 固定全量注入 4,000 字 |
| 治理 | 去重、冲突、TTL、逐条删除、审计 | 只能整块清空 |

**建议：Memory v1（1–2 周）**

1. 新表 `memory_entries(id, scope, kind, content, structured_json, source, confidence, created_at, updated_at, last_used_at, expires_at, supersedes_id)`。
2. scope：`global` / `project:<id>` / `session:<id>`；kind：`preference` / `fact` / `constraint` / `decision` / `procedure`。
3. 写入：
   - 用户显式“记住……”→ 立即写；
   - 每轮结束用便宜模型抽取候选，只保留“持久、可复用、非敏感”的事实；
   - 写入前向量查重（cos > 0.9）+ LLM 判 ADD/UPDATE/DELETE/NOOP；
   - 敏感内容（密钥、身份证、地址）拒绝写入。
4. 读取：每轮按 query + scope 检索 top-k，预算 800–1,200 token，作为 `<memory>` 参考块注入，低于系统规则和本轮请求。
5. 治理：记忆面板（列表/搜索/编辑/删除/停用/来源链接）；删除传播到 FTS/向量/摘要；TTL 与“最近使用”衰减。
6. 评测：20 个跨会话场景（偏好、板卡、约束、冲突、过期、删除），测召回率/精确率/token 成本。

**硬件差异化**：把“板卡 profile”做成结构化记忆——MCU 型号、封装、供电、引脚映射、外设、工具链、已否决方案。比通用 Agent 的自由文本记忆更有价值，也是这个项目真正的护城河。

### 3.2 上下文压缩：设计 4 分，生产闭环 2 分

**现状**

- 预发送压缩在 `chat_routes.py:855`；运行中压缩在 `sse_adapter.py:982`，发 `context_compressing` 后 drain → 摘要 → 条件替换 checkpoint → `input=None` 续跑（`checkpoint_publish.py:366`）。
- 保留最近 10 条（`autocompact.py:37`），按 tool_call/result 对切分，摘要严格 JSON + source_refs，失败重试 3 次后 fail-closed。
- 但：
  1. **预算单位错误（P0）**：`reasoning_chat.py:121-125` 用 `len(serialized)` 的**字节数**与 `budget.token_limit`（token）比较，且 `ensure_ascii=True`，中文每字约 6 字节。中文场景会在实际 token 远未超限时提前 4–10 倍触发 `CONTEXT_LIMIT`。
  2. **运行中漏计工具结果（P0）**：`accumulate_tokens` 只处理 str/list，dict 工具结果计 0（`context_guard.py:379-390`）。
  3. **microcompact 是死代码**：`compact_old_tool_results` 全仓无调用（`microcompact.py:26`）。
  4. **压缩结果不跨轮**：预发送压缩只改本次请求，前端历史/会话历史不变；下一轮重置 checkpoint 后重新压缩（`chat_routes.py:971-977`）。
  5. **无压缩后事件**：只有压缩前 banner，前端还丢弃了 reason/threshold/context_window（`useChatStore.ts:2250`）。
  6. **无手动 `/compact`**，无上下文余量 UI，无摘要留存/审计。
  7. 早期历史超过摘要模型窗口时直接失败，没有分段/层级摘要（`autocompact.py:141-153`）。
  8. 摘要 500 字上限，关键数值约束没有独立字段，可能被摘要掉（`autocompact.py:51-68`）。

**成熟做法**

- Claude Code：auto-compact 阈值触发；压缩后从磁盘重注入 CLAUDE.md/无路径规则/auto memory；回读最近改动的文件；`/compact` 手动入口；连续压缩失败即停。
- Anthropic Context Editing：`clear_tool_uses_20250919`（keep 默认 3 组、trigger 默认 100k、exclude_tools、clear_at_least）、`clear_thinking_20251015`；压缩以 append-only compaction block 继续，保留 prefix cache。
- OpenAI：`context_management.compact_threshold`、`/responses/compact`；压缩产物原样回传。
- LangGraph：`trim_messages` + summarization node；保留最近消息 + 摘要。

**建议**

- P0：修单位与 dict 计数；压缩结果写回会话（摘要 + 覆盖范围 + epoch），下一轮直接复用；新增 `context_compacted` 事件（before/after token、条数、摘要 ref）。
- P1：分层摘要（超窗口分段）；pinned invariants 每轮重注入（系统规则、板卡 profile、用户硬约束、已否决方案）；接入或删除 microcompact；手动 `/compact`；上下文余量环。
- P2：thinking 块清理、工具输入清理、按工具类型选择性保留；压缩质量评测（10 段长对话植入约束，测保留率）。

### 3.3 会话恢复与流式恢复

**现状**

- 普通 Agent 请求每轮先重置 thread checkpoint（`chat_routes.py:971-977`），只有 HITL resume 复用 checkpoint；进程重启后 pending 确认快照（30 分钟 TTL、内存）丢失（`chat_routes.py:98-101`）。
- 前端 SSE 无 Last-Event-ID、无自动重连（`frontend/src/api/client.ts:255-385`）；刷新后背景流/pendingConfirm 丢失，只能重发。
- 会话列表默认只取 50 条、无无限滚动；单会话消息全量拉取，无分页/虚拟化（`crud.py:102-108,275-281`）。
- 分支会话按钮/树未挂载；retry 直接删旧答重发，无版本对比。

**成熟做法**

- Claude Code `--continue`/`--resume`，会话落盘、可恢复。
- Codex sessions + resume；OpenCode session 持久化。
- 通用做法：SSE 带 event id + 服务端事件缓冲，客户端断线带 Last-Event-ID 重连；或提供 `GET /runs/{id}/events?after=`。

**建议**

- P0：HITL pending 快照与 MCP 配置持久化到 SQLite；会话消息分页。
- P1：SSE 事件 ID + 断线重连；后台流状态持久化（run id、最后事件序号）；刷新后 resume。
- P2：分支会话/消息版本对比，retry 保留旧版本。

### 3.4 可观测性与评测

**现状**

- 自研 JSON timing（`telemetry.py:74-363`），审计落 SQLite（`audit_recorder.py:134-168`），但没有 request_id 贯穿审计/SSE，无 token 用量、无 span 树、无成本面板。
- 无 OpenTelemetry、Langfuse、LangSmith；仅保留 LangChain callback 扩展点。
- RAG：35 题仅 7 题四指标完整，加权 97.02 是选择偏差子集；来源链路 30/35=85.7%；无数值/型号确定性校验。
- 无 Agent 轨迹评测、无 Memory 评测、无 CI 门禁。

**成熟做法**

- OpenTelemetry GenAI semantic conventions：agent turn / LLM call / tool execute / retrieval span，属性含 model、tokens、cost、tool name、status。
- Langfuse 自托管 + dataset/experiment + CI RegressionError；LangSmith dataset→experiment。
- RAGAS/DeepEval/TruLens：faithfulness、context precision/recall、answer relevancy；LLM-judge 需校准偏差。
- Agent：τ-bench 校验最终状态、pass^k 测稳定性；Agent-as-a-Judge 评中间轨迹。

**建议**

- P0：给每个请求生成 `trace_id`，贯穿 SSE/审计/telemetry；记录每轮 token/cost；跑完整 35 题四指标并冻结基线。
- P1：接 OTel GenAI + Langfuse（本地自托管）；补 60–100 题 RAG 集、20 个 Agent 场景、20 个 Memory 场景；CI 夜间跑，回归超阈值失败。
- P2：轨迹评测（工具选择/顺序/参数/最终状态）；成本预算与模型路由。

### 3.5 子代理与并行

**现状**

- 无 subagent、handoff、worktree 隔离；同一批 tool_calls 由 ToolNode 并行。
- `recursion_limit=500`，无循环检测（`prompts.py:252`；`tool_router.py:17-18` 注释称已迁移但无实现）。
- 工具失败只对超时重试，无退避、无 replan。

**成熟做法**

- Claude Code：Task/Agent 子代理 + 独立上下文 + git worktree；Plan 子代理先探索再执行。
- Codex：cloud tasks/worktrees；OpenCode subagents。
- 共同模式：大检索下沉子上下文，只回摘要 + 引用；主上下文不承载原文。

**建议**

- P1：加一个 `research_subagent(query, doc_ids)`，在独立上下文中读大量手册 chunk，返回结构化摘要 + `[srcN]`；主 Agent 只拿结果。对“整本手册找参数”收益最大。
- P1：循环检测（tool name + 参数 hash，重复 N 次终止/提示）；LLM 调用指数退避；工具失败后允许一次 replan。
- P2：多子代理并行 + worktree 隔离（主要服务代码工作区，不是 RAG）。

### 3.6 安全执行

**现状**

- `run_command` 直接起 `powershell.exe -Command` / `bash -c`，只校验 cwd 与可解析的写目标（`run_command.py:225,316-331`）。
- `path_guard` 的允许目录限制已被移除，只保留黑名单（`path_guard.py:225-234`）。
- 无 OS 沙箱；MCP 是真实本机进程；`PermissionMatcher`/plugins 是未接线的死代码。

**成熟做法**

- Claude Code：权限模式 + allow/deny 规则 + hooks + OS 沙箱（macOS Seatbelt / Linux bubblewrap）。
- Codex：approval modes + Seatbelt/Landlock/seccomp。
- 区分“审批”（是否允许）与“沙箱”（实际能做什么）。

**建议**

- 如果只自己用：P2，先把审批/审计/路径边界做扎实。
- 如果要开源分发：P1，Windows Job Object/受限令牌或 WSL 容器；命令与网络 egress 白名单；MCP 子进程同样受限。
- 密钥从 `.env` 迁移到 Windows Credential Manager；日志脱敏。

### 3.7 RAG 与引用（次要但影响可信度）

**现状**

- 单 KB 内向量与 BM25 串行（`kb_manager.py:1113,1121`）；跨 KB 才并行。
- `doc_filter` 在融合后过滤，候选只扩 3 倍，不缩小扫描（`kb_manager.py:333,1109`）。
- 文档资格过滤在 top-k 后，可能滤空（未提交版）。
- 后端不校验模型是否编造 `[srcN]`，只靠提示词；`page=chunk_index` 与 `page_start` 混用（`search_docs.py:357`）。
- 父块按每个小片重复展开，同段可多次占 src（`search_docs.py:231-238`）。
- 无 contextual retrieval；hybrid 不合并跨页表；图片/时序图仅 multimodal。

**建议**

- P0：引用校验（未知 srcN 剥离/重试）；统一页码字段；doc_filter 前移到 Chroma where/BM25 并扩池；区分“故障”与“空结果”。
- P1：chunk 头拼文档名+章节+型号+页码再入索引（Anthropic Contextual Retrieval 的轻量版）；单 KB 向量+BM25 真并行；逐句引用支持校验；补全 35 题标准评测。
- P2：数值/型号/寄存器确定性校验；跨页表合并；图片逐页描述 + 并发；ColPali 视觉检索按指标决定。

### 3.8 前端体验

**现状**

- 多 Zustand store，SSE 直接写 store，localStorage 分片 + 后端覆盖；HITL、来源卡片、Todo、工作台联动都可用。
- 但：无流式续传/重连；聊天无虚拟化（`ChatArea.tsx:386` 直接 map）；消息默认 50 条无分页；压缩 banner 丢弃阈值/前后信息；无文档级 `doc_filter` UI；工作台全局单例不按会话隔离；ErrorBoundary 出错会清空所有本地数据且无确认（`ErrorBoundary.tsx:32-33`）；无 E2E。
- 31 个 Vitest 文件，覆盖持久化/HITL/context-limit/布局，但没有 Playwright/Cypress。

**建议**

- P0：流式重连/续跑；会话分页；ErrorBoundary 先确认/导出再清理。
- P1：消息虚拟化；压缩详情（阈值、before/after、摘要）；知识库失败重试；doc_filter 选择器；工作台按会话隔离。
- P2：E2E（Playwright）覆盖首发、刷新、HITL、断网、压缩、知识库上传；i18n 收口；a11y。

### 3.9 开源工程

**现状**

- README 很完整（痛点、亮点、截图、快速开始、配置），有 LICENSE、docs、测试。
- 但：无 `.github/workflows`（无 CI）、无 CHANGELOG、无 CONTRIBUTING/SECURITY、无 issue/PR 模板、无 release/tag、无 repo description/topics、无 demo 视频/GIF。
- GitHub 0 star、0 fork；不是代码问题，是分发问题。

**建议**

- P0：GitHub Actions（Windows + Ubuntu）：backend pytest、frontend vitest/lint/build；badge。
- P1：CHANGELOG、CONTRIBUTING、SECURITY、issue 模板、release 流程；repo description/topics；一张 30 秒 demo GIF。
- P2：Docker/一键脚本（注意本项目只监听 localhost）；示例知识库与黄金集公开版。

### 3.10 工程一致性：规则与代码脱节

`CLAUDE.md` 要求“单文件 ≤300 行、单函数 ≤10 行、参数 ≤3”，但当前核心文件普遍超标：

| 文件 | 行数 |
| --- | ---: |
| `backend/app/api/chat_routes.py` | 2,130 |
| `frontend/src/stores/useChatStore.ts` | 3,000+ |
| `backend/src/agent/sse_adapter.py` | 1,457 |
| `backend/src/agent/agent_factory.py` | 1,382 |
| `backend/src/agent/compact/autocompact.py` | 1,225 |
| `backend/src/agent/compact/writer_lifecycle.py` | 1,145 |

同时存在“注释说已实现、实际没有”的死代码/空实现：

- `microcompact.py:26` 全仓无调用；
- `tool_router.py:17-18` 注释称循环检测已迁移，实际没有实现；
- `PermissionMatcher`、`PluginManager/RedactPlugin` 已实现但从未导入使用（`permission_matcher.py:63`、`plugins/manager.py:23`）。

**建议**

- 要么把规则改成可执行的（ruff `max-complexity`、`max-args`、eslint `max-lines`），要么明确“存量文件豁免、新文件遵守”，不要让文档规则和现实长期背离。
- 在 CI 里加 `ruff`/`eslint`/`tsc`，把“防屎山”从口号变成门禁。
- 每个死代码要么接线，要么删除；不要保留“看起来有、实际没有”的能力。

---

## 4. P0 缺陷清单（带文件行号）

| # | 问题 | 位置 | 修法 | 估时 |
| --- | --- | --- | --- | --- |
| 1 | 字节数当 token 数，中文提前 4–10 倍触发 CONTEXT_LIMIT | `backend/src/agent/reasoning_chat.py:121-125` | 对序列化文本做 token 估算，或单独设字节预算；最好接 tiktoken/provider count_tokens | 0.5d |
| 2 | 运行中 dict 工具结果计 0 token | `backend/src/agent/context_guard.py:379-390` | JSON 序列化后估算；补测试 | 0.5d |
| 3 | FTS5 会话索引从未初始化/写入 | `backend/src/agent/session_search.py:24,42-45,58-62` | 启动 `init_session_db`；消息落库后 `index_session_message`；回填历史 | 1d |
| 4 | 删除会话不删 FTS/Store，隐私泄漏 | `backend/app/api/crud.py:252-257` | 删除/截断同步清理；补测试 | 0.5d |
| 5 | 引用可被模型编造，后端不校验 | `backend/src/agent/tools/groups/retrieval/search_docs.py`、`prompts.py:140-159` | 校验 srcN 是否在本次来源；未知则剥离/重试/标错 | 1-2d |
| 6 | 页码字段混用（`page=chunk_index`） | `search_docs.py:357` | 统一 page_start/page_end，`page` 仅兼容 | 0.5d |
| 7 | 压缩结果不跨轮持久化，每轮重复压缩 | `chat_routes.py:855-891`、`chat_routes.py:971-977` | 摘要/覆盖范围/epoch 写回会话；下一轮复用 | 2d |
| 8 | microcompact 死代码 | `backend/src/agent/compact/microcompact.py:26` | 接线或删除 | 1d |
| 9 | 无循环检测 / 无退避重试 | `tool_router.py:17-18`、`tool_spec.py:37-38` | 工具签名 hash + 重复计数；LLM/工具指数退避 | 2-3d |
| 10 | 同步 SQLite 审计阻塞事件循环 | `audit_recorder.py:134-168`、`tool_router.py:183,402` | `asyncio.to_thread` 或异步队列 | 1-2d |
| 11 | 7,428 行未提交、未统一测试 | 工作区 | 建分支、提交、跑全量、合并；CI 门禁 | 2-3d |
| 12 | PlatformIO 构建期任意 Python 执行（`extra_scripts`/`lib_deps`） | `pio_runner.py:184-210` | 构建/烧录放进沙箱；选项白名单；工作区只挂临时目录 | 2-4w |
| 13 | `binary_path` 只用 `normpath` 前缀校验，symlink/junction 可绕过 | `pio_runner.py:163-171` | 改 `realpath` + 拒绝符号链接 + 只允许本会话产物 | 0.5d |

---

## 5. 升级路线

### P0：收口（1–2 周）

目标：把“已有的”变成“可信的”。

1. 建 `feature/agent-harness-hardening` 分支，提交本地 7,428 行改动；保留主树可回滚。
2. 修 4 个 P0 正确性 bug：token 单位、dict 计数、FTS 索引接线、引用校验。
3. 压缩结果持久化 + `context_compacted` 事件；前端显示 before/after。
4. 会话分页 + HITL/MCP 快照持久化。
5. 加 GitHub Actions：backend pytest、frontend vitest/lint/build。
6. 跑完整 35 题四指标，冻结第一份可信基线。

### P1：对齐成熟线（3–6 周）

目标：用户在 Claude Code/Codex/OpenCode 里习以为常的能力，这里也有。

1. **Memory v1**：`memory_entries` + 自动抽取 + 去重/冲突 + 混合检索 + 记忆面板 + 20 场景评测。
2. **Context v2**：分层摘要、pinned invariants 重注入、手动 `/compact`、上下文余量环、microcompact 接线。
3. **Resume**：SSE Last-Event-ID + 断线重连；运行状态持久化；刷新后续跑。
4. **Observability**：trace_id 贯穿 + OTel GenAI + Langfuse 自托管 + token/成本面板。
5. **Agent**：循环检测、退避重试、失败 replan、`research_subagent`。
6. **RAG**：contextual retrieval、单 KB 并行召回、doc_filter 前移、逐句引用校验。
7. **Frontend**：消息虚拟化、压缩详情、KB 失败重试、E2E。

### P2：差异化（2–3 个月）

目标：做通用 Agent 不会做的事。

1. **硬件记忆模型**：板卡 profile（MCU/封装/供电/引脚/外设/工具链/已否决方案）结构化、版本化、可检索。
2. **硬件约束校验**：电压/电流/strapping/引脚复用/封装兼容的确定性规则 + RAG 证据。
3. **多代理/工作区**：代码修改走 worktree + diff/review/undo；大手册解析走子代理。
4. **安全**：OS 沙箱、MCP 远程/OAuth、密钥保险箱。
5. **发布**：CHANGELOG、release、示例库、demo、社区模板。

---

## 6. 不建议做的事

1. **不要继续扩大自研 writer lifecycle / checkpoint publication 的复杂度**。它解决的是真实问题，但已经明显偏离 LangGraph 原生 checkpoint 语义；在测试和文档补齐前继续加功能，会变成不可维护的“第二套运行时”。先把它收敛成“压缩发布适配器”，而不是通用分布式写入框架。
2. **不要直接引入 Mem0/Letta 整套依赖**。它们的价值是设计模式（抽取、分层、命名空间、治理），不是库本身。你的记忆规模（单机、单用户）用一个 SQLite 表 + 向量索引就够。
3. **不要在没有评测基线前做多代理、ColPali、知识图谱**。这些是“看起来成熟”的功能，但没有指标就无法证明收益。
4. **不要为了对标 Claude Code 去做 OS 沙箱，如果你是唯一用户**。先修正确性、记忆、恢复；沙箱在开源分发前做。
5. **不要把所有 RAG 框架的能力都抄一遍**。你的差异化是“硬件手册 + 工作台 + 来源可信”，不是“又一个 RAGFlow”。

---

## 7. 验收标准（建议写进 docs/testing.md）

### 上下文

- 100 轮长对话 + 10 条植入硬约束（电压、引脚、型号、已否决方案），压缩后约束保留率 100%。
- 中文 30K 字符请求不再误触发 CONTEXT_LIMIT；实际 token 与估算误差 < 15%。
- 压缩失败时原文完整保留，SSE 返回 `CONTEXT_LIMIT`，不降级。
- 同一会话连续 3 轮，只压缩 1 次（结果复用）。

### 记忆

- 跨会话 20 场景：召回率 ≥ 80%，精确率 ≥ 80%，无关记忆注入 ≤ 1 条/轮。
- 用户说“记住……”后，新会话首轮即可用；删除后所有检索路径不再命中。
- 记忆注入 token ≤ 1,200/轮；面板可见来源、可编辑、可删除。

### RAG / 引用

- 60–100 题黄金集：faithfulness ≥ 0.9，context precision ≥ 0.8，answer relevancy ≥ 0.9。
- 每个 `[srcN]` 都能在本次 sources 中找到，页码与 chunk 元数据一致；未知引用为 0。
- 空库/无相关资料时明确拒答，不编造。

### Agent

- 20 个脚本化任务：工具成功率 ≥ 90%，循环率 < 5%，HITL 拒绝后 100% 不执行。
- 每个请求一条 trace，含模型、工具、检索、压缩、token、成本。

### 工程

- CI 在 Windows + Ubuntu 通过；主干永远绿色。
- 发布有 tag + CHANGELOG；新环境按 README 一次启动成功。

---

## 8. 下一步建议

如果只做一件事：**把本地 7,428 行改动提交到分支并跑全量测试**。这是所有后续工作的前提，也是“成熟”与“个人实验”的分水岭。

如果做三件事：

1. 修 token 单位 bug + dict 计数（半天到一天，直接解决中文长对话误报）。
2. 接通 FTS5 会话索引 + 删除传播（一天，让 `search_history` 真正可用）。
3. 引用校验 + 页码统一（一到两天，直接提升“回答可信”这个核心卖点）。

这三件做完，项目在“可信度”上会立刻上一个台阶；然后再做 Memory v1 和压缩持久化。

---

## 附：与 Claude Code / Codex / OpenCode 的逐域对照

> 依据：各家官方文档与源码（URL 见文末），调研日期 2026-11-04。产品迭代快，参数以官方当前版本为准。

### A.1 逐域对照表

| 能力域 | Claude Code | OpenAI Codex | OpenCode | 本项目成熟线 |
| --- | --- | --- | --- | --- |
| **规划** | plan 是权限模式，`EnterPlanMode/ExitPlanMode` 进出；任务清单 `TaskCreate/Get/Update/List` | `update_plan` checklist；`/plan` 先访谈再产出可验收 goal；Plan mode 禁止调用 update_plan | primary `plan`/`build`，Tab 切换；plan 只能写 `.opencode/plans/`；步数超限强制总结 | **计划实体 + 审批门 + 压缩后按 id 重注入**；现有 `todo_write` 只是执行清单，不是 plan mode |
| **压缩** | 接近窗口自动摘要；`/compact [focus]`；`/autocompact` 可配；压缩后重注入 CLAUDE.md、auto memory、plan、skills；连续失败停止 | `model_auto_compact_token_limit`、`model_post_turn_compact_threshold_percent`、`compact_prompt`、`model_auto_compact_token_limit_scope` | `compaction.auto/prune/reserved`；隐藏 compaction agent；插件钩子可改摘要提示 | **工具结果外置 + 分级压缩 + 阈值可配 + 手动 /compact + 失败熔断 + 压缩后重注入不变量** |
| **记忆** | CLAUDE.md 四级（managed/user/project/local）+ AGENTS.md 兼容 + `.claude/rules` 按 paths 懒加载；auto memory 每会话加载 MEMORY.md 前 200 行/25KB | AGENTS.md（`/init`）+ `trust_level`；跨会话 memories | AGENTS.md + `instructions` glob | **AGENTS.md/项目规则 + 可审查的 memory 条目 + 检索注入**；不做黑箱全自动 |
| **会话/恢复** | JSONL 存 `~/.claude/projects/<project>/<id>.jsonl`；`--continue/--resume`、`/branch`、fork；30 天清理；checkpoint 保留最近 100 个，可只回滚代码/对话 | `codex resume`；`history.jsonl` + sqlite；线程 60s 卸载 | `--continue/--session/--fork`；session list/delete/export/import；SQLite | **checkpointer + fork + 命名 + TTL + “恢复是否重放未完成工具调用”的明确策略** |
| **权限/审批** | 6 模式（default/acceptEdits/plan/auto/dontAsk/bypassPermissions）；auto 用分类器；deny 规则任何模式生效；allow 不能预批 `.git/.claude` 等保护路径；审批与 Bash 沙箱分离 | `sandbox_mode`（read-only/workspace-write/danger-full-access）× `approval_policy`（on-request/never/granular）；网络默认关；Seatbelt/bwrap+seccomp/Windows MXC | allow/ask/deny，最后匹配胜；`doom_loop`/`external_directory` 默认 ask；`.env` ask；agent 级覆盖 | **deny 恒优先 + 保护路径 + 模式化审批 + 决策写审计 + 独立沙箱** |
| **子代理** | `.claude/agents/*.md`，可配 tools/model/permissionMode/maxTurns/`isolation: worktree`；默认后台；并发 20、嵌套 3 层 | 云端每任务独立 workspace；本地并行建议 git worktree | primary/subagent，@mention，子会话可导航 | **只读检索子代理 + 独立上下文 + 并发/深度上限**；暂不做通用 worktree 编排 |
| **Skills/MCP/Hooks** | SKILL.md 渐进披露；MCP 工具默认延迟加载；约 30 个 hook 事件，exit 2 可阻断 | skills/plugins + MCP（required、startup_timeout、tool allow/deny）；PreToolUse/PostToolUse/PreCompact/PostCompact | `.opencode/plugins`；MCP local/remote + OAuth；timeout 30s | **hooks（PreToolUse/PostToolUse/PreCompact）+ MCP 持久化**；Skills 已有较好基础 |
| **可观测/评测** | OTEL 指标（session/token/cost/code_edit decision/active_time）+ 事件 + beta trace；内容默认脱敏 | `[otel]` 可选；公开 trace 文档较少 | 插件事件（permission.asked/replied、session.compacted、todo.updated） | **OTel GenAI + trace_id 贯穿 + 轨迹评测（工具序列/引用/权限断言）** |
| **UX** | TUI/IDE/桌面/Web 同一 loop；双 Esc rewind、diff 预览、`/context`、`/usage`、状态栏成本/上下文占比 | 状态栏 context left、`/review`、`/status`、`/permissions` | Tab 切模式、`/undo`/`/redo`、子会话导航 | **上下文/成本状态栏 + diff/回滚 + once/always/reject 审批卡 + 来源卡片** |

### A.2 最值得抄的 10 条（按 ROI）

1. **工具结果外置**：超过阈值落盘，上下文只留“路径 + 预览 + 引用”，需要时用 `read_tool_observation` 回读。你已经有归档和读取工具，差的是“默认替换而不是复制”。
2. **压缩分级 + 熔断**：先清旧 tool results，再摘要；阈值可配；连续失败停止并告知用户，不反复压缩。你的 fail-closed 方向对，补“分级”和“熔断”。
3. **计划一等公民**：`plan` 作为实体（版本、状态、审批人、关联 todo），压缩后按 id 重注入；Plan mode 下禁止写操作。比单纯 `todo_write` 更成熟。
4. **权限判定器**：allow/ask/deny + 模式 + 保护路径，deny 恒优先；每次决策写审计。你已有 HITL，补“规则引擎”和“保护路径”。
5. **会话事件日志 + resume/fork**：JSONL 或事件表，支持命名、TTL、fork；明确“恢复时是否重放未完成工具调用”。你的 checkpoint 只覆盖 HITL，补普通轮次恢复。
6. **HITL hooks**：PreToolUse 校验、PostToolUse 裁剪、PreCompact 保存关键状态。你的 Router 已接近，抽成 hook 点即可复用。
7. **AGENTS.md + path-scoped rules + 可审查 auto-memory**：项目规则按路径懒加载；自动记忆必须可查看、可删除、可关闭。不要黑箱。
8. **轨迹评测集**：20–50 条硬件问答/排障场景，断言工具序列、RAG 引用、权限决策、最终状态；用 pass^k 测稳定性。
9. **子代理隔离**：独立上下文、只回摘要 + 引用、并发与深度上限。最适合“整本手册找参数/读长日志”。
10. **UX 三件套**：上下文/成本状态栏、diff/回滚、once/always/reject 审批卡。用户对“成熟”的感知，很大程度来自这三样。

### A.3 产品约定 vs 必须自研

**可以直接照搬的交互约定**

- Shift+Tab 模式轮换、`/compact`、`/context`、`/usage`、`/resume` 选择器、`@subagent`、计划审批页、diff/rewind、三态审批卡、上下文百分比/成本显示。

**必须自研的基础设施**

- token 计数与模型窗口映射；
- 压缩流水线与摘要 schema；
- 工具结果存储/引用；
- 会话持久化/恢复/分支；
- 权限引擎与沙箱执行；
- 子代理调度与隔离；
- OTel/事件 schema；
- 评测 harness。

**协议适配即可**

- MCP、Agent Skills 格式、AGENTS.md 解析。

### A.4 对本项目的定位建议

不要试图变成“通用 Claude Code”。你的护城河是：

1. **硬件领域记忆**：板卡 profile、引脚、电压、封装、工具链、已否决方案；
2. **来源可信的 RAG**：每个 `[srcN]` 可点开原文和页码，无依据明确拒答；
3. **单屏工作台**：串口、烧录、代码、接线图、安全检查与 Agent 对话联动。

成熟的做法是：**通用 harness 能力（记忆、压缩、恢复、权限、观测）对齐成熟线，领域能力（硬件记忆、来源可信、工作台）做到别人没有。** 不要把精力花在重复造一个通用 Agent 上。

### A.5 参考 URL

**Claude Code**

- https://code.claude.com/docs/en/how-claude-code-works
- https://code.claude.com/docs/en/permission-modes
- https://code.claude.com/docs/en/tools-reference
- https://code.claude.com/docs/en/context-window
- https://code.claude.com/docs/en/model-config
- https://code.claude.com/docs/en/checkpointing
- https://code.claude.com/docs/en/memory
- https://code.claude.com/docs/en/sessions
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/worktrees
- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/monitoring-usage
- https://platform.claude.com/docs/en/build-with-claude/context-editing

**OpenAI Codex**

- https://learn.chatgpt.com/codex/cli
- https://learn.chatgpt.com/codex/agent-approvals-security
- https://learn.chatgpt.com/codex/config-file/config-reference
- https://learn.chatgpt.com/codex/cloud
- https://learn.chatgpt.com/docs/long-running-work.md
- https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/tools/handlers/plan.rs

**OpenCode**

- https://opencode.ai/docs/agents/
- https://opencode.ai/docs/permissions/
- https://opencode.ai/docs/config/
- https://opencode.ai/docs/plugins/
- https://opencode.ai/docs/mcp-servers/
- https://opencode.ai/docs/cli/

**Gemini CLI / Cursor / Cline / Aider**

- https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/plan-mode.md
- https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/core/subagents.md
- https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/checkpointing.md
- https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/sandbox.md
- https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/behavioral-evals.md
- https://cursor.com/docs/context/rules
- https://cursor.com/docs/agent/modes
- https://cursor.com/docs/agent/security
- https://docs.cline.bot/prompting/cline-memory-bank
- https://docs.cline.bot/features/auto-compact
- https://aider.chat/docs/usage/modes.html
