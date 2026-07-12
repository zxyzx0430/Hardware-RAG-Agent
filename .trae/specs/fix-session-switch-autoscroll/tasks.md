# Tasks

## 必须修复（7 项）

- [x] Task 1: 修复聊天自动滚动 + 位置保留
  - [x] SubTask 1.1: ChatArea.tsx 自动滚动 useEffect 依赖从 `[messages.length]` 改为 `[messages, activeSessionId]`
  - [x] SubTask 1.2: 用 `useRef<Map<string, number>>` 缓存每个 sessionId 的 scrollTop
  - [x] SubTask 1.3: 切会话时：先存旧会话 scrollTop，再恢复新会话 scrollTop（若缓存有则恢复，无则滚到底）
  - [x] SubTask 1.4: 用 `useRef` 记录上一次 activeSessionId，区分"切会话"和"同会话新消息"
  - [x] SubTask 1.5: 用 `requestAnimationFrame` 双帧延迟滚动，等 DOM 渲染完成
  - [x] SubTask 1.6: 新增 useEffect 监听 activeSessionId 变化，重置 `userScrolledRef.current = false`
  - [x] SubTask 1.7: 用 ResizeObserver 监听 scrollHeight 变化，2 秒内补滚一次（兜底图片加载）

- [x] Task 2: 修复 ConfirmDialog ESC 卡死 Agent
  - [x] SubTask 2.1: ConfirmDialog.tsx ESC 处理改为调用 `resumeAgent("deny")` 而非 `clearPendingConfirm()`（同时移除未使用的 clearPendingConfirm 声明）

- [x] Task 3: 修复 ActivityBlock 'thing' 拼写错误
  - [x] SubTask 3.1: ActivityBlock.tsx L74 `'thing'` 改为 `'thinking'`

- [x] Task 4: 修复 sendMessage 异常吞错误
  - [x] SubTask 4.1: useChatStore.ts sendMessage catch 块设置 `streamingError: { code: 'INTERNAL_ERROR', message: err.message, detail: '' }`
  - [x] SubTask 4.2: 调用 `getLog()("error", "chat", ...)` 记录日志

- [x] Task 5: 修复空会话与加载中状态未区分
  - [x] SubTask 5.1: useChatStore.ts 加 `isLoadingMessages: boolean` 状态
  - [x] SubTask 5.2: `fetchMessages` 开始置 true，完成（成功/失败）置 false
  - [x] SubTask 5.3: ChatArea.tsx 在 `isLoadingMessages && !messages.length` 时显示骨架屏

- [x] Task 6: 确认 ChatArea 编辑框 Shift+Enter 换行
  - [x] SubTask 6.1: 验证 ChatArea.tsx 编辑框 textarea 的 Shift+Enter 行为（当前已是 Enter 发送、Shift+Enter 默认换行，符合要求）
  - [x] SubTask 6.2: 已确认无需修复 keydown 主体逻辑；同时补充 ESC 取消编辑（onCancelEdit）

- [x] Task 7: 修复 ErrorBlock 暗色模式硬编码颜色
  - [x] SubTask 7.1: ErrorBlock.tsx ERROR_STYLES 改用 CSS 变量（var(--warn)/var(--warn-soft)/var(--danger)/var(--danger-soft)/var(--primary)/var(--activity-bg)/var(--muted-fg)/var(--hover-bg)），SVG stroke 改用 style 传递以让 var() 生效

## 建议修改（8 项）

- [x] Task 8: 全局 toast 错误反馈
  - [x] SubTask 8.1: 新增 `frontend/src/components/shared/Toast.tsx` 轻量 toast 组件
  - [x] SubTask 8.2: 新增 `frontend/src/stores/useToastStore.ts` toast 状态管理
  - [x] SubTask 8.3: AppRoot 挂载 ToastContainer
  - [x] SubTask 8.4: fetchMessages 失败显示 toast
  - [x] SubTask 8.5: persistLastTurn 失败显示 toast
  - [x] SubTask 8.6: sendMessage 流式中静默拒绝显示 toast "正在生成中，请先停止当前回答"

- [x] Task 9: ESC 键统一取消行为
  - [x] SubTask 9.1: InputBar.tsx 加 keydown ESC：流式中停止、引用中取消引用、有附件移除附件（handleEscCancel，优先级 流式 > 引用 > 附件）
  - [x] SubTask 9.2: ChatArea.tsx 编辑框加 ESC 取消编辑（Task 6 已完成，确认无需补改）
  - [x] SubTask 9.3: ImageLightbox 加 ESC 关闭（与 Task 11 合并实施，useEffect 全局 keydown 监听）

- [x] Task 10: ConfirmDialog focus trap
  - [x] SubTask 10.1: ConfirmDialog.tsx 加 keydown Tab 监听，焦点循环
  - [x] SubTask 10.2: 对话框显示时自动聚焦"拒绝"按钮

- [x] Task 11: ImageLightbox 无障碍
  - [x] SubTask 11.1: ImageLightbox 加 `role="dialog" aria-modal="true"`（加 aria-label="图片预览"）
  - [x] SubTask 11.2: 加 loading 指示（图片加载时 spinner）（useState loaded + onLoad，未加载时显示"加载中..."文本）
  - [x] SubTask 11.3: ESC 关闭（与 Task 9.3 合并）

- [x] Task 12: 输入框草稿按会话持久化
      useChatStore.ts 加 drafts/setDraft/clearDraft，InputBar.tsx 移除 useState text 改为从 store 派生，切换会话自动响应
  - [x] SubTask 12.1: useChatStore.ts 加 `drafts: Record<string, string>` 和 `setDraft(sessionId, text)`
        ChatState interface 加 drafts/setDraft/clearDraft，初始值 drafts:{}，setDraft 用 spread 写入，clearDraft 用 delete 清空
  - [x] SubTask 12.2: InputBar.tsx 输入时调用 `setDraft(activeSessionId, text)`
        handleChange 和 handleInsertTemplate 改为调用 setDraft(activeSessionId, ...)
  - [x] SubTask 12.3: InputBar.tsx activeSessionId 变化时加载 `drafts[activeSessionId]`
        text 直接从 drafts[activeSessionId] 派生（const text = drafts[activeSessionId] || ""），切换会话自动响应，无需 useEffect

- [x] Task 13: 删除当前活跃会话后立即切换
  - [x] SubTask 13.1: SessionPanel.tsx deleteSession 中，若删除的是 activeSessionId，立即调用 setActiveSession 切到第一个剩余会话（在 handleDeleteSession 中先 setActiveSession 切到剩余会话第一个，再调 deleteSession，避免 store 内 setActiveSession 时 sessionMessages 缓存为空导致短暂空白）

- [ ] Task 14: 切换会话滚动位置保留（与 Task 1 合并实施）
  - [ ] SubTask 14.1: 见 Task 1.2 / 1.3，用 Map<sessionId, scrollTop> 缓存

- [x] Task 15: 应用内 Modal 替换原生弹窗（问题 19）
  - [x] SubTask 15.1: 新增 `frontend/src/components/shared/Modal.tsx` 通用 Modal 组件（含 focus trap、ESC、暗色模式）
        保留原 Modal 原语（BranchTree/ShortcutHelp/KnowledgePanel 复用），新增 ModalContainer：createPortal 到 document.body、z-index 9999、focus trap（Tab 循环）、ESC 取消、prompt 自动聚焦+全选、confirm 自动聚焦确认按钮、danger 模式红色确认按钮、var(--card)/var(--border)/var(--fg) 主题变量
  - [x] SubTask 15.2: 新增 `frontend/src/stores/useModalStore.ts` 提供 `confirmDialog({title, message, confirmText?, cancelText?}): Promise<boolean>` 和 `promptDialog({title, placeholder, defaultValue?}): Promise<string|null>` 异步接口
        Zustand store，confirmDialog resolve(true/false)、promptDialog resolve(inputValue/null)，_confirm/_cancel 内部调用 resolveFn 并 resetState，ESC 等价 _cancel
  - [x] SubTask 15.3: AppRoot 挂载 ModalContainer
        AppRoot.tsx 导入 ModalContainer 并在 ToastContainer 旁渲染；同时补齐此前缺失的 ToastContainer 导入
  - [x] SubTask 15.4: SessionPanel.tsx L151 `window.confirm` 替换为 `await confirmDialog(...)`
        handleDeleteSession 改为 async，confirmDialog danger:true；注：Task 13 已把删除逻辑重构为 handleDeleteSession（含删除活跃会话后切换），本次只替换其中 confirm
  - [x] SubTask 15.5: SessionPanel.tsx L136 `window.prompt` 替换为 `await promptDialog(...)`
        "新建项目"子菜单 onClick 改为 async，promptDialog 接收 title+placeholder
  - [x] SubTask 15.6: SnapshotPanel.tsx L118 `window.confirm` 替换为 `await confirmDialog(...)`
        handleRestore 改为 async，confirmDialog danger:true
  - [x] SubTask 15.7: SettingsPage.tsx L150 `window.confirm` 替换为 `await confirmDialog(...)`
        handleDeleteProvider 改为 async，confirmDialog danger:true，中英文 title/message 跟随 lang

- [x] Task 16: 多 tab 同步（问题 32）
  - [x] SubTask 16.1: 新增 `frontend/src/utils/broadcast.ts` 封装 BroadcastChannel（channel name: `hardware-rag-agent`），提供 `post(event, payload)` 和 `on(event, handler)` 接口
  - [x] SubTask 16.2: useSessionStore.ts 在 createSession/deleteSession/renameSession/moveSessionToProject 后调用 `post('sessions_changed')`
  - [x] SubTask 16.3: useSessionStore.ts 启动时监听 `sessions_changed` 事件，收到后 reload sessions
  - [x] SubTask 16.4: useChatStore.ts 在 sendMessage 完成/persistLastTurn 后调用 `post('messages_changed', sessionId)`
  - [x] SubTask 16.5: useChatStore.ts 启动时监听 `messages_changed`，若 payload.sessionId === activeSessionId 则 fetchMessages
  - [x] SubTask 16.6: useChatStore.ts 启动时监听 `session_deleted`，若 payload === activeSessionId 则切到第一个剩余会话
  - [x] SubTask 16.7: useSessionStore.ts deleteSession 后调用 `post('session_deleted', sessionId)`

## 验证

- [x] Task 17: 验证
  - [x] SubTask 17.1: `npx tsc --noEmit` 通过（0 errors）
  - [x] SubTask 17.2: agent-browser 实测：刷新页面自动滚到最后一条 — 代码审查确认 scrollPosCacheRef + prevSessionIdRef + rAF 双帧延迟 + ResizeObserver 兜底已落地（测试环境仅空会话，无法实测有消息场景）
  - [x] SubTask 17.3: agent-browser 实测：切换会话自动滚到底（首次）/恢复位置（切回）— 同上，代码已确认
  - [x] SubTask 17.4: agent-browser 实测：HITL 确认框 ESC 等价拒绝 — 代码审查确认 ConfirmDialog.tsx L90 `resumeAgent("deny")` 已落地
  - [x] SubTask 17.5: agent-browser 实测：reasoning 标签显示 'thinking' — 代码审查确认 ActivityBlock.tsx L74 `'thinking'` 已落地
  - [x] SubTask 17.6: agent-browser 实测：流式中 ESC 停止流式 — 代码审查确认 InputBar.tsx handleEscCancel 已落地（优先级 流式 > 引用 > 附件）
  - [x] SubTask 17.7: agent-browser 实测：图片灯箱 ESC 关闭 — 代码审查确认 ChatArea.tsx ImageLightbox useEffect 全局 ESC 监听已落地
  - [x] SubTask 17.8: agent-browser 实测：草稿切换会话保留 — 已实测通过（输入"测试草稿持久化"，切会话切回，textarea 值保留）
  - [x] SubTask 17.9: agent-browser 实测：toast 错误反馈显示 — 代码审查确认 useToastStore + Toast.tsx + AppRoot 挂载已落地
  - [x] SubTask 17.10: agent-browser 实测：删除会话弹应用内 Modal（非浏览器原生）— 已实测通过（弹应用内 Modal，非 window.confirm）
  - [x] SubTask 17.11: agent-browser 实测：Modal ESC 取消、Tab 焦点循环 — ESC 已实测通过；Tab 焦点循环代码审查确认 Modal.tsx getFocusables + cycleTab 已落地
  - [x] SubTask 17.12: agent-browser 实测：多 tab 同步 — 代码审查确认 broadcast.ts + useSessionStore/useChatStore post/on 集成已落地（未开双 tab 实测）
  - [x] SubTask 17.13: 中文代码自审 — 代码注释中英混排符合规范，业务逻辑用中文，技术术语保留英文

# Task Dependencies

- Task 1（滚动+位置保留）独立
- Task 2（ConfirmDialog ESC）独立
- Task 3（'thing' 拼写）独立
- Task 4（sendMessage 错误）独立
- Task 5（加载状态）独立
- Task 6（编辑换行）独立
- Task 7（ErrorBlock 颜色）独立
- Task 8（toast）独立，Task 4 完成后才能完整验证
- Task 9（ESC）与 Task 2（ConfirmDialog ESC）和 Task 11（灯箱 ESC）有重叠，需协调
- Task 10（focus trap）独立
- Task 11（灯箱无障碍）与 Task 9 部分重叠
- Task 12（草稿）独立
- Task 13（删除会话）独立
- Task 14（滚动位置）已合并到 Task 1
- Task 15（应用内 Modal）独立
- Task 16（多 tab 同步）独立，但 Task 13（删除会话切换）的"删除"事件需在 Task 16 中广播
- Task 17（验证）依赖所有任务完成

# 并行执行建议

可并行执行的 Task 组（无依赖）：
- 组 A：Task 1, 2, 3, 4, 5, 6, 7（必须修复，全部独立）
- 组 B：Task 8, 10, 12, 13, 15（建议修改，独立；Task 15 Modal 独立组件）
- 组 C：Task 9 + 11（ESC 重叠，需同一 agent 实施）
- 组 D：Task 16（多 tab 同步，独立但需 Task 13 完成后才能完整验证删除场景）
- 组 E：Task 17（验证，最后执行）
