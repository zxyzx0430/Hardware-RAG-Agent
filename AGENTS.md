# AGENTS.md - Hardware RAG Agent

> Last reviewed: 2026-07-01

## Project Overview

Hardware RAG Agent — 面向嵌入式开发者的硬件知识库 AI Agent。
基于官方芯片手册做 RAG 检索，回答硬件参数/接线方案、生成驱动代码、审查代码问题。
用户自配 API Key + 自选模型，回答标注来源。

## Deploy Model

该项目是**本地自部署项目（self-hosted）**，用户下载后在自己的电脑上运行。已计划开源到 GitHub。

## 复用优先原则

- 大型模块（路由拆分、Agent框架、RAG pipeline、新UI组件等）或者优化方案实现前，优先去GitHub搜索成熟开源实现，参考其架构设计后再动手
- 不要求直接使用开源库，但要求先看别人怎么做的，避免从零造轮子
- 搜索方向：GitHub搜同类项目、awesome-*列表、该语言/框架的官方示例仓库
- 例外：学习目的或架构差异过大时可以不参考，但需注明理由

### 安全立场

- **需要防护的**：本地风险（XSS、API Key 泄漏、文件注入、SQL 注入）——用户浏览器扩展可能窃取凭证，恶意文档可能包含脚本
- **不需要管的**：网络攻击（DDoS、CSRF、HTTPS、CORS 硬化、暴力破解、请求频率限制）——服务只监听 127.0.0.1，不暴露到公网
- **性能方面**：不需要高并发优化、不需要分布式缓存、不需要 CDN

---

## Start Here（新线程开工先读）

Codex首次进入项目的线程，按这个顺序读：
| 顺序 | 做什么 | 为什么 |
|------|--------|--------|
| 1 | plur inject 任务描述 --fast --json | 读 PLUR 长期记忆 |
| 2 | 读 docs/completed.md | 知道哪些功能已经做了 |
| 3 | 读 docs/pitfalls.md | 知道前人踩过什么坑 |
| 4 | 读 docs/todos/XX-name.md（自己的 TODO） | 知道自己要做什么 |
| 5 | 只读 TODO 前 2 项，开始干活 | 聚焦，不要一次性扛太多 |

如果你是trae则不须做45


---

## PLUR 规则

每次对话开始必须先读 PLUR：
`
plur inject "当前任务描述" --fast --json
`

重要决策必须先写入 PLUR 再执行。
---

## 踩坑文件使用规则

- **问题定位阶段**：先自己分析，卡住了再翻 pitfalls.md 看有没有前人踩过
- **修复阶段**：确定根因后，翻 pitfalls.md 的"下次注意"看有没有同类型教训
- **修复完成**：无重复则追加到 pitfalls.md

---

## Role Division

Codex 和 Trae 权限对等，均可读写代码和文档。唯一区别：
| 角色 | 负责 |
|------|------|
| **Codex (00-control)** | 方向把控、接口契约最终裁决、PLUR 长期记忆维护、跨线程冲突协调、PR 审核与合并 |
| **Trae** | 以上除 00-control 之外的所有工作 |

双方共同维护：全部文档和全部代码。
---

## Trae 6 线程系统（字节创造力大赛冲刺）

> 与 Codex 的 00-08 线程系统并存，专用于 Trae 的 15 天 demo 冲刺（6.30 - 7.15）。
> Codex 线程系统见下方 Thread Overview，Trae 线程编号用 T1-T6 区分。

### 线程识别机制

**Trae 开工时通过 prompt 第一行标注自己的线程身份**，格式：

```
你是 Trae 的 T{N}-{名称} 线程。
```

例：
- `你是 Trae 的 T2-Agent 全链路线程。`
- `你是 Trae 的 T6-硬件工作台线程。`

**主控线程（T1）由用户担任**，不下发 prompt。Trae 收到任务时先看 prompt 第一行确认自己的线程身份，然后只做自己范围内的事。

### 6 线程职责矩阵

| 编号 | 线程名 | 职责 | 状态 | 关键文件边界 |
|------|--------|------|------|--------------|
| **T1** | 主控（用户） | 全局协调 + 跨线程冲突裁决 + demo 最终决策 | ✅ 运行中 | 不直接写代码 |
| **T2** | Agent 全链路 | LangGraph + 9 工具 + 权限门控 + 审计日志 | 🔄 进行中 | `backend/src/agent/*` + `chat_routes.py` L121+ Agent 区 |
| **T3** | 数据加 RAG | 重新索引 + DeepEval + 主案例验证（总分 0.75+ 用户自己追） | 🔄 进行中 | `data/` + `scripts/reindex_*` + `scripts/audit_*` |
| **T4** | 部署加文档 | Dockerfile + README + start.bat + demo 脚本 + 录屏 + 端到端测试 + bug 清单 | ⏳ 待启动 | `Dockerfile` / `docker-compose.yml` / `README.md` / `docs/demo-*.md` |
| **T5** | 前端体验 | 自动滚动 + SSE 重连 + source 分数 + toast + Agent 前端 + 04-session 持久化验证 | 🔄 进行中 | `frontend/src/stores/useChatStore.ts` + `frontend/src/components/chat/*` |
| **T6** | 硬件工作台（新开） | WorkbenchPanel 验证 + 串口扫描 + 接线图渲染 + 引脚审计展示 + 烧录 SSE + 与 Agent 联动 | 🆕 新开 | `frontend/src/components/workbench/*` + `backend/app/api/hardware_routes.py` + `build_routes.py` |

### 文件边界规则（防冲突）

每个 Trae 线程只能改自己范围内的文件，**禁止越界**。公共文件按以下规则协调：

| 文件类别 | 规则 |
|---------|------|
| **独占文件** | 各线程在自己边界内独占，其他线程不碰 |
| **SSE 事件 schema** | T2 定义（Agent 工具调用事件格式），T5 / T6 消费 |
| **`chat_routes.py`** | T2 独占 L121+ Agent 接入区，其他线程不碰 |
| **`useChatStore.ts`** | T5 独占，T2 不碰前端 |
| **`hardware_routes.py` / `build_routes.py`** | T6 独占后端硬件路由，T2 调工具时只读不写 |
| **`api-contract.md`** | 改接口前先改这里，任何线程都可改但需通知 T1 |
| **`pitfalls.md`** | 任何线程修 bug 后必须追加，无冲突 |
| **`completed.md`** | 任何线程完成端到端闭环后追加，无冲突 |

### 线程间协作点

| 协作点 | 上游 | 下游 | 时序 |
|--------|------|------|------|
| Agent SSE 事件 schema | T2 定义 | T5 / T6 消费 | T2 第 1 天先出 schema |
| Agent 调 audit_pins/wiring → 前端展示 | T2 后端调工具 | T6 前端展示结果 | T6 等 T2 schema 后做 |
| Agent 调 search_docs → source 引用展示 | T2 后端调工具 | T5 前端展示 source 分数 | T5 等 T2 schema 后做 |
| 端到端测试找 bug → 修复 | T4 找 bug | 各线程修自己部分 | 7.12-7.13 |
| demo 录屏 | T4 录 | 各线程配合修最后 bug | 7.14 |

### 线程启动顺序

```
Day 1（6.30）：
  T1 主控（用户）立即可用
  T3 数据加 RAG 立即开跑（重新索引 + 主案例验证）
  T5 前端体验立即开跑（D2 自动滚动，不依赖任何人）
  T2 Agent 先出 SSE schema，再开始 LangGraph

Day 5+（7.4+）：
  T6 硬件工作台启动（需先 Glob 定位 WorkbenchPanel 组件）
  T4 部署加文档启动（Docker 骨架可早启动）

Day 13-15（7.12-7.15）：
  T4 端到端测试 + 录屏 + demo 脚本
  各线程配合修最后 bug
```

### Trae 线程与 Codex 线程的关系

| 维度 | Trae 6 线程 | Codex 00-08 线程 |
|------|------------|-----------------|
| 用途 | 15 天 demo 冲刺 | 长期项目维护 |
| 编号 | T1-T6 | 00-08 |
| 范围 | 端到端纵切（按 demo 优先级） | 端到端纵切（按子系统） |
| 共存 | Trae 6 线程优先 | Codex 线程作为长期参考 |
| 映射 | T2 ≈ 05-agent / T3 ≈ 03-knowledge / T5 ≈ 02-chat+04-session / T6 ≈ 07-hardware / T4 ≈ 08-infra | — |

**Trae 不需要管 Codex 的 TODO 清单系统**（下方 TODO 清单系统章节明确说了"如果你是trae则忽略这个todo清单系统"）。

### 线程开工模板（给主控用）

主控给某个 Trae 线程下发任务时，prompt 第一行必须标注线程身份：

```
你是 Trae 的 T{N}-{名称} 线程。

开工前必须：
1. 读 AGENTS.md 的「Trae 6 线程系统」章节，确认自己的文件边界
2. 读 docs/superpowers/specs/2026-06-30-agent-react-design.md（如果涉及 Agent）
3. 读 docs/api-contract.md 中与你相关的章节
4. 修复错误后更新 docs/pitfalls.md
5. 完成端到端闭环后更新 docs/completed.md

你只负责自己线程范围内的事，禁止越界改其他线程的文件。
公共文件协作规则见 AGENTS.md「文件边界规则」。

当前任务：
{具体任务描述}
```

---

## TODO 清单系统

codex每个线程在 docs/todos/ 下有一个独立的 TODO 文件（01-app ~ 08-infra），所有线程共同维护。如果你是trae则忽略这个todo清单系统

### 工作流
1. **00-control 或任意线程发现问题** → 先更新对应线程的 TODO 文件，再动手做
2. **你直接给线程下命令** → 线程先更新 TODO（加在最前面），再做。修完通知 00-control
3. **线程开工** → 直接编辑 docs/todos/XX-name.md，做完一项把 [ ] 改为 [x]
4. **执行中发现新问题** → 追加到该 TODO 文件**最前面**（倒序排列，最新的在最上面）
5. **跳过任务** → 改为 [-] 并写明理由
6. **需确认** → 改为 [?] 并写明疑问，等 00-control 回复
7. **全部完成** → 通知 00-control 审查
8. **00-control 审查通过** → 删除该 TODO 文件，更新 docs/completed.md

### 完成说明

每项完成时，在 [x] 后面写一句说明做了什么：

`
- [x] 拆分 routes.py
      routes.py → 5 个文件（chat_routes.py / kb_routes.py / hardware_routes.py / build_routes.py / tool_routes.py），主路由注册已更新，api-contract.md 同步
`

### 聚焦规则

线程每次启动**只读前 2 项**（最上面的 2 条），做完再看后面的。防止一次性扛太多。

### 00-control 审查清单

审查 TODO 时检查：
1. 每项 [x] 都有完成说明，能判断做了什么
2. 改了哪些文件写清楚了（如 routes.py、endpoints.ts）
3. 修 bug 的项有没有同步记 pitfalls.md

---

### 标记含义

| 标记 | 含义 |
|------|------|
| [ ] | 待做 |
| [x] | 已完成（写完成说明） |
| [-] | 跳过（写原因） |
| [?] | 需确认（写疑问） |

---

## Docs Map（文档目录）

所有文档在 docs/ 下，按用途分组：

### 开工必读（开发长期维护）
| 文件 | 内容 |
|------|------|
| docs/completed.md | 项目完成记录（各线程做了啥、缺啥、已知问题） |
| docs/pitfalls.md | 踩坑唯一来源，修 bug 必追加 |
| docs/api-contract.md | 接口契约，改接口先改这里 |
| docs/thread-map.md | 线程归属与范围 |
| docs/architecture-map.md | 项目全景图（架构 + 传导链思维导图，面向小白，长期迭代） |

### 给用户看（长期）
| 文件 | 内容 |
|------|------|
| docs/RAG.md | RAG 子系统完成度/生命周期/优缺点报告 |
| docs/git-cheatsheet.md | Git 回滚速查卡（项目专用） |
| docs/resume-achievements.md | 简历技术亮点（面试用，按模块记录） |

### 任务协调
| 文件 | 内容 |
|------|------|
| docs/todos/*.md | 各线程 TODO 清单（01-app ~ 08-infra） |
| docs/threads/*.md | 各线程职责详情 |
| docs/handoff/*.md | 线程交接模板（README/BLOCKED/DECISION/REVIEW-TEMPLATE） |
| docs/issue-tracker.md | 已知问题追踪 |
| docs/workflow-trae-codex.md | Codex × Trae 配合流程 |

### 规划参考
| 文件 | 内容 |
|------|------|
| docs/plans/roadmap.md | 路线图 |
| docs/plans/rag-guide.md | RAG 入门与实践指南 |
| docs/plans/agent-engineering-guide.md | Agent 实现指南 |

### 设计稿
| 文件 | 内容 |
|------|------|
| docs/design/specs/*.md | 功能设计稿（brainstorming 输出，按日期归档） |

### 代码审查
| 文件 | 内容 |
|------|------|
| docs/review/*-scan.md | 8 个线程代码扫描报告（01-app ~ 08-infra） |

### 一次性报告
| 文件 | 内容 |
|------|------|
| docs/reports/*.md | 测试/优化/审计报告（按日期归档，不长期维护） |

### 归档
| 文件 | 内容 |
|------|------|
| docs/archive/handoff/*.md | 历史交办单（已完成的 250621-*.md 等） |
| docs/archive/review-2026-06-21/*.md | 2026-06-21 早期全项目审查快照（已过时） |
| docs/archive/plans/*.md | 旧版 plans/ |
| docs/archive/feature-gap-*.md | 旧版功能缺口分析 |
| docs/archive/dev-status/*.md | 旧版开发进度状态 |
| docs/archive/architecture.md / usage-guide.md / Week 1 代码导读.md | 早期文档 |

---

## Thread Overview

| 线程 | 职责 | 详情 |
|------|------|------|
| 00-control | 主控/契约/PLUR | docs/threads/00-control.md |
| 01-app | 布局/导航/主题 | docs/threads/01-app.md |
| 02-chat | SSE 流式聊天 | docs/threads/02-chat.md |
| 03-knowledge | 知识库 RAG | docs/threads/03-knowledge.md |
| 04-session | 持久化/设置 | docs/threads/04-session.md |
| 05-agent | LangGraph Agent | docs/threads/05-agent.md |
| 06-sandbox | 沙箱执行 | docs/threads/06-sandbox.md |
| 07-hardware | 硬件工作台 | docs/threads/07-hardware.md |
| 08-infra | Docker/CI/日志 | docs/threads/08-infra.md |

---

## Development

前端访问 http://127.0.0.1:5173，Vite 自动把 /api/* 代理到后端。
powershell
# 后端
cd E:\Desktop\agent\backend
python main.py --web --port 58080

# 前端
cd E:\Desktop\agent\frontend
npx vite --port 5173


## Project Structure

`
agent/
├── backend/       FastAPI + LangChain + ChromaDB
├── frontend/      React + TypeScript + Vite + Tailwind + Zustand
├── scripts/       开发辅助脚本
├── data/          知识库 PDF + 向量数据库
├── docs/
│   ├── 开工必读   completed.md / pitfalls.md / api-contract.md / thread-map.md / architecture-map.md
│   ├── 给用户看   RAG.md / git-cheatsheet.md / resume-achievements.md
│   ├── 任务协调   todos/ / threads/ / handoff/(模板) / issue-tracker.md / workflow-trae-codex.md
│   ├── 规划参考   plans/roadmap.md / rag-guide.md / agent-engineering-guide.md
│   ├── 设计稿     design/specs/
│   ├── 代码审查   review/*-scan.md
│   ├── 一次性报告 reports/
│   └── 归档       archive/(handoff/review-2026-06-21/plans/feature-gap/dev-status)
├── AGENTS.md      本文件
└── .gitignore
`

---

## Commit 规范

每次提交用 conventional commits 格式，禁止用「v0.1」「update」「fix bug」这种通用说明。

### 格式

`
<type>(<scope>): <一句话说清楚改了什么>
`

### Type 对照

| type | 什么时候用 |
|------|-----------|
| feat | 新功能 |
| fix | 修 bug |
| docs | 文档 |
| refactor | 重构，不改变行为 |
| style | 代码格式，不改变逻辑 |
| build | 构建/依赖/配置 |

### Scope 对照

| scope | 对应 |
|-------|------|
| frontend | 前端 React 代码 |
| backend | 后端 Python 代码 |
| docs | 文档 |
| scripts | 工具脚本 |
| build | 项目配置 |

### 示例

`
feat(backend): /api/models 代理上游模型列表
fix(frontend): SSE 断连后不自动重连
docs: 补充 api-contract.md 错误码表
refactor(backend): 拆分 routes.py 到独立模块
`

---

## 沟通原则（重要）

### 理解需求的方式

用户不是技术人员，描述需求可能不精确、不完整、非技术用语。职责是先理解意图，再翻译成方案。
- **先理解，再行动**：收到需求后，先用大白话复述一遍我的理解，确认对了再动手
- **不问技术问题**：不问「用没用过 Git」「懂不懂 SQL」这类问题
- **给选择，不给黑盒**：方案类的决定，列成简单的选项让用户选
- **进度透明**：长时间任务每做完一步说一声
- **主动发现**：主动发现临时文件、gitignore 纰漏、该删的旧代码，列出来让用户决定
- **容忍模糊**：用户说「去 GitHub 看看」意思是「帮我找有用的工具」。先猜再确认，不要让用户补充技术细节
---

## 阅读指南

不同角色关注不同内容，不必从头读到尾：
| 角色 | 必读 | 参考 |
|------|------|------|
| **00-control** | Start Here / PLUR 规则 / Role Division / TODO 工作流 | Docs Map / Thread Overview |
| **01-08 线程** | Start Here / TODO 清单系统 / 踩坑规则 / Development | Commit 规范 / Docs Map |
| **Trae** | 全部 | — |

---

## 维护触发条件

以下情况出现时，必须更新 AGENTS.md：
- **新增/删除了一个 thread** → 更新 Thread Overview 和 TODO 清单表
- **新增了 docs/ 顶层内容** → 更新 Docs Map 和 Project Structure
- **删除 doc 前** → 先搜 AGENTS.md 有没有引用它，有则更新
- **项目进入新阶段（V1→V2→V3）** → 更新阶段相关规则
- **pitfalls.md 中同一模式重复 >= 3 次** → 考虑升格为 AGENTS.md 规则
- **TODO 状态表** → 每轮开工前手动同步

---

## 项目全景图维护

项目维护一份面向小白的架构 + 传导链思维导图，长期迭代。

### 文件位置（双份同步）
- **项目内副本（开发迭代版）**：`docs/architecture-map.md`
- **桌面副本（用户查阅版）**：`C:\Users\奶茶丸\Desktop\agent-architecture-map.md`
- 两份内容必须保持一致。每次更新项目内副本后，必须同步刷新桌面副本。

### 维护触发条件（代码改动后必须同步更新全景图）
- **新增/删除一个 API 路由** → 更新「数据持久化」或对应链路章节
- **新增/删除一个 store action** → 更新「状态管理」章节
- **新增/删除一个核心组件** → 更新「启动链路」章节
- **新增/删除一个数据库表** → 更新「整体架构」章节
- **修复重要 bug** → 追加到「已修复的 bug」表格
- **新增/删除一个核心函数** → 更新对应链路章节

### 维护负责人
- Trae / Codex 任意线程修改代码后，若触发上述条件，必须同步更新 `docs/architecture-map.md`
- 00-control 在 PR 审查时核验全景图是否同步
- 桌面副本由用户手动复制，或由 Trae 在结束任务前用 `Copy-Item` 同步

### 同步桌面副本的命令
```powershell
Copy-Item "e:\Desktop\agent\docs\architecture-map.md" "C:\Users\奶茶丸\Desktop\agent-architecture-map.md" -Force
```

### Last reviewed
- 2026-06-29：初始创建，覆盖后端 11 个路由模块 + 前端 8 个 store + 聊天/RAG/知识库/硬件/沙箱 5 条核心链路

---

## AGENTS.md 维护规则

- 每次修改后必须更新 Changelog
- 非 00-control 修改前须通知 00-control
- Last reviewed 日期每次 review 后更新
---

## Changelog

| 日期 | 改了什么 | 谁改的 |
|------|---------|--------|
| 2026-07-01 | v3 Agent 接入完成：9 工具 + 4 步权限门控 + HITL + 审计日志 + 反死循环 + 上下文保护 + fallback。新增 13 个后端 Agent 模块 + 1 个 agent_sandbox_routes + 3 个前端组件。详细见 docs/completed.md | Trae T2 |
| 2026-06-30 | 新增「Trae 6 线程系统」章节：T1 主控/T2 Agent 全链路/T3 数据加 RAG/T4 部署加文档/T5 前端体验/T6 硬件工作台。定义线程识别机制（prompt 第一行标注）+ 文件边界规则（防冲突）+ 协作点 + 启动顺序 + 与 Codex 00-08 线程的映射关系。用于字节创造力大赛 15 天冲刺 | Trae T1 主控 |
| 2026-06-29 | architecture-map.md v3 大扩写：新增「用户能力地图」（8 大能力）+「工程能力地图」（启动/测试/调试/配置/迁移/规范）+ 数据库表清单 + API 路由总表 + 前端组件清单 + 关键参数汇总 + 文件目录速查，从 348 行扩到 1178 行，桌面副本同步 | Trae |
| 2026-06-29 | docs 目录大整理：测试报告归 reports/、旧审查报告归 archive/review-2026-06-21/、历史交办单归 archive/handoff/、superpowers→design、删 trae-competition-proposal.html 和 chunk-debug.txt、Docs Map 新增「给用户看」「设计稿」「代码审查」「一次性报告」分组，Project Structure 同步更新 | Trae |
| 2026-06-29 | 新增项目全景图 docs/architecture-map.md（架构+传导链思维导图），新增 AGENTS.md「项目全景图维护」章节，Docs Map 补 architecture-map.md | Trae |
| 2026-06-23 | 文档整合：6 个规划文件 → roadmap.md，2 个 RAG 文件 → rag-guide.md，归档旧 plans/、dev-status/、feature-gap-analysis.md | 00-control |
| 2026-06-21 | 初始结构化：新增 Start Here / Docs Map / TODO 工作流 / 阅读指南 / 维护规则 | 00-control |

---

### Changelog 修剪

保持最近 20 条。超出时删除最旧的一半。每个 V 阶段结束时清空 Changelog，改为一条总结。
---

## Language

- 回复默认中文
- 代码和注释用英文


<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **Hardware-RAG-Agent** (6433 symbols, 10097 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/Hardware-RAG-Agent/context` | Codebase overview, check index freshness |
| `gitnexus://repo/Hardware-RAG-Agent/clusters` | All functional areas |
| `gitnexus://repo/Hardware-RAG-Agent/processes` | All execution flows |
| `gitnexus://repo/Hardware-RAG-Agent/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |


## 代码规范（防屎山）

> 所有线程写代码时必须遵守以下规则，AI 生成代码也会被检查。

### 函数级
- **max-lines-per-function ≤ 10**：一个函数不超过 10 行，过长必须拆分。强迫函数短小、单一职责。
- **complexity ≤ 10**：圈复杂度不超过 10。超过说明逻辑太复杂，需要拆分子函数或简化条件。
- **max-params ≤ 3**：函数参数不超过 3 个。超过 3 个应封装为 dataclass/dict 或重新设计。

### 文件级
- **max-lines ≤ 300**：单文件不超过 300 行。超过必须拆模块（Python）或拆组件（React）。

### 代码质量
- **no-magic-numbers**：禁止魔法数字。所有数字常量必须用命名常量表达含义，如 MAX_RETRIES = 3 而非 if retry > 3。
- **命名规范**：变量/函数用英文，命名自文档化。
- **错误处理**：所有外部调用（API/DB/文件）必须有 try/except，不能静默吞异常。
- **类型注解**：Python 函数必须有类型注解，TypeScript 启用 strict 模式。

### 检查方式
- AI 生成代码后自动执行上述规则检查
- PR review 时逐条核验
- 违反规则的代码标注为「待重构」，不阻塞上线但需记录 TODO

<!-- gitnexus:end -->
