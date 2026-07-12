# 代码审查第三批整改 Spec

## Why

4 个 Sub-Agent 并行审查 70+ 文件，发现 21 个必须修复问题：HITL resume 完全失效、read_file/write_file 缺 path_guard 可读写敏感文件、RAG score_threshold 语义错误导致召回率下降、pio_runner 烧录丢 framework/lib_deps、auth store_key 无鉴权可覆盖已存 Key、feedback 裸 SQL 在 SQLAlchemy 2.0 报错、前端后台 SSE 控制器丢失导致消息损坏。本 spec 覆盖全部 21 个必须修复 + 高优先级建议修改。

## What Changes

### 安全 / 鉴权（3 项）
- `read_file` / `write_file` / `edit_file` 补 `validate_path` 校验（defense-in-depth，不依赖 PermissionClassifier）
- `auth.py POST /api/auth/store-key` 补 `_require_auth`（首次无 Provider 仍免鉴权）
- `auth.py keys_store.json` 加 `threading.Lock` + 原子替换防并发丢更新

### RAG 检索正确性（5 项）
- `score_threshold` 改为只在向量检索阶段过滤，融合后不再二次过滤
- `rrf_fusion` docstring 改为 average（与实现一致）
- `import_kb` 查询加 `kb_id` 过滤，防跨 KB 偷文档
- `reranker fallback` 检测改 `any(s != 0.0 for _, s in reranked)`
- `search()` BM25-only 注释更正

### 后端功能 bug（7 项）
- `chat_routes.py:433` HITL resume 漏 `await` 补上
- `pio_runner.py` 编译超时重复 done 事件：加 `done_sent` 标志
- `pio_runner.py _ensure_upload_port` 透传 `framework` / `lib_deps`
- `feedback_routes.py` 裸 SQL 改 `text()` 或 ORM
- `hardware_routes.py diagnose_code` 初始化 `found: list[str] = []`
- `crud.py create_session` 分支逻辑改为复制到分支点后 break
- `pio_runner.py` 上传超时进程句柄泄漏：finally 中 `proc.wait()`

### 前端 SSE / 状态管理（6 项）
- `useChatStore.ts` 后台 SSE 用 `backgroundSseRequests: Map` 追踪
- `useChatStore.ts setActiveSession` 三元 no-op 修复
- `useChatStore.ts truncateAndResend` 把 `sendMessage` 放进 `apiDelete.then()`
- `useChatStore.ts` `_lastToolCallId` / `_needsParagraphBreak` 按 sessionId 隔离
- `client.ts apiSSE` 加断连检测 + 用户可点重试（不做自动重连，复杂度过高）
- `FlashPane.tsx` 编译/烧录加 AbortController + 停止按钮 + 卸载 abort

### 高优先级建议修改（选 8 项）
- `agent_factory.py` 全局 `_TOOL_REGISTRY` 并发覆盖：改为 per-request 实例
- `autocompact.py` multimodal content 类型收敛
- `sse_adapter.py` finally 块区分 `task.cancelled()`
- `audit_recorder.py` command 参数 value 脱敏
- `run_command.py timeout_ms` 加 `ge=1000` 下限
- `kb_manager.py list_kbs` N+1 查询改 GROUP BY
- `document_processor.py _parse_pymupdf_per_page` 加 try/finally 关闭 PDF
- `auth.py` 过期 session 清理 + `delete_key` 清理关联 session

## Impact

- **Affected code**: 21 个必须修复文件 + 8 个建议修改文件
- **Affected specs**: 无（纯内部修复，不改 API 契约）
- **BREAKING**: 无

## User-Visible Changes（用户能直接感知的变化）

### 严重 bug 修复（用户高频遇到）
1. **HITL 权限确认后 Agent 不再卡死**（Task 5）
   - 之前：用户点"允许"后 Agent 不响应，必须刷新页面
   - 之后：权限确认后 Agent 正常恢复执行
2. **多会话并发不再串 ctx**（Task 11）
   - 之前：A 会话的工具调用可能用 B 会话的 ctx，结果错乱
   - 之后：每个请求独立实例，并发安全
3. **切换会话不再断流 / 串消息**（Task 8）
   - 之前：流式中切换会话，SSE 被错误中断或两个流并发内容混乱
   - 之后：后台流保留 + 切回能继续 + 不冲突

### RAG 检索质量提升
4. **RAG 召回率明显提升**（Task 3）
   - 之前：threshold=0.5 时，vector 0.55 + BM25 0.3 融合 0.425 被错误过滤
   - 之后：只在向量阶段过滤，融合后不再二次过滤，召回更多相关结果

### 编译烧录稳定性
5. **编译超时不再出现重复失败事件**（Task 6）
   - 之前：done 事件发两次，前端 UI 错乱
   - 之后：done 只发一次
6. **烧录不再丢 framework / lib_deps**（Task 6）
   - 之前：espidf 编译后烧录可能用错框架导致失败
   - 之后：framework / lib_deps 正确透传
7. **编译停止按钮能用**（Task 10）
   - 之前：点停止无效
   - 之后：AbortController 立即停止

### 安全升级
8. **bypass 模式不能读敏感文件**（Task 1）
   - 之前：bypassPermissions 模式可读 .env / settings.json / *.key
   - 之后：path_guard 兜底拒绝
9. **覆盖 API Key 要鉴权**（Task 2）
   - 之前：无鉴权可覆盖已存 Key
   - 之后：已有 Provider 时要求鉴权（首次配置仍免鉴权）
10. **并发保存 Key 不再丢更新**（Task 2）
    - 之前：两个请求同时存可能丢一个
    - 之后：threading.Lock + 原子替换

### SSE 体验
11. **SSE 断连有提示可重试**（Task 10）
    - 之前：断连无提示，用户以为还在加载
    - 之后：明确提示"连接断开，点击重试"

## Internal Optimizations（用户感知不到但内部优化）

- path_guard defense-in-depth 兜底
- 原子替换防写丢 + 过期 session 自动清理 + delete_key 清理关联 session
- score_threshold 算法正确性
- import_kb 跨 KB 隔离防偷文档
- reranker fallback 不再误判跳过
- pio_runner 进程句柄不再泄漏
- feedback SQL SQLAlchemy 2.0 兼容
- diagnose_code NameError 修复
- create_session 分支逻辑正确
- 模块级变量 _lastToolCallId / _needsParagraphBreak 按 session 隔离
- sse_adapter finally 区分 task.cancelled()
- audit_recorder command 参数脱敏（API Key 不再泄漏到日志）
- run_command timeout_ms ge=1000 下限校验
- kb_manager list_kbs N+1 改 GROUP BY 性能优化
- document_processor PDF 句柄 try/finally 释放

## Verification Plan

### 单元测试
- 后端：`pytest tests/` 168+ 用例跑通
- 前端：`npx tsc --noEmit` 0 error

### 集成测试
- 后端启动 + 59 路由正常
- 用 9router Text 模型做端到端验证（用户提供：`https://9router.zxyzx.bbroot.com/v1` + key + model Text）

### 回归测试
- 已有测试全部跑通（不引入新 bug）
- 修改文件 python -c 导入正常

### 文档同步
- 修复 bug 后追加 `docs/pitfalls.md`
- 完成后追加 `docs/completed.md`
- 生成全面审查报告给用户

## ADDED Requirements

### Requirement: read_file/write_file/edit_file path_guard
文件操作工具 SHALL 在 `execute` 方法开头调用 `validate_path`，不依赖 PermissionClassifier。

#### Scenario: bypassPermissions 模式读 .env
- **WHEN** LLM 在 bypassPermissions 模式调 `read_file(path=".env")`
- **THEN** path_guard 拒绝，返回 `{"error": "denied path pattern"}`

### Requirement: auth store_key 鉴权
`POST /api/auth/store-key` SHALL 在已有 Provider 时要求鉴权。

#### Scenario: 首次配置
- **WHEN** 无任何 Provider 时调 store-key
- **THEN** 免鉴权，正常存储

#### Scenario: 覆盖已存 Key
- **WHEN** 已有 Provider 时调 store-key
- **THEN** 要求 `_require_auth`，未授权返回 401

### Requirement: RAG score_threshold 语义
`score_threshold` SHALL 只在向量检索阶段过滤（cosine），RRF 融合后不再二次过滤。

#### Scenario: vector cosine 0.55 + BM25 0.3
- **WHEN** threshold=0.5，vector cosine=0.55（通过），BM25 normalized=0.3
- **THEN** 融合分 0.425，不被 threshold 过滤（因为 cosine 已通过）

### Requirement: HITL resume await
`/api/agent-sandbox/resume` SHALL `await _build_agent_for_payload(...)`。

#### Scenario: resume 请求
- **WHEN** 用户在 HITL 确认后点继续
- **THEN** agent 正确构建，resume_agent_after_user 正常执行

### Requirement: pio_runner 烧录透传 framework/lib_deps
`_ensure_upload_port` SHALL 透传 `framework` 和 `lib_deps` 到 platformio.ini。

#### Scenario: espidf 编译后烧录
- **WHEN** 编译用 framework=espidf，烧录时
- **THEN** platformio.ini 仍含 framework=espidf + lib_deps

### Requirement: 前端后台 SSE 管理
`useChatStore` SHALL 用 `backgroundSseRequests: Map<sessionId, AbortController>` 追踪后台 SSE。

#### Scenario: 流式中切换会话
- **WHEN** 会话 A 流式中切到会话 B
- **THEN** A 的 SSE controller 移入 backgroundSseRequests，B 发消息时不冲突

## MODIFIED Requirements

### Requirement: feedback_routes SQL
`feedback_routes.py` SHALL 用 `text()` 包裹裸 SQL 或改用 ORM。

### Requirement: diagnose_code 初始化
`diagnose_code` SHALL 在 if/elif/else 之前初始化 `found: list[str] = []`。

### Requirement: create_session 分支逻辑
`create_session` SHALL 复制到分支点（含）后 break，不跳过之前的消息。

### Requirement: apiSSE 断连处理
`apiSSE` SHALL 在 `onError` 中区分"已收到 done"和"中途断开"，后者提示用户重试。
