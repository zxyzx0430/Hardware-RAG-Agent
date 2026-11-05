# Memory v2 WP1 对抗审查报告

- 日期：2026-11-06
- 审查对象：`feat/memory-v2-review`，基线 `eeb6c33e`（`feat(memory): add v2 schema, project identity and session lifecycle`）
- 审查范围：`backend/app/db/memory_models.py`、`memory_store.py`、`memory_v2_schema.py`、`project_identity.py`、`database.py`（init hook）、`api/crud.py`（session purge）、相关迁移/生命周期测试
- 只读参考：`backend/src/agent/memory_context.py`、`backend/app/api/memory_routes.py`、`backend/app/api/memory_epoch.py`、`backend/app/api/session_epoch.py`、`backend/src/agent/tools/groups/memory.py`
- 约束：本分支只新增报告和复现测试，未改产品代码；未 push、未运行真实模型/浏览器、未写用户数据库

## 结论

**PASS（WP1 基础层可合并），但 Memory v2 发布门槛尚未满足。**

- WP1 的核心数据迁移、重启幂等、会话删除清理、worktree/子目录项目身份、snapshot 幂等和状态机都按设计工作；177 项既有/新增定向测试通过。
- 本审查新增 10 个复现测试，全部失败，对应 8 个确认项：7 个可修复缺陷（含 1 个安全门槛问题）和 1 个需要 00 决策的身份设计问题。
- 另有 4 项不依赖 WP1 产品代码的接线风险（snapshot 读取默认值、清空记忆 epoch、episodic 保留、清理任务缺失），会在 WP2/WP3/WP4 接线时决定门槛是否真的成立，建议由 00 在集成时逐项闭环。

## 确认问题

严重度定义：高 = 直接违反计划硬门槛，或会在真实使用中造成数据/安全问题；中 = 需要特定接线路径或生命周期事件才会触发，但会发生；低 = 健壮性/一致性缺口。

### P1 高：密钥检测对自然语言和短小写凭据存在绕过

- 位置：`backend/app/db/memory_store.py:84-124`（`_SECRET_PATTERNS` / `_ASSIGNMENT_RE` / `_assignment_value_is_secret`）
- 复现：
  1. `store.create(tier="user", kind="fact", content="password: correct horse battery staple")`
  2. `store.create(tier="user", kind="fact", content="api_key: abcdefghijkl")`
  3. `store.create(tier="user", kind="fact", content="my password is correcthorse")`
  4. `store.create(..., evidence={"password": "correct horse battery staple"})`
- 实际证据：
  - 复现测试 `test_natural_language_passwords_are_rejected` 3 个参数全部 `DID NOT RAISE MemorySecretError`，条目被写入。
  - `test_evidence_natural_language_password_is_rejected` 同样写入成功。
  - 原因：赋值正则只接受 `key:` / `key=` 形式，值在空白处截断；`_assignment_value_is_secret` 只在长度 ≥16、含数字或全大写时判定为密钥，`correct`/`correcthorse`/`abcdefghijkl` 都不满足。
- 影响：计划硬门槛“密钥/令牌/密码存储或检索：0”不成立。WP2 自动投影会把 checkpoint claim 写入候选（proposed），WP3 审核界面会展示，WP4 可能批准后注入上下文；即使用户明确要求“记住密码”，也不应写入 SQLite。该缺陷在 v1 已存在，但 v2 的自动投影和证据字段扩大了暴露面。
- 建议修复（不在本分支改产品）：
  - 在 `contains_secret` 中增加自然语言形式：`password is X`、`密码是/为 X`、`api key is X`，并对赋值值做长度 ≥8 或高熵判定，而不是只认数字/长度；
  - 对 `evidence`、`structured`、`snapshot body` 共用同一检测，并补回归测试覆盖上述四类；
  - 若继续使用启发式，WP2 投影前必须再做一次 `redact_secrets`，且审核界面默认脱敏显示。

### P2 中：working 记忆去重不区分 session，跨会话合并

- 位置：`backend/app/db/memory_store.py:878-915`（`find_duplicate` 过滤 tier/project/principal/kind，未包含 `scope_key`）
- 复现：
  1. `store.upsert_proposed({"tier":"working","scope_key":"session-a","kind":"fact","content":"UART uses pins 1 and 2."})`
  2. `store.upsert_proposed({"tier":"working","scope_key":"session-b","kind":"fact","content":"UART uses pins 1 and 2."})`
- 实际证据：`test_working_memory_dedupe_stays_within_one_session` 失败；第二次返回的条目 id 与第一次相同，`scope_key` 仍是 `session-a`。
- 影响：如果 WP2/WP3 用 `upsert_proposed` 处理 session-only 候选（WP5 的参考实现已经包含 session_only 场景），会话 B 可能拿到属于会话 A 的 working 行：会话 A 删除时该行被清掉，会话 B 的“工作记忆”随之消失；编辑/过期也会串。属于跨会话归属错误，不是单纯的重复数据。
- 建议修复：`find_duplicate` 的归属键对 working tier 必须包含 `scope_key`（session id）；episodic 如果按 session 归属也应包含。可把去重键统一为 `(tier, project_id, principal_id, scope_key, kind, normalized_content)`，或为 working/episodic 单独实现 session-scoped 去重。

### P3 中：user 记忆去重受 project_id 影响，同一偏好会按项目重复

- 位置：`backend/app/db/memory_store.py:878-915`、`917-954`；计划中的 Proposal JSON 固定包含 `project_id`
- 复现：
  1. `upsert_proposed({"tier":"user","project_id":"project-a","kind":"preference","content":"Prefers 3.3V logic levels."})`
  2. `upsert_proposed({"tier":"user","project_id":"project-b","kind":"preference","content":"Prefers 3.3V logic levels."})`
- 实际证据：`test_user_memory_dedupe_ignores_delivery_project` 失败；两次返回不同 id，DB 中有两条 user/preference。
- 影响：user 记忆定义为跨项目偏好；如果 WP2 按统一 proposal 形状给所有 tier 都带 `project_id`，同一偏好在两个项目各存一份，WP3 的 user 查询（通常不按 project 过滤）可能注入重复条目，审核界面也会出现重复候选。
- 建议修复：`create`/`upsert_proposed` 对 `tier="user"` 像 global scope 一样把 `project_id` 归一化为空；或 `find_duplicate` 对 user tier 忽略 `project_id`。二选一并在接口文档写清。

### P4 中：legacy `longTermMemory` 在物理清理后会于下次启动复活

- 位置：`backend/app/db/memory_v2_schema.py:156-223`；幂等标记存在被导入行自身的 `evidence_json` 中
- 复现：
  1. 旧库写入 `settings.longTermMemory = "Prefer 3.3V logic levels."`
  2. `apply_memory_v2_schema` → 1 条 `source='import'` 行
  3. 模拟 `purge_expired()` 物理删除该行（当前 v2 计划会接入 TTL/软删除清理）
  4. 再次 `apply_memory_v2_schema`（重启）
- 实际证据：`test_legacy_import_does_not_resurrect_after_physical_cleanup` 失败；删除后重启又出现 1 条 import 行（`assert 1 == 0`）。
- 影响：用户删除/清理掉这条记忆后，只要旧 `longTermMemory` 设置还在（迁移有意保留），重启就会重新生成候选。属于“删除后仍可检索”的复活路径，违反生命周期预期。当前 `purge_expired` 尚无生产调用者，所以是**潜在**问题，但计划已要求接入清理。
- 建议修复：把“已导入”标记放到独立的 `settings` 键（例如 `_memoryV2LongTermMemoryImported`），或用 `settings` 中的迁移版本标记，而不是依赖 entry 行存活；同时保留对软删除行的判定，避免用户在未清理前被重复导入。

### P5 中：旧调用方通过 `scope="project"` 创建的新行没有 `project_id`，迁移也不会回填

- 位置：`backend/app/db/memory_store.py:515-583`（`create` 未从 path-like `scope_key` 推导 `project_id`）；`memory_v2_schema.py:109-153` 只处理 `schema_version < 2`
- 复现：
  1. 已升级的库上调用 `store.create(scope="project", scope_key=<绝对路径>, kind="fact", content=...)`
  2. 检查返回的 `project_id`
  3. 重启（再次迁移）
- 实际证据：`test_legacy_project_scope_create_populates_project_id` 失败；返回 `project_id == ""`，`schema_version == 2`，因此下次迁移不会处理它。当前 `POST /api/memory`（`memory_routes.py:116-126`）和 `memory_remember` 工具（`tools/groups/memory.py:156-164`）仍在走这条路径。
- 影响：WP3 的新项目过滤按 `project_id == current_project_id()` 查询时看不到这些行；`find_duplicate`/`upsert_proposed` 也不会把它们与实际项目合并，造成重复或“记忆消失”。legacy 注入路径仍能读到，所以只在 WP3 切换到新过滤后暴露；WP3 计划同时拥有这两个调用文件，必须在接线时传 `tier/project_id`，否则这是稳定复现的集成缺口。
- 建议修复：`create` 在 `scope="project"` 且 `project_id` 为空、`scope_key` 为路径时调用 `current_project_id(scope_key)` 回填（与迁移保持一致）；或明确要求所有调用方传 `project_id`，并在 WP0 接线清单中列为必检项。

### P6 中（设计决策）：`git init` / 添加或更换 origin 会改变 project_id，旧项目记忆失联

- 位置：`backend/app/db/project_identity.py:128-143`（identity = `git common dir + normalized remote`；非 git 用路径）
- 复现：
  1. 普通目录 `root`，`project_id_before = current_project_id(root)`（路径回退）
  2. 用该 id 写入一条 `tier=project` 记忆
  3. `git init`，再 `git remote add origin https://github.com/example/board.git`
  4. `project_id_after = current_project_id(root)`，按新 id 查询项目记忆
- 实际证据：`test_existing_project_memory_survives_git_init_and_origin_add` 失败；id 从 `dfbee23a…` 变为 `1fa9f8b5…`，按新 id 查不到旧记忆。
- 影响：单机用户从“本地目录”到“git 仓库”再到“推送远端”是常见生命周期；每次身份变化都会让此前保存的项目记忆在新项目 id 下不可见（原始行仍在 DB，但没有任何查询会命中）。worktree、子目录、Windows 大小写、环境变量覆盖这些已测场景都稳定，问题只在身份输入发生变化时。
- 说明：这是计划中 `hash(git common dir + normalized remote URL)` 公式的直接结果，需要 00/用户决定是否接受。若要求“项目记忆不因 init/remote 丢失”，建议：
  - identity 只使用 git common dir（worktree 已由 common dir 共享，remote 对单机不提供额外区分）；
  - 或保留 remote 作为元数据，读取时同时接受旧 id/路径回退 id（alias 表）；
  - 至少在 `AGENTS.md`/计划中记录“修改 origin 或 git init 会使旧项目记忆失联”。

### P7 低：`tier="working"` 允许没有 session scope，形成无法随会话删除的孤儿行

- 位置：`backend/app/db/memory_store.py:533-550`（`create` 不校验 working 必须有 `scope_key`）
- 复现：`store.create(tier="working", kind="fact", content="Sessionless working note.")`
- 实际证据：`test_working_tier_requires_a_session_scope` 失败（`DID NOT RAISE`）；返回 `scope_key == ""`、`status == "active"`。
- 影响：该行不会被 `purge_session_memory` 删除，也不参与任何 session 过滤，成为永久残留；legacy 注入不会命中它，但 `list(tier="working")`/Memory Hub 会显示，且“session/working 残留 0”的生命周期门槛无法保证。
- 建议修复：working tier 且 `scope_key` 为空时抛 `MemoryValidationError`（或强制要求传 session_id）。WP1 交接里已把它写成调用方约定，但 store 层面缺少防线。

### P8 低：密钥启发式的假阳性会静默丢弃合法旧记忆

- 位置：`memory_store.py:105-112`；`memory_v2_schema.py:167-169`
- 复现：旧库 `longTermMemory = "password: REQUIRED"`（大写强调）或 `"WiFi password: CHANGEME"`。
- 实际证据：`_assignment_value_is_secret` 对全大写值返回 True；迁移只记 warning 并跳过导入，用户在 UI 看不到任何提示。显式 `create` 则返回 `MEMORY_SECRET_REJECTED`。
- 影响：合法笔记可能无法保存/迁移；单机用户不会去翻日志。属于安全启发式的取舍，建议在错误信息/审核界面说明原因，或对“全大写但像占位符”的值（`REQUIRED`、`CHANGEME`、`TODO`）做白名单。
- 说明：本条未写复现测试，因为它是启发式取舍而非明确缺陷；列在这里供 00 决定是否接受。

## 设计风险 / 接线前必须确认（非 WP1 产品缺陷）

### R1 高：snapshot 读路径默认“不过滤 epoch”，最短调用即 fail-open

- 位置：`memory_store.py:1042-1054`（`get_snapshot` 完全不过滤 epoch）、`1056-1085`（`list_snapshots(memory_epoch=None)` 表示不限）、`1087-1100`（`latest_snapshot` 同样）。
- 风险：WP3 最自然的调用 `latest_snapshot(session_id)` 会返回清空记忆前的快照；`get_snapshot(session, seal)` 在任何 epoch 下都返回。计划要求“memory-clear epoch 使旧 snapshot 失效”，但失效完全依赖调用方记得传 epoch。另外 `read_memory_clear_epoch()` 在 DB 异常/损坏时返回 `None`，传 `None` 正好等于“不过滤”。若 WP3 用 `None` 做 fail-open，清空后旧快照会复活。
- 建议：读路径改为必须显式传 epoch（缺省即拒绝返回），或提供 `latest_snapshot_safe(...)` 并在 None 时不返回任何快照；`get_snapshot` 增加 epoch 参数。WP3 接线时用测试覆盖“clear 后 latest/get 均返回 None”。

### R2 高：现有“清空长期记忆”在 `longTermMemory` 为空时不会推进 epoch，也没有 v2 清理入口

- 位置：`backend/app/api/crud.py:1509-1515`
- 事实：只有 `previous is not None and previous.value` 时才调用 `stage_memory_clear_epoch`；清空动作不删除 `memory_entries`/`memory_snapshots`。
- 风险：v2-only 用户（`longTermMemory` 为空，working/project/user 记忆由 v2 产生）在设置页点“清空记忆”时，记忆清空 epoch 不变，旧 snapshot 仍然有效；计划硬门槛“删除记忆后仍可检索：0”和 WP5 的 `memory_clear` 场景在产品侧没有对应动作。WP3 的 API 清单里也没有看到全局清空端点。
- 建议：00 明确 v2 的“清空记忆”入口和语义（是否删除 user/project 条目、是否只失效 working/snapshot），并保证即使 `longTermMemory` 为空也推进 epoch；补一个走 HTTP 的端到端测试。不要复用“设置文本非空才推进”的 v1 条件。

### R3 中：episodic 是否随会话删除保留，计划未定义

- 位置：`memory_store.py:458-482`（purge 只删 `tier=working & scope_key=session` 或 `scope=session & scope_key=session`）；`create(tier="episodic")` 默认 `scope=project`。
- 风险：WP2 若把 checkpoint 历史投影为 episodic（plan 要求保留每次 checkpoint），这些行不属于 working，不会随会话删除；单机用户删除会话后，episodic 摘要仍可能在后续检索中出现。若这是有意保留，需要在 UI/文档说明；若不是，`purge_session_memory` 需要支持按会话归属的 episodic 行。
- 建议：00/WP2 明确 episodic 的归属字段（建议用 `scope_key=session_id` 或独立 session 列）和删除策略，并纳入 WP5 `session_delete` 场景的断言。

### R4 中：没有生产清理调用者，snapshot 与软删除行会无界增长

- 位置：`purge_expired`（`memory_store.py:962-983`）、`purge_session` 无 `backend/app`/`src` 生产调用；`memory_snapshots` 无保留策略。
- 风险：计划要求“软删除物理清理率”“存储增长有界”，但 WP1 只提供接口，没有任何调度/启动钩子。snapshot 每次 checkpoint 一条，长期会持续增长，且旧 epoch 快照只过滤不删除。WP1 交接已登记，这里只作为集成前必须接线的项。
- 建议：WP3/WP0 在启动或定时任务中调用 `purge_expired`；snapshot 保留最近 N 条/按 session 删除，或至少在删除会话时已覆盖（已覆盖），并补容量测试。

### R5 低：`create` 允许 tier 与 scope 不一致的组合

- 位置：`memory_store.py:533-550`
- 事实：`create(tier="user", scope="session", scope_key="s1")` 会得到 `tier=user` 但 `scope=session` 的行，legacy 注入会把它当作 session 条目注入；`tier="episodic", scope="session"` 则会被 `purge_session_memory` 的第二个条件删除（因为 `scope == "session"`），与 episodic 保留语义冲突。
- 风险：接口层没有强制 `TIER_TO_SCOPE`，WP2/WP3 一旦传错组合会产生难以解释的归属/清理行为。
- 建议：`create` 校验 `scope == TIER_TO_SCOPE[tier]`（或忽略调用方 scope，完全由 tier 推导）。

## 已验证通过的部分

以下为本次实际运行确认，不是仅阅读代码：

1. **迁移幂等**：旧 schema（无 v2 列）+ 重复 `apply_memory_v2_schema`，列、索引、snapshot 表只建一次；旧行 tier/status/project_id 回填正确；`schema_version=2` 行不被二次改写。
2. **重启流程**：真实文件 SQLite 上 `init_db()` → 创建 project 条目和 snapshot → 再连续 `init_db()` 两次，条目、snapshot、project_id 均保持不变；`longTermMemory` 只导入一次。
3. **会话删除**：`crud.delete_session` 在同一事务内删除 working/legacy session 条目与该 session 的 snapshots；user/project 条目和另一个 session 的 snapshot 保留；重复删除幂等。
4. **项目身份**：真实主仓库、linked worktree、子目录三者 `project_id` 相同；`HARDWARE_AGENT_PROJECT_PATH` 覆盖生效；非 git 路径稳定；Windows 大小写/斜杠归一。
5. **状态机**：只允许 `proposed→active/rejected`、`active→superseded`；拒绝的条目可被重新 propose；默认读路径只返回 active，审核路径可显式读 proposed。
6. **dedupe 基本边界**：不同 project_id、不同 kind、不同 principal 不合并；软删除/过期/rejected 不参与去重。
7. **snapshot**：`(session_id, seal_id)` 重复写入幂等且首个 body 胜出；`memory_epoch`/`delete_epoch` 显式过滤生效；snapshot body 中的明显密钥被拒绝。
8. **清理保护**：`purge_expired` 只删除软删除和已过期行，不误删未过期 active。
9. **真实旧库只读检查**：本机现有 `hardware_rag.db` 没有 `memory_entries` 表，`longTermMemory` 为空；迁移在真实库上只会建表，不存在旧数据风险。该项为只读检查，未在用户库上执行迁移。

## 实际运行的测试与计数

环境：`D:/python/python.exe` 3.13.13；`TEMP/TMP=E:/temp/memory-v2-review`；独立 `SQLITE_DB_PATH`；`AGENT_CHECKPOINTER_TYPE=memory`；`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；`HF_HUB_OFFLINE=1`；`TRANSFORMERS_OFFLINE=1`；`-p asyncio -p pytest_timeout`。

| 批次 | 文件 | 结果 |
| --- | --- | --- |
| WP1 新增 | `test_memory_v2_schema.py`、`project_identity`、`store`、`lifecycle` | **34 passed** |
| 既有回归 | `test_memory_store.py`、`test_memory_runtime.py`、`test_session_delete_resume.py`、`test_memory_clear_resume.py` | **79 passed** |
| 既有回归 | `test_manual_memory_settings.py`、`test_manual_memory_integration_review.py`、`test_chat_long_term_memory.py`、`test_crud_message_idempotency.py` | **46 passed** |
| 既有验收 | `test_memory_acceptance.py`（单独运行，满足隔离条件） | **18 passed** |
| 本审查复现 | `test_memory_v2_review_findings.py` | **10 failed**（全部为有意复现，见上） |

合计既有/新增回归 **177 passed / 0 failed**；复现文件 **10 failed**。另有手动脚本验证重启幂等、真实库只读 schema、worktree/子目录/环境变量身份。

关键复现测试节点：

- `test_working_memory_dedupe_stays_within_one_session`
- `test_user_memory_dedupe_ignores_delivery_project`
- `test_working_tier_requires_a_session_scope`
- `test_natural_language_passwords_are_rejected[...]`（3 项）
- `test_evidence_natural_language_password_is_rejected`
- `test_legacy_import_does_not_resurrect_after_physical_cleanup`
- `test_legacy_project_scope_create_populates_project_id`
- `test_existing_project_memory_survives_git_init_and_origin_add`

## 建议给 00 的处理顺序

1. **合并前**：确认 WP3 会修改 `memory_routes.py` 与 `memory_tools` 的 create 调用（P5）；确认 WP2 对 user tier 的 `project_id` 归一化（P3）；确认 working 候选的去重键（P2）。
2. **WP0 集成时**：把 R1（snapshot 读取必须带 epoch/失败关闭）、R2（v2 清空记忆入口与 epoch 推进）写成端到端测试；R3、R4 决定是否本轮处理。
3. **发布前**：修复 P1 密钥检测并补测试；P4 改用独立导入标记；P6 由 00/用户决定身份公式；P7、P8 可作为低优先级健壮性项。
4. 本分支的复现测试不要直接删除；修复后应转为通过（P6 若决定接受现状，则改为记录该行为的正向测试并更新计划）。

## 边界与未验证

- 未验证 WP2/WP3/WP4 接线后的真实聊天、SSE、压缩投影和注入；本报告只覆盖 WP1 的 db/store/迁移/生命周期层。
- 未运行真实模型、浏览器、前端构建、真实开发板；未在用户数据库上执行迁移（仅只读复制 schema 检查）。
- 未运行完整后端套件；只运行了与 memory/session 相关的 13 个文件。
- GitNexus `detect-changes` 在 worktree 内无索引，未作为本次门禁；按 WP1 交接同样记录为不可用。
- 未验证 Windows junction/符号链接、UNC、远程同步、多用户（按计划不做）。

## 变更文件

- 新增 `memory-v2-review.md`（本报告）
- 新增 `backend/tests/test_memory_v2_review_findings.py`（10 个复现测试，预期失败）
- 未修改任何产品代码、既有测试、依赖或文档
