# Hardware RAG Agent 架构对标与打磨顺序

2026-11-06 · `w1-baseline` · `f2f5bd7d`

本报告按本地优先的硬件知识助手和工作台排序。对标范围为 Claude Code、Codex、OpenCode 和 Pi，另用 RAGFlow 和 PlatformIO 比较各自的专业流程。清单保留旧全景附录逐行列出的 112 项，再补 18 个项目相关条目。旧附录标题写「70 项」，正文的 11 组实际合计 112 行，其中有些从不同角度重复，例如工具审计与安全审计，或前端重连与运行恢复。保留这些条目是为了能够和旧调查逐项对照。 [旧全景](agent-feature-atlas-2026-11.md#附录-a功能点速查70-项) · [十月对标](agent-capability-alignment-2026-10.html)

当前 HEAD 已合入滚动上下文、Recovery R1、Memory v1 和硬件档案。`d3d01058` 的全量与真实 HTTP 冒烟早于 R1 和 Memory v1 合入。上一轮在当前 HEAD 跑过 80 项定向测试和 35 条离线题库校验。本轮只查源码、项目记录和上游资料，未运行测试、启动应用、调用项目模型或操作硬件。表里的验收条件是后续设计建议。 [基线交接](baseline-2026-11-05-handoff.md#10-最终合并与真实验收2026-11-05)

## 一、各家选型及其代价

| Harness | 核心选型 | 适合借鉴的部分 | 移植时的取舍 |
| --- | --- | --- | --- |
| **Claude Code** | 会话内由权限模式和工具规则约束行为。计划确认是模式转换。文件 checkpoint 与会话回退配合，子代理拥有独立上下文，Hooks 拦截生命周期。 | 从同一套规则表达只读规划、精确审批和角色权限。将 Hook 的匹配范围、决策和审计设为明确契约。 | 官方可见的是行为与接口。Shell 沙箱、文件工具和外部进程覆盖不同，原生 Windows 的 Shell 也有不同边界。应分别验证产品里的每条执行路径。 [权限模式](https://code.claude.com/docs/en/permission-modes) · [沙箱](https://code.claude.com/docs/en/sandboxing) · [Hooks](https://code.claude.com/docs/en/hooks) · [Checkpointing](https://code.claude.com/docs/en/checkpointing) |
| **Codex** | App Server 把 Thread、Turn、Item、审批请求和事件暴露给客户端。协作模式、进度工具、Goal、子线程、Worktree 是独立对象。 | 学它的控制协议。中断作用于 Turn，订阅不发起新 Turn，计划文本与进度事件分开。Goal 的跨轮调度有单独状态和预算。 | 可以借这些对象的边界，沿用当前 REST/SSE 和 SQLite。独立 worktree 服务于并行改工程，沙箱负责 OS 权限。 [App Server](https://learn.chatgpt.com/docs/app-server) · [Goal](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex) · [Worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees) |
| **OpenCode V2** | 后台服务持有会话、执行与权限，客户端提交 inbox 工作并订阅事件。Plan/Build 复用 Agent 定义，权限用 `action/resource/effect`。子代理是归属明确的子会话。 | 学服务端持有运行、客户端可重连的分工，以及权限规则对资源的表达。把 Plan/Build 做成运行配置，可复用执行循环。 | V1、V2 的规则和工具名称不同。V2 Plan 的项目文件和 Shell 有各自规则。完整复制后台服务、inbox 和插件平台会扩大部署和迁移面。 [V2 服务架构](https://dev.opencode.ai/v2/docs/troubleshooting/) · [Agent](https://dev.opencode.ai/v2/docs/agents/) · [权限](https://dev.opencode.ai/v2/docs/permissions/) · [API](https://dev.opencode.ai/v2/docs/api) |
| **Pi / Pi Durable** | Pi 的会话是带父节点的 JSONL 树，模型使用活动分支，工具、上下文、界面经扩展 API 组合。Pi Durable 是独立的实验性方案，逐任务记录意图和检查点，只有声明可重放的调用才会重试。 | 学小核心和窄扩展点。用检查点表达明确恢复点，把重放安全定义到具体动作。子代理可复用会话机制。 | Pi 官方 Plan/Subagent 示例属于扩展。进程分开也可能访问同一个工作目录。项目已有 SQLite 会话和 LangGraph 检查点，适合保留现有所有权和删除机制。 [Pi 核心](https://pi.dev/docs/latest/how-pi-works) · [扩展](https://pi.dev/docs/latest/extensions) · [Pi Durable](https://earendil.com/posts/pi-durable/) |

OpenCode V2 的 Todo 还提供了一个小而实用的选型。TUI 从回退边界前最近一次成功的 `todowrite` 推导清单，空清单本身也是有效更新。本项目已持久化一部分 Run 事件，可以在服务端建立权威的当前清单视图，复用 `todo_update` 作为传输，给新旧客户端保留兼容期。采用事件投影时需要明确旧 Run 的保留、跨 Run 查找和清空语义。若这些数据不够完整，直接持久化当前快照更容易交付。 [OpenCode Todo 投影源码](https://github.com/anomalyco/opencode/blob/6666acc12cbbb2a640b1802f3fef609d3498ef52/packages/tui/src/util/todo.ts) · [本项目 Todo 工具](../../backend/src/agent/tools/groups/execution/todo_write.py)

## 二、落到本项目的架构决策

### 1. 运行所有权、恢复与观察

保留 FastAPI、SQLite、LangGraph 和现有 RunStore。第一步只对一条只读问答建立服务端运行所有权，复用 Run ID、事件序号和订阅入口。SSE 客户端断开后由服务端决定 Run 的生命周期，重连使用游标补读。当前 `persisted_event_generator` 的执行仍跟随响应迭代，`RunStore` 保存事件但这条接线还没有脱离连接。进程重启与客户端重连分开处理。前者第一版明确标记中断，后者可继续观察同一任务。需要跨进程自动继续时，再引入持久调度和领取所有权的机制。 [聊天入口](../../backend/app/api/chat_routes.py#L2302) · [Run 事件](../../backend/src/agent/run_events.py#L636) · [运行恢复](../../backend/app/api/runs.py#L189)

Run 记录执行尝试，Conversation 保存历史，Checkpoint 保存图的继续位置。现有前端和 API 分别承载这些信息，设计上可用 `run_id`、`call_id` 和来源标识串联。工具调用先记录授权与目标，完成后记录结果。进程崩溃且烧录结果未知时，展示待核对状态，先观察设备再决定动作。哪怕只读搜索可以再执行，新执行也可能遇到文档版本变化，应保留实际证据版本。先验证一次查询的完整时间线，再增加专用可观测页面。 [现有 TraceContext](../../backend/src/observability/trace.py) · [Claude 工具事件关联](https://code.claude.com/docs/en/monitoring-usage)

| 场景 | 第一版承诺 | 后续扩展条件 |
| --- | --- | --- |
| 刷新、短暂断网 | 保持同一 Run，用最后接收的序号补读。执行中与已完成两种情况都能收敛到服务端状态。 | 读链稳定后逐步接入耗时工具和构建。 |
| 用户停止 | 结束本次 Run，旧审批失效，保留已知结果。 | 支持后台进程后，进一步验收取消传到子进程和子代理。 |
| 进程重启 | 已保存的历史与事件可读，旧活跃 Run 显示中断。 | 需要可持续运行的任务有明确的检查点和安全重放规则后，再考虑持久队列。 |
| 审批期间更换目标或参数 | 拒绝旧批准并展示变更。 | 权限规则可复用，仍需对新动作单独判断。 |
| 烧录已发出但无结果 | 保存实际证据和未知状态，核对设备后才能决定下一步。 | 只有执行器提供可可靠查询的结果时才考虑自动对账。 |

### 2. 权限、规划、进度和目标

复用 `ToolSpec` 和现有审批链，在模型工具暴露与实际执行两个入口应用相同的规划策略。策略至少分清动作、目标与上下文。计划提交生成带版本的草案，用户批准该版本才启动执行 Run。后续针对文件、MCP 和烧录的审批仍按各自规则执行。计划变了，旧批准要失效。Todo 则保持简洁的进度状态，支持更新、清空和恢复。Goal 在这些机制稳定后单独承载持续结果、预算和终止条件。 [ToolRouter](../../backend/src/agent/core/toolkit/tool_router.py) · [Claude 计划确认](https://code.claude.com/docs/en/permission-modes#review-and-approve-a-plan) · [Codex 进度工具源码](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/plan.rs)

将工具调用许可与 OS 执行能力分开。先用现有保护路径、动作级审批和目标约束收口本地交付，再为需要运行任意脚本的 Shell 或扩展增加受限执行环境。设备操作由明确授权的本地适配器持有设备访问。沙箱验收时分别测文件、网络、进程和设备边界。 [项目命令规则](../../backend/src/agent/core/toolkit/command_policy.py) · [Codex Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)

### 3. 模型上下文和来源证据

沿用 SQLite 原历史、滚动摘要和版本化来源。模型窗口里保留最近原文、摘要及可回读标识，用户在 UI 中看到当前预算和压缩结果。Provider 已明确拒绝窗口大小时，可以评估接入现有一次重试辅助函数。先验证原文仍可回读、同一请求无副作用重放，再扩大入口。模型提示、记忆、Skills、文档内容和工具结果应保留各自的来源及信任等级。Memory v1 可先显示「这轮用到了哪些记忆」，板卡事实保留原始证据和验证记录。 [滚动压缩](../../backend/src/agent/compact/autocompact.py) · [Memory 注入](../../backend/src/agent/memory_context.py) · [OpenCode 压缩](https://dev.opencode.ai/v2/docs/compaction/) · [Claude Memory](https://code.claude.com/docs/en/memory)

OpenCode 将 provider、模型和选定的模型变体分开配置，Pi 通过模型注册表和适配器暴露能力。项目可以沿现有模型设置增加可检查的能力表，例如工具调用、视觉、推理、窗口与使用量字段。先为常用 provider 准备小型协议夹具，错误区分认证、限流、窗口和能力不支持。运行记录保存实际 provider、模型和降级结果。等有可比较的延时、调用和质量数据，再决定自动路由与缓存。 [OpenCode Providers](https://dev.opencode.ai/v2/docs/providers/) · [Pi SDK](https://pi.dev/docs/latest/sdk)

RAG 用同一份固定资料分开验收解析、检索、回答与引用跳转。来源标识应能回到文档版本与真实位置，区分物理页、印刷页和片段索引。硬件工作流沿用构建 ID，再绑定确定的固件摘要、目标设备和验证日志。工具成功返回、固件已上传、实机按预期运行是不同的结果。 [RAG 来源注册](../../backend/src/agent/tools/groups/retrieval/source_registry.py) · [构建记录](../../backend/app/api/build_registry.py) · [RAGFlow 检索测试](https://ragflow.io/docs/v1.0.0-rc3/retrieval_testing) · [PlatformIO 主机与目标设备测试](https://docs.platformio.org/en/stable/advanced/unit-testing/runner.html#test-types)

### 4. 变更交付、子代理和扩展

用现有 Explorer Diff 和私有快照先实现按 Run 看变更。显示改动的来源和验证结果，撤销仍需检测冲突。上下文隔离用子运行解决，代码隔离用 worktree，权限隔离由执行器实现。第一版子代理只承担可独立验证的只读工作，运行由父级取消，结果带来源回传。出现并行写同一项目的实际需求时，再增加 worktree 所有权、基线和合并流程。 [现有快照](../../backend/src/agent/tools/groups/file_ops/_git_snapshot.py) · [OpenCode Snapshots](https://dev.opencode.ai/v2/docs/snapshots/) · [Claude Subagents](https://code.claude.com/docs/en/sub-agents) · [Codex Worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)

现有 Skills 和本机 MCP 分别负责流程知识与外部工具。成熟化先补包来源、版本、加载诊断和本机 MCP 生命周期。Hook 的同步决策、异步观察、超时和信任需要各自定义。扩展代码如有执行权限，需受同一授权与执行策略约束。工具目录规模达到需要时，再做按需加载，量化 schema 的上下文占用和选错工具的比例。 [Skills API](../../backend/app/api/skill_routes.py) · [本机 MCP](../../backend/src/mcp/manager.py) · [Codex Hook 信任](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks) · [Claude Tool Search](https://code.claude.com/docs/en/mcp#configure-tool-search)

## 三、实施排序和退出条件

排序依据是本地用户路径、错误代价和依赖范围。阶段标记表示该能力下一次值得投入的时间点，已有能力可以在前面继续使用。单项优先级见清单。权限缺陷一旦证实，进入对应执行链的首批修复。

| 顺位 | 阶段 | 交付与退出条件 |
| --- | --- | --- |
| 1 | **P0 只读主链与发布基线** | 固定同一提交和文档，覆盖首次启动、带引用的问答、刷新订阅、取消、来源点回原文、重启读回。以 `run_id` 和事件序号判断断线有无重复执行。保留一组可重复的离线回归，单列真实浏览器、模型与硬件结果。01、02 可分别查检索与恢复，00 协调共享接口。 |
| 2 | **P1 现有写入、硬件与交付** | 先收口现有写文件、命令、编译、烧录与重连。按 Run 汇总 Diff 和产物，固件与目标设备可核对。误操作和未知结果能明确停止。04 管授权与执行，05/06 分别交付文件和实机证据。 |
| 3 | **P2 人可控的 Plan/Todo 和现有功能细化** | 计划版本、确认、权限切换、Todo 恢复与清空形成闭环。同时收口使用频率高的上下文、Memory、Skills 和模型设置问题。用任务数据决定手动压缩、用量面板和优化的投入。 |
| 4 | **P3 有界委派与隔离** | 只读子代理能在独立上下文复核一个具体问题，带证据返回，父级可取消并能看用量。只有出现并行写工程需求才开启产品内 worktree，受限执行环境先覆盖所引入的命令边界。 |
| 5 | **P4 Goal、后台和扩展** | 单 Agent Goal 有证据、预算、暂停与停止。运行生命周期支撑之后再增加本地队列或调度。MCP 的连接治理、远程 OAuth、Hooks 与工具搜索分别按实际使用量和信任边界进入。 |
| 6 | **P5 按需求开通** | CLI/SDK、IDE、图形工作流、云端、多租户、浏览器操作、市场及跨代理协议，每项单独用用户场景和维护成本决策。 |

建议首张任务仍然是「固定手册的带引用问答，在生成中刷新后找回同一 Run」。它会直接检验 P0 的所有权、恢复、来源身份和 UI 投影。完成后，硬件试点可以沿用 Run 和证据记录，另行定义设备状态与结果未知时的处理。

## 四、24 个能力域索引

| 旧全景能力域 | 新清单位置 | 旧全景能力域 | 新清单位置 |
| --- | --- | --- | --- |
| 01 Agent Loop / 工具路由 | B、C | 13 Guardrails / Injection | I06–I08 |
| 02 工具协议 / 生态 | C、H | 14 RAG / Sources | E12、L12–L14 |
| 03 Skills | H01、H08–H09 | 15 Multimodal / Browser / Computer Use | C09–C10、L15 |
| 04 Plugins / Hooks / Extensions | H02–H04 | 16 Subagents / Worktrees | G05–G09 |
| 05 指令 / 规则系统 | B09、H05 | 17 Workflows / Multi-agent | G04–G06、G09–G10 |
| 06 MCP | C13、H06–H07、L18 | 18 Background / Cloud / Scheduling | F07、L16 |
| 07 Context / Compaction | D | 19 Observability / Tracing | J01–J03、L17 |
| 08 Memory / History | E、F08 | 20 Eval / Quality | J04–J07 |
| 09 Session / Run / Checkpoint | F | 21 Cost / Model Routing / Caching | B06、D08、J03、J08–J09 |
| 10 Plan / Todo / Goal | G01–G04 | 22 UX / Interaction | A、F08 |
| 11 Permissions / HITL | C12、G07、I01–I03 | 23 Deployment / OSS | K |
| 12 Sandbox / Execution | C08、I04–I05 | 24 Security / Privacy / Multi-user | E11、I08–I10、K04 |

## 五、逐项清单

状态按当前源码证据分为「已有」「部分」「缺口」「待核」。已有表示能找到接线，部分表示接线或闭环有边界，缺口表示此次源码检查未找到相应产品入口，待核表示需要进一步区分配置、调用路径或验收。阶段为建议顺序，「维护」表示随所在链回归。

### A. 交互与入口

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| A01 | Web / 桌面 UI | 已有 Web 工作台。先打磨主界面状态，桌面打包另议。 | P0 / P5 |
| A02 | CLI / TUI | 缺口。现有启动脚本可启动服务，交互 CLI 取决于真实使用场景。 | P5 |
| A03 | IDE 插件 | 缺口。优先稳定项目文件与审查接口，再做客户端适配。 | P5 |
| A04 | API / SDK | 部分。已有 REST、SSE、WS，先维护接口契约和版本兼容。 | P0 / P5 |
| A05 | 流式输出 | 部分。有 SSE 和 Run 补读，收口断开、重复事件、取消与终态。 | P0 |
| A06 | 富内容渲染 | 已有 Markdown、代码、Diff、图像、来源。重点校验历史恢复和点击证据。 | P0 |
| A07 | 通知、命令面板、快捷键 | 部分。有通知及快捷操作。先统一异常和审批提示。 | P2 |
| A08 | 多语言、a11y、移动端 | 部分。有中英文。键盘焦点、读屏和窄窗口逐步覆盖主流程。 | P2 / P5 |

### B. 核心循环

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| B01 | ReAct / 工具循环 | 已有 LangGraph `create_agent` 接线，沿用。 | 维护 |
| B02 | 最大步数与预算 | 已有 `MAX_RECURSION=500`，另有上下文预算。Run 时间、调用和用量预算分开。 | P0 / P4 |
| B03 | 循环检测 | 缺口。先利用轨迹定位重复工具与无进展循环，再加退出规则。 | P2 |
| B04 | 超时、重试、退避 | 部分。工具与模型有各自路径，先明确哪些失败可以安全重试。 | P0 |
| B05 | 取消与中断 | 已有取消入口，收口状态传播和进程退出。 | P0 |
| B06 | 模型、温度、推理强度 | 部分。模型与温度有配置，各 provider 的推理强度映射需逐路核验。 | P2 |
| B07 | 结构化输出 | 已有工具与摘要结构。按场景校验工具结果、计划和证据。 | 维护 |
| B08 | 推理流 | 已有适配路径。不同 provider 按能力显示，统一结束与错误状态。 | P2 |
| B09 | 指令层级 | 部分。现有系统提示、Skills 和记忆需明确优先级与来源。 | P2 |
| B10 | 反思 / Replan | 缺口。先对计划变更建显式版本和审批，再测复杂失败恢复。 | P2 / P4 |

### C. 工具

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| C01 | Schema、必填、枚举和校验 | 已有 `ToolSpec` 与服务端校验。外部工具 schema 固定到调用。 | 维护 |
| C02 | `tool_choice` 四态与工具子集 | 部分。已有请求级工具组装。先确保只读子集与执行授权一致。 | P2 |
| C03 | 并行工具调用 | 部分。图层有工具执行能力，按写路径、设备和配额验证互斥。 | P3 |
| C04 | 结果 envelope 与错误语义 | 已有结构。把拒绝、取消、失败和结果未知明确区分。 | P0 |
| C05 | 大结果裁剪、外部化与回读 | 部分。已有观察归档与读回，收口证据完整性和敏感正文边界。 | P0 / P2 |
| C06 | 工具搜索与延迟加载 | 缺口。工具清单过大时按 schema 成本和工具命中率判断投入。 | P4 |
| C07 | 命名空间与版本 | 部分。有工具分组和 MCP 适配，明确名称冲突与版本变动。 | P4 |
| C08 | 代码执行、Shell、补丁与文件 | 部分。有文件和命令主路径，补丁实现的各入口可达性需分别核对。 | P1 |
| C09 | 浏览器与 Computer Use | 缺口。网页读取和 GUI 操作分别授权。 | P5 |
| C10 | RAG、文件搜索、Web、视觉工具 | 已有。将来源与对应工具调用关联。 | P0 |
| C11 | 元工具 | 部分。已有 Todo 与 Memory 工具，Subagent 排 G05。 | P2 / P3 |
| C12 | 工具权限、风险与 HITL | 已有。按动作和资源验证批准、拒绝、过期及中断。 | P1 |
| C13 | MCP | 部分。本机 stdio 工具已接入，生态扩展排 H06–H07、L18。 | P4 |
| C14 | 工具审计 | 已有。补齐 Run 与调用关联，控制正文采集。 | P0 |
| C15 | 幂等与去重 | 部分。有审批一次性消费等控制。分别定义只读重试和有副作用调用。 | P0 / P1 |
| C16 | 工具数和上下文成本 | 部分。有工具列表和 token 用量。补 schema 占用和按调用归因。 | P2 / P4 |

### D. 上下文

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| D01 | Token 计数和窗口 | 已有注册窗口、预算与回退估算。验证高频 provider 的误差。 | P0 |
| D02 | 自动压缩 | 已有发送前与图内路径。验收压缩前后引文和上下文连贯。 | P0 |
| D03 | 手动 `/compact` | 缺口。对需要主动收缩窗口的长会话提供可观察入口。 | P2 |
| D04 | 工具结果清理 | 部分。已有归档和窗口处理，逐个确认哪些结果可以只留引用。 | P2 |
| D05 | 结构化摘要 | 已有版本化摘要和来源标识。维护摘要与原文的一致性。 | 维护 |
| D06 | 压缩后重注入不变量 | 部分。已做保文与来源约束。真实长会话验证旧证据能否读回。 | P0 |
| D07 | 上下文用量 UI | 部分。已有用量数据，补窗口占比、来源和压缩前后变化。 | P2 |
| D08 | Prompt caching | 待核。区分 provider 自动命中与本项目显式控制。 | P4 |
| D09 | 检索上下文、附件与引用 | 已有。优先保证版本、页码和会话恢复。 | P0 |
| D10 | 预算分配、溢出与失败 | 部分。已有保文及预算失败。provider 超长后单次恢复需接线和验证。 | P2 |

### E. 记忆与知识

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| E01 | 短期与工作记忆 | 已有会话历史、checkpoint 和滚动摘要。 | 维护 |
| E02 | 长期记忆 | 已有 Memory v1 和旧手动记忆。补本轮使用来源。 | P2 |
| E03 | 分层记忆 | 部分。历史、结构化记忆和 Skills 各有路径，统一展示各自来源。 | P2 |
| E04 | 用户 profile | 部分。有用户范围条目。区分偏好、事实和项目配置。 | P2 |
| E05 | 项目与板卡 profile | 已有独立硬件档案。补证据位置、修订与审核。 | P1 / P2 |
| E06 | 自动抽取与写入闸门 | 部分。已有显式写入与校验，自动提案或抽取未形成产品路径。 | P4 |
| E07 | 去重、冲突与更新 | 部分。有更新与替代关系，补冲突展示和纠正后的生效范围。 | P2 |
| E08 | TTL 与衰减 | 部分。有过期字段。按应用场景区分有效期和自动衰减策略。 | P2 |
| E09 | 检索注入与预算 | 已有预算、检索和硬件档案排除。补命中解释。 | P2 |
| E10 | 记忆面板与治理 | 已有设置和档案面板。展示来源、编辑、遗忘和使用范围。 | P2 |
| E11 | 删除传播与隐私 | 部分。已有删除/epoch 等防复活机制，验证各持久介质和派生缓存。 | P0 |
| E12 | RAG、引用、图谱和连接器 | 部分。RAG 与引用已接线，图谱检索和更多连接器按需求。 | P0 / P5 |

### F. 会话与状态

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| F01 | 会话和消息持久化 | 已有服务端历史及兼容路径。验收首问、旧数据和重启。 | P0 |
| F02 | Run / Event 模型 | 已有 R1。统一一次运行的所有权、事件顺序和终态。 | P0 |
| F03 | Checkpoint | 已有 LangGraph 持久路径与审批续跑。验收不同作用域的继续点。 | P0 |
| F04 | Resume | 部分。有事件补读和审批恢复边界。重连、审批后继续、崩溃重跑分别处理。 | P0 |
| F05 | Fork、Branch 与 Rewind | 部分。已有会话分支入口和树组件，分支与文件恢复分别验证。 | P2 |
| F06 | 崩溃恢复与持久执行 | 部分。重启可标中断和读回。可安全续跑的任务按动作范围开放。 | P0 / P4 |
| F07 | 后台、云与定时 | 部分。已有部分专用任务。通用任务宿主、持久队列和云需独立选型。 | P4 / P5 |
| F08 | 并发、多端、搜索与导出 | 部分。有历史搜索和导出。先验收并发写和切换会话，再做多端。 | P0 / P5 |

### G. 规划与编排

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| G01 | Plan Mode | 缺口。工具层只读，计划版本与批准可追溯。 | P2 |
| G02 | Todo | 部分。已有实时清单。补持久投影、清空和刷新恢复。 | P2 |
| G03 | Goal 与完成判据 | 缺口。明确证据、续轮条件、预算和暂停。 | P4 |
| G04 | 反思 / Replan | 缺口。先允许显式修改计划和恢复，再考虑自动重排。 | P2 / P4 |
| G05 | Subagent | 缺口。第一版只读任务，独立上下文和可取消子运行。 | P3 |
| G06 | Handoff、多代理与工作流图 | 缺口。需求成熟后再支持写入交接及用户自定义编排。 | P5 |
| G07 | HITL / Interrupt | 已有工具审批与中断。审批记录和执行快照分别验收。 | P1 |
| G08 | Worktree 与工作区隔离 | 缺口。并行修改工程时引入受管理工作区和交付。 | P3 |
| G09 | 并发与嵌套上限 | 部分。有图步数上限。对子运行定义并发、深度、用量和回收。 | P3 |
| G10 | A2A / AG-UI | 缺口。待确有第三方 Agent 或 UI 消费者时适配。 | P5 |

### H. 扩展生态

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| H01 | Skills | 已有标准包、固定提交、快照及导出。补来源与加载诊断。 | P2 |
| H02 | Plugins | 缺口。按明确的分发需求确定包清单、权限和卸载。 | P4 / P5 |
| H03 | Hooks | 缺口。先做少量事件，明确阻断、超时和执行身份。 | P4 |
| H04 | 自定义命令与 Agent 定义 | 缺口。先用固定只读角色验证，之后再开放配置。 | P3 / P5 |
| H05 | 项目指令文件 | 缺口。为产品内 Agent 定义发现范围、合并优先级及信任。 | P2 |
| H06 | MCP tools | 已有本机 stdio。完善启停、连接诊断、授权与配置恢复。 | P4 |
| H07 | MCP resources、prompts、sampling | 缺口。分开评估读取资源、模板和服务端模型调用。 | P4 / P5 |
| H08 | 市场与分发 | 部分。已有 GitHub 导入。市场随第三方维护需求。 | P5 |
| H09 | 版本、信任和哈希 | Skills 已有固定版本与哈希。将信任模型扩展到安装和可执行扩展。 | P2 / P4 |
| H10 | GitHub、Slack、Notion 与 Webhook | 部分。已有 GitHub Skills 导入路径，业务集成按场景推进。 | P5 |

### I. 安全与执行

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| I01 | 权限模式 | 已有当前权限模式。Plan 只读策略排 G01。 | P1 / P2 |
| I02 | Allow / Ask / Deny | 已有。补审批决策、调用身份和超期的端到端验证。 | P1 |
| I03 | 参数级规则与保护路径 | 已有路径及命令规则。分别检查文件、外部工具、设备与网络入口。 | P1 |
| I04 | OS 或容器沙箱 | 缺口。先确定 Windows 文件、网络、进程和设备约束，再接受限执行器。 | P3，扩大执行权前 |
| I05 | 资源限制 | 部分。有超时等限制。对进程树、输出、CPU、内存与设备租约分别设边界。 | P1 / P3 |
| I06 | Guardrails 与内容安全 | 部分。已有工具和内容边界。高风险领域输出增加结构检查。 | P1 |
| I07 | Prompt injection 防护 | 部分。有提示边界和权限闸门。优先验证不可信 PDF、网页和 MCP 内容不能提升权限。 | P1 |
| I08 | PII、凭证、加密和日志 | 部分。已有凭证及脱敏措施。核对导出、日志与工具归档的敏感字段。 | P1 |
| I09 | 审计 | 已有。用同一调用 ID 找到决策来源、目标和结果。 | P0 / P1 |
| I10 | 多租户、认证与配额 | 部分。有本机认证及 owner 边界。多租户另需租户隔离和配额。 | P0 / P5 |

### J. 可观测与质量

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| J01 | Tracing / OTel | 部分。有本地 trace 和 Run 事件。先能按一次任务串起证据，再做 OTel 导出。 | P0 / P4 |
| J02 | 日志与指标 | 已有。让故障定位到模型、检索、审批、工具或客户端。 | P0 |
| J03 | Token 与成本 | 部分。有用量统计。区分估算、provider 返回、缓存及真实计费。 | P2 |
| J04 | RAG 评测 | 已有 35 条题库与离线门禁，完整内容验收需当前版本结果。 | P0 |
| J05 | Agent 轨迹评测 | 部分。有 Run 记录与测试。补任务级行为标准和副作用检查。 | P0 / P2 |
| J06 | Memory 评测 | 部分。有运行和验收用例。补长期冲突、错误记忆和删除回读。 | P2 |
| J07 | CI 回归 | 已有 Windows/Ubuntu CI。当前发布提交和真实场景结果分别记录。 | P0 |
| J08 | 模型路由与降级 | 部分。有 provider 选择与部分回退。定义明确失败与可接受降级。 | P2 / P4 |
| J09 | Prompt / 语义缓存 | 部分。已有检索等缓存。复用模型结果前需核对来源版本与权限。 | P4 |
| J10 | 错误分类与降级 | 已有部分错误分类。为每类用户可见故障提供下一步和证据。 | P0 |

### K. 产品与交付

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| K01 | 首启引导与文档 | 已有 README 和启动指引。验收空白机、模型首问与升级读回。 | P0 |
| K02 | Demo 与截图 | 已有截图。随能复现的知识问答和硬件路径更新。 | P2 |
| K03 | 本地、云及自托管部署 | 已有本机运行。优先稳定 Windows 安装、迁移和回滚。 | P0 / P5 |
| K04 | 团队与多用户 | 缺口。需求成立后与租户、权限和资源治理一起排。 | P5 |
| K05 | 版本、CHANGELOG、Release | 部分。有基线 tag 与发布清单。收口统一版本、迁移与发布记录。 | P1 |
| K06 | CI/CD | 部分。CI 已有，自动发布和分发验收另行决策。 | P1 / P5 |
| K07 | 许可与品牌 | 已有许可文件，发布时核对仓库描述和授权表述。 | P1 |
| K08 | 社区与贡献 | 部分。已有项目约定与开发资料。外部贡献流程随公开发布完善。 | P5 |

### L. 硬件、工作区、产物和证据补项

| ID | 功能 | 现状与下一步 | 阶段 |
| --- | --- | --- | --- |
| L01 | 设备扫描与身份 | 已有扫描。操作前将端口与当前目标设备身份核对。 | P1 |
| L02 | 串口与重连 | 已有连接与日志的实机记录，自动重连待实体板验收。 | P1 |
| L03 | 编译、产物标识与环境 | 已有 PlatformIO 和 `build_id`。补输入版本、环境和固件摘要。 | P1 |
| L04 | 烧录与实机验证 | 部分。已有入口和审批。补目标核对、断开时状态与启动验证。 | P1 |
| L05 | 引脚审计与接线图 | 已有。将板卡变体、引脚判断、出处和图关联起来。 | P1 |
| L06 | Explorer 根目录、监听、搜索、回收站 | 已有。验证权限、外部修改和恢复冲突。 | P1 |
| L07 | 持久终端与进程归属 | 部分。有命令工具和构建日志。PTY 与长进程控制按实际场景。 | P4 |
| L08 | Run 级 Diff、审查与撤销 | 部分。有文件 Diff 和撤销，补 Run 聚合及人工改动冲突。 | P1 |
| L09 | Git 分支、提交、PR 与交付 | 部分。有 Git Diff 和私有快照。产品内 Git/PR 入口按需求。 | P3 / P5 |
| L10 | 代码智能、LSP 与符号导航 | 部分。有文本/文件搜索。仓库开发用 GitNexus 与产品内符号工具分别看待。 | P5 |
| L11 | 产物目录、预览、下载与复核 | 部分。有代码、接线图、预览和导出，补按 Run 查产物版本。 | P1 |
| L12 | 文档导入、解析、索引生命周期 | 已有。能区分解析、索引、取消、失败、重跑与删除。 | P0 |
| L13 | 混合检索、重排与诊断 | 已有路径。用固定问题检查型号噪声、重复和召回排序。 | P0 |
| L14 | 原文页定位、版本与来源恢复 | 部分。已有来源注册。统一物理页、印刷页和片段位置的展示。 | P0 |
| L15 | 附件、PDF、图片与媒体边界 | 部分。已有附件和视觉工具。分别声明文件类型、上限和解析能力。 | P2 |
| L16 | 后台任务、队列、通知与远程控制 | 部分。有专用后台工作。通用任务状态与观察先在本机验收。 | P4 / P5 |
| L17 | 实机、真实模型、浏览器与离线门禁 | 部分。有各类历史证据。每次发布记录同一提交、范围和失败条件。 | P0 / P1 |
| L18 | MCP 配置持久化、远程传输、OAuth 与健康状态 | 部分。本机配置在进程内。持久配置与健康诊断先于新传输。 | P4 |

清单的主要本地入口为 [聊天与 Run](../../backend/app/api/chat_routes.py)、[工具工厂](../../backend/src/agent/agent_factory.py)、[前端会话](../../frontend/src/stores/useChatStore.ts)、[项目工作区](../../backend/app/api/explorer_routes.py)、[知识库](../../backend/app/api/kb_routes.py)、[硬件](../../backend/app/api/hardware_routes.py)、[CI](../../.github/workflows/ci.yml) 和 [发布清单](../release-checklist.md)。
