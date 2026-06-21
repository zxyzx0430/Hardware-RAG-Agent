# Hardware RAG Agent — Codex 线程拆分备忘录

这份文档用于长期维护 Hardware RAG Agent 的多线程开发上下文。线程不是按“前端/后端”横切，而是按端到端长期子系统纵切。每个线程都必须同时关注前端入口、后端接口、状态管理、接口契约、测试和踩坑记录。

## 使用规则

- 开任何线程前，先读 PLUR：`plur inject "<当前线程任务>" --fast --json`。
- 每个线程都要读本文件，并只负责自己范围内的事项。
- 新增或修改接口前，先更新 `docs/api-contract.md`。
- 修复错误后，更新 `docs/pitfalls.md`。
- 每个线程有自己的上下文文件：`docs/threads/XX-name.md`。
- 不要用 `apply_patch` 创建新文件；优先用 `scripts/write_file.py` 或 shell 写入。

## 总线程图

| 编号 | 线程名 | 一句话职责 |
| --- | --- | --- |
| 00 | control | 项目主控、路线图、契约、PLUR 与 PR 裁决 |
| 01 | app | 产品外壳、布局、导航、全局 UI 与错误边界 |
| 02 | chat | SSE 流式聊天、输入输出、停止生成、重试 |
| 03 | knowledge | 文档上传、解析、索引、检索、source 引用 |
| 04 | session | 会话、消息、设置、API Key、快照、书签持久化 |
| 05 | agent | LangGraph、工具、召回、记忆、检查点、多 Agent 编排 |
| 06 | sandbox | 运行隔离、权限、危险操作审计、工具调用安全 |
| 07 | hardware | 串口、烧录、接线图、引脚审计、硬件工作台 |
| 08 | infra | Docker、CI、日志、Trace、Token、README、可观测性 |

---

# 00-control — 主控线程

## 负责范围

- 产品边界与路线图裁决
- 技术栈确认与变更
- 多线程任务分派
- `docs/api-contract.md` 最终裁决
- PLUR 长期记忆维护
- PR 审核与合并建议
- 跨线程冲突协调

## 不负责

- 不直接实现业务功能
- 不替代具体功能线程写长期代码

## 细颗粒模块

- 路线图管理：V1/V2/V3 功能归属
- 技术栈管理：前端/后端/Agent/部署栈
- 接口契约管理：接口状态、字段变更、版本化
- 记忆同步：PLUR、Codex 本地记忆、线程上下文文件
- 风险管理：过度设计、范围膨胀、阶段错位
- 合并审核：是否满足完成线，是否破坏契约

## 关键文件

- `docs/api-contract.md`
- `docs/pitfalls.md`
- `docs/thread-map.md`
- `docs/plans/*`
- `C:\Users\奶茶丸\.plur\engrams.yaml`

## 完成标准

- 所有线程知道自己负责什么、不负责什么
- 所有接口变更能在 `api-contract.md` 追踪
- 所有长期决策能在 PLUR 或本文件找到

---

# 01-app — 产品外壳线程

## 负责范围

- React/Vite 应用骨架
- 页面布局、导航、左右面板
- 全局主题、响应式、暗色模式
- Error Boundary、loading skeleton、空状态
- App-level Zustand 状态
- 前端日志面板入口

## 不负责

- 不负责聊天业务逻辑
- 不负责 RAG 检索
- 不负责数据库持久化
- 不负责 Agent 工具编排

## 细颗粒模块

- App Shell：`App.tsx`、`AppRoot.tsx`
- Layout：IconNav、LeftPanel、RightPanel、TopBar、MainArea
- Panel System：左右面板开关、拖拽宽度、工作区切换
- Theme System：light/dark token、CSS variables、Tailwind 兼容
- Navigation State：activeNav、modal、settings、knowledge、bookmarks
- Error Boundary：前端崩溃兜底，错误展示和恢复按钮
- Loading/Skeleton：页面级和组件级 loading
- Responsive：桌面、窄屏、移动端布局退化

## 前后端完整性

- 前端：必须能在 `http://127.0.0.1:5173` 打开
- 后端：不要求具体业务接口，但不能阻塞页面基本渲染
- API：所有 App 级请求必须通过 `api/client.ts`
- 状态：App store 只放全局 UI 状态，不放业务数据

## 关键文件

- `frontend/src/App.tsx`
- `frontend/src/main.tsx`
- `frontend/src/components/layout/*`
- `frontend/src/components/topbar/*`
- `frontend/src/stores/useAppStore.ts`
- `frontend/src/styles/*`
- `docs/threads/01-app.md`

## 完成标准

- 应用无白屏
- 主导航可切换
- 左右面板可开关和拖拽
- 错误边界能兜住前端异常
- App Shell 不依赖后端真实数据也能渲染

---

# 02-chat — 聊天线程

## 负责范围

- 用户输入、发送、停止、重试
- `/api/chat` 后端接口
- SSE 流式输出
- thinking/text/source/tool/done/error 事件消费
- 消息渲染、Markdown 渲染、代码块展示
- streaming 状态管理
- 网络错误和超时处理

## 不负责

- 不负责知识库入库
- 不负责 LangGraph Agent 工具
- 不负责历史消息持久化

## 细颗粒模块

- Input Flow：InputBar、快捷键、附件入口、模板入口
- Send Pipeline：sendMessage、stopStreaming、retryMessage
- SSE Parser：event 行、data 行、done/error 终态
- Message Model：user/assistant/system 消息结构
- Streaming State：isStreaming、streamingContent、streamingSteps
- Renderer：MarkdownRenderer、代码块、thinking 折叠块
- Source Placeholder：source 事件先可展示，实际数据由 Knowledge 线程接入
- Error UX：网络断开、401、500、模型超时

## 前后端完整性

- 前端：`InputBar` 调 `apiSSE('chat', ...)`
- 后端：`POST /api/chat` 返回 SSE
- API：事件格式必须符合 `docs/api-contract.md` §5.1
- 状态：消息进入 Chat store，streaming 结束后落成 assistant 消息
- 测试：输入一句话，能看到打字机输出和 done 事件

## 关键文件

- `frontend/src/components/input/InputBar.tsx`
- `frontend/src/components/chat/*`
- `frontend/src/stores/useChatStore.ts`
- `frontend/src/api/client.ts`
- `backend/main.py` 或未来 `backend/app/api/chat.py`
- `docs/threads/02-chat.md`

## 完成标准

- `/api/chat` 可用
- SSE 不丢 token
- stop/retry 不炸状态
- 后端断开时前端有错误提示
- 不依赖 LangChain/LangGraph 也能完成基础聊天

---

# 03-knowledge — 知识库线程

## 负责范围

- PDF/MD/TXT 上传
- 知识库文件列表、删除、索引状态
- Docling 解析
- chunking
- embedding
- ChromaDB
- BM25/RRF 混合检索
- source 引用
- 回答引用资料片段

## 不负责

- 不负责 Agent 决策工具
- 不负责会话持久化
- 不负责串口/烧录

## 细颗粒模块

- Upload Flow：`/api/kb/upload`、multipart、文件大小限制
- Library View：文件列表、分类、页数、chunk 数、上传时间
- Parser：Docling PDF → Markdown / blocks
- Chunker：按标题、表格、代码块、长度切分
- Embedding：模型选择、批量写入、失败重试
- Vector Store：ChromaDB collection、metadata、删除同步
- Hybrid Search：BM25、向量检索、RRF 融合
- Source Linking：doc_id、chunk_id、page、score、excerpt
- Index Status：queued、processing、ready、failed

## 前后端完整性

- 前端：KnowledgePanel 上传和展示文档
- 后端：kb upload/list/delete/status
- API：文档状态和 source 字段写入 `api-contract.md`
- 状态：Knowledge store 保存当前文件列表和索引状态
- 测试：上传一份 PDF，提问时能引用对应文档片段

## 关键文件

- `frontend/src/components/knowledge/*`
- `frontend/src/stores/useKnowledgeStore.ts`
- `backend/src/rag/*`
- `backend/data/` 说明
- `data/pdfs/README.md`
- `docs/threads/03-knowledge.md`

## 完成标准

- 上传不丢文件
- 文件列表刷新后存在
- chunks 数量可信
- 检索结果可追溯到 PDF 页码或 chunk
- 回答 source 可点击查看

---

# 04-session — 会话与设置线程

## 负责范围

- 会话列表
- 消息持久化
- 设置持久化
- API Key 保存与加密
- 模型选择保存
- 书签
- 快照
- SQLite / SQLAlchemy / Alembic
- Zustand 与后端同步

## 不负责

- 不负责聊天生成质量
- 不负责 RAG 检索质量
- 不负责 Agent 工具逻辑

## 细颗粒模块

- DB Schema：sessions、messages、settings、bookmarks、snapshots
- Migration：Alembic 初始化、升级、回滚
- Session API：list/create/rename/delete/pin/archive
- Message API：list/append/update/delete
- Settings API：provider、base_url、model、theme、language
- Key Vault：API Key 加密存储、明文显示开关
- Bookmark：收藏、文件夹、移动、删除
- Snapshot：保存、恢复、对比
- Hydration：页面刷新后恢复 store

## 前后端完整性

- 前端：SessionPanel、SettingsPage、BookmarkPanel、SnapshotPanel
- 后端：sessions/messages/settings/bookmarks/snapshots API
- API：所有持久化接口必须进入 `api-contract.md`
- 状态：Zustand 与数据库保持单向或双向同步策略清楚
- 测试：刷新页面后会话和设置不丢

## 关键文件

- `frontend/src/stores/useSessionStore.ts`
- `frontend/src/stores/useSettingsStore.ts`
- `frontend/src/components/session/*`
- `frontend/src/components/settings/*`
- `backend/app/models/*`
- `backend/app/db/*`
- `backend/alembic/*`
- `docs/threads/04-session.md`

## 完成标准

- 刷新不丢会话
- API Key 不用每次重填
- 书签和快照可恢复
- migration 可重复执行

---

# 05-agent — Agent 线程

## 负责范围

- LangGraph Agent 工作流
- ReAct 动态容错
- Tool Schema 注册
- 工具调用、工具结果标准化
- 对话召回
- 工作记忆与长期记忆
- 状态机检查点
- 多 Agent 编排

## 不负责

- 不负责基础 SSE 水管
- 不负责 PDF 入库本身
- 不负责系统级沙箱策略的最终裁决
- 不负责 Docker/CI

## 细颗粒模块

### 05A. Agent Runtime

- LangGraph 状态图
- 节点：plan、retrieve、tool_call、observe、answer、repair
- ReAct 循环
- 动态容错纠错
- 最大步数限制
- 工具失败后的降级策略
- 人工介入节点

### 05B. Agent Tool Registry

- Tool Schema 自动注册
- 工具名称、描述、参数、返回类型
- 参数校验
- 工具权限声明
- 工具超时
- 工具结果标准化
- 工具错误码

### 05C. Agent Tool Set

- search_kb
- lookup_register
- generate_code
- review_code
- diagnose_problem
- audit_pins
- compare_components
- web_search（可选）

### 05D. Agent Recall

- 当前对话召回
- 历史会话召回
- 向量召回
- 时间线召回
- 最近上下文窗口
- 任务相关记忆注入
- source 与 memory 区分

### 05E. MCP / Skill 接入

- MCP server 注册
- Skill 描述和权限
- OpenAI 工具调用协议适配
- MCP tool → Agent tool 映射
- 工具不可用时的降级提示

### 05F. Memory Evolution

- 工作记忆
- 长期偏好固化
- 用户纠错入库
- 项目事实沉淀
- PLUR 与项目内 memory 的边界
- 不写入隐私或临时信息

### 05G. State Checkpoint

- LangGraph checkpoint
- 断点续传
- 快照恢复
- 多用户隔离
- 人工介入后重试
- 失败路径回放

### 05H. Multi-Agent Matrix

- Router 主管 Agent
- Code Agent
- Review Agent
- Sandbox Audit Agent
- Knowledge Agent
- Frontend UI Agent
- 任务分派协议
- 子 Agent 输出验收格式

## 前后端完整性

- 前端：tool call 面板、Agent activity block、工具结果展示
- 后端：LangGraph runtime、tool registry、`/api/tool`
- API：chat SSE 的 `tool` 事件和 `/api/tool` 边界必须清晰
- 状态：Agent step、tool result、checkpoint ID 可追踪
- 测试：一个用户问题能触发工具调用，并显示完整轨迹

## 关键文件

- `backend/src/agent/*`
- `backend/app/api/tool.py`
- `frontend/src/components/chat/ActivityBlock.tsx`
- `frontend/src/components/workbench/*`
- `docs/threads/05-agent.md`

## 完成标准

- Agent 能稳定调用至少 1 个工具
- 工具失败有可读错误
- 多步轨迹可展示
- 可限制最大步数，防止无限循环
- checkpoint 能恢复一次中断任务

---

# 06-sandbox — 沙箱与权限线程

## 负责范围

- Agent 和工具运行的安全边界
- 文件系统写入范围
- 高危命令拦截
- 删除/覆盖/移动审计
- 子进程启动规范
- 工具调用权限矩阵
- 操作日志

## 不负责

- 不负责 Agent 具体推理策略
- 不负责业务接口字段
- 不负责 UI 视觉

## 细颗粒模块

### 06A. Runtime Isolation

- 工作目录限制
- 临时目录策略
- 用户项目目录白名单
- 禁止访问敏感目录
- 环境变量过滤

### 06B. File Operation Policy

- 创建文件规则
- 修改文件规则
- 删除文件审批
- 移动文件审批
- 二进制文件处理
- `data/` 不进 Git

### 06C. Command Risk Levels

- 低风险：读文件、列目录、状态检查
- 中风险：启动服务、安装依赖、格式化
- 高风险：删除、重置、覆盖、网络下载、系统级命令
- 高风险命令必须记录原因

### 06D. Tool Call Policy

- 禁用 `apply_patch` 创建新文件
- 禁止大段 inline Python
- 禁止复杂 PowerShell 管道串联
- 长命令拆分
- 输出过长时限制行数

### 06E. Audit Log

- 谁执行了什么命令
- 改了哪些文件
- 是否触发高危操作
- 错误是否进入 pitfalls

## 前后端完整性

- 前端：必要时展示权限提示或危险操作确认
- 后端：工具执行前检查权限
- API：危险操作要有明确请求体和错误码
- 状态：每次工具调用有 audit record

## 关键文件

- `AGENTS.md`
- `scripts/write_file.py`
- `docs/pitfalls.md`
- `docs/threads/06-sandbox.md`

## 完成标准

- 高危操作不会静默发生
- 误用工具时有明确替代方案
- 出错后能在 pitfalls 找到记录

---

# 07-hardware — 硬件工作台线程

## 负责范围

- 串口扫描
- 串口监视器
- 编译
- 烧录
- 接线图
- 引脚审计
- 硬件调试闭环

## 不负责

- 不负责基础聊天
- 不负责知识库解析
- 不负责 Agent 工作流本身

## 细颗粒模块

- Devices：`/api/devices`、pyserial 扫描
- Serial Monitor：WebSocket、baud、暂停、清空、导出
- Build：PlatformIO `pio run`
- Upload：`pio run -t upload`
- Wiring：WireViz、SVG、BOM
- Pin Audit：strapping pin、冲突检测、替代引脚建议
- Progress SSE：build/upload 进度事件
- Hardware Mock：无硬件时可演示

## 前后端完整性

- 前端：WorkbenchPanel、SerialMonitor、FlashPanel、WiringDiagram
- 后端：devices/build/upload/wiring/audit_pins/monitor
- API：SSE 和 WebSocket 格式必须进 contract
- 状态：连接状态、进度、日志、错误

## 关键文件

- `frontend/src/components/workbench/*`
- `backend/app/api/serial.py`
- `backend/app/api/flash.py`
- `backend/app/api/wiring.py`
- `backend/app/api/safety.py`
- `docs/threads/07-hardware.md`

## 完成标准

- 没硬件时能 mock 演示
- 有硬件时能扫到串口
- 编译和烧录有进度反馈
- 引脚审计能返回可执行建议

---

# 08-infra — 基建与可观测性线程

## 负责范围

- Docker
- CI
- lint/test
- 启动脚本
- README
- 日志
- token 统计
- request id
- trace
- ReAct 轨迹可视化
- 开源贡献体验

## 不负责

- 不负责业务功能实现
- 不负责 UI 细节
- 不负责 RAG 算法本身

## 细颗粒模块

### 08A. Local Dev

- 一键启动脚本
- 端口检查
- 前后端健康检查
- `.env.example`
- 本地依赖安装说明

### 08B. Docker

- Dockerfile
- docker-compose
- ChromaDB 服务
- 前端 build 集成
- volume 挂载

### 08C. CI

- frontend typecheck
- frontend build
- backend pytest
- ruff
- mypy
- GitHub Actions

### 08D. Observability

- request id
- structured logs
- token usage
- model latency
- SSE duration
- tool trace
- error dashboard

### 08E. Open Source Docs

- README
- CONTRIBUTING
- architecture overview
- quick start
- screenshots/GIF
- issue templates

## 前后端完整性

- 前端：错误边界、日志面板、构建产物
- 后端：结构化日志、健康检查、指标输出
- 工程：CI 必须覆盖两端
- 文档：新贡献者能 10 分钟跑起来

## 关键文件

- `README.md`
- `docker-compose.yml`
- `.github/workflows/*`
- `scripts/*`
- `docs/dev-status/*`
- `docs/threads/08-infra.md`

## 完成标准

- clone 后能按 README 跑起来
- CI 能阻止明显坏代码进入主干
- 出错时能通过日志定位到请求、模型、工具或接口

---

# 每个线程的上下文文件模板

每个线程第一次开工时，创建对应文件：

```md
# XX-name 线程上下文

## 负责范围
- 做什么：
- 不做什么：

## 当前状态
- 已完成：
- 正在做：
- 阻塞：

## 接口契约
- 涉及 `docs/api-contract.md`：

## 关键文件
- 前端：
- 后端：
- 文档：

## 决策记录
- YYYY-MM-DD：

## 踩坑记录
- 关联 `docs/pitfalls.md`：

## 下次开工先看
- 1.
- 2.
- 3.
```

---

# 开线程 Prompt 模板

```text
你是 Hardware RAG Agent 的 <线程名> 线程。

开工前必须：
1. 运行 `plur inject "<本线程任务>" --fast --json`
2. 阅读 `docs/thread-map.md`
3. 阅读 `docs/threads/<线程文件>.md`，如果不存在就创建
4. 阅读 `docs/api-contract.md` 中与你相关的章节
5. 修复错误后更新 `docs/pitfalls.md`

你负责端到端功能，不是单独前端或单独后端。
每次改动必须检查：
- 前端入口是否存在
- 后端接口是否存在
- api-contract.md 是否一致
- 状态管理是否闭环
- mock/真实数据切换是否清楚
- 是否记录踩坑

禁止：
- 不要超范围改其他线程负责的代码
```
