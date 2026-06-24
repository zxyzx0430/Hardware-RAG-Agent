# 01-app 代码扫描报告

> **扫描时间:** 2026-06-24
> **范围:** App.tsx / main.tsx / layout/* / topbar/* / shared/* / useAppStore / hooks(theme/panelResize/keyboard) / globals.css
> **方法:** Pass 1 广度扫描 → Pass 2 深度扫描
> **原则:** 只记录不修改

---

## Pass 1：广度扫描

---

## [P1] TopBar.tsx Message.content 联合类型未处理

- **位置:** `frontend/src/components/topbar/TopBar.tsx:20`
- **现象:** `sessionTitle` 通过 `messages.find((m) => m.role === "user")?.content?.slice(0, 32)` 取值，但 `Message.content` 类型为 `string | ContentPart[]`。`ContentPart[]` 有 `.slice()` 方法（数组切片），不会崩溃，但在 <span> 中渲染数组对象会显示 `[object Object]` 而非文本。
- **影响评估:** 当消息 content 为 ContentPart[] 时，TopBar 标题显示乱码。之前已发现未修。
- **建议修复方式:** 使用类似 `renderContent()` 的工具函数提取纯文本。

---

## [P1] `chatFontSize` 状态未实际应用到 UI

- **位置:** `frontend/src/stores/useAppStore.ts`（定义）+ `frontend/src/components/chat/ChatArea.tsx:100`（解构）
- **现象:** `useAppStore` 定义了 `chatFontSize` state 和 `setChatFontSize` action（默认 `14`），ChatArea 也解构了该字段，但 **未在任何渲染元素上使用** `style={{ fontSize: chatFontSize }}`。搜索 `fontSize` / `font-size` 属性，ChatArea 中只有硬编码值（如 `fontSize: 11`）。
- **影响评估:** 设置面板中如果用户调整了字体大小，界面上没有任何变化。功能形同虚设。
- **建议修复方式:** 在聊天消息容器上应用 `style={{ fontSize: chatFontSize }}`。

---

## [P1] 全局滚动条隐藏导致可用性问题

- **位置:** `frontend/src/styles/globals.css:17`
- **现象:** `::-webkit-scrollbar { display: none }` 全局隐藏所有滚动条。用户在长内容区域（ChatArea、代码块、左侧会话列表）无法感知当前滚动位置。
- **影响评估:** 桌面端通过滚轮仍可滚动，但触摸板用户和移动端用户无法看到滚动条位置，体验劣化。
- **建议修复方式:** 改用 `::-webkit-scrollbar { width: 6px }` 配合 hover 显示。

---

## [P1] ErrorBoundary 主题硬编码

- **位置:** `frontend/src/components/shared/ErrorBoundary.tsx:23-50`
- **现象:** 错误展示页面使用了硬编码的暗色主题（`background: "#0d1117"`, `color: "#e6edf3"`），不随 app 的 light/dark 主题切换。当用户使用 light 主题时，突然跳出一个暗色错误页，视觉突兀。
- **影响评估:** 主题不一致，light 模式下用户体验差。
- **建议修复方式:** 使用 CSS 变量（`var(--bg)`, `var(--fg)`）替代硬编码颜色。

---

## [P2] MainArea.tsx 是死代码

- **位置:** `frontend/src/components/layout/MainArea.tsx`
- **现象:** `MainArea.tsx` 是完整的独立组件（含 TopBar + ChatArea + InputBar），但 `AppRoot.tsx` **未导入也未使用**它。AppRoot 内联了相同布局代码。
- **影响评估:** 无运行时问题，但 42 行代码是 dead weight，增加维护负担。
- **建议修复方式:** 删除 `MainArea.tsx`，或将 AppRoot 中的内联布局替换为 `<MainArea />`。

---

## [P2] IconNav 中存在无 onClick 的导航按钮

- **位置:** `frontend/src/components/layout/IconNav.tsx:54-77`
- **现象:** 4 个 `.nav-dim` 按钮（代码/时钟/地球/柱状图图标）没有任何 `onClick` 处理器，使用 `className="nav-btn nav-dim"`。鼠标悬停显示指针但点击无反应。
- **影响评估:** 用户可能误以为功能未实现而非装饰元素。
- **建议修复方式:** 要么添加功能，要么使用 `<div>` 替代 `<button>`，或者加 `disabled` 样式 + `title` 提示。

---

## [P2] SearchModal 全局搜索空结果无提示

- **位置:** `frontend/src/components/shared/SearchModal.tsx`
- **现象:** `globalSearch` tab 下，当 API 返回空结果（`results: []`）时，UI 仅显示空白区域。没有"未找到结果"的提示文字。
- **影响评估:** 用户搜索后看到空白面板，不确定是没结果还是正在搜索。
- **建议修复方式:** 添加空结果提示 `<div>{t('noResults')}</div>`。

---

## [P2] TemplatePanel 保存交互模式反直觉

- **位置:** `frontend/src/components/shared/TemplatePanel.tsx:58-72`
- **现象:** `handleSave` 是两阶段交互——第一次点击进入"命名模式"，第二次点击才真正保存。无视觉反馈区分两种状态（仅有 `saving` state 从 false→true），用户容易困惑。
- **影响评估:** 用户可能以为第一次点击就保存了，实际并未。
- **建议修复方式:** 使用模态框 + 输入框一次完成命名和保存，或明确显示输入框和确认按钮。

---

## [P2] 部分 shared 组件缺少 i18n

- **位置:** `frontend/src/components/shared/ErrorBoundary.tsx`
- **现象:** ErrorBoundary 硬编码了中文"应用出错"、"清除缓存并重置"等文本，未使用 `useI18n()` hook。当语言设为英文时仍显示中文。
- **影响评估:** i18n 覆盖不完整。
- **建议修复方式:** ErrorBoundary 是 class 组件无法直接使用 hooks，可通过 `withI18n` HOC 或外层包裹函数组件注入。

---

## [P2] ContextMenu 子菜单定位索引计算脆弱

- **位置:** `frontend/src/components/shared/ContextMenu.tsx`（SubMenu 组件）
- **现象:** 子菜单项 `onMouseEnter` 中通过 `ref.current?.children[i + items.slice(0, i).filter((x) => x.label === "---").length]` 计算 DOM 索引来定位。此计算依赖 children 的渲染顺序与 items 数组完全一致，且未考虑分隔符（`"---"`）的 `div.ctx-sep` 在 children 中也是元素。
- **影响评估:** 当菜单项较多且有分隔符时，子菜单位置可能偏移。
- **建议修复方式:** 使用 `data-index` 属性或 ref 数组替代 children 索引遍历。

---

## Pass 2：深度扫描

基于 Pass 1 结果，选取 3 个最可疑路径做深度检查：

---

## [深度] SearchModal 契约对齐与错误处理

- **位置:** `frontend/src/components/shared/SearchModal.tsx`
- **路径:** `apiPost<{ results: GlobalSearchResult[]; total: number }>("search", ...)`

### 契约对齐
- **API 路径:** `/api/search`（通过 `apiPost` 拼接）。检查 `docs/api-contract.md`：
  - §4.3 搜索接口定义为 `POST /api/search`，路径一致 ✅
  - 请求体应为 `{ query, limit?, filters? }`，代码发送 `{ query, limit: 20 }` ✅
  - 响应格式应为 `{ success, data: { results, total } }`，但代码直接从 `apiPost` 解构 `data?.results`。`apiPost` 已做 `unwrapResponse`，返回 `data` 字段。但代码中 `data?.results ?? []` 如果后端返回格式不符会静默失败 ❌

### 错误处理
- `catch` 块中 `setGlobalResults([])` — 错误被吞掉，用户看不到错误提示 ❌
- 无 loading indicator 在搜索期间（`globalSearching` 被设 true/false 但 UI 可能未使用）❓

### 边界情况
- 搜索词为纯空格：`query.trim()` 后为空，`useEffect` 中 `!query.trim()` 时 `setGlobalResults([])`，UI 显示空白 ✅（但无提示）
- 超长搜索词：无长度限制，后端可能返回 400 ❌
- 快速连续输入：`useEffect` 有 `timer` 防抖（`setTimeout`），但无 `AbortController`，前一个请求可能覆盖后一个结果 ⚠️

---

## [深度] AppRoot 面板系统竞态与边界

- **位置:** `frontend/src/components/layout/AppRoot.tsx`

### 布局边界
- **所有面板关闭时：** `leftPanelOpen=false`, `rightPanelOpen=false`, `activeNav="chat"` — 此时只剩 IconNav(48px) + ChatArea(满宽)，布局正常 ✅
- **快速切换 nav:** `showKnowledgePage`/`showBookmarkPage` 与 `showChatShell` 互斥，不会同时渲染两个内容区 ✅
- **SettingsPage 叠加层：** `{activeNav === "settings" && <SettingsPage />}` 在 chat 渲染之后，弹出在顶层 ✅

### Resizer 隐藏逻辑
- `sidebarResizer` 的 hidden 条件是 `showLeftRail`（= chat 且 leftPanelOpen）❌ — 但 resizer 实际上应该在 leftPanel 关闭时也保持显示，否则用户无法重新打开？不对——左侧有 `leftToggleBtn` 重新打开面板，所以 resizer 隐藏是安全的 ✅

### 竞态条件
- `usePanelResize` hook 通过 `startInfo` ref 存储拖拽起始位置，不依赖 state，所以不存在 setState 竞态 ✅
- 多个 `setWidth` 调用在 mousemove 中直接触发，无防抖——在动画帧中可能短时间内多次 setState。Zustand 的批处理 + React 18 自动批处理可应对 ✅

---

## [深度] useKeyboard 资源泄漏与竞态

- **位置:** `frontend/src/hooks/useKeyboard.ts`

### 资源泄漏
- `useEffect` 返回清理函数 `() => window.removeEventListener("keydown", handler)` ✅
- 依赖数组 `[actions]`：actions 引用变化时会重新绑定。`useDefaultKeyboardShortcuts()` 在 App() 中每次渲染都创建新数组，导致 useEffect 频繁重绑 ⚠️
- **建议修复:** 用 `useMemo` 或 `useRef` 稳定 actions 引用，或改用 ref 存储 handler。

### 快捷键冲突
- `Ctrl+,` 在浏览器中通常是"打开设置"（Chrome 默认是 `Ctrl+,` 无特殊功能），功能正常 ✅
- `Ctrl+N` 在浏览器中是"新建窗口"，被 `e.preventDefault()` 拦截后不会触发浏览器默认行为 ✅
- `Ctrl+K` 在某些浏览器中也会触发搜索栏，被拦截 ✅

### 边界情况
- 多个 `handler` 匹配时只执行第一个（因为 `return`）✅
- 大小写敏感：`a.key.toLowerCase() !== e.key.toLowerCase()` 不区分大小写 ✅
- Meta 键（Command/Mac）支持 ✅

---

## 扫描结论

| 优先级 | 数量 | 关键问题 |
|--------|------|----------|
| P0 | 0 | — |
| P1 | 4 | TopBar content 类型、chatFontSize 未应用、滚动条隐藏、ErrorBoundary 主题硬编码 |
| P2 | 5 | MainArea 死代码、IconNav 空按钮、SearchModal 空提示、TemplatePanel 交互、ContextMenu 索引 |

**最深发现：** `useKeyboard` 的 `actions` 依赖每次渲染都重建，导致 `useEffect` 频繁重新绑定事件监听。App.tsx 中 `useKeyboard(useDefaultKeyboardShortcuts())`，每次渲染 `useDefaultKeyboardShortcuts()` 返回新数组 → `useKeyboard` 的 `useEffect` 拆旧绑新 → 但 handler 内部逻辑正确，实际不影响功能，仅微性能损耗。
