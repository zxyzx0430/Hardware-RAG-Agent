# Tasks

- [x] Task 1: TopBar 标题订阅会话 title
  - [x] SubTask 1.1: TopBar.tsx 引入 useSessionStore，按 activeSessionId 查找当前会话 title
  - [x] SubTask 1.2: 保留 messages[0] 派生作为 fallback（旧会话无 title 时）
  - [x] SubTask 1.3: 验证重命名后标题立即更新

- [x] Task 2: TopBar 来源徽章改用消息级 sources
  - [x] SubTask 2.1: TopBar.tsx 改为从 messages 中找最后一条 assistant 消息的 msg.sources
  - [x] SubTask 2.2: 验证切换会话后徽章归零

- [x] Task 3: 书签注入真实会话信息
  - [x] SubTask 3.1: useBookmarkStore.ts 创建 BookmarkEntry 时读取 useChatStore.activeSessionId 和 useSessionStore.sessions
  - [x] SubTask 3.2: 注入真实 sessionTitle 和 sessionId，替换硬编码 "当前对话"
  - [x] SubTask 3.3: 验证 BookmarkPanel 显示正确的所属对话名

- [x] Task 4: StatsPanel model 读取会话级模型
  - [x] SubTask 4.1: StatsPanel.tsx 引入 useSessionStore，按 activeSessionId 查找 session.model
  - [x] SubTask 4.2: fallback 到 useSettingsStore.chatModel
  - [x] SubTask 4.3: 验证不同会话显示不同模型名

- [x] Task 5: useSessionStore 操作失败回滚
  - [x] SubTask 5.1: renameSession 失败时恢复旧 title
  - [x] SubTask 5.2: pinSession 失败时恢复旧 pinned 状态
  - [x] SubTask 5.3: moveSession 失败时恢复旧 project
  - [x] SubTask 5.4: deleteSession 失败时恢复会话到列表
  - [x] SubTask 5.5: 所有失败路径加 console.warn 记录

- [x] Task 6: useChatStore clearMessages 失败回滚
  - [x] SubTask 6.1: clearMessages 的 .catch(() => {}) 改为回滚 + console.warn

- [x] Task 7: ConfirmDialog 移除"永久允许"按钮
  - [x] SubTask 7.1: ConfirmDialog.tsx 移除"永久允许"按钮
  - [x] SubTask 7.2: 只保留"允许本次"/"拒绝"/"停止"三个按钮
  - [x] SubTask 7.3: 移除 footer 中关于"永久允许将记录白名单规则"的文案

- [x] Task 8: SerialPane 演示数据标注
  - [x] SubTask 8.1: seed() 函数注入的每条日志前加 `[DEMO]` 前缀
  - [x] SubTask 8.2: ⟳ 按钮加 title="注入演示数据（非真实串口数据）"

- [x] Task 9: SettingsPage About 链接处理
  - [x] SubTask 9.1: 移除 docs.example.com 和 github.com/example/... 占位链接
  - [x] SubTask 9.2: 改为显示"文档与开源仓库即将上线"

- [x] Task 10: AuditLogPanel 过滤器补全
  - [x] SubTask 10.1: FilterBar 补充 tool_name 输入框
  - [x] SubTask 10.2: 挂载时自动 fetchLogs

- [x] Task 11: 删除 endpoints.ts 死代码
  - [x] SubTask 11.1: 确认全局无 import 引用
  - [x] SubTask 11.2: 删除 frontend/src/api/endpoints.ts

- [x] Task 12: IconNav 装饰按钮处理
  - [x] SubTask 12.1: 4 个 nav-dim 按钮改为 `<div>` 或加 disabled + title="功能开发中"

- [x] Task 13: 类型检查与验证
  - [x] SubTask 13.1: npx tsc --noEmit 通过
  - [x] SubTask 13.2: 浏览器验证重命名→标题更新、切会话→徽章归零、书签显示会话名

# Task Dependencies

- Task 1, 2 可并行（都改 TopBar.tsx，但不同字段）
- Task 3, 4 可并行（不同文件）
- Task 5, 6 可并行（不同 store）
- Task 7, 8, 9, 10 可并行（不同文件）
- Task 11, 12 可并行（不同文件）
- Task 13 依赖所有前置任务完成
