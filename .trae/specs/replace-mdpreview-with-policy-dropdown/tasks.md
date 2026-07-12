# Tasks

- [x] Task 1: 删除 InputBar 中的 Markdown 预览功能
  - [x] SubTask 1.1: 读取 `frontend/src/components/input/InputBar.tsx`，定位 `showPreview` 状态、预览区域、Markdown 预览按钮
  - [x] SubTask 1.2: 删除 `showPreview` 的 `useState` 及相关 `useEffect`/事件处理
  - [x] SubTask 1.3: 删除 `input-preview` 预览区域 JSX
  - [x] SubTask 1.4: 删除 Markdown 预览按钮 JSX
  - [x] SubTask 1.5: 删除 `previewHtml` 相关计算逻辑（如有）
  - [x] SubTask 1.6: 验证输入栏无 Markdown 预览入口

- [x] Task 2: 在输入栏底部操作区新增 Agent 权限下拉选择器
  - [x] SubTask 2.1: 读取 `frontend/src/components/chat/PolicyBar.tsx`，确认三种权限模式及标签
  - [x] SubTask 2.2: 在 `InputBar.tsx` 引入 `useSettingsStore` 的 `permissionMode` 和 `updateSetting`
  - [x] SubTask 2.3: 在底部操作区（模型选择器与发送按钮之间）新增权限下拉选择器组件/内联 JSX
  - [x] SubTask 2.4: 下拉选项绑定三种模式：bypassPermissions / default / acceptEdits
  - [x] SubTask 2.5: 选择后调用 `updateSetting("permissionMode", mode)`
  - [x] SubTask 2.6: 点击外部关闭下拉菜单
  - [x] SubTask 2.7: 验证切换权限后，后续 `sendMessage` 请求体中的 `permission_mode` 正确更新

- [x] Task 3: 清理样式与 i18n
  - [x] SubTask 3.1: 检查 `frontend/src/styles/misc.css` 中 `.preview-toggle` / `.preview-pane` 是否仅 InputBar 使用，若是则删除
  - [x] SubTask 3.2: 删除 `frontend/src/i18n/zh.ts` 和 `frontend/src/i18n/en.ts` 中的 `mdPreviewBtn`、`mdPreviewHint`
  - [x] SubTask 3.3: 确认权限模式 i18n 词条存在，若缺失则补充（复用 PolicyBar 已有标签或新增）

- [x] Task 4: 验证
  - [x] SubTask 4.1: 运行 `cd frontend && npx tsc --noEmit`，确保 0 errors
  - [x] SubTask 4.2: 手动验证 Markdown 预览按钮和预览区域已消失
  - [x] SubTask 4.3: 手动验证权限下拉选择器可展开、可切换、显示正确
  - [x] SubTask 4.4: 手动验证发送消息后请求体 `permission_mode` 与选择一致

# Task Dependencies

- Task 1 与 Task 2 可并行开发，但合并时需注意布局不冲突
- Task 3 依赖 Task 1 完成后再清理样式/i18n
- Task 4 依赖 Task 1/2/3 完成
