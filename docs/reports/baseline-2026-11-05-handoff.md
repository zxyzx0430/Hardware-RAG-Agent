# 新开发基线 handoff（2026-11-05）

> 给用户醒来后的接手说明。产品代码已冻结在 tag `baseline-2026-11-05`；context 候选未合并。

## 1. 官方新基线

| 项 | 值 |
| --- | --- |
| 分支 | `w1-baseline` |
| 冻结 commit | `46563470` |
| tag | `baseline-2026-11-05`（annotated） |
| 后端全量 | **26 failed / 890 passed / 20 skipped / 0 超时**，232s（tag 干净目录） |
| 前端 | 174/174，lint 0 error（13 warnings）、tsc 0、build 0 |
| 工作区 | 干净（`git status` 无产品改动） |

> 注：主工作区曾记录的 914 passed 含 2 个被 `.gitignore` 忽略的旧测试文件（`test_html_ingest.py`、`test_ocr_parser.py`，共 24 例）；tag 本身可复现的数字是 890 passed。

26 个失败**全部**是 context/compaction 簇：`context_guard.py` ×11、`sse_adapter.py` ×5、autocompact 断言 ×3、`chat_routes.py` ×2、`autocompact.py` ×1、其余 5 个 compaction/stream 断言。没有安全、HITL、FTS、删除级联、checkpoint GC、trace 失败。

## 2. 这个基线包含什么

- W1：快照/回滚 tag、CI 矩阵（Windows+Ubuntu）、pytest-timeout、依赖 lock、npm 统一、死代码移到 `scripts/legacy/2026-11-04/`、52 个根目录临时脚本迁移、`.items()` 修复、#5 夹具修复、Skills 集成夹具。
- W2：path_guard allowlist/密钥 deny/realpath、受限 argv + HIGH 确认、build/upload 认证 + 白名单 + build_id、首启 setup code/MCP/audit 鉴权、webfetch SSRF、前端 Provider key/token 移出 localStorage。
- W3：`messages_fts` 统一、rowid JOIN 修复、`search_history` 同源、删除 `sessions_fts`/`store_adapter`、消息 seq/version schema、删除级联、离线孤儿 GC/启动对账（实测 279 orphan chunks、45 orphan threads 清零，checkpoint 139MB→2.6MB）。
- W4：`agent_factory` 有界取消 join（`abfff2e5` + 4 个测试），checkpoint reset 不再永久挂起。
- W5：trace context、SSE/TokenUsage/audit 字段、离线评测门禁、release 清单。
- 五次 merge 全部纯增量、零回归；20 个 skip 是 W5 显式 E2E/trace 骨架（需真实服务）+ 1 个历史遗留。

## 3. 已知失败与边界

- 26 个 context/compaction 失败是**已知、隔离**的；它们属于 context 候选要解决的范围，不是基线回归。
- 真实模型/浏览器已做基础冒烟（见第 9 节）；实体烧录/自动重连、新电脑空白安装仍未验收。
- 20 个 skip 中 19 个是 W5 的 E2E/trace 骨架，代码内显式 `@pytest.mark.skip`，需要真实服务。

## 4. context 候选（未合并）

| 项 | 值 |
| --- | --- |
| 分支 | `context-rebased`（worktree `E:\Desktop\agent-worktrees\skills-chat-install-s01b-20261009`） |
| HEAD | `014c4e28` |
| 基础 | `439d8c56`（已 merge 冻结基线 `46563470`） |
| 原分支 | `codex/context-compaction-server-history-20261105`，HEAD `5f4cf227`，未动 |
| 后端定向 | 251 passed / 17 skipped / 0 failed |
| 前端 | `tsc -b` 0；server-history + useChatStore 52 passed |
| 后端全量 | **70 failed / 1375 passed / 37 skipped / 0 timeout** |
| patch/bundle | `E:\DevCaches\Temp\context-20261105\context-20261105.patch` / `.bundle`（4.3MB） |

**未合并原因**：两套 writer lifecycle 接口不兼容：

- `chat_routes` 用基线 `compact.writer_lifecycle.WriterLease`（token-only：`acquire/begin_run/finish_run/quarantine/release`）。
- `sse_adapter`/`checkpoint_publish` 仍调用候选 `src.agent.writer_lifecycle.WriterLease` 的 rich 方法（`start_run/validate/finish_run/quarantine/mark_paused`）。
- 换 import 只能解决 ImportError，运行到 writer 路径会 AttributeError；把 adapter 换回基线版本又会让 context 的 `_PublicationSaver`/mid-run/pause-receipt 流程 collection 失败。

70 失败主要集中在：observation index 9（已回滚）、Skills install 8、pending snapshot 11、context_window 硬上限 4、autocompact 4、MCP 4、source_registry 4、bypass 4、request_tools 1，其余为 adapter/夹具。

两轮分桶结果（2026-11-05）：`request_tools` 4→1（`b966f014`）；observation index 修好后带出 19 个 generic archive 新失败，已用 `014c4e28` 回滚；skill preflight 补 `import asyncio`（`2456ff2b`）。最终仍 70 failed，远超 26，停止硬修，保留为 feature branch。

**W4 复核补充（P1，2026-11-05）**：聊天技能安装除了 S1 无入口（`_prepare_chat_skill_install` 无调用者），`install_tool.py:488/491/494` 的 `authorize_dispatch/request_binding/matches_resume` 也没有调用者（S10），grant 永远不会 claim；干净导出 `b966f014` 的 `test_chat_skills_install_tool` 9 failed。即使补入口也装不上，属于 context feature 的未完成工作。writer P0 在 `b966f014` 仍复现（`sse_adapter.py:437` 仍是 `lease.start_run`）；我此前引用的 `_iterate_agent_sse_once`/`activate_writer_binding` 只在主基线 `E:\Desktopgent`（46563470），不在合并树。

## 5. writer 统一任务（下一项，未开工）

两条路，推荐 **A**：

- **A（推荐）**：以基线 `compact.writer_lifecycle` 为唯一 owner，重写 `sse_adapter`/`checkpoint_publish` 约 50 个调用点，补 pause receipt/host-write 语义。保留基线的 epoch/lease/安全语义，回归预算大。
- **B**：以候选 `src.agent.writer_lifecycle` 为 owner，替换基线 writer binding/epoch 路径。可能代码少，但会改动基线安全语义，风险高。

在 A/B 完成前，**不要合并 context-rebased**，也不要 reset/force/delete 任何分支。

## 6. 回滚

```bash
git switch w1-baseline
git checkout baseline-2026-11-05   # 或 git reset --hard 46563470（谨慎）
```

逐项回滚：

```bash
git revert -m 1 46563470   # W2 HITL fixtures
git revert -m 1 439d8c56   # W5 observability
git revert -m 1 43f64a37   # W3 P2/P3
git revert -m 1 0172af1b   # W3 P1
git revert -m 1 41a90cbe   # W4 cancellation
```

快照 tag：`pre-reset-2026-11-04`（`2569ad3c`）。死代码恢复：`git mv scripts/legacy/2026-11-04/dead-modules/* backend/src/agent/`。

## 7. 磁盘

- C: 曾 0GB，已清理到 **1.47GB**：HuggingFace/OpenAI 缓存迁到 `E:\Cache` junction，清 `.codex\.tmp`、Edge/CrashDumps/D3D 缓存。未动用户文档/密钥。
- E: 约 31GB 可用。后续大文件/测试临时目录请继续放 E:。

## 8. 醒来后的建议顺序

1. 先看 `w1-baseline` + tag 是否满足“新开发基线”的预期；26 个 context 失败是否接受为已知缺口。
2. 若接受：直接以 `baseline-2026-11-05` 为开发基线；下一步开 writer 统一任务（A）。
3. 若不接受：先修 26 个 context 失败（不做大合并），或让 context 候选在 writer 统一后继续。
4. 再考虑把 context 候选的 71 条降到 ≤26 后合入 `baseline-v2`（当前 `baseline-v2` 仍指向 46563470，未使用）。

## 9. 干净环境验收（2026-11-05）

- 目录：`E:\Desktopgent-test-baseline-20261105`（detached at tag `46563470`）；数据库/Chroma 复制到 E: 临时目录，原 `data/` 未改。
- 后端：**26 failed / 890 passed / 20 skipped / 0 超时**（232s），失败仍是已知 context/compaction 簇。
- 前端：**174/174 passed**；lint 0 error（13 warnings）；production build 成功。
- 浏览器：`http://127.0.0.1:5173` 正常打开；模型选择/设置/技能开关/聊天框可见。
- 真实模型：普通模式 `/api/chat` 200，SSE 63 事件，`done success=true`，usage 2530+169；key 取自现有 `.env`，未打印。
- KB：`/api/kb/list` 122 文档 / 20 KB；BM25 16/20 KBs；558MB Chroma 集合完整。
- **P0（真实服务器）**：Agent 模式首条消息失败 `AsyncSqliteSaver ... exact supported binding`；随后同进程请求报 `owner is quarantined`，重启才恢复。根因：`_get_checkpointer()` 懒创建 `AsyncSqliteSaver` 后没有 `await saver.setup()`，而 `reset_thread_checkpoint_for_writer` → `_validate_bound_async_sqlite_saver` 要求 `is_setup=True`；单测的 saver 已 setup，故没覆盖到。
- 普通聊天未带来源：`use_agent=false` 的 KB 检索回答“未找到文档”，需确认非 Agent 路径是否走 RAG；Agent 路径被 P0 挡住，未完成。
- 建议：先修 P0（`_build_sqlite_checkpointer` 进入 context 后 `await checkpointer.setup()`），再重跑 Agent 冒烟；这是基线可用的前置条件。

