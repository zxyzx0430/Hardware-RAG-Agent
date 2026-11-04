# 成熟度重置：问题总账与开发优先级

> 日期：2026-11-04
> 代码基线：`E:\Desktop\agent`，master `eab62d2` + 7,428 行新增 / 536 行删除未提交改动
> 方法：4 路红队审计（状态/架构/测试/安全）+ 本机实测
> 结论先说：**这不是“差一点成熟”，这是“功能很多的原型”。问题不在功能数量，在基础不变量。**

---

## 0. 先承认现实

### 0.1 本机实测（2026-11-04）

| 项目 | 结果 |
| --- | --- |
| 后端测试 | 83 个测试文件 / 443 个用例（参数化后 839 项）；首轮 `20 failed / 108 passed` 后触发 maxfail（全量仍在跑）；59/76 根测试文件用 mock |
| 前端测试 | `171 passed / 2 failed`（`useChatStore.context-limit.test.ts` 超时） |
| `npm test` | **跑不起来**：`node_modules/.bin` 不存在（pnpm 布局 + npm 脚本），需直接 `node node_modules/vitest/vitest.mjs run` |
| 未提交改动 | 37 个修改 + 34 个未跟踪，共 71 个路径；7,428 行新增 |
| 后端根目录临时脚本 | 52 个 `_*.py`（`_check_*`、`_verify_*`、`_tmp_*`） |
| 测试/CI | 无 `.github/workflows`；无 Playwright/Cypress；无 release/tag/CHANGELOG |
| 代码规模 | 11 个文件 >1,000 行；60+ 个函数 >80 行；`useChatStore.ts` 3,222 行 |
| 仓库 | 0 star、0 fork；无 repo description/topics |

> 这不是“测试偶尔红”，是**当前工作区没有可交付的绿色基线**。之前文档里的 608/145 是历史快照，不是现在这份代码。

**最刺眼的一条安全链**：RAG 文档里的提示注入 → `read_file` 读 `.enc_key`/`keys_store.json` → `webfetch` 外传。三个工具都是 `LOW`、自动放行，不需要用户确认。一份恶意 datasheet 就够了。

### 0.2 一句话

**你已经把“功能”堆到了一个很高的位置，但“可信”还在地基下面。**

差异不是“功能多少”，而是：

- 别人一个概念**一套实现**，你一个概念 **2–3 套**（FTS、流式、来源编号、记忆、todo、会话状态）；
- 别人**一条 CI**，你有 **37KB 的测试日志**和 0 条 CI；
- 别人**一层 OS 沙箱**，你有一层**黑名单**；
- 别人**删死代码**，你有 6 个死模块 + 52 个临时脚本；
- 别人**发布**，你有 7,428 行未提交。

---

## 1. 差异体现在哪里：7 个不变量

成熟项目不是功能更多，而是**不变量被强制执行**。你的项目在每个不变量上都有具体缺口。

### 1.1 单一真源（Single Source of Truth）

| 成熟项目 | 这个项目 |
| --- | --- |
| 会话历史以服务端 DB 为准，前端只是缓存 | 前端 localStorage 是主真源；后端每轮只保存最后两条（`useChatStore.ts:1628`），`ErrorBoundary` 出错会清空所有 `hwrag_*` 且不确认（`ErrorBoundary.tsx:32-33`） |
| 一套全文索引 | 两套：`messages_fts`（真源，触发器同步）与 `sessions_fts`（从未建表/写入，`session_search.py:42,48`） |
| 一套来源编号 | 三套：`source_registry` 的 `srcN`、`search_docs.py:283/351` 的 `src{idx}`、前端 `src-${Date.now()}`（`useChatStore.ts:1074`） |
| 一套聊天流 | 两套：Agent `sse_adapter` 与 legacy 纯 LLM 流（`chat_routes.py:1390-1490`） |
| 一套 todo | 后端模块 dict（`todo_write.py:60`）+ 前端 messages.todos，重启即丢 |
| 一套配置 | 128000/4096/262144/300s/8000/4000 在多处重复（`model_registry.py:28`、`autocompact.py:41`、`context_guard.py:32`、`sse_adapter.py:58` 等） |

**后果**：同一份数据在不同地方不一致；刷新/换浏览器/出错时，用户会丢消息或看到错状态。

### 1.2 可恢复状态（Recoverable State）

| 成熟项目 | 这个项目 |
| --- | --- |
| 会话/运行/审批/检查点可恢复 | 普通请求每轮重置 checkpoint（`chat_routes.py:971-977`）；HITL pending 只在内存 30 分钟（`chat_routes.py:98-101`）；SSE 无 `id:`/Last-Event-ID，断线只能重发 |
| 崩溃后 run 标记 interrupted，可续跑 | 无 run 概念；无事件表；无崩溃恢复；写/烧录工具没有幂等键 |
| 删除级联 | 删 session 只删 ORM；FTS/Store/旧 compaction/observations 不保证清理 |

**后果**：刷新、断网、重启、切会话都可能丢状态；长任务无法可靠续跑。

### 1.3 可验证正确性（Verifiable Correctness）

| 成熟项目 | 这个项目 |
| --- | --- |
| CI 绿 + E2E + 黄金集 | 无 CI、无 E2E；当前后端 20+ 失败、前端 2 失败；35 题 RAG 只有 7 题四指标完整 |
| 每个引用可追溯到真实来源 | 后端不校验 `[srcN]`；`page=chunk_index` 与 `page_start` 混用（`search_docs.py:357`） |
| 核心工具单测 | 39/135 个模块在测试中完全没有出现；`read_file/edit_file/grep/apply_patch` 等核心文件工具无直接单测 |
| 真实 provider/浏览器/硬件验证 | 大量测试用 fake/mock；真实烧录未验收 |

**后果**：测试绿不代表能用；引用可能是编的；改一个地方不知道会坏哪里。

### 1.4 安全边界（Enforced Safety Boundary）

| 成熟项目 | 这个项目 |
| --- | --- |
| 权限 + OS 沙箱分离 | 只有应用层权限/审计；`run_command` 以当前用户全权限起 PowerShell（`run_command.py:316-331`） |
| 路径默认拒绝（allowlist） | `path_guard` 的允许目录已被移除，只剩黑名单（`path_guard.py:225-234`） |
| 密钥对 Agent 不可读 | `.enc_key`、`keys_store.json` 可被 `read_file` 读取；`grep/ls` 被列为 LOW 自动放行（`risk_classifier.py:76-80`） |
| 构建/烧录需认证 | `/api/build`、`/api/upload` 无认证（`build_routes.py:131-161`） |
| 构建输入白名单 | `options/lib_deps` 原样写入 `platformio.ini`（`pio_runner.py:184-211`），`extra_scripts` 可执行任意 Python |
| 前端不存明文密钥 | Provider API key 与 `session_token` 存 localStorage（`useSettingsStore.ts`、`client.ts:34`） |

**后果**：一份恶意 datasheet 的提示注入，就可能读到密钥、外传、甚至烧录任意固件。

### 1.5 质量门禁（Quality Gate）

| 成熟项目 | 这个项目 |
| --- | --- |
| 每个 PR 过 CI + lint + typecheck + test | 无 CI；lint/typecheck 靠手动 |
| 失败测试阻断合并 | 当前 20+ 失败仍留在工作区 |
| E2E 覆盖刷新/断网/HITL/上传 | 无 Playwright/Cypress |
| 可复现安装 | `npm test` 当前跑不起来；README 的 `npm ci` 与现有 pnpm 布局不一致 |

**后果**：所有“通过”都是局部快照；没有人能说“这份代码是绿的”。

### 1.6 可维护结构（Maintainable Structure）

| 成熟项目 | 这个项目 |
| --- | --- |
| 文件 ≤300 行、函数 ≤10 行（你的 CLAUDE.md 也这么写） | 11 个文件 >1,000 行；60+ 个函数 >80 行；`chat_sse` 471 行、`event_generator` 339 行、`publish_compacted_checkpoint` 331 行 |
| 一个概念一个实现 | 6 个死模块：`microcompact`、`PermissionMatcher`、`PluginManager/RedactPlugin`、`session_search` 旧路径、`store_adapter`、`tool_events` |
| 注释与实现一致 | `microcompact.py:4` 说“每轮调用”（没有）；`file_ops.py:14` 说“run_command 从这里导入”（没有）；`session_search.py:5` 说“存储消息对”（从未写入） |
| 分层单向 `app → src` | `src/agent` 反向导入 `app.api.sse`/`app.db`；`src/rag` 导入 `app.main` 私有符号 |

**后果**：改一处要考虑三处；新人无法遵守规则；死代码让测试/搜索充满噪声。

### 1.7 可复现交付（Reproducible Delivery）

| 成熟项目 | 这个项目 |
| --- | --- |
| 一条命令启动；CI badge；release/tag | README 有步骤，但 `npm test` 当前失败；无 CI badge/release |
| 依赖锁定 | Python/Node 版本、pnpm/npm 布局不一致；`tiktoken` 未显式声明 |
| 升级/回滚 | Alembic 只有 3 个 revision，11 张表中 `feedback/token_usage` 无迁移；`init_db` 用 `create_all` + 手写 ALTER |
| 数据备份/恢复 | 无备份/恢复流程 |

**后果**：换一台电脑/换一个 Node 版本/升级数据库，都可能“我这儿能跑”。

---

## 2. 问题总账（按严重度）

> 只列有代码/实测证据的；不是全部，但足以说明“极其不成熟”。

### 2.1 P0：会让用户丢数据/泄密/执行任意代码

| # | 问题 | 证据 | 后果 |
| ---: | --- | --- | --- |
| 1 | 前端 localStorage 是消息主真源，后端每轮只存最后两条 | `useChatStore.ts:1628`；`ErrorBoundary.tsx:32-33` | 出错/换浏览器/清缓存丢历史 |
| 2 | `ErrorBoundary` 无确认清空所有 `hwrag_*` | `ErrorBoundary.tsx:32-33` | 一次渲染错误丢全部本地会话 |
| 3 | `grep/ls/dir` 等 LOW 命令直接放行，`run_command` 是完整 PowerShell | `risk_classifier.py:76-80`、`permission_classifier.py:97-103`、`run_command.py:316-331` | 任意文件读取，无需确认 |
| 4 | `.enc_key` / `keys_store.json` 可被 `read_file` 读取 | `path_guard.py:225-234`、`auth.py:20-21` | 离线解出全部 Provider Key |
| 5 | `/api/build`、`/api/upload` 无认证 | `build_routes.py:131-161` | 任意人触发构建/烧录 |
| 6 | `options/lib_deps/extra_scripts` 原样进 `platformio.ini` | `pio_runner.py:184-211` | 构建期任意 Python 执行 |
| 7 | 首次无 Provider 时 `store-key`/MCP 管理免鉴权 | `auth.py:180-191`、`dependencies.py:42-44` | 首次配置被接管，拉起任意 exe |
| 8 | 前端明文存 Provider key/session token | `useSettingsStore.ts`、`client.ts:34` | XSS/本机脚本窃取 |
| 9 | 提示注入 → 读密钥 → `webfetch` 外传链 | `prompts.py:135,198`、`webfetch.py:150-177` | 恶意 datasheet 可泄露密钥 |
| 10 | `binary_path` 用 `normpath` 非 `realpath` | `pio_runner.py:163-171` | junction/symlink 绕过前缀 |
| 11 | 当前后端测试 20+ 失败、前端 2 失败 | 本机实测 | 没有可信基线 |
| 12 | SSE 无 `id:`/Last-Event-ID；HITL pending 只在内存 | `client.ts:255-385`、`chat_routes.py:98-101` | 断线/重启丢状态 |
| 13 | 普通请求每轮重置 checkpoint | `chat_routes.py:971-977` | 无法续跑/恢复 |
| 14 | 两套 FTS，`search_history` 永远空 | `session_search.py:42,48` | 历史检索功能整体失效 |
| 15 | 压缩结果不持久化 | `chat_routes.py:855-891,971-977` | 每轮重复压缩，长对话抖动 |

### 2.1.1 状态审计实测补充（比静态检查更严重）

| 级别 | 问题 | 实测证据 | 后果 | 修法 |
| --- | --- | --- | --- | --- |
| P0 | 删会话不删 checkpoint | `agent_checkpoints.sqlite` 139MB；52 个 thread 中 45 个无对应 session；767 行含 ESP32/STM32 正文（`crud.py:244-258`、`agent_factory.py:274-276`） | 用户以为删了，正文仍在磁盘；库无限膨胀 | 删除事务后按 thread_id `adelete_thread`；observations/feedback 同清；后台 GC |
| P0 | 本地缓存把已删/已截断消息重新 POST | `useChatStore.ts:1498-1536` 把“本地有、后端无”当未保存回传；`:608-623` 404 自动重建 session | 已删会话“幽灵复活”、截断旧消息刷新重现、重复条目 | 服务端 tombstone/版本号；404 不自动重建；删除广播使缓存失效 |
| P1 | `/api/search` FTS JOIN 永远 0 行 | `search_routes.py:23` 写成 `m.id = f.rowid`（id 是字符串）；实测该 JOIN=0，改 `m.rowid=f.rowid` 为 72，MATCH 'ESP32' 为 30 | 搜索静默返回空，LIKE 降级不触发 | 改 `m.rowid=f.rowid`；空结果再走 LIKE |
| P1 | Alembic 迁移链不可用 | 空库 `upgrade head` 在第二个 revision 失败；`9467...init` 只建 6 表，后续 revision 对不存在的表加列；`database.py:47-117` 用 `create_all` + 手写 ALTER 且吞异常 | 新环境无法升级；`downgrade` 留下半迁移 | 重建单一 baseline；`alembic stamp`；启动校验 schema，失败拒绝启动 |
| P1 | 聊天历史真源在浏览器 | `chat_routes.py:740-755,979` 只用客户端 payload；全仓无 chat 读 DB messages；`useChatStore.ts:2035` 用本地 messages，`:642` 超 200 条静默裁剪 | 换设备/清缓存/加载前发送，Agent 按残缺历史回答 | 服务端按 session_id 从 DB 组装历史；客户端只做渲染缓存 |
| P1 | HITL 暂停态只在内存且按 session 单键 | `chat_routes.py:100` `_PENDING_AGENT_REQUESTS`；`:512` 按 session_id 覆盖；`:974-976` 普通请求先 reset checkpoint | 重启/双标签/暂停期间再发消息 → 409 或恢复错请求 | pending 落库；按 request_id+epoch 键；暂停期间保留租约 |
| P1 | async 热路径同步 SQLite | `session_epoch.py:33-35` 每帧两查；`database.py:22` 同步 engine；实测 `journal_mode=delete, busy_timeout=5000` | 事件循环阻塞最多 5s，所有流卡顿/`database is locked` | async 驱动或 `to_thread`；开 WAL；按请求缓存 epoch |
| P1 | 分支复制丢时间/activity，排序无全序 | `crud.py:145-154` 不复制原 `created_at`/`activity`；`models.py:42` 仅按 `created_at` 排序；实测 44 条消息只有 2 个时间戳 | 同时间戳顺序不确定；`keep_count` 可能删错；分支时间线失真 | 复制原时间/activity；排序 `(created_at,rowid/id)`；keyset 分页 |
| P1 | 删文档/删 KB 留孤儿 big_chunks | `kb_routes.py:881-882` 只删 KnowledgeDoc；实测 279 条 orphan big_chunks（含 2 个已删 KB）；`kb_manager.py:843-858` Chroma 失败仅 warning 后仍删 DB | 已删内容仍占库；向量/DB 不一致；重传命中残留 | 同事务删 docs+big_chunks；Chroma 失败不删 DB；对账 GC |
| P1 | `/api/settings` 把 observation 正文当设置返回 | `crud.py:367-375` 只过滤 epoch 键；`tool_observations.py:106-109` 正文存 Settings | 响应可达 16MB，工具输出/内部键泄漏前端 | observation 独立表；settings 白名单读取 |
| P2 | UTC 时间无时区标记 | `models.py:39-40` SQLite 丢 tz；`crud.py:96` `isoformat()` 无 Z；前端 `new Date()` 按本地解析 | 中国时区时间偏 8 小时，跨天分组错误 | 统一 UTC + `Z`；前端按 UTC 解析 |



### 2.2 P1：架构/维护/正确性

| # | 问题 | 证据 | 后果 |
| ---: | --- | --- | --- |
| 16 | 6 个死模块 | `microcompact`、`PermissionMatcher`、`PluginManager`、`session_search` 旧路径、`store_adapter`、`tool_events` | 噪声、误判“已有能力” |
| 17 | 两套聊天流式状态机 | `chat_routes.py:1390-1490` vs `sse_adapter.py` | 行为不一致、双倍维护 |
| 18 | 三套来源编号 | `source_registry`、`search_docs.py`、`useChatStore.ts` | 引用错乱/去重失效 |
| 19 | `writer_lifecycle` 1,145 行 + `checkpoint_publish` 703 行 | 自述“不是完整 fence” | 复杂度/收益比失衡 |
| 20 | `tool_events.py` 575 行未接线 | 无生产调用者 | 白写 |
| 21 | 60+ 个函数 >80 行、11 个文件 >1,000 行 | AST 实测 | 无法维护/审查 |
| 22 | 注释与实现不一致 5+ 处 | `microcompact.py:4`、`file_ops.py:14`、`session_search.py:5` 等 | 误导开发/验收 |
| 23 | 全局可变状态 10+ 处 | `_TOOL_REGISTRY`、`_SESSION_TODOS`、`_queues`、`_IMAGE_CACHE` 等 | 测试污染/并发串数据 |
| 24 | `_PENDING_AGENT_REQUESTS` 以 `session_id or "default"` 为键 | `chat_routes.py:100,189` | 匿名会话互相覆盖 |
| 25 | 同步 SQLite 审计/观察写入 async | `audit_recorder.py:134-168`、`tool_router.py:183,402` | 阻塞事件循环 |
| 26 | `path_guard` 允许目录被移除，仅黑名单 | `path_guard.py:225-234` | 大量路径可读写 |
| 27 | `systemPrompt` 设置对 Agent 路径无效 | `agent_factory.py:167` vs `chat_helpers.py:135` | UI 误导 |
| 28 | `todo_write` 模块 dict，不落库 | `todo_write.py:60` | 重启丢 todo |
| 29 | MCP 配置只在内存 | `mcp_routes.py` | 重启丢配置 |
| 30 | 删除 session 不清理全部派生数据 | `crud.py:245-258` | 隐私/陈旧数据 |
| 31 | `feedback` 表/API 无前端入口 | `feedback_routes.py` | 功能半成品 |
| 32 | `BranchTree` 死组件；branch action 未完整 | 前端无 import | 功能半成品 |
| 33 | 配置常量重复 7+ 组 | 128000/4096/262144/300s/8000/4000 | 改一处漏一处 |
| 34 | `src → app` 反向依赖/循环 | `agent`/`rag` 导入 `app.api`/`app.main` | 分层破坏 |
| 35 | 52 个后端根目录临时脚本 | `backend/_*.py` | 仓库卫生差 |

### 2.3 P1：测试/交付

| # | 问题 | 证据 | 后果 |
| ---: | --- | --- | --- |
| 36 | 无 CI | 无 `.github/workflows` | 没有合并门禁 |
| 37 | 无 E2E | 无 Playwright/Cypress | 刷新/断网/HITL/上传未端到端验证 |
| 38 | `npm test` 跑不起来 | `node_modules/.bin` 缺失 | 交付不可复现 |
| 39 | 39/135 模块测试未提及 | 近似覆盖映射 | 核心工具/硬件路由无直接测试 |
| 40 | 测试大量 mock/fake | 测试文件 | 真实 provider/浏览器/硬件未验证 |
| 41 | 35 题 RAG 只有 7 题四指标完整 | `rag-quality-baseline.md` | 质量无统计意义 |
| 42 | 无 release/tag/CHANGELOG/CONTRIBUTING | 仓库 | 不是可交付开源项目 |
| 43 | Alembic 只 3 个 revision，2 张表无迁移 | `alembic/versions` | 升级不可靠 |
| 44 | `init_db` 用 `create_all` + 手写 ALTER | `database.py:45-160` | 迁移机制分裂 |

### 2.3.1 测试/交付实测补充（“绿”是假的）

| 级别 | 问题 | 证据 | 为什么不能证明可用 | 修法 |
| --- | --- | --- | --- | --- |
| P0 | 路由测试全局跳过鉴权 | `backend/tests/conftest.py` 自动把 `_load_store` 置空；仅 3 个 401 测试 | 生产鉴权全坏也能全绿 | 移除全局 bypass；补加密/过期/并发/损坏 store 测试 |
| P0 | 普通 pytest 会原生崩溃，绿色靠隔离脚本 | `verify_backend_isolated.ps1` 预导入 pyarrow、禁插件、HF 离线、内存 checkpointer、`--maxfail=1`；docs 记录普通 pytest 曾原生崩 | “全绿”是特定 wrapper + 子集，不是全量 | 修原生崩溃；CI 跑全量 83 文件，禁 maxfail/子集 |
| P0 | 依赖漂移、无 lock/hash | 实际 fastapi 0.115.6 vs pin 0.115.0；SQLAlchemy 2.0.50 vs 2.0.35；pytest 9.0.3 vs 8.3.0；pytest-asyncio 1.3.0 vs 0.24.0 | 换机器/换时间结果不同 | lock+hash；锁 Python；CI 矩阵 |
| P0 | KB 测试写真实 `backend/data` | `test_routes_kb` 用真实 `SessionLocal`，只断言 200+indexing | 测试污染用户库；索引完成/Chroma/BM25 未验 | 每测试临时 DB + 真 Chroma 轮询 indexed |
| P1 | `checkpoint_publish.py` 703 行 0 直接测试 | midrun 测试直接 patch `_do_compact` | 成功发布/重启恢复未验 | 真 LangGraph+SQLite 发布/重启测试 |
| P1 | 前端 152 个 jsdom 用例，关键 UI 0 组件测试 | KB/build/serial/来源卡无组件测试 | UI 主路径未验 | 补组件测试 + Playwright |
| P1 | 测试大量 mock/fake | 59/76 后端根测试文件用 mock，44 用 Fake | 真实 provider/浏览器/硬件路径未验 | 关键路径用真组件/真 DB/真子进程 |
| P1 | Agent 路径不写 TokenUsage，`done.usage=None` | `sse_adapter`/`chat_routes` | 无法从一次 run 追到 token/成本 | 统一 usage 采集；写入 TokenUsage |
| P1 | 三个互不关联的 request_id | middleware/AgentSnapshot/Telemetry 各自生成 | 线上无法归因 | 单一 `trace_id` 贯穿 HTTP→SSE→工具→检索→token |
| P1 | 平台依赖自动下载 | reranker 280MB、PlatformIO 200-500MB；OCR 依赖未装；HF_ENDPOINT 只在 `.env.example` | 新机器首次检索/编译失败 | 启动 preflight；离线包；文档化 |
| P1 | Windows 上 MCP 只 `process.terminate()` | `mcp/client.py` | 子进程/端口泄漏 | `taskkill /T` 或 Job Object |
| P2 | README 称 Python 3.10+，但 `datetime.UTC` 需 3.11+ | `session_search.py:55` | 3.10 直接崩 | 锁 3.11+ 或改 `timezone.utc` |



### 2.4 P2：产品/体验

| # | 问题 | 证据 | 后果 |
| ---: | --- | --- | --- |
| 45 | 无上下文占用/成本状态栏 | 前端 | 用户不知道快超限 |
| 46 | 压缩仅 banner，无前后/摘要卡片 | `ChatArea.tsx:380` | 用户不知道发生了什么 |
| 47 | 无手动 `/compact` | 无接口 | 长对话只能等自动 |
| 48 | 无记忆面板（只有文本框） | `LongTermMemoryEditor.tsx` | 无法逐条治理 |
| 49 | 无文档级 `doc_filter` UI | 只有 KB 选择 | 检索范围控制不完整 |
| 50 | 无系统通知/命令面板 | 前端 | 长任务体验差 |
| 51 | 消息列表无虚拟化 | `ChatArea.tsx:386` | 长会话卡顿 |
| 52 | 工作台状态全局单例，不按会话隔离 | `useWorkbenchBridge.ts:70-87` | 切会话串状态 |

---

## 3. 新开发优先级：先止血，再建不变量，最后加能力

> 上一版方案把 Memory/Plan/Subagent 排在前面，是**错的**。它假设“测试是绿的、基础是可靠的”。红队审计证明不是。新顺序如下。

### 3.0 优先级 Top 20（按依赖排序）

| 顺序 | 事项 | 为什么现在做 | 估时 | 依赖 |
| ---: | --- | --- | ---: | --- |
| 1 | 冻结功能 + 建分支 + 提交 7,428 行 | 没有回滚点，后面所有改动都危险 | 0.5d | 无 |
| 2 | 全量测试分诊，修到全绿 | 当前 20+ 后端/2 前端失败，没有可信基线 | 2–4d | 1 |
| 3 | GitHub Actions（Windows + Ubuntu） | 没有门禁，修绿也会再红 | 0.5d | 2 |
| 4 | 删 6 个死模块 + 52 个临时脚本 | 噪声下降一个量级，测试才有意义 | 1d | 2 |
| 5 | 恢复 path_guard allowlist + 密钥文件硬拒绝 | 任意文件读取/密钥泄漏 | 1–2d | 2 |
| 6 | 取消 `grep/ls` LOW 自动放行；`webfetch` 加 egress allowlist | 提示注入→读密钥→外传链 | 1–2d | 5 |
| 7 | `/api/build`、`/api/upload` 加认证 + 输入白名单 | 未认证构建/烧录、任意 Python | 2–3d | 5 |
| 8 | 前端密钥不落 localStorage（HttpOnly/后端代理） | XSS/本机脚本窃取 | 2–3d | 6 |
| 9 | 服务端 `messages` 成为主真源；前端只做缓存；404 不重建 | 换浏览器/出错丢历史、已删消息幽灵复活 | 3–5d | 2 |
| 9.5 | 删除级联：session/message/doc/KB → checkpoint/FTS/Chroma/observations/compactions | 实测 139MB 孤儿 checkpoint、279 条 orphan big_chunks | 2–3d | 9 |
| 10 | 只保留 `messages_fts`，修 `/api/search` JOIN（`m.rowid=f.rowid`） | 两套 FTS + JOIN 写错，搜索永远空 | 1–2d | 9 |
| 11 | 只保留一个 streamer；legacy 路径删/降级 | 两套流式状态机 | 2–3d | 9 |
| 12 | Token 计数修复（tiktoken + dict + 预算） | 中文长对话提前 4–10 倍失败 | 1–2d | 2 |
| 13 | 引用校验 + 页码统一 + doc_filter 前移 | `[srcN]` 可编造，页码错 | 2–3d | 10 |
| 14 | `runs/run_events/pending_approvals` + SSE id/replay | 断线/重启丢状态 | 1–1.5w | 9 |
| 15 | 压缩持久化 + 手动 `/compact` + 事件 | 每轮重复压缩 | 3–5d | 12,14 |
| 16 | Context v1（pipeline/pinned/UI） | 长任务地基 | 1–1.5w | 15 |
| 17 | Memory v1（表/抽取/检索/面板） | 最大能力短板 | 1.5–2w | 10,12 |
| 18 | Plan/Todo/Goal（plan mode + plans 表） | 用户可感知的编排 | 1–1.5w | 14,16 |
| 19 | Subagent researcher（隔离上下文） | 大手册/长日志 | 1–1.5w | 16,18 |
| 20 | Observability/Eval/OSS/Sandbox | 可观测、可评测、可发布 | 3–4w | 14,16 |

> 前 8 项是“止血 + 安全”，不完成不要做 9 以后；前 13 项是“单一真源 + 正确性”，不完成不要做 Memory/Plan/Subagent。

### Phase -1：止血（Week 0–1）

目标：让项目重新“可验证”。

1. **冻结功能**：不再加新模块、不再扩 writer lifecycle。
2. 建分支，提交 7,428 行改动；主树可回滚。
3. 全量跑 backend/frontend；把失败分成“真 bug / 测试过期 / 环境问题”；**修到全绿**，修不了的标记 `xfail` 并写原因。
4. 删除 6 个死模块 + 52 个根目录临时脚本；删除或接线 `tool_events`。
5. 修 `npm test`：统一包管理器（npm 或 pnpm），提交 lockfile，确保 `npm ci && npm test` 可跑。
6. 加 GitHub Actions：backend pytest、frontend vitest/lint/tsc/build；Windows + Ubuntu。
7. 写 `KNOWN_BROKEN.md`：明确哪些是“有代码但没验收”。

**退出条件**：`git status` 干净；CI 绿；全量测试通过；无死模块；无根目录临时脚本。

### Phase 0：安全止血（Week 1–2）

目标：堵住“任意文件/密钥/烧录”的 P0。

1. **路径**：恢复 allowlist 根；所有工具统一 resolver（realpath + reparse/symlink 检查）；密钥文件硬拒绝（`.enc_key`、`keys_store.json`、`.env*`）。
2. **命令**：取消 `grep/ls/dir` 的 LOW 自动放行；LOW 命令只允许受限 argv，路径参数走 path_guard；`run_command` 默认 ask。
3. **构建/烧录**：`/api/build`、`/api/upload` 加认证 + HITL；`options/lib_deps` 字段白名单；禁 `extra_scripts`；只接受服务端 `build_id`；`binary_path` realpath + 本会话归属。
4. **MCP**：管理接口鉴权；exe/args 白名单；首次配置不允许匿名拉起进程。
5. **密钥**：前端不再存 Provider key/session token 明文；用 HttpOnly cookie 或后端代理；日志/审计/SSE 全链路脱敏。
6. **网络**：`webfetch` 拒绝 loopback/link-local/私网；egress 域名 allowlist；外发需确认。
7. **认证**：`store-key` 首次配置加本地确认码；`audit` 接口鉴权；`bypassPermissions` 服务端枚举校验，HIGH/ALWAYS 仍确认。

**退出条件**：负向测试通过——`grep . .env`、`read_file .enc_key`、`extra_scripts`、`POST /api/build` 无认证、MCP 拉 powershell 全部被拒绝。

### Phase 1：单一真源（Week 2–4）

目标：一个概念一个真源。

1. **消息**：服务端 `messages` 成为主真源；前端只做缓存；每轮保存用户/助手/工具消息，而不是最后两条；`ErrorBoundary` 不再清空历史，先导出/确认；404 不自动重建 session。
2. **删除级联**：session 删除清理 checkpoint/FTS/observations/pending/feedback/compactions；doc/KB 删除清理 big_chunks/Chroma；提供后台 GC 与对账。
2. **搜索**：只保留 `messages_fts`；`search_history` 改为查它；删除 `sessions_fts` 旧路径。
3. **流**：只保留 Agent streamer；legacy 路径删除或明确标记降级。
4. **来源**：只由 `source_registry` 分配 `srcN`；前端不再生成 ID。
5. **Todo**：落 `todos` 表；与 plan step 共用 id。
6. **配置**：模型窗口/超时/阈值/大小上限集中到 `model_registry`/`settings`；删重复常量。
7. **状态归属**：模块级可变 dict 收敛为请求级 ContextVar 或 DB；删 `_PENDING_AGENT_REQUESTS` 的 `"default"` 键。

**退出条件**：同一概念只有一个入口；刷新/换浏览器/重启后消息一致；搜索有结果；来源编号唯一。

### Phase 2：恢复与正确性（Week 4–6）

目标：中断可恢复，引用可信。

1. `runs` / `run_events` / `pending_approvals` / `context_compactions` 表；`X-Run-Id`；SSE `id:` + replay；HITL 持久化。
2. 崩溃恢复：running → interrupted；只读工具可重放、写/烧录不自动重放；`run_id + call_id` 幂等。
3. Token 计数：tiktoken + dict 计数 + 预算模型。
4. 引用校验 + 页码统一 + doc_filter 前移。
5. 全量 35 题四指标基线；CI 夜间跑。

**退出条件**：断网 10s 可续；重启 pending 还在；中文 30K 不误报；未知 srcN 为 0。

### Phase 3：长任务能力（Week 6–9）

目标：真正能“长期工作”。

1. Context v1：压缩流水线、持久化、手动 `/compact`、pinned invariants、上下文 UI。
2. Memory v1：`memory_entries`、自动抽取、检索注入、记忆面板、删除传播。
3. Plan/Todo/Goal：plan mode（服务端强制）、plans 表、计划审批、todo 持久化。
4. Subagent：只读 `researcher`、独立上下文、工具白名单、摘要+引用。

**退出条件**：100 轮约束 100% 保留；跨会话记忆 ≥80%；plan mode 不能写；子代理主上下文降 ≥50%。

### Phase 4：质量与交付（Week 9–12）

目标：可观测、可评测、可发布。

1. Observability：`trace_id` 贯穿、OTel GenAI、Langfuse、token/成本/延迟。
2. Eval：60–100 题 RAG、20 Agent 场景、20 Memory 场景；CI 回归门禁。
3. Sandbox full：编译/烧录 Job Object/容器、网络/设备限制。
4. OSS：CHANGELOG、CONTRIBUTING、SECURITY、issue 模板、release/tag、demo、topics。

**退出条件**：每请求一条 trace；完整 golden set；release 可复现；CI badge 绿。

### 3.1 和上一版方案的关系

- 上一版《Agent 能力开发优先级与实施方案》里的**表结构/接口/流水线设计仍然有效**，不用推翻。
- 变化的是**顺序**：先 Phase -1/0/1/2（止血/安全/单一真源/恢复），再做原来的 Context/Memory/Plan/Skills/Subagent。
- 原因：没有绿色基线、单一真源和安全边界，后面的功能只会再增加一套实现。

### 3.2 7,428 行未提交改动怎么处理

不要试图“全部救活”。先分诊，按三个桶处理：

| 桶 | 判断标准 | 处理 |
| --- | --- | --- |
| **保留** | 有生产调用者、有测试、解决真实问题、比现有实现简单 | cherry-pick 到新分支 |
| **重写** | 方向对但实现过度/耦合 | 用更小实现替换 |
| **丢弃** | 无生产调用者、无测试、与现有实现重复、注释与实现不符 | 直接删 |

按当前证据：

- **保留**：token 估算修复、结构化摘要 schema、工具观察归档/回读、引用/页码修复、FTS 接线、部分 SSE 可靠性测试。
- **重写**：`writer_lifecycle` + `checkpoint_publish` → 每 session 一把 `asyncio.Lock` + checkpoint id CAS；`tool_events` → 合并进 `tool_observations`；`autocompact` 主函数拆成 pipeline。
- **丢弃**：`microcompact`、`PermissionMatcher`、`PluginManager`、`sessions_fts` 旧路径、`store_adapter`、legacy 纯 LLM 流、52 个临时脚本。

> 如果 2 周内不能把这 7,428 行修到全绿，就退回上一个已知可用的 commit，只 cherry-pick “保留”桶，重新用更小的实现做。**回滚不是失败，是止损。**

---

## 4. 删除/简化清单（“删除也是开发”）

| 对象 | 行数/规模 | 动作 | 理由 |
| --- | ---: | --- | --- |
| `microcompact.py` | 65 | 删或接线 | 零调用 |
| `PermissionMatcher` | 140 | 删 | 与 PermissionClassifier 重复 |
| `PluginManager`/`RedactPlugin` | ~110 | 删或接线 | 死钩子 |
| `session_search` 旧路径 | ~115 | 合并到 `messages_fts` | 从未写入 |
| `store_adapter` | 143 | 删或换 SqliteStore | 进程内丢数据 |
| `tool_events.py` | 575 | 删或接线 | 无生产调用者 |
| legacy 纯 LLM 流 | ~100 | 合并/删 | 双流式状态机 |
| `writer_lifecycle` + `checkpoint_publish` | 1,848 | 收敛成小适配器 | 过度设计，自述非完整 fence |
| `file_ops.py` shim | ~50 | 删 | 死兼容层 |
| `BranchTree.tsx` | ~? | 删或接线 | 无 import |
| 52 个 `backend/_*.py` | 52 文件 | 移到 `scripts/dev/` 或删 | 仓库卫生 |
| `todo_write` 模块 dict | 小 | 换 DB | 重启丢 |
| `sessions_fts` | 小 | 删 | 双 FTS |

> 先删再改，测试/搜索/审查的噪声会立刻下降一个量级。

---

## 5. 第一周具体任务（Phase -1）

### Day 1：冻结 + 提交

- [ ] 在团队/个人层面宣布“本周不加功能”。
- [ ] 建 `feature/maturity-reset` 分支，提交 7,428 行改动。
- [ ] 打 tag `pre-reset-2026-11-04`，保留回滚点。

### Day 2：测试分诊

- [ ] 跑全量 backend/frontend，导出失败清单。
- [ ] 每个失败标记：真 bug / 测试过期 / 环境问题。
- [ ] 修 P0 真 bug；测试过期的更新或 `xfail`；环境问题写进 `KNOWN_BROKEN.md`。

### Day 3：删死代码

- [ ] 删 6 个死模块（或接线）。
- [ ] 删 52 个根目录临时脚本（先移到 `scripts/dev/`）。
- [ ] 跑测试，确认删除没有破坏。

### Day 4：CI + 可复现

- [ ] 统一包管理器；提交 lockfile；确保 `npm ci && npm test` 可跑。
- [ ] 加 GitHub Actions（Windows + Ubuntu）。
- [ ] 加 lint/tsc/ruff 门禁。

### Day 5：安全止血第一刀

- [ ] 恢复 path_guard allowlist；密钥文件硬拒绝。
- [ ] 取消 `grep/ls` LOW 自动放行。
- [ ] `/api/build`、`/api/upload` 加认证。
- [ ] 写负向测试：任意文件/密钥/未认证构建必须失败。

---

## 6. 验收总表（重置版）

| 阶段 | 退出条件 |
| --- | --- |
| Phase -1 止血 | git 干净；CI 绿；全量测试通过；无死模块；无根目录临时脚本 |
| Phase 0 安全 | 任意文件/密钥/未认证构建/MCP 拉起进程全部被拒绝；负向测试通过 |
| Phase 1 单一真源 | 消息/搜索/流/来源/todo/配置各只有一个真源；刷新/换浏览器一致 |
| Phase 2 恢复+正确性 | 断网/重启可恢复；中文 30K 不误报；未知 srcN 为 0；35 题基线 |
| Phase 3 长任务 | 100 轮约束 100%；记忆 ≥80%；plan mode 不能写；子代理隔离 |
| Phase 4 质量+交付 | trace/成本/评测/CI；release 可复现；CI badge 绿 |

---

## 7. 最后一句话

**先让它“可信”，再让它“聪明”。**

现在的项目不是“再补几个功能就能成熟”，而是需要一次**基础重置**：删掉一半死代码/临时脚本，统一真源，修绿测试，堵住安全洞，然后再谈 Context/Memory/Plan/Subagent。

> 配套文档：
> - [Agent 功能全景](agent-feature-atlas-2026-11.md) —— 有哪些功能、成熟线在哪
> - [成熟度对标](agent-harness-maturity-2026-11.md) —— 与 Claude Code/Codex/OpenCode 的差距
> - [能力开发方案](agent-development-plan-2026-11.md) —— 各模块的表结构/接口/详细做法（顺序按本文调整）
