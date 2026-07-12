# 代码审查第二批整改 Spec

## Why

代码审查报告（2026-07-04）指出 30 个「必须修复」问题，经三 Sub-Agent 并行验证：25 条完全属实、3 条部分真、2 条假。本 spec 覆盖用户选定的 10 项修复（编号 3/4/5/6/8/9/10/11/12/14），集中在安全鉴权、静默吞异常、死代码清理、路径穿越防护四类。修复后系统在安全性和可维护性上明显提升，对正常用户基本无感。

回滚点：`0f9d6a9`（`git reset --hard 0f9d6a9` 可回到整改前状态）。

## What Changes

### 安全 / 鉴权
- WebSocket `/api/monitor/{port}` 加 `Depends(current_user)` 鉴权（**BREAKING**：外部脚本直连此 WS 需带 token）
- WebSocket 异常分支（`SerialException` / generic `Exception`）补 `await websocket.close(...)`，防止僵尸连接
- `path_guard.DENY_PATTERNS` 补 `Windows/` / `System32/` / `$Recycle.Bin/` / `Boot/bootmgr`
- `path_guard` 的 `..` 检查从子串匹配改为精确匹配（只拦路径组件 `..`，不误杀 `notes..draft.txt`）

### RAG / 检索稳定性
- `reranker.py` 永久禁用改为冷却恢复（5 分钟冷却后自动重试）
- `vector_store.search` 接入 `_with_retry`，ChromaDB 偶发失败自动重试 3 次

### 静默吞异常补日志
- `client.py` L286/L353 `except Exception: pass` 改为 `logger.warning`
- `multimodal_chunker.py` L729-732 表格 `to_markdown()` 失败的 `continue` 补 `logger.warning`（含页码）
- `chat_routes.py` L268 `CancelledError` pass 保留（finally 哨兵已防挂死），但补 `logger.debug` 记录取消

### 死代码删除
- 删 `backend/src/agent/tools/wrappers.py`（已迁移到 `groups/hardware/` + `groups/retrieval/`）
- 删 `backend/src/agent/tools/workbench_tools.py`（已迁移到 `groups/workbench/`）
- 删 `backend/src/agent/tools/web_search.py`（live 文件在 `groups/retrieval/web_search.py`）

### 导入修复
- `autocompact.py` 修正 `from src.agent.context_guard import estimate_tokens`（该符号不存在）→ 改为 `from src.llm.client import LLMClient` 后用 `LLMClient._estimate_tokens`

### .gitignore 补全
- 根 `.gitignore` 加 `*.key` / `*.pem` / `*credentials*` / `.env` / `settings.json` / `stdout.txt` / `stderr.txt` / `backend/_*.py`

### 前端清理
- `useSessionStore.ts` L62 `s: any` → `s: unknown` + 类型守卫
- `SafetyPane.tsx` L137 直接改 `el.style.outline` → 改用 state + className 切换高亮
- `_SUB_SPLIT_SEPARATORS` / `_INLINE_CODE_RE` 抽到 `chunking/_constants.py` 共享
- `build_routes.py` `_upload_locks` 删除，复用 `app/api/locks.py:get_port_lock`
- `DiagnosticTool` / `ListSkillsTool` / `CallSkillTool` 决定命运：注册到 agent_factory（如不注册则删除，本 spec 选择删除——未在生产使用）

## Impact

- **Affected specs**: 无（纯内部修复，不改 API 契约）
- **Affected code**:
  - 后端：`tool_routes.py` / `path_guard.py` / `reranker.py` / `vector_store.py` / `client.py` / `multimodal_chunker.py` / `chat_routes.py` / `autocompact.py` / `build_routes.py` / `agent_factory.py` / `locks.py`
  - 后端删除：`tools/wrappers.py` / `tools/workbench_tools.py` / `tools/web_search.py` / `tools/groups/execution/diagnostic.py` / `tools/groups/execution/skills.py`（如选删除）
  - 前端：`useSessionStore.ts` / `SafetyPane.tsx`
  - 配置：`.gitignore`
  - 新增：`backend/src/rag/chunking/_constants.py`（共享常量）

## ADDED Requirements

### Requirement: WebSocket 鉴权
WebSocket `/api/monitor/{port}` SHALL 要求 `Depends(current_user)` 鉴权，未带有效 token 的连接 SHALL 被拒绝（close code 4401）。

#### Scenario: 未鉴权连接
- **WHEN** 客户端不带 token 直连 `ws://127.0.0.1:58080/api/monitor/COM3`
- **THEN** WebSocket 立即关闭，close code 4401，reason "未授权"

#### Scenario: 已鉴权连接
- **WHEN** 前端带有效 token 连接
- **THEN** 正常进入串口监视流程

### Requirement: WebSocket 异常关闭
串口监视器异常分支（`SerialException` / generic `Exception`）SHALL 调用 `await websocket.close(code=4xxx, reason=...)` 后再 return，防止僵尸连接。

#### Scenario: 串口打开失败
- **WHEN** 串口打开抛 `SerialException`
- **THEN** 发送 error 消息后 `websocket.close(code=4003)`，前端能收到关闭事件并自动重连或提示

### Requirement: Reranker 冷却恢复
`reranker.py` 失败后 SHALL 进入 5 分钟冷却（而非永久禁用），冷却期满后下次调用自动重试。

#### Scenario: 一次失败后冷却
- **WHEN** reranker predict 抛异常
- **THEN** 标记 `_RERANKER_PREDICT_FAILED = True` + 记录失败时间，5 分钟内直接返回 `[(i, 0.0)]`
- **AND** 5 分钟后下次调用自动重试，成功则清除标志

### Requirement: Vector Store Search 重试
`vector_store.search` SHALL 接入 `_with_retry`，ChromaDB 偶发失败自动重试 3 次（指数退避）。

#### Scenario: ChromaDB 偶发失败
- **WHEN** `similarity_search_with_relevance_scores` 抛异常
- **THEN** 按 `_with_retry` 策略重试 3 次，全部失败才返回空列表 + `logger.exception`

### Requirement: Path Guard Windows 路径
`path_guard.DENY_PATTERNS` SHALL 包含 `Windows/` / `System32/` / `$Recycle.Bin/` / `Boot/bootmgr`。

#### Scenario: 拦截系统目录
- **WHEN** Agent 工具尝试读 `C:\Windows\System32\config\SAM`
- **THEN** path_guard 拒绝，返回 `False, "denied path pattern"`

### Requirement: Path Guard 精确匹配 ..
`path_guard` SHALL 只拦截作为路径组件的 `..`（如 `../` 或 `..\`），不误杀含 `..` 的合法文件名（如 `notes..draft.txt`）。

#### Scenario: 合法文件名不误杀
- **WHEN** Agent 工具读 `notes..draft.txt`
- **THEN** 路径检查通过，正常读取

#### Scenario: 路径穿越拦截
- **WHEN** Agent 工具读 `../../../etc/passwd`
- **THEN** path_guard 拒绝

## MODIFIED Requirements

### Requirement: 静默吞异常补日志
以下位置 SHALL 不再静默吞异常，改为 `logger.warning` 或 `logger.debug` 记录：
- `client.py` L286/L353 `except Exception: pass`（summarize 失败）→ `logger.warning`
- `multimodal_chunker.py` L729-732 表格 `to_markdown()` 失败 `continue` → `logger.warning`（含页码）
- `chat_routes.py` L268 `CancelledError` pass → `logger.debug`（保留 pass，finally 哨兵已防挂死）

### Requirement: .gitignore 补全
根 `.gitignore` SHALL 包含：`*.key` / `*.pem` / `*credentials*` / `.env` / `settings.json` / `stdout.txt` / `stderr.txt` / `backend/_*.py`。

### Requirement: 共享常量
`_SUB_SPLIT_SEPARATORS` / `_INLINE_CODE_RE` SHALL 抽到 `backend/src/rag/chunking/_constants.py`，三个 chunker（hybrid / agent / multimodal）从该模块导入。

### Requirement: 复用锁
`build_routes.py` SHALL 删除自造的 `_upload_locks`，复用 `app/api/locks.py:get_port_lock`。

## REMOVED Requirements

### Requirement: 死代码副本
**Reason**: `tools/wrappers.py` / `tools/workbench_tools.py` / `tools/web_search.py` 已迁移到 `groups/` 子目录，无人 import，绕过 ToolRouter 审计管线。
**Migration**: 无（已迁移的 live 文件在 `groups/hardware/` / `groups/retrieval/` / `groups/workbench/`）。

### Requirement: 未注册工具
**Reason**: `DiagnosticTool` / `ListSkillsTool` / `CallSkillTool` 已实现但从未注册到 agent_factory，生产从未使用。
**Migration**: 无（如未来需要可从 git 历史恢复）。

### Requirement: 前端 Mock 残留（部分）
**Reason**: `useSessionStore.ts` L62 `s: any` 违反 strict 模式；`SafetyPane.tsx` L137 直接改 DOM 绕过 React。
**Migration**: `useSessionStore` 改 `unknown` + 类型守卫；`SafetyPane` 改 state + className。
