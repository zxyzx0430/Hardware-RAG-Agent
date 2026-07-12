# 代码与文档一致性二次扫描报告（2026-07-01）

> Change-ID: scan-code-doc-consistency（二次扫描）
> Scan Owner: T1 主控线程
> 上次报告: [consistency-scan-2026-06-30.md](file:///e:/Desktop/agent/docs/reports/consistency-scan-2026-06-30.md)
> 上次扫描时点: commit `48a6e5a`（2026-06-30）
> 本次扫描时点: commit `48a6e5a` + 未提交工作树改动（2026-07-01）

---

## 扫描元信息

- 扫描时点: 工作树状态（基于 commit `48a6e5a` + 各线程 7.1 未提交改动）
- 扫描方式: 3 个 sub-agent 并行（P0 验证+公共文档 / T2+T3 活跃区 / T5+T6 活跃区）
- 上次发现总数: 32 项
- 本次发现总数: 18 项（含未修复项 + 新发现）
- 修复进度: **2 项完全修复 / 2 项部分修复 / 9 项未修复 / 5 项新发现**

---

## P0 阻断问题修复进度

| 编号 | 上次问题 | 状态 | 当前证据 |
|------|---------|------|---------|
| P0-1 | useChatStore 引用未定义变量 permissionMode/toolKeys | ✅ **已修复** | `useChatStore.ts:509` 已从 `useSettingsStore.getState()` 解构 `permissionMode, toolKeys`；L538-539 用于构造请求体。pitfalls.md 已记 |
| P0-2 | tool_router.py WiringTool.run() 缺 title 参数 | ❌ **未修复（仍阻断）** | `tool_router.py:230-232` 仍为 `generate_wiring_svg(components=components, connections=connections,)`，缺 title；`svg_generator.py:38-42` 签名 `title` 必填无默认值。**运行时必抛 TypeError** |
| P0-3 | settings.py 默认 host=0.0.0.0 违反安全立场 | ❌ **未修复** | `settings.py:48` 仍为 `host: str = Field(default="0.0.0.0", alias="HOST")` |
| P0-4 | crud.py sandbox 白名单仍含 sandboxEnabled/sandboxImage | ❌ **未修复** | `crud.py:292` `ALLOWED_SETTINGS_KEYS` 仍含 `"sandboxEnabled", "sandboxImage"` |
| P0-5 | api-contract.md §4 缺失大量已实现路由 | ⚠️ **部分修复** | §4 已补 4 条 `/api/agent-sandbox/*` + 5 条 `/api/kb/collections*` + `/api/wiring`。**仍缺 7 类**：`/api/auth/store-key`、`/api/mcp/*`、`/api/feedback/*`、`/api/search/*`、`/api/wiring/extract`、`/api/tools/*`、`/api/token-usage` |
| P0-6 | architecture-map.md 9 处 sandbox 残留 | ⚠️ **部分修复** | `Last reviewed: 2026-07-01` 已更新。**仍残留 6 处**：L259（sandbox_routes.py+executor.py）、L837（settings 白名单含 sandboxEnabled/sandboxImage，与 P0-4 镜像）、L907-911（沙箱路由表）、L1044-1048（executor.py 限制参数）、L1091+L1133（目录树 sandbox 相关）、L1233（理解沙箱指引） |

### 🔴 仍阻断 demo 的问题（3 项，需立即修复）

#### 阻断 1: `tool_router.py` WiringTool 调用会 TypeError（P0-2 未修复）
- 文件: [backend/src/agent/tool_router.py:230-232](file:///e:/Desktop/agent/backend/src/agent/tool_router.py#L230)
- 责任线程: T2
- 现象: `generate_wiring_svg` 签名 `(title: str, components, connections)`，title 必填无默认值。当前调用只传 components/connections，运行时必抛 `TypeError: missing 1 required positional argument: 'title'`。同仓库 `wrappers.py:206` 已正确传 title，仅旧 dispatch 路径漏改。
- 建议: 补 title 参数（与 wrappers.py 一致），或给 svg_generator 的 title 加默认值。

#### 阻断 2: `settings.py` 默认 `host=0.0.0.0` 违反安全立场（P0-3 未修复）
- 文件: [backend/src/config/settings.py:48](file:///e:/Desktop/agent/backend/src/config/settings.py#L48)
- 责任线程: 公共区
- 建议: 默认值改为 `"127.0.0.1"`。

#### 阻断 3: `crud.py` settings 白名单仍含 sandbox 死键（P0-4 未修复）
- 文件: [backend/app/api/crud.py:292](file:///e:/Desktop/agent/backend/app/api/crud.py#L292)
- 责任线程: 公共区
- 现象: `ALLOWED_SETTINGS_KEYS` 仍含 `"sandboxEnabled", "sandboxImage"`。前端已无此配置（grep 0 命中），后端白名单仍允许写入脏数据。与 architecture-map.md L837 镜像残留。
- 建议: 从 `ALLOWED_SETTINGS_KEYS` 移除这两行，并同步改 architecture-map.md L837。

---

## 上次发现验证总表（按线程归属）

### T2 - Agent 全链路

| 编号 | 上次问题 | 优先级 | 状态 | 备注 |
|------|---------|--------|------|------|
| T2-1 | tool_router.py 缺 title 参数 | [必须修复] | ❌ 未修复 | 同 P0-2，升级为运行时 TypeError |
| T2-2 | /api/agent-sandbox/resume 命名误导 | [问题] | ✅ **已合理化** | 新增 `agent_sandbox_routes.py` 统一用 `/api/agent-sandbox/{policy,audit}` 前缀；L8 注释明确"resume stays in chat_routes.py"。`agent-sandbox` 已成为有意命名空间，不再是误导 |
| T2-3 | spec §1.1 描述过时 | [仅供参考] | ❌ 未修复 | spec:16 仍写"chat_routes.py L176-180 仍是 # TODO: ReAct loop"，实际 L176-180 是图片处理逻辑 |
| T2-4 | path_guard.py 沿用 sandbox 命名 | [仅供参考] | ❌ 未修复（属设计选择） | 与 T2-2 一致，agent-sandbox 命名空间已合理化，path_guard 沿用同命名不算 bug |

### T3 - 数据加 RAG

| 编号 | 上次问题 | 优先级 | 状态 | 备注 |
|------|---------|--------|------|------|
| T3-1 | 三个 chunker 超 300 行 | [建议修改] | ❌ 未修复 | hybrid=584 / multimodal=2132 / agent=1497，行数几乎不变。AGENTS.md 已豁免核心算法文件，记 TODO 即可 |

### T5 - 前端体验

| 编号 | 上次问题 | 优先级 | 状态 | 备注 |
|------|---------|--------|------|------|
| T5-1 | useChatStore permissionMode/toolKeys | [必须修复] | ✅ **已修复** | 同 P0-1 |
| T5-2 | useChatStore 1285 行 | [建议修改] | ❌ 未修复（恶化） | 反而增到 **1301 行**（+16），T5 持续加功能但未拆分 |
| T5-3 | ChatArea 484 / InputBar 472 行 | [建议修改] | ⚠️ 部分修复 | ChatArea 484→**459**，InputBar 472→**433**，有所收敛但仍超 300 |
| T5-4 | useSessionStore 326 / useKnowledgeStore 355 行 | [建议修改] | ❌ 未修复 | 行数完全一致 |
| T5-5 | useBookmarkStore log 闭包 | [仅供参考] | ❌ 未修复（扩散） | 同模式扩散到 useSessionStore.ts:12 和 useChatStore.ts:81，共 3 个文件 |
| T5-6 | useSessionStore 魔法数字 86400000 | [仅供参考] | ❌ 未修复 | 4 处硬编码未提取常量 |

### T6 - 硬件工作台

| 编号 | 上次问题 | 优先级 | 状态 | 备注 |
|------|---------|--------|------|------|
| T6-1 | useWorkbenchBridge workaround | [问题] | ✅ **已修复** | `useAppStore.ts:130` 已加 `resetWorkbenchOverride: () => set({ workbenchUserOverride: false })`；useWorkbenchBridge.ts:102 已调用；workaround 注释已删除；T5 已在 useChatStore.ts:709,751 接入 handleToolCallEvent/handleToolResultEvent 主路径 |

### 跨线程公共区

| 编号 | 上次问题 | 优先级 | 状态 | 备注 |
|------|---------|--------|------|------|
| 公共-1 | crud.py sandbox 白名单 | [必须修复] | ❌ 未修复 | 同 P0-4 |
| 公共-2 | settings.py host=0.0.0.0 | [必须修复] | ❌ 未修复 | 同 P0-3 |
| 公共-3 | api-contract.md §4 缺路由 | [必须修复] | ⚠️ 部分修复 | 同 P0-5 |
| 公共-4 | architecture-map.md 9 处 sandbox 残留 | [必须修复] | ⚠️ 部分修复 | 同 P0-6 |
| 公共-5 | api-contract.md §2.14 SANDBOX_UNAVAILABLE | [建议修改] | ❓ 未验证 | 本次未单独检查 |
| 公共-6 | architecture-map.md Store 数量 8 vs 10 | [建议修改] | ⚠️ 部分修复 | 标题未改，但发现实际是 9 个真 store + 1 个 bridge（详见新发现 4） |
| 公共-7 | architecture-map.md §七 API 路由总表组成错误 | [建议修改] | ❓ 未验证 | 本次未单独检查 |
| 公共-8 | thread-map.md 未提及 Trae 6 线程 | [建议修改] | ❌ 未修复 | 仍仅记录 Codex 00-08 线程 |
| 公共-9 | thread-map.md 仍把 06-sandbox 列为活跃 | [建议修改] | ❓ 未验证 | 本次未单独检查 |
| 公共-10 | 06-sandbox-scan.md 整文档过时 | [建议修改] | ❓ 未验证 | 本次未单独检查 |
| 公共-11 | 其他文档残留 sandbox 引用（5 处） | [建议修改] | ❓ 未验证 | 本次未单独检查 |
| 公共-12~18 | 各种代码规范违反 | [建议修改] | ❓ 未验证 | 本次未单独检查（与 P2 重构相关） |
| 公共-19~21 | 命名优化 / 行号偏差 / 魔法数字 | [仅供参考] | ❓ 未验证 | 本次未单独检查 |

---

## 新增文件扫描结果（5 个，全部健康）

| 文件 | 用途 | 被引用情况 | 引用断裂 | 代码规范 |
|------|------|------------|---------|---------|
| [agent_sandbox_routes.py](file:///e:/Desktop/agent/backend/app/api/agent_sandbox_routes.py) | Agent 沙箱权限管理 + 审计日志查询（GET/POST /policy、GET /audit） | main.py:39 import + main.py:323 include_router | 无 | ⚠️ `get_audit_logs` 5 个参数违反 max-params ≤ 3 |
| [audit_logger.py](file:///e:/Desktop/agent/backend/src/agent/audit_logger.py) | Agent 工具调用审计日志 SQLite 持久化 | main.py:268 / agent_sandbox_routes.py:82 / tools/run_command.py:125 / tools/file_ops.py:51 / test_v3_t7_audit.py:17 | 无 | ⚠️ `log_tool_call` 9 个参数严重违反 max-params ≤ 3 |
| [context_guard.py](file:///e:/Desktop/agent/backend/src/agent/context_guard.py) | Agent 累计 token 上限 + 单请求 wall-clock 超时保护（ContextVar 隔离） | sse_adapter.py:21 / test_v3_t5_context_guard.py:24 | 无 | ✅ 完全合规（75 行 / 函数 ≤ 10 行 / 参数 ≤ 3 / 命名常量齐全） |
| [loop_detector.py](file:///e:/Desktop/agent/backend/src/agent/loop_detector.py) | Agent 工具调用链环路检测（repeat / no_progress） | sse_adapter.py:92 | 无 | ✅ 完全合规（70 行 / 函数 ≤ 10 行 / 参数 ≤ 3 / 命名常量齐全） |
| [code_extractor.py](file:///e:/Desktop/agent/backend/app/hardware/code_extractor.py) | Arduino 代码器件/连线提取（正则匹配 pinMode/digitalWrite/Wire/SPI/Serial） | wiring_extract.py:16 | 无 | ✅ 完全合规（198 行 / 命名常量化 / 函数拆分到位 / 有类型注解） |

**关键正面结论**：T2 新增的 4 个 Agent 模块（agent_sandbox_routes / audit_logger / context_guard / loop_detector）全部被正确 import，无引用断裂。context_guard 和 loop_detector 完全符合 AGENTS.md 代码规范，质量良好。

---

## 新发现（5 项）

### [必须修复] 新发现 1: api-contract.md §4 仍缺 7 类已实现路由（P0-5 剩余）

- 文件: [docs/api-contract.md:303-342](file:///e:/Desktop/agent/docs/api-contract.md#L303)
- 类别: 引用断裂（文档说无但代码有）
- 稳定性: 稳定区
- 现象: §4 接口目录仍缺 7 类路由（对应后端文件均存在）：
  - `/api/auth/store-key`（auth.py 存在，architecture-map L895 已提及）
  - `/api/mcp/*`（mcp_routes.py 存在）
  - `/api/feedback/*`（feedback_routes.py 存在）
  - `/api/search/*`（search_routes.py 存在）
  - `/api/wiring/extract`（wiring_extract.py 存在）
  - `/api/tools/*`（tool_routes.py 存在）
  - `/api/token-usage`（chat_routes.py 中）
- 建议: 各线程把自家路由补进 §4 + §5 详情。

### [必须修复] 新发现 2: architecture-map.md L837 设置白名单与 crud.py 镜像残留

- 文件: [docs/architecture-map.md:837](file:///e:/Desktop/agent/docs/architecture-map.md#L837)
- 类别: 不一致
- 稳定性: 稳定区
- 现象: L837 文档白名单仍写"permissionMode / sandboxEnabled / sandboxImage"。crud.py:292 实际白名单也仍含 sandboxEnabled/sandboxImage（P0-4 未修复）。两侧镜像残留。
- 建议: 修 P0-4 时同步改 architecture-map L837。

### [建议修改] 新发现 3: architecture-map.md Store 数量与实际不符（9 vs 标 8）

- 文件: [docs/architecture-map.md:24, 763, 1176](file:///e:/Desktop/agent/docs/architecture-map.md#L763)
- 类别: 不一致
- 稳定性: 稳定区
- 现象: 文档 3 处说"前端 8 个 Store"，但 `frontend/src/stores/` 实际有 9 个真 store + 1 个 bridge。L763-806 列出 8 个 store（chat/session/settings/knowledge/app/bookmark/serial/log），遗漏 `useWiringStore.ts`（真正的 zustand store，存放器件/连线/SVG/BOM）。useWorkbenchBridge.ts 是 bridge 不是 store，可不计入。
- 建议: 把 useWiringStore 补入第 9 个 store，标题改为"前端 9 个 Store"；useWorkbenchBridge 在 bridge 章节单独说明。

### [建议修改] 新发现 4: 新增模块参数违反 max-params ≤ 3

- 文件:
  - [agent_sandbox_routes.py:74-80](file:///e:/Desktop/agent/backend/app/api/agent_sandbox_routes.py#L74) — `get_audit_logs(session_id, limit, tool_name, decision, risk_level)` 5 个参数
  - [audit_logger.py:28-38](file:///e:/Desktop/agent/backend/src/agent/audit_logger.py#L28) — `log_tool_call(session_id, tool_name, args, decision, decision_source, risk_level, exit_code, duration_ms, error)` 9 个参数
- 类别: 不一致（违反 AGENTS.md「代码规范」max-params ≤ 3）
- 稳定性: ⚠️ 活跃区待三次扫描（commit `48a6e5a` + 工作树改动 2026-07-01）
- 建议: 封装为 dataclass：
  - `log_tool_call(record: ToolCallRecord)` — 用 `ToolCallRecord` dataclass 封装 9 个字段
  - `get_audit_logs(filters: AuditQueryFilter)` — 用 `AuditQueryFilter` dataclass 封装查询条件

### [仅供参考] 新发现 5: T5 边界 3 项轻微问题

#### 5a. `initWorkbenchBridge` 死代码
- 文件: [frontend/src/stores/useWorkbenchBridge.ts:192](file:///e:/Desktop/agent/frontend/src/stores/useWorkbenchBridge.ts#L192)
- 现象: `initWorkbenchBridge()` 被 export 但全工程无任何调用点。fallback 订阅路径在 T5 已接入主路径后变成纯死代码。
- 建议: 二选一 — (a) 在 AppRoot.tsx 启动 useEffect 中调一次作为防御性备份；(b) 删除 fallback 段 + 文件头注释精简。

#### 5b. 模块级 log 闭包风险扩散到 3 个 store
- 文件: [useChatStore.ts:81](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.ts#L81) / [useSessionStore.ts:12](file:///e:/Desktop/agent/frontend/src/stores/useSessionStore.ts#L12) / [useBookmarkStore.ts:39](file:///e:/Desktop/agent/frontend/src/stores/useBookmarkStore.ts#L39)
- 现象: 三个 store 都用 `const log = useLogStore.getState().log;` 在模块顶层捕获 log 函数。对比 api/client.ts:11 用的是 `function getLog() { return useLogStore.getState().log; }` 函数封装。
- 建议: 统一改为 `function getLog() { return useLogStore.getState().log; }` 模式。这是上次 T5-5 的同模式，但范围已扩到 3 个文件。

#### 5c. ConfirmDialog "永久允许"按钮与"允许本次"行为完全相同
- 文件: [frontend/src/components/chat/ConfirmDialog.tsx:95-96](file:///e:/Desktop/agent/frontend/src/components/chat/ConfirmDialog.tsx#L95)
- 现象: 行 95 `允许本次` 和行 96 `永久允许` 都调用 `handleDecision("allow")`，传同一 decision 值。底部注释（行 101）写"永久允许将记录白名单规则（v3 实现）"，承认功能未实现。当前两个按钮对用户来说是欺骗性的。
- 建议: 二选一 — (a) 隐藏"永久允许"按钮直到 v3 实现；(b) 给 handleDecision 加第二个参数（如 `scope: "once" | "always"`），即便后端先打桩也要区分调用。

---

## 总结

### 整体评价

24 小时内 **T5 + T6 完成度高**（T5-1 / T6-1 两个 P0 阻断已修复，T5-3 部分收敛），**T2 Agent 模块扩展质量良好**（4 个新增模块全部无引用断裂，context_guard / loop_detector 完全合规），但 **3 个 P0 阻断未修复**（tool_router title / settings host / crud.py 白名单）仍在阻碍 demo；**api-contract.md §4 仍缺 7 类路由**（文档严重落后于代码）；**useChatStore 反而恶化**（1285→1301 行，T5 持续加功能但未拆分）；**模块级 log 闭包模式扩散**到 3 个 store。

### 数量统计

| 优先级 | 上次 | 本次 | 变化 |
|--------|------|------|------|
| [必须修复] | 6 | 5 | -1（P0-1 已修复，P0-5/P0-6 部分修复后仍计必须修复，新增 2 项） |
| [建议修改] | 17 | ~10 | -7（部分已修复，部分未单独验证） |
| [仅供参考] | 7 | ~6 | -1 |
| [问题] | 2 | 0 | -2（T2-2 / T6-1 已合理化或修复） |
| **合计** | **32** | **~21** | **-11** |

### 建议修复顺序

**P0 — 立即修复（7.1 当天）**：
1. T2 修 `tool_router.py` WiringTool 缺 title 参数（**1 行改动，运行时 TypeError**）
2. 公共区 修 `settings.py` host=0.0.0.0 → 127.0.0.1（**1 行改动**）
3. 公共区 修 `crud.py` ALLOWED_SETTINGS_KEYS 移除 sandboxEnabled/sandboxImage（**2 行删除**）
4. 公共区 同步 `architecture-map.md` L837 + L259/L907-911/L1044-1048/L1091/L1133/L1233 删除 6 处 sandbox 残留
5. 各线程补 `api-contract.md` §4 缺失的 7 类路由

**P1 — demo 冲刺中修复（7.2-7.10）**：
6. T2 重构 `audit_logger.log_tool_call`（9→1 参数，封装为 ToolCallRecord dataclass）
7. T2 重构 `agent_sandbox_routes.get_audit_logs`（5→1 参数，封装为 AuditQueryFilter dataclass）
8. T5 拆分 `useChatStore.ts`（1301 行 → 抽 useChatStream / useAgentEvents / useHitlConfirm / useChatPersistence 4 个 hook）
9. T5 统一模块级 log 闭包模式（3 个 store 改为 getLog() 函数封装）
10. T5 修 ConfirmDialog "永久允许"按钮（隐藏或加 scope 参数）
11. 公共区 补 `thread-map.md` Trae 6 线程系统说明
12. 公共区 补 `completed.md` 2026-07-01 v3 Agent 接入完成项

**P2 — demo 后批量重构（7.16+）**：
13. T3 拆分三个 chunker（AGENTS.md 已豁免核心算法文件，仅记 TODO）
14. T5 拆分 ChatArea / InputBar / useSessionStore / useKnowledgeStore
15. 公共区拆分 kb_manager / chat_helpers / serial_monitor
16. T5 清理 `initWorkbenchBridge` 死代码
17. T2 更新 spec §1.1 现状描述（删除 `# TODO: ReAct loop` 引用）

### 三次扫描触发机制

下列区域仍标注「⚠️ 活跃区待三次扫描」：
- T2 边界：新增 agent_sandbox_routes / audit_logger / context_guard / loop_detector 4 个模块 + tool_router 仍存 P0-2
- T5 边界：useChatStore 持续恶化 + 3 项轻微问题扩散
- T6 边界：T6-1 已修复，但 useWorkbenchBridge 仍有死代码
- 公共区文档：thread-map / completed 仍待补

三次扫描建议时机：T2 修完 P0-2 + T5 拆分 useChatStore 后，由 T1 重新触发。

---

## 末尾声明

- 二次扫描过程未修改任何代码文件
- 二次扫描过程未修改任何现有文档（仅新建本报告）
- 本报告归档到 `docs/reports/`（一次性报告目录，不长期维护）
- 上次报告 [consistency-scan-2026-06-30.md](file:///e:/Desktop/agent/docs/reports/consistency-scan-2026-06-30.md) 保留作为对比基线
