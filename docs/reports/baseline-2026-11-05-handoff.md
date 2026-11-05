# 新开发基线 handoff（2026-11-05）

> 最终状态（2026-11-05）：context 管线 + writer A 已合并到 `w1-baseline`，tag `baseline-2026-11-05-2`；后端与前端全绿，真实 HTTP Agent 冒烟通过。

## 1. 官方新基线

| 项 | 值 |
| --- | --- |
| 分支 | `w1-baseline` |
| 冻结 commit | `d3d01058`（context + writer A + 前端持久化修复） |
| tag | `baseline-2026-11-05-2`（当前）；旧 tag `baseline-2026-11-05-1` = `1da21736`，`baseline-2026-11-05` = `46563470` |
| 后端全量 | **1444 passed / 42 skipped / 0 failed / 0 超时**（453.59s） |
| 前端 | **265/265 passed**，lint 0 error（13 warnings）、tsc 0、build 0 |
| 工作区 | 干净（`git status` 无产品改动） |

> 注：旧 tag `baseline-2026-11-05` 的 914 passed 含 2 个被 `.gitignore` 忽略的旧测试文件（`test_html_ingest.py`、`test_ocr_parser.py`，共 24 例）；最终 tag 的 1444 是 context 合并后的可复现全量。

26 个失败**全部**是 context/compaction 簇：`context_guard.py` ×11、`sse_adapter.py` ×5、autocompact 断言 ×3、`chat_routes.py` ×2、`autocompact.py` ×1、其余 5 个 compaction/stream 断言。没有安全、HITL、FTS、删除级联、checkpoint GC、trace 失败。

## 2. 这个基线包含什么

- W1：快照/回滚 tag、CI 矩阵（Windows+Ubuntu）、pytest-timeout、依赖 lock、npm 统一、死代码移到 `scripts/legacy/2026-11-04/`、52 个根目录临时脚本迁移、`.items()` 修复、#5 夹具修复、Skills 集成夹具。
- W2：path_guard allowlist/密钥 deny/realpath、受限 argv + HIGH 确认、build/upload 认证 + 白名单 + build_id、首启 setup code/MCP/audit 鉴权、webfetch SSRF、前端 Provider key/token 移出 localStorage。
- W3：`messages_fts` 统一、rowid JOIN 修复、`search_history` 同源、删除 `sessions_fts`/`store_adapter`、消息 seq/version schema、删除级联、离线孤儿 GC/启动对账（实测 279 orphan chunks、45 orphan threads 清零，checkpoint 139MB→2.6MB）。
- W4：`agent_factory` 有界取消 join（`abfff2e5` + 4 个测试），checkpoint reset 不再永久挂起。
- W5：trace context、SSE/TokenUsage/audit 字段、离线评测门禁、release 清单。
- 五次 merge 全部纯增量、零回归；20 个 skip 是 W5 显式 E2E/trace 骨架（需真实服务）+ 1 个历史遗留。

## 3. 已知失败与边界

- context 管线合并后，后端全量为 **0 failed**；42 个 skip 是显式 E2E/trace/legacy 跳过，需真实服务或已弃用契约。
- **P0 已修复**：真实服务器 Agent 首请求的 `AsyncSqliteSaver ... exact supported binding` 及后续 `owner is quarantined`；hotfix `1da21736` 补 `setup()` 并改为 fail-closed，真实 HTTP 首请求/二次请求/重启后请求/普通聊天均通过。
- 真实模型/浏览器已做基础冒烟（见第 9 节）；实体烧录/自动重连、新电脑空白安装仍未验收。
- 20 个 skip 中 19 个是 W5 的 E2E/trace 骨架，代码内显式 `@pytest.mark.skip`，需要真实服务。

## 4. context 候选（已合并）

| 项 | 值 |
| --- | --- |
| 分支 | `context-rebased`（worktree `E:\Desktop\agent-worktrees\skills-chat-install-s01b-20261009`） |
| HEAD | `0dbff185`（已含 hotfix；合并到 `w1-baseline` 的提交是 `4d07036c`） |
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

## 5. writer 统一任务（已完成）

两条路，推荐 **A**：

- **A（已实施）**：`9e7669be` 以基线 `compact.writer_lifecycle` 为唯一 owner，新增 `writer_bridge.py`/`hitl_pause.py`/`async_join.py`，`sse_adapter`/`checkpoint_publish` 全部走 registry；已合并到 `4d07036c`。
- **B**：以候选 `src.agent.writer_lifecycle` 为 owner，替换基线 writer binding/epoch 路径。可能代码少，但会改动基线安全语义，风险高。

writer A 已完成；context 分支保留为历史，不要 reset/force/delete。

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

1. 先看 `w1-baseline` + tag `baseline-2026-11-05-1` 是否满足“新开发基线”的预期；26 个 context 失败是否接受为已知缺口。
2. 若接受：直接以 `baseline-2026-11-05-1` 为开发基线；下一步开 writer 统一任务（A）。
3. 若不接受：先修 26 个 context 失败（不做大合并），或让 context 候选在 writer 统一后继续。
4. 后续如需继续，基于 `baseline-2026-11-05-2` 开新分支；不要直接改冻结 tag。

## 9. 干净环境验收（2026-11-05）

- 目录：`E:\Desktopgent-test-baseline-20261105`（detached at tag `46563470`）；数据库/Chroma 复制到 E: 临时目录，原 `data/` 未改。
- 后端（旧 tag 干净目录）：**26 failed / 890 passed / 20 skipped / 0 超时**（232s）；hotfix 后 **26 failed / 918 passed / 20 skipped / 0 超时**。
- 前端：**174/174 passed**；lint 0 error（13 warnings）；production build 成功。
- 浏览器：`http://127.0.0.1:5173` 正常打开；模型选择/设置/技能开关/聊天框可见。
- 真实模型：普通模式 `/api/chat` 200，SSE 63 事件，`done success=true`，usage 2530+169；key 取自现有 `.env`，未打印。
- KB：`/api/kb/list` 122 文档 / 20 KB；BM25 16/20 KBs；558MB Chroma 集合完整。
- **P0（已修复，hotfix `1da21736`）**：修复前 Agent 首条消息失败 `AsyncSqliteSaver ... exact supported binding`，随后 `owner is quarantined`。根因：`_get_checkpointer()` 懒创建 `AsyncSqliteSaver` 后没有 `await saver.setup()`，而 `reset_thread_checkpoint_for_writer` → `_validate_bound_async_sqlite_saver` 要求 `is_setup=True`；单测 saver 已 setup，故没覆盖。修复后真实 HTTP 复测：Agent 首请求成功、同进程二次请求成功、重启后 Agent 成功、无 `owner is quarantined`。
- 普通聊天未带来源：`use_agent=false` 的 KB 检索回答“未找到文档”，需确认非 Agent 路径是否走 RAG；Agent 路径被 P0 挡住，未完成。
- 修复内容：`_build_sqlite_checkpointer` 在 `__aenter__` 后 `await checkpointer.setup()`；失败时先清理已进入的 cm，再 raise，不再静默降级 InMemorySaver。新增 `tests/test_checkpointer_setup.py` 4 条。

## 10. 最终合并与真实验收（2026-11-05）

- 合并：`4d07036c`（context 管线 + writer A + skills/resume），随后前端持久化修复 `4f37c4e7`、Agent observation 重名修复 `d3d01058`；最终 tag `baseline-2026-11-05-2`。
- 后端干净目录全量：**1444 passed / 42 skipped / 0 failed / 0 超时**（453.59s）。
- 前端：**265/265 passed**；lint 0 error（13 warnings）；tsc/build 成功。
- 真实 HTTP（真实模型、真实知识库副本）：Agent 首请求、同进程二次请求、重启后 Agent、普通聊天全部 `done success=true`，无 error、无 `owner is quarantined`。
- 修复了两个只有真实运行才会暴露的问题：checkpoint 未 `setup()`（`1da21736`）、`read_tool_observation` 被默认工具和条件追加重复注册（`d3d01058`）。
- 前端修复还补回了 Skill 安全投影：活动记录不再持久化 Skill 原始 `body`/返回内容，只保留安全字段；legacy 分片迁移在 quota/readback 失败时保留旧缓存。
- 未验收：实体烧录/自动重连、真实浏览器完整 E2E、新电脑空白安装；42 个 skip 中多数需要真实服务或已弃用契约。
