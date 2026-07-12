# Tasks

## 阶段 1：功能性 Bug 修复（2 项，可并行）

- [ ] Task 1: 修复 F2 新建项目会话刷新丢失
  - [ ] SubTask 1.1: 读取 `frontend/src/stores/useSessionStore.ts` 的 `createProject` 函数（约 297-321 行）和 `newSession` 函数（约 151-165 行），理解两者差异
  - [ ] SubTask 1.2: 重构 `createProject`，创建会话时调用 `apiPost("sessions", ...)` 持久化到后端（复用 newSession 的逻辑），用后端返回的真实 id 替代 `s${Date.now()}`
  - [ ] SubTask 1.3: 验证：新建项目 → 在新会话发消息 → 刷新页面 → 会话和消息仍在

- [ ] Task 2: 修复 F3 收藏夹空数组初始化竞态
  - [ ] SubTask 2.1: 读取 `frontend/src/stores/useBookmarkStore.ts` 的初始化逻辑（约 49-51 行）
  - [ ] SubTask 2.2: 在 store 初始化后加保护逻辑：如果 `bookmarkFolders.length === 0`，自动补一个 default 文件夹（id="default", name="默认收藏夹"）
  - [ ] SubTask 2.3: 确保 `toggleBookmark` 无 folderId 参数时用真实存在的第一个文件夹 id，而非硬编码 "default"
  - [ ] SubTask 2.4: 验证：localStorage 存空数组 → 打开应用 → 点收藏 → 书签在收藏面板可见

## 阶段 2：简单 UX 提升（4 项，可并行，改动小）

- [ ] Task 3: U1 恢复细滚动条
  - [ ] SubTask 3.1: 读取 `frontend/src/styles/base.css` 第 68 行附近的 `::-webkit-scrollbar { display: none; }`
  - [ ] SubTask 3.2: 替换为细滚动条样式：8px 宽、thumb 用 `var(--border)` 色、hover 变 `var(--muted-fg)`、track 透明、圆角 4px
  - [ ] SubTask 3.3: 验证：会话列表、知识库列表、长对话、设置面板都能看到细滚动条

- [ ] Task 4: U2 顶栏空对话标题
  - [ ] SubTask 4.1: 读取 `frontend/src/components/topbar/TopBar.tsx` 第 21 行
  - [ ] SubTask 4.2: 把 fallback `"STM32 I2C 通信问题排查"` 改为 `t('untitled')`（i18n 里已有 '未命名对话'）
  - [ ] SubTask 4.3: 验证：新建空对话 → 顶栏显示"未命名对话"

- [ ] Task 5: U5 导航 tooltip
  - [ ] SubTask 5.1: 读取 `frontend/src/components/layout/IconNav.tsx`
  - [ ] SubTask 5.2: 给所有 nav-btn 加 `title={t('chat')}` / `title={t('knowledge')}` / `title={t('bookmarks')}` / `title={t('settings')}` 等 i18n key
  - [ ] SubTask 5.3: 确认 i18n translations.ts 里有对应 key，没有则补充
  - [ ] SubTask 5.4: 验证：悬停各导航按钮 → 500ms 后显示对应文字 tooltip

- [ ] Task 6: U8 设置页保存反馈
  - [ ] SubTask 6.1: 读取 `frontend/src/components/settings/SettingsPage.tsx`
  - [ ] SubTask 6.2: 实现一个 `useSavedFeedback()` hook 或局部 state：触发后显示"✓ 已保存"，2 秒后淡出
  - [ ] SubTask 6.3: 在温度滑块、Top-K、系统提示词、API Key 等字段的 `onChange`/`updateSetting`/`setProviderKey` 后触发反馈
  - [ ] SubTask 6.4: 反馈小字用绿色（`var(--success)` 或类似），定位在字段下方
  - [ ] SubTask 6.5: 验证：调整温度 → 下方闪现"✓ 已保存" → 2 秒淡出

## 阶段 3：中等 UX 提升（4 项，需新建组件/逻辑）

- [ ] Task 7: U3 EmptyState API Key 引导
  - [ ] SubTask 7.1: 读取 `frontend/src/components/chat/ChatArea.tsx` 的 EmptyState（约 447-461 行）
  - [ ] SubTask 7.2: 从 `useSettingsStore` 读取 `providerKeys`，判断是否所有 provider 均无 key
  - [ ] SubTask 7.3: 无 key 时渲染配置引导卡片：标题"第一步：配置 API Key" + 说明 + "去设置"按钮（点击 `setActiveNav('settings')`）
  - [ ] SubTask 7.4: 有 key 时保留原 4 个建议问题
  - [ ] SubTask 7.5: 验证：清空所有 API Key → EmptyState 显示引导卡片 → 配置后恢复建议问题

- [ ] Task 8: U6 知识库删除二次确认
  - [ ] SubTask 8.1: 读取 `frontend/src/components/knowledge/KnowledgePanel.tsx` 第 336 行附近的删除按钮
  - [ ] SubTask 8.2: 新增 state `deleteConfirmId: string | null`，点击删除按钮时 set 该 id
  - [ ] SubTask 8.3: 当 `deleteConfirmId` 非空时，渲染 Modal 确认框（复用 `components/shared/Modal.tsx`）
  - [ ] SubTask 8.4: Modal 内容：标题"删除文档"、正文"确定删除《{文档名}》？该操作不可撤销。"、"取消"按钮、"删除"按钮（红色危险色）
  - [ ] SubTask 8.5: 点"删除"时调 `deleteItemWithAPI(deleteConfirmId)` 然后关闭 Modal；点"取消"或遮罩时仅关闭 Modal
  - [ ] SubTask 8.6: 验证：点删除 → 弹确认框 → 取消不删 → 确认才删

- [ ] Task 9: U7 上传进度反馈
  - [ ] SubTask 9.1: 读取 `frontend/src/components/knowledge/KnowledgePanel.tsx` 的 `handleFiles`（约 73-128 行）和 `pollIndexingStatus`（约 131-164 行）
  - [ ] SubTask 9.2: 把 `apiPost` 改为原生 `XMLHttpRequest`，监听 `upload.onprogress` 计算 percentage
  - [ ] SubTask 9.3: 新增 state：`uploadProgress: { [docId]: { phase: 'uploading'|'indexing', percent: number, chunks: number } }`
  - [ ] SubTask 9.4: 上传阶段显示进度条（0-100%）+ "上传中… {percent}%"
  - [ ] SubTask 9.5: 索引阶段轮询时从后端响应提取已建立 chunk 数，显示"索引中… 已建立 {N} 个片段"
  - [ ] SubTask 9.6: 加"取消"按钮：上传阶段 `xhr.abort()`，索引阶段标记 `cancelled` 标志，轮询结束后清理
  - [ ] SubTask 9.7: 验证：上传 10MB 文件 → 看到进度条 → 可取消

- [ ] Task 10: U9 收藏弹窗新建入口
  - [ ] SubTask 10.1: 读取 `frontend/src/components/bookmarks/BookmarkPanel.tsx` 的 `FolderSelectDialog`（约 21-72 行）
  - [ ] SubTask 10.2: 在文件夹列表底部加一行"+ 新建收藏夹"按钮
  - [ ] SubTask 10.3: 点击后该行变为 input + 确认/取消，Enter 提交（调 `addBookmarkFolder`），Esc/失焦取消
  - [ ] SubTask 10.4: 新建成功后自动选中新夹（把书签归入新夹）
  - [ ] SubTask 10.5: 验证：点消息收藏 → 弹窗 → 点"+ 新建" → 输入名 → Enter → 书签归入新夹

## 阶段 4：验证

- [ ] Task 11: 全量验证
  - [ ] SubTask 11.1: 运行 `cd frontend && npx tsc --noEmit`，确保 0 errors
  - [ ] SubTask 11.2: 手动验证所有 10 项改动的 Scenario（见 spec.md）
  - [ ] SubTask 11.3: 确认未引入回归（聊天、知识库、收藏、设置主流程正常）

# Task Dependencies

- Task 1, 2 可并行（互不依赖）
- Task 3, 4, 5, 6 可并行（都是小改动，互不依赖）
- Task 7 依赖 Task 5 完成（U3 用到 settings store，但不依赖 U5）—— 实际可并行
- Task 8, 9 都改 KnowledgePanel.tsx，**不可并行**，需顺序执行
- Task 10 独立，可与 Task 7-9 并行
- Task 11 依赖所有前置任务完成

**并行分组建议**：
- 第一波：Task 1 + 2 + 3 + 4 + 5 + 6 + 10（7 个独立任务）
- 第二波：Task 8（KnowledgePanel 删除确认）
- 第三波：Task 9（KnowledgePanel 上传进度，依赖 Task 8 完成避免冲突）
- 第四波：Task 7（EmptyState，可放第一波）
- 最终：Task 11 验证
