# 01-app — 产品外壳线程

> **Version:** 1.0.0 | **Last updated:** 2026-06-21
> **Thread Overview (AGENTS.md):** 布局 / 导航 / 主题
> **TODO 文件:** docs/todos/01-app.md

## 线程元信息

| 字段 | 值 |
|------|-----|
| 线程编号 | 01 |
| 线程名 | app |
| 创建日期 | 2026-06-21 |
| 职责 | React 应用骨架、布局、导航、全局主题、Error Boundary、面板系统 |

## 负责范围

- React/Vite 应用骨架
- 页面布局：IconNav、LeftPanel、RightPanel、TopBar、MainArea
- 全局主题 system：light/dark/auto、CSS variables、Tailwind 兼容
- Error Boundary：前端崩溃兜底、错误展示和恢复按钮
- App-level Zustand 状态（useAppStore）
- 面板系统：左右面板开关、拖拽宽度、工作区切换
- Navigation State：activeNav、modal、settings、knowledge、bookmarks
- Loading/Skeleton：页面级和组件级 loading
- Responsive：桌面、窄屏、移动端布局退化

## 不负责

- 不负责聊天业务逻辑（02-chat）
- 不负责 RAG 检索（03-knowledge）
- 不负责数据库持久化（04-session）
- 不负责 Agent 工具编排（05-agent）
- 不负责硬件串口/烧录/接线图（07-hardware）

## 关键文件

| 文件 | 说明 |
|------|------|
| frontend/src/App.tsx | 应用入口组件，挂载 useTheme + useKeyboard |
| frontend/src/main.tsx | 应用入口，挂载 ErrorBoundary + QueryClientProvider |
| frontend/src/components/layout/AppRoot.tsx | 主布局：IconNav + LeftPanel + MainArea + RightPanel + 模式页面 |
| frontend/src/components/layout/IconNav.tsx | 左侧导航栏（chat/knowledge/bookmarks/settings） |
| frontend/src/components/layout/LeftPanel.tsx | 左侧面板容器（包裹 SessionPanel） |
| frontend/src/components/layout/RightPanel.tsx | 右侧面板（workbench/content 双模式） |
| frontend/src/components/layout/MainArea.tsx | 主内容区容器 |
| frontend/src/components/topbar/TopBar.tsx | 顶栏（标题/快照/源面板/汉堡菜单） |
| frontend/src/components/shared/ErrorBoundary.tsx | 前端崩溃兜底组件 |
| frontend/src/stores/useAppStore.ts | 全局 UI 状态管理 |
| frontend/src/hooks/useTheme.ts | 主题切换逻辑 |
| frontend/src/hooks/usePanelResize.ts | 面板拖拽调整 hook |
| frontend/src/hooks/useKeyboard.ts | 键盘快捷键管理 |
| frontend/src/styles/globals.css | 全局样式与 CSS 变量 |

## 当前完成状态

### ✅ 已实现

- [x] App Shell：App.tsx → AppRoot.tsx 完整链路
- [x] Error Boundary：包裹整站，含清除缓存并重置按钮
- [x] 主导航：IconNav 含 chat/knowledge/bookmarks + settings 底部
- [x] 左侧面板：SessionPanel 集成，含右键菜单/搜索/折叠组
- [x] 右侧面板：workbench/content 双模式切换
- [x] TopBar：标题显示/快照按钮/源面板按钮/汉堡菜单
- [x] 面板拖拽：usePanelResize hook，左右面板均可拖拽
- [x] 主题系统：light/dark/auto 三模式，CSS 变量 + Tailwind 兼容
- [x] 全局状态：useAppStore 覆盖导航/面板/主题/硬件/辅助 UI 状态
- [x] 全局样式：完整 CSS 设计 Token

### ❌ 待实现

- [ ] 响应式布局退化（窄屏/移动端）
- [ ] Loading skeleton 组件
- [ ] App-level 持久化（面板宽度/主题偏好保存到 localStorage）
- [ ] 键盘快捷键统一管理（Ctrl+K 搜索已实现，其余待验证）
- [ ] 前端日志面板入口
- [ ] 附件上传（文件/图片）对接真实接口
- [ ] 对话导出/分享功能验证与完善

> 完整待办清单见 docs/todos/01-app.md（倒序排列，每次只读前 2 项）

## 细颗粒模块清单

### AppRoot 布局结构

布局结构参考 AppRoot.tsx：IconNav(48px) | [TopBar / ChatArea+InputBar / 模式页面] | RightPanel

### 导航视图切换

- chat：显示 TopBar + ChatArea + InputBar + LeftPanel(Session) + RightPanel
- knowledge：显示 KnowledgePanel（全宽，无左右面板）
- bookmarks：显示 BookmarkPanel（全宽，无左右面板）
- settings：显示 SettingsPage（覆盖在主布局上）

## 踩坑记录

| 日期 | 问题 | 状态 |
|------|------|------|
| — | 暂无本线程专属踩坑 | — |
| docs/pitfalls.md | 项目级踩坑记录中心，各线程读写 | ✅ 已关联 |

## 跨线程接口

### 给 02-chat 的依赖

- AppRoot.tsx 渲染 ChatArea + InputBar 作为默认视图
- TopBar 显示 session title
- 右侧面板 source 模式需要 chat store 数据

### 给 04-session 的依赖

- LeftPanel 渲染 SessionPanel（由 04-session 提供）
- 左侧面板宽度由 useAppStore 管理

### 给 07-hardware 的依赖

- RightPanel workbench 模式渲染 WorkbenchPanel（由 07-hardware 提供）
- 硬件相关状态（serialConnected, flashState 等）存储在 useAppStore

## 构建报告

> 验证时间：2026-06-21 | 命令：npm run build（tsc -b && vite build）

| 检查项 | 结果 |
|--------|------|
| vite build | ✅ 成功（750 modules, 15.8s） |
| tsc -b | ❌ 33 项 TS 错误（均在 02-chat / 04-session 范围） |
| 01-app 内错误 | ⚠️ TopBar.tsx:20 — Message.content 联合类型 |

---

## 下次开工先看

按 AGENTS.md Start Here 流程：

1. `plur inject "01-app 当前任务" --fast --json` — 读 PLUR 长期记忆
2. 读 docs/completed.md — 知哪些功能已做完
3. 读 docs/pitfalls.md — 知前人踩过什么坑
4. 读 docs/todos/01-app.md — 知自己要做啥（只读前 2 项）
5. 读完前 2 项就开始干活，不要一次扛太多
