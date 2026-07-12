# Agent 链条优化调查报告

> 调研日期：2026-07-02
> 调研对象：opencode / OpenAI Codex CLI / Claude Code / Hermes Agent / OpenHands / OpenClaw
> 调研目的：为我们的 Python + FastAPI + LangGraph ReAct Agent（硬件 RAG + 代码生成 + 代码审查）寻找可借鉴的架构优化点
> 调研方法：4 个 subagent 并行调研 + WebSearch/WebFetch 获取 2026 年最新资料 + 本项目源码 gap 分析

---

## 一、当前架构概览（我们的 Agent 链条现状）

### 1.1 核心组件

| 组件 | 文件 | 现状 |
|------|------|------|
| Agent 循环 | `agent_factory.py` + LangGraph ReAct | `agent.astream(stream_mode=["messages","updates"])` 状态机驱动 |
| SSE 适配 | `sse_adapter.py` + `sse_helpers.py` | text/thinking/tool_call/tool_result/source/loop_detected 6 类事件，刚改为 async generator 逐个 yield source |
| 上下文保护 | `context_guard.py` | **仅 token 计数**，超限抛 `ContextLimitError`，无压缩/摘要 |
| 死循环检测 | `core/toolkit/loop_guard.py` | 4 种模式：repeat / no_progress / max_calls / cooldown，**无 Prompt 注入自愈** |
| HITL 审批 | `hitl_handler.py` | 4 步权限门控，**静态白名单**，无风险分级 |
| 风险分类 | `risk_classifier.py` | HIGH/MEDIUM 关键词分类（V1），用于 sandbox |
| 审计日志 | `audit_logger.py` | 30 天留存，自动清理 |
| 路径保护 | `path_guard.py` | 强制 deny: .git/.env/*.key 等 |
| 持久化 | MemorySaver（内存） | 按 thread_id 隔离，**无跨会话记忆** |

### 1.2 工具清单（12 个，6 域）

| 域 | 工具 | 权限 |
|----|------|------|
| retrieval | search_docs / web_search / list_kb_docs | 只读自动放行 |
| hardware | audit_pins / wiring | 只读自动放行 |
| workbench | render_code / render_wiring / render_safety_report | 只读自动放行 |
| code | generate_code | 只读自动放行 |
| file_ops | read_file / edit_file / write_file | 需审批 |
| execution | run_command | 需审批 |

### 1.3 已识别的痛点

1. **长会话必爆**：context_guard 只计数不压缩，50+ 轮对话直接撞 token 上限报错
2. **审批粒度太粗**：`ls`/`pwd` 和 `rm -rf` 一视同仁都要审批，体验差
3. **死循环只拦不治**：LoopGuard 检测到死循环就终止，不注入反思提示让 LLM 换方法
4. **无跨会话记忆**：每次新会话从零开始，用户反复问同类接线方案无法复用
5. **工具 schema 全量注入**：12 个工具的 schema 全塞进 system prompt，不管这次用不用
6. **无 Subagent 隔离**：search_docs 读 5 个 PDF 的中间结果全堆在主会话，context rot 严重
7. **API 错误直接断流**：token 超限直接抛异常终止会话，无恢复机制
8. **无会话搜索**：用户问"上次你帮我生成的代码呢"无法回答

---

## 二、参考项目调研结果

### 2.1 opencode（sst/opencode）— 借鉴价值 7.5/10

**架构**：TypeScript + Go 双语言，客户端/服务器架构，Effect.ts 异步编排，SQLite 持久化。

**核心亮点**：
- **ReAct + 隐藏 Agent**：compaction（上下文压缩）和 summary（历史摘要）做成隐藏 Agent，由 LLM 自己决定压缩什么，而非硬编码"保留最近 N 条"
- **细粒度权限模式匹配**：`bash` 工具支持 `"git *": "allow"` / `"rm *": "deny"` 通配符，last match wins，还有 `always` 审批记忆
- **doom_loop 权限化**：同一工具相同输入重复 3 次触发，默认 `ask`（问用户）而非直接终止
- **文件版本表 + /undo**：edit/write 工具执行前存快照，支持一键回滚
- **tool.execute.before/after 钩子**：插件可拦截/修改工具输入输出

**不适合我们**：客户端/服务器多客户端架构（我们单用户）、Effect.ts（Python 无等价物）、LSP 工具（非代码 IDE 场景）、MCP servers（封闭硬件场景）。

### 2.2 OpenAI Codex CLI — 借鉴价值 7/10

**架构**：Rust 实现，基于 OpenAI Responses API 的函数调用循环。

**核心亮点**：
- **Auto-Compaction**：每轮 `recompute_token_usage`，接近上限时自动压缩历史 + `encrypted_content` 保留模型理解
- **apply_patch diff 编辑**：模型只生成 diff 而非全量文件，token 省 60%+，变更可审计
- **Prompt Caching 友好的工具排序**：工具列表严格排序，不变的放前面，最大化前缀缓存命中
- **三层安全**：OS 级沙箱（Seatbelt/Landlock）+ 命令风险分析 + suggest/auto-edit/full-auto 三档审批
- **spawn_agent 子 Agent 并行**：主 Agent 可派生子 Agent 并行处理子任务

**不适合我们**：OS 级沙箱（Windows 不可用）、Responses API 的 encrypted_content（OpenAI 专有）、Rust 重写、shell 万能工具（硬件场景不安全）。

### 2.3 Claude Code — 借鉴价值 9/10

**架构**：TypeScript 实现，显式 `while(true)` 状态机 + function calling，1884 个文件。

**核心亮点**：
- **五层 Compact 机制**（最值得借鉴）：
  - microcompact：每轮检查，单工具结果替换/删除
  - snip：删除中间历史片段
  - autocompact：token > 阈值时全对话压缩为摘要
  - context collapse：细粒度折叠 + 还原
  - reactive compact：收到 PTL 错误时被动触发
  - 关键阈值：`AUTOCOMPACT_BUFFER_TOKENS=13000`、`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3` 熔断
- **错误扣留 + 恢复**：API 错误（PTL/max_output_tokens）先扣留不 yield，尝试 collapse drain + reactive compact 恢复，都失败才释放
- **StreamingToolExecutor 并发模型**：`isConcurrencySafe` 标记，安全工具并行执行，不安全工具串行，结果按序输出
- **Subagent 机制**：`.claude/agents/*.md` YAML 配置，独立上下文窗口，只汇报结论。description 是路由接口（Claude 不读正文）。model 字段做成本杠杆
- **Verification Agent**：非简单任务完成后，必须由独立对抗性 agent 验证

**不适合我们**：ANT-only 双轨、KAIROS 自主存活模式、undercover 模式、多客户端远程驱动。

### 2.4 Hermes Agent（NousResearch/hermes-agent）— 借鉴价值 7/10

**架构**：Python 实现，function calling 循环 + 自学习闭环，173K+ Stars。

**核心亮点**：
- **三层记忆系统**：会话记忆 + 持久记忆 + 技能记忆
- **FTS5 会话搜索**：SQLite + FTS5 全文本搜索，`session_search` 无需 LLM，4500× 更快且免费
- **自学习 Skill 闭环**：任务完成自动判断该不该存 → 提炼生成 `SKILL.md` → 下次类似场景自动调用 → 持续优化
- **插件钩子系统**：`pre_tool_call`（veto 权）/ `transform_tool_result`（重写返回）/ `transform_terminal_output`
- **/steer 命令**：运行中注入指令，不中断轮次、不破坏 prompt cache
- **Kanban Swarm**：Orchestrator 自动分解任务，root + parallel workers + gated verifier + synthesizer

**不适合我们**：17 个消息平台网关、MoA 多模型会诊（成本 3-5 倍）、6 种终端后端、Cron 定时任务。

### 2.5 OpenHands（All-Hands-AI/OpenHands）— 借鉴价值 8/10

**架构**：Python 实现，CodeAct（用可执行代码做一切 Action），60K+ Stars。

**核心亮点**：
- **EventStream 事件总线**：所有模块（Agent/Controller/Runtime/Memory/UI）通过 EventStream 解耦，事件分 Action/Observation/状态变更三类，订阅者各取所需
- **StuckDetector 五模式检测**：5 种卡死场景，特别是"最近 4 组 action-observation 对完全一致"的滑动窗口比较
- **Condenser 上下文压缩**：历史事件超阈值时自动摘要早期事件，保留近期完整 + 早期摘要
- **Security Analyzer 风险评级**：每个 Action 调 `security_risk()` 返回风险等级，高风险触发 confirmation_mode
- **Docker 沙箱 Runtime**：每会话一个容器，隔离文件系统/进程/网络

**不适合我们**：Docker 沙箱（本地单用户过重）、CodeAct 代码执行范式（硬件场景结构化工具更安全）、SWE-Bench 优化的反思循环。

### 2.6 OpenClaw — 借鉴价值 7.5/10

**架构**：Node.js 22+ / TypeScript 单进程守护，AI-Native 消息网关 + 本地 Agent Runtime，2026 年现象级项目。

**核心亮点**：
- **Skills 三层渐进披露**（最高价值）：YAML frontmatter（元数据，始终加载）→ SKILL.md（说明书，按需加载）→ 执行代码（调用时加载），省 30-50% token
- **Prompt 分段 token 预算**：SOUL(200-1k) / AGENTS-TOOLS(100-500) / Memory(上限 2k) / Skills(每 skill 100-500) / History，每段独立上限
- **Memory 持久化 + Hybrid Search**：`~/.openclaw/memory/` Markdown 文件，hybrid search 检索相关片段塞入 prompt（上限 2k）
- **循环检测 + Prompt 注入自愈**：537 次 exec 死循环案例的解法——检测到循环后注入提示让 LLM 改变策略
- **Tool Policy 默认拒绝白名单**：default deny + 白名单，而非 default allow + 黑名单

**不适合我们**：多 IM 渠道网关、Node.js 技术栈、SOUL.md 人格系统、自迭代 Loop Engineering、Cron 定时任务。

---

## 三、Gap 分析（对比表格）

| 能力维度 | 我们现状 | opencode | Codex CLI | Claude Code | Hermes | OpenHands | OpenClaw |
|---------|---------|----------|-----------|-------------|--------|-----------|----------|
| 上下文压缩 | ❌ 仅计数 | ✅ 隐藏 Agent | ✅ Auto-Compaction | ✅ 五层 Compact | ⚠️ 辅助模型 | ✅ Condenser | ⚠️ 分段预算 |
| 死循环检测 | ⚠️ 4 模式只拦 | ✅ doom_loop 问用户 | ❌ 隐式 | ⚠️ 无显式 | ⚠️ 拦截 | ✅ 五模式 | ✅ + Prompt 自愈 |
| 审批粒度 | ❌ 静态白名单 | ✅ 模式匹配 | ✅ 风险分级 | ⚠️ Verification | ⚠️ 钩子 | ✅ 风险评级 | ✅ default deny |
| 跨会话记忆 | ❌ 无 | ⚠️ 父子 session | ⚠️ load context | ✅ CLAUDE.md | ✅ 三层记忆 | ⚠️ Condenser | ✅ Memory+hybrid |
| Subagent | ❌ 无 | ⚠️ 多 Agent 类型 | ✅ spawn_agent | ✅ 独立上下文 | ✅ Kanban Swarm | ⚠️ delegate | ❌ |
| 工具钩子 | ❌ 无 | ✅ before/after | ❌ | ⚠️ stop hooks | ✅ 5 种钩子 | ⚠️ EventStream | ⚠️ Tool Policy |
| 会话搜索 | ❌ 无 | ✅ SQLite | ⚠️ load context | ✅ Memory Prefetch | ✅ FTS5 | ⚠️ EventStream | ✅ hybrid search |
| 错误恢复 | ❌ 直接断流 | ⚠️ /undo | ⚠️ compaction | ✅ 扣留+恢复 | ⚠️ 重试降级 | ⚠️ StuckDetector | ⚠️ Prompt 注入 |
| 工具描述优化 | ❌ 全量注入 | ⚠️ 静态 | ✅ 排序缓存 | ⚠️ 静态 | ⚠️ 静态 | ⚠️ 静态 | ✅ 三层渐进披露 |
| 文件版本/回滚 | ❌ 无 | ✅ /undo /redo | ⚠️ Git | ⚠️ Git | ✅ worktree | ⚠️ Git | ❌ |
| 自学习 Skill | ❌ 无 | ❌ | ❌ | ⚠️ Skill 加载 | ✅ 自学习闭环 | ❌ | ✅ Skills 系统 |
| 插件系统 | ❌ 无 | ✅ NPM 插件 | ⚠️ MCP | ⚠️ hooks | ✅ 钩子 | ⚠️ EventStream | ⚠️ Skills |

---

## 四、优化机会（按 ROI 排序）

### P0 — 立即做（低难度 + 高收益）

#### 优化 1：循环检测 + Prompt 注入自愈（增强 LoopGuard）
- **来源**：OpenClaw 537 次 exec 死循环案例 + opencode doom_loop
- **现状**：LoopGuard 检测到死循环发 `loop_detected` SSE 并终止
- **优化**：检测到死循环后，向消息历史注入一条 system 反思提示（"你已连续 N 次调用相同工具，请换一种方法"），让 LLM 自愈而非终止
- **难度**：低 | **改动范围**：`loop_guard.py` + `hitl_handler.py`（resume 时注入 prompt）
- **收益**：直接解决死循环顽疾，减少 Agent 误杀

#### 优化 2：StuckDetector 五模式增强（对标 OpenHands）
- **来源**：OpenHands `stuck.py`
- **现状**：LoopGuard 检测 4 种模式（repeat/no_progress/max_calls/cooldown）
- **优化**：增加 action-observation 配对哈希 + 滑动窗口（最近 N=4 轮），检测"动作-错误循环"等组合模式
- **难度**：低 | **改动范围**：`loop_guard.py` 纯逻辑增强
- **收益**：比单一重复检测更全面

#### 优化 3：Prompt 分段 token 预算（对标 OpenClaw）
- **来源**：OpenClaw Prompt 组装
- **现状**：context_guard 做总量保护，各段无独立预算
- **优化**：按段（system/tools/memory/history）分配 token 上限，超限段单独截断/摘要
- **难度**：低 | **改动范围**：`context_guard.py` 增加 token budget 配置
- **收益**：防止 RAG 结果吃满上下文，挤压工具描述和历史

#### 优化 4：FTS5 会话搜索（对标 Hermes）
- **来源**：Hermes `session_search`
- **现状**：MemorySaver 只存当前会话，无跨会话检索
- **优化**：SQLite + FTS5 全文本搜索，会话结束存 FTS5 表，新增 `/api/sessions/search` 接口 + Agent 加一个 `search_history` 工具
- **难度**：中低 | **改动范围**：新增 `session_search.py` + 一个新工具
- **收益**：用户问"上次你帮我生成的 STM32 代码呢"可回答，4500× 更快且免费

### P1 — 短期做（中难度 + 高收益）

#### 优化 5：分层 Compact 机制（对标 Claude Code）
- **来源**：Claude Code 五层 Compact
- **现状**：context_guard 只计数不压缩
- **优化**：先做 microcompact（每轮检查，删旧 tool_result）+ autocompact（token > 阈值时调 LLM 压缩历史为摘要）。关键阈值参考 Claude Code：`AUTOCOMPACT_BUFFER_TOKENS=13000`、`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3` 熔断
- **难度**：中 | **改动范围**：新增 `backend/src/agent/compact/` 模块 + graph 节点加预处理
- **收益**：长对话不再报错，token 成本下降

#### 优化 6：错误扣留 + 恢复 + 模型 fallback（对标 Claude Code）
- **来源**：Claude Code Withholding 机制
- **现状**：API 错误直接抛出终止会话
- **优化**：捕获 `ContextWindowExceededError` → 触发 summarizer 压缩 → 重试。模型 fallback 让用户配主+备两个模型
- **难度**：中 | **改动范围**：`sse_adapter.py` + `context_guard.py`
- **收益**：长会话遇到 token 超限不再断流，体验提升

#### 优化 7：细粒度权限模式匹配（对标 opencode）
- **来源**：opencode permission system
- **现状**：HITL 按工具类型审批，`run_command` 无论跑什么都要审批
- **优化**：权限支持按工具输入做模式匹配（`fnmatch`），如 `run_command` 的 `"ls *": "allow"` / `"rm *": "deny"`，last match wins。审批 UI 加"always 记住"选项
- **难度**：中 | **改动范围**：HITL 审批逻辑 + 前端审批 UI
- **收益**：HITL 体验大幅提升，低风险命令不再打扰用户

#### 优化 8：插件钩子系统（对标 Hermes）
- **来源**：Hermes `pre_tool_call` / `transform_tool_result`
- **现状**：工具执行是黑盒，无 before/after 钩子
- **优化**：定义 `Plugin` 抽象类 + `pre_tool_call` / `post_tool_result` 钩子，在工具执行器外层包一层 dispatcher。先做 `transform_tool_result` 钩子用于脱敏（屏蔽 API Key 泄漏到前端）
- **难度**：中 | **改动范围**：新增 `plugin_manager.py` + 各工具加 hook 调用点
- **收益**：安全收益高，防 API Key 泄漏，可扩展性强

### P2 — 中期做（中高难度 + 中高收益）

#### 优化 9：Subagent 隔离重上下文工具（对标 Claude Code）
- **来源**：Claude Code Subagent
- **现状**：12 工具全在主 ReAct 循环，search_docs 中间结果全堆主会话
- **优化**：用 LangGraph subgraph + 独立 state，定义 `doc_search_subagent`（把 PDF 检索中间结果隔离），主 graph 通过 `Send` API 委派
- **难度**：中高 | **改动范围**：新增 `subagents/` 目录 + 主 graph 加路由节点
- **收益**：保护主会话上下文，减少 context rot

#### 优化 10：Skills 三层渐进披露（对标 OpenClaw）
- **来源**：OpenClaw Skills 系统
- **现状**：12 个工具的 schema 全量塞进 system prompt
- **优化**：工具拆成"元数据层（name+一句话描述，常驻）+ 详细 schema 层（LLM 选定后注入）+ 实现层"。LangGraph 的 `bind_tools` 改造为两阶段绑定
- **难度**：中 | **改动范围**：工具注册 + agent_factory
- **收益**：长对话 token 成本降 30-50%

#### 优化 11：跨会话长期记忆（对标 OpenClaw + Hermes）
- **来源**：OpenClaw Memory + Hermes 持久记忆
- **现状**：每次新会话从零开始
- **优化**：会话结束抽取关键事实存入 `data/agent_memory/`（复用 ChromaDB 向量库），下次会话开始 hybrid search 注入
- **难度**：中 | **改动范围**：新增 `long_term_memory.py`
- **收益**：跨会话"越用越懂"，用户反复问同类问题可复用

### P3 — 长期做（高难度 + 长期收益）

#### 优化 12：自学习 Skill 闭环（对标 Hermes）
- **来源**：Hermes 自学习循环
- **现状**：无
- **优化**：任务完成后 skill 提炼节点（调 LLM 总结本次工具链 + 答案）→ skill 存储 → 下次任务开始 skill 匹配注入。先做"FAQ 缓存"简化版
- **难度**：高 | **改动范围**：新增 `skills/` 模块 + graph 加节点
- **收益**：同类问题不走完整 Agent，省 token + 加速

#### 优化 13：apply_patch diff 编辑（对标 Codex CLI）
- **来源**：Codex CLI apply_patch
- **现状**：edit_file/write_file 全量写入
- **优化**：增加 diff 解析能力（Python `difflib`），模型只生成变更部分
- **难度**：中 | **改动范围**：`edit_file.py` + 前端 diff 渲染
- **收益**：代码编辑 token 省 60%+，变更可审计

---

## 五、不适合我们的部分（明确排除）

| 设计 | 来源 | 不适合理由 |
|------|------|-----------|
| Docker 沙箱 Runtime | OpenHands | 本地单用户过重，需装 Docker，AGENTS.md 明确"不需要分布式" |
| OS 级沙箱（Seatbelt/Landlock） | Codex CLI | Windows 不可用，跨平台成本高，HITL 审批 + 命令风险分级已够 |
| CodeAct 代码执行范式 | OpenHands | 硬件场景结构化工具更安全，shell 自由发挥会涉及烧录/串口危险操作 |
| Responses API encrypted_content | Codex CLI | OpenAI 专有，我们"用户自配 API Key + 自选模型"不能用 |
| 多 IM 渠道网关 | OpenClaw/Hermes | 我们 Web UI 单前端，服务只监听 127.0.0.1 |
| MoA 多模型会诊 | Hermes | token 成本 3-5 倍，单用户本地部署不划算 |
| KAIROS 自主存活模式 | Claude Code | 我们是请求-响应模式，不需要 Agent 主动存活 |
| SOUL.md 人格系统 | OpenClaw | 硬件知识库专业工具需准确严谨，人格化削弱专业感 |
| 多 Agent 类型（build/plan/explore） | opencode | 增加用户配置复杂度，单一 ReAct + 12 工具已够 |
| Rust 重写 | Codex CLI | Python + LangGraph 生态成熟，硬件 RAG 对性能要求不高 |
| Cron 定时任务 | OpenClaw/Hermes | 交互式 RAG Agent，用户主动问才响应 |
| shell 万能工具 | Codex CLI | 嵌入式硬件场景 shell 可能涉及烧录/串口，必须封装专用工具 |

---

## 六、综合借鉴价值评分

| 项目 | 评分 | 最值得借鉴 | 一句话总结 |
|------|:----:|-----------|-----------|
| Claude Code | 9/10 | 五层 Compact + 错误扣留恢复 + Subagent | 工程级 Agent 教科书，每项直击我们痛点 |
| OpenHands | 8/10 | EventStream + StuckDetector + Condenser | 架构最完整的开源 Coding Agent |
| opencode | 7.5/10 | 隐藏 compaction Agent + 细粒度权限 + doom_loop | 权限系统和压缩 Agent 设计巧妙 |
| OpenClaw | 7.5/10 | Skills 三层渐进披露 + Prompt 分段预算 + 循环自愈 | 2026 现象级项目，token 优化设计精妙 |
| Codex CLI | 7/10 | Auto-Compaction + apply_patch + 命令风险分级 | 验证我们方向正确，工程细节值得补 |
| Hermes | 7/10 | FTS5 会话搜索 + 插件钩子 + 自学习 Skill | 长期演化方向，钩子系统短期收益高 |

---

## 七、总结

我们的 Agent 架构方向正确（ReAct + 12 工具 + SSE + LoopGuard + HITL），与所有参考项目思路一致。但在以下 8 个方面存在明显差距，按 ROI 排序：

1. **上下文压缩**（最大缺口）：只有计数没有压缩，长会话必爆
2. **死循环自愈**：只拦不治，应注入 Prompt 让 LLM 换方法
3. **审批粒度**：太粗，应按命令内容模式匹配
4. **跨会话记忆**：无，应做 FTS5 搜索 + 长期记忆
5. **错误恢复**：API 错误直接断流，应扣留 + 压缩 + 重试
6. **Subagent 隔离**：重上下文工具污染主会话
7. **工具描述优化**：全量注入，应三层渐进披露
8. **插件钩子**：无扩展点，应做 transform_tool_result 脱敏

建议按 P0 → P1 → P2 → P3 分批落地，详见配套计划文件 `2026-07-02-agent-chain-optimization-plan.md`。

---

**调研说明**：本报告由 4 个 subagent 并行调研（opencode / Codex CLI / Claude Code+hermes / OpenHands+openclaw）+ 本项目源码 gap 分析综合而成。信息来源为 2026 年最新官方文档、源码分析文章、GitHub Release Notes。落地前建议再到各仓库核对最新代码。
