# Tasks

- [x] Task 1: 后端 — session 表加 context_window 字段（models.py + api/crud.py + database.py 迁移）
  - [ ] SubTask 1.1: `backend/app/db/models.py` Session 表加 `context_window: int = 262144` 字段
  - [ ] SubTask 1.2: `backend/app/db/crud.py` 创建/更新 session 时支持 context_window 读写
  - [ ] SubTask 1.3: 加数据库迁移（ALTER TABLE sessions ADD COLUMN context_window INTEGER DEFAULT 262144）

- [ ] Task 2: 后端 — autocompact 阈值动态化 + snip 兜底 + 摘要 prompt 改进
  - [ ] SubTask 2.1: `backend/src/agent/compact/autocompact.py` 的 `should_autocompact` 改为接收 `threshold` 参数（由调用方传入 `context_window × 0.8`）
  - [ ] SubTask 2.2: `autocompact.py` 的 `_truncate_with_notice` 改为带存在标记的 snip：`[已删除 N 条早期消息]`（N = 被删除的条数）
  - [ ] SubTask 2.3: `autocompact.py` 的 `autocompact_messages` 接收 `context_window` 参数，内部计算阈值
  - [ ] SubTask 2.4: `autocompact.py` 的 `_SUMMARIZER_PROMPT` 从"200 字 + 4 项"改为"500 字 + 7 项"（用户意图、技术决策、芯片/接线/文件路径、代码片段、错误及解决、当前状态、待办事项）

- [x] Task 3: 后端 — context_guard 支持用户配置窗口（compute_token_limit 加 override 参数 + sse_helpers 读 session context_window）
  - [ ] SubTask 3.1: `backend/src/agent/context_guard.py` 的 `compute_token_limit` 加可选 `context_window_override` 参数，优先使用
  - [ ] SubTask 3.2: `backend/src/agent/sse_helpers.py` 的 `init_stream_state` 从 session 读取 context_window，存入 state

- [x] Task 4: 后端 — sse_adapter 接入压缩检查点（7 个新函数 + _should_compact + _maybe_compact + 压缩后 update_state）
  - [ ] SubTask 4.1: `backend/src/agent/sse_adapter.py` 在 `_collect_node_events` 处理完 ToolMessage 后，调用压缩检查函数
  - [ ] 4.1 说明：检查 `_CUMULATIVE_TOKENS` 是否超 `state['token_limit']`，超了发 SSE `context_compressing` 事件 → 调 `autocompact_messages` → 更新 agent state → 重置 token 计数器
  - [ ] SubTask 4.2: 新增 `_check_and_compact(state, agent, config)` 函数：检查 → 压缩 → 重置计数器，返回是否压缩了
  - [ ] SubTask 4.3: 在 `app/api/sse.py` 注册 `context_compressing` SSE 事件类型

- [ ] Task 5: 后端 — chat_routes 发送前历史检查
  - [ ] SubTask 5.1: `backend/app/api/chat_routes.py` 的 `_run_agent_stream` 在调用 agent.astream 前，检查当前 session 历史 token 是否超窗口 80%
  - [ ] 5.1 说明：从 checkpointer 读取历史 messages → 估算 token → 超了发 SSE `context_compressing` → 调 autocompact → 更新 state → 再调 astream
  - [ ] SubTask 5.2: 从 ChatRequest 或 session 读取 context_window，传给 agent config

- [x] Task 6: 后端 — session API 支持 context_window（PATCH /api/sessions/{id} + _apply_session_update + 确认 GET 返回 context_window）
  - [ ] SubTask 6.1: `backend/app/api/session_routes.py` 的创建 session 接口接收 context_window 参数
  - [ ] SubTask 6.2: 新增 PATCH `/api/sessions/{id}` 接口更新 context_window
  - [ ] SubTask 6.3: GET `/api/sessions` 返回 context_window 字段

- [ ] Task 7: 前端 — session 级 contextWindow 状态
  - [ ] SubTask 7.1: `frontend/src/stores/useSessionStore.ts` 加 `contextWindow: number` 字段，切换 session 时读取
  - [ ] SubTask 7.2: `frontend/src/api/client.ts` 的 session 相关接口传递 context_window

- [x] Task 8: 前端 — 设置页上下文窗口下拉框（API 标签页加 select + 说明文字 + setContextWindow 调用）
  - [ ] SubTask 8.1: `frontend/src/components/settings/SettingsPage.tsx` 在 API 标签页加上下文窗口下拉框（256K / 1M）
  - [ ] 8.1 说明：下拉框下方显示说明文字"上下文窗口决定 AI 能记住的对话长度。256K 适合大多数场景，1M 适合超长对话。切换到更小窗口时，如果当前对话已超出，系统会自动压缩历史。"
  - [ ] SubTask 8.2: 切换时调 PATCH `/api/sessions/{currentSessionId}` 更新 context_window
  - [ ] SubTask 8.3: 加 i18n 文案（zh.ts / en.ts）

- [x] Task 9: 前端 — SSE context_compressing 事件处理（isCompressing state + ChatArea 横幅 + chat.css 样式）
  - [ ] SubTask 9.1: `frontend/src/stores/useChatStore.ts` SSE 事件处理加 `context_compressing` 分支
  - [ ] 9.1 说明：收到事件时在对话区显示"正在压缩上下文..."加载状态，压缩完后自动消失（下一条 text 事件到达时清除）
  - [ ] SubTask 9.2: UI 组件加压缩中状态展示（可选：在聊天区顶部显示横幅或 thinking 卡片）

- [x] Task 10: 测试验证 + 三个 subagent 审查（完成度 11/11 + 修复 3 处缺陷 + 端到端测试通过）
  - [ ] SubTask 10.1: 后端启动无报错，`/api/health` 正常
  - [ ] SubTask 10.2: 前端 TypeScript 编译通过（`npx tsc --noEmit`）
  - [ ] SubTask 10.3: 设置页能切换 256K/1M，切换后刷新值保持
  - [ ] SubTask 10.4: 长对话触发压缩时前端显示"正在压缩上下文..."
  - [ ] SubTask 10.5: 开 3 个 subagent 并行审查：
    - SubAgent A：完成度检测 — 对照 spec 逐条核验所有 Requirement 是否实现
    - SubAgent B：缺陷检测维修 — 检查代码缺陷并修复
    - SubAgent C：端到端测试 — 用测试 API（https://9router.zxyzx.bbroot.com/v1, key: sk-ff9714180887bef5-twgqps-5fbe7399, 模型: Text）做真实对话测试，验证压缩功能

# 测试 API 配置
- Base URL: https://9router.zxyzx.bbroot.com/v1
- API Key: sk-ff9714180887bef5-twgqps-5fbe7399
- 模型: Text

# Task Dependencies
- [Task 4] depends on [Task 2] + [Task 3]
- [Task 5] depends on [Task 2] + [Task 3]
- [Task 8] depends on [Task 6] + [Task 7]
- [Task 9] depends on [Task 4]（需要后端 SSE 事件先就绪）
- [Task 10] depends on all others
