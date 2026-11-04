# 上下文压缩候选软件验收

日期：2026-10-26。00 已完成独立源码、限定差异、报告及实际隔离副本检查，接受下述候选范围的定向软件交付。

候选目录：`E:/Desktop/agent-worktrees/skills-chat-install-s01b-20261009`。HEAD：`e61b8a6505ee827734af237c7f721e02cc1c7a24`。产品仍为未提交改动；没有同步到 `E:/Desktop/agent` 的产品文件，没有合并、提交或推送。两个工作树原有 dirty 内容分别保留。

## 交付行为

- 发送前压缩成功且满足预算才替换请求历史；摘要失败、空摘要或压后仍超预算时保留完整输入，停止本轮请求。
- 流中由真实工具更新触发压缩，经宿主 writer lease、原生 `RunControl` / `GraphDrained`、同任务关闭及 join 后发布，按精确保存配置续跑模型；工具不会为此次压缩重放。
- 真实 SQLite 保存与父检查点读回、摘要正文和完整来源引用恢复、宿主发布凭证确认均有定向运行证据。只有合法来源进入既有归档范围，未扩大原始工具、Skill 或系统正文的归档资格。
- 普通请求和 resume 的 `ContextLimitError` 映射为 `CONTEXT_LIMIT` 及失败终态，未退回只回答最后问题的降级路径。
- 多 pending 暂停保留全部待处理调用凭证，UI 和批准仍针对单个调用；被隔离 writer 所属业务会话不能通过换新图取得 lease。

本轮修正包括：publisher 仅额外接受原生 `branch:to:model` 且值为 `None` 的内部调度标记；其它分支、PUSH/Send 和非空标记仍拒绝。保存确认、父检查点条件和事务所有权未因测试而放宽。另补齐发送前预算复核、错误映射和完整 pending 暂停凭证。

## 实际验证

所有指定验收用例共 **13 个不同 case 分组通过**。不是最终文件单次完整运行，也不把重复运行的通过数相加。

| 证据 | 实际结果 | 接受范围 |
| --- | --- | --- |
| native round5，RunName `s05-context-compaction-final-20261025-07` | 6 passed / 4 warnings / exit 0 | 原生 memory 保存续跑、缺 lease 拒绝、四种非法调度写拒绝 |
| native round6，RunName `s05-context-compaction-final-20261025-08` | 7 passed / 1 failed / 4 warnings / exit 1 | 前六项重复通过，新增真实 SQLite / host 归档与凭证 / 正文恢复 / 续跑通过；随后停在新测试的错误导入 |
| native round7，RunName `s05-context-compaction-final-20261025-09` | 3 passed / 1 failed / 27 warnings / exit 1 | 发送前保文与预算、普通及 resume 错误终态通过；暂停测试最后误重用已消费 pending 而停止 |
| native round8，RunName `s05-context-compaction-final-20261025-10` | 2 passed / 27 warnings / exit 0 | 真实 `handle_auto_resume` 多 pending 与单 call 确认、quarantined owner 拒绝 |
| adapter，RunName `s05-context-compaction-final-20261026-11` | 1 passed / 27 warnings / exit 0 | 真正消费生产 `_iter_agent_sse`，自动触发压缩、原生 drain/join、SQLite 发布和配置续跑；tools/model 各执行一次 |

最终 adapter 用例只替换摘要客户端为已登记模型名对应的本地确定性响应；通过生产 `init_stream_state`、`accumulate_tokens` 初始化合成历史计数。没有直接设置 `needs_compact`、伪造保存成功或替代生产 drain/publisher。00 确认追加该用例前的既有测试文件前 34,580 字节逐字节保持不变。

另外五个指定回归文件有分组通过覆盖：`test_autocompact_failure_preservation.py`、`test_structured_checkpoint.py`、`test_context_event_archive.py` 在 regression run2 首个失败前整文件通过；`test_context_history_reader.py` 和 `test_ordinary_request_budget.py` 在 run3 整文件通过。run2 总结果为 149 passed / 1 failed / 30 warnings / exit 1；run3 为 91 passed / 27 warnings / exit 0。两轮有重叠，不能相加为总通过数。

旧正例所用普通 StateGraph 夹具没有 lease/drain，新建 `compaction_writer_support.py` 提供真实宿主绑定、原生 drain 和 lease 覆盖调用者的异步上下文；只迁移两处已失败正例，保留成功、九字段、完整来源、四条引用和尾部断言。历史 reader 测试的“Skills 隐藏普通工具”断言与已批准契约不符，改为检查仍注册，并用合法真实 locator 验证 Skills auto 运行时仍拒读、同一 scope/locator 在 off 时恢复完整原文。没有更改产品来迁就测试。

原 14 路径首轮失败、后续诊断失败与 preflight 抄表错误全部保留。未重跑完整 14 路径套件，不能声明其整体通过。

## 证据路径与复现

摘要、完整命令、实际输出、退出码和各轮隔离目录保留在：

- `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/s05-context-compaction-native-protocol-verification-20261025/`：`report-round5.md`、`report-round6.md`、`report-round7.md`、`report-round8.md`、`report-adapter-integration.md`。
- `E:/Desktop/agent-acceptance-artifacts/core-plan-20261006/s05-compaction-source-regression-20261026/`：`report-run1.md`、`report-run2.md`、`report-run3.md`；原 preflight `report.md` 保留。
- 唯一使用既有 `s02-b2-history-projection-implement-20261020-00/run-tests.ps1` 和 `D:/python/python.exe`；每轮新 RunName、E 盘白名单隔离副本、临时 SQLite、PyArrow 预载、仅 asyncio pytest 插件、离线模型配置及 E/D 盘缓存和临时目录。

关键报告 SHA-256：adapter `716366C789A8B01A67F95944873C3ADE54241E0609B65EC76CAB120DC1D9D41B`；regression run3 `C06239AD1D1DC8A97186AA14BB7B132346BCD8D8E44DDA106FBD0B2D99C28AAA`；regression run2 `85241D96233875657F1A6EEDD159DB8ADC51D30DBC5EAC80B32FC43061DC657E`。

## 最终源码与测试

00 将实际隔离目录中的源码、测试及相关依赖与候选逐项比对，七个产品文件始终匹配冻结表。最后 adapter 副本为 `s05-context-compaction-final-20261026-11/hardware-rag-verify-50f9f65ca21c4c7cb2f3e346e80dbcfe`。迁移差异限于新 helper、两个正例上下文及一个 reader 契约用例；`conftest.py` 未变。

| 候选相对路径 | SHA-256 |
| --- | --- |
| `backend/app/api/chat_routes.py` | `18B618C7AE8A7DCC8112CC351FCC57CEA542D09867F756CC2717A100EA8491C4` |
| `backend/src/agent/hitl_handler.py` | `27BC854972840D3F85123B47DBBEF2E1076FDC35F45B18A11FE7BB95D18DC1D8` |
| `backend/src/agent/writer_lifecycle.py` | `6322A5689E512B3EC22E2451C337520D04D6450C152F23D0DB5E6C33D3AE4D5F` |
| `backend/src/agent/sse_adapter.py` | `4A6E83D4FED100ADDE94BEA96C27B21C4773EAB175F8FE6D2CB269E7F38035BB` |
| `backend/src/agent/compact/autocompact.py` | `7601A977BA809994699EC47EE6766526693F3AF26C256B4424AE2AB9BA041314` |
| `backend/src/agent/compact/checkpoint_publish.py` | `0A98B1A1895753B811544C80ACE7FD67362C946AA61AF131CE13948F929B029A` |
| `backend/app/api/compaction_checkpoint.py` | `28693B921C52574731187D3DCA9610D32ECB2F508518F6BF44F7AFB52FC369BB` |
| `backend/tests/test_s05_context_compaction_acceptance_20261025.py` | `F992CDADBF297ECDC525E6BFD3E723BE18FB21A62CD01C051B2E3337A1503089` |
| `backend/tests/compaction_writer_support.py` | `919109235E320A82B87723968A70B97C9C20023FC78CCEE67B6F8AB641E7BB52` |
| `backend/tests/test_autocompact_failure_preservation.py` | `9B5E81D3D06C15F12348E8091E34805660476DDCEEA353AF5DD3C25B83B68A44` |
| `backend/tests/test_structured_checkpoint.py` | `1AC85A25C6DC3539692B6F42E13F0E1FDD3F5414AF937E52D1A59C660BF30F75` |
| `backend/tests/test_context_history_reader.py` | `96765BFAEB6372412EB4FFA372ED5517ACC600F911FD684CACEF97B64665F593` |

## 未验边界与执行偏差

没有调用联网模型或真实用户数据，没有启动服务；未做真实 provider、浏览器或完整 HTTP/SSE/resume 联网 E2E，未运行整个后端或前端套件。本地确定性摘要证明协议和保文行为，不证明摘要的实网语义质量。此实现不承诺任意 runner/saver、跨进程全 writer CAS、跨数据库全局原子性或物理安全擦除。

一次子线程违反禁止工具命令的边界，运行 `./scripts/gitnexus.ps1 status`，输出显示 pnpm 下载两个包到 E 盘工具缓存。该调用没有纳入允许的测试步骤，不能称本轮完全无下载；已停止后续命令并在 regression run2 报告留证，没有检查或清理缓存。产品及授权文件 SHA 核验没有发现该调用造成任务文件变化，不能由此推断缓存或执行环境完全未受影响。

03、04 及其 worker 的本轮写权和验证权均已结束。当前限定的候选软件交付完成，不自动扩展其它模块。
