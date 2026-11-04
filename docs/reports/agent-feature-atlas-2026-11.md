# Agent 功能全景与项目对照

> 调研日期：2026-11-04
> 代码基线：本地 `E:\Desktop\agent`，master `eab62d2` + 7,428 行新增 / 536 行删除未提交改动
> 调研方法：7 路并行，分别覆盖工具协议、Skills/插件/Hooks、规划与多代理、记忆/知识、运行时/沙箱/安全、可观测/评测/成本/部署、主流框架功能矩阵；每路以官方文档/源码为准
> 证据边界：只读源码与公开资料，未运行完整测试、真实模型、浏览器或硬件；“已有”只表示代码路径存在，不等于已验收

---

## 0. 先看结论

把 25 个主流 Agent 产品/框架放在一张矩阵里，2026 年的“入场券”其实只有 6 项：

**Agent loop、类型化工具 + 并行调用、MCP、扩展 API、权限/审批、可恢复的会话。**

这 6 项你已经有了 5 项半。你缺的不是“会不会调工具”，而是成熟 Agent 的**第二层能力**：

- **记忆**：从“一个手动文本框”到“分层 + 自动抽取 + 检索 + 治理”；
- **上下文**：从“压缩一次算一次”到“压缩可复用、可手动、可观测、失败可恢复”；
- **恢复**：从“HITL 能续”到“断线能续、重启能续、可 fork/rewind”；
- **编排**：从“单 Agent + todo”到“子代理 + 计划审批 + 工作区隔离”；
- **质量**：从“有测试”到“有 trace、有黄金集、有 CI 回归、有成本”；
- **执行安全**：从“权限模式 + 审计”到“真正的 OS/容器沙箱”。

一句话：**你的第一层（工具/RAG/权限）已经很像成熟项目；第二层（记忆/恢复/编排/质量）还差一个身位。**

### 0.1 成熟度五级（用来定位）

| 级别 | 名字 | 标志性能力 | 你的位置 |
| --- | --- | --- | --- |
| L1 | 对话式 | 聊天、流式、模型选择、Markdown | 已超过 |
| L2 | 工具型 | 工具协议、并行、权限/审批、RAG、审计 | **已基本达到** |
| L3 | 长任务型 | 长期记忆、上下文压缩、会话恢复、计划/Todo、子代理 | **部分达到**（有压缩/Todo，缺记忆/恢复/子代理） |
| L4 | 生产型 | 可观测、评测、CI、沙箱、成本、多用户 | 未达到 |
| L5 | 平台型 | 插件市场、多代理、云任务、A2A/AG-UI | 未达到，也不建议现在追 |

“和成熟项目大差不差”的现实目标：**L4 的骨架 + L3 的完整体验**，而不是 L5 的功能清单。

---

## 1. 24 个能力域总表

评级：✓ 已有且较完整；◐ 有路径但缺关键闭环；✗ 基本没有。

| # | 能力域 | 成熟线 | 项目现状 | 优先级 |
| ---: | --- | --- | --- | --- |
| 1 | Agent Loop / 工具路由 | 必备 | ✓ LangGraph `create_agent` + ToolNode + 27 个工具 | 保持 |
| 2 | 工具协议 / 生态 | 必备 | ✓ schema/并行/结果 envelope/审计；◐ 无 tool_choice 四态、无 tool search | P1 |
| 3 | Skills | 常见 | ✓ 标准 SKILL.md、固定 SHA、请求快照、预算 | 保持 |
| 4 | Plugins / Hooks / Extensions | 常见 | ✗ 无生产插件系统；`PermissionMatcher`/plugins 是死代码 | P1 |
| 5 | 指令 / 规则系统 | 必备 | ◐ 有 `systemPrompt` 但 Agent 路径不用；无 AGENTS.md/CLAUDE.md 加载 | P1 |
| 6 | MCP | 必备 | ✓ stdio tools；✗ resources/prompts/sampling、远程/OAuth、配置持久化 | P1 |
| 7 | Context / Compaction | 必备 | ◐ 结构化摘要 + fail-closed；✗ 预算单位 bug、结果不持久、microcompact 死代码 | **P0** |
| 8 | Memory / History | 常见 | ◐ 手动 4,000 字；✗ FTS 未接线、InMemoryStore、无记忆表 | **P0** |
| 9 | Session / Run / Checkpoint | 必备 | ◐ 消息持久化 + HITL checkpoint；✗ 普通轮次不恢复、无 fork/rewind | P1 |
| 10 | Plan / Todo / Goal | 常见 | ◐ 有 `todo_write` + 右栏；✗ 无 plan mode、无 goal | P1 |
| 11 | Permissions / HITL | 必备 | ✓ 模式 + allow/ask/deny + 一次性授权 + 审计 | 保持 |
| 12 | Sandbox / Execution | 常见 | ✗ 仅应用层路径/命令检查；PlatformIO 构建可执行任意 Python | P2（开源前 P1） |
| 13 | Guardrails / Injection | 常见 | ◐ 提示词层“不可信数据”；✗ 无分类器、无 PII、无输出护栏 | P1 |
| 14 | RAG / Sources | 常见 | ✓ 三种分块 + 混合检索 + rerank + 来源；◐ 引用不校验、页码混用 | **P0** |
| 15 | Multimodal / Browser / Computer Use | 加分 | ◐ 图像/视觉/生成；✗ 浏览器/Computer Use | P2 |
| 16 | Subagents / Worktrees | 常见 | ✗ 无子代理、无 worktree | P1 |
| 17 | Workflows / Multi-agent | 加分 | ◐ LangGraph 单图；✗ 无自定义图/多代理 | P2 |
| 18 | Background / Cloud / Scheduling | 加分 | ✗ 无后台任务、云任务、定时 | P3 |
| 19 | Observability / Tracing | 常见 | ◐ 自研 timing + Prometheus + 审计；✗ 无 OTel/trace 树 | P1 |
| 20 | Eval / Quality | 常见 | ◐ 35 题 RAG 部分通过；✗ 无 Agent/Memory 评测、无 CI | **P0** |
| 21 | Cost / Model Routing / Caching | 常见 | ◐ token 统计；✗ 无成本、无路由、无 prompt cache | P1 |
| 22 | UX / Interaction | 常见 | ✓ 会话/来源/工作台/diff/todo/审批；◐ 无流式续传、无虚拟化 | P1 |
| 23 | Deployment / OSS | 必备 | ◐ README/截图/文档强；✗ 无 CI、CHANGELOG、CONTRIBUTING、release | P1 |
| 24 | Security / Privacy / Multi-user | 必备 | ◐ 加密 key + 本地 token + 审计；✗ 无多租户/配额、无 PII | P2 |

---

## 2. Table stakes / 常见 / 差异化

基于 25 个产品/框架的横向统计（Claude Code、Codex、OpenCode、Gemini CLI、Cursor、Cline、Continue、Copilot、Aider、OpenAI Agents SDK、Claude Agent SDK、LangGraph、LlamaIndex、Google ADK、MS Agent Framework、AutoGen、Semantic Kernel、CrewAI、Pydantic AI、Vercel AI SDK、Mastra、Agno、Dify、n8n、RAGFlow）：

### 2.1 Table stakes（≥80% 都有，不做就像半成品）

1. Agent loop + 工具调用
2. 类型化工具 schema + 服务端校验
3. 并行工具调用
4. MCP（至少 client）
5. 扩展 API（自定义工具/插件/命令）
6. 权限/审批
7. 流式输出
8. 会话持久化
9. 错误处理/超时
10. 至少一套模型配置

**你的差距**：扩展 API 只有 REST/SSE，没有插件 SDK；MCP 只有 stdio。其余都在。

### 2.2 常见但非必需（30–80%，决定“成熟感”）

- 长期记忆 / 用户 profile
- Session resume / fork / rewind
- 成本统计 / 模型路由
- Tracing / 可观测
- 浏览器 / 代码执行
- 多代理 / 工作流图
- RAG / 引用
- 子代理
- 云任务 / 定时
- 多用户 / RBAC
- 产物 / 撤销
- Plan / Todo
- Durable execution
- 沙箱
- 引用 / 来源
- Eval
- Hooks

**你的差距**：记忆、resume、tracing、成本、子代理、沙箱、eval、hooks 是主要缺口；RAG/引用、Plan/Todo、产物/撤销已有基础。

### 2.3 差异化（<30%，但头部正在快速补齐）

- Tool search / 延迟加载
- Skills
- Plugins / marketplace
- 动态工作流
- Agent teams / A2A
- 硬件/领域专用记忆

**你的判断**：Skills 已经有，是加分项；tool search 只有工具数 >30–50 才有收益，暂时不用做；插件市场没有生态时是负资产。

---

## 3. 你已经有的能力（不要推翻）

### 3.1 工具层

- 27 个工具，覆盖文件（read/write/edit/multi_edit/apply_patch/undo/glob/grep/list）、检索（search_docs/list_kb_docs/search_history/read_tool_observation）、Web（web_search/webfetch）、视觉（view_image/vision_analysis/image_generation）、硬件（audit_pins/wiring/build_firmware/flash_firmware）、工作台（render_code/render_wiring/render_safety_report）、执行（run_command/todo_write）。
- `ToolSpec` 统一 schema/风险/超时/重试；`ToolRouter` 做校验、超时、envelope、审计、观察归档。
- HITL：模式、逐调用确认、一次性授权、拒绝继续、审计脱敏。
- 这已经是“成熟 Agent 工具层”的 80%。

### 3.2 RAG 层

- 三种分块（hybrid / agent / multimodal），Vision-LLM 2 阶段/2 轮投票/4 级降级。
- 父子块、BM25 + 向量、RRF(k=60)、bge-reranker、来源注册器、`[srcN]` 卡片。
- 这比大多数“RAG Demo”成熟，接近 RAGFlow/Dify 的简化版。

### 3.3 上下文层

- 结构化摘要（goal/facts/decisions/open_tasks/uncertainties/evidence_locators）。
- fail-closed，不 snipping；工具观察归档 + `read_tool_observation` 回读。
- 方向正确，差的是预算单位、持久化、运行中计数和产品闭环。

### 3.4 产品层

- 会话、项目分组、置顶、重命名、分支 action、重试、编辑重发、引用、导出、模板、书签、反馈、统计、全局搜索。
- 文件浏览器：树、编辑、Diff、快照、回收站、监听、搜索。
- 工作台：串口、烧录、代码、接线图、安全检查。
- 设置：模型/Provider、token、上下文窗口、RAG、权限模式、Skills、MCP、审计、长期记忆、Token 用量。
- 这已经是“一个小产品”，不是“一个脚本”。

### 3.5 工程层

- 83 个后端测试文件、31 个前端测试文件；AGENTS.md、能力基线、API 契约、测试边界、验收记录。
- 文档纪律比很多“成熟开源项目”更强。

---

## 4. 逐域详解

> 每域格式：**功能点（成熟线）→ 成熟实现 → 项目现状 → 差距/建议**。
> 成熟线标记：● 必备　○ 加分　△ 可选。

### 4.1 Agent Loop / 工具路由

**功能点**

- ● ReAct/工具循环；● 最大步数/递归上限；● 取消/中断；● 流式输出；○ 反思/自检；○ replan；△ 多方案规划。
- ● 工具调用并行；● 结果按 call id 回填；● 成功/错误区分；● 超时；○ 退避重试；● 循环检测。

**成熟实现**

- Claude Code / Codex / OpenCode 都是“模型→工具→模型”循环；Claude Code 有 Task/Todo、子代理、后台任务；Codex 有 `update_plan`、`/goal`、cloud tasks。
- LangGraph `create_agent`/StateGraph + checkpointer + interrupt；OpenAI Agents SDK handoffs + sessions + tracing。

**项目现状**

- ✓ `create_agent` + ToolNode；同批 tool_calls 并行；`recursion_limit=500`；stop/AbortController；SSE 13 类事件。
- ◐ 重试只覆盖超时；无退避；无循环检测（注释称已迁移，实际没有）；无反思/replan。
- ◐ 工具输出不再截断（`tool_router.py:240-243`），容易撑爆上下文/成本。

**差距/建议**

- P1：循环检测（`tool_name + args hash`，重复 N 次终止/提示）；LLM/工具指数退避；工具输出“预览 + observation ref”而不是全文回灌。
- P2：可选 reflection/replan 节点；先有评测证明收益，不要默认开。

### 4.2 工具协议 / 生态

**功能点**

- ● JSON Schema/Pydantic/Zod；● 必填/枚举/默认；● 服务端二次校验；○ strict；○ output schema；● `tool_choice` 四态；● 指定工具子集；● 并行；○ 流式参数；● 错误语义；● 结果按 id 回填；● 超时/重试/幂等；● 去重/循环检测；○ tool search/defer；● 命名空间；● 工具数量/成本监控；○ 审计/回放；○ MCP；△ 市场。

**成熟实现**

- OpenAI：`tool_choice` auto/required/none/指定；`allowed_tools`；`strict` 要求 `additionalProperties:false`；`parallel_tool_calls`。
- Anthropic：`input_schema`、`tool_choice` auto/any/tool/none、`disable_parallel_tool_use`；tool search（regex/bm25 + `defer_loading`）在 30–50+ 工具时保准确率。
- MCP 2025-11-25：`outputSchema`、`structuredContent`、`tasks`、`icons`、cursor 分页。
- Claude Code permissions：`Bash(npm run *)`、`WebFetch(domain:)`、`Read(./.env)`，deny→ask→allow。

**项目现状**

- ✓ schema、并行、envelope、审计、MCP stdio、工具开关。
- ◐ 无显式 `tool_choice` 四态/工具子集；无 tool search；无 output schema 硬校验；工具无版本号；MCP 无 resources/prompts/sampling/远程/OAuth。
- ◐ 工具命名空间靠 group 目录，模型侧没有前缀。

**差距/建议**

- P1：加 `tool_choice`（auto/required/none/指定）与按请求工具子集；工具描述/参数做契约测试；MCP 配置持久化。
- P2：工具数超过 40 时再做 tool search/defer；现在做收益低。

### 4.3 Skills

**功能点**

- ● SKILL.md 格式；● name/description；● references/assets/scripts；○ allowed-tools；● 渐进披露（描述→正文→资源）；● 按需加载/预算；● 固定版本/哈希；● 启用/停用；● 导入预览/确认；● 兼容诊断；○ 跨 Agent 互操作；○ 市场/更新。

**成熟实现**

- Claude Code Skills：描述截 1536 字符、正文按需、压缩后每 skill 5k/总 25k；Skills 是“能力包”，不是权限。
- Codex/OpenCode/Gemini 也有 skills/instructions；Claude Code plugins 把 commands/agents/skills/hooks/MCP 打包分发。

**项目现状**

- ✓ 标准 SKILL.md、项目/用户目录、GitHub 固定 SHA 预览/导入/导出、请求级快照、96KiB 预算、内容哈希、默认可用/停用、兼容诊断。
- ◐ 无插件打包、无 hooks、无跨 Agent 适配（Claude/Codex/OpenCode 目录兼容只有读取）；`allowed-tools` 不授权（正确）。
- ✗ 无技能市场/更新检查/签名。

**差距/建议**

- 保持现有“只读、不执行脚本、不授予权限”的边界。
- P1：Skills + Plugins + Hooks 打包成“扩展包”，至少支持 `skills/`、`commands/`、`hooks/` 三类；这是 Claude Code/Codex 的成熟形态。
- P2：更新检查/签名/市场；没有生态前不要做。

### 4.4 Plugins / Hooks / Extensions

**功能点**

- ● 插件打包/发现/启用；● 生命周期 hooks（PreToolUse/PostToolUse/PreCompact/SessionStart/SessionEnd/UserPromptSubmit/Stop/SubagentStop/Notification）；● hook 可阻断/改写/注入上下文；○ 插件市场；○ 插件级权限；○ 版本/信任。

**成熟实现**

- Claude Code：约 30 个 hook 事件，exit 2 阻断，JSON 返回 `permissionDecision/additionalContext`；plugins 打 commands/agents/skills/hooks/MCP。
- Codex：PreToolUse/PostToolUse/PreCompact/PostCompact + `additionalContextLimit` 默认 2500。
- OpenCode：`.opencode/plugins`，`tool.execute.before/after`、event、自定义 tool。
- Gemini：extensions 打包 prompts/MCP/commands/themes/hooks/subagents/skills。

**项目现状**

- ✗ 无生产插件/ hook 系统；`PermissionMatcher`、`PluginManager/RedactPlugin` 已实现但未接线（`permission_matcher.py:63`、`plugins/manager.py:23`）。
- ◐ 有若干“隐式 hook 点”：ToolRouter dispatch、audit recorder、SSE adapter、compaction；但没有统一事件契约。

**差距/建议**

- P1：先做 4 个高价值 hook 点：`PreToolUse`（权限/参数校验）、`PostToolUse`（结果裁剪/脱敏）、`PreCompact`（保存关键状态）、`SessionStart/End`（记忆/索引）。
- P1：把现有 `PluginManager`/`RedactPlugin` 接线或删除；不要保留死代码。
- P2：插件打包/市场；等有第三方需求再做。

### 4.5 指令 / 规则系统

**功能点**

- ● 项目指令文件（AGENTS.md/CLAUDE.md/GEMINI.md）；● 用户/项目/本地分层；● 路径作用域；● 规则优先级/冲突；● 压缩后重注入；○ 自动 memory；○ 规则建议/生成。

**成熟实现**

- Claude Code：CLAUDE.md 四级（managed/user/project/local）+ AGENTS.md 兼容 + `.claude/rules` 按 paths 懒加载；auto memory 每会话加载 MEMORY.md 前 200 行/25KB。
- Codex：AGENTS.md + `/init` + trust_level。
- Cursor：`.cursor/rules/*.mdc` 四种激活，Team>Project>User，建议 <500 行，支持嵌套 AGENTS.md。
- Cline：memory-bank 六文件（brief/product/system/tech/active/progress）。

**项目现状**

- ◐ `systemPrompt` 设置存在，但 Agent 路径用 `build_system_prompt()`，不读 `payload.system_prompt`；只有 legacy chat 路径用（`chat_helpers.py:135`）。
- ✗ 无 AGENTS.md/CLAUDE.md 加载、无路径作用域、无规则冲突处理、无压缩后重注入。
- ✗ 无提示词版本/实验/回滚；系统提示词是代码常量，改了就影响所有会话。
- ✓ 手动长期记忆 + Skills 以“参考数据”注入，优先级低于系统规则。

**差距/建议**

- P0/P1：接通 `system_prompt` 到 Agent 路径（或明确 UI 说明只对 legacy 生效）；加 AGENTS.md 读取（项目根 + 用户目录），按路径/文件作用域懒加载；压缩后重注入。
- P1：加“本次不用记忆/规则”开关；规则冲突时展示来源和优先级。

### 4.6 MCP

**功能点**

- ● stdio/HTTP/SSE 传输；● tools/list + 快照；● tools/call；○ resources；○ prompts；○ sampling；○ elicitation/roots；● OAuth 2.1；● 工具注解（readOnly/destructive/idempotent/openWorld）；● 超时/重启；○ 市场/发现。

**成熟实现**

- MCP 2025-11-25：tools/resources/prompts/sampling/roots/elicitation、outputSchema、structuredContent、tasks、icons；OAuth 2.1 + PKCE + resource indicator。
- Claude Code：MCP 工具默认延迟加载；Codex：MCP required/startup_timeout/tool allow-deny；OpenCode：local/remote + OAuth，token 存 `mcp-auth.json`。

**项目现状**

- ✓ stdio JSON-RPC、initialize/tools/list 快照、按 server 启停、逐调用确认、一次性授权、HIGH 风险、bypass 仍需确认。
- ✗ 配置只在内存（重启丢）；无远程/OAuth；无 resources/prompts/sampling；第三方兼容未验收。
- ◐ 每请求重新绑定 spec，有快照但无 `list_changed` 处理。

**差距/建议**

- P1：MCP 配置持久化到 SQLite；加 resources 读取（只读文本/JSON）；加 `list_changed` 刷新。
- P2：远程 HTTP/OAuth；先做本地 stdio 的稳定性。

### 4.7 Context / Compaction

**功能点**

- ● token 计数/模型窗口；● 自动压缩；● 手动压缩；● 工具结果清理；● 结构化摘要；● 压缩后重注入不变量；● 压缩失败熔断；○ 上下文用量 UI；○ prompt caching；○ 分层摘要；○ 压缩事件可观测。

**成熟实现**

- Claude Code：接近窗口自动摘要；`/compact [focus]`；`/autocompact` 可配；压缩后重注入 CLAUDE.md、auto memory、plan、skills（每 skill 5k/总 25k），重读最近 5 个文件；连续失败停止并报 thrashing。
- Anthropic API：`clear_tool_uses_20250919`（默认 trigger 100k、keep 3 个 tool_use、`clear_at_least`、`exclude_tools`），服务端清理，客户端保留全量历史。
- Codex：`model_auto_compact_token_limit`、`model_post_turn_compact_threshold_percent`、`compact_prompt`、`model_auto_compact_token_limit_scope`。
- OpenCode：`compaction.auto/prune/reserved`，隐藏 compaction agent，插件钩子可改摘要。
- Gemini：`compressionThreshold=0.5`。

**项目现状**

- ✓ `autocompact_messages`：保留最近 10 条、按 tool_call/result 对切分、结构化 JSON 摘要、source_refs、失败重试 3 次后 fail-closed（`autocompact.py:117-375`）。
- ✓ 工具观察归档 + `read_tool_observation` 回读（`tool_observations.py:84-174`）。
- ✓ 压缩前 `context_compressing` SSE + 前端 banner。
- ✗ **预算单位 bug**：`reasoning_chat.py:121-125` 用字节数与 token 阈值比较，且 `ensure_ascii=True`，中文每字约 6 字节，提前 4–10 倍触发 `CONTEXT_LIMIT`。
- ✗ **运行中漏计**：`accumulate_tokens` 对 dict 工具结果计 0（`context_guard.py:379-390`）。
- ✗ **microcompact 死代码**：全仓无调用（`microcompact.py:26`）。
- ✗ **不跨轮**：预发送压缩只改本次请求，下一轮重置 checkpoint 后重新压缩（`chat_routes.py:855-891,971-977`）。
- ✗ 无手动 `/compact`、无压缩后事件（before/after token）、无摘要留存、无上下文余量 UI、无分层摘要。

**差距/建议**

- **P0**：修单位与 dict 计数；压缩结果写回会话（摘要 + 覆盖范围 + epoch），下一轮复用；新增 `context_compacted` 事件。
- **P1**：分层摘要（超窗口分段）；pinned invariants 每轮重注入（系统规则、板卡 profile、用户硬约束、已否决方案）；接入或删除 microcompact；手动 `/compact`；上下文余量环。
- **P2**：thinking 块清理、工具输入清理、按工具类型选择性保留；压缩质量评测。

### 4.8 Memory / History

**功能点**

- ● 会话短期记忆；● 用户级长期记忆；● 自动抽取；● 去重/更新；● 语义检索 + 元数据过滤；● 来源/时间戳；● 用户可见/可编辑/可关闭；● 删除传播；○ 分层（episodic/semantic/procedural）；○ 图记忆；○ TTL/衰减；○ 项目/板卡 profile；○ 评测。

**成熟实现**

- Claude：CLAUDE.md 四级 + `.claude/rules` 路径作用域 + auto memory（MEMORY.md 前 200 行/25KB）；Memory tool 文件式 `/memories`，view/create/str_replace/insert/delete/rename。
- Codex：AGENTS.md + 跨会话 memories；OpenCode：AGENTS.md + instructions。
- Mem0：抽取 → 查重 → ADD/UPDATE/DELETE/NOOP → 向量 + 实体图；SQL 为真源。
- Letta：core blocks（常驻、label/limit/read_only）+ archival（向量、Agent 主动写）+ recall；sleep-time 后台重整。
- LangGraph Store / LangMem：namespace + semantic/episodic/procedural；profile（单文档覆盖）vs collection（逐条 reconcile）。
- Zep/Graphiti：时序知识图，边带双时间与失效标记。

**项目现状**

- ◐ 手动 `longTermMemory` 单字符串，最多 4,000 字，每轮全量注入（`crud.py:389-396,432-439`、`prompts.py:174-186`）。
- ✗ `session_search.py:24,42-45,58-62` 的 `init_session_db()`/`index_session_message()` **全仓无调用者**，`search_history` 实际返回空；现有库只有 1 条旧数据。
- ✗ 语义存储是 `InMemoryStore`，进程重启即丢（`store_adapter.py:49,67`）。
- ✗ 删除会话只删 ORM，不删 FTS/Store（`crud.py:252-257`）。
- ✗ 无条目级 scope/type/来源/置信度/TTL/冲突/逐条删除；无记忆面板（只有文本框）。

**差距/建议**

- **P0**：启动调用 `init_session_db`，消息落库后 `index_session_message`；删除/截断同步清理；`ORDER BY rank`；UI 支持显式空串。
- **P0/P1**：`memory_entries` 表 + `MemoryManager` + 每轮检索注入 + 记忆面板（Memory v1，1–2 周）。
- **P1**：自动抽取（只写持久/可复用/非敏感）、去重/冲突（ADD/UPDATE/DELETE/NOOP）、TTL/衰减、删除传播。
- **P2**：板卡/项目 profile 结构化记忆；图记忆；LongMemEval/LoCoMo 类评测。

### 4.9 Session / Run / Checkpoint

**功能点**

- ● 会话/消息持久化；● checkpoint；● resume；○ fork/branch；○ rewind/时间旅行；○ run/event 事件模型；○ 崩溃恢复/持久执行；○ TTL/清理；○ 多端同步。

**成熟实现**

- Claude Code：JSONL 存 `~/.claude/projects/<project>/<id>.jsonl`；`--continue/--resume`、`/branch`、fork；30 天清理；checkpoint 保留最近 100 个，可只回滚代码/对话。
- Codex：`codex resume`；`history.jsonl` + sqlite。
- OpenCode：`--continue/--session/--fork`；session list/delete/export/import；SQLite。
- LangGraph：`thread_id` + checkpointer，time travel；Temporal/Inngest 事件重放与 step 记忆化。

**项目现状**

- ✓ `sessions/messages` 表；前端 localStorage 分片 + 后端覆盖；HITL checkpoint（SQLite/InMemory）。
- ◐ 普通 Agent 请求每轮先重置 thread（`chat_routes.py:971-977`），只有 HITL resume 复用；进程重启后 pending 确认快照（30 分钟 TTL、内存）丢失（`chat_routes.py:98-101`）。
- ◐ `branchThread` action 存在，但 `BranchTree` 组件未挂载；无 rewind。
- ◐ SSE 无 Last-Event-ID/自动重连（`client.ts:255-385`），刷新后背景流/pendingConfirm 丢失。
- ✓ 会话导出 markdown/json；项目分组、置顶、重命名、搜索。

**差距/建议**

- **P0**：HITL pending 快照与 MCP 配置持久化到 SQLite。
- **P1**：SSE 事件 ID + 断线重连；运行状态持久化（run id、最后事件序号）；普通轮次 resume。
- **P2**：fork/rewind UI；分支树；多端同步。

### 4.10 Plan / Todo / Goal

**功能点**

- ● 只读 plan mode；● 计划文件持久化；● 计划审批/模式切换；● todo/任务图 + 依赖；○ goal + 完成判据；○ 计划版本/多方案；○ 压缩后重注入。

**成熟实现**

- Claude Code：`EnterPlanMode/ExitPlanMode`，Shift+Tab 或 `/plan`；计划存 `~/.claude/plans/`，Ctrl+G 外部编辑；`TaskCreate/Get/Update/List` 支持依赖。
- Codex：`update_plan` checklist；`/plan` 先访谈；`/goal` 把目标+约束+验证标准作为完成判据，可暂停/恢复/编辑/清除。
- OpenCode：primary `plan`/`build`，Tab 切换；plan 只能写 `.opencode/plans/`。
- Gemini：两段确认（策略确认→计划审批）；计划文件保留 30 天，Ctrl+X 编辑。
- Cursor：Shift+Tab，Markdown 计划，默认存 home。

**项目现状**

- ✓ `todo_write` + 右栏 TodoCard + SSE `todo_update`；提示词要求复杂任务先列清单（`prompts.py:120`）。
- ✗ 无 plan mode（先只读研究、出计划、用户批准再执行）；无 goal/完成判据；无计划文件/版本/审批状态；无压缩后重注入。

**差距/建议**

- **P1**：加 plan mode（只读 + 计划文件 + 审批后执行）；与现有 todo 打通；计划压缩后按 id 重注入。
- **P2**：goal/完成判据；计划版本/多方案；外部编辑器。

### 4.11 Permissions / HITL

**功能点**

- ● 权限模式；● allow/ask/deny；● deny 恒优先；● 参数级规则；● 保护路径；● 一次性/会话授权；● 审计；○ 审批疲劳缓解（分类器/记住选择）；○ 策略即代码。

**成熟实现**

- Claude Code：6 模式（default/acceptEdits/plan/auto/dontAsk/bypassPermissions）；顺序 deny→ask→allow；支持 `Bash(npm run *)`、`WebFetch(domain:)`、`Read(./.env)`；保护 `.git/.claude` 等；auto 分类器默认拦 `curl|bash`、force push、`reset --hard`。
- Codex：`sandbox_mode`（read-only/workspace-write/danger-full-access）× `approval_policy`（on-request/never/granular）；网络默认关，域名 deny 胜。
- OpenCode：allow/ask/deny，最后匹配胜；`doom_loop`/`external_directory` 默认 ask；`.env` ask。

**项目现状**

- ✓ 权限模式、风险分级、逐调用确认、一次性授权、拒绝后继续、审计脱敏（`permission_classifier.py:74-113`、`mcp_authorization.py:155-179`、`audit_recorder.py:81-130`）。
- ◐ `path_guard` 的允许目录限制已被移除，只保留黑名单（`path_guard.py:225-234`）；无参数级规则（如按命令前缀/域名）。
- ◐ 无“记住此选择/会话级放行”；审批疲劳没有缓解。
- ✓ 审计表字段完整（session/tool/args/decision/source/risk/exit/duration/error/call_id/success）。

**差距/建议**

- **P1**：加参数级规则（命令前缀、域名、路径 glob）；保护路径硬编码；deny 恒优先。
- **P1**：加“本次会话允许此工具/此命令”与分类器建议；减少审批疲劳。
- **P2**：策略即代码/团队策略。

### 4.12 Sandbox / Execution

**功能点**

- ● OS/容器沙箱；● 文件 allowlist；● 网络 allowlist；● 资源限制（CPU/内存/时长/PID）；○ 快照恢复；○ 设备访问代理；○ 构建/烧录隔离。

**成熟实现**

- 强度阶梯：Docker（namespace+cgroup+seccomp）< gVisor（用户态内核）< Firecracker microVM（KVM）；托管 E2B/Daytona/Modal/Browserbase；WASI capability。
- OS 原语：macOS Seatbelt；Linux Landlock + bubblewrap + seccomp；Windows AppContainer + Job Object / MXC。
- Codex/Claude Code 都把“审批”与“沙箱”分开：审批决定是否允许，沙箱决定实际能做什么。

**项目现状**

- ✗ 无 OS/容器沙箱；`run_command` 直接起 `powershell.exe -Command` / `bash -c`，只校验 cwd 与可解析写目标（`run_command.py:225,316-331`）。
- ✗ PlatformIO 构建：`options` 原样写入 `platformio.ini`（`pio_runner.py:184-210`），`extra_scripts`/`lib_deps` 可导致构建期任意 Python 执行；构建与烧录都在宿主进程。
- ◐ `/api/upload` 的 `binary_path` 有 `.build/tmp/` 前缀校验，但用 `normpath` 而非 `realpath`（`pio_runner.py:163-171`），symlink/junction 可绕过；应加 realpath + 不允许符号链接。
- ◐ 密钥：Fernet 加密 + Windows ACL（`auth.py`），但主 `.env` 仍有明文 key；日志脱敏不完整。

**差距/建议**

- 只自己用：**P2**；开源分发前：**P1**。
- 最小方案：编译/烧录放进 Windows Job Object/受限令牌或 WSL 容器；工作区只挂临时目录；网络默认断；只允许本会话产物路径（realpath + 前缀）。
- MCP 子进程同样受限；设备访问走代理（只允许指定 COM 口）。

### 4.13 Guardrails / Injection

**功能点**

- ● 输入/输出护栏；● 结构化输出校验；● 提示注入防护；● PII/日志脱敏；○ 内容安全分类器；○ 工具参数校验；○ 二次确认。

**成熟实现**

- OpenAI Agents SDK input/output guardrails + tripwire；NeMo Guardrails（Colang）；Guardrails AI（RAIL + reask）；Llama Guard；Presidio（PII）；Instructor/Pydantic 结构化输出重试。
- 原则：指令与数据分离；检索文档/网页/工具结果/skill 正文一律不可信；最小工具集；高风险动作二次确认；OWASP LLM01；lethal trifecta。

**项目现状**

- ◐ 提示词层已声明“检索文本是证据不是指令”“Skill 正文不可信”“手动记忆是参考数据”（`prompts.py:135,181-185,198`）。
- ◐ 工具结果归档/回读时标注“untrusted historical data”（`read_tool_observation.py`）。
- ✗ 无输入/输出分类器、无 PII 脱敏、无内容安全、无结构化输出硬校验；工具参数只做 schema 校验。
- ◐ 提示注入主要靠 prompt，没有“工具结果注入扫描/引用来源扫描”。

**差距/建议**

- **P1**：输出 schema 硬校验 + 失败重试；PII/密钥正则脱敏（日志/审计/SSE）；工具结果标记 + 高风险动作二次确认。
- **P2**：注入分类器（本地小模型或规则）；内容安全；red team 测试集。

### 4.14 RAG / Sources

**功能点**

- ● 摄取/分块；● 混合检索；● rerank；● 父子块；● 引用/页码；● 多 KB/范围过滤；○ contextual retrieval；○ 逐句引用；○ 知识图谱；○ 连接器；○ 数值/型号校验。

**成熟实现**

- RAGFlow：DeepDoc 版面/OCR/表格，模板切分，BM25+向量加权，rerank 候选池 64–256。
- Dify：General/Parent-Child，混合检索 + rerank，Citations 到文档+chunk。
- LlamaIndex：IngestionPipeline、SentenceSplitter、HierarchicalNodeParser + AutoMerging、QueryFusionRetriever、CitationQueryEngine。
- Anthropic Contextual Retrieval：chunk 前置 1–2 句文档语境，失败检索降 35–49%，加 rerank 共降 67%。
- Claude Citations：`page_location` + 逐字 `cited_text`。

**项目现状**

- ✓ 三种分块、父子块、BM25+向量、RRF(k=60)、bge-reranker、来源注册器、`[srcN]` 卡片。
- ◐ 单 KB 内向量与 BM25 串行（`kb_manager.py:1113,1121`）；`doc_filter` 融合后过滤、候选只扩 3 倍（`kb_manager.py:333,1109`）。
- ✗ 后端不校验模型是否编造 `[srcN]`；`page=chunk_index` 与 `page_start` 混用（`search_docs.py:357`）。
- ✗ 父块按每个小片重复展开，同段可多次占 src（`search_docs.py:231-238`）；hybrid 不合并跨页表。
- ◐ 35 题黄金集仅 7 题四指标完整；来源链路 30/35=85.7%；无数值/型号确定性校验。

**差距/建议**

- **P0**：引用校验（未知 srcN 剥离/重试）；统一页码字段；doc_filter 前移到 Chroma where/BM25 并扩池；区分“故障”与“空结果”。
- **P1**：chunk 头拼文档名+章节+型号+页码；单 KB 并行召回；逐句引用支持校验；补全 35 题标准评测。
- **P2**：contextual retrieval；跨页表合并；图片逐页描述；数值/型号/寄存器校验；ColPali 按指标决定。

### 4.15 Multimodal / Browser / Computer Use

**功能点**

- ● 图像输入/视觉分析；○ 图像生成；○ 浏览器/Computer Use；○ 音频/语音；○ PDF/文档多模态；○ 页面级视觉检索。

**成熟实现**

- Claude/OpenAI computer use、browser toolset；Playwright MCP；Gemini code execution；OpenAI image generation；ColPali 页图检索。

**项目现状**

- ✓ `view_image`、`vision_analysis`、`image_generation`；PDF 渲染/多模态切分；附件支持图片/文本。
- ✗ 无浏览器/Computer Use；无语音；无页图检索（ColPali）。

**差距/建议**

- P2：浏览器工具（Playwright）对“查官网/数据手册”有价值；Computer Use 对本地硬件 UI 价值低。
- P2：页图检索仅在引脚图/时序图召回不足时评估。

### 4.16 Subagents / Worktrees

**功能点**

- ● 子代理定义；● 独立上下文；● 结构化返回；● 并发/嵌套上限；○ worktree 隔离；○ 后台运行；○ 子代理续聊；○ agent teams。

**成熟实现**

- Claude Code：`.claude/agents/*.md`，tools/model/permissionMode/maxTurns/isolation:worktree；默认后台；并发 20、嵌套 3 层；transcript 可 SendMessage 续。
- Codex：cloud 每任务独立 workspace；本地建议 git worktree。
- OpenCode：primary/subagent，@mention，子会话可导航。
- Gemini：`.gemini/agents/*.md`，max_turns 30、timeout 10min，禁止子代理再嵌套。

**项目现状**

- ✗ 无 subagent、handoff、worktree；所有工具都在主上下文。

**差距/建议**

- **P1**：加一个只读 `research_subagent(query, doc_ids)`：独立上下文读大量手册 chunk，只回结构化摘要 + `[srcN]`；主 Agent 不承载原文。对“整本手册找参数”收益最大。
- **P2**：代码工作区子代理 + worktree；agent teams。

### 4.17 Workflows / Multi-agent

**功能点**

- ○ 图/条件边/子图；○ durable execution；○ supervisor/swarm/teams；○ handoff/agent-as-tool；○ A2A/AG-UI；○ 可视化工作流。

**成熟实现**

- LangGraph StateGraph + checkpointer + interrupt；Temporal/Inngest/Restate；AutoGen teams；CrewAI crews/flows；Google ADK；MS Agent Framework；Dify/n8n 画布。

**项目现状**

- ◐ 用 LangGraph `create_agent` 单图；无自定义节点/条件边/子图；无多代理；无 A2A。
- ◐ 自研 writer lifecycle/checkpoint publication 提供了一部分 durable 语义，但复杂且未测试。

**差距/建议**

- **P2/P3**：单 Agent + 检索子代理足够；多代理/工作流图在评测基线稳定后再做。
- 不建议现在引入 Temporal/Inngest；本地单机收益低。

### 4.18 Background / Cloud / Scheduling

**功能点**

- ○ 后台任务；○ 云任务；○ 定时/Cron；○ 通知；○ 人工接管；○ 取消/暂停/恢复。

**成熟实现**

- Claude background agents、`/loop`、cloud routines；Codex cloud（电脑休眠仍跑，开 PR）；Temporal Schedule、Inngest cron、Dify/n8n 触发器。

**项目现状**

- ✗ 无后台任务、云任务、定时、系统通知；只有前台 SSE 流和 toast。

**差距/建议**

- **P3**：本地单机优先；先把前台会话/恢复做稳。
- 可选：本地定时任务（索引重建、记忆 consolidation、评测）比云任务更有价值。

### 4.19 Observability / Tracing

**功能点**

- ● trace 树；● token/成本/延迟；● 会话聚合；● 脱敏；○ 采样；○ OTLP 导出；○ agent 拓扑图。

**成熟实现**

- OTel GenAI semconv：`chat/embeddings/execute_tool/invoke_agent` span，`gen_ai.*` 属性；Langfuse/LangSmith/Braintrust/Phoenix/Helicone/Weave。

**项目现状**

- ◐ 自研 JSON timing（`telemetry.py:74-363`）、SQLite 审计、Prometheus `/metrics`、TokenUsage 表 + 面板。
- ✗ 无 request_id 贯穿审计/SSE；无 span 树；无 OTel/Langfuse；无成本；无 trace 查看器。

**差距/建议**

- **P1**：`trace_id` 贯穿请求/SSE/审计/telemetry；记录每轮 token/cost；接 OTel GenAI + Langfuse（自托管）。
- **P2**：trace 查看器；采样/脱敏；agent 拓扑。

### 4.20 Eval / Quality

**功能点**

- ● RAG 指标；● Agent 轨迹评测；● 黄金集管理；● CI 回归；○ Memory 评测；○ LLM-judge 校准；○ pass^k。

**成熟实现**

- RAGAS/DeepEval/TruLens/promptfoo/Inspect；τ-bench/AgentBench/WebArena/SWE-bench；Agent-as-a-Judge；pass^k。

**项目现状**

- ◐ 35 题黄金集 + DeepEval 四指标；仅 7 题完整，加权 97.02（选择偏差）；来源链路 30/35；G026 上下文精度 41.67。
- ◐ `feedback` 表/API 存在（👍/👎），但前端没有反馈入口，也没有把反馈回流到评测集。
- ✗ 无 Agent 轨迹评测、无 Memory 评测、无 CI 门禁；评测脚本手动跑。

**差距/建议**

- **P0**：跑完整 35 题四指标，冻结第一份可信基线；CI 夜间跑。
- **P1**：补 60–100 题 RAG 集；20 个 Agent 场景（工具序列/引用/权限）；20 个 Memory 场景；pass^3。
- **P2**：Agent-as-a-Judge；red team；评测集版本化。

### 4.21 Cost / Model Routing / Caching

**功能点**

- ● token 计费；● 成本统计；○ prompt caching；○ semantic cache；○ 模型路由/降级；○ 预算/配额。

**成熟实现**

- Anthropic/OpenAI/Gemini prompt caching；GPTCache；LiteLLM Router（fallback/429/配额/成本）；Helicone 网关。

**项目现状**

- ◐ TokenUsage 表 + 统计面板（按天/模型/会话）；无成本（缺单价表）。
- ✗ 无 prompt caching、无 semantic cache、无模型路由（只有 compaction fallback）。
- ✓ 搜索缓存 + embedding 缓存。

**差距/建议**

- **P1**：成本单价表 + 会话/模型成本；prompt caching（system/tools 固定前缀）；预算上限。
- **P2**：LiteLLM 式路由/降级；语义缓存。

### 4.22 UX / Interaction

**功能点**

- ● 会话/消息/来源/工具时间线；● 审批卡；● diff/undo；● Todo；○ 上下文/成本状态栏；○ 通知；○ 多语言/a11y；○ 移动端；○ 命令面板。

**成熟实现**

- Claude Code：TUI/IDE/Web 同一 loop；双 Esc rewind、diff、`/context`、`/usage`、状态栏成本/上下文占比。
- Codex：状态栏 context left、`/review`、`/status`、`/permissions`。
- OpenCode：Tab 切模式、`/undo`/`/redo`、子会话导航。

**项目现状**

- ✓ 会话/项目/置顶/重命名/分支 action/重试/编辑重发/引用/导出；来源卡片；工具时间线；TodoCard；ConfirmDialog。
- ✓ 文件浏览器：树、编辑、Diff、快照、回收站、监听、搜索；工作台 5 pane。
- ✓ zh/en、部分快捷键、Toast。
- ◐ 无流式续传、无消息虚拟化（`ChatArea.tsx:386` 直接 map）、无上下文/成本状态栏、无系统通知、无 E2E。
- ◐ ErrorBoundary 出错会清空所有 `hwrag_*` 本地数据且无确认（`ErrorBoundary.tsx:32-33`）。

**差距/建议**

- **P1**：流式重连/续跑；会话分页；消息虚拟化；压缩详情；上下文/成本状态栏；ErrorBoundary 先确认。
- **P2**：命令面板；系统通知；移动端只读/审批；a11y。

### 4.23 Deployment / OSS

**功能点**

- ● 本地/自托管；○ 云/Serverless；○ 团队/多用户；○ CI/CD；○ release/CHANGELOG；○ 贡献指南；○ demo。

**成熟实现**

- 成熟开源项目：CI badge、release/tag、CHANGELOG、CONTRIBUTING、SECURITY、issue/PR 模板、Docker、demo GIF、示例数据。

**项目现状**

- ✓ README（痛点/亮点/截图/快速开始/配置）、LICENSE、docs、截图、83+31 测试文件。
- ✗ 无 `.github/workflows`、无 CHANGELOG、无 CONTRIBUTING/SECURITY、无 issue 模板、无 release/tag、无 Docker、无 repo description/topics、无 demo GIF。
- GitHub 0 star/0 fork；不是代码问题，是分发问题。

**差距/建议**

- **P0**：GitHub Actions（Windows + Ubuntu）：backend pytest、frontend vitest/lint/build；badge。
- **P1**：CHANGELOG、CONTRIBUTING、SECURITY、issue 模板、release 流程、repo description/topics、30 秒 demo。
- **P2**：Docker/一键脚本；示例知识库/黄金集。

### 4.24 Security / Privacy / Multi-user

**功能点**

- ● 密钥存储；● 日志脱敏；● 数据出境提示；○ 多租户/认证；○ 配额/限流；○ PII；○ 审计；○ 数据删除/留存。

**成熟实现**

- OS keychain/DPAPI/Credential Manager；SQLCipher；Presidio；OAuth/OIDC/RBAC；租户 RLS；限流配额。

**项目现状**

- ◐ Fernet 加密 + Windows ACL（`auth.py`）；本地 session token；审计表。
- ✗ 主 `.env` 仍有明文 key；日志脱敏不完整；无 PII；无多租户/配额/限流。
- ◐ 本地只监听 127.0.0.1（正确）；无 CORS/CSRF/DNS rebinding 专项测试。

**差距/建议**

- **P1**：key 迁移到 Windows Credential Manager/DPAPI；日志/审计/SSE 全链路脱敏；数据出境提示。
- **P2**：多用户/RBAC/配额；团队部署；SQLCipher。

---

## 5. 如果只补 10 项

按“投入产出 + 成熟感”排序。估时是净开发时间，不含等待/评审。

| # | 做什么 | 为什么 | 估时 | 验收 |
| ---: | --- | --- | ---: | --- |
| 1 | 修 token 预算单位 + dict 工具结果计数 | 中文长对话现在会提前 4–10 倍报 `CONTEXT_LIMIT` | 0.5–1d | 30K 中文字符请求不误报；估算误差 <15% |
| 2 | 压缩结果持久化 + 手动 `/compact` + `context_compacted` 事件 | 现在每轮重复压缩；用户不知道发生了什么 | 3–4d | 同会话连续 3 轮只压 1 次；UI 显示 before/after |
| 3 | Memory v1：`memory_entries` + 检索注入 + 记忆面板 | 长期记忆是最大短板；现在只有 4,000 字文本框 | 5–8d | 跨会话 20 场景召回/精确 ≥80%；可编辑/删除 |
| 4 | 接通 FTS5 会话索引 + 删除传播 | `search_history` 现在实际一直返回空 | 1–2d | 历史检索有结果；删会话后检索不到 |
| 5 | 引用校验 + 页码统一 | `[srcN]` 可被编造；页码字段混用 | 2–3d | 未知 srcN 为 0；页码与 chunk 元数据一致 |
| 6 | 流式断线重连 + HITL 快照持久化 | 刷新/重启后未完成轮次丢失 | 5–7d | 断网 30s 后自动续；重启后 pending 仍在 |
| 7 | `research_subagent(query, doc_ids)` | 整本手册检索下沉，主上下文只拿摘要+引用 | 5–7d | 主上下文 token 降 50%+；引用可追溯 |
| 8 | OTel GenAI + Langfuse + 成本 | 现在只有自研 timing，无法归因/看成本 | 5–7d | 每请求一条 trace；token/成本/延迟可查 |
| 9 | Eval harness + CI 门禁 | 现在 35 题仅 7 题完整，且手动跑 | 5–7d | 完整四指标基线；PR 回归超阈值失败 |
| 10 | AGENTS.md + 4 个 hook 点 + 扩展包 | 规则/插件/hook 是成熟 harness 的“第二层” | 5–7d | 项目规则生效；PreToolUse/PostToolUse/PreCompact 可阻断 |

> 如果目标是开源分发，再加第 11 项：**编译/烧录沙箱**（Job Object/容器 + 工作区 + 网络 + 设备代理），约 2–4 周。

---

## 6. 你的差异化（不要丢）

通用 Agent 框架不会替你做这些，这是这个项目的护城河：

1. **硬件记忆模型**：板卡 profile（MCU/封装/供电/引脚/外设/工具链/固件版本/已否决方案）结构化、版本化、可检索；比通用自由文本记忆更有价值。
2. **证据级 RAG**：每个 `[srcN]` 可点开原文和页码；无依据明确拒答；数值/型号/引脚约束可校验。
3. **单屏工作台**：串口、烧录、代码、接线图、安全检查与对话联动；这是 Claude Code/Codex 不会做的。
4. **本地优先/离线**：Ollama + 本地 embedding/rerank；数据不出本机；可一键删除。
5. **硬件评测基准**：datasheet 问答、引脚/封装/时序、erratum、接线审计的黄金集；比通用 RAG 基准更贴领域。

成熟策略：**通用 harness 能力（记忆、压缩、恢复、权限、观测）对齐成熟线，领域能力（硬件记忆、来源可信、工作台）做到别人没有。**

---

## 7. 不建议做的事

1. **不要在工具数 <40 时做 tool search/defer**。收益低，复杂度高；先把工具描述和分组写好。
2. **不要在没有生态时做插件市场**。插件系统的价值来自第三方，不是自己写几个。
3. **不要在没有评测基线前做多代理/工作流图**。无法证明收益，只会增加调试成本。
4. **不要继续扩大自研 writer lifecycle / checkpoint publication**。它解决真实问题，但已经明显偏离 LangGraph 原生语义；先收敛成“压缩发布适配器”，而不是通用分布式写入框架。
5. **不要直接引入 Mem0/Letta 整套依赖**。借设计模式（分层、抽取、命名空间、治理），用 SQLite + 向量索引自己实现更合适。
6. **不要为了对标 Claude Code 做 OS 沙箱，如果你是唯一用户**。先修正确性、记忆、恢复；开源分发前再做。
7. **不要把所有 RAG 框架能力抄一遍**。你的差异化是“硬件手册 + 工作台 + 来源可信”，不是“又一个 RAGFlow”。

---

## 8. 参考 URL（按域）

**Agent Harness / 产品**

- Claude Code: https://code.claude.com/docs/en/how-claude-code-works
- Claude Code 权限: https://code.claude.com/docs/en/permission-modes
- Claude Code 上下文: https://code.claude.com/docs/en/context-window
- Claude Code 记忆: https://code.claude.com/docs/en/memory
- Claude Code 会话: https://code.claude.com/docs/en/sessions
- Claude Code 子代理: https://code.claude.com/docs/en/sub-agents
- Claude Code Skills: https://code.claude.com/docs/en/skills
- Claude Code Hooks: https://code.claude.com/docs/en/hooks
- Claude Code Plugins: https://code.claude.com/docs/en/plugins
- Codex CLI: https://learn.chatgpt.com/codex/cli
- Codex 安全: https://learn.chatgpt.com/codex/agent-approvals-security
- Codex 配置: https://learn.chatgpt.com/codex/config-file/config-reference
- OpenCode: https://opencode.ai/docs/agents/
- OpenCode 权限: https://opencode.ai/docs/permissions/
- OpenCode 插件: https://opencode.ai/docs/plugins/
- Gemini CLI Plan: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/plan-mode.md
- Gemini CLI Sandbox: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/sandbox.md
- Cursor Rules: https://cursor.com/docs/context/rules
- Cline Memory Bank: https://docs.cline.bot/prompting/cline-memory-bank

**工具 / MCP**

- Anthropic Tool Use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- Anthropic Tool Search: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- Anthropic Context Editing: https://platform.claude.com/docs/en/build-with-claude/context-editing
- OpenAI Function Calling: https://developers.openai.com/api/docs/guides/function-calling
- MCP 规范: https://modelcontextprotocol.io/specification/2025-11-25
- MCP 安全: https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices
- MCP 授权: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization

**Skills / 扩展**

- Agent Skills: https://agentskills.io/specification
- Claude Skills: https://code.claude.com/docs/en/skills
- Codex Skills: https://learn.chatgpt.com/docs/build-skills
- OpenCode Skills: https://opencode.ai/docs/skills/
- Gemini Extensions: https://github.com/google-gemini/gemini-cli/blob/main/docs/extensions/reference.md

**记忆 / 知识**

- Mem0: https://docs.mem0.ai/core-concepts/memory-types
- Letta Memory Blocks: https://docs.letta.com/guides/agents/memory-blocks
- Letta Sleep-time: https://docs.letta.com/guides/agents/sleep-time-agents
- LangGraph Memory: https://docs.langchain.com/oss/python/langgraph/memory
- LangMem: https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Zep/Graphiti: https://help.getzep.com/concepts
- LongMemEval: https://arxiv.org/abs/2410.10813
- LoCoMo: https://arxiv.org/abs/2402.17753

**RAG / 引用**

- Anthropic Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
- Claude Citations: https://platform.claude.com/docs/en/build-with-claude/citations
- RAGFlow: https://ragflow.io/docs/dev/
- Dify: https://docs.dify.ai/
- LlamaIndex: https://developers.llamaindex.ai/python/framework/
- RAGAS: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- DeepEval: https://deepeval.com/docs/metrics-introduction

**运行时 / 沙箱 / 安全**

- LangGraph Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- Temporal: https://docs.temporal.io/workflow-execution
- Inngest: https://www.inngest.com/docs
- gVisor: https://gvisor.dev/docs/
- Firecracker: https://firecracker-microvm.github.io/
- E2B: https://e2b.dev/docs
- Modal Sandbox: https://modal.com/docs/guide/sandbox
- Windows AppContainer: https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation
- Windows Job Objects: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- OWASP LLM Top 10: https://genai.owasp.org/llm-top-10/
- Presidio: https://microsoft.github.io/presidio/

**可观测 / 评测 / 成本**

- OTel GenAI: https://github.com/open-telemetry/semantic-conventions-genai
- Langfuse: https://langfuse.com/docs
- LangSmith: https://docs.smith.langchain.com/
- Arize Phoenix: https://docs.arize.com/phoenix
- Braintrust: https://www.braintrust.dev/docs
- promptfoo: https://www.promptfoo.dev/docs/intro/
- Inspect AI: https://inspect.aisi.org.uk/
- τ-bench: https://github.com/sierra-research/tau-bench
- SWE-bench: https://www.swebench.com/
- Agent-as-a-Judge: https://arxiv.org/abs/2410.10934
- LiteLLM Router: https://docs.litellm.ai/docs/routing
- GPTCache: https://github.com/zilliztech/GPTCache
- Prompt Caching (Anthropic): https://platform.claude.com/docs/en/build-with-claude/prompt-caching

**框架**

- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
- Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview
- LangGraph: https://docs.langchain.com/oss/python/langgraph/overview
- Google ADK: https://adk.dev/
- MS Agent Framework: https://learn.microsoft.com/en-us/agent-framework/overview/agent-framework-overview
- CrewAI: https://docs.crewai.com/
- Pydantic AI: https://pydantic.dev/docs/ai/overview/
- Vercel AI SDK: https://ai-sdk.dev/docs/introduction
- Mastra: https://mastra.ai/docs
- Agno: https://docs.agno.com/
- n8n: https://docs.n8n.io/

---

## 9. 和另一份报告的关系

- 本文是**功能全景**：回答“常见 Agent 有哪些功能、成熟线是什么、你有哪些/缺哪些”。
- [agent-harness-maturity-2026-11.md](agent-harness-maturity-2026-11.md) 是**成熟度对标**：回答“你离 Claude Code/Codex/OpenCode 这类 harness 有多远、P0 缺陷是什么、路线怎么排”。
- 两份文档可以一起看：本文用来查缺补漏，另一份用来排优先级和验收。

> 本文的“项目现状”来自 2026-11-04 本地工作区只读检查；代码行号以当时工作区为准。未运行完整测试，不把“代码存在”写成“已验收”。

---

## 附录 A：功能点速查（70 项）

标记：● 必备　○ 加分　△ 可选；项目：✓ 已有　◐ 部分　✗ 缺失。

### A. 交互与入口（8）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| Web/桌面 UI | ● | ✓ Web，无桌面 |
| CLI/TUI | ○ | ✗ |
| IDE 插件 | ○ | ✗ |
| API/SDK | ● | ◐ REST/SSE/WS，无公开 SDK |
| 流式输出 | ● | ✓ SSE 13 类事件，无重连 |
| 富内容渲染（Markdown/代码/Diff/图片/来源） | ● | ✓ |
| 通知/命令面板/快捷键 | ○ | ◐ Toast + 部分快捷键 |
| 多语言/a11y/移动端 | ○ | ◐ zh/en，a11y 部分，无移动端 |

### B. 核心循环（10）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| ReAct/工具循环 | ● | ✓ |
| 最大步数/预算 | ● | ✓ `recursion_limit=500` |
| 循环检测 | ● | ✗ |
| 超时/重试/退避 | ● | ◐ 超时 + 仅超时重试 |
| 取消/中断 | ● | ✓ |
| 模型选择/温度/推理强度 | ● | ✓ 模型/温度，无 reasoning effort |
| 结构化输出 | ○ | ◐ 工具 schema + 摘要 JSON |
| 推理内容流 | ○ | ✓ `reasoning_content` |
| 指令层级 | ● | ◐ 记忆/Skills 有优先级，无 AGENTS.md |
| 反思/Replan | ○ | ✗ |

### C. 工具（16）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| schema/必填/枚举/服务端校验 | ● | ✓ |
| `tool_choice` 四态/工具子集 | ● | ◐ 模型驱动 |
| 并行工具调用 | ● | ✓ |
| 结果 envelope/错误语义 | ● | ✓ |
| 大结果截断/外部化/回读 | ● | ◐ 归档+回读，但消息里保留全文 |
| 工具搜索/延迟加载 | ○ | ✗ |
| 命名空间/版本 | ● | ◐ group 目录，无版本 |
| 代码执行/Shell/补丁/文件 | ● | ✓ 10 个文件工具 + run_command |
| 浏览器/Computer Use | ○ | ✗ |
| RAG/文件搜索/Web/视觉 | ● | ✓ |
| 元工具（todo/memory/subagent） | ○ | ◐ 只有 todo |
| 工具权限/风险/HITL | ● | ✓ |
| MCP | ● | ◐ stdio tools，无远程/resources |
| 工具审计 | ○ | ✓ |
| 幂等/去重 | ● | ◐ 一次性授权，无通用幂等 |
| 工具数量/上下文成本监控 | ● | ✗ |

### D. 上下文（10）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| token 计数/模型窗口 | ● | ◐ 启发式估算，有单位 bug |
| 自动压缩 | ● | ✓ |
| 手动压缩 `/compact` | ● | ✗ |
| 工具结果清理（context editing） | ● | ◐ microcompact 死代码 |
| 结构化摘要 | ○ | ✓ 9 字段 + source_refs |
| 压缩后重注入不变量 | ● | ✗ |
| 上下文用量 UI | ○ | ◐ token 统计，无占比 |
| prompt caching | ○ | ✗ |
| 检索上下文/附件/引用 | ● | ✓ |
| 预算分配/失败熔断 | ● | ◐ 有输出预留，无分段预算 |

### E. 记忆与知识（12）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| 短期/工作记忆 | ● | ✓ 消息 + checkpoint |
| 长期记忆 | ● | ◐ 4,000 字手动文本 |
| 分层（episodic/semantic/procedural） | ○ | ◐ procedural=Skills；episodic FTS 未接线；semantic InMemory |
| 用户 profile | ○ | ◐ 手动文本 |
| 项目/板卡 profile | ○ | ✗ |
| 自动抽取/写入闸门 | ● | ✗ |
| 去重/冲突/更新 | ● | ✗ |
| TTL/衰减 | ○ | ✗ |
| 检索注入/预算 | ● | ✗ 全量注入 |
| 记忆面板/治理 | ● | ◐ 只有文本框 |
| 删除传播/隐私 | ● | ✗ |
| RAG/引用/图谱/连接器 | ● | ✓ RAG；◐ 引用；✗ 图谱/连接器 |

### F. 会话与状态（8）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| 会话/消息持久化 | ● | ✓ |
| run/event 事件模型 | ○ | ◐ messages + audit，无统一 run |
| checkpoint | ● | ✓ HITL |
| resume | ● | ◐ 仅 HITL，无流式续传 |
| fork/branch/rewind | ○ | ◐ branch action + schema；无 UI/rewind |
| 崩溃恢复/持久执行 | ○ | ◐ 自研 writer，部分 |
| 后台/云/定时 | ○ | ✗ |
| 并发/多端/搜索/导出 | ● | ◐ 并发有全局队列问题；搜索/导出 ✓ |

### G. 规划与编排（10）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| plan mode | ● | ✗ |
| todo | ● | ✓ |
| goal/完成判据 | ○ | ✗ |
| 反思/Replan | ○ | ✗ |
| subagent | ● | ✗ |
| handoff/多代理/图 | ○ | ✗ |
| HITL/interrupt | ● | ✓ |
| worktree/工作区隔离 | ○ | ✗ |
| 并发/嵌套上限 | ● | ◐ recursion limit |
| A2A/AG-UI | △ | ✗ |

### H. 扩展生态（10）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| Skills | ○ | ✓ 标准包/固定 SHA/预算 |
| Plugins | ○ | ✗ 死代码 |
| Hooks | ○ | ✗ |
| 自定义命令/Agent 定义 | ○ | ✗ |
| 指令/规则文件（AGENTS.md/CLAUDE.md） | ● | ✗ |
| MCP tools | ● | ✓ stdio |
| MCP resources/prompts/sampling | ○ | ✗ |
| 市场/分发 | △ | ◐ GitHub 导入 |
| 版本/信任/哈希 | ● | ✓ 固定 SHA/内容哈希 |
| 集成（GitHub/Slack/Notion/Webhook） | ○ | ◐ 仅 GitHub Skills 导入 |

### I. 安全与执行（10）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| 权限模式 | ● | ✓ |
| allow/ask/deny | ● | ✓ |
| 参数级规则/保护路径 | ● | ◐ 只有黑名单 |
| OS/容器沙箱 | ● | ✗ |
| 资源限制 | ○ | ◐ 超时，无 CPU/内存 |
| Guardrails/内容安全 | ○ | ✗ |
| Prompt injection 防护 | ● | ◐ 提示词层 |
| PII/密钥/加密 | ● | ◐ Fernet+ACL，无 PII/日志脱敏不完整 |
| 审计 | ● | ✓ |
| 多租户/认证/配额 | ○ | ◐ 本地 token，无多用户/配额 |

### J. 可观测与质量（10）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| Tracing/OTel | ● | ✗ 自研 timing |
| 日志/指标 | ● | ✓ logs + Prometheus |
| Token/成本 | ● | ◐ token，无成本 |
| RAG 评测 | ● | ◐ 35 题仅 7 完整 |
| Agent 轨迹评测 | ○ | ✗ |
| Memory 评测 | ○ | ✗ |
| CI 回归 | ● | ✗ |
| 模型路由/降级 | ○ | ◐ compaction fallback |
| 缓存（prompt/semantic） | ○ | ◐ 搜索/embedding，无 prompt |
| 错误分类/降级 | ● | ✓ |

### K. 产品与交付（8）

| 功能 | 线 | 项目 |
| --- | --- | --- |
| Onboarding/文档 | ● | ✓ |
| Demo/截图 | ○ | ✓ 截图，无 GIF |
| 部署（本地/云/自托管） | ● | ◐ 仅本地 |
| 团队/多用户 | ○ | ✗ |
| 版本/CHANGELOG/release | ● | ✗ |
| CI/CD | ● | ✗ |
| 许可/品牌 | ● | ◐ LICENSE，无 description/topics |
| 社区/贡献 | ○ | ✗ |

---

**统计**：约 70 项中，✓ 约 25 项，◐ 约 25 项，✗ 约 20 项。  
**结论**：你已经覆盖了“工具 + RAG + 权限 + 产品 UI”的大部分；缺口集中在“记忆 + 恢复 + 编排 + 观测/评测 + 开源工程”。



