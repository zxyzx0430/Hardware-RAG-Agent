# Hardware RAG Agent 项目说明

> 最近检查：2026-09-26

## 项目简介

本项目是在个人电脑本地运行的硬件知识助手，包含芯片手册检索、带来源的问答、Agent 工具调用和硬件工作台。主要组成是 FastAPI 后端与 React / TypeScript 前端。

## 长期功能线

线程按用户能完成的整条流程分工，同一条线负责相关前端、后端、接口和测试。编号表示长期职责，不表示六条线程必须同时开工；通常同时运行 2～3 个实现任务。

| 编号 | 功能线 | 负责范围 |
| --- | --- | --- |
| 00 | 主控与集成 | 确定目标、协调共享接口与文件、审查交接和测试结果、整合分支。 |
| 01 | 知识库与 RAG | 文档上传、解析、索引、检索、来源数据与黄金集评测，以及知识库界面。 |
| 02 | 聊天、会话与设置 | 提问与 SSE 流式显示、消息和会话持久化、模型与用户设置、刷新恢复。 |
| 03 | Agent 决策 | 任务规划、上下文、工具选择、失败恢复和执行轨迹；决定调用什么，不包揽各领域工具的实现。 |
| 04 | 工具扩展与安全执行 | ToolSpec/ToolRouter、Skills/MCP、权限与 HITL、路径和命令限制、沙箱与审计。 |
| 05 | 项目文件与代码工作区 | 文件树、编辑、预览、差异、文件监听与项目文件操作；执行权限由 04 线提供。 |
| 06 | 硬件工作台 | 设备扫描、串口、编译烧录、接线图、引脚审计和实机验证。 |

跨线交接按接口划分：01 提供检索结果和来源字段，02 展示并保存对话；03 发起工具调用，04 注册、校验、授权和审计，01/05/06 实现各自领域的动作；02 负责统一的 SSE 传输和用户可见状态。涉及 `chat_routes.py`、`useChatStore.ts`、共享 API 客户端或类型、`docs/api-contract.md`、本文件时，先向 00 说明变更字段、受影响线程及兼容方式，同一时刻只由一个线程修改同一共享文件。

新线程的第一轮先只读熟悉项目，核对当前代码和测试，不继承旧计划里的完成状态。启动提示词应包含职责、单次目标、主控任务 ID、相关线程、工作树或分支及交付要求。线程通过 Codex 任务列表与任务摘要查看其他线程的进度；工具不可用时，在交接报告中写明依赖，由 00 转达。不同工作树不会自动共享未提交文件，实现前先核对所读文档和代码的版本。

每次交接说明目标与实际结果、改动文件、接口变化及受影响线程、运行的测试、尚未验证的部分、阻塞或下一步。实机验证必须与模拟或自动化测试分开记录。安装、CI 和总体验收由 00 按任务安排；各功能线仍负责自己改动的测试。

## 开工先看

1. 查看 `git status --short --branch`、当前提交和差异，先保护现有改动。
2. 阅读 README、下方对应的正式文档和本轮任务说明。
3. 查看同项目正在进行的任务及接口依赖；准备修改共享文件时先与 00 协调。

## 正式文档

| 文件 | 用途 |
| --- | --- |
| `README.md` | 安装、启动和主要功能 |
| `docs/completed.md` | 当前实现状态与验证边界，不是开发计划 |
| `docs/testing.md` | 测试命令、必留样例、运行产物和测试记录 |
| `docs/api-contract.md` | 前后端接口契约 |
| `docs/architecture/architecture-map.md` | 当前架构总览 |
| `docs/git-cheatsheet.md` | 安全查看和恢复 Git 改动 |

不要把旧 Phase 计划、线程 TODO 或历史审查报告当成当前开发路线图。

## 测试与数据

- 可复用的 RAG 黄金集和输入文档见 `backend/tests/rag_eval/` 与 `data/test_docs/*.md`，这些文件需要保留。
- 自动评测、数据库、向量索引、PDF 和本地配置不属于公开测试夹具；按 `.gitignore` 的具体路径规则留在本机。
- 运行命令和最新有记录的结果见 `docs/testing.md`。没有实际运行的测试不得写成已通过。
- 已用 COM5 上的真实开发板验证串口连接、接收日志和正常断开；烧录和烧录后自动重连尚未做实机验证。

## 修改约定

- 用户限定的任务范围优先；没有明确要求时，不改产品功能、依赖或测试逻辑。
- 修改接口时同步更新 `docs/api-contract.md`。
- 修改代码符号前先做 GitNexus 影响分析；不确定或高风险结果要进一步核实。准备提交前检查图谱变更。
- 代码和注释使用英文；对用户默认使用中文。
- 保留 Conventional Commits 格式。没有明确要求时，不提交、不推送。
- 清理文件时逐个确认用途，使用精确路径；不要运行 `git clean` 或覆盖本机材料。
- 文档中不得加入密钥、个人内容或运行日志里的隐私数据。

## 常用开发命令

开发环境和服务启动步骤以 README 为准。后端测试、前端测试、静态检查和构建命令以 `docs/testing.md` 为准。

Windows 上运行 GitNexus CLI 时使用 `scripts/gitnexus.ps1`，例如 `./scripts/gitnexus.ps1 status`。该脚本固定 GitNexus 1.6.12，并向 pnpm 显式授权所需的原生构建包；直接运行 `.gitnexus/run.cjs` 可能因 pnpm 拒绝未授权的 Tree-sitter 语法包而失败。修改代码符号前先用此脚本运行 `impact`，准备提交前运行 `detect-changes`。

当前本机 GitNexus 图谱可用于 `context`、`trace` 和 `impact`；`doctor` 显示 LadybugDB FTS / VECTOR 扩展不可用，因此 `query` 可能返回空结果。修复全文搜索需要能下载 LadybugDB 扩展，并安装 Windows 所需的 OpenSSL 3；修复后先用 `doctor` 确认能力恢复。

## 文档变更记录

| 日期 | 内容 |
| --- | --- |
| 2026-09-23 | 旧踩坑记录移出项目并保留项目外备份；开工规则不再依赖该记录。 |
| 2026-09-23 | 确立 00 主控与六条端到端功能线、共享文件协调和线程交接规则；移除不再使用的外部记忆开工步骤。 |
| 2026-09-23 | 增加 GitNexus Windows 稳定运行入口，并记录本机 FTS / VECTOR 扩展状态和恢复条件。 |
| 2026-09-23 | 移除已过期的冲刺线程 / TODO 规则，明确当前正式文档、测试数据保留规则和实机验证边界。 |

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Hardware-RAG-Agent** (12305 symbols, 22259 relationships, 740 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "master"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "master" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/Hardware-RAG-Agent/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Hardware-RAG-Agent/clusters` | All functional areas |
| `gitnexus://repo/Hardware-RAG-Agent/processes` | All execution flows |
| `gitnexus://repo/Hardware-RAG-Agent/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
