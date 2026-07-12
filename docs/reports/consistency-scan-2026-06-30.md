# 代码与文档一致性扫描报告（2026-06-30）

> Change-ID: scan-code-doc-consistency
> Scan Owner: T1 主控线程
> Scan Mode: Spec（仅产出报告，不改代码）

---

## 与既有扫描报告的关系

本报告与 `docs/review/01-app-scan.md` ~ `08-infra-scan.md` 并存：
- **本报告**：按 Trae 6 线程分组（T2 / T3 / T5 / T6 / 公共区），聚焦「引用断裂 / 过时注释 / 不一致」三类问题，扫描时点 2026-06-30 commit `48a6e5a`
- **既有 8 份扫描**：按 Codex 8 线程分组（01-app ~ 08-infra），聚焦子系统，扫描时点 2026-06-21（已过时，其中 06-sandbox-scan.md 应归档）

本报告不替代既有扫描，只补充 Trae 冲刺期一致性视角。

---

## 扫描元信息

- 扫描时点 commit: `48a6e5a`
- 扫描文件数: 173（稳定区 ~120 / 活跃区 ~53）
  - 后端 Python：`backend/app/api/*.py` 19 个 + `backend/src/**/*.py` 45 个 + `scripts/**/*.py` 64 个
  - 前端 TS：`frontend/src/stores/*.ts` 11 个 + `frontend/src/components/**/*.{ts,tsx}` 39 个
  - 关键文档：`api-contract.md` / `architecture-map.md` / `thread-map.md` / `AGENTS.md` / `completed.md` / `pitfalls.md`
- 扫描人: T1 主控线程（3 个 sub-agent 并行扫描）
- 发现总数: 32 项（[必须修复] 6 / [建议修改] 17 / [仅供参考] 7 / [问题] 2）

---

## 🔴 阻断 demo 问题（需立即修复，最多 3 条）

### 阻断 1: `useChatStore.ts` 引用未定义变量 `permissionMode` 和 `toolKeys`，聊天发送会崩

- 文件: `frontend/src/stores/useChatStore.ts:426-427`
- 责任线程: T5
- 类别: 引用断裂
- 现象: `sendMessage` 构建 `requestBody` 时引用 `permissionMode` / `toolKeys`，但全文无声明、无 import、无解构。一旦执行该行抛 `ReferenceError`，整个聊天发送流程崩溃。看似 T5 接入 Agent 时遗漏从 `useSettingsStore` 取这两个字段。
- 建议: 在 `sendMessage` 函数顶部从 `useSettingsStore.getState()` 解构，或直接读 `useSettingsStore.getState().permissionMode` / `.toolKeys`。

### 阻断 2: `tool_router.py` `WiringTool.run()` 调用 `generate_wiring_svg` 缺 `title` 参数

- 文件: `backend/src/agent/tool_router.py:227-230`
- 责任线程: T2
- 类别: 引用断裂
- 现象: `generate_wiring_svg` 签名为 `(title, components, connections)`（`svg_generator.py:38-42`，3 个位置参数），但 `WiringTool.run()` 只传 `components=..., connections=...` 两个关键字参数。运行时抛 `TypeError: missing 1 required positional argument: 'title'`。其他两处调用（`wrappers.py:206` / `workbench_tools.py:70-72`）均正确。
- 建议: 改为 `generate_wiring_svg("Wiring Diagram", components, connections)` 或加命名常量。

### 阻断 3: `settings.py` 默认 `host=0.0.0.0` 违反「只监听 127.0.0.1」安全立场

- 文件: `backend/src/config/settings.py:48`
- 责任线程: 公共区（T1 直接派单）
- 类别: 不一致（违反 AGENTS.md 安全立场）
- 现象: AGENTS.md「安全立场」明确「服务只监听 127.0.0.1，不暴露到公网」，但默认 `host="0.0.0.0"` 监听所有网卡。用户首次运行（无 .env）即暴露到局域网，与 demo 自部署定位冲突。
- 建议: 默认值改为 `"127.0.0.1"`，需外部访问时由用户在 .env 显式覆盖。

---

## 按线程归属分组

### T2 - Agent 全链路

#### [必须修复] T2-1: `tool_router.py` `WiringTool.run()` 缺 `title` 参数
- 文件: `backend/src/agent/tool_router.py:227-230`
- 类别: 引用断裂
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象 / 建议: 见阻断 2

#### [问题] T2-2: `/api/agent-sandbox/resume` 路由命名误导（含 "sandbox" 字样但实际是 HITL）
- 文件: `backend/app/api/chat_routes.py:360-408`
- 类别: 不一致
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: 路由实际功能是 Agent HITL（human-in-the-loop）工具确认恢复，对应 `hitl_handler.resume_agent_after_user`，与已删除的 docker sandbox 无关。前端 grep 0 调用，目前是死端点。命名易让 T1 / 扫描器误判为 sandbox 残留。
- 建议: 先问 T2：此端点是否计划在 Agent 全链路接入后由前端调用？若是，建议改名 `/api/agent/resume` 或 `/api/agent-hitl/resume`；若不再使用则删除。

#### [仅供参考] T2-3: spec §1.1 现状描述过时（`# TODO: ReAct loop` 已不存在）
- 文件: `docs/superpowers/specs/2026-06-30-agent-react-design.md:16`
- 类别: 过时注释
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: spec §1.1 写「`chat_routes.py L176-180` 仍是 `# TODO: ReAct loop`」，但实际 Grep 已无匹配；L176 现为附件处理后 `text_part = ...`。Agent 主路径（L199-214）已取代该 TODO。
- 建议: 在 spec §1.1 标注「已实现，见 §2.2」或删除该现状描述。

#### [仅供参考] T2-4: `path_guard.py` 沿用 `sandbox` 命名（非残留但易误判）
- 文件: `backend/src/agent/path_guard.py:5,9,29-33,89-92`
- 类别: 不一致（命名歧义）
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: `SANDBOX_DIR_NAME="agent-sandbox"` / `ensure_sandbox_dir()` 是 Agent 文件工具临时目录保护机制，与已删除的 docker sandbox 无关。命名沿用 "sandbox" 让全局 grep 难以区分。
- 建议: 长期可改名 `AGENT_WORK_DIR` / `agent-workspace`，非阻塞。

---

### T3 - 数据加 RAG

#### [建议修改] T3-1: 三个 chunker 文件严重超过 300 行限制
- 文件:
  - `backend/src/rag/chunking/hybrid_chunker.py`（584 行）
  - `backend/src/rag/chunking/multimodal_chunker.py`（2133 行，超 7 倍）
  - `backend/src/rag/chunking/agent_chunker.py`（1497 行）
- 类别: 不一致（违反 AGENTS.md「max-lines ≤ 300」）
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: 三个 chunker 都大幅超标，混了 prompt 模板 / trace dataclass / pipeline / render / merge 多重职责。AGENTS.md「工程约定」明确「核心算法文件 multimodal_chunker.py / agent_chunker.py 不重构」，故记 TODO 后处理即可。
- 建议: demo 后拆分 prompt 常量到 `*_prompts.py`、trace dataclass 到 `*_trace.py`、merge 工具到 `*_merge.py`。当前先在文件头加 `# TODO: refactor after demo (超 300 行)` 标注。

---

### T5 - 前端体验

#### [必须修复] T5-1: `useChatStore.ts` 引用未定义变量 `permissionMode` 和 `toolKeys`
- 文件: `frontend/src/stores/useChatStore.ts:426-427`
- 类别: 引用断裂
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象 / 建议: 见阻断 1

#### [建议修改] T5-2: `useChatStore.ts` 1285 行严重超过 300 行限制
- 文件: `frontend/src/stores/useChatStore.ts`
- 类别: 不一致（违反代码规范）
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: 单文件 1285 行，超 300 行限制 4 倍。SSE event 处理（onEvent 大 switch）/ persistLastTurn / branchThread / exportConversation / loadMockData 全堆在一个 store 里。
- 建议: demo 后拆分：mock 数据 → `useChatStore.mock.ts`、SSE 处理 → `chatSseHandler.ts`、branchThread/export → `chatActions.ts`。当前先加 TODO。

#### [建议修改] T5-3: `ChatArea.tsx`（484 行）和 `InputBar.tsx`（472 行）超 300 行限制
- 文件:
  - `frontend/src/components/chat/ChatArea.tsx`（484 行）
  - `frontend/src/components/input/InputBar.tsx`（472 行）
- 类别: 不一致（违反代码规范）
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: ChatArea 含 UserMessageContent / AssistantMessageContent / Lightbox 多重子组件；InputBar 含输入框 / 附件预览 / 拖拽上传 / 快捷键 / 引用回复多重职责。
- 建议: demo 后拆出 `UserMessageContent.tsx` / `AssistantMessageContent.tsx` / `MessageLightbox.tsx`；InputBar 拆 `AttachmentPreview.tsx` / `QuotedReplyBar.tsx`。

#### [建议修改] T5-4: `useSessionStore.ts`（326 行）和 `useKnowledgeStore.ts`（355 行）超 300 行
- 文件: `frontend/src/stores/useSessionStore.ts` / `useKnowledgeStore.ts`
- 类别: 不一致（违反代码规范）
- 稳定性: 稳定区（T5 边界外）
- 建议: useSessionStore 拆出 session 分组 / 时间格式化工具；useKnowledgeStore 拆出 chunk viewer 子 store。

#### [仅供参考] T5-5: `useBookmarkStore.ts` 模块级捕获 log 函数（潜在 stale closure）
- 文件: `frontend/src/stores/useBookmarkStore.ts:39`
- 类别: 不一致
- 稳定性: 稳定区
- 现象: `const log = useLogStore.getState().log;` 在模块级执行捕获 log 引用。当前无 bug，但若未来 `useLogStore.log` 被替换会失效。
- 建议: 改为在各 action 内 `useLogStore.getState().log(...)` 调用。

#### [仅供参考] T5-6: `useSessionStore.ts` 魔法数字 `86400000` 多处硬编码
- 文件: `frontend/src/stores/useSessionStore.ts:21-22,35,43`
- 类别: 不一致（违反 no-magic-numbers）
- 稳定性: 稳定区
- 建议: 提取 `const MS_PER_DAY = 24 * 60 * 60 * 1000;` 命名常量。

---

### T6 - 硬件工作台

#### [问题] T6-1: `useWorkbenchBridge.ts` 含已知 workaround 注释，T1 未跟进
- 文件: `frontend/src/stores/useWorkbenchBridge.ts:100-104`
- 类别: 不一致
- 稳定性: ⚠️ 活跃区待二次扫描（commit `48a6e5a`）
- 现象: `resetOverrideIfNewCallId` 函数体内有注释「Task 7 did not add resetWorkbenchOverride setter; setState directly. Workaround for missing setter — flagged to T1.」直接 `useAppStore.setState({ workbenchUserOverride: false })` 绕过 setter。T6 在等 T1 加 setter 但一直没加。
- 建议: T1 在 `useAppStore` 加 `resetWorkbenchOverride: () => void` action，T6 改成调用它；或确认当前 setState 直接写法可接受，删除 workaround 注释。

---

### 跨线程公共区

#### [必须修复] 公共-1: `crud.py` settings 白名单仍含 `sandboxEnabled` / `sandboxImage` 死键
- 文件: `backend/app/api/crud.py:292`
- 类别: 引用断裂（残留死代码）
- 稳定性: 稳定区
- 现象: `ALLOWED_SETTINGS_KEYS` 集合仍保留 `"sandboxEnabled", "sandboxImage"`。Docker sandbox 删除后前端 `useSettingsStore.ts` 已无此配置（grep 0 命中），但后端白名单仍允许前端 PUT 这两个键入库，写入永远不会被读取的脏数据。
- 建议: 从 `ALLOWED_SETTINGS_KEYS` 移除这两行。

#### [必须修复] 公共-2: `settings.py` 默认 `host=0.0.0.0` 违反安全立场
- 文件: `backend/src/config/settings.py:48`
- 类别: 不一致
- 稳定性: 稳定区
- 现象 / 建议: 见阻断 3

#### [必须修复] 公共-3: `api-contract.md` §4 接口目录缺失大量已实现路由
- 文件: `docs/api-contract.md:303-338`
- 类别: 引用断裂（文档说无但代码有）
- 稳定性: 稳定区
- 现象: §4 接口目录仅列 30 个端点，但 `backend/app/api/` 实际有 40 个 `@router` 装饰器端点。完全缺失的路由：
  - `POST /api/agent-sandbox/resume`（chat_routes.py:370）
  - `POST /api/wiring/extract`（wiring_extract.py:38）
  - `POST /api/search`（search_routes.py）
  - `POST/GET /api/feedback`（feedback_routes.py）
  - `POST/GET/DELETE /api/auth/store-key` / `/api/auth/keys` / `/api/auth/keys/{provider}`（auth.py）
  - `/api/mcp/servers/*` 全部 6 个端点（mcp_routes.py）
  - `PATCH /api/kb/collections/{kb_id}/rename`（kb_routes.py:832）
  - `PATCH /api/kb/collections/{kb_id}/config`（kb_routes.py:876）
  - `GET /api/kb/documents/{doc_id}/chunks`（kb_routes.py:915）
  - `GET /api/kb/chunks/{small_chunk_id}`（kb_routes.py:1022）
  - `POST /api/kb/{kb_id}/export`（kb_routes.py:1159）
  - `POST /api/kb/{kb_id}/import`（kb_routes.py:1186）
  - `GET /api/tools`（tool_routes.py:55）
  - `GET /api/token-usage/stats`（chat_routes.py:471）
- 建议: 按 AGENTS.md「未写进文档的接口默认视为不存在」铁律，上述路由要么补进契约要么从代码移除；优先补 auth / mcp / feedback / search / agent-sandbox-resume 等已联调路由。

#### [必须修复] 公共-4: `architecture-map.md` 整体未同步 2026-06-30 沙箱移除（9 处残留）
- 文件: `docs/architecture-map.md:66-70, 232-259, 441, 790, 858-862, 981-988, 1020, 1061-1062, 1142`
- 类别: 不一致
- 稳定性: 稳定区
- 现象: 文件头部 `Last reviewed: 2026-06-29`，恰在沙箱删除之前。9 处残留：
  - L66-70 架构图仍画 Docker 沙箱块
  - L79 "Docker = 试菜区"类比
  - L232-259「能力 4：代码沙箱」整节描述为现行功能含 5 语言镜像表 + 安全限制 + 引用 `sandbox_routes.py` / `executor.py`
  - L441 "沙箱用 Docker 跑用户代码"
  - L790 设置白名单含 `sandboxEnabled` / `sandboxImage`
  - L858-862 §8 API 表列 `/api/sandbox/execute` 与 `/api/sandbox/status` 为现行路由（无 DEPRECATED）
  - L981-988 沙箱限制表引用 `executor.py:37/38/39/98`
  - L1020 文件目录列 `sandbox_routes.py`
  - L1061-1062 列 `sandbox/executor.py`
  - L1142 导航提示「想理解沙箱 → sandbox_routes.py → executor.py」
- 建议: 触发 AGENTS.md「项目全景图维护」条件，必须同步：删除 / 改写「能力 4」章节、删除 §8 沙箱 API 表、文件目录删除 sandbox 相关行、设置白名单删除 `sandboxEnabled` / `sandboxImage`、`Last reviewed` 更新为 2026-06-30，并同步桌面副本 `C:\Users\奶茶丸\Desktop\agent-architecture-map.md`。

#### [建议修改] 公共-5: `api-contract.md` §2.14 错误码表残留 `SANDBOX_UNAVAILABLE`
- 文件: `docs/api-contract.md:231`
- 类别: 过时注释
- 稳定性: 稳定区
- 现象: §2.14 仍列 `SANDBOX_UNAVAILABLE | 503 | 沙箱不可用（Docker 未安装/未运行）`，但 §5.21-5.23 sandbox 接口已正确标 DEPRECATED，sandbox_routes.py 与 backend/src/sandbox/ 已删除。该错误码对应代码路径已不存在。
- 建议: §2.14 同步标注 `SANDBOX_UNAVAILABLE` 为 DEPRECATED 或从错误码表移除。

#### [建议修改] 公共-6: `architecture-map.md` 前端 Store 数量与实际不符（8 vs 10）
- 文件: `docs/architecture-map.md:717, 1087`
- 类别: 不一致
- 稳定性: 稳定区
- 现象: §五标题「前端 8 个 Store」与 L1087 文件目录「8 个 Zustand store」均标 8 个，实际 `frontend/src/stores/` 有 10 个 store：useAppStore / useBookmarkStore / useChatStore / useKnowledgeStore / useLogStore / useSerialStore / useSessionStore / useSettingsStore / useWiringStore / useWorkbenchBridge。架构图 8 个 Store 框图缺 useWiringStore.ts 与 useWorkbenchBridge.ts。
- 建议: §五补 useWiringStore（接线图组件 / 连线状态）与 useWorkbenchBridge（workbench↔chat 桥接）两个 store 框；标题与文件目录改「10 个 Store」。

#### [建议修改] 公共-7: `architecture-map.md` §七 API 路由总表组成错误（含已删 sandbox、缺 wiring_extract）
- 文件: `docs/architecture-map.md:794, 858-862, 1007-1031`
- 类别: 不一致
- 稳定性: 稳定区
- 现象: §七标题「11 个模块 60+ 端点」，11 个分节中 §8 沙箱（sandbox_routes.py）已被删除，同时 `main.py:33,307` 注册的 `wiring_extract_router`（`wiring_extract.py`，`POST /api/wiring/extract`）未在 §七列出。main.py 实际 include 11 个 router（不含 sandbox），数量凑巧 11 但组成错位：多 1 个 sandbox、少 1 个 wiring_extract。L1013 文件目录注释也写「11 个路由模块」但 L1020 仍列 `sandbox_routes.py`。
- 建议: §七删除 §8 沙箱节，新增 wiring_extract 节（或合并入硬件节），文件目录删 `sandbox_routes.py` 行、补 `wiring_extract.py` 行。

#### [建议修改] 公共-8: `thread-map.md` 完全未提及 Trae 6 线程系统
- 文件: `docs/thread-map.md:1-717`
- 类别: 不一致
- 稳定性: 稳定区
- 现象: `thread-map.md` 标题「Hardware RAG Agent — Codex 线程拆分备忘录」，全文 717 行只描述 Codex 8 线程（00-08），Grep "Trae|T1|T2|T3|T4|T5|T6" 零匹配。AGENTS.md 2026-06-30 新增「Trae 6 线程系统」章节（T1-T6 + 文件边界规则 + 与 Codex 线程映射 T2≈05-agent / T3≈03-knowledge / T5≈02-chat+04-session / T6≈07-hardware / T4≈08-infra），`thread-map.md` 作为 AGENTS.md「开工必读」引用的线程归属文件却未同步。
- 建议: `thread-map.md` 头部加一节说明「Trae 6 线程系统（T1-T6）见 AGENTS.md 同名章节」，并补 T1-T6 与 Codex 00-08 的映射表；或明确声明 `thread-map.md` 只维护 Codex 8 线程长期归属，Trae 6 线程为短期冲刺由 AGENTS.md 单独维护。

#### [建议修改] 公共-9: `thread-map.md` 仍把 06-sandbox 列为活跃线程
- 文件: `docs/thread-map.md:429-507`
- 类别: 过时注释
- 稳定性: 稳定区
- 现象: L429-507 详述「06-sandbox — 沙箱与权限线程」负责范围 / 细颗粒模块（06A-06E）/ 关键文件 / 完成标准，但 `completed.md` 2026-06-30 明确「原 06-sandbox 线程已废弃，替代方案为 Agent run_command 工具」。总线程图 L24 也仍列 06 sandbox。
- 建议: `thread-map.md` 06-sandbox 节顶部加大字 DEPRECATED 标记 + 指向 `completed.md` 沙箱移除记录的链接；总线程图 06 行加 "(已废弃 2026-06-30)" 后缀。

#### [建议修改] 公共-10: `06-sandbox-scan.md` 整文档过时需归档
- 文件: `docs/review/06-sandbox-scan.md:1-50+`
- 类别: 过时注释（文档残留）
- 稳定性: 稳定区
- 现象: 整份扫描报告基于已删除的 `backend/src/sandbox/executor.py` 与 `backend/app/api/sandbox_routes.py`，所有 P1/P2 发现均已失效。头部「扫描时间 2026-06-25」早于 sandbox 删除日 2026-06-30。
- 建议: 移动到 `docs/archive/review-2026-06-25/06-sandbox-scan.md`，或在文件头加 `> **状态：已过时**（sandbox 模块已于 2026-06-30 删除）` 标注。

#### [建议修改] 公共-11: 其他文档残留 sandbox 引用（共 5 处）
- 文件（多处）:
  - `docs/completed.md:439,479` —— 仍记录 `backend/app/api/sandbox_routes.py` 为已完成文件
  - `docs/threads/06-sandbox.md` —— 整个线程文档过时
  - `docs/design/specs/2026-06-30-sandbox-frontend-design.md` —— sandbox 前端设计稿过时
  - `docs/todos/06-sandbox.md` —— TODO 文件过时
  - `docs/issue-tracker.md` —— 仍含 06-sandbox 线程条目
- 类别: 过时注释
- 稳定性: 稳定区
- 建议: T4 部署文档线程统一清理：sandbox 相关线程文档 / 设计稿 / TODO 全部加 DEPRECATED 头部或移到 `docs/archive/`。

#### [建议修改] 公共-12: `kb_manager.py` 文件 1037+ 行严重超标
- 文件: `backend/src/rag/kb_manager.py:1-1037`
- 类别: 不一致（违反代码规范）
- 稳定性: 稳定区
- 现象: 文件 1037+ 行（含 `KnowledgeBaseManager` 类 700+ 行 god class），严重超 AGENTS.md「max-lines ≤ 300」规则。
- 建议: 按 BM25Index / KnowledgeBaseManager / RRF fusion 拆分为 3 个模块。demo 后处理。

#### [建议修改] 公共-13: `chat_helpers.py` 多个函数严重超长
- 文件: `backend/app/api/chat_helpers.py:156-277, 277-385, 46-81, 399-455`
- 类别: 不一致（违反代码规范）
- 稳定性: 稳定区
- 现象: 文件 431 行（超 300），`_rewrite_query_for_rag` ~121 行、`_run_rag_retrieval` ~108 行、`_process_attachments` ~35 行、`_build_source_event` ~56 行，全部远超 max-lines-per-function ≤ 10 规则。
- 建议: 按职责拆分子函数（query 改写 / 检索执行 / source 格式化 / token 记录各自独立）。

#### [建议修改] 公共-14: `serial_monitor` WebSocket 函数 ~105 行
- 文件: `backend/app/api/tool_routes.py:76-181`
- 类别: 不一致（违反代码规范）
- 稳定性: 稳定区
- 现象: `serial_monitor` 函数体 ~105 行，含串口打开 / 读循环 / 心跳 / 写循环 / DTR / RTS / 异常处理全部塞在一个函数里。
- 建议: 拆为 `_open_serial` / `_serial_read_loop` / `_serial_write_loop` / `_heartbeat` 子函数。

#### [建议修改] 公共-15: `extract_attachment_text` 函数 ~63 行
- 文件: `backend/app/api/attachments.py:14-77`
- 类别: 不一致（违反代码规范）
- 稳定性: 稳定区
- 现象: 单函数处理 6 种附件类型（PDF/XLSX/CSV/JSON/HTML/文本），每类 try-except，~63 行。
- 建议: 用 dispatch 字典 `{ext: parser_fn}` 拆分。

#### [建议修改] 公共-16: `mcp_routes.py` 使用 pydantic v2 已弃用的 `.dict()`
- 文件: `backend/app/api/mcp_routes.py:20`
- 类别: 不一致（API 弃用）
- 稳定性: 稳定区
- 现象: `manager.register_config(config.id, config.dict())` 使用 `.dict()`，pydantic 2.9 已弃用，会触发 warning。
- 建议: 改为 `config.model_dump()`。

#### [建议修改] 公共-17: `feedback_routes.py` rating 未校验范围
- 文件: `backend/app/api/feedback_routes.py:13`
- 类别: 不一致（缺校验）
- 稳定性: 稳定区
- 现象: `rating: int` 注释写 `1=👍, -1=👎`，但无校验，用户可传任意 int 入库。
- 建议: 改 `rating: int = Field(..., ge=-1, le=1)` 或加 Literal。

#### [建议修改] 公共-18: `auth.py` `keys_store.json` 未限制文件权限
- 文件: `backend/app/api/auth.py:39-41`
- 类别: 不一致（安全实践不一致）
- 稳定性: 稳定区
- 现象: `_save_store` 写入 `keys_store.json`（含加密后的 API Key）未做 chmod，但同文件 `_get_fernet` 对 `.enc_key` 做了 `chmod 0o600`。两处安全实践不一致。
- 建议: `_save_store` 写入后同样 `os.chmod(STORE_PATH, 0o600)`。

#### [仅供参考] 公共-19: `.gitignore` 残留 `sandbox_workspace/` 条目
- 文件: `backend/.gitignore:54`
- 类别: 过时注释（配置残留）
- 稳定性: 稳定区
- 现象: `sandbox_workspace/` 条目对应已删除的 docker sandbox 工作目录，代码侧无引用（`path_guard.py` 用的是 `agent-sandbox` 目录名）。
- 建议: 删除该条目。

#### [仅供参考] 公共-20: `settings.py` 默认 `port=8000` 与 AGENTS.md Development 端口不一致
- 文件: `backend/src/config/settings.py:49`
- 类别: 不一致（文档 / 代码漂移）
- 稳定性: 稳定区
- 现象: 默认 `port=8000`，但 AGENTS.md Development 章节示例用 `--port 58080`，`architecture-map.md` 也以 58080 为准。
- 建议: 默认值改 58080，或在文档里明确「默认 8000，示例用 58080」。

#### [仅供参考] 公共-21: `pitfalls.md` 中 `chat_routes.py` L155 行号偏差 8 行
- 文件: `docs/pitfalls.md:29`
- 类别: 引用断裂
- 稳定性: 稳定区
- 现象: 第 2 条 pitfalls「Agent 路径未触发」修复方式写「chat_routes.py L155 把 model 解析为 `payload.model or header_model or settings.llm_model`」，实际 chat_routes.py:163 才是该解析行，L155 实为 `header_key = request.headers.get("x-api-key")`。偏差 8 行。
- 建议: 把 L155 改为 L163；pitfalls 行号类引用本身只作定位辅助，影响低，但下次踩坑对照时会找错位置。

---

## 总结

### 整体评价

代码主体功能完整、Agent 路径已正确接入，但 **T5 在 useChatStore 引入未定义变量** + **T2 tool_router 调用签名错位** 两个运行时崩溃风险点必须在 demo 前修复；**T1 公共区文档（architecture-map / api-contract / thread-map）整体落后于 2026-06-30 沙箱移除**，需触发「项目全景图维护」同步更新；**多个稳定区文件超 300 行 / 函数超 10 行** 违反 AGENTS.md 代码规范，但属技术债，不阻断 demo，建议 demo 后批量重构。

### 数量统计

| 优先级 | 数量 | 说明 |
|--------|------|------|
| [必须修复] | 6 | 3 个阻断 demo（顶部置顶）+ 3 个文档严重漂移 |
| [建议修改] | 17 | 11 个文档同步 + 6 个代码规范违反 |
| [仅供参考] | 7 | 命名优化 / 行号偏差 / 魔法数字 |
| [问题] | 2 | 需作者解释意图（agent-sandbox/resume 命名 / useWorkbenchBridge workaround） |
| **合计** | **32** | — |

### 建议修复顺序（按 demo 优先级）

**P0 — 立即修复（demo 前必须，6.30 当天）**：
1. T5 修 `useChatStore.ts` permissionMode/toolKeys 未定义（阻断 1）— 影响聊天主流程
2. T2 修 `tool_router.py` WiringTool 缺 title 参数（阻断 2）— 影响 non-Agent 路径
3. T1 修 `settings.py` host=0.0.0.0 → 127.0.0.1（阻断 3）— 安全立场
4. 公共-1 修 `crud.py` sandbox 白名单残留 — 死代码清理
5. 公共-3 补 `api-contract.md` §4 缺失路由 — T4 写文档依赖
6. 公共-4 同步 `architecture-map.md` 9 处 sandbox 残留 — 触发全景图维护条件

**P1 — demo 冲刺中修复（7.1-7.10）**：
7. T2 确认 `/api/agent-sandbox/resume` 是否改名或删除（问题 T2-2）
8. T6 处理 `useWorkbenchBridge.ts` workaround（问题 T6-1）
9. 公共-5/6/7/8/9/10/11 批量清理 sandbox 相关文档残留（派给 T4）
10. 公共-16/17/18 修 mcp `.dict()` / feedback rating / keys_store chmod

**P2 — demo 后批量重构（7.16+）**：
11. T3 拆分三个 chunker（T3-1，但 AGENTS.md 已豁免核心算法文件，仅记 TODO）
12. T5 拆分 useChatStore / ChatArea / InputBar / useSessionStore / useKnowledgeStore
13. 公共区拆分 kb_manager / chat_helpers / serial_monitor / extract_attachment_text
14. 公共-21 修 pitfalls.md 行号偏差

### 二次扫描触发机制

下列区域已标注「⚠️ 活跃区待二次扫描（commit `48a6e5a`）」，T1 在对应线程宣布该区域稳定后重新触发扫描：
- T2 边界：8 项发现（含 1 必须修复）→ 待 T2 宣布 Agent 全链路稳定后重扫
- T3 边界：1 项发现 → 待 T3 宣布 RAG 重索引稳定后重扫
- T5 边界：6 项发现（含 1 必须修复）→ 待 T5 宣布前端体验稳定后重扫
- T6 边界：1 项发现 → 待 T6 宣布硬件工作台稳定后重扫

二次扫描命令：`plur inject "重新扫描 T2/T3/T5/T6 活跃区一致性" --fast --json`，对比本次报告 commit `48a6e5a` 之后的 diff。

---

## 末尾声明

- 扫描过程未修改任何代码文件
- 扫描过程未修改任何现有文档（仅新建本报告）
- 本报告归档到 `docs/reports/`（一次性报告目录，不长期维护）
- 后续修复由 T1 按上述建议顺序派发给对应 Trae 线程
