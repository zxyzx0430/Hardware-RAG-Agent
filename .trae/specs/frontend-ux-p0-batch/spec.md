# 前端功能性 Bug 修复 + UX 提升（P0 批次）Spec

## Why

经两轮深度审查（功能性 bug + UX 提升），前端存在 3 个 P0 功能性 bug 和 8 个高收益 UX 提升点。本批次聚焦"小改大收益"，不动核心流式架构（F1 用户明确禁止改动），保证核心功能稳定的同时显著提升用户体验。

## What Changes

### 功能性 Bug 修复（2 项）

- **F2 新建项目会话刷新丢失**：`useSessionStore.createProject` 创建会话时复用 `newSession` 的后端创建逻辑，确保会话持久化到后端，刷新后不丢失
- **F3 收藏夹空数组初始化竞态**：`useBookmarkStore` 初始化时强制保证至少有一个 default 文件夹，即使 localStorage 存了空数组也补一个

### UX 提升（8 项）

- **U1 恢复细滚动条**：`base.css` 把 `::-webkit-scrollbar { display: none; }` 改为细滚动条样式（8px 宽，hover 变深），全局恢复滚动可感知性
- **U2 顶栏空对话标题**：`TopBar.tsx` fallback 从硬编码 "STM32 I2C 通信问题排查" 改为 `t('untitled')`
- **U3 EmptyState API Key 引导**：`ChatArea.tsx` 的 EmptyState 检测 `providerKeys` 为空时，显示"第一步：配置 API Key"卡片 + "去设置"按钮，替代 4 个建议问题；配过 Key 后恢复建议问题
- **U5 导航 tooltip**：`IconNav.tsx` 所有导航按钮加 `title` 属性（走 i18n），新用户能理解图标含义
- **U6 知识库删除二次确认**：`KnowledgePanel.tsx` 文档删除按钮点击后弹二次确认框（复用 Modal 组件），避免手滑损失上传+索引工作量
- **U7 上传进度反馈**：`KnowledgePanel.tsx` 上传阶段用 `XMLHttpRequest.upload.onprogress` 显示百分比进度条，索引阶段显示"已建立 N 个片段"计数，加取消按钮
- **U8 设置页保存反馈**：`SettingsPage.tsx` 每次 `updateSetting` 后在字段下方短暂显示"✓ 已保存"小字（2 秒淡出）
- **U9 收藏弹窗新建入口**：`BookmarkPanel.tsx` 的 `FolderSelectDialog` 列表底部加"+ 新建收藏夹"行，点击行内输入名称直接创建并选中

## Impact

- **Affected specs**: 无（本批次不涉及已有 spec）
- **Affected code**:
  - `frontend/src/stores/useSessionStore.ts` — createProject 重构
  - `frontend/src/stores/useBookmarkStore.ts` — 初始化逻辑
  - `frontend/src/styles/base.css` — 滚动条样式
  - `frontend/src/components/topbar/TopBar.tsx` — 标题 fallback
  - `frontend/src/components/chat/ChatArea.tsx` — EmptyState 引导
  - `frontend/src/components/layout/IconNav.tsx` — tooltip
  - `frontend/src/components/knowledge/KnowledgePanel.tsx` — 删除确认 + 上传进度
  - `frontend/src/components/settings/SettingsPage.tsx` — 保存反馈
  - `frontend/src/components/bookmarks/BookmarkPanel.tsx` — 收藏弹窗新建入口
  - `frontend/src/i18n/translations.ts` — 新增 i18n key（如有）

## ADDED Requirements

### Requirement: 新建项目会话持久化

系统 SHALL 在用户通过"新建项目"创建会话时，同步调用后端 `POST /api/sessions` 持久化会话，确保刷新后不丢失。

#### Scenario: 用户新建项目后聊天刷新

- **WHEN** 用户点击"新建项目"创建项目分类，系统自动创建一个"新对话"会话
- **AND** 用户在该会话中发送消息并收到回复
- **AND** 用户刷新页面
- **THEN** 该会话和所有消息依然存在，无数据丢失

### Requirement: 收藏夹默认文件夹保证

系统 SHALL 在 `useBookmarkStore` 初始化时，无论 localStorage 中 `bookmarkFolders` 为何值（空数组、缺失、损坏），都强制保证至少存在一个 default 文件夹。

#### Scenario: localStorage 存了空数组

- **WHEN** 用户曾手动删光所有收藏夹，localStorage 中 `bookmarkFolders = []`
- **AND** 用户重新打开应用
- **THEN** store 初始化后 `bookmarkFolders` 至少包含一个 default 文件夹
- **AND** 用户点击消息收藏按钮时，书签能正确归入 default 文件夹，在收藏面板可见

### Requirement: 全局细滚动条

系统 SHALL 在所有滚动区域显示细滚动条（8px 宽），hover 时变深，替代当前完全隐藏的滚动条。

#### Scenario: 用户在长列表中滚动

- **WHEN** 用户在会话列表、知识库文档列表、长对话、设置面板等任意滚动区域
- **THEN** 滚动条可见（8px 宽，与主题边框色一致）
- **AND** hover 滚动条时颜色变深（变为主题 muted-fg 色）
- **AND** 滚动条轨道透明，不占用视觉空间

### Requirement: 顶栏空对话标题

系统 SHALL 在会话无标题时，顶栏显示 i18n 的"未命名对话"，而非硬编码的占位符文本。

#### Scenario: 新建空对话

- **WHEN** 用户新建一个对话，尚未发送任何消息
- **THEN** 顶栏标题显示"未命名对话"（或对应英文 i18n）
- **AND** 不显示 "STM32 I2C 通信问题排查" 等开发占位符

### Requirement: EmptyState API Key 引导

系统 SHALL 在用户未配置任何 API Key 时，EmptyState 显示配置引导卡片而非建议问题列表。

#### Scenario: 新用户首次进入未配置 Key

- **WHEN** 新用户首次打开应用，`providerKeys` 为空（所有 provider 均无 key）
- **THEN** ChatArea 的 EmptyState 显示一张居中卡片
- **AND** 卡片包含标题"第一步：配置 API Key"和说明文字
- **AND** 卡片包含"去设置"按钮，点击跳转到设置页
- **AND** 不显示 4 个建议问题

#### Scenario: 已配置 Key 后恢复建议

- **WHEN** 用户配置了至少一个 provider 的 API Key
- **THEN** EmptyState 恢复显示 4 个建议问题
- **AND** 不再显示配置引导卡片

### Requirement: 导航按钮 tooltip

系统 SHALL 为左侧 IconNav 的所有导航按钮提供 `title` tooltip，文字走 i18n。

#### Scenario: 新用户悬停导航按钮

- **WHEN** 用户鼠标悬停在 IconNav 的任意按钮上
- **THEN** 约 500ms 后显示 tooltip 文字（如"聊天"、"知识库"、"收藏"、"设置"）
- **AND** 文字跟随当前语言（中文/英文）

### Requirement: 知识库文档删除二次确认

系统 SHALL 在用户点击知识库文档删除按钮时，弹出二次确认框，避免误删。

#### Scenario: 用户点击删除按钮

- **WHEN** 用户点击知识库文档列表中某文档的删除按钮（垃圾桶图标）
- **THEN** 弹出 Modal 确认框，标题"删除文档"
- **AND** 正文显示"确定删除《{文档名}》？该操作不可撤销。"
- **AND** 包含"取消"和"删除"两个按钮，"删除"为危险色（红色）
- **AND** 用户点"取消"或遮罩时不删除
- **AND** 用户点"删除"时才执行 `deleteItemWithAPI`

### Requirement: 上传进度反馈

系统 SHALL 在用户上传知识库文档时，提供上传进度条、索引计数和取消按钮。

#### Scenario: 上传大文件

- **WHEN** 用户上传一个 10MB 的 PDF 文件
- **THEN** 上传阶段显示进度条（0%-100%），基于 `XMLHttpRequest.upload.onprogress`
- **AND** 进度条下方显示"上传中… 45%"
- **AND** 提供"取消"按钮，点击后中止上传

#### Scenario: 索引阶段

- **WHEN** 文件上传完成，进入后端索引阶段
- **THEN** 显示 spinner + 文字"索引中… 已建立 {N} 个片段"
- **AND** N 随轮询结果实时更新
- **AND** 保留"取消"按钮（点击后标记取消，索引完成后清理）

### Requirement: 设置页保存反馈

系统 SHALL 在用户修改设置项（温度、Top-K、系统提示词、API Key 等）后，在字段下方短暂显示"✓ 已保存"反馈。

#### Scenario: 用户调整温度滑块

- **WHEN** 用户拖动温度滑块，触发 `updateSetting`
- **THEN** 滑块下方显示"✓ 已保存"小字（绿色）
- **AND** 2 秒后小字淡出消失

#### Scenario: 用户修改 API Key

- **WHEN** 用户在 API Key 输入框输入内容，触发 `setProviderKey`
- **THEN** 输入框下方显示"✓ 已保存"小字
- **AND** 2 秒后淡出

### Requirement: 收藏弹窗新建收藏夹入口

系统 SHALL 在收藏夹选择弹窗（FolderSelectDialog）中提供"+ 新建收藏夹"入口，支持在弹窗内直接创建新收藏夹并选中。

#### Scenario: 用户收藏消息时新建夹

- **WHEN** 用户点击消息的收藏按钮，弹出 FolderSelectDialog
- **AND** 用户点击列表底部的"+ 新建收藏夹"行
- **THEN** 该行变为行内输入框
- **AND** 用户输入名称后按 Enter，创建新收藏夹并自动选中
- **AND** 用户按 Esc 或失焦时取消新建
- **AND** 新收藏夹创建后，当前消息的书签归入该夹

## MODIFIED Requirements

无（本批次均为新增能力或修复，不修改已有 spec 中的需求）

## REMOVED Requirements

无
