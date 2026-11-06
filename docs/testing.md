# 测试与测试数据

> 更新日期：2026-11-06。记录修复后的最终软件门禁，并单列修复前 606 项快照与 67 项失败轨迹；浏览器、硬件和 RAG 全量质量评测仍分别记录，不混作软件测试结论。

## 2026-11-06 Plan Mode 与 Todo v1

在 `feat/plan-todo-integration` 集成 WP1 Todo 后端（`b78a5d00`）、WP3 前端（`000eff4e`）和 WP2 Plan 后端（`0d7bfa81`），合并到 `w1-baseline`，并完成真实模型端到端验收。Todo 改用已有 `todos` 表作为服务端权威快照；Plan 使用新 `plans` 表保存版本，`plan` 是请求级只读模式，不写入持久化设置。

- WP1 定向（E 盘隔离 `TEMP`/`SQLITE_DB_PATH`，显式 `-p asyncio -p pytest_timeout`）：`test_todo_store/routes/tool_persistence/context/permission_mode_setting` 共 **43 passed**；含 `test_crud_message_idempotency`、`test_session_delete_resume`、`test_build_tool`、`test_protected_routes` 等关联组 **126 passed**。WP1 报告的全后端首轮为 **1791 passed / 68 skipped / 0 failed**（704s，exit 0）。
- WP2 定向：plan store/policy/tool/routes/execution/SSE 与 Todo、chat、HITL、factory、router、context-history 关联组 **145 passed**，含审查后新增的 `submit_plan` ctx、snapshot、build-agent、旧库建表、并发版本和 approved prompt 断言。
- 完整后端（含 `tests/rag_eval`，`--timeout=180`）：**1867 passed / 68 skipped / 0 failed**，812.55 秒，exit 0。此前含 `rag_eval` 的复跑曾在 eval 阶段停滞；本轮加单测超时后完整跑完，没有测试超时。该轮早于最后 3 项边界测试文件，产品代码相同；边界组单独 **145 passed**。
- 真实模型端到端（Chromium + `ccgo/gpt-6-luna` + 复制的真实 DB/keys 副本）：计划模式只读调用 `list_files` → `submit_plan`，计划横幅出现；批准后执行 `todo_write` → `write_file`，HITL 弹出 `write_file` 确认；点“允许本次”后文件创建、`read_file` 复核，最终回答“已按已批准计划执行”。文件 `E:/DevCaches/Temp/plan-todo-e2e/plan_e2e_note.txt` 内容为 `plan e2e ok`；无 page error。证据在 `E:/DevCaches/Temp/plan-todo-e2e/evidence/e2e-result.json`。
- 对抗复查发现并修复两个真实问题：`SubmitPlanTool` 追加在 `_ctx` 注入之后，真实 Agent 路径会把它当成非 plan mode（`4068d646`）；HITL resume 的 `_persisted_rows` 在 `get_db_ctx` 提交/关闭后读取过期 ORM 行，触发 `DetachedInstanceError`（`66f477fc`，含回归测试）。
- 浏览器 UI（seed 计划/任务，拦截 `/api/chat`）此前已验证：计划横幅、计划面板、任务清单、批准、清空、刷新恢复、会话隔离；计划模式请求体实测 `permission_mode="plan"`。
- 前端：目标 29 passed；全量 **51 files / 351 passed**；`tsc --noEmit -p tsconfig.app.json`、`npm run build` 通过；`eslint .` 0 error / 13 条既有 warning。
- GitNexus：刷新索引后 `detect-changes --scope all` 为 **3 files / 8 symbols / 0 affected processes / low**；`_persisted_rows` 上游 impact **LOW（6 impacted / 1 process）**；`build_tool_specs` 图谱无解析调用者（`UNKNOWN`），已用文本搜索确认实际调用点在 `agent_factory`。
- 未验证：真实硬件/串口/烧录；真实模型 E2E 使用文件写入任务，未覆盖硬件动作；RAG 全量质量结论仍按独立门禁记录。

## 2026-11-05 合并后完整后端门禁

在 `context-rebased` 工作树运行完整后端套件，环境为 E 盘显式 `TEMP`/`TMP`、独立 `SQLITE_DB_PATH`、`AGENT_CHECKPOINTER_TYPE=memory`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，并显式加载 `-p asyncio -p pytest_timeout`。

命令：

```bash
python -m pytest tests -q -p asyncio -p pytest_timeout
```

结果：**1440 passed / 42 skipped / 0 failed**，3,417 warnings，400.01 秒，退出码 0。42 skipped 沿用既有标记（含被真实 writer/SSE 验收取代的旧 StateGraph 夹具和已被 V3 契约取代的 legacy summary/pre-send 用例），本轮没有为通过测试放宽 owner、epoch、writer-lease、HITL 回执或 source-registry 校验。此前 248 passed / 1 failed 与 73 failed 快照保留为历史记录，不覆盖本次完整结果。

真实模型服务、浏览器端到端、实体硬件/烧录和新电脑空白安装仍未验证；自动测试通过不代表这些验收完成。


## 2026-11-04 上下文压缩与服务端历史定向验证

本轮在 E 盘隔离副本中验证服务端历史、预发送/图内压缩、工具输出归档、HITL/刷新恢复和前端 server-history 契约。运行前显式设置 `TEMP`/`TMP` 到可写 E 盘目录，并使用 `SQLITE_DB_PATH`、`AGENT_CHECKPOINTER_TYPE=memory`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。由于禁用了插件自动加载，必须显式追加 `-p asyncio`；否则 `asyncio_mode` 未注册，大量 async 测试会批量失败，不能把那类结果当成产品回归。

后端定向命令（在隔离副本的 `backend` 目录）：

```bash
python -m pytest \
  tests/test_server_history_request.py \
  tests/test_chat_compaction_source_sidecar.py \
  tests/test_compaction_generations.py \
  tests/test_compaction_checkpoint_publish.py \
  tests/test_autocompact_input_budget.py \
  tests/test_autocompact_tool_pairs.py \
  tests/test_autocompact_archive_binding.py \
  tests/test_autocompact_failure_preservation.py \
  tests/test_structured_checkpoint.py \
  tests/test_context_history_reader.py \
  tests/test_s05_context_compaction_acceptance_20261025.py \
  tests/test_generic_tool_observation_archive.py \
  tests/test_crud_message_idempotency.py \
  -q -p asyncio
```

结果：**248 passed / 17 skipped / 1 failed**，解锁 3,103 warnings，260.58 秒。17 skipped 中 15 项是被真实 writer-lease/SSE 验收取代的旧简单 StateGraph/publish 夹具，2 项是 archive-binding 的假 graph/lease 夹具。唯一失败项 `test_crud_message_idempotency.py::test_history_mode_bootstraps_empty_owner_history_and_reads_back_pointer` 在内存 SQLite fixture 中没有安装 `message_source_generations` 触发器，append-v1 读不到迁移 fence；生产启动会安装该触发器，因此这是测试夹具缺口，不是 append-v1 产品路径。`test_server_history_request.py` 单独 **6 passed**。

前端定向命令（在 `frontend` 目录）：

```bash
node node_modules/vitest/dist/cli.js run \
  src/stores/useChatStore.server-history.test.ts
```

结果：**10 passed**。`useSessionStore` **4 passed**；`tsc -b` 退出码 0。候选工作区扩展后的旧 `useChatStore.test.ts` 仍有 **32 failed / 20 passed**，集中在既有 Skills/聊天事件、usage 和 resume 路径；主工作树同一旧测试文件 **42 passed**，未发现这些失败由本轮 server-history 改动直接导致，但仍属于当前候选工作区的未收口项。

完整后端套件：**73 failed / 1166 passed / 17 skipped**，3,339 warnings，456.62 秒，退出码 1。失败集中在候选既有 `test_memory_clear_resume.py`（15）、`test_session_delete_resume.py`（14）、`test_rag_rank_merge.py`（11）、`test_skill_runtime_review.py`（7）、`test_chat_skills_integration.py`（6）、`test_mcp_agent_integration.py`（4）、`test_chat_source_registry_snapshot.py`（4）等套件；上下文压缩/服务端历史定向文件本身已通过。另修复 `deepseek-reasoner` / `deepseek-r1` 未在模型注册表登记导致预算 fail-closed 的问题，`test_reasoning_chat.py` 3 项通过；未注册模型预算解析新增 `test_context_budget_resolution.py` 7 项：显式 override 走 `min(override, 1,048,576)`/`explicit_override`，无 override 走 32K/`conservative_default`，并记录 `budget_source`。本轮没有修改其余失败区域，不能把完整套件结果称为全量发布门禁通过。普通对话历史由 SQLite 持久化，进程重启后可由服务端历史重建；HITL 待确认快照仍在进程内存中，重启后不能续跑该次确认。Provider 明确返回 context overflow 时的压缩重试辅助函数尚未接入流式路由，当前依赖本地预算 fail closed。真实模型、浏览器端到端、实体硬件/烧录和新电脑空白安装仍未验证。

> 2026-10-26 上下文压缩候选定向验收完成：13 个不同专项 case 和五个指定回归文件按分组运行有通过覆盖。最终真实 `_iter_agent_sse` + SQLite/host archive 集成为 `1 passed / 27 warnings / exit 0`，RunName `s05-context-compaction-final-20261026-11`；reader + ordinary 最终回归为 `91 passed / 27 warnings / exit 0`。前面的 `149 passed / 1 failed` 与各诊断轮保留，不相加、不冒称单次全文件或全后端通过。00 已核对源码、限定测试差异及实际隔离副本。完整命令、SHA、失败处置、一次越界工具下载及未验边界见[验收记录](reports/context-compaction-acceptance-20261026.md)。本段优先于下方进行中快照。

> 2026-10-26 上下文压缩集中验证进行中：候选树运行 `s05-context-compaction-final-20261025-07`，正式无 spy 原生协议验收为 `6 passed, 4 warnings in 20.14s`，exit 0。包括缺 lease 拒绝且不改状态、真实 drain/join 后保存及续跑，以及四项非法调度写拒绝。报告 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/s05-context-compaction-native-protocol-verification-20261025/report-round5.md` SHA 为 `CB89A050A0D7DE083A8936AE799613D7146B1C4DDEA507A40767C6071486CF12`。00 比较隔离副本与候选树的七个产品文件及测试，SHA 全部匹配。首轮旧 14 路径套件仍保留 `1 failed / 6 passed` 记录，其成功夹具尚未遵循新的 lease/drain 契约；不能被本次子集通过覆盖。用户可见边界、SQLite/正文引用链及独立定向回归尚未交付结果，不预记通过。

> 2026-10-23 本轮原生标准 Skills：三项实现与审查修正已交回停写，00 已授权最后集中验收，运行当前主仓库的隔离源副本，不能误用旧候选树 runner。范围为共享发现/包外状态/标准导出、固定版本导入后当前请求加载、Skills 不屏蔽普通工具与 MCP、原确认/路径/哈希/禁用规则、前端范围参数，以及前端测试/类型检查/构建。实现期间未执行测试；结果尚未交回，不预记通过。没有服务/真实模型/用户数据库操作。

> 2026-10-23 仅补录历史ordinary交接，不启动测试：旧turn01a15f7f实际exec74af5c02输出150pass/26warn/54.04s/exit0已回收，前后两hash及当前client D2D7042E/test ED66401C匹配。作者567按原报告记录，与150重叠不加；旧冻结窄片接受记录在s05-ordinary-budget-20261022-00/acceptance-report.md。当前所有近期Agent/身份/HITL/publisher/lifecycle代码仍未测，最终统一测试尚未开始，OpenCode longsummary/opaque/provider边界不被旧green覆盖。

> 2026-10-23 报告范围事故处置只做文件证据/hash：原报告已精确恢复D37508B4，十产品冻结hash匹配；没有测试、探针或运行验证，不计通过数。旧worker关闭，新fresh Luna/max只读设计未交付，不提前启用control/恢复或统一测试。

> 2026-10-23 replacement51B9FCD4交付只作源码核，四报告/九非写hash/initial suffix与actual门禁实序匹配；ownership/cancel/原兼容边界仍未运行，旧通过结果不覆盖此码。下一host RunControl/生命周期只设计，不能把native正常退出或GraphDrained当测试已通过/保存ack。

> 2026-10-23 新replacement事务所有权实施仍不运行验证：最终须覆盖unready不setup、foreign不commit/rollback、BEGIN失败不rollback、own stale/error仅回滚自身、rowcount1+commit ack、取消前后及取消后stale/error不吞取消、原InMemory/真实读回登记与initial模块不变。源码反例不当已复现数据事故。检索正文安全来源策略尚未成立，不给新归档加空白名单假测试通过。

> 2026-10-23 单函数恢复失败关闭已交源码非测试：autocompact94144BB3与四报告匹配，00核实际门禁先于修改，UNKNOWN仍未解析生产caller。失败/无压缩不调用最后问题fallback、成功须graph-applied、typed错误/取消/完整输入与原SSE/HITL分支仍只在最终队列，未运行或增加通过数。下一仅六源存储/删除/配额/coverage设计，不提前创建数据库、运行探针或测试。

> 2026-10-23 新一文件实施未开始统一测试：现仅autocompact恢复入口收紧最后问题fallback，最终须验失败/无压缩/不支持不调用fallback、不删原history、不新图重放、typed CONTEXT_LIMIT与取消传播、真实graph-applied才retry；此入口当前app/src未发现上游调用，不为测试方便接入生产。ToolMessage→provider内容连续性目前仅冻结源码阅读，不能替代最终真实ToolNode/实际wire样例，也不声称任意opaque输出可JSON/安全归档。

> 2026-10-23 producer修正已源码核非运行验证：route861531CD/adapterD2E7B2B与更新报告匹配，actual direct/merged consumer本地复核位置已核。正式测试仍全部延期；新路径必须在最终阶段实测真实START保存/None输入/读回、后续HITL状态不pin旧seed、消费者调度间epoch失效、取消及原默认分支。没有据此增加通过数。S05a工具观察/Skill恢复下一仅边界设计，尚非可测试完成状态。

> 2026-10-23 consumer源码已收仍未测试：route861531CD/adapter8BF79B80报告匹配，00发现outer检查到producer task之间的epoch调度窗口，正由原作者仅adapter修正。最终统一测试必须额外覆盖outer iterator通过后、direct/merged native消费前删除/清记忆，确认不启动原图/模型；不能仅在构造generator前断言通过。新producer-local复核不代表native内部await/allwriter全局fence。HIGH与闭包拟名门禁偏差保留；本轮没有执行该验证。

> 2026-10-23 初始模块源码修正后仍禁提前测试：factory7333D4AE/publisher691DDB9A已源码核，不是通过结果；START seen/updated None误判已修，两常量原事前impact遗漏及00误选base同名定义的分析偏差保留。下一仅route/adapter接线，最终统一验证须同时覆盖实际活动v4构造器、首次None输入与真实checkpoint pin仅一次、随后tools/HITL不读旧seed、direct/merged两流分支、producer启动前epoch、REFUSED/UNKNOWN不重投输入、完整incoming/原tail/receipt不确定及原默认调用兼容。当前没有运行这些用例或构建。

> 2026-10-23 caller/admission源码交接已收：三hash BB74BC8F/63A082C7/EAF39150核匹配，five符号事前门禁（LOW2/3/1与两个UNKNOWN）及六raw结果已核，不是测试。取消不转保原继续、尾部原对象/marker全保留、epoch失效走原error/failed done不fallback为源码发现。初始模块接下来只实施factory/publisher、无caller；最终统一验还须覆盖新连接setup在共享前完成、foreign事务不被setup/rollback偷改、原图/copy/writer/branch/callback/隐藏saver覆盖拒绝、首次START调度无重复输入、实际commit返回和exact raw/state读回、APPLIED登记状态未知不能重投输入、未知提交停止及取消join。Arendt13/12只读范围偏差不抹掉；未运行任何上述验证。

> 2026-10-23 恢复源码交接已收、当前仍禁测试：四文件恢复hash已核D7103B27/0B8AD034/6710186B/55F338EB，不是通过结果。原claim TTL问题源码改为owner生命周期、原图pending/epoch复核和精确queue清理；未启动body的ASGI dispatch清理已写，外部middleware根本丢弃未调用response不在其保证内，任意线程/直接重复Task.cancel/全native写终止仍无运行证据。event_generator独立impact事后补查流程缺口保留。下一三文件caller/load接入只实施：最终须验两caller真实save返回/读回后登记、完整有序incoming匹配/保尾、Skills/匿名不恢复、await期间epoch变更、head CAS/配额/提交后取消不重放；不准初始未ack seal借此追认。旧通过数不覆盖本批。

> 2026-10-23 交接异常后的待验状态（仍不运行）：01误读旧只读开工上下文，自撤已授权的路由/HITL/事件通道实现，现已冻结。三个文件回到任务入口，chat_routes.py 为部分回滚状态58587C4B...68178B；原作者报告不是当前实现完成证据。四文件恢复及claim生命周期修正已移交同Mill，当前尚无恢复交付。最终统一验证还须覆盖残留改动保护、无TTL活claim接管、BaseException及未启动响应清理、await准备后epoch复核、同session重叠图/pending/事件隔离；不得用旧绿覆盖。裸checkpoint事件/自然耗尽以及同连接tuple存在均不能充当已提交ack，初始发布观察未启用。额度错误后的原模型接续状态以实际返回为准，不当成测试或功能进度。

> 2026-10-22 新增最终待验（当前不运行）：publisher默认关闭分支/原bool保持，native实际save config与exact-ID读回后才confirm；新reader UUID不替代producer图ID，旧private prepared缺graph绑定不可用。graph已提交但登记失败/取消不假称回滚、不工具重放，to_thread ContextVar与重复取消join必须实际验。已发布parent扩展还须核stable head父marker/body/allrefs、展开raw前缀覆盖与实际graph tail区别、v1/prose/origin-only/orphan拒绝、删除/配额/CAS。01路由/HITL/事件通道正在实现，未进入测试；pre-send观察checkpoint事件本身不能证明commit，自然耗尽/写任务join方案目前仅源代码设计，不称支持。

> 请求图隔离基础层新增最终待验（2026-10-22，未运行）：旧build_agent_config结果不变；宿主request ID与业务session正确分离，畸形绑定不把graph UUID当session；窗口读取与拒绝审计仍取业务session。后续必须验证同session重叠请求不互删graph/HITL，SSE生产消费使用同请求通道，续跑仅用原host快照/config且错worker/歧义/缺失拒绝。当前仅分派基础层、尚未激活路由，无测试通过或并发安全接受。Mill私有层报告/当前hash只作源码交接，额度中断后补最终报告，不以此代替统一测试。

> 跨轮恢复分派实施新增最终待验（2026-10-22，当前不运行）：prepared-only/orphan/v1不能被当published；确认必须来自实际graph保存读回而非自报ID/布尔；head预期CAS/currentowner/epoch/全部source/body/receipt链完整性；incoming完整有序prefix与保尾；编辑/缩短/Skills/tool/非text/权限缺失保持原history或CONTEXT_LIMIT。prepared/receipt/head共享配额及删除规则、取消提交边界、多worker不支持时不猜可用。现仅私有层被分派，尚无publisher/route完整接入或接受。
> 2026-10-22 流程记录：01的search_docs_core fresh impact遗漏发生在编辑前，事后补LOW影响说明不补正该遗漏；报告最新EE19A317...45BEA0B，三产品hash不变。此为门禁/源码交接记录，不是测试、测试失败或功能接受。候选所有新差异继续等统一最终验证。

> 01来源状态/发布前复核新增待验（2026-10-22，未运行）：indexing/error/带error/未知来源不得进入Agent盘点与新检索；来源状态变更使旧缓存不命中，检索await期间签名变更或复核异常时不发布旧结果；管理页仍显示失败/索引状态；None/[]全启用及显式范围不放宽。test_rag_rank_merge.py旧_FakeDb不提供doc状态列，预计需在最终阶段补真实状态fixture，不为兼容mock给生产加fail-open、不改排名断言。该预计来自源码阅读，不称新测试失败。三产品当前hash及报告已00核，仍未接受。

> 分派输出保留新增待验（2026-10-22，未运行）：大于原8000字符阈值的output及关键后缀完整保留、原envelope/data/metadata语义、工具次数和权限/审计/attempt不变；metadata引用不能代替正文；下游正常请求完整输入、超限明确CONTEXT_LIMIT、不自动剪历史。Mill只改helper和说明，无测试断言变更。00已核当前源hash与交接，不把该只读核对记成测试通过。

> 2026-10-22 主控已按真人要求分派实施：01的S06范围/来源失效链与Mill的工具输出保留均为实现任务，明确禁止提前测试/探针/构建/模型回放/服务验证；结果只能记未测试。00收集待验证清单，待全部实现与接入就绪后统一安排，不把旧通过数用于新差异。

> 2026-10-22 连续实施新增待测（未运行）：调度缺工具/错spec/非法args/异常/取消与原envelope/权限保持，HITL pending/拒绝/停止不变批准，observer导入/存储失败不干预执行；LOW read_tool_event真实ToolNode→metadata locator→实际provider内容→同scope读取、匿名/standalone/错身份/会话/Skills/删除/坏ref/旧版本/完整JSON预算及disabled过滤。KB上传另测读大小≤cap+1、超限拒绝、无效chunk配置不得清理旧失败记录/写新文件或DB、合法重传/后台索引保持。本轮未新写/修改测试断言，均留待全部实现就绪后统一验证；不称原RED已转绿。

> 2026-10-22 尝试级生命周期实现，未运行：最终统一覆盖actual await前start、每次retry编号、timeout/error/cancelled记录与原异常/tuple/重试策略保持、归档失败不重放工具、取消不冒称撤销、1..16 attempt可记录范围与request64总事件配额、return原locator兼容、并发/删除epoch/隔离和上下文清理。dispatch/pipeline HIGH未编辑，只有timeout方法LOW窄改。这里无新测试统计或关闭P1结论。

> 2026-10-22 实施续接（未运行）：tool_events返回元数据改为schema2请求内有界链，新增previous-envelope hash与head、读事务snapshot及整链响应预算。最终统一验证另含并行append、event/head同事务rollback、缺中间/缺尾/缺head/错顺序、同call幂等与冲突、65项拒绝、head计共享配额、旧schema1不追认、单事件version2/序号与总JSON预算，以及删除期间读写。这里是待测清单，不是新RED/GREEN或通过统计；未增加/修改测试代码，也未执行测试。

> 最新真人测试节奏（2026-10-22）：恢复开发后，先完成全部计划内实现与接入，最后集中运行测试、探针、构建及回放；不再逐项开发逐项测试，也不提前派发独立测试任务。当前 Agent 最终请求总预算及其聊天/续跑错误状态为新增未测试实现，历史普通聊天567/独立150和结构化485/独立112仅证明各自旧冻结版本，不证明这些新修改。影响分析、源码阅读和保护现有改动不属于运行测试。

> 同轮工具返回元数据档案也未测试：最终集中覆盖宿主scope隔离、匿名/standalone/MCP不入档、Skills活跃不授原文archive、manual预载/stream/HITL绑定与换身份409、contextvar并行/取消清理、真实SQLite重开/错误scope/hash/预算/坏编码、同call幂等存储与冲突不覆盖、共享配额/满拒绝/删除epoch同事务purge与rollback。只记录固定元数据，不收集命令、参数、正文或任意错误，不把returned当completed；仍缺完整观察恢复链，不能以未运行的条件关闭该缺口。本轮没有修改任何测试文件或执行用例。

> 2026-10-22 ordinary-budget作者整组：`s05-ordinary-full07` 25模块 **567pass/165warn/157.82s/exit0**，含新57；仅client+新test两文件变动，旧测试未改。三仓库RED/GREEN及44项安全组完整命令见 `s05-ordinary-budget-20261022-00/author-report.md` SHA `95FAD000A3979D8171C974C406E59443C7F23684F7CE301CB740D4FD11485F43`；所有组重叠不加。新57含chat/stream晚约束完整输入、摘要失败/空/超UTF8/取消、runtime model/凭据不串、framing/无wire输出cap预留、duplicates/roles/textblocks和opaque-image拒绝。原32后端/10前端hash未改，新增两文件合并34冻结；仅既有01本人指定四模块复核已派，未提前独立接受。新符号UNKNOWN actualdefs/调用补核；parent方法HIGH预先gate，nested send closure单独gate晚于首tracer字段编辑的流程偏差在报告如实登记，不冒称全部逐符号事前gate。无真实provider/用户库/OS重启；普通opaque图像预算暂不可用、Agenttool全预算未验，不把567称全产品。tracked diffcheck0，new no-index check exit1为差异状态且无空白错误，不把它写成exit0。

> 2026-10-22 structured独立结果已00全文/实际命令核准：`s05-structured-independent01` 指定四模块 **112pass/27warn/43.39s/exit0**，含最终new50；实际命令 `exec-983b7e2b-d3e1-400b-ae40-d561f0460a33`，前后12hash匹配。独立报告SHA `F30851F2F886F4F02F998EE233ABF9EAAD93448FA7052992F75036577309DB1B`，作者485与112重叠不加。00当前后端32/前端10、factory/models核不变，机器表 `s05-structured-checkpoint-20261021-00/acceptance-protection-results.json`。严格schema/真实Graph+SQLite/多代引用与预算内完整摘要输入指定软件接受，真实provider语义/工具档案/全窗口预算/OS重启未验；01idle38。额外只读旧调查/skill与通用MCP资源列表已纠偏，不计GitNexus门禁或新的产品测试。下方复核中及纯37/1deselect是历史。

> 2026-10-22 下一普通聊天预算外置探针（非485回归组）：实际 LLMClient、仅 provider 合成，`s05-ordinary-summary-probe01` **1fail/1deselect/7.19s/exit1**，620字后的安全约束在发送摘要时被500字切掉；`s05-ordinary-budget-probe02` **1fail/1deselect/6.81s/exit1**，无history的固定system已超本地登记窗口仍发起一次调用。分别 `-k retains_late`/`oversized_system`，外置测试未载 backend/tests/conftest，实际输出没有 warnings，不能照抄其他运行3warn。runner/完整命令/条件见 `s05-full-budget-probe-20261022-00/report.md`；无网络/真实库，client SHA `2D6D0EC1531A66C446A11D5147563D4D7056A141B1C0F29C7A17126B403A1AA6` 未改，结构化32冻结仍匹配。当前是下一片新验收RED，非完整预算修复或 Agent tool-schema缺口已复现。

> 2026-10-22 structured 生产接入作者整组通过、独立复核中：`s05-structured-full11` 20 模块 **485pass/150warn/129.38s/exit0**，包含最终新50项和源内容在摘要期间改写拒绝。实际 `wire-red05` **1fail/38deselect/3warn/18.49s**（旧源码仍存模型槽位字符串）→`wire-green06` **39pass/3warn/18.81s/exit0**；原缺字段 Graph/AsyncSqliteSaver RED 同时转绿。旧夹具迁移 `compat-red07` **1fail/26warn/19.58s**，中间工具模型调用预期不符 `compat-green08` **1fail/178pass/33warn/55.06s**→`compat-green09` 八模块 **188pass/33warn/56.25s/exit0**。随后六种 SQLite 无效响应、四种有 seal 的无效父摘要组 `security-green10` **77pass/27warn/36.83s/exit0**（新49项+generations）；各组重叠不加，后加改写测试不冒充包含于188/77。只有合成模型、真实 Graph/隔离 SQLite，不叫真实 provider/OS重启。十二文件表、完整实际命令、门禁与32后端/10前端保护表见 `s05-structured-checkpoint-20261021-00/wiring-author-report.md` 及 wiring-*.json；既有01本人四模块只读复核已派，未提前独立接受。下文模块37pass/1deselect及未接入 RED 是历史，不再作为当前结果。

> 2026-10-21 structured 模块推进，尚非生产接受：仓库真实 Graph/AsyncSqliteSaver `s05-structured-repo-red01` **1fail/3warn/18.75s/exit1**，缺字段 JSON 的拒绝尚未接入；新纯校验模块缺能力 RED `module-red02` **1fail/1deselect/3warn/18.59s**→`module-green03` **1pass/1deselect/3warn/18.96s/exit0**。最终 `s05-structured-module-security04` 使用 `-k 'not rejects_invalid_output'`，**37pass/1deselect/3warn/18.54s/exit0**；该1项就是明确保留的生产集成 RED，不隐藏成 xfail/不删除，不能称全38项通过。严格 schema、引用槽位/原文 grounding、未验证 completed 拒绝、UTF-8/绑定膨胀等只证明模块接口；尚无 DB 生命周期/生产多代接入证明。现有29后端/10前端保护 hash 全匹配，原断言未改；无重跑旧整组凑数/独立复核派发。新源码/测试、设计/完整命令见 `s05-structured-checkpoint-20261021-00/author-report.md`。下方“下一仓库RED尚未开始”是历史，当前仓库RED已固化但未 GREEN。

> 2026-10-21 inherited指定软件独立接受：`s05-inherit-independent01` 四模块 **102pass/33warn/45.46s/exit0** 含最终28项，00全文/hash/actualcommands/四hash4/4核。独立报告SHA `C588FB61D4195617B1936899C5F3B6936C893DB713386343059B24663A4BCA6F`；与作者full08 433项/后加2项重叠不加。01idle22、无新agent/下载/仓库写入。下一外置结构化契约 `s05-structured-contract-probe01` **1fail/3warn/19.41s/exit1**：真实Graph/SQLite、当前narrative接受缺字段JSON并发布changedTrue14→11，是新九字段验证尚缺的RED，不是旧窄功能回归/原文丢失或工具执行。下一仓库RED/生产schema实现尚未开始，不重跑此已知外置红凑数；见 `s05-structured-content-probe-20261021-00/report.md`，四产品/test继续冻结。

> 2026-10-21 inherited host checkpoint作者：`s05-inherit-full08` 19文件 **433pass/150warn/127.76s/exit0**，包含新模块当时26项；其后新增provider多批locator/超大UTF8摘要 `provider09` **2pass/26deselect/26warn/26.23s/exit0**，最终新28项，不把2项冒充full08包含/不加总。三个真实仓库RED：repo-red01 **1fail/26warn/20.09s**（新host marker缺失）；conversion-red03 **1fail/1deselect/26warn/19.45s**；transport-red06 **1fail/25deselect/26warn/19.04s**（nested任意字段被携带），均最小GREEN后兼容通过。green02 **1pass/26warn/19.73s**；security04三文件 **75pass/33warn/38.94s**；guards05新25项 **25pass/27warn/26.37s**；green07新26项 **26pass/27warn/26.48s**均exit0，重叠不累加。全部既有隔离runner+新E SQLite/cache/TEMP+ D Python/PyArrow/asyncio-only。四冻结表/完整命令见 `s05-inherited-checkpoint-20261021-00/author-report.md`，既有01本人四模块delta中，未计独立接受；真实Graph三代不是三段模型回放/OS重启/实网，也不是九字段结构化内容质量证明。

> 2026-10-21 reader非作者指定四文件 `s05-reader-independent01` **73pass/32warn/36.73s/exit0**，00全文/hash/actualcommands及当前六hash6/6核验，独立报告SHA `1EFC8AF6B22221EECF54596ADCEF4F1B582EDDD26932541D20943425E39B86C3`；与作者407重叠不加，窄软件接受。01idle17，无新代理/GitNexus/下载/仓库写入。另外置 `s05-second-generation-probe01` **1pass/26warn/20.27s/exit0**：真实Graph/SQLite同进程重开、第二压缩changed=False、首11/追加前25/后25/旧事件可读，验证安全拒绝，不当多代实现通过或产品RED。源文件冻结；下一持久RED/结构化继承refs尚未实现，不重复此探针凑数。

> 2026-10-21 模型历史reader作者完整组 `s05-reader-full08`：18文件 **407pass/149warn/122.07s/exit0**。新测试34项含真实ReasoningChatOpenAI outgoing payload、registry/disabled、真实create_agent+ToolNode+ToolRouter+SQLite/合成模型消费、错误locator/身份/删除同ID/UTF8预算/存储失败/并行ContextVar/最大64manifest与坏refs保原对象。三个实际RED：provider-red01 **1fail/3warn/82.12s**；tool-red03 **1fail/1deselect/26warn/20.26s**（新模块不存在，非已有reader回归）；registration-red05 **1fail/2deselect/26warn/20.09s**。定向GREEN02 **1pass/1deselect/3warn/27.24s**、04 **1pass/2deselect/26warn/19.85s**、06 **29pass/27warn/28.63s**、07 **2pass/29deselect/26warn/25.65s**均exit0，与407重叠不加。用既有run-tests.ps1、新E白名单副本和SQLite、D Python/PyArrow/asyncio-only，全TEMP/cache/appdata在E。六hash和完整命令见 `s05-history-reader-implement-20261021-00/author-report.md`；既有01本人四文件独立复核进行中，未计接受。非真实网络模型/用户库/OS重启/多代压缩。

> 2026-10-21 后续普通writer外置特征探针：`s05-later-writer-probe01` 和补诊断 `probe02 -s` 均 **1pass/3warn/18.52s/exit0**，不加总。真实Graph+新E SQLite，节点挂起期间尝试压缩实际changed=False，原15/压缩后仍15/普通writer完结16，输入和回答保留；未复现全局旧writer P1、非产品RED、不据此关闭其他并发场景。源码未改。下一模型历史reader仅source-gate，无新pytest或实现通过结论。

> 2026-10-21 checkpoint 非作者实际五文件结果已00核：`s05-publish-independent01` **87 passed / 31 warnings / 37.22s / exit0**，包含最终17项新SDK/Graph/SQLite回归及四旧模块；独立报告SHA `7F790E5E48174D3F8D53D79B9C77598DB0C1C3CB62AA6B93B36E4226694468F9`，实际命令/九hash前后和00当前9/9核过。与作者362重叠不加，指定软件接受，非完整S05/实网/OS重启/全部writer CAS。01误用wrapper自动下载3包/242added已中断保留缓存，非产品测试红/graph通过。稍后普通writer P1另项目外probe01正在实跑，不提前写通过或失败。

> 2026-10-21 checkpoint 发布作者修复：E 隔离 `s05-publish-full11` 15 文件 **362 passed / 148 warnings / 110.17s / exit0**；它包含新16项与原14文件，真实 SDK/内存/SQLite/reopen/并发尾部/pending writes/失败回滚/取消提交边界/流中异步读，不与此前346或中途组累加。新增 actual create_agent + FakeMessagesListChatModel 单例 `agent12` **1pass/16deselect/3warn/20.05s/exit0** 在 full11 后加入，故不能称 full11包含它。持久repo-red02 1fail/3warn/19.00s、SQLite-red04 1fail/1deselect/3warn/19.78s、pending-red09 1fail/12deselect/3warn/19.06s 均已实际红绿；red01错路径0tests、stream-red06会话丢失仅缓存lastfailed，不编造统计。九文件作者表 `s05-checkpoint-publish-20261021-00/author-report.md`；01本人五文件独立复核已派，尚未接受本片。无真实模型/用户库/OS重启/全局线程CAS证明。

> 2026-10-21 请求绑定/受限GET窄范围接受：最终 full-green09 **346pass/148warn/109.24s/exit0**；原01独立85pass/38warn/38.03s，加header新两文件增量 `s05-wire-header-independent02` **23pass/31warn/23.83s/exit0**（不加总）。00全文/hash/actualcommands核 `report-header-verified.md` SHA `CCF582D05B13FD355F7AB13CAB3588AE86CF0F08CDA9555E1687484ECA832A37`、当前两hash2/2与其他六未变；GET条件P1窄关闭，不是全球token日志治理。01 idle46，无活跃任务/测试。真实Graph/SQLite更新的后续红灯如下，不能用此窄范围接受遮盖。

> 2026-10-21 接线首独立85pass/38warn/38.03s原命令已核；条件URLtoken P1另外加真实RED **1fail/22deselect/3warn/19.28s**。路由仅加header-only与query token拒绝后，最终 `s05-wire-full-green09` **346pass/148warn/109.24s/exit0**。这才是header收紧后的完整组，之前08/85对应旧两hash，均保留不累加/不冒充最新独立。01本人header-only两文件delta进行中；新scope hash与原8表兼容方式见作者报告，暂无新整体接受。

> 2026-10-21 真实 checkpoint 后续探针（产品冻结未改）：外置 `s05-real-checkpoint-api-red01` 实际 **1fail/2deselect/3warn/21.67s**，CompiledStateGraph内存恢复sync update后await dict失败且14→15；sqlite-red04 **1fail/2deselect/3warn/22.44s**拒绝同步接口；正确async/reducer-red03 **1fail/2deselect/3warn/22.45s**原14/拟11/实15。reducer-red02 **1fail/2deselect/3warn/20.07s**是外置手工seed as_node歧义，已只修探针，非产品红。不是模型实网或用户数据损失；不能用346/合成Agent边界通过掩盖实际checkpoint失败，下一片持久回归与最小修复另授。

> 2026-10-21 S05a 生产接线作者最终：`s05-wire-full-green08` 14 文件 **346 passed / 148 warnings / 107.39s / exit0**；前次 full-green07 为346/148/108.99s，不相加。持久仓库 red01 无绑定 **1fail/3warn/18.77s**、red02 有绑定 stub **1fail/1deselect/3warn/19.22s**、red03 转换丢 refs **1fail/3deselect/3warn/19.54s**、red04 缺读取 HTTP **1fail/3deselect/26warn/19.31s**、red05 错误封套 **1fail/22deselect/3warn/20.29s**均实际运行后最小修。中途1/2/4/51/66绿见作者report，不累加。full-green06 **1fail/22pass/31warn/26.28s**为新 fixture 未绑定 database.SessionLocal、真实 Sessions 表缺失；仅夹具补同一 SQLite 工厂。八文件冻结，01本人独立五文件组进行中，不提前接受。真实 ASGI/SQLite +假认证依赖/模型/Agent boundary，不是真 token/模型/浏览器或实际 LangGraph checkpoint 验证。

> 2026-10-21 S05a 内部接缝指定软件接受：作者 323 项/93.81s、01 本人独立 `s05-archive-independent01` 三文件 **91 passed / 60 warnings / 33.04s / exit0**，00 已核实际 runner 输出、全文/hash `A1CCF124470CCE0CAFC0E9AACCBAFEBC5B653D204EDB889E7038C878E8E8F3FF` 和当前三 hash3/3。01 idle33，无 P1。下条“复核中”为此前记录，非当前任务。各组不累加，尚未接生产压缩。下一接线外置 `s05-archive-unbound-red01` 实际 **1 fail / 8.37s / exit1**：无持久化宿主绑定仍替换，属于待实现新不变量，不是本片回归失败或真实用户丢失；生产未改，下一步新 gate 和仓库持久 RED→GREEN。

> 2026-10-21 S05a 内部档案接缝作者冻结：`s05-archive-lifecycle-red01` 私有设置值泄漏 **1 fail/1 deselected/3warn/19.42s**；新持久化 stub `red02` **1 fail/2 deselected/3warn/18.70s**；删除未清理新 namespace `red03` **1 fail/2 deselected/4warn/20.16s**。最小绿 3 项/19.94s，负面控制绿 45 项/27.93s；最终 `s05-archive-full-green03` 13 文件 **323 passed/143warn/93.81s/exit0**，各组重叠不加。真实 SQLite 保存/新 engine 重开、提交失败/删除回滚、范围/hash/权限/预算/容量与私有 namespace 已跑；不是独立 OS 进程或真实库/模型。作者报告 `s05-event-archive-seam-20261021-00/report.md`，01 本人短只读 delta 进行中，尚未独立接受；三个生产压缩 caller 未接此 seam，不宣称自动原文恢复。

> 2026-10-21 S05 删除失效指定软件接受：`s05-delete-full-green05` 13 文件实际 **266 passed / 143 warnings / 95.48s / exit0**；既有 01 本人 `s05-delete-independent01` 指定五文件实际 **100 passed / 119 warnings / 27.39s / exit0**。报告 SHA `D51EFF27D269924189460EA34C59899A4B3C05C7CC6BCFA6758B1D301F7B6B1F` 已 00 全文/hash/actualcommands 核，六当前 hash 6/6；stream 第六文件仅独立 diff 审、由作者完整组实跑。包含 15 项新生命周期用例，重叠组不加。Settings marker 损坏/DB 异常失败关闭、删除重存/重建及事务失败均通过；没有真实模型/库/页面或物理 checkpoint 删除验证。独立首次范围澄清误读没有被计通过，确认实跑后才接受。

> 2026-10-21 S05 会话删除失效作者回归进行中：仓库持久 RED `s05-delete-repo-red01` **1 failed / 1 passed / 26 warnings / 22.05s**；私有 Settings generation/快照核对补丁后，green01 六文件 **162 passed / 113 warnings / 26.23s**。空 marker RED **1 failed / 6 deselected / 26 warnings / 20.14s** → green02 新文件 **10 passed / 31 warnings / 20.57s**。扩组 green03 **1 fail/176 pass/119warn/27.67s**、full-green04 **1 fail/73 pass/33warn/25.09s**，均为旧 synthetic source/stream 测试未建 Settings 表；仅给两文件补真实 SQLite 夹具，原断言保留。full-green05 13 文件尚在运行，01 本人独立短 delta active，不能先称全绿或正式接受。现有软件阶段和新 15 项重叠不累加；报告 `s05-session-delete-implement-20261021-00/report.md`，未改真实库/网络模型/前端/AgentFactory。

> 2026-10-21 S05 有界完整摘要输入指定软件接受：作者 `s05-input-full-green04` 12 文件 **251 passed / 136 warnings / 93.58s / exit0**；既有 01 本人独立 `s05-input-independent01` 三文件 **47 passed / 26 warnings / 22.31s / exit0**，报告 SHA `C3CA1A540724050B245E8D6D90506CC4178E864B1A061F63083FDD5B8D731E08` 已 00 全文/hash/实际命令核，四当前文件 hash 4/4。无 P1 发现；精确能力声明、固定/输出开销、超限不发送/不写 checkpoint 仅软件接受，各组重叠不累加。01 初次额度中断没有测试/报告，不算一次验收；额度恢复后同模型完成，无新代理或升级。普通 chat 500 字、提供商容量和 JSON 大字符串峰值内存/真实语义另列未验。

> 2026-10-21 下一 S05 删除失效真实 RED：00 项目外 `s05-event-recovery-source-gate-20261021-00/test_session_delete_resume_probe.py`，`s05-delete-resume-probe-red02` 实际 **1 failed / 1 passed / 26 warnings / 20.54s / exit1**；真实 CRUD 删除后 GET404，真实 resume 仍未拒绝旧暂停快照。首 red01 的控制组 async-generator join 错误 **1 failed / 26 warnings / 21.92s** 是夹具错误，已只改外置脚本，不当产品红。未改生产删除逻辑/库，未消费删除组 SSE；报告源码证据和下一门禁见主计划。

> 2026-10-21 S05 有界完整摘要输入作者冻结：`s05-input-red01` 第 620 字后约束 **1 failed / 3 warnings / 23.49s** → green01 **1 passed / 3 warnings / 20.70s**；red02 工具来源缺失 **1 failed / 1 passed / 3 warnings / 20.87s** → green02 连原 pair/failure **24 passed / 26 warnings / 22.83s**；red03 多模态误串化 **1 failed / 2 deselected / 3 warnings / 20.85s** → green03 三文件 **47 passed / 26 warnings / 23.06s / exit0**。25 项预算测试覆盖完整内容、UTF8/累计/工具参数、未知能力、固定/输出预留、实际 LLMClient 假 transport 与三 caller；旧断言保留，组不累加。12 文件兼容 `s05-input-full-green04` 运行中；既有 01 本人只读独立 delta active，暂未正式接受新范围。项目外完整命令/hash/边界见 `s05-bounded-summary-input-20261021-00/report.md`，没有真实模型/用户库/服务调用。

> 2026-10-21 S05 工具组指定软件接受：01 独立 `s05-pair-independent-01` 实跑工具组/失败保留/流终止三文件，**39 passed / 26 warnings / 80.36 秒 / exit0**；00 已读实际命令完整输出、报告全文/hash（`5CA7ED739A3797D8E155E38D6E5D3E1F72CAB1610C36BC2A6C6B880A9BF9A2A2`）与两文件机器 hash 2/2，diff check0。结合作者 226 项回归，接受候选切分和无早期块保护的窄范围，组数不累加。没有新的 P1；独立报告明确跨两 AI 消息复用待决 ID 尚无单独冻结测试，不把代码分支当已实测覆盖。原 500 字输入缺口、完整预算/归档、真实长对话仍未验，产品继续冻结。

> 2026-10-21 S05 工具组切分作者 GREEN：新 13 项按公开 compactor 与三真实 caller 测试，先后真实复现跨切点孤立返回（1 failed/22.82秒）、空早期块仍摘要（1 failed/1 passed/22.97秒）、未解析请求被摘要（1 failed/11 deselected/19.57秒）、重复 ID 半组丢失（1 failed/12 deselected/28.58秒），逐个最小修复。最终 `run-tests.ps1 -RunName s05-pair-full-green-05` 指定 11 文件 **226 passed / 136 warnings / 128.52 秒 / exit0**，source `BAADDDF9405C042C1B5C09532300B2CF8080ED2E9F075F8F557C7EA9CF9FCF1A` / test `08D6EBF944D59025DBEF88602339E0BD52520B522A49F66474BDE30B46146895` 冻结；命令/中途 37/21 兼容组和全部边界见 `s05-tool-pair-preservation-20261021-00/report.md`，不累加重叠结果。仅改原模块两函数及新测试，01 正独立只读 delta，暂不正式接受新范围；500 字 formatter 与预算/归档仍未修，无真实模型/库/页面/服务验证。

> 2026-10-21 S05 成功压缩回放 RED：项目外 `s05-successful-compaction-replay-20261021-00/` 在真实候选模块上运行记录型假模型，**1 failed / 1 passed / 8.53 秒 / exit 1**；有效摘要控制通过，综合观测为 `constraint_visible=False`、`orphan_tools=['synthetic-call']`。前者为 500 字截断后的约束未进入摘要输入，后者为工具调用和返回跨切点失配。生产 SHA 仍 `A1FBBB9B45DCD9DCF3308F4E814C14810E5125D67522F79BABBB14ED68410CAB`，未改用户库或联网；不是已接受失败保留补丁回归。下一步先分离成对工具用例、门禁后最小修复，500 字预算输入另切片，不与下方已通过组相加。

> 2026-10-21 独立接受：01 `s02-b2-s04-delta-review-20261021-01/report.md` SHA `0018E5C4F27C9362CA3A8336614F45D3D848C4D64A336A257F40DE5780D0B879` 已由 00 全文/hash/实际工具核，指定三文件 **80 passed / 112 warnings / 25.58 秒 / exit 0**，八冻结 hash 匹配。00 原独立进程外置探针复跑为 **3 passed / 29 warnings / 113.20 秒 / exit 0**，从原 1 failed/2 passed 转绿；四不同 PID 同一新 E SQLite 保存替换、重启读取、清空、重启空值通过，正常和清空后续跑行为通过。faulthandler 90 秒仅诊断等子进程，不是断言失败或超时中断。接受候选 B2 / S04 清空旧暂停快照的指定软件范围；不和作者 196/旧组累加，未做真实浏览器/模型/用户库/发布验收。精确命令及新源码 hash 见两作者报告，生产继续冻结。

> 2026-10-21 B2 P1 / S04 作者联合 GREEN：`s02-b2-history-projection-implement-20261020-00/run-tests.ps1 -RunName memory-b2-compat-green-03`，九文件实际 **196 passed / 136 warnings / 31.07 秒 / exit 0**（B2/幂等 64、原记忆 38、新清空 16、上下文 9、来源快照 5、Skills 集成 64），不与下方 103/100 或旧 64 相加。01 首轮独立 56 passed / 57 warnings / 22.46 秒发现畸形 type 绕过，00 补八参数真实存储/旧行 GET 回归为 1 failed / 53 deselected / 4 warnings / 22.95 秒后最小修复；P2 文档疑问来自读取候选旧 docs，正式 main §5.16 已有相应契约。报告 `s02-b2-history-projection-review-20261020-01/report.md` SHA `D605AA21A35641DC732DC716CEA3EDDE0064ADD9DDA6A4BA2E5F2D8EC53C32DD` 已全文/hash/实际工具核。新 B2/S04 联合 delta 由 01 独立只读复核中，尚未正式接受完整 B2/S04。

> S04 红绿细节：新回归清空后仍允许续跑 RED 1 failed / 28 warnings / 20.30 秒；首轮补丁联合原 memory 组 1 failed / 23 passed / 43 warnings / 24.15 秒，随后扩组 1 failed / 142 passed / 129 warnings / 126.56 秒，均因旧合成续跑测试未建 Settings 表，导致新 epoch 读取失败关闭。00 为两份旧测试加真实隔离 DB 夹具，不弱化任何原断言，才得到上述 196 全绿。新清空 16 项含清空→替换、失败事务/非法清空不误失效、旧请求再次暂停、标记私有/畸形/读取失败、两独立 engine 共用文件 SQLite、异步准备期间清空竞态。生产三文件及新模块/测试 hash、命令及未验边界见 `s04-clear-resume-implement-20261020-00/report.md`；没有修改真实用户库或 model，独立进程外置探针仍在回验，不把 faulthandler 的耗时诊断输出当产品断言失败。

> 2026-10-20 实际 GREEN 与边界：00 B2 作者报告 `s02-b2-history-projection-implement-20261020-00/report.md` 记录真实 POST raw 字段入库 RED→GREEN，另含非法形状、合法点文件路径、异常关联/结果信封 RED→GREEN。最终新 B2 + 旧幂等 56、上下文 9、现有记忆 38 的同进程联合回归 **103 passed / 104 warnings / 24.44 秒 / exit 0**，命令为报告目录 `run-tests.ps1 -RunName final-mixed-02` 和报告所列六文件；不与旧组相加。首次 final-mixed-01 写错记忆测试文件名、收集零项，纠正调用后通过，非产品断言失败。B2 正由非作者 01 独立复核，暂不记完整 S02 通过。

> 上下文失败保留指定软件范围：04 仅改 autocompact 无有效摘要返回原列表及新增 9 项测试；00 全文核作者报告 SHA `3F3C002943D9E4741B6AA22CACA6F41540E57D37833029DAF51872F1862511A4` 并独立运行原外置 5 项 + 新 9 项，**14 passed / 8.91 秒 / exit 0**。00 随后移除新增测试全局 sys.modules 配置替换，防同进程污染；最终上述 103 项联合通过。生产 SHA `A1FBBB9B45DCD9DCF3308F4E814C14810E5125D67522F79BABBB14ED68410CAB`，测试 SHA `29D35E2E88FAC0993428B24DC99C92B98080645C92789741EC7B45D3C626E7AE`。仅接受失败保留窄范围；未完成原事件归档、预算/消息配对、500 字限制修复、真实模型长对话或完整 S05。

> S04 新外置探针事实：真实 E 文件 SQLite 四个独立后端进程保存替换→重启读→清空→重启空值通过，普通续跑控制通过；清空后原暂停请求仍送旧合成记忆为 RED。02 `s04-clear-resume-restart-probe-20261020-02/test_s04_memory_isolated_probe.py` 最终完整实际输出 **1 failed / 2 passed / 28 warnings / 83.85 秒 / exit 1**，其后线程漂移已撤权，未接受未交付报告。此轮无真实用户 DB/模型/浏览器；00 正补仓库回归并按计划修失效路径，不能把现有基线通过当成完整记忆闭环。

> 2026-10-20 上下文失败保留 RED：00 在 E 盘项目外对真实候选 `autocompact`/恢复包装运行合成消息与假模型、记录型 Agent，结果 **4 failed / 1 passed / 8.61 秒 / exit 1**。异常三次耗尽、None client、空摘要、空白摘要均错误更新上下文并返回成功；正常有效摘要控制通过。报告 `s05-failure-preservation-red-20261020-00/report.md` SHA `C39913A1085F7FCF6A02190FCE6A4541D98D33C7E3DC1815B9FE387AFA7CFB0D`；生产文件 hash 仍 `6A751DCB…018E60`。这是真实函数行为 RED，不是安装/收集失败；只证明写回 fake checkpoint 的早期约束缺失，不声称用户真实 DB 记录丢失。无产品或仓库测试编辑，待 impact 后最小修复和独立复核。

> 2026-10-20 手动记忆现有基线：00 全文/hash 核 02 `s04-memory-isolated-baseline-20261020-02/report.md`（SHA `2BCB892017F645CB5C3480745420E4DD598DC4E8E121CDF1F217F638B415FB21`），并读真实命令输出、当前核 17 项文件 hash 全部匹配。候选白名单 E 隔离后端三个指定文件 38 passed / 50 warnings / 83.04 秒 / exit 0；前端编辑器与设置 Store 两文件 29 passed / 20.16 秒 / exit 0，含非记忆 provider/retrieval 回归，不统称 29 条记忆案例。前端首次外置配置导入错误发生于收集前、0 tests，修正后实际通过；合成失败日志符合失败用例，不是产品失败。仅接受现有软件基线，不代表真实页面、独立进程重启、清空后旧暂停快照失效、分范围条目或完整 S04 完成。未改仓库源码/测试或真实数据，下一步只补 E 外置重启与清空续跑探针，不重跑本基线凑数。

> 2026-10-19 B2 scope 已核（无测试）：05 报告 SHA `E0FEFF65336A3E89FC58A4000B4F5E00FAE7A029C46D45E99527C849CE9B3725` 已全文/hash 核，两树六关键文件 hash 当前匹配报告。POST 及消息列表/会话详情两个 GET 的活动记录入口只经源码核查；未运行 pytest、应用导入或数据库。GitNexus 完整缓存缺失、恢复区副本不完整，00 仅只读验证已有离线包版本 1.6.12 和 SHA `96391A6D…D141E1`，未恢复安装/下载/运行片段，不记 impact 通过；待真人一次确认后再处理工具恢复。

> 2026-10-19 B1b 指定软件范围独立接受：00 全文/hash 核 04 `report-verified.md`（SHA-256 `119AD5E89863F51E7C67C42CAFD9DF461C75F5D2654833F92A5B08116C5628BA`），读独立定向 1 passed / 30 skipped / 6.37 秒（计时 7.186 秒）、原 prototype 探针 1 passed / 2.22 秒（3.162 秒）、完整组件 31 passed / 0 skipped / 2.52 秒（3.318 秒）、tsc exit 0 / 14.900 秒。测试前后十 hash/HEAD/status 不变，00 当前再核全部匹配。继承路径不显示、label 无法验证、raw 哨兵不显示；合法 own path 保持。各组重叠不相加，首次配置收集失败 0 tests 与产品断言结果分开。接受候选 UI 软件范围，不含真实浏览器/页面样式/服务器历史/真实存储/完整 S02/发布；P2 英文标签未修。05 已接 B2 只读范围/impact 门禁，无测试或实现权。

> 2026-10-19 独立 delta 首轮停止不是产品测试失败：04 报告 `FDFD966E…F3AA0E` 未运行四项验证；其压缩后核验脚本预期组件 hash 末位为 6，实际/计划/作者报告均为 1。00 读实际工具记录并重算十 hash，写项目外机器可读 `baseline-00.json` 后明确恢复同一独立任务。新测试记录待 `report-verified.md`，保留首轮停止报告，不补写未执行结果、不重跑旧 100/后端组。

> 2026-10-19 own path 作者续接验证完成：报告 SHA-256 `1685C435184C9505DDAC28BA32F5A9147BC00992C0ED951269560A10EB6BBBCD` 已由 00 全文/hash 核，读实际组件 31 passed / 0 skipped / 2.67 秒（命令计时 3.554 秒）、原 04 探针 1 passed / 2.24 秒（计时 3.108 秒）、tsc exit 0 / 计时 1.986 秒、diff check exit 0。探针标签为“无法验证”、DOM 无继承路径及 raw 参数结果哨兵。较早冷启动同组也过，重叠不相加；若干 PowerShell 解析失败与配置发现失败没有执行目标，与产品结果分开。当前十 hash 匹配且恢复 turn 无仓库 fileChange。04 已接独立 delta 验收，尚无独立通过；不是完整 B1b/S02/浏览器/真实用户存储或服务端历史出口通过。

> 2026-10-19 恢复验证登记：10 月 15 日暂停前 own path 新组件回归实际 RED 1 failed / 30 skipped（3.07 秒）→ GREEN 1 passed / 30 skipped（2.20 秒），原 04 外置探针另一次因文件访问配置在收集前失败，0 tests，不当产品红灯或通过。00 读原 turn 并重算当前组件 `DE95BD7C…922AB1` / 测试 `DBB6EC86…C54EE`；05 本轮只续完整组件、tsc、原探针及项目外报告，无仓库源码/测试写权。尚无恢复后的测试结果，待作者交接及 04 独立 delta 复核，不标 B1b/S02 放行。

> 2026-10-15 B1b 独立 30 通过但原型 path 门禁失败：00 全文/hash 核 04 报告 `27F741953AB43996D39AAF53A2E59E6AE6BDB374186244C901F4995D68F27EF3`，实读独立组件 30 passed / 0 skipped / 2.86 秒 / exit 0、tsc exit 0（命令 9.060 秒）。外置真实组件 `prototype-path.probe.test.tsx` 两次 exit 1；最终 1 failed / 2.10 秒，两个 soft 断言分别确认继承 path 进入 DOM 且状态误标成功，不算两个测试。raw args/result 哨兵未进入 DOM；十文件 hash 不变。05 仅获原 UI 两文件 own path 增量红绿权，未放行 B1b 或完整 S02。语言标签 P2 另记，未测真实浏览器/原型污染实网来源，不重跑旧 100 凑数。

> 2026-10-15 B1b 作者结果已核、独立复核待交：00 全文/hash 核 `s02-b1b-safe-trace-ui-implement-20261015-05/report.md`（SHA-256 `3D41A58B59B9B748AF06D9EA84B5677E818F75F3761274BD715C1865213DC018`），实读隔离 hook 后现有组件 raw 参数 DOM 哨兵 RED 1 failed → GREEN 1 passed，最终完整组件 30 passed / 0 skipped / 3.43 秒 / exit 0、tsc exit 0（命令 9.831 秒），diff check exit 0。初始测试 hook 导入噪声、中间夹具默认值错误的 28/1 单独保留，不当最终产品失败；29 与 30 重叠不相加。组件/新测试 hash 与作者交接一致，B1a 八文件未变。04 已接非作者独立组件/tsc 与必要小探针，尚无独立结果，不标 B1b、完整 S02、浏览器或真实 SSE 通过；运行副本未同步。

> 2026-10-15 B1a 指定软件范围独立接受：04 报告 SHA-256 `444FC6E00E81B22F7BB69194EE667FEB1A2F450559BD2150444B3B3BE84089BA` 已由 00 全文/hash 核，实际三文件 100 passed / 5.82 秒 / exit 0、真实 init 探针 4 passed / 2.61 秒 / exit 0、tsc exit 0 / 11.340 秒；八文件哈希前后与 00 当前复算一致。作者 100 与独立 100 分开记录，不相加；初版 95/1 红灯保留过程证据。软件范围仅候选前端投影/绑定/缓存，不含真实浏览器/用户存储/完整 S02/发布。已派 05 ActivityBlock + 新测试两文件 B1b 渲染红绿，暂无 B1b 测试结果；不重复已过 100/旧后端组凑数。

> 2026-10-15 初始化联动窄修作者结果：报告 `095BB254…4DD8D` 已由 00 全文/hash 核；读实际三文件 100 passed / 5.30 秒 / exit 0，tsc exit 0 / 8.617 秒。旧迁移 RED 1 failed / 46 skipped、新空分片 RED 1 failed / 47 skipped → 二例 GREEN 2 passed / 46 skipped；另三初始化定向 GREEN 3 passed / 48 skipped。各组重叠不相加成更多覆盖。store/test 当前 hash 与交接一致，其他六文件未变；05 已结束写权释放。04 已接独立增量源码/联合复跑，尚无独立通过，不标完整 B1a/S02 通过，不重跑旧后端组凑数。

> 2026-10-15 联合独立红灯：04 报告 SHA-256 `F3314FAB876A09685E097549E9EA799C1CFF13AD2C5656AE98C16DED13956D65` 已全文/hash 核。00 读实际三文件 95 pass / 1 fail / 4.41 秒 / exit 1，失败 `projects legacy session messages before migrating them into shards` 单独重跑 1 fail / 46 skip / 2.75 秒，缺分片导致断言读取 null。外置真实 init 探针 2 pass / 3.02 秒，无可见孤儿会话增加证据；tsc 报告 exit 0。最初 ESM 配置/测试发现错误未运行目标测试，与产品稳定红灯区分。八冻结哈希不变。05 已获 store/test 初始化两文件窄修，先真实红绿再联合/tsc，04 后续非作者复核；当前没有联合通过或完整 S02 放行。

> 2026-10-15 两项修复待联合独立复核：00 已全文/hash 核 05 配额报告 `AFBE9E3C…6F8624`，实读最终 persistence 14 + helper 35 = 49 passed / 1.77 秒 / exit 0，tsc exit 0 / 命令 8.346 秒；此前新增测试 this 类型未标注 exit 2 保留过程记录。03 store 新回归实际 RED 后两例 GREEN / 45 skipped / 2.04 秒，未跑 store 整组/最终联合验收；其后漂移旧后端 P0-2 测试 1 passed 不算本轮，已要求只回范围清点后结束。四修复文件哈希由 00 当前重算冻结，04 已接非作者只读联合复跑及真实 init 小探针。尚无独立联合通过，不以作者绿色或旧 83 累加成完整验收；旧整体 legacy 初始化删除 ID 的内存/可见边界待核。

> 2026-10-15 定向 RED 诊断已交接：报告 SHA-256 `AF2124081DD9189F2C9F2C2AA32C51F5C6FD19C1091AAAD5C5DD59BE7D2ED228` 已全文/hash 核。项目外真实 store/init 合成探针两次 exit 1，normal/resume 错配及迁移配额三项 RED、匹配名称对照通过；00 已读实际失败输出。错配只证实活动/内存步骤哨兵与误成功，POST/分片已有过滤，非真实泄露记录；迁移丢副本为 HEAD 原有问题。首次外部配置加载限制发生测试前，改项目外最小配置才实际执行。已派 03 store/test 与 05 persistence/test 两项分文件窄红绿；本轮修复尚无最终通过证据，联合验收须两作者冻结后独立运行，不累计旧 83 或重复后端组。

> 2026-10-15 B1a 独立复核：05 项目外报告 SHA-256 `BE699F5294B725AA766623F63C39F4D1842B56F1894F0FDF6B47F4F622D17728` 已由 00 全文核验。实际独立三文件 83 passed / Vitest 16.64 秒 / exit 0，TypeScript exit 0（命令 14.593 秒），八文件哈希前后不变；不与作者 83 相加。初次临时目录存在检查 exit 1 发生在测试启动前，换唯一 E 根后通过。两项条件性源码发现（同 call_id 工具名错配、旧 key 迁移分片配额失败）尚无定向红灯，03 已接项目外真实入口离线复现，暂无仓库修复权；不是完整 B1a / S02 / 浏览器 / 真实数据验收。

> 2026-10-15 恢复 / B1a 作者交接核验：00 已读 03 实际最终输出，`D:/Node.js/node.exe node_modules/vitest/vitest.mjs run src/stores/useChatStore.test.ts src/utils/skillTrace.test.ts src/utils/persistence.test.ts` 在候选 frontend 中 3 files / 83 passed，Vitest 5.15 秒，exit 0；最终 `node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json --pretty false` exit 0（命令 12.153 秒）。批准文件 diff check exit 0，行尾警告非失败。八文件当前 hash 与作者最终交接全一致。既有定向 RED、中间 GREEN 和测试夹具类型失败是过程记录，不能累加测试数或隐去失败。未运行真实服务、模型、浏览器、用户数据库；运行副本未同步。05 独立只读复核已派，独立结果尚无，不提前放行 B1a / S02，不重跑旧后端组凑数。

> 2026-10-11 B1a impact 门禁：03 报告 `852C4C6F…16241` 已全文/hash 核；00 已读真实 impact 输出，保存 CRITICAL 67/16/17、读取 CRITICAL 19/1/13、保存体 HIGH 5/1/3（影响/直接/流程）。历史映射 UNKNOWN 以候选两处实际引用补核，不降为 LOW；候选 5 文件哈希不变，3 新文件不存在。未跑测试。00 已告警并另授 03 候选 8 文件窄实现，后续真实红绿与候选 store/helper/persistence/typecheck、05 独立复核待执行；不是主树 44、浏览器或全链验收。

> 2026-10-11 B1a 范围保护：02 turn 再次转回旧 P0-3 保存调查，00 已撤销继续实现并要求只交范围回执。已读工具记录暂未见 fileChange，主树/候选各 5 文件 hash 不变且 3 个候选新文件不存在；未跑红绿或验收，不作全仓无变化证明。下方 B1a 条件授权已撤销，先核回执，不重复旧测试或派恢复。

> 2026-10-11 B1a 登记：02 只读报告 SHA-256 `26654745C6DC850F60C10F0C5498E94103A1E63CE7C8F499872AB737C373E6D1` 已由 00 全文与两树 8 文件哈希核验，实际项目外目录为 s02-frontend-history-b-scope-align-20261011-02。没有运行测试，不把版本登记当验收。新 B1a 任务先 impact 门禁，后候选前端类型/helper/store/persistence 的窄红绿及现有依赖类型检查，HIGH/CRITICAL 须另批；尚无红绿结果。B1b 页面与 B2 CRUD 分别后续，主树 44 不复制到候选 43，不重跑旧后端组凑数。

> 2026-10-11 SSE A 增量独立复核已接受指定范围：00 实读 05 两条命令输出，新增 9 项为 9 passed / 55 deselected / 26 warnings、21.38s、exit 0，来源快照文件 5 passed / 25 warnings、16.34s、exit 0，均无 skip。报告 SHA-256 1F935413…B1460、三候选哈希再次一致；额外 warning 来自隔离 TRANSFORMERS_CACHE，非新产品失败。完整 64 项仅作者/00 已读证据，不是本次独立重跑；不相加。00 接受后端 A 软件范围，前端/历史出口和完整 S02、真实模型/浏览器/数据库仍未验，运行副本未同步。02 下一任务只读版本登记、不跑测试，不因单步完成终止全计划。

> 2026-10-11 内层成功字段窄修作者红绿已实读：两个 Skills 工具均通过真实 converter 复现缺失 data.success 误成功。选择器 `requires_explicit_boolean_inner_success or failure_without_metadata_or_data_success`：红灯 2 failed / 7 passed / 55 deselected / 25 warnings、12.12s、exit 1；修后 9 passed / 55 deselected / 25 warnings、15.95s、exit 0。完整 `test_chat_skills_integration.py` 为 64 passed / 25 warnings、11.49s，`test_chat_source_registry_snapshot.py` 为 5 passed / 25 warnings、10.46s，各 exit 0。均 D 盘已有 Python、E 独立 pytest/cache/appdata/TEMP/TMP、离线假数据；不是用户数据库、模型、浏览器或全量验证。00 核 helpers 40FFB65C…E4B9F / integration test A441B1EC…87DAD / adapter 未变；05 增量独立复核已派，尚未放行。不同组及旧快照不累加。

> 2026-10-11 独立复跑证据已读、审查仍未放行：05 用 D:/python/python.exe 在两个唯一 E 假数据根运行完整 `backend/tests/test_chat_skills_integration.py -q --tb=short --disable-warnings -p no:cacheprovider --basetemp <各自新目录>`，真实输出分别 55 passed / 25 warnings、66.60s 和 26.84s，均 exit 0。配置 TEMP/TMP/HWRAG_APP_DATA_DIR、缓存及应用数据到 E，模型离线、进程测试凭据清除。第二次为同范围复跑，不相加。随后上下文压缩漂回旧 Explorer，00 已停止该方向并要求范围回执；指定独立报告尚不存在，最小相关 SSE 兼容组和独立审查结论未交，不能宣称 A 步已获独立放行。00 再核三候选文件哈希不变，未运行额外 pytest。

> 2026-10-11 Skills SSE 安全投影 A 作者交接：仅 E 隔离候选三文件变更。00 实读作者定向组 `test_chat_skills_integration.py -k 'emit_skill_tool_call or emit_non_skill_tool_call or skill_tool_result_sse or fails_closed_on or skill_tool_failure_can_omit or non_skill_tool_result or manual_mode_loads_through_router'` 输出为 38 passed / 17 deselected / 25 warnings、13.88s、exit 0；作者红灯报告为 26 failed / 1 passed、exit 1。使用 D 盘现有 Python 与 E 盘合成数据、独立 TEMP/TMP、pytest/cache/HWRAG_APP_DATA_DIR，未测真实模型、用户数据库、浏览器、硬件或全量。00 核三文件哈希与交接一致（完整值见 development-plan.md 页首）；`git diff --check` 作者通过。05 独立只读复核及整文件复跑已派，尚未独立放行；不与旧 28/5/50 或中间复跑相加，不将本组当完整 S02 或运行副本通过。

> 2026-10-10 新手动/参考资料页面验收未执行：07 在 Chrome 新标签窗口第一次取状态时被 Computer Use 的 URL 安全识别停止；未导航项目、导入/编辑技能、输入凭据或请求模型，无本轮会话/工具调用证据。不算 pytest 失败或产品验收通过。07 精确收停本轮自有 PID 15164/5056，00 再查均退出且 58080/55875 无监听；v3 清单仍 D0F5A2A8…A09649E、brand 停用、playwright 不存在。本轮不重试或绕过该工具停止，旧真实 Chrome 成功证据不作此次替代。

> 2026-10-10 审计窄修独立复核通过：05 新回归 5 passed / 2 warnings、11.81s；`test_tool_router_request_spec.py` + `test_chat_skills_integration.py` + `test_skill_runtime_review.py` 组合 50 passed / 25 warnings、12.91s，各 exit 0。00 读取实际输出、核两文件 A326AE37…0BB166 / D83AAD78…83690 未变后接受指定范围。组合包含已验资源 28 项，不能累加成全量通过；仅内存 SQLite 及模拟工具链，不是真实旧库迁移、HTTP 工具或浏览器通过。

> 2026-10-10 独立资源增量审查已过：03 精确 instruction-file-removed 一例 1 passed / 3 warnings、13.69s；整文件 28 passed / 2 warnings、13.36s，各 exit 0。00 实读命令结果并核 B65BF33B…E1995C9 未变，接受指定新增范围；非 Router audit/浏览器验收，组间不相加。

02 审计持久化修复作者红→绿：真实 Router/Recorder/未替换 logger、fixture SessionLocal 与 SQLAlchemy SQLite 内存表，作者 4 failed / 1 passed、12.97s → 5 passed / 3 warnings、12.80s。修前 HITL deny 因不接受 call_id 未落行，修后实际 helper 投影保留 deny/false/PERMISSION_DENIED；旧脚本形态默认兼容。00 核两文件哈希 A326AE37…0BB166 / D83AAD78…83690 并读取实际输出。05 已派只读独立复核，当前仅作者通过，不是完整回归、真实旧库或浏览器通过；未运行 init_db/Alembic。

> 2026-10-10 缺失说明文件作者交接：05 新增 instruction-file-removed（快照后只删除合成 SKILL.md，保留目录和资源），作者实际精确一例 1 passed / 2 warnings、14.50s，整文件 28 passed / 2 warnings、14.29s；00 读取实际输出并核 SHA-256 `B65BF33BA70FF8995EEE1892D5B2FC8D9CD64AF30FF7F70A7772CC340E1995C9`。首次 Windows 大小写检查测试假设失败不是产品失败，修正后通过；组间不累加。03 新增 delta 独立复审已派，尚未独立放行。

> 审计诊断证据（2026-10-10）：00 读取 02 项目外报告/脚本并核哈希 27777626…86EEEE / 53A97F18…BC50F9，直接读取实际 7.514s 红灯输出（exit 1，四处结果字段不一致）。成功正常；技能结构化失败与 executor 异常在 Router/AuditRecord 为 false/EXEC_ERROR，旧签名 sink 却为 true/null；仅换扩展 sink 的反事实对照匹配。仅内存 sink 模拟，不是生产 SQLite 写入通过或权限绕过。02 后续窄修必须补真实 logger→SQLAlchemy 内存表回归先红后绿，不能把固定 legacy sink 的原脚本虚称修后通过；尚无修复运行结果。

> 2026-10-10 独立资源复审已收：05 使用 D 盘 Python 3.13.13、E 假包/缓存/临时根及内存数据库，整文件两次退出码 0，分别 27 passed / 3 warnings / 0 skipped、44.26s 和 13.92s；00 直接读取实际输出并再核原哈希 9DB16FC30924AE4DC5286F73F083E1F9E8CF7CBB929746EB11454B29EBC1B0C2。与作者 27 项重叠不累加。新增第三个警告是 TRANSFORMERS_CACHE 隔离配置的弃用提示。条件放行仅涵盖现有用例，不覆盖单独删除 SKILL.md 或 Router audit 结果。

05 后续仅获指定测试文件写权补“只删除整份 SKILL.md、保留包和资源”一例，生产代码冻结；该新增 delta 待另一个线程独立复核。02 审计诊断仍进行，未有修复结论。以上均非浏览器、主树或完整 S02 验收。

> 2026-10-10 17:01（Asia/Shanghai）运行状态：02/05 上次任务均以 usage limit 错误结束，不是测试失败或审查通过。00 核对账户已允许普通使用，已按原边界续接两项任务；尚未收到恢复后的运行结果。历史作者测试与浏览器证据保留，不重复计数。

> 2026-10-10 最新交接：02 最终资源回归整文件再次运行 27 passed / 2 warnings / 0 skipped、30.26s、exit 0，同哈希不累加；文件写权已释放。说明字段删除不等于 SKILL.md 文件删除，05 独立复审需明确未覆盖部分。03 旧配置测试不算审计诊断进展，诊断任务已撤销并改派 02 的项目外隔离红灯复现，目前尚无审计复现结果，未改任何生产代码。

## 2026-10-10 S02 持久资源回归作者验证，独立复审已派

00 读取 02 实际命令输出：快照后 package/instructions/resource 删除场景 3 passed / 18 deselected / 2 warnings；路径六例 6 passed / 21 deselected / 2 warnings；整文件 27 passed / 2 warnings / 29.29s，各 exit 0，组间重叠不相加。仅 E 盘自建假包与外部 marker、D 已有 Python、内存库/offline；不是实际模型或浏览器。五类路径返回 SKILL_PATH_INVALID，编码路径按原规则为 SKILL_RESOURCE_NOT_FOUND，没有改生产错误语义。

00 独立核候选测试 SHA-256 `9DB16FC30924AE4DC5286F73F083E1F9E8CF7CBB929746EB11454B29EBC1B0C2`、diff 114 增/1 删。02 明确冻结，最后交接尚待；05 已获派独立只读复审/复跑，未出独立结论。03 仅负责审计失败误记复现，不把旧 P0-2 重跑计为该诊断通过。新增测试不自动进入已停浏览器运行副本或主树。

## 2026-10-10 S02 页面报告及收停独立复核

00 已读取 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/s01b-runtime-20261009/reports/s02-browser-matrix-20261010.md` 正文及收停更新，Get-FileHash 得到 `3A99F321DAE198C001262423400BFA149888CDAE6B9A672191CC2922655CE465`。Get-NetTCPConnection 全表筛 58080/55875 得空，Get-Process 全表筛 21528/26288 得空；Test-Path 显示数据库/SKILL.md 存在、启用 marker 不存在，命令 exit 0。没有删除数据或停止其他服务。下方最后审计/报告/收停待核状态由本节接续；候选新测试和审计诊断未完成，仍不能记总体验收通过。

## 2026-10-10 停用加载失败与审计成功字段不一致

07 只读核最后会话 `s1791611369084`：两条消息、activity done/4 steps/2 tool steps；两次 load_skill 分别指向测试前缀与 brand-guidelines，两步 status=error、结果匹配失败摘要而非成功摘要。对应 audit 19/20 却均为 allow/auto_allow/LOW、success=1、error_type=null。00 页面看到明确失败，不能用这两行 success=1 宣称已加载，也不代表权限绕过。原始 DB 未修改。

03 已获派仅 E 项目外合成的实际 Router 复现与只读根因调查，未写生产代码或测试；报告/新测试结果待交。此记录修正上节“最后审计待交”，最终项目外报告与服务收停仍待核。

## 2026-10-10 Chrome Skills 四个真实行为对照，最终报告待核

00 实际页面输入、复用授权 Text 服务商；07 仅管理原 E 隔离服务并只读审计，未后端注入。手动会话 `s1791611032988` 用户 `2fd9aa4f-9b71-46d3-845c-711c9be7de99`、助手 `db6a9815-20e4-4b8d-b312-d3153598bcfd`：56.0s 终态，具体颜色及字体正确；07 核 audit 15/16 两次 load_skill 均成功，分别 manual_skill_selection/auto_allow，32/27ms，无其他工具。Activity 5 steps 含 thinking，不是五个工具；保留重复加载观察，不记为已修。

自动无关会话 `s1791611141498` 用户 `136edaf5-bc64-41e8-a1ed-b233f5c3a186`：17+25 回答 42、3.7s；07 核两消息、activity done/1 step/0 tool、无 audit。关闭会话 `s1791611205668` 用户 `3e8b74aa-8e1d-42bb-b3a5-e0d47006af63`：1m14s 终态，如实说明无法加载；07 核两消息、两普通工具 list_kb_docs/search_history（audit 17/18 均成功），无 load_skill。关闭 Skills 不代表关闭 Agent/RAG/历史。

页面停用确认后，新自动请求用户 `09b57786-f0af-4e71-a87c-ab39728f4bff`、助手 `82946c4b-607c-4c45-8d30-e3beea184bc1`：32.1s 终态，尝试测试前缀 ID 与 brand-guidelines 两次均失败，展开实际工具输出为 Skill operation failed，回答未假称成功。07 已核 `.enabled` 缺失及元数据/正文哈希不变，最后审计和报告待交。停用按钮点击曾工具超时，但后续页面明确显示技能已停用，未重复点击，不把交互超时当产品失败。截图仅为会话内 inline 证据，无虚构本地截图文件。

07 已被通知精确收停本轮归属 PID 21528/26288，保留停用现场及数据库；最终无监听证据待核。02 仅在候选指定测试文件补持久安全回归，尚无新测试结果。四个真实对照不证明手动独特结构、缺失/超限/不支持模型实际页面、恶意模型矩阵或主树集成通过。

## 2026-10-10 S02 只读矩阵交接与后续页面测试

03 报告 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/s02-readonly-acceptance-matrix-20261010-03/s02-agent-acceptance-matrix.md` 已由 00 完整读取并独立核 SHA-256 `DE78E9F6831CA7EB7CE424D1FA9C1D3913A7E9047D7F3CD0D0DFDE0C0FD70FFB`。00 复核候选 chat_routes.py、agent_factory.py、tool_router.py、skill_runtime.py 四份哈希与报告一致。

03 使用 D 盘现有依赖、E 盘临时/应用数据及内存 checkpointer，离线定向 Runtime 18 passed / 3 warnings；Runtime + Chat 精选组 13 passed / 24 deselected / 26 warnings；InputBar.skills 3 passed，各 exit 0，重叠不累加。临时合成包的停用/off 与六种越界路径断言通过，但未形成持久回归。00 核交接及版本，不声称自己重跑这些测试。未执行真实模型、浏览器或硬件。

真实 Chrome 自动相关正例来自此前联合报告，不是本矩阵新执行；手动、无关问题、关闭、停用后新请求已派 07 管理原隔离服务并由 00 操作页面，当前尚无新结果。快照后资源删除、异常页面、恶意模型样例及原始 reasoning 内容风险仍未验，源码展示链不能当泄密实证。不得把本矩阵记为 S02 总体验收。

## 2026-10-10 Chrome 原句安装、页面启用、下一请求加载的联合证据

07 报告保存于 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/s01b-runtime-20261009/reports/s01b-browser-retry-20261010.md`；00 完整读取，独立 Get-FileHash 得到 `790EEADA2EE4E44137FAD3DC0678EA027E0F0E03D841D54ECDD329E7EFD52259`。00 用 Get-NetTCPConnection 成功枚举 42 条监听后筛目标端口为空，用 Get-Process 全表筛目标 PID 为空，SQLite 与 `.enabled` 的 Test-Path 均为 true。首次直接查询已不存在的 PID 导致命令退出 1，后以全表筛选复核退出 0；不是产品失败。未停止其他进程或删除数据。

00 通过受支持 Chrome 扩展操作页面，07 保持 E 隔离服务并用 SQLite `mode=ro` / `PRAGMA query_only=ON` 核证，不后端注入安装或凭据。会话 `s1791608153741` 保存四条消息；安装原句用户/助手 ID 为 `4c9c4625-c835-4d28-b704-f70dc4d73634` / `9cdc6356-e76d-4ce3-8846-af3f7776a253`，下一条加载请求为 `37624d6a-1dcf-4540-b9ab-dc58d777f0d7` / `37de4957-e2f8-4f23-a334-11cc0fd3d296`。

07 实际只读命令输出：audit 13 `install_github_skill` / allow / explicit_chat_skill_install / low / 8034ms / success=1；audit 14 `load_skill` / allow / auto_allow / low / 35ms / success=1，无其他工具。安装后 SKILL.md 2,235 B、LICENSE.txt 11,345 B 及导入元数据落盘，`.enabled` 不存在；页面明确确认启用后 marker 出现。来源提交 `9d630808e4add0a7146de4af9384155d5dee350a`、路径 `skills/brand-guidelines`、import_hash `ddf22e788d6aa33e818ec59be85d4fba5aeda23075518dafc412bb90b7cc63ce` 与 SKILL.md SHA-256 `1120B3769E2985CEFB3D25BE981B1F914ABEBA57AE079B83C20C666C164FA9FE` 保持不变。解析 activity 的首次命令有引号语法错误，改为只读安全字段查询后成功；不是产品失败，也不计为测试用例。

Chrome 实际显示 load_skill 输入 brand-guidelines、输出“Skill instructions loaded”，回答按技能列出主色/强调色、Poppins→Arial、Lora→Georgia；终态 19.9s。该 UI 输出未显示版本 SHA，版本证据来自固定安装元数据与未变正文哈希，不宣称页面展示了加载版本。刷新后四条消息正文、活动及设置页启用状态保留；模式重置关闭、侧栏时间/日期变化仅为待核观察。07 正整理项目外报告；当前接受正常闭环，不接受 S01b/S02 完整矩阵或主树发布。03 仅只读梳理剩余矩阵，不重复安装回归或扩大实现。

## 2026-10-10 Chrome 真实安装阶段观察，独立取证待交

07 在 E 隔离环境启动后端 PID 27012/58080 和前端 PID 30212/55875，启动记录见运行根的 `state/logs/processes-retry-20261010.json`。07 缺浏览器接口，00 通过受支持 Chrome 扩展实际控制独立验收标签，双方没有并行操作页面。00 在设置页添加授权测试服务商、点击验证后显示 193 个模型，默认 Text；新会话原句安装的用户消息 ID 为 `4c9c4625-c835-4d28-b704-f70dc4d73634`，回答 ID 为 `9cdc6356-e76d-4ce3-8846-af3f7776a253`。

页面专用 `install_github_skill {}` 活动返回 brand-guidelines、提交 `9d630808e4add0a7146de4af9384155d5dee350a`、路径 `skills/brand-guidelines` 和 disabled，耗时 8.2s；回答 40.8s 终态，两条消息。Activity 3 项含两项 thinking，不代表调用三次工具。设置页随后显示该技能“支持”、启用开关关闭，未后端注入凭据或安装。07 正只读核对实际文件和审计，尚未交独立结论；启用及下一请求加载还未执行，不把当前页面观察记为全链路通过。

## 2026-10-10 Skills 新运行清单独立核验与执行派发

00 用 PowerShell 解析旧/新清单并逐项核对路径、长度和 SHA-256：497 项、5,406,388 字节，与候选及运行副本一致，零错误；无重复、绝对或越界路径，无新增/删除文件，仅 `backend/src/agent/tools/groups/skills/install_tool.py` 与 `backend/tests/test_chat_skills_install_tool.py` 两项 delta。禁止数据路径检查无命中；此检查不是动态安全测试。新版 `source-manifest-v2.json` SHA-256 `F841B58FBB3C8042933709E2BBA2DF181A94832EC466A9DDC7E1F4CD4CC8A1DA`；旧清单 `BFE6225C5D1DDD1798B6758961B98D47C642D4B4738049341E89EC6BDFCAD43C` 保留未改。核对时技能目录为空、58080/55875 无监听。

清单位于 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/s01b-runtime-20261009`。00 已派 `S01B-BROWSER-RETRY-EXEC-20261010`，07 仅在该隔离环境前端配置凭据、真实 GitHub 原句安装，分别记录默认停用、页面启用和下一请求实际 load_skill；要求启动时显式设置 E 盘 TEMP/TMP 与 HWRAG_APP_DATA_DIR，并复核其他运行数据路径。此次浏览器结果尚待，未重跑或宣称主树最新聊天/Explorer 已通过；不将清单一致当作功能通过。

## 2026-10-10 Skills 独立 R4 与页面准备放行

02 只读复审候选原文件（未跟踪文件不能用空 git diff 代替审查），install_tool.py SHA `1AD785460FA0D0E10A43CAA69B1579A3D9CA77F98CB4B1697FD01AE89693CD60`、测试 SHA `BD494A13216A99C5BB1BA04E8363EA513C2E4C6779AA9CE3ECE0D47076444368` 与 00 再核一致。D:/python/python.exe pytest、独立 E 临时基目录、内存库/offline：安装单文件 25 passed / 25 warnings / 20.75s；安装/聊天/ALWAYS 三文件 48 passed / 25 warnings / 21.29s；平台/review 两文件 46 passed / 2 warnings / 20.22s，各 exit 0、测试重叠不相加。进程句柄失效后的结果通过实际重启命令取得，不将失效句柄当测试通过。

无网络探针中 `../` 与 `%2e%2e` 子目录均被 SKILL_PATH_INVALID 拒绝；全角 `！/？/）/”` 会并入 URL，记录 P2 可预测目标边界，未证为越权安装，不能宣称所有标点均支持。候选无图谱，02 未更新或运行影响图，不称独立图谱验证。R4 只放行固定样例的受限真实页面，不是实网、模型工具选择或浏览器确认通过。

07 已派新版运行清单准备，暂不启动服务/模型/安装；原 497 文件清单需与当前已审候选逐项比较，仅同步受影响已审文件，新清单 00 核对后再授权真实页面执行。候选 temp/真实数据/凭据/依赖不纳入，旧失败现场保留。P03 真实聊天库未迁移。

## 2026-10-10 Explorer 独立 R2 条件放行

03 独立只读运行 `D:/python/python.exe -m pytest tests/test_explorer_routes.py -q --tb=short --disable-warnings -p no:cacheprovider --basetemp E:/DevCaches/Temp/explorer-policy-compat-r2-20261010-1b6e9c2f4d5a4af4a6e3b2c7d9e1f005/pytest`，TEMP/TMP/应用数据在 E、SQLite 内存库、离线模型配置；Python 3.13.13，32 passed / 2 warnings / 0 skipped、exit 0。另在 E 假根确认 System32-notes 搜索可见、MySystem32 沿用现有子串拒绝。未运行真实系统/用户材料、浏览器、模型、硬件、全后端或 Python 3.10/3.11。

两文件前后哈希不变，00 再核 search `10879653DAA8AAFD4B908F7F83CD0ECA76015493277B2AD88567C6056CD68854`、测试 `007AE55D6BE819A1F7E9D3536D445BD8E11F2FBEB00C56DD5754121D944A016F`。03 未重跑图谱，引用已有 stale/LOW/CRITICAL 警告并核源码，不能记作新图谱分析。00 接受静态本地目录假设下的窄修条件放行；恶意并发换 link 的 TOCTOU、504 不取消扫描线程、总扫描预算未收口，不称强隔离或总体验收。

02 的 `P03-CONDITIONAL-SAVE-DESIGN-20261010` 已交，随机版本列/原子条件保存/冲突保留/兼容旧客户端局限为提议，未改代码、schema、API 或运行新方案测试。00 未授权迁移真实聊天库；隔离开发批准待用户选择。不能把此前竞态复现 4 项或既有 CRUD 3 项记作此设计验收通过。

## 2026-10-10 Explorer 策略/兼容窄修交接

05 已冻结两文件，00 实核 search SHA-256 `10879653DAA8AAFD4B908F7F83CD0ECA76015493277B2AD88567C6056CD68854`、测试 `007AE55D6BE819A1F7E9D3536D445BD8E11F2FBEB00C56DD5754121D944A016F`。独立 R2 已改派空闲 03，仅只读这两文件及隔离复跑；02 继续保存设计→Skills R4，不重复派同一审查。

05 实际使用 `scripts/verify_backend_isolated.ps1 -Python D:/python/python.exe`：E 临时红灯 `test_search_skips_a_denied_resolved_junction_target` exit 1（read403 已成立，search 仍有假 marker）；修后该用例、近似目录允许、敏感文件组定向 10 passed / 2 warnings；完整 routes 32 passed / 2 warnings / 0 skipped，Windows junction 实际执行。隔离进程模拟删除 Path MRO 的 is_junction 后，四条 junction 用例 4 passed / 2 warnings，只证明测试不依赖该接口，不是 Python 3.10/3.11 实跑。不同范围不累加；本轮无真实系统/私人项目读写、无依赖安装或提交推送。独立 R2/浏览器/全仓尚待，旧 R1 30 项不替代此补丁审查。

## 2026-10-10 Skills 中文安装修复交接

03 回交 S01b 候选 install_tool.py 修复，00 实核 SHA-256 `1AD785460FA0D0E10A43CAA69B1579A3D9CA77F98CB4B1697FD01AE89693CD60`；测试文件现为 `BD494A13216A99C5BB1BA04E8363EA513C2E4C6779AA9CE3ECE0D47076444368`，03 称用例已存在于本轮红灯阶段，未在最后修复阶段再改。红灯 12 passed / 2 failed → 窄回归 14 passed / 25 warnings。`test_chat_skills_install_tool.py test_chat_skills_integration.py test_bypass_always_confirmation.py` 48 passed / 25 warnings；`test_skills_platform.py test_skills_platform_review.py` 46 passed / 2 warnings。不同选择器/快照不累加成全量通过；均假 GitHub、E 临时目录及内存状态，非真实模型/安装验收。02 独立后端 R4 已排，先交保存设计再复审。

03 报现有 GitNexus 索引不含新文件，影响为 UNKNOWN；用候选 parser/prepare 与 chat_routes 调用补核，不能记为低风险。为运行固定 1.6.12 CLI，pnpm dlx 在 E 缓存创建临时工具树、下载 0；这是工具缓存写入，不等于没有副作用。主控已要求不再 dlx/npx、不擅自清理缓存；未改项目依赖清单。候选 temp 等未跟踪材料保留，更新页面副本仍按逐文件清单排除临时/隐私数据，不能整树复制。

05 只读 followup 确认完整路径拒绝 helper 当前供 authorize_root/require_authorized_root/validate_path 调用，搜索仅复用单组件规则，故 E 假 System32 路径读/搜策略有缺口。GitNexus `_assert_not_system` impact CRITICAL（24 affected/16 processes），`is_denied_component` CRITICAL（13/16）；图谱仍落后一提交，search_content UNKNOWN 由实际 route 的 to_thread 调用补核。00 已明确高风险依赖警告，未授权更改这些 helper。05 本轮只读没有 pytest、新假根或源码写入；后续两文件窄修已单独批准，结果待交，不沿用 R1 30 项冒充新补丁通过。

## 2026-10-10 聊天跨页面竞态合成复现

02 在 `E:/DevCaches/Temp/p0-3-race-20261010-r1-510d55c8/crossTabRace.test.ts` 使用真实 store、全模拟 API 与 LocalStorage 运行四项，4/4 断言成立：陈旧本地覆盖服务器新内容/来源、本地确实较新可上传、同内容去重清标记、离线恢复陈旧副本仍覆盖。不是修复通过或真实双浏览器实测。00 实核隔离文件 SHA-256 `DE8306806E13ADA917FC3429E265F2F0F6C5EBA4C86C3CC2933159B70870C25E`，主树聊天三文件仍为已冻结哈希；未独立重跑此项目外复现。

02 同轮前端 `vitest.cmd run src/stores/useChatStore.test.ts --reporter dot` 40 passed；后端 `pytest tests/test_crud_message_idempotency.py -q --tb=short -p no:cacheprovider --basetemp <E唯一临时目录>` 用测试自建内存 SQLite 3 passed，确认同 ID 可更新内容与来源，不提供写入版本保护。首次前端 regex 参数经 PowerShell/.cmd 解析失败，未启动测试，后改跑完整 store 通过；不计为产品用例失败。未写仓库文件、未读真实聊天库、未浏览器/模型联调。条件保存设计已派，尚无实施。

## 2026-10-10 Explorer 独立复查 R1

02 在 E 临时目录、SQLite 内存库及 offline 环境独立运行 routes，30 passed / 2 warnings，Windows junction 无 skip，两个目标哈希检查前后一致：search `F4A3D8AA5AA81A7D41BCC3D13B1CDDCB6619044CA44460ED0EF07BE1D25BCE55`，测试 `8688654B5773E450FCE1E70908F208A59FB0A73412E45E9E47028137BD5E5A55`。首次未建 basetemp 父目录的 30 项 setup error 在修环境后重跑，不归因于产品。合成根内 `.git` junction 搜索为空，移除 link 后目标仍存在。

仅 E 假根内 System32 名称目录/junction 有搜索命中，是否违反完整路径策略尚由 05 核契约；未扫描真实系统目录。测试 helper Path.is_junction 的最低版本与 README Python 3.10+ 有兼容缺口，本次只有 3.13.13 实跑，不宣称 3.10/3.11 通过。此次独立审查不是全仓、浏览器或策略一致性全验；05 当前只读，不改源码。02 已转入隔离复现聊天陈旧 pending 风险，尚无结果。

## 2026-10-10 搜索窄修交接与调度证据更正

03 聊天三文件独立人工复核中间交接：实际复跑前端 44、内存库后端 6 通过，哈希与冻结值一致；API 投影不带本地 pending 字段、CRUD 重试不重放 SSE 的路径有覆盖。但提出陈旧标签页同 ID pending 自动 POST 覆盖另一页新服务端内容/来源的条件性风险，目前仅源码推断，未双浏览器或模拟复现。02 在 Explorer 审查后独立只读复现已派，未批准新接口/源码更改。03 尝试审查子代理遇 OS error 112 未启动，未清理磁盘数据；本轮是人工审查，不声称双代理审查。

05 已回交主树 `backend/src/explorer/search.py` 与 `backend/tests/test_explorer_routes.py`，仅搜索真实根包含性和 visited 去重/防环及四条直接回归。00 实核 SHA-256 分别为 `F4A3D8AA5AA81A7D41BCC3D13B1CDDCB6619044CA44460ED0EF07BE1D25BCE55`、`8688654B5773E450FCE1E70908F208A59FB0A73412E45E9E47028137BD5E5A55`，差异为搜索 35 行和测试/helpers 172 行。05 报 routes 全部 30 passed / 2 warnings / 0 skipped；新增用例定向 4 passed / 26 deselected / 2 warnings / 0 skipped，均 exit 0。同一 E 临时假 junction 根外 marker 修前可见，修后 `[]`；未用真实用户材料。00 当前核对文件而非独立重跑测试；02 独立只读复审已派，不能计为审查或浏览器通过。

05 本轮平台 503 为执行服务中断，恢复后交付，不计作产品回归失败。00 只读核对 02/03/05 原始 user 记录，P0-3、P0-2、Explorer 原文分别时间为 2026-09-24 05:39:01、05:38:21、05:38:36 UTC；此前称其为今天新增任务不准确。已有结果保留，当前唯一写者以 development-plan.md 页首为准：03 先聊天审查再 Skills 两文件修复，02 独立复审，05 搜索补丁冻结。下方历史写权变更不用于再次派发。

## 2026-10-10 Explorer 主树与冻结候选复核

05 在两树相同 `e61b8a65` 加各自补丁下只读复验，未改文件。主树 `tests/test_explorer_routes.py` 为 26 passed / 2 warnings；五个关联前端文件 13 passed。候选后端同组 33 passed / 2 warnings，Windows junction 实际运行未跳过；六个关联前端文件 20 passed、TypeScript 退出 0。20 项与此前 19 项检查范围不同，具体文件分母待完整交接说明，不按增长一项推断有新代码。

只含假文件的 `E:/DevCaches/Temp/explorer-junction-main-red-7c609568720a4d13a641b75fc1815cc1`：05 报告主树 `search_content()` 返回根外 `fake-explorer-outside-marker`，候选同一树返回空列表。00 实核候选 search SHA-256 `D411DAB5462CD9B7D2ACBD4EE2D97882819641B8A6B05E12A4ACAFA0D43A3CA4` 与首轮独立 review 一致，主树搜索和 routes 测试文件原无既存差异。已登记 05 仅精确接入这两文件，先 impact/保留红灯、后同假树绿灯/完整 routes 回归，未回交接入后结果；不能记为主树已修或发布通过。

所有回收/文件测试限定 E 临时假根及 mock；当前产品 `%LOCALAPPDATA%` 回收区不迁移、不操作。既有 session 根授权、首次配置、Agent 非黑名单文件访问口径不改；前端缓存警告和 i18n 候选仍保留未接入。

## 2026-10-09 S01b 后端独立复核 R3 与页面接续

当前排期更正：05 回交直接真人 Explorer P0-6/P0-5 范围，未修改 Skills；00 已撤销 02/04/05 的安装两文件写权，03 在聊天独立审查交接后唯一实施安装窄修，02 后续独立后端审查。05 当前只读核主树与已有 33/19 项 Explorer 候选差异，未获准重做方案/补丁，所有测试回收根用 `HWRAG_APP_DATA_DIR` 指向 E 临时目录，不迁移或触碰实际 `%LOCALAPPDATA%` 回收材料。

2026-10-10 最新更正：02 回交本线程直接真人 P0-3 指令原文，00 撤回此前“任务漂移”判断；该轮完成聊天补丁，不涉及 Skills 安装文件。已报前端 44 项、TypeScript、后端内存库 6 项通过，尚待 03 独立审查且没有浏览器联调。00 当前核对主树三文件 SHA-256：`useChatStore.ts` = `7A2108F3C54F969E85F01D3B1CDAAD7B49AFF4B8657FAB91C62A64F68E047A37`；`useChatStore.test.ts` = `F77CA86D6661C0D3A3536E09C36E7784A9A72E0C372C88608D6E73D4CF3E30C7`；`test_chat_routes_resume_terminal.py` = `06849F146AD66C633F88333A531CE361A7E4FC8683A2FE44F88C5EF0003A3850`。内部本地字段 `__hwrag_pending_save` 据交接不进入 API，由 03 核实。S01b 原四 store 仍为 43 项旧快照，不得混称已同步；Skills 两文件现由 05 唯一实现，02/04 写权已取消。

调度与版本边界：02 当前活跃轮次回到聊天刷新恢复测试，尚未确认承接主控的 Skills 窄修纠偏。00 实核主树 `useChatStore.test.ts` 当前 SHA-256 为 `C559ECEE412EFD895372432C04E88A0A16D6DEAD77C081F86E490C5B2F8994D4`，已不同于此前 43 项快照；同一检查点三份 store 产品源码哈希仍一致。新增测试尚无通过交接，不计作 Skills 测试或新的完整聊天门禁。保留改动、不覆盖、不并行引入另一 Skills 写者，等待线程明确承接或释放。

停止后 07 回交：独立会话 `s1791561242361` 两条消息，页面已停止；`backend/data/skills` 为空，未产生导入标记或启用文件。audit 12 项为目录列举/只读命令/读取/grep/webfetch，无 `install_github_skill`；UI 18 项另含 thinking 等，不与 audit 数量相等。实际提问第一行是“请安装这个技能，默认保持停用，不要启用。只安装这个固定提交和目录：”，skills_mode=off、每次询问、Text。07 报告正则后向边界不接受“安装这个”导致意图未识别；04 正独立复现，尚未记为修复通过。截图仅内联可见，未取得支持的持久化接口，不编造磁盘截图证据。

最终报告 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/s01b-stall-report-20261010.md`，00 实核 SHA-256 `B724601FC5EDFDACF57E6C6458775A75082114818DFEBF940C900AC6F39F1AEF`；原自有 PID 27264/28880 已退出，00 核验 58080/55875 无监听，保留数据库、origin 和缓存。00 接受内联截图为补充，不声称有 PNG 文件。04 未写候选而因任务范围待确认退出当前修复，02 接手两文件窄修，03 待修后独立审查；此人员调整不将静态归因变成已复现或已修复。

页面接续现象：07 在独立 IAB 设置页验证服务商成功（193 模型）并选中 Text；固定提交安装请求约两分钟，Activity 显示 18 项，获取说明/许可后仍显示生成中，未取得专用安装成功证据。Activity 数量不是写入或安装计数。00 已要求通过页面停止一次、保留脱敏工具轨迹，核验文件/audit/实际终态，不重发。04 接只读复现诊断；根因、是否实际写入与能否正常停止尚待证据，不能计为安装成功或安全漏洞。

02 对 `E:/Desktop/agent-worktrees/skills-chat-install-s01b-20261009` 的后端安装、授权、请求注入、路由及导入事务做只读复核，HEAD 为 `e61b8a65` 加冻结未提交补丁；02 曾写前端四 store，没有写此次被审查的后端。未发现阻止受限页面验收的 P0，结论为有条件放行，不是完整安全或实网验收。

- `test_chat_skills_install_tool.py`、`test_chat_skills_integration.py`、`test_bypass_always_confirmation.py`：34 passed / 25 warnings / 13.97 秒。
- `test_skills_platform.py`、`test_skills_platform_review.py`：46 passed / 2 warnings / 13.77 秒。
- 首次因复核者错误设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD` 而缺异步插件；清除后原测试重跑通过，没有修改测试或安装依赖。只用假 GitHub、临时目录、内存检查点，不访问私人数据库、模型、真实 GitHub 或硬件，不与既有 116 项重复累加。
- 合成授权顺序检查不等同 MCP/ALWAYS 混合 Agent/SSE 联调。聊天拒绝 partial 包的仅安装，而设置页支持停用导入，记录为兼容行为差异，未改产品规则。

00 已派 07 使用现有 E 盘 497 文件隔离副本，先重新核对清单，再经页面配置授权测试服务商、实际预览受支持包、聊天安装且默认停用、明确启用、下一请求实际加载；优先 Chrome 工具，受限则独立 IAB 并单列。此处仅记录已派任务，真实安装/加载尚未回交，不得写成通过。样例固定 `anthropics/skills` 提交 `9d630808e4add0a7146de4af9384155d5dee350a` 的 `skills/brand-guidelines`，不执行脚本或依赖，不迁移目录，不改主树和候选源码。

## 2026-10-09 当前定向证据与复验入口

本节区分实际测试、主控文件核对和未执行验收；不同快照的数量不累加成全量通过数。

- 02 最新 `useChatStore.test.ts` 与 `useSessionStore.test.ts` 共 **42 passed**，`tsc -b --pretty false` 和 `git diff --check` 通过。覆盖首发建会、401 提示、创建失败恢复、创建期间并发输入、保存重试与去重。后端 `test_crud_message_idempotency.py` 3 项、`test_chat_routes_resume_terminal.py` 2 项是此前内存 SQLite 定向结果。00 本次仅复核哈希，没有重跑测试。
- 04 按序运行 A/B/C：`test_tool_api_permission_gate.py` **36 passed / 25 warnings**；`test_git_snapshot_undo_isolation.py` **7 passed / 25 warnings**；`test_tool_router_request_spec.py` 加 `test_agent_factory_request_tool_config.py` **4 passed / 25 warnings**。通过 `scripts/verify_backend_isolated.ps1` 在 E 盘副本运行；B 的 Git 行为只在 pytest 临时仓库。04 本轮无源码修改，未运行全量、真实模型或硬件测试。
- 03 在 S01b 独立树此前的工厂目标组为 **7 passed / 25 warnings**，早于后续 Router/安装补丁，不代表当前 S01b 组合通过。
- 07 修后浏览器尝试受启动策略拒绝，未启动隔离 Chrome 或发送模型请求。报告：`E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-20261009/acceptance-report.md`。此前受控模型页面结果不替代修后真实模型验收。
- 07 接续使用独立 origin `127.0.0.1:55873` 的内置浏览器，页面新增服务商。实际端点值含中文时 fetch 未能发出 `/api/models` 请求；页面修正准确 ASCII 端点后，上游 `/models` 返回 **200**（12.7 秒），页面显示 **193 个模型**。本机凭据保存经历初次 401、登录 200、保存 200。未记录密钥值；尚不能归因端点值变化是产品还是输入残留。已选择 Text，首发/刷新结果待回交，不把模型列表验证算成聊天成功。
- 03 本轮只读比较三棵树，没有运行测试。指出主工作区及 S01b 未包含 S01a 的实际聊天 ALWAYS/bypass 闸门；04 将补齐依赖后重跑确认前不执行及 allow/deny/stop 流程。现有 A 的 36 项不能单独证明该流程。
- 05 专用 Explorer 树从 `e61b8a65` 创建，`test_explorer_routes.py` 基线 **26 passed / 2 warnings**。临时 junction 夹具观察到 `is_symlink=False`、`is_junction=True` 且解析越出项目根，修复回归尚未运行；只使用假 marker，不读取私人数据。
- 07 阶段交接：在 `E:/DevCaches/chat-first-send-20261009` 冻结副本（后端 58080、IAB 55873）使用真实 Text 首发，服务端创建会话，回答正常完成，UI 与隔离 SQLite 均保存 user/assistant 各一条；刷新前后仍两条且无重复，tool_calls 与 tool_audit 均为 0。四文件哈希仍与下表一致。Activity 徽标“1 工具”但展开只有 thinking，记为待调查的计数显示疑点，不算真实工具执行。异常分支和 Chrome 独立验收不包含在本次通过项。
- 00 已读取上述阶段报告：`E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/acceptance-report.md`，当时 SHA-256 为 `8F2A6199BF5C612F06CF4F848F33CD550A35C2D3D80D655FC2656E94C579B330`。报告记录本次回答约 44.6 秒；不是检索速度或整体性能基线。后续异常续测会追加报告，哈希须重新核对。
- 02 尊重新的真人 P0-3 授权，重跑相同前端 **42 passed**、内存 SQLite 后端 **5 passed**，未修改文件、未复现保存缺陷。补测缺口为真实 done SSE（尤其 stop 的失败终态）与部分写入失败、刷新和 CRUD 重试组合，已安排独立树测试；不重放模型请求。
- 02 新增单条 stop 终态/部分写入失败/刷新/CRUD-only 重试用例后报告 **43 passed**、TypeScript 退出码 0，后端内存库 **5 passed / 7 warnings**；无产品源码修改。03 只读比较确认仅新增 105 行测试、未弱化旧断言。02 已按批准用 apply_patch 精确纳入 E 主树并实跑相同 **43/5 项及 TypeScript**；00 核对最新测试文件 SHA-256 为 `FABD715F26404B48B45F703E4DBBE61BDF10E0211052CA07133FCC684FB46CA9`，其余三个文件仍与下表一致。下表旧测试哈希仍用于标识 07 浏览器的冻结输入，不混同最新测试文件。模拟 SSE/CRUD 不是浏览器故障验收。
- 路径偏差：02 曾误用默认托管入口在 C 盘建立聊天测试候选，已停止其写入/运行、保留不清理。代码和 Junction 元数据位于 C，依赖目标为 E 主树，tsbuildinfo/Vitest cache 与 Node compile cache 实际在 E；没有安装依赖或更改真实数据库。不以 TEMP 设置掩盖该路径偏差，后续复验已回 E 盘。
- 07 最终阶段报告追加受控注入限制后 SHA-256 为 `8BF8C5DC28D2B30BB58CCE10C0E7488DEF640C1FB06F02D915B47A90641B6F5B`；00 复核 58080/55873/55874 无监听。故障注入未落地，不算 401/503 页面通过；测试 DB、独立 origin 配置及缓存保留。
- 05 Explorer 最终候选 `E:/Desktop/agent-worktrees/explorer-p0-20261009` 的 7 文件差异：后端 `test_explorer_routes.py` **30 passed / 2 warnings**，前端四文件 **16 passed**，两个 TypeScript 项目、目标文件 ESLint、diff 检查退出码 0。后端 basetemp 为 `E:/DevCaches/Temp/explorer-p0-backend-final-20261009`，仅临时项目/外目录/回收区及假 marker；junction 修前红、修后绿。草稿写入失败、保存中输入与恢复冲突是组件/mock 测试，未做真实浏览器或全量套件，独立审查待完成。
- 04 S01b 依赖整合：E 盘隔离脚本副本运行七组后端回归 **116 passed / 25 warnings**（直接 API、实际模拟聊天 bypass 确认、工厂请求配置、Router、Skills 平台、安装工具、聊天集成）；直接 API 单组 **36 passed / 25 warnings**、平台单组 **36 passed / 2 warnings** 为同补丁窄检查，不与 116 累加。前端 E 盘临时副本 `SkillsManager.test.tsx` **9 passed**，已有依赖链接、未安装。包含通用确认与安装专用 grant、兼容诊断；不含真实模型/GitHub/浏览器/硬件或完整套件。02 后续四 store 对齐及 03 整体独立审查尚待回交。
- 03 Explorer 独立只读审查：冻结 7 文件，diff 检查退出码 0，未重跑 05 的 30/16 项。00 逐项核对其交接哈希一致，无阻断集成发现；P2 为 zh/en 缺 `draftPersistenceFailed` key、搜索缺正常成功命中及根内 junction/循环覆盖，另缺磁盘写入成功但草稿缓存失败组合。05 接续补测；旧数量不是补测后结果。图谱使用 E 盘既有 e61 scratch，未知调用结合源码核实，未重建索引。
- 02 已在 S01b 候选仅对齐四个批准 store：两文件 Vitest **43 passed**、`tsc -b --pretty false` 退出码 0、后端消息幂等与续跑终态 **5 passed**（内存 SQLite/模拟 Agent）；没有浏览器联调。00 实核四文件 SHA-256 与当前 E 主树相同，API 类型、契约和 04 原补丁未动，依赖通过候选 node_modules Junction 复用 E 主树。此为对齐后局部复验，不与 04 的 116 项拼成全量套件。
- 07 S01b 只读环境预检报告 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/s01b-preflight-20261009.md`：已有 D/E 运行时/依赖可用，58080/55875 空闲；未启动服务、模型、导入或测试。初选 `define-goal` 固定旧 SHA 的仓库弃用且资源目录导致 partial，主控拒绝将其当 supported 安装启用成功样例，继续选包。03 回报真人 P0-2 后，Skills 后端独立复核转 02，候选仍冻结。03 的主树单组隔离 1 项通过仅为顺序配置证据；并发与冲突验收未完成，空选择语义不变。
- 03 新并发配置回归仅修改主树 `test_agent_factory_request_tool_config.py`，用 barrier 核验 A/B 检索及模拟 Web 同时执行、参数/凭据/来源隔离，默认 registry 实例作错误哨兵；单组 **1 passed / 25 warnings / 16.40 秒**，Router 加工厂组 **4 passed / 25 warnings / 13.60 秒**。均由隔离脚本在 E 临时副本运行，不访问真实模型/用户 KB。00 核对当前测试 SHA-256 `53B2A9572B07024398817E85BA5DF45B81DD0C34C834685A33D9CEE7CF6D2C5F`。没有生产源码修复；S01b 工厂测试仍保留自己的旧快照及注入测试，增量待精确集成。空选择冲突验收未实施。
- 05 Explorer P2 最终定向：后端 routes **33 passed / 2 warnings**，真实 Windows 根内/越界/祖先 junction 均执行、未跳过；前端五文件 **19 passed**（原四文件加 locale），两个 TypeScript 项目、八个前端目标文件 lint、diff 检查退出码 0。locale 修前 zh/en 两项红、补字典后绿。磁盘 API 成功/缓存失败为模拟测试，旧缓存可能恢复陈旧 dirty 草稿的边界仍保留。00 核对 search/actions/persistence 生产哈希与首轮审查一致，routes 测试新哈希为 `1F33582684FD8504AE1E491232DAF92944711AA85B2512BDB1942D1226330DF8`；未重跑全量或真实浏览器。
- 07 新样例与副本报告 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/p03-browser-resume-20261009/s01b-preflight-followup-20261009.md`；00 独立核对运行目录 `s01b-runtime-20261009/source` 的 497 项文件与候选及 source-manifest.json 全部匹配、显式禁止路径 0。run-plan.json 记录未启进程/模型/导入、无 C 盘绝对环境路径字段。官方 brand-guidelines 固定 SHA、2 文件 13,580 B 的 supported 状态仅静态预期，不是实际导入证据；等待 02 审查放行。

00 于 2026-10-09 核对的四文件 SHA-256 与 02 最终交接一致：

| 文件 | SHA-256 |
| --- | --- |
| `frontend/src/stores/useChatStore.ts` | `02EF23BEE66D6703500F9E01C3B7D9A1E62416170A20BA147B98B48E58323966` |
| `frontend/src/stores/useChatStore.test.ts` | `5F5B3D12664BFF190ABC607B74D39A8FC05C328F8E020C50A7BD9EB679740929` |
| `frontend/src/stores/useSessionStore.ts` | `0C478A5EC9799B3A99E26A281E766F37F00632EA418BBE1005B12CCFE4A4904F` |
| `frontend/src/stores/useSessionStore.test.ts` | `5D1791F86FF78D156CFC1DD4CA6FA520B3794A43D16455098672EFD8664088CC` |

07 接续验收先核对上述版本，再从页面配置服务商、完成真实 Text 首发与刷新；建会/保存失败、并发输入和确认分支使用受控故障单列。数据、缓存及临时目录使用 D/E 盘。当前发布门禁未完成。

> 以下保留此前测试记录，其中 2026-10-05 发布门禁与修前 606 项快照、67 项失败轨迹分别列出；不代表页首新增改动的全量结果。浏览器、硬件和 RAG 质量分别记录。

## 2026-10-07 Skills 兼容诊断恢复后独立复核

用户恢复此前暂停的开发后，仅对隔离分支 `codex/skills-chat-import-20261006` 的资源目录兼容诊断补丁做代码复核和窄回归。主控核对候选 `format.py` 与 `test_skills_platform.py` 的 SHA-256 和交接一致；没有把补丁合并回主线。

- 实际运行：`test_skills_platform.py`、`test_skill_runtime_review.py`，**52 passed / 0 failed，2 warnings，30.14 秒，退出码 0**。Python 为 `D:/python/python.exe`；临时数据库及 `TEMP/TMP` 位于全新 E 盘目录，关闭字节码和 pytest 缓存写入，模型下载离线。
- 额外四种路径规范化拒绝断言通过，不计入上述 52 项，也不是完整 GitHub 路径遍历端到端验收。
- 未发现阻断问题；目录提示将原始大小写折叠的显示精度问题保留为非阻断体验项。读取白名单与脚本禁执行边界未扩大。
- 精确命令、源码哈希和边界见项目外报告 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/preflight/S01a-compat-independent-review-20261007.md`。本轮未运行前端、全量后端、真实 GitHub、模型或硬件测试；07 的浏览器回验另行记录，当前不能宣布 S01a 全部通过。

## 2026-10-07 Skills 页面导入验收

S01a 页面导入验收通过，范围仅为导入管理与资源清单，不包含聊天安装、真实模型使用或 Agent 读取资料。07 的普通 Chrome 报告为 `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/s01-browser/S01a-browser-resume-20261007.md`，最终 SHA-256 为 `28AD709DA2DCC7FA37772E326BACC93DA2D9090D6A39CC7FA8343D274097EDDF`。

- 固定 `anthropics/skills@683bc88e56f3e09ba94f7055977f3d3aa499f202` 的 `internal-comms`：部分兼容及目录警告、默认停用、启用刷新保持、重复导入 409 不覆盖、取消删除通过；非 GitHub 主机 400、真实不存在仓库的页面 404 通过。先前空候选提示与受控断连恢复证据保留在旧报告，未混作本轮重复测试。
- 固定 `openai/skills@49f948faa9258a0c61caceaf225e179651397431` 的 `skills/.curated/playwright`：页面导入成功，刷新仍停用；9 个文件、22,850 字节，其中两个 UTF-8 `references/*.md` 标为可读取。07 核对 9/9 Git blob 与固定提交一致，00 独立核对当前安装文件 9/9 SHA-256 与清单一致。该目录被官方标为过时，仅作固定验收样例；脚本、安装命令和 eval 示例均未执行。
- `skill-creator` 正文超出既有 32 KiB 限制而未导入，不能作为成功样例，也未放宽限制。两份数据库及已存在测试包保持报告中的哈希；00 最终检查隔离端口 5175/58082 均无监听。隔离工作树保留启用的 `internal-comms` 和停用的 `playwright`，没有写入原工作区技能目录。
- 本轮没有输入服务商密钥、调用模型或进行新自动化测试。离线缓存中重排器加载失败与 Skills 导入验收无关，也不能据此宣称 RAG 已验收。Agent 实际资料读取留待 S02，S01b 聊天安装尚未实现；版本仍未提交/合并/推送。

## 2026-10-05 最终修复后软件发布门禁

本节记录 2026-10-05 的最终隔离软件门禁。后端在全新的 `copy-full` 副本中完成；00 核对当前源码与运行副本的 247 项 manifest，均无哈希差异。前端最终测试使用隔离副本；前后 160 项文件哈希一致。提交与发布状态以 Git 记录为准。

| 范围 | 实际结果 | 输入快照与限制 |
| --- | --- | --- |
| 后端完整测试 | **608 passed / 0 failed / 0 skipped，193 warnings，191.87 秒，退出码 0**。日志：`04-backend-pytest-r1/logs/full-backend-pytest-fixed.log`。 | 全新 E 盘隔离副本 `copy-full`；当前源码和副本 247 项 SHA-256 均与 manifest 一致。数据库、向量目录、pytest 临时目录及缓存位于隔离副本；没有改 API、权限、KB 行为或依赖。 |
| 前端最终测试 | 2026-10-05 20:36:02 开始，35.64 秒；23 个测试文件、**145 项通过**。 | 独立副本 160 项源码/配置文件哈希一致，报告为 `05-frontend-release-gate/final-rerun-report.md`。不是浏览器验收。 |
| 前端静态检查与构建 | lint 0 errors/13 warnings，TypeScript 与构建退出码 0，827 模块。 | 这些结果来自同一源码的较早检查，不是 20:36 最终前端复跑的一部分；构建有大分块警告。 |

### 导入循环 P1：修复前失败、修复和复核

修复前诊断确认循环依赖：`agent_factory` 初始化经 `context_guard`、`app.api.sse`、`app.api` 回到 `chat_routes`；后者从未完成初始化的 `agent_factory` 导入 `_should_use_agent` 失败后走 fallback，未建立 `create_hardware_agent` 导出。原 03 测试曾报属性缺失；04 的首次窄复核 1 项和完整 `test_chat_source_registry_snapshot.py` 5 项通过，但同一 67 项组随后仍为 **66 passed、1 failed**。RED 证据及诊断值 `FACTORY_CREATE=True`、`ROUTE_AGENT_AVAILABLE=False`、`ROUTE_CREATE=False` 均保留。

04 在 `backend/src/agent/context_guard.py` 中将两个 SSE helper 的导入延迟到函数使用处，并新增两种 fresh-import 顺序回归；修复只涉及这一个源码文件和一条新测试文件，未调整权限、API、KB 语义或依赖。修后两种独立导入顺序 **2 passed**（38.77 秒，3 warnings）；原 67 项组 **67 passed**（61.38 秒，47 warnings）；来源快照文件 **5 passed**（11.69 秒，26 warnings）；最终完整后端套件 **608 passed**。导入顺序 P1 已由这些软件回归关闭。项目外证据：`04-backend-pytest-r1/logs/full-backend-pytest-fixed.log`、`source-copy-hashes-full.tsv` 及对应 fresh-import、67 项和来源快照测试报告。

修复前历史门禁：后端完整测试为 **606 passed / 0 failed / 0 skipped，193 warnings，244.65 秒，退出码 0**；前端 **145 项通过**（23 个测试文件）。该 606 项全量通过与当时独立 67 项组合的 **66 passed、1 failed** 均为实际记录；后者暴露导入顺序缺陷，不能被全量结果或随后单项/5 项窄复核覆盖。最初 RED（`1 failed / 1 passed`）也保留。完整轨迹见 `03-review/pytest-backend-focused.log`、`03-review/release-review.md` 和 `04-backend-pytest-r1/logs/same-67-focused-group.log`。

知识库检索范围沿用当前契约：省略或传空数组的 `kb_ids` 表示检索全部启用知识库，不能用空数组关闭 RAG（[API 契约 §5.1](api-contract.md)）。当前实现与此一致，本轮没有把“空知识库选择”登记为新缺陷，也没有更改其语义。

这些软件回归不代表真实浏览器、模型服务、硬件、全量 RAG 质量、其他新电脑安装或发布流程已完成验收。未在本轮重跑的 `lint`、TypeScript 和 Vite 构建仍只引用同源码的先前记录。

## 2026-10-04 F1 集成软件回归

F1 候选 `RAG-P1-20261004-A-F1` 是 2026-10-04 测试时的未提交工作区快照，HEAD 为 `32e82560d8c7723005b7247482910f1b5f01d579`；此处描述的是当时状态，后续发布状态以 Git 记录为准。运行前后核对冻结清单 422 个文件，缺失 0、哈希差异 0；后端隔离副本的 243 个源码/测试/输入文件及 `backend/pytest.ini` 也逐项匹配清单。副本在独立目录，未复制 `.env`、认证文件、数据库、索引或用户知识库；SQLite、审计、Chroma、上传、会话历史、检查点、临时目录及模型缓存均位于该运行目录，检查点使用内存模式，Hugging Face/Transformers 离线。

全套后端命令由项目外验证脚本执行：

```powershell
& '<external-acceptance-root>/20261004-rag-improvement-control/verify_backend_f1.ps1' `
  -IsolatedRoot '<external-acceptance-root>/F1-6dbff660011f49d186083bedb51d8f03/copy'
```

结果为 **606 passed / 0 failed / 0 errors / 0 skipped，193 warnings，152.06 秒，退出码 0**。日志与运行路径核验 JSON 保存在 `<external-acceptance-root>/F1-6dbff660011f49d186083bedb51d8f03/`。警告主要包括 FastAPI `on_event`、`datetime.utcnow()` 弃用、两项旧测试返回布尔值，以及 Transformers 缓存变量弃用；不影响本次退出码。

首次深层隔离路径运行得到 599 passed、7 个 Git 测试夹具初始化错误：Windows Git 无法创建 260 字符的模板 hook 文件路径（`Filename too long`）。保留原始日志；没有改全局 Git 配置或跳过测试。换用较短的唯一 E 盘根目录后完整重跑，606 项全部通过。前端独立快照另有 16 个文件的 99 项测试通过、lint 0 错误/13 警告、TypeScript 与 Vite 构建通过；源码清单 152 项哈希与聚合哈希均已核对。

以上是自动化软件门禁，不代表浏览器、真实本地 API/RAG 全链路、35 道质量评测、标准评分或真实硬件验收已完成。导入预检提示缺少 FFmpeg shared libraries，因此音视频解码仍未验收；没有下载权重或启动用户服务。

## 怎么运行

2026-10-04 发布检查点：`codex/day1-3-baseline` 的 `32e82560d8c7723005b7247482910f1b5f01d579` 当时已推送并核对远端 SHA，`master` 保持原提交。105 个文件包括源码、回归、公开样例与正式文档；凭证、数据库、索引、缓存、日志及评测产物未提交。提交前清除一个测试文件末尾的多余空行，没有改测试行为；其余后端源码/样例此前与 549 项全绿隔离副本逐文件一致。这是历史发布记录，不预判当前工作区后续状态。

GitNexus 缓存版 1.6.12 的全范围检查识别 105 文件、1,554 符号和 223 个受影响流程，风险 `critical`。全范围的符号列表触及 1,000 条硬上限；随后以两个项目外临时 Git index 分组重跑：已修改 48 文件/561 符号/213 流程，新增 57 文件/993 符号/10 流程，分别为 `critical`、`high`，均退出码 0且无 partial/truncated 提示。没有改工作树或原暂存范围来绕过检查。图谱陈旧和动态调用覆盖限制仍在，不将这些结果称为全量无风险认证。

可选 `tests/mcp_live_acceptance.py` 为本机原生导入顺序使用 PyArrow，项目未单独锁定这个直接依赖；已有主机可运行不等于该脚本在空白环境已验证。DeepEval 的独立判分环境不用于验证此硬件/Agent 验收脚本。

### 后端

Windows PowerShell：

    cd backend
    .\.venv\Scripts\python.exe -m pytest tests

macOS / Linux：

    cd backend
    python -m pytest tests

### 前端

    cd frontend
    npm ci
    npm run test -- --run
    npm run lint
    npm run build

### RAG 黄金集评测

先用 `cd backend`，再运行 `python -m tests.rag_eval.run_golden_eval --validate-only`，只检查黄金集格式。

2026-10-04 推送后重新运行上述命令：30 道通用题 schema 校验通过，version 1.0、退出码 0、无模型调用；另有 5 道 PDF 专项题，因此历史合并 35 题不是该通用 YAML 的独立题数。

需要完整评测的参数和流程见 [黄金集评测说明](../backend/tests/rag_eval/GOLDEN_EVAL_README.md)。完整评测会访问运行中的本地服务并生成结果，不属于普通单元测试。

修复前的 2026-10-03～04 评测见 [RAG 质量基线第 1–7 节](rag-quality-baseline.md)：35 道正式题已采集，30 道完成来源引用链路；这不是答案正确率。30 个合格候选均尝试 v3 自评诊断，8 个有效、21 个评分超时、1 个 API 错误，8 个子集加权平均 85.31，不能代表全量质量分。题库/资料错误已在后续 P0 中校准，但没有据此重写旧答案或旧分数；修复后证据另见该报告第 8 节。当时 DeepEval 标准四指标完整样本为 0；后续部分完整分见第 10 节，仍无全量标准总分。修复前实际重排器未加载，结果属于降级路径。

### 2026-10-04 独立 DeepEval 环境

软件快照推送后，在项目外隔离目录建立独立评测环境。解释器为应用自带的 Python 3.12.14，不下载或替换全局 Python；DeepEval 固定 1.5.5。判分环境使用 LangChain 0.2.17 / core 0.2.43 / openai 0.1.25 和 Ragas 0.1.21 的旧依赖 API；不装后端的 Torch/Docling 等完整运行栈。安装退出码 0，`pip check` 返回 `No broken requirements found`，104 行依赖锁保存在项目外目录，SHA-256 为 `3141cc3cfd3dafbfe36272b3be54736118e648e9c593044b6697068275d125ec`。缓存、安装日志与临时文件均在该隔离目录；当时的系统 Python 3.13.13 / DeepEval 4.0.7 未改动。

`backend/requirements-eval.txt` 已改为仅安装判分依赖，不再包含 `-r requirements.txt`。安装成功不代表评分成功；Text 自定义适配和原生四指标的实际验证结果另记。此配置与本节结果文档是推送之后的本地改动，不冒充已包含在 `32e82560` 的远端文件。

在上述环境中，项目现有 `preflight_deepeval_runtime()` 实际返回 `python=3.12, deepeval=1.5.5`，四个原生指标均可导入；提前设置 `DEEPEVAL_TELEMETRY_OPT_OUT=YES`，仅在这次离线导入检查的上下文中阻断 DeepEval 的 PyPI 更新查询，退出后还原，没有改第三方包代码。原有 `test_metric_failures.py`、`test_report_denominators.py`、`test_eval_deadlines.py` 三文件另跑 **33 项通过、1 条警告、0.98 秒、退出码 0**，无模型请求。为避免导入后端认证 fixture 或插件副作用，使用 `--confcutdir=.../backend/tests/rag_eval` 和 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；警告是关闭插件后已有 `asyncio_mode` 配置不识别，不是跳过测试。结果保存为项目外 `protocol-tests.log`。这组纯评分协议测试不代替完整后端 549 项或真实原生判分。

Luna/max 实现项目外 `saved_deepeval.py` 与配套测试，采用实际 `Text` 身份的 `DeepEvalBaseLLM`，不伪装为 OpenAI 模型名；schema 返回实例、请求不重试、共享 40 次 HTTP 上限、单指标 90 秒、单响应 4,096 token。主控另跑 unittest：**7 项通过，0.354 秒、退出码 0**，四个原生 `a_measure` 都用假响应实际计算完成，另验非法格式不重试、请求封顶与取消。执行线程的同组 pytest 也为 7 项通过、5 条依赖弃用警告，不能合计成 14 个不同测试；首次自动加载插件曾触发非收费 PyPI 版本查询，随后禁用插件复测阻断查询，全程零 judge 请求。主控的评分 driver 另核对提交、题库/回答 hash 和成功检索的完整父上下文，dry preflight 有 3 个合格样本、0 模型调用。

首次真实原生判分运行 `grade-run-20261004T034214Z-353ef5ed`：G001/G023/G026 共 **3 planned / 3 eligible / 3 attempted / 0 scored / 3 failed**，四指标各 0/3 有效，全部为 `format_failure`，标准总分为 `null`，不得解释为 RAG 质量 0 分。实际 judge HTTP 尝试 **15 次**，没有重试或重新生成回答，脚本退出码 1；失败原文输入、结果分母与日志保留在项目外目录。评分器格式适配需继续核对，后续修复验证不覆盖这次失败成绩。

后续明确非流式请求与完整 JSON 代码块解码，适配器测试 9 项通过；两次格式诊断共 2 次 HTTP，不生成质量分。G001/G026 两题的第二轮实际 16 次 HTTP：召回及精确度均各 2/2 有效、分数 1.0，忠实性及回答相关性各 0/2 有效，两个完整总分均 `null`，退出码 1。再增加 SSE 与长度截断回归后外部适配器 **11 项通过、0.852 秒**；只针对 G001 的两个失败维度做流式探针，单指标 180 秒、单 HTTP 读取 90 秒、额度最多再 7 次。实际 4 次 HTTP，两个指标均失败、退出码 1：回包均 HTTP 200/SSE，各指标出现 `finish_reason=length`，在 4,096 token 预算下被判为不完整而拒绝计分。合计 **37 次 judge/诊断 HTTP 尝试**，不混同旧聊天或 Embedding 调用数。原生评分完整样本仍为 0；没有用跨版本好分数拼总分。用户中止时该探针已自然结束，确认无该评分进程继续运行，未再发请求。具体报告和未验证边界见 [质量报告第 9 节](rag-quality-baseline.md#9-2026-10-04-发布与独立-deepeval-验收)。

### 2026-10-04 四指标重评和提示词优化

产品只增加完整证据阅读、关键限制、逐句引用及检索内容不作为指令的规则，新增 `test_rag_answer_prompt.py`，没有改题库、指标公式、权重或工具权限。使用已有系统 Python 建立新的 E 盘源码/SQLite/索引隔离副本，实际命令：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-score-optimization/backend-regression `
  -TestPaths @('tests', '-vv', '-o', 'faulthandler_timeout=120')
```

结果 **550 collected / 550 passed / 0 failed / 0 skipped，183 warnings，227.86 秒，退出码 0**。副本为该目录下 `hardware-rag-verify-434078a12a4744c88d777f3fc3b13855`；新提示词和测试文件与当前工作区 SHA-256 一致。本轮没有前端、浏览器、新电脑或硬件重验。

项目外 `collect_optimized.py` 复用只读真实问答流程；准备版本 `071038Z-72834b67` 在计费前累计台账核验失败，零模型请求，保留。正式新 `live-smoke/runs/20261004T071141Z-03fb7ee6`：三份 MD 共 457 片段入库；4 planned / 4 attempted / 4 completed，三题来源链路 3/3、严格内容检查 2/3、空库 1/1；退出码 1 来自内容检查而非传输错误。完整父上下文分别 5/5/10 段；自有服务 PID 2896 已退出，58087 空闲，cleanup verified。累计应用问题尝试为 13，内部模型与 Embedding 次数未计量。

Text 外部适配器和原生公式对照最终 **16 项离线 unittest 通过、1.223 秒、退出码 0**，包括输出截断拒绝、结束标记后连接不结束、取消和请求上限；无真实 judge 调用。项目外 driver 的默认行为只预检，显式 `--run-live` 才读取本机未跟踪凭证和调用模型。最终使用命令如下；这是付费实网操作，重跑会新建记录且不保证相同分数，不属于普通自动测试：

```powershell
$evalPython = '<external-acceptance-root>/20261004-release-deepeval/.venv-eval/Scripts/python.exe'
& $evalPython '<external-acceptance-root>/20261004-release-deepeval/grade_rag_run.py' `
  --answer-run '<external-acceptance-root>/20261004-rag-p0/live-smoke/runs/20261004T071141Z-03fb7ee6' `
  --max-output-tokens 32768 --judge-effort low --run-live
```

四项仍用 DeepEval 1.5.5 原生类，30/25/25/20 是项目已有加权方案，不是 DeepEval 统一规定的默认权重。`include_reason=False` 仅省去额外总结请求，保留原生事实抽取、逐条 verdict 和公式；每指标 600 秒、单 HTTP 读取 90 秒、每轮最多 40 次尝试、不重试、无输入截断。客户端请求 low 不证明上游实际配置。

| 保存运行 | 答案版本 | planned / eligible / attempted / scored / failed | judge HTTP | 有效完整子集均值 |
| --- | --- | --- | --- | --- |
| `grade-run-20261004T064036Z-0f04ca97` | 旧 | 3 / 3 / 3 / 0 / 3 | 26 | 无 |
| `rag-grade-20261004T070429Z-b4c65c22` | 旧 | 3 / 3 / 3 / 2 / 1 | 20 | 100，仅 2/3 |
| `rag-grade-20261004T072004Z-7ce19b16` | 新 | 3 / 3 / 3 / 2 / 1 | 21 | 93.47，仅 2/3 |
| `rag-grade-20261004T074755Z-5a13c032` | 同一批新答案、请求 low | 3 / 3 / 3 / 1 / 2 | 19 | 100，仅 1/3 |
| `rag-grade-20261004T081323Z-5db5cac6` | 用户确认网络恢复后的同配置单轮重测 | 3 / 3 / 3 / 3 / 0 | 21 | 95.51，三题全部完整 |

恢复前的四轮均退出码 1，均不是三题完整或全量质量验收。另一次旧 G023 单项低推理开销探针用 3 次 HTTP、200.969 秒完成，忠实性 1.0，不参与均值。恢复前检查点合计 **89 次 judge/诊断尝试**，上一小节 37 次另计，该检查点独立环境累计 126；不等于实际计费请求数，也不包含应用/Embedding 次数。

恢复前统一配置轮明确出现三次 HTTP 503 与一次传输错误；失败保留为空值，不等于质量零分，不跨运行补分。用户确认短暂网络问题后，授权再用相同配置重测一次：最后一行实际退出码 **0**，四指标各 3/3 有效，21 次 HTTP 均为 200/stop。指标均值按百分制为召回 100、忠实性 97.62、回答相关性 100、检索精确度 80.56；每题先保留两位再平均，项目加权均分 **95.51**。这是三题开发子集数值达标，不是全量验收，UART 解释错误和 ESP32-S3 精确度 41.67 仍在。

新增 21 次后本节继续评分合计 **110 次 judge/诊断尝试**，与前一检查点的 37 次累计 **147 次**；应用提问没有重发，仍为 13 个历史累计问题尝试。适配器与公式对照重复复测仍为 16 项通过、1.538 秒，不将两轮同名测试相加。所有判分进程已自然结束，未继续循环刷分。完整报告和逐条审查在 `20261004-release-deepeval/` 各对应目录内 `report.json`。新问题、判分波动与总体置信度边界见[质量报告第 10 节](rag-quality-baseline.md#10-2026-10-04-四指标重评与证据边界优化)。

### 多模态模型接口测试

- 测试接口地址保存在本机未纳入版本控制的设置中；本文件不记录服务地址或密钥。
- 模型名称：`Text`；支持多模态，可用于后续聊天及多模态接口联调。
- API key 属于凭证，不保存在本文件或 Git 中；请只在本机应用设置或其他未纳入版本控制的安全配置中填写。
- 记录接口和模型信息不代表模型连通性或具体功能已经通过测试；运行后需单独记录实际结果。

## 哪些测试数据必须保留

- `backend/tests/rag_eval/golden_dataset.yaml`：人工维护的通用黄金问答集。
- `backend/tests/rag_eval/golden_dataset_pdf.yaml`：PDF 专项黄金问答集。
- `backend/tests/rag_eval/golden_dataset_schema.json`：黄金集格式规则。
- `data/test_docs/*.md`：RAG 评测使用的输入文档；这些 Markdown 文件已纳入 Git，应继续保留。

不要把上述黄金集或 Markdown 输入当成运行结果删除。新增可复用样例时，放在测试目录并一并更新说明。

## 哪些是运行后生成的文件

- `backend/tests/rag_eval/run_golden_eval.py` 在 `data/test_results/` 生成带日期的 JSON 和 Markdown 结果，以及调试日志。
- `backend/tests/rag_eval/run_eval.py` 在 `data/test_results/` 生成规则评测结果。
- `scripts/run_baseline_deepeval.py` 和 `scripts/run_baseline_deepeval_v2.py` 在 `data/benchmark/` 生成评测报告、日志和续跑检查点。
- 页面预览、PDF 图像抽取、分块快照等是脚本产物，不是默认自动化测试的输入。

重新运行会产生一份新的结果，不保证还原旧报告里的分数。旧日期的单次评测报告和日志已清理。`.gitignore` 只忽略这些具体结果名称或输出目录，不会忽略整个 `data/` 或 `backend/tests/`。

## 本机保留但不提交的材料

- `data/test_docs/*.docx` 是本机的文档解析样例，保留在电脑上并按现有规则忽略。
- `data/test_results/pdf_golden_dataset.json`、分析快照、页面预览和旧 embedding 备份用途不完全明确，暂时保留在本机并精确忽略；当前自动评测使用上方的 YAML 黄金集。
- `backend/tests/test_html_ingest.py`、`backend/tests/test_ocr_parser.py` 和 `backend/tests/rag_eval/_sse_probe.py` 是被精确规则忽略的本地测试文件。本次未改动或删除它们，也不把它们算作 Git 中的正式测试套件。
- 本地知识库、数据库和用户导入手册属于运行数据，不是源码或测试夹具，不应提交。

## 最近一次有记录的测试结果

| 运行日期 | 命令 | 结果 |
| --- | --- | --- |
| 2026-10-03～04 | 本机项目外 `20261003-rag-quality/live_quality.py`：独立源码/数据库服务、真实上传索引、`collect`、`rubric_full`、`cache_probe`；参数和材料见 [质量基线](rag-quality-baseline.md) | 7 份固定输入均索引，共 1,228 片段；普通题已保存 30/30、引用链路 25/30，PDF 已保存 5/5、链路 5/5；另 2 个缺资料探针。2 次空输出错误、2 次本轮 300 秒截止、1 次无有效 KB 引用未算成功。v3 诊断 8/30 有效、21 次超时和 1 次 API 错误，标准总分不可用。删除合成 KB 后仍返回缓存内容已复现；没有修改产品代码、原知识库或硬件，非浏览器/启用重排器后的质量验收 |
| 2026-10-03 | `./scripts/verify_backend_isolated.ps1 -Python <python-executable> -TempRoot <external-acceptance-root>/20261003-rag-quality -TestPaths tests/rag_eval` | 18 项通过、30 条警告，34.32 秒，退出码 0；证据/评测报告逻辑回归，不是 18 个真实模型问答的质量通过 |
| 2026-10-03 | `./scripts/verify_backend_isolated.ps1 -Python <python-executable> -TempRoot <external-acceptance-root>/20261003`（本轮最终源码，包括 MCP 停止后的排队请求保护） | Git 范围正式后端测试：457 项通过、183 条警告，退出码 0，98.87 秒；隔离副本不含被忽略的本地探索测试，不是新电脑安装验收 |
| 2026-10-03 | `cd frontend; npm test`（本轮最终前端） | 16 个文件、79 项通过，退出码 0，24.95 秒；组件及状态/API 模拟测试，不是浏览器验收 |
| 2026-10-03 | `cd frontend; npm run lint`（本轮最终前端） | 0 错误、13 条已有 React Hooks 警告，退出码 0 |
| 2026-10-03 | `cd frontend; npm run build`（本轮最终前端） | 824 个模块构建成功，退出码 0，11.77 秒；仍有超过 500 kB 的分块提示 |
| 2026-10-03 | `cd frontend; npm run test -- --run`（Skills 删除确认和请求类型补齐后） | 14 个文件、62 项通过，退出码 0，8.72 秒；组件/API 模拟测试，不是浏览器验收 |
| 2026-10-03 | E 盘隔离副本：`tests/test_skill_runtime_review.py`、`tests/test_chat_skills_integration.py` | 26 项通过、25 条警告，15.25 秒；包括真实 SkillsRuntime 下伪造工具派发、MCP 来源标记、KB 范围、缺少运行时及模式不匹配均未执行 spy 工具；之后还有工具停用交集复核 |
| 2026-10-03 | E 盘隔离副本：记忆数据库集成、SkillsRuntime、技能平台及独立 HTTP/格式回归 | 64 项通过、28 条警告，17.59 秒；不是本轮最终全量 |
| 2026-10-03 | `./scripts/verify_backend_isolated.ps1 -Python D:\python\python.exe`（Skills/聊天记忆仍在实施时的源码快照） | Git 范围正式后端测试：317 项通过、116 条警告、退出码 0，104.87 秒；包含新增的 10 项记忆保存测试，不含被忽略的本地探索测试，不代表本轮新功能已验收 |
| 2026-10-03 | `cd frontend; npm run test -- --run`（记忆界面完成、Skills 界面仍在实施时） | 10 个文件、42 项通过、退出码 0；静态检查 0 错误、13 条已有警告。此检查点不作为最终构建/界面验收 |
| 2026-10-03 | 隔离源码副本中，按下方受控包装方式执行 `pytest tests -q --tb=short --maxfail=1 --disable-warnings` | 日期 1–3 历史工作区全量：331 项通过、104 条警告、退出码 0，98.84 秒；包含当时复制的 24 项未纳入 Git 的 HTML/OCR 本地测试，不能与现在的 Git 范围套件直接比较项数 |
| 2026-10-02 | 隔离源码副本中，以预先导入 PyArrow、禁用自动插件并显式加载 `pytest_asyncio.plugin` 的方式执行 `pytest tests -q --tb=short --maxfail=1 --disable-warnings` | 后端改动后全量：327 项通过、104 条警告、退出码 0；本次未再输出原生崩溃栈。具体包装命令与环境边界见下方 |
| 2026-10-02 | `cd frontend; npm run test -- --run` | 改动后：8 个测试文件、34 项通过；包含失败状态与停止轮询的新回归 |
| 2026-10-02 | `cd frontend; npm run lint` / `npm run build` | 改动后：0 错误、13 条已有 Hooks 警告；构建成功，仍有超过 500 kB 的分块提示 |
| 2026-10-02 | `python -m tests.rag_eval.run_golden_eval --validate-only` | 30 个样本格式校验通过；没有调用模型或产生质量分数 |
| 2026-10-02 | `tests/test_routes_tool.py` 与 `tests/test_tool_api_permission_gate.py` 定向回归，使用下方隔离包装方式 | 20 项通过、33 条警告；隔离测试间工具注册表状态，并验证高风险直接调用仍被确认门禁拦截 |
| 2026-10-02 | `python -m pytest tests/test_hitl_resume_fail_closed.py tests/test_local_binding.py tests/test_chat_routes_resume_terminal.py tests/test_tool_api_permission_gate.py -q --tb=short` | 04 线改动后回归：56 项通过、28 条警告；进程启动前设置临时 `SQLITE_DB_PATH` |
| 2026-10-02 | `python -m pytest tests/test_hitl_resume_fail_closed.py tests/test_local_binding.py tests/test_chat_routes_resume_terminal.py tests/test_tool_api_permission_gate.py tests/test_git_snapshot_undo_isolation.py tests/test_tool_router_request_spec.py tests/test_agent_factory_request_tool_config.py tests/test_kb_index_status_isolated.py -q --tb=short --maxfail=1 --disable-warnings` | 00 改动后复核：72 项通过、27 条警告；临时源码副本与临时 SQLite。系统 Python 另输出 PyArrow 原生 access violation 信息；虽退出码为 0，不能据此认定运行环境完全可靠 |
| 2026-10-02 | `cd backend; python -m pytest tests/test_tool_api_permission_gate.py -q --tb=short --maxfail=1` | 实施前基线：15 项通过，26 条警告；使用系统 Python 3.13.13，未运行新增续跑/监听地址测试 |
| 2026-10-02 | `cd frontend; npm run test -- --run` | 实施前基线：7 个测试文件、33 项通过 |
| 2026-10-02 | `cd frontend; npm run lint` | 实施前基线：0 错误、13 条已有 React Hooks 警告 |
| 2026-10-02 | `cd frontend; npm run build` | 实施前基线：构建成功；仍有 JS 分块超过 500 kB 的提示 |
| 2026-09-26 | `cd backend; python -m pytest tests -q --tb=short --maxfail=1 --disable-warnings` | 268 项通过，103 条警告；增加 Agent 心跳测试 |
| 2026-09-26 | `cd frontend; npm run test -- --run` | 7 个测试文件、33 项通过；新增聊天超时后结束生成并保留部分回答的测试 |
| 2026-09-26 | `cd frontend; npm run lint` | 0 错误，13 条 React Hooks 警告 |
| 2026-09-26 | `cd frontend; npm run build` | 构建成功；有 JS 分块超过 500 kB 的提示 |
| 2026-09-24 | `cd frontend; npm run test -- --run` | 6 个测试文件、28 项通过 |
| 2026-09-24 | `cd frontend; npm run build` | 构建成功；有单个 JS 分块超过 500 kB 的提示 |
| 2026-09-24 | `cd frontend; npx eslint src/components/layout/AppRoot.tsx src/components/explorer/ExplorerPanel.layout.test.tsx` | 通过，无 lint 错误或警告 |
| 2026-09-16 | `cd backend; python -m pytest tests -q --disable-warnings --maxfail=1` | 后端 197 项通过；报告注明使用 Python 3.13，非空白数据库环境 |
| 2026-09-16 | `cd frontend; npm run test -- --run` | 前端 7 项通过 |
| 2026-09-16 | `cd frontend; npm run build` | 构建成功；报告记录有大包提示 |
| 2026-09-16 | `cd frontend; npm run lint` | 0 个错误、13 条 Hook 警告 |
| 2026-09-23 | `npm ci` | 隔离环境安装 428 个包成功 |

2026-09-24 的内置浏览器检查：在 1280×720 视口把资源管理器拖至下限后，实际面板宽度保持 220px，四个当前显示的头部按钮各为 32px，按钮行没有溢出；折叠条为 24px，重新展开后仍为 220px。该浏览器检查时没有打开项目根目录，因此项目打开后额外出现的“已删除”按钮由前端组件测试确认存在，并按 5 个图标按钮计算所需宽度；未在浏览器里单独点击该按钮。

2026-09-23 的收口记录另外摘要称后端 206 项、前端 10 项通过，构建和静态检查通过；但没有记下这些检查各自的实际命令，因此这里只保留为历史报告摘要，不冒充可复现记录。本次整理没有重跑这些测试。

2026-10-02 本机环境检查发现根目录旧 `.venv` 指向另一 Windows 用户目录，启动报 `uv trampoline failed to spawn Python child process`；`backend/.venv` 当时不存在。本轮改动前检查使用可运行且已安装依赖的系统 Python，未覆盖或删除旧环境，也不将这一结果称为新电脑全新安装验收。上方安装步骤仍应在正确创建的项目隔离环境中执行。

本轮早期测试有未设置临时库的导入：`app.main` 模块级创建应用会初始化数据库并清理旧审计，HITL 状态初始化会按测试会话 ID 查询数据库。已停止这种测试方式，后续通过的回归采用临时库或完整临时源码副本。只读检查未发现原数据库文件大小或修改时间变化，但这不能证明没有行级影响；不将早期运行写成“完全未接触本机数据”。

### 2026-10-02～03 隔离回归的运行方式与限制

可用 `./scripts/verify_backend_isolated.ps1 -Python <可运行的 Python 路径>` 重现受控源码副本回归。脚本只复制 Git 已跟踪和未被忽略的后端源码、正式测试及 Markdown 夹具，不复制 `.env`、凭据、原数据库或本地探索测试；使用新建的临时数据目录。用 `-TestPaths tests/test_manual_memory_settings.py` 可执行定向测试。保留副本用于诊断，不覆盖旧虚拟环境；它不是新电脑安装验证，也不是网络/浏览器/硬件验收。

本轮后期 C 盘空间耗尽，测试改为 `-TempRoot <external-acceptance-root>/20261003`。每次创建独立源码副本、SQLite、`TEMP/TMP` 和 Pytest base；只搬移本轮自己创建的已完成临时副本到该目录保留诊断，未清理用户缓存或私有材料。不要并行启动会扩大系统缓存的 GitNexus `pnpm dlx` 调用。

2026-10-03 本轮记忆保存补丁及消息持久化定向回归：上述脚本运行 `tests/test_manual_memory_settings.py`、`tests/test_crud_message_idempotency.py`，13 项通过、42 条警告、退出码 0。包括严格类型/4,000 字符边界、替换/清空/读取、非法值整批不更新，以及原消息幂等行为；此时 Skills、Agent 记忆注入和界面仍在实施，不据此宣称整项验收。

测试目录是系统临时目录中的源码、正式测试和固定 Markdown 样例副本；没有复制项目 `.env`、真实数据库或用户手册。测试使用临时 `SQLITE_DB_PATH`，`AGENT_CHECKPOINTER_TYPE=memory`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，不下载模型或使用原知识库。

系统 Python 的默认插件/导入顺序曾触发 PyArrow 原生 access violation。最终全量回归在同一 Python 3.13.13 中禁用无关的全局自动插件，显式加载异步测试插件，并在 Pytest 前导入 PyArrow：

```powershell
# 在准备好的隔离源码副本 backend 目录内运行。
# SQLITE_DB_PATH 必须在 Python 启动前指向该副本的新临时数据库。
$env:SQLITE_DB_PATH = Join-Path (Get-Location).Path ('data/test-' + [guid]::NewGuid().ToString('N') + '.db')
$env:AGENT_CHECKPOINTER_TYPE = 'memory'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:ANONYMIZED_TELEMETRY = 'False'
python -c "import pyarrow,pytest; raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','-q','--tb=short','--maxfail=1','--disable-warnings']))"
```

这次进程通过且未再输出原生崩溃栈，但只是可靠完成了上述受控回归，不代表旧虚拟环境已修复、所有导入顺序都正常，或 README 在新电脑上的安装已重新验收。中途全量运行曾在 236 项通过后因共享工具注册表污染失败；修正测试夹具后重跑全量通过，没有跳过失败测试或放宽产品权限。

### 2026-10-02 浏览器与真实模型检查

- 隔离 API 绑定 `127.0.0.1:58080`，健康检查成功；前端绑定 `127.0.0.1:5173`。这些服务使用临时数据，不是原知识库。00 的内置浏览器初始化失败后，由 02 线使用可工作的浏览器完成下列检查。
- 索引成功的固定文档：实际打开片段预览，看到第 0 个片段、第 1 页及测试正文。
- 未配置 Embedding 的临时库：实际看到“索引失败”和“文档已分块但不可检索。配置后请重新上传”；3 秒后仍是稳定失败终态，没有持续索引加载。是否停止后台轮询另由前端自动化测试确认，不能只凭这 3 秒观察推断。
- 03 线用 `Text` 聊天模型和独立的 `text-embedding-v4` 向量模型，对临时合成文档运行 3 个固定问题。三问均记录到 `search_docs`、匹配该临时知识库/文档的 `src1` 来源和成功结束事件；两问完整回答分别确认 SDA=GPIO8 / SCL=GPIO9，以及第 9 个时钟 ACK 拉低 SDA / NACK 保持高电平。另一问的回答记录截断，只能确认部分内容，不能计为完整答案验收。
- 这不是完整黄金集或 DeepEval 质量评分，也没有验证引用的逐句语义忠实性。真实请求的凭证仅在本机临时服务配置中使用，不写入正式文档或 Git。
- 聊天界面的来源引用展示仍未验收：浏览器设置接口返回 401，本轮没有强制填写凭证、清空用户设置或扩大任务修复认证问题。

2026-10-03 补齐策略入库失败、聊天 HTTP 超时和连续失败的报告统计。新回归测试在旧版隔离副本上确实复现“两次失败只统计一次”，随后修正计数累加；不把已尝试和未尝试的题目混在一起。01 线完成补丁后因任务额度限制中断，00 接手复核和最终套件验证。

浏览器验收结束后，00 已关闭自己启动的两个临时进程，并确认 58080、5173 不再监听；未停止其他用户服务。测试资料保留在项目外临时目录，未删除真实知识库、旧环境或用户材料。

## 2026-10-04 RAG P0 修复检查

这些结果来自 `9bd7e292` 之后、版本提交前的隔离工作区测试；后续发布提交和远端分支以 Git 记录为准。执行任务显式使用 `gpt-6-luna` / `max`，主控按事先列出的验收条件复核；验收计划与产物保存在项目外目录。定向结果不能相加当作全量数量。

| 检查点 | 实际结果 | 边界 |
| --- | --- | --- |
| `test_webfetch_failure_envelope.py`，隔离源码与受控 HTTP 响应 | 8 项通过、25 条警告，12.46 秒，退出码 0 | 真实 ToolRouter 包装失败/超时，保留成功和普通字典语义；不是外网网页兼容性验收 |
| 四组事实校准后的 `tests/rag_eval` 检查点 | 22 项通过、30 条警告，退出码 0 | 包括 MODER/NVIC/UART/ESP32-S3 确定性检查，独立重算 65 行 UART 表；不是整个知识语料的认证 |
| 缓存新鲜度、索引响应、原索引状态和原缓存兼容联合 | 29 项通过、29 条警告，26.49 秒，退出码 0 | 单进程范围；受控阻塞期间实际 ASGI 健康/列表可响应，非真实大文档性能测试 |
| 评测协议最终 `tests/rag_eval` | 49 项通过、30 条警告，24.79 秒，退出码 0 | 受控评分超时、非法值、上下文、客户端期限与分母回归，没有真实模型评分调用 |
| 首轮/续跑终态、Agent 空流/无进展和 Skills/MCP/HITL 八文件联合 | 77 项通过、25 条警告，14.28 秒，退出码 0 | 此后全量揭示旧公开流式入口兼容问题；77 项不等于最终全量通过 |
| 最终前端 `npm test` | 16 个文件、86 项通过，8.82 秒，退出码 0 | store/组件/API 模拟测试，不是浏览器实际点击或断网验收 |
| 最终前端 `npm run lint` | 0 错误、13 条警告（7 个文件），退出码 0 | 既有 Hooks 警告保留 |
| 最终前端 `npm run build` | 824 个模块构建成功，构建阶段 5.55 秒，退出码 0 | 仍有超过 500 kB 的分块提示；不是新电脑安装 |
| 首次最终后端全量 | 97 项通过、1 项失败、63 条警告，30.01 秒，退出码 1 | `--maxfail=1` 后未运行的测试不能算通过；`test_chat_long_term_memory.py` 找不到模块公开 `stream_agent_to_sse` 属性，交回实现线程修复，不跳过旧测试 |
| 恢复公开流式入口后的七文件联合 | 111 项通过、25 条警告，48.80 秒，退出码 0 | 包含原记忆快照失败、正常两种导入顺序、模拟可选依赖缺失、wrapper 关闭/取消、流/HITL/MCP/Skills；真缺依赖仍走 fallback，不强制启用 Agent |
| 第二次后端全量 `integrated-final2` | 约 66% 后继续输出若干进度点，随后持续占用 CPU 且无终态；主控授权精确停止该轮 PID，进程回收退出码 1 | 记为挂起/人工中止，不捏造通过数量，不算完整通过。副本 `hardware-rag-verify-ca27e84d38db4951b91b7a4d0e0d8479` 保留；只停止经独有源码 GUID 核验的测试 PID 23652，未停止用户服务 |
| 第三次详细后端全量 `integrated-diagnostic`，额外参数 `-vv -o faulthandler_timeout=120` | 停在约 75% 的 `test_routes_kb.py::TestKbUpload::test_upload_success_returns_doc_id_and_status`；120 秒堆栈确认 AnyIO portal 正等待退出、后台停在 `_run_index_worker` 的 runner cancel-all 路径 | 已停止自有诊断进程；副本 `hardware-rag-verify-a70b7635c12c480ab2e4a7f8f7442326` 保留。不是全量通过；需要修正被取消 worker 的循环等待，并保留实体线程完成前不释放索引许可的约束 |
| 索引 runner 取消修复后的联合回归 | 35 项通过、10 条警告，26.71 秒，退出码 0 | A29 的四文件、新增 runner/上下文/异常回归及原 TestClient 上传用例；许可和 active 文档在实际线程结束前仍保留，不误报完成 |
| 索引取消补丁后的全量 `integrated-final3`，`-vv -o faulthandler_timeout=120` | 收集 545 项：537 项通过、1 项失败、183 条警告，132.50 秒，退出码 1；最后 7 项因 `--maxfail=1` 未执行 | 索引取消与原上传均通过；首个失败是 HTTP 404 的依赖日志脱敏断言，业务 `EXEC_ERROR` 正确，不能称全量通过。副本 `hardware-rag-verify-14c796aec9014b5d980de5c185877788` 保留 |
| WebFetch 请求范围 HTTPX INFO 脱敏 | 12 项通过、25 条警告，Pytest 11.77 秒，退出码 0 | 旧实现实测 1 项失败；原 8 项保留并强化 INFO 检查。双 WebFetch 重叠、真实 MockTransport 旁路请求、异常/取消清理有回归；不全局静音日志，不改变权限或成功结构 |
| 最终完整后端 `integrated-final4` | 549 项全部通过、183 条警告，Pytest 133.35 秒，退出码 0 | 自然结束，未跳过上述失败测试。主控逐文件比对源码/测试/公开 MD 共 236 文件 SHA-256，当前工作区与副本 0 差异；不是浏览器、真实模型或固定依赖空白安装验收 |

上述 404 回归使用受控 HTTP 响应，没有外网请求。工具自身错误消息和 warning 未泄漏 URL，但当时 HTTPX INFO 仍输出合成查询参数，不能把自身日志安全泛化为依赖日志安全；另有关闭日志 handler 的诊断，不能用它掩盖脱敏失败。已用 ContextVar 标记请求范围，锁/引用计数管理临时 HTTPX logger filter；A 结束后 B 的记录仍脱敏，父任务非 WebFetch HTTP 请求保留原日志，最后一条抓取退出才移除 filter。红灯日志 `webfetch-httpx-red-green/red-direct-8c1f79a771dd4893ba6befc579f66f24.log` 保留（1 项失败、25 条警告、11.99 秒），最终绿灯日志 `green-concurrent-11dbc42cebb549ab8f0b5ec2857feace.log` 和副本 `hardware-rag-verify-6b00fc42f9a54f04b23a72cbae491d8c` 保留。范围是当前 WebFetch 的 HTTPX URL 日志，不承诺所有第三方日志均已脱敏；修复后全量另记。

最终完整后端命令（在当前 PowerShell 直接调用，不再嵌套外层 shell）：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/integrated-final4 `
  -TestPaths @('tests', '-vv', '-o', 'faulthandler_timeout=120')
```

完整日志 `integrated-final4/backend-full-43ccbd13dd064eebbc06258dd1f1149c.log`，副本 `hardware-rag-verify-2c997524dab04c68aab2e0063960f5b7`。549 项是全量实际执行数，不是相加定向结果。真实模型四问只在这一轮绿色及源码哈希比对后授权，结果另记。

缓存/索引检查使用下面的精确参数；多路径必须作为 PowerShell 数组传递，不能把一串逗号参数误当作全部执行：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/final-verified2 `
  -TestPaths @('tests/test_rag_cache_freshness.py', 'tests/test_kb_index_responsiveness.py',
    'tests/test_kb_index_status_isolated.py', 'tests/test_task4_parallel_cache.py')
```

上述 77 项聊天联合回归的路径为 `test_agent_stream_reliability.py`、`test_chat_stream_termination.py`、`test_chat_routes_resume_terminal.py`、`test_hitl_resume_fail_closed.py`、`test_chat_skills_integration.py`、`test_mcp_agent_integration.py`、`test_resume_snapshot_integration_review.py`、`test_sse_adapter_heartbeat.py`，均在 `backend/tests/`。导入兼容修复后使用新副本重新验收，不能沿用这个检查点作为最终源码证明。

公开入口兼容与显式关闭 wrapper 的最终联合命令为：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/stream-wrapper-final `
  -TestPaths @('tests/test_chat_stream_termination.py', 'tests/test_chat_long_term_memory.py',
    'tests/test_agent_stream_reliability.py', 'tests/test_hitl_resume_fail_closed.py',
    'tests/test_mcp_agent_integration.py', 'tests/test_chat_skills_integration.py',
    'tests/test_skill_runtime_review.py')
```

这次副本为 `stream-wrapper-final/hardware-rag-verify-ba361243c99749b1b9db6c8b64c8b7f9`。恢复模块公开可 patch 的惰性入口，不改 `app/api/__init__.py`，正常结束/异常/调用者关闭或取消均显式关闭内层 adapter。此后的全量另用新副本；定向数量不替代全量。

全量回归进程启动前清空测试进程的 API 凭证环境变量，使用上方隔离脚本的新源码副本、新 SQLite/Chroma 和 E 盘临时目录；不复制 `.env`、加密 key、凭据 JSON 或原数据库，不使用用户手册。第一次失败副本保留于 `integrated-final/hardware-rag-verify-c9797f2793874ff7a11fbf453eaffd10`。没有安装/升级依赖、删除原材料、提交或推送。

评测线程的 49 项检查另用项目外隔离副本，运行 `pytest tests/rag_eval -q -p no:cacheprovider`，将 `config.TEST_DOCS_DIR`、`config.OUTPUT_DIR` 和黄金评测 `_OUTPUT_DIR` 指向该副本。复制阶段曾机械复制 `app/db/.enc_key`、`keys_store.json` 及备份，三份副本在 Pytest 启动前已精确删除；没有查看/打印/解析或用于认证，原文件不改动。不能将这一过程描述为“凭据从未被复制”。后续全量回归采用 Git 白名单复制，已核对这些凭据不属于复制范围。

标准质量评分在当前 Python 3.13.13 / DeepEval 4.0.7 环境预检失败；标准路径要求 Python 3.10～3.12 / DeepEval 1.5.5 和异步评分接口。本轮没有更换全局环境。协议回归通过与“取得完整标准质量总分”是两件事。

索引取消死循环修复只调整 `_run_index_worker` 及其回归：改用受 shield 保护、不会被 runner 当作 Task 一并取消的 executor Future，并显式复制 `contextvars`。实体线程结束前不归还索引名额；已完成 Future 的异常通过 `result()` 传播，避免线程自身 `CancelledError` 再被无限循环处理。旧 helper 的 AST 子进程红灯确实打印线程启动、runner 已取消及释放前快照后超时，父测试终止并回收自己的 PID 14440；不是依赖导入慢造成的假红灯。修复后的精确联合命令为：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/resumed-cancel-fix `
  -TestPaths @('tests/test_rag_cache_freshness.py', 'tests/test_kb_index_responsiveness.py',
    'tests/test_kb_index_status_isolated.py', 'tests/test_task4_parallel_cache.py',
    'tests/test_routes_kb.py::TestKbUpload::test_upload_success_returns_doc_id_and_status')
```

副本 `resumed-cancel-fix/hardware-rag-verify-d319c566e8a140d28e2bfc094dd53555` 保留。这次原上传测试未修改，没有通过 mock、skip 或提前释放实体线程掩盖问题。线程重复取消、上下文、线程自身取消异常/普通异常、文档状态和 active 生命周期都有回归。导入流程共用该 helper，但没有单独做真实 `kb_import` 端到端验收；修复后的最终全量另记。

### 真实模型小样本与失败保留

项目外 `live-smoke/prepare_and_run.py --run-live` 只使用已授权 Text 凭据在独立源码、数据库、上传与向量目录执行，计划 G001/G023/G026 和 EMPTY1 四个固定样本，顺序请求，无评分模型。测试 fixture 仅开放 `search_docs` / `list_kb_docs` 并保留原禁用过滤；不开放硬件、文件、网络或 MCP 工具，不替换产品提示词。客户端总期限 300 秒、读超时 45 秒；测试进程的模型/Embedding 重试预算为零。它不是全部 Agent 体验、标准评分或 35 题完整重跑。

第一次真实运行 `live-smoke/runs/20261003T191921Z-564b6ec2` 索引三份公开 Markdown，共 457 个片段。仅 G001 实际发送，31.98 秒，正常结束、四种映射完整，但无 `search_docs`、知识库来源和引用，RAG 证据门槛未通过。汇总脚本处理三个未尝试样本的 `content_checks=None` 时又异常，已停止后续请求、保留记录并修诊断脚本，不重写为成功。修复后将使用不同源码的新快照另记四样本验收；旧一次失败保留，累计请求数量与各版本分母分别报告。

第二次真实运行 `live-smoke/runs/20261003T201359Z-c41385a3` 按正常 main-first 启动，实际同一服务 PID 的 Agent/Text 门控和两工具预检均通过。四个 API 问题均进入 client dispatch：G001 用时 40.709 秒，事实检查通过，有工具结果、5 条来源、引用和成功结束；G023 为 `ReadError`，G026/EMPTY1 为 `ConnectError`，后三题没有可复核的完整回答。本轮同时发现上述索引取消挂起，主控撤回源码冻结并精确停止服务 PID 9956，中止与这些错误有重叠，不能据此归因模型/代理网络故障。汇总记录是 4 planned / 4 attempted / 1 verified；`run_status=complete` 仅说明计划尝试已走完，不代表验收成功。原版本一次加该版本四次，累计 **5 个已尝试 API 问题**，不是 Agent 内部模型轮数；Embedding 索引请求次数未测量，不能说为零。cleanup 记录确认 PID 退出、58087 空闲，旧失败不修改。

索引取消和日志脱敏补丁完成、完整后端绿色及主控源码哈希核对后，运行最终四样本 `live-smoke/runs/20261004T023214Z-4d9f84ef`。三文档全部 indexed，共 457 片段；同一 PID 10168 的 Agent/两工具预检通过，不置真开关、不替换产品提示词。主控比对 manifest 的 228 个文件哈希，当前工作区零差异。离线重排器记录 `weights_unavailable_offline` 降级。

| 样本 | 请求耗时 | 传输/结束 | 来源与内容检查 |
| --- | --- | --- | --- |
| G001 | 52.074 秒 | 正常完成、无错误 | 实际成功检索、5 条来源、有效引用，四模式诊断与关键事实人工核对通过 |
| G023 | 86.474 秒 | 正常完成、无错误 | 实际成功检索、5 条来源、有效引用，USARTDIV/BRR/误差诊断与关键事实人工核对通过 |
| G026 | 65.758 秒 | 正常完成、无错误 | 来源链路通过；未明确说 GPIO46 不选择 VDD_SPI，预设严格内容诊断失败，保留，不误记为已回答错误电压角色 |
| EMPTY1 | 43.476 秒 | 正常完成、无错误 | 空库实际检索、0 来源、无伪造数字引用、明确缺资料，规则诊断与人工核对通过 |

汇总为 4 planned / 4 attempted / 4 completed；资料题来源链路 3/3，严格内容诊断 2/3，空库 1/1。`acceptance_passed=false`，smoke 脚本退出码 1 来自严格内容诊断，四问无传输错误；运行完成不等于严格验收通过。累计旧 5 + 新 4 = 9 个 API 问题尝试，grader 0，不是 LLM 内部调用数；Embedding 请求次数未计量。新旧结果和全部失败分别保留，没有额外重试或调整检查来改成绩。summary/完整答案/工具与 SSE 记录在同一新目录；最终 cleanup 确认 PID 10168 已退出、58087 空闲，未停止其他进程。

主控完整答案旁审还发现 G026 的 JTAG 说明附有引用但此次检索上下文不含该依据，以及 src1 的 `page=3` 与页范围/citation 的 p1 不一致；需要 P1 的逐句引用与页语义核对，未声称浏览器缺陷已复现。具体依据与后续顺序见[质量报告第 8 节](rag-quality-baseline.md#8-2026-10-04-p0-实施与新验收)。本次四问不是标准质量总分。

本轮没有真实浏览器点击验收、新电脑安装验收、烧录或烧录后自动重连实机验收；不能以构建或受控模型小样本替代。

## 2026-10-03 Skills、记忆与 MCP 检查

### 2026-10-03 Skills 与记忆实网检查点

- 从真实公共仓库 `anthropics/skills` 的 `skills/brand-guidelines` 目录预览并导入，固定提交 `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4`。实际 ASGI HTTP 路由预览/导入为 200；新包默认停用、诊断 `supported`；同名重复导入返回 409，启用为 200，编辑后自动停用。仅删除本次项目外临时目录里的测试导入，删除 200，随后详情 404。没有操作用户现有技能。
- 真实 Windows HTTP 读取曾复现 socket 提前关闭后的 `WinError 10038`，并非代理超时；修复底层 EOF 处理后重新预览和上述导入流程通过。单元回归另证明关闭 socket 后仍保留总 deadline 检查。
- 普通聊天调用真实 `Text` 模型两次：保存合成记忆后，非空回答含预置合成板子标签；清空后新请求仍有非空回答但不再含标签。两次均成功结束、无错误事件。使用临时 SQLite、只在进程内读取已授权测试凭证，没有保存凭证或记忆原文到本文件。早期 128 token 的两次请求只有成功结束、没有可核对的最终答案，不能算记忆生效或清空验收；最终重试使用 2,048 token 回复预算。
- 真实 `Text` 模型分别完成手动、自动技能两次请求：两次均实际产生成功的 `load_skill` 工具结果，内容摘要与请求包一致，非空答案命中仅在合成技能正文中的预置口令，且成功结束、无错误事件。自动模式初始提示只有元数据，手动模式通过后端加载；客户端工具轨迹均不含说明正文。这里只证明该合成技能闭环，不证明任意 GitHub 包、第三方脚本或多模态技能可运行。
- 当前主控和 02 线的内置浏览器均因 `failed to write kernel assets` 初始化失败；新技能管理、选择及记忆页面只完成组件自动化，未完成本轮实际浏览器点击验收，不沿用旧日期截图冒充新功能已验收。

### MCP 集成与真实模型

- `test_mcp_communication.py` 的真实安全 stdio fixture 完成初始化、发现、并发回显/求和、停止、重启与旧 spec 不能转用新进程。RPC ID/坏消息/超时/远端业务错误/通知与分页限制等异常用受控协议进程测试，分别注明真实子进程或模拟异常，不将 mock 当作第三方兼容验收。
- `test_mcp_agent_integration.py` 走实际 LangChain ToolNode/Agent 图、真实安全 stdio 子进程和 ASGI 续跑 HTTP：default 与 bypass 模式均逐次确认；一个批次先拒绝 echo，再单独确认 add；echo 零次远端调用、add 一次。停止没有执行工具；缺失、错误、重复 call_id 为 409；Router 对参数/实例篡改、伪造 user_allow、重复派发、超时和非法原始 schema 有回归。这些集成场景的模型是可控流式模型，不是实网模型。
- 最后安全复核补充“停止时已排队调用不得发送”、连接/发现整体 60 秒截止、长名称不超过 64 字符与歧义名称区分。两份 MCP 测试合计 37 项通过、25 条警告、13.33 秒；另重跑下方全量，不用定向数量代替总体验证。
- `mcp_live_acceptance.py` 为显式手动运行脚本，不被普通 pytest 自动执行。仅允许项目外 `hardware-rag-verify-*` 源码副本及该副本私有 SQLite；凭证通过显式 `--credentials` 文件只读载入内存，关闭文件/硬件工具，只开放安全 MCP fixture。
- 真实 `Text` 两次聊天、各续跑一次，单次回复预算 2,048 token：拒绝检查先暂停、远端调用为 0、最终回答非空；允许检查先暂停、远端调用为 1、成功工具结果和最终非空回答包含合成回显标签。都无错误或额外确认，汇总 `passed:true`，退出码 0。不保存密钥、正文或远端返回到报告；未测试多模态 MCP、第三方服务或浏览器点击。
- 重现方式：先用隔离验证脚本取得新副本路径，在副本 backend 目录设置 `SQLITE_DB_PATH` 为新的副本内 `data/mcp-live.db`、`AGENT_CHECKPOINTER_TYPE=memory` 和离线 RAG 环境变量，再运行 `python -m tests.mcp_live_acceptance --credentials <本机未跟踪测试配置的绝对路径>`。不要在原工作区运行或把凭证复制进夹具。
- 实测使用 Python 3.13.13、jsonschema 4.25.1；requirements 固定的 jsonschema 是 4.23.0，本轮未安装/升级依赖。这不是对完整固定依赖新环境的重新验收。前端 79 项通过，0 lint 错误/13 条既有 hooks 警告，构建成功；仍有大分块与 React act 环境提示。
- 主控再次尝试内置浏览器和 Computer Use，均在初始化时报 kernel assets / 系统找不到指定路径。实际新页面点击、布局与确认卡互动均未验收，不通过终端伪装浏览器操作绕过。
- GitNexus 已运行符号/文件 impact 与 detect-changes。公共入口有 HIGH/CRITICAL 风险，新符号 UNKNOWN 用源码调用与测试进一步核实；当前图谱仍有错配/陈旧调用和新未跟踪文件覆盖不足，变化报告不是“图谱全量无风险通过”。没有提交或推送。

## 硬件实机验证

2026-09-26 使用用户确认的 COM5 开发板，完成应用内串口连接、接收实际开发板日志和正常断开检查。未发送串口数据，也未编译或烧录固件。日志出现过 `PSRAM ID read error` 和 BME280 探测失败信息，尚未诊断其硬件或固件原因。固件烧录及烧录后的串口自动重连仍未做实机验证。

## 已知待修复的鲁棒性问题

受控测试中，在聊天回答已开始输出后断开后端连接，页面超过一分钟仍显示生成中，未及时提示连接中断。2026-09-26 增加 Agent 每 15 秒心跳和前端聊天流 45 秒无数据报错；定向和全量自动化测试通过，但尚未用同一浏览器场景复测，不能写成实测通过。另一次测试从首页直接开始的未命名会话刷新后未出现在会话列表中；正式创建的会话刷新后可以保留，前一种路径仍需专门复测。

## 2026-10-24 Native Skills 最终隔离验收（全绿）

使用主仓库和既有 E 盘隔离 runner，唯一全绿 RunName 为 `native-verification-20261024-030026-caa451a2daac444892dfff848dbcee6e`。隔离产物、运行副本和缓存保存在版本库外的本机验收目录。使用既有 Python/Node 与依赖；运行期目录和缓存隔离到 E 盘。没有安装或下载依赖，也没有访问真实 GitHub、模型、服务、数据库或用户 Skills 数据。

后端只运行以下 13 个指定路径，不代表全后端测试：

```text
tests/test_skills_shared_install.py
tests/test_skills_native_agent.py
tests/test_skills_platform.py
tests/test_skills_platform_review.py
tests/test_skill_routes_integration_review.py
tests/test_skill_runtime_review.py
tests/test_chat_skills_integration.py
tests/test_tool_router_request_spec.py
tests/test_mcp_agent_integration.py
tests/test_hitl_resume_fail_closed.py
tests/test_agent_factory_request_tool_config.py
tests/test_tool_api_permission_gate.py
tests/test_chat_routes_resume_terminal.py
```

| 阶段 | 结果 | Exit code |
| --- | --- | ---: |
| 指定的 13 个后端路径 | 186 passed、26 warnings | 0 |
| 完整前端 Vitest | 24 个文件、159 passed | 0 |
| TypeScript `tsc -b` | 完成、无错误输出 | 0 |
| Vite build | 完成，9.37s；有大 chunk advisory | 0 |
| 隔离 runner | 所有阶段完成 | 0 |

Vite 的 advisory 报告 Prism chunk 621.06 kB、主 bundle 1,020.16 kB（minified）；Vitest 输出有模拟设置失败日志和 React `act(...)` 环境提示。它们没有改变上述阶段退出码。此前几轮失败后仅调整测试侧，没有改生产源码：包括把真实 GitHub importer 用例的猴补改为 `GitHubSkillImporter` 子类以避免类符号污染；让 mock repository 测试替身接收并向父类转发 `claude_adapter`；将 Windows 下写入 SKILL.md 的 fixture 改用相同 UTF-8 正文的 `write_bytes` 以保留 LF；以及将 MCP 用例改为正向检查 snapshot inclusion、auto mode 的 bound context 和未经确认的 synthetic Probe 仍被 `MCPRequestAuthorizations` 拒绝。

最终运行采用的授权测试文件 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `backend/tests/test_mcp_agent_integration.py` | `5B2D460AF23C5402C26E871F04231992CEE87B5C66AD45ED390333DCF5E6D327` |
| `backend/tests/test_chat_skills_integration.py` | `1E40D49BA3EF985881D98EA73EDA8BCB92041EDEC649E413FADA33658457C53B` |
| `backend/tests/test_skill_runtime_review.py` | `D89475538AD2FAB299C64D3002CF82C45513207CDB54336562951DD3A2EE204C` |
| `backend/tests/test_skills_platform.py` | `28D61B79984D428C2A3991E02B89E430FC1575B665517186139B312EA476014A` |
| `backend/tests/test_skills_native_agent.py` | `91CFBAAEEC3CA4B76304CFB5F7D4C59677F31AA9E8440E14B0F13D74E5E92BF7` |

本次仅覆盖上述 13 个后端路径及前端 Vitest、TypeScript 和 Vite build；不是全后端、实网或端到端验收。没有访问真实 GitHub、模型、服务、DB 或用户 Skills 数据，也没有完成完整 HTTP SSE/resume E2E。浏览器与实机行为未由本轮构建/自动化结果替代验收。

## W5 质量门禁（2026-11-05）

- E2E 骨架：`python scripts/ci/run_e2e.py`（临时 SQLite/Chroma、离线 HF）。
- 评测门禁：`python scripts/ci/run_eval_gate.py`（离线校验 30+5 题；`--report` 检查四指标阈值）。
- 两者归 W5；`.github/workflows/ci.yml` 的 job 接线归 W1。

## Memory v2（2026-11-06）

集成分支 `feat/memory-v2-integration` 的实际结果：

| 阶段 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `cd backend; python -m pytest tests -q -p asyncio -p pytest_timeout` | **1749 passed / 68 skipped / 0 failed**，602.12s，exit 0 |
| 记忆 v2 评测 | `python scripts/acceptance/memory_v2.py --skip-pytest --out <dir>` | **PASS**：22 个参考场景通过，产品适配 **8/8** 检查通过 |
| 前端 Vitest | `cd frontend; npm run test -- --run` | **322 passed / 45 files**，exit 0 |
| 前端 lint | `cd frontend; npm run lint` | 0 error、13 条既有 warning，exit 0 |
| 前端 build | `cd frontend; npm run build` | `tsc -b` + Vite build 成功，exit 0 |
| Chromium 验收 | Playwright/Chromium，临时 SQLite + 临时 `HARDWARE_AGENT_HOME` | 记忆 Hub 四区、条目创建、proposal 批准、硬件档案、工作记忆快照均通过；无 console/page error |

测试环境均使用 E 盘 `TEMP/TMP`、独立 `SQLITE_DB_PATH`、`AGENT_CHECKPOINTER_TYPE=memory`；浏览器证据在 `E:\DevCaches\Temp\mem-v2-browser\evidence`。真实模型自动抽取、真实聊天回答效果、硬件/串口未在本轮验收范围内，不能由上述软件回归替代。
