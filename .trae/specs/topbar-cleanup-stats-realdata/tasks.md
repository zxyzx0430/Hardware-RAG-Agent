# Tasks

## 阶段 1：TopBar 精简（前端）

- [x] Task 1: 删除 TopBar 快照和来源按钮
  - [x] SubTask 1.1: 读取 `frontend/src/components/topbar/TopBar.tsx` 当前实现
  - [x] SubTask 1.2: 删除 `snapshotToggleBtn` 按钮组（含 📷 图标和 `snapshotPanelOpen` 状态引用）
  - [x] SubTask 1.3: 删除 `sourceToggleBtn` 按钮组（含 SVG 图标）
  - [x] SubTask 1.4: 删除因移除按钮而产生的多余 `topbar-divider`
  - [x] SubTask 1.5: 验证：进入聊天页，TopBar 右侧只剩汉堡菜单按钮

- [x] Task 2: 确认汉堡菜单只保留三项
  - [x] SubTask 2.1: 读取 `frontend/src/components/shared/HamburgerMenu.tsx`
  - [x] SubTask 2.2: 确认当前菜单项只有：切换深色模式、导出对话、对话统计
  - [x] SubTask 2.3: 无需删除其他项（文件已符合 spec）
  - [x] SubTask 2.4: 验证：点击汉堡菜单，下拉面板只有三个选项

## 阶段 2：后端接口扩展

- [x] Task 3: 扩展 `/api/token-usage/stats` 支持 session_id
  - [x] SubTask 3.1: 读取 `backend/app/api/chat_routes.py` 中 `token_usage_stats` 函数
  - [x] SubTask 3.2: 在函数签名增加可选参数 `session_id: Optional[str] = None`
  - [x] SubTask 3.3: 在查询 `daily_rows` 和 `model_rows` 时，若 `session_id` 非空则追加 `TokenUsage.session_id == session_id` 过滤
  - [x] SubTask 3.4: 验证无 `session_id` 时行为不变
  - [x] SubTask 3.5: 更新 `docs/api-contract.md` 中 `/api/token-usage/stats` 接口说明

## 阶段 3：对话统计接入真实数据（前端）

- [x] Task 4: StatsPanel 接入后端真实数据
  - [x] SubTask 4.1: 读取 `frontend/src/components/shared/StatsPanel.tsx` 和 `frontend/src/api/client.ts`
  - [x] SubTask 4.2: 在 `useChatStore.ts` 中确认 `activeSessionId` 可直接使用
  - [x] SubTask 4.3: StatsPanel 打开时调用 `apiGet<TokenStats>(`token-usage/stats?session_id=${activeSessionId}`)`
  - [x] SubTask 4.4: 使用后端返回的 `summary.total_input` / `summary.total_output` / `summary.total_tokens` 作为真实 token 数
  - [x] SubTask 4.5: 使用后端返回的 `daily` 绘制 Input/Output 折线图
  - [x] SubTask 4.6: 使用后端返回的 `by_model` 展示模型分组统计
  - [x] SubTask 4.7: 消息数/用户消息数/AI 消息数/来源引用数仍从 `messages` 本地计算
  - [x] SubTask 4.8: 当后端返回空数据或 `summary.total_tokens === 0` 时，显示"暂无真实用量数据..."
  - [x] SubTask 4.9: 当后端请求失败时显示错误提示 + 重试按钮
  - [x] SubTask 4.10: 删除原有的 `estimateTokens` 估算函数和相关提示
  - [x] SubTask 4.11: 验证：有真实 usage 时显示真实数据；无数据时显示空状态提示

## 阶段 4：验证

- [x] Task 5: 全量验证
  - [x] SubTask 5.1: 运行 `cd frontend && npx tsc --noEmit`，确保 0 errors
  - [x] SubTask 5.2: 后端 `/api/token-usage/stats?session_id=xxx` 已验证可按会话过滤
  - [x] SubTask 5.3: 手动验证 TopBar 只剩汉堡菜单
  - [x] SubTask 5.4: 手动验证汉堡菜单只有三项
  - [x] SubTask 5.5: 手动验证对话统计调用真实接口并正确展示/空状态/错误重试

# Task Dependencies

- Task 1 与 Task 2 可并行（互不依赖）
- Task 3 与 Task 1/Task 2 可并行
- Task 4 依赖 Task 3（需要后端接口支持 session_id）
- Task 5 依赖 Task 1/2/4 完成
