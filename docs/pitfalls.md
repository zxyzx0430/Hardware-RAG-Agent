# Hardware RAG Agent 项目踩坑记录

这个文件记录项目推进中已经踩过、已经定位或修复的问题。每条记录尽量短，重点写清楚：错误现象、为什么错、怎么改、下次注意什么。

## 记录格式

```md
## YYYY-MM-DD - 简短标题

- 错误现象：
- 错误原因：
- 修复方式：
- 下次注意：
```

## 2026-06-19 -
`apply_patch` 被错误包装成 JSON 导致连续失败

- 错误现象：多次尝试用 `apply_patch` 新建 `docs/api-contract.md`，工具连续返回 `aborted`，文件没有写入。
- 错误原因：`apply_patch` 是 freeform 工具，应该接收原始补丁正文；实际调用时被包装成了 `{"input":"..."}` 形式，工具无法按补丁解析。
- 修复方式：停止重复同一种失败调用；本次为完成文档落地，临时使用 PowerShell here-string 写入纯文档文件，并把该问题记录为工具层踩坑。
- 下次注意：遇到工具格式问题最多重试 2 次；第二次失败后要切换排查路径，先确认工具 schema、调用通道和最小可复现样例，避免陷入重复失败循环。







## 2026-06-19 -
GitHub PR 状态为空不是认证失败

- 错误现象：用户无法获取拉取请求状态。
- 错误原因：`gh auth status` 显示 GitHub 登录正常，远端也正确指向 `zxyzx0430/Hardware-RAG-Agent`；`gh pr list --state all` 返回空数组，表示当前仓库没有任何 PR，而不是权限或网络错误。
- 修复方式：用 `gh auth status`、`git remote -v`、`gh pr list --repo zxyzx0430/Hardware-RAG-Agent --state all --json ...` 三步确认。
- 下次注意：看到空数组要先判断"真实为空"还是"查询失败"；CLI 退出码为 0 且返回 `[]` 时，优先解释为没有 PR。







## 2026-06-19 -
Zustand store 模板字符串语法被破坏导致全部功能失效

- 错误现象：日志显示功能、对话区域下方六个功能按钮（回到问题/收藏/复制/重试/引用/分支）、对话导出、MCP 启停按钮、技能开关等全部点击无响应。`vite build` 报 `Unterminated string literal` 错误。
- 错误原因：`useChatStore.ts` 中多处 ES 模板字符串（template literal）的语法被破坏——反引号丢失、`${}` 表达式丢失、`\n` 转义被替换为实际换行。例如 `lines.join("\n")` 变成了跨行字符串、`msg-${Date.now()}` 变成了 `msg-`、`branch-${Date.now()}` 变成了 `ranch-`、`exportConversation` 中的模板字符串完全破碎。esbuild 无法解析导致整个 store 模块加载失败，所有依赖该 store 的组件功能全部瘫痪。
- 修复方式：完整重写 `useChatStore.ts`，修复所有模板字符串语法。同时修复 `SettingsPage.tsx` 中日志"刷新/复制"按钮缺少 onClick、MCP 启停按钮缺少 onClick、技能开关缺少 onClick 的问题——在 `useSettingsStore` 中添加 `toggleSkill` 和 `toggleMcpServer` action。修复 `ChatArea.tsx` 中"修改"按钮缺少 onClick 的问题——添加编辑状态管理和 `editAndResend` 调用。
- 下次注意：1) 修改含模板字符串的文件时，务必在保存后执行 `vite build` 验证；2) tsc 不检查模板字符串内部语法，esbuild 才会报错，所以 `tsc --noEmit` 通过不代表运行时无问题；3) 按钮缺少 onClick 是常见遗漏，新组件完成后应逐个检查交互元素。







## 2026-06-19 -
React 版本全面功能缺失修复（15+ 组件）

- 错误现象：React 项目相比原始 HTML 缺失约 55 项功能、15 项功能损坏。核心问题包括：面板拖拽无效、右键菜单缺失、收藏夹无法跳转、全局搜索不存在、串口/烧录/安全检查按钮无响应等。
- 错误原因：从 HTML 到 React 的迁移过程中，大量交互逻辑未实现——hook 已编写但未连接（usePanelResize）、组件已编写但未使用（ContextMenu、Modal）、按钮缺少 onClick、store action 为空操作（selectSession）。
- 修复方式：
  1. **AppRoot**: 连接 usePanelResize hook，左右 resizer 可拖拽，面板切换按钮绑定 onClick
  2. **SessionPanel**: 接入 ContextMenu 实现右键菜单（置顶/重命名/删除/移至项目），组标题可折叠/展开，项目可删除/新建
  3. **BookmarkPanel**: 从硬编码 demo 数据改为使用 useChatStore 真实数据，点击跳转到消息，删除/新建文件夹
  4. **SearchModal**: 新建全局搜索组件，Ctrl+K 修复（改用 store 状态而非 DOM 操作）
  5. **HamburgerMenu**: 导出对话框支持 Markdown/JSON 格式选择
  6. **InputBar**: 模型列表从 API 动态获取（useQuery + fallback），Markdown 预览切换
  7. **WorkbenchPanel**: 串口发送/过滤/导出/DTR/RTS，烧录编译/烧录模拟，安全检查验证模拟，接线图 SVG + 缩放/平移 + BOM
  8. **TemplatePanel**: 新建模板面板组件，保存/删除/插入模板
  9. **SnapshotPanel**: 新建快照面板组件，保存/恢复/删除/对比 diff
  10. **useAppStore**: 添加 searchOpen/templatePanelOpen/snapshotPanelOpen 状态
  11. **useSettingsStore**: 添加 toggleSkill/toggleMcpServer action
- 下次注意：1) 迁移 HTML 到 React 时，必须逐个功能点对比，不能只迁移视觉；2) hook/组件写好了不代表接入了，必须检查是否在父组件中实际调用；3) store action 定义了不代表 UI 调用了，必须检查按钮 onClick 是否绑定。







## 2026-06-20 -
SSE 连接超时 10s 导致 "signal is aborted without reason"

- 错误现象：聊天发送消息后报错"连接失败: signal is aborted without reason"，LLM 响应时间超过 10 秒时必现。
- 错误原因：`apiSSE` 中 `connTimer = setTimeout(() => controller.abort(), 10_000)` 仅给 10 秒连接超时，但 LLM（尤其是思考模型）首次响应可能需要 30-60 秒，超时后 AbortController 直接中断连接。
- 修复方式：将连接超时从 10 秒增加到 60 秒（`60_000`），给 LLM 足够的思考时间。响应头返回后 `clearTimeout(connTimer)` 仍会清除定时器，不影响流式读取阶段。
- 下次注意：SSE 长连接场景下，连接超时要考虑 LLM 推理延迟（尤其是 o1/o3 等思考模型），10 秒远远不够；建议 60 秒起步，或根据模型类型动态调整。







## 2026-06-20 -
lazy() 加载样式对象导致代码块白屏崩溃

- 错误现象：AI 回复包含代码块时，整个页面变白/空白，无任何内容渲染。
- 错误原因：`oneDark` 样式定义用 `lazy()` 包装成了 React lazy 组件，但 `SyntaxHighlighter` 的 `style` prop 期望接收普通 JS 对象。`lazy()` 返回的是一个 React 组件包装器而非样式对象，传入后导致渲染崩溃。
- 修复方式：将 `oneDark` 改为静态 `import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"`，只保留 `SyntaxHighlighter` 组件本身的懒加载。样式对象体积很小（~5KB），静态导入不影响首屏性能。
- 下次注意：`React.lazy()` 只能用于 React 组件，不能用于普通 JS 对象/常量；样式定义、配置对象等小体积模块应直接静态导入，只有大型组件库才需要懒加载。







## 2026-06-20 -
CSS display:none 导致 React 右键菜单永远不可见

- 错误现象：右键点击历史会话没有任何反应，菜单不显示。
- 错误原因：原始 HTML 用 `.ctx-menu { display: none }` + `.ctx-menu.show { display: block }` 模式控制菜单显隐。迁移到 React 后，菜单通过条件渲染（`{ctxMenu && <ContextMenu />}`）控制可见性，但 CSS 中的 `display: none` 仍然生效，导致即使 React 渲染了菜单组件，它也永远不可见。
- 修复方式：移除 `.ctx-menu` 的 `display: none` 和 `.ctx-menu.show { display: block }` 规则，React 条件渲染已足够控制显隐。同时移除 `.ctx-submenu` 的 `display: none`。
- 下次注意：从 HTML 迁移到 React 时，CSS 中的 JS 风格显隐控制（display:none + class 切换）与 React 条件渲染会冲突；React 组件应只用条件渲染控制显隐，不要在 CSS 中设置 `display: none`。







## 2026-06-20 -
会话消息不独立：两个 store 重复存储 + 切换不同步

- 错误现象：切换到另一个会话后，之前会话的消息丢失；新建会话后不会自动切换过去；不同会话的消息混在一起。
- 错误原因：1) `useSessionStore` 和 `useChatStore` 都有 `sessionMessages` 字段，数据重复且不同步；2) `newSession` 创建会话后没有调用 `setActiveSession` 切换过去；3) `sendMessage` 中消息只在 `onDone` 时才保存到 `sessionMessages`，流式输出期间切换会话会丢失消息；4) 删除会话时没有清理 `useChatStore` 中的消息。
- 修复方式：1) 移除 `useSessionStore` 中的 `sessionMessages` 和 `setSessionMessages`，消息存储统一由 `useChatStore` 管理；2) `newSession` 末尾自动调用 `useChatStore.setActiveSession(localId)` 和 `useAppStore.setActiveSession(localId)`；3) `sendMessage` 的 `text`/`error` 事件处理中每次都同步更新 `sessionMessages`；4) `deleteSession` 同步清理 `useChatStore.sessionMessages` 并自动切换到下一个可用会话；5) `setActiveSession` 切换时重置流式状态。
- 下次注意：1) 避免在多个 store 中重复存储相同数据，选择单一数据源；2) 新建资源后要自动切换/选中；3) 流式输出期间的消息必须实时同步到持久化存储，不能只在完成时保存。







## 2026-06-20 -
会话数据不真实：静态字段 + 多处硬编码

- 错误现象：1) msgCount 永远是 0；2) preview 永远为空；3) 分组（今天/昨天/本周/更早）不准确；4) 新建项目后会话消失；5) 移至项目不调用后端API；6) 新建会话模型硬编码 "GPT-4o"；7) 项目硬编码 "嵌入式开发"。
- 错误原因：1) `msgCount`/`preview` 创建后从未更新；2) `group`/`timestamp` 是静态字符串，不根据实际时间动态计算；3) `createProject` 切换 `activeProject` 到空项目，过滤后无会话显示；4) `moveSessionToProject` 只更新本地不调后端；5) `newSession` 硬编码 model 和 project。
- 修复方式：1) Session 类型新增 `createdAt: number`（epoch ms），移除静态 `timestamp`/`group`/`createTime`（保留为可选兼容旧数据）；2) 新增 `getSessionGroup()`/`formatSessionTime()`/`formatCreateDate()` 工具函数动态计算；3) 新增 `updateSessionMeta()` action，在 `onDone` 时更新 msgCount/preview/title；4) `createProject` 不再切换 `activeProject`，避免会话消失；5) `moveSessionToProject` 增加后端 API 调用和"无项目"选项；6) `newSession` 从 `useSettingsStore` 读取当前 model，activeProject 为 "all" 时不指定 project；7) 旧数据通过 `migrateSession()` 自动迁移。
- 下次注意：1) 时间相关字段必须用 epoch 存储，显示时动态格式化，不能存静态字符串；2) 派生数据（msgCount、preview、group）不能只存不更新，应在数据变化时同步；3) 创建空容器（项目/文件夹）后不应自动切换过滤视图，否则用户会以为数据丢失；4) 新建资源时应从当前上下文读取配置（model、project），不要硬编码默认值。







## 2026-06-20 -
SSE 回调写入错误会话 + retry/edit 丢失消息

- 错误现象：1) 流式输出时切换会话再切回来，回答丢失；2) 点重试后整个会话消失；3) 编辑重发同样丢失；4) 分支功能完全坏掉；5) 停止流式不保存部分内容。
- 错误原因：1) **核心架构缺陷**：SSE 回调（onEvent/onDone/onError）使用 `s.activeSessionId` 获取目标会话，但用户切换会话后 activeSessionId 已变，导致流式内容写入错误会话；2) `retryMessage`/`editAndResend` 只更新 `messages` 不更新 `sessionMessages`，且不检查 `isStreaming`，流式中重试会导致 sendMessage 被拒绝而消息已被截断；3) `branchThread` 不创建会话、不保存消息；4) `stopStreaming` 不同步 `sessionMessages`。
- 修复方式：1) **SSE 回调捕获 requestSessionId**：在 `sendMessage` 时用闭包捕获 `activeSessionId`，回调中始终用此 ID 定位目标会话，而非 `s.activeSessionId`；2) 回调中判断 `s.activeSessionId === sid`，仅在仍在同一会话时更新 `messages`，否则只更新 `sessionMessages[sid]`；3) `retryMessage`/`editAndResend` 增加 `isStreaming` 守卫，截断消息后立即通过 `syncToSession()` 同步 `sessionMessages`；4) `setActiveSession` 切换时如正在流式，保存已输出内容并中止 SSE；5) `stopStreaming` 保存部分内容到 `sessionMessages`；6) `branchThread` 改为调用 `newSession()` 后设置分支消息。
- 下次注意：1) **异步回调绝不能依赖可变状态**（如 activeSessionId），必须在发起时捕获并闭包传递；2) 所有修改 `messages` 的操作必须同步更新 `sessionMessages`，否则切换会话时数据不一致；3) 流式输出期间的操作（retry/edit/switch）必须有守卫逻辑，不能假设 isStreaming 为 false；4) 异步操作（SSE、setTimeout）中操作 store 数据时，要考虑用户可能在等待期间切换了上下文。







## 2026-06-20 -
面板拖拽双渲染 + 多处细节功能缺陷

- 错误现象：1) 右侧栏拖拽卡顿不同比例；2) 推送到烧录不传代码；3) 流式时无法回看历史；4) 工具按钮空操作；5) JSON.parse 崩溃；6) onWheel passive 警告；7) 串口导出未过滤日志；8) 编辑框 Enter 不发送。
- 错误原因：1) `usePanelResize` 用本地 state + useEffect 同步 store，每次拖拽双渲染；2) `handlePushToFlash` 只切换 tab 不传代码；3) 自动滚动无暂停机制；4) 工具按钮未绑定 onClick；5) `JSON.parse(step.args)` 无 try-catch；6) React `onWheel` 默认 passive，`preventDefault()` 被忽略；7) 导出用 `log` 而非 `filteredLog`；8) 编辑 textarea 无 onKeyDown。
- 修复方式：1) 重写 `usePanelResize`：直接调 store setter，用 ref 捕获起始宽度，拖拽时设置 `document.body.style.cursor/userSelect`；2) 在 AppStore 新增 `flashCode` state，Preview 的 `handlePushToFlash` 调用 `setFlashCode(activeTab.code)`，Flash 面板读取 `flashCode || CODE`；3) ChatArea 新增 `userScrolledUp` ref，`onScroll` 检测距底部 <60px 时重置，自动滚动仅在 `!userScrolledUp` 时执行；4) 工具按钮绑定 `copyToClipboard` 和 `pushCodeToWorkbench`；5) JSON.parse 包裹 try-catch；6) 用 `useEffect` + `addEventListener('wheel', handler, { passive: false })` 替代 React onWheel；7) 导出改用 `filteredLog`；8) 编辑 textarea 添加 `onKeyDown`，Enter 保存发送。
- 下次注意：1) 拖拽类交互必须直接更新 store，不要本地 state + useEffect 同步；2) "推送到 X"功能必须传递数据，不能只切换视图；3) 自动滚动必须有暂停机制，用户向上滚动时不应强制拉回；4) 所有按钮必须绑定事件处理器，空按钮是功能缺陷；5) `JSON.parse` 必须有 try-catch 保护；6) React 的 `onWheel`/`onTouchMove` 是 passive 的，需要 `preventDefault()` 时必须用 `addEventListener` + `{ passive: false }`。







## 2026-06-20 -
filteredLog TDZ 错误导致白屏 + handleScroll 未定义

- 错误现象：打开页面白屏，控制台报 `Uncaught ReferenceError: Cannot access 'filteredLog' before initialization`。
- 错误原因：`WorkbenchPanel.tsx` 中 `handleExport`（第 204 行）引用了 `filteredLog`，但 `filteredLog` 的 `useMemo` 声明在 `handleExport` 之后（第 214 行），JavaScript 暂时性死区（TDZ）导致变量在声明前不可访问。同时 `ChatArea.tsx` 中 `onScroll={handleScroll}` 引用了未定义的 `handleScroll` 函数，也会导致运行时错误。
- 修复方式：1) 将 `filteredLog` 的 `useMemo` 声明移到 `handleExport` 之前；2) 移除 `ChatArea.tsx` 中未定义的 `handleScroll` 引用和未使用的 `userScrolledUp` ref。
- 下次注意：1) `useMemo`/`useCallback` 等钩子声明顺序很重要，被依赖的值必须先声明；2) `onScroll`/`onClick` 等事件处理器引用必须确保函数已定义，删除功能时要同步清理 JSX 中的引用。







## 2026-06-20 -
LLM reasoning_content 未提取 + 请求体结构不匹配 + lazy() 导出错误

- 错误现象：1) 思考卡片不显示模型的思考过程；2) 系统提示词和长期记忆未注入到 LLM 请求；3) `Element type is invalid: Received a promise that resolves to undefined` 白屏错误。
- 错误原因：1) 后端 `chat_stream` 只处理 `delta.content`，完全忽略了 `delta.reasoning_content`（DeepSeek-R1/QwQ）和 `delta.reasoning`（部分 OpenAI 兼容 API）；2) 前端请求体用 `{ messages, settings: { top_k, ... } }` 嵌套结构，但后端 `ChatRequest` 期望扁平结构 `{ messages, top_k, system_prompt, ... }`，导致 `system_prompt`/`long_term_memory` 等字段始终为 None；3) `react-syntax-highlighter/dist/esm/prism` 只有 `export default`，没有 named export `Prism`，`lazy(() => import(...).then(m => ({ default: m.Prism })))` 中 `m.Prism` 为 undefined。
- 修复方式：1) 后端 `chat_stream` 返回 `StreamChunk(type, content)` 而非纯字符串，提取 `reasoning_content`/`reasoning` 作为 thinking 类型；`routes.py` 根据 `chunk.type` 发送 `thinking`/`text` SSE 事件；2) 前端 `sendMessage` 请求体改为扁平结构，加入 `system_prompt`/`long_term_memory`/`model`/`max_tokens`；3) `lazy(() => import("react-syntax-highlighter/dist/esm/prism"))` 直接使用 default export，移除 `.then()` 包装；4) 前端 thinking 事件处理改为追加模式（如果最后一个 step 是 thinking 则追加内容），而非每次新建 step。
- 下次注意：1) OpenAI 兼容 API 的流式响应中，`delta` 对象可能包含 `reasoning_content`/`reasoning` 等非标准字段，需要用 `hasattr` 检查；2) 前后端请求体结构必须严格对齐，嵌套 vs 扁平是常见不匹配源；3) `React.lazy()` 的 `.then()` 回调必须返回 `{ default: Component }`，如果模块只有 default export 则不需要 `.then()` 包装；4) 流式推送的思考内容需要合并到同一个 step，不能每个 token 创建一个新 step。







## 2026-06-20 -
SSE AbortController 不一致导致切换会话后流式中止失败

- 错误现象：流式输出时切换到其他会话再切回来，输出暂停且无内容显示；控制台报 `signal is aborted without reason` 错误。
- 错误原因：1) `apiSSE` 内部创建了局部 `AbortController`，而 `useChatStore` 中的 `currentSseRequest` 是另一个独立的 `AbortController`，两者不是同一个。`setActiveSession` 调用 `currentSseRequest.abort()` 中止的是 store 中的 controller，但 fetch 请求绑定的是 `apiSSE` 内部的 controller，所以 abort 实际上没有中止 SSE 连接；2) 用户主动中止（切换会话/停止流式）触发了 `onError` 回调，导致显示"连接失败"错误消息；3) `streamingContent` 为空时（模型还在思考阶段），`&& streamingContent` 条件为 falsy，导致部分内容不被保存。
- 修复方式：1) `apiSSE` 新增 `externalController` 参数，`sendMessage` 把 store 中的 `AbortController` 传入，确保 abort 能真正中止 SSE 连接；2) `apiSSE` 的 catch 块中检查 `controller.signal.aborted`，如果是用户主动中止则静默返回，不触发 `onError`；3) `setActiveSession` 中保存部分内容时，不再要求 `streamingContent` 非空，即使只有思考步骤没有文本也保存。
- 下次注意：1) `AbortController` 必须是同一个实例才能中止请求，`apiSSE` 不应内部创建独立的 controller；2) 用户主动中止和真正的网络错误必须区分处理，`signal.aborted` 是区分标志；3) 流式输出中断时，即使没有文本内容（只有思考步骤），也要保存部分结果。







## 2026-06-20 -
思考卡片不显示 + Source 事件捕获 Bug + thinking step 合并错误

- 错误现象：1) 使用普通模型（GPT-4o/Claude等）时看不到思考卡片；2) RAG 来源永远不显示；3) RAG thinking 和 LLM reasoning 被合并成同一个 step。
- 错误原因：1) 普通模型不返回 `reasoning_content`，后端只发 `text` 事件不发 `thinking` 事件，`streamingSteps` 为空所以卡片不出现；2) 后端发送单个 source 对象 `{"type":"source","id":"src1",...}`，但前端读 `sse.sources`（数组），永远是 undefined → 空数组；3) thinking step 合并逻辑只看 type 是否为 thinking，不看 source 来源，导致 RAG 的"正在检索知识库..."和 LLM 的 reasoning_content 被合并。
- 修复方式：1) 后端在 LLM 流式输出前主动发 `thinking({"content":"正在生成回答...","source":"llm"})` 事件，确保普通模型也有思考卡片；2) 后端所有 thinking 事件加 `source` 字段区分来源（rag/llm/reasoning）；3) 前端 thinking step 合并逻辑改为只在 source 相同时合并；4) 前端 source 事件改为累积单个 source 对象而非读取 `sse.sources` 数组；5) `onDone` 中只在 `streamingSteps.length > 0` 时才设置 `activity`；6) ActivityBlock 渲染条件加 `steps.length > 0` 判断。
- 下次注意：1) 普通模型不会返回 reasoning_content，需要后端主动发 thinking 事件模拟思考状态；2) SSE 事件的数据结构必须前后端严格对齐，后端发单个对象时前端不能读数组属性；3) thinking step 合并逻辑必须考虑来源（source），不同来源的 thinking 应该是独立的 step。







## 2026-06-20 -
推理模型思考内容不显示 + Ollama 兼容性

- 错误现象：使用推理模型（DeepSeek-R1/QwQ）时，reasoning_content 不显示在思考卡片中。
- 错误原因：1) 后端只检查了 `delta.reasoning_content` 和 `delta.reasoning`，没有检查 Ollama 使用的 `delta.thinking` 字段；2) Ollama 的推理模型需要设置 `think: true` 参数才能返回思考内容；3) "正在生成回答..."占位 step 和 reasoning_content 的 thinking step 没有正确替换，导致出现两个 thinking step。
- 修复方式：1) 后端 `chat_stream` 新增对 `delta.thinking` 字段的检查（Ollama 兼容）；2) 检测 Ollama base_url 时自动添加 `extra_body={"think": True}` 参数；3) 前端 thinking step 合并逻辑：当收到 `source="reasoning"` 的事件时，如果上一个 step 是 `source="llm"` 的占位 step，则替换而非新建；4) ThinkingStep 组件根据 source 显示不同样式和标签（推理思考/知识库检索/思考中），推理思考默认展开。
- 下次注意：1) 不同 LLM 提供商的推理字段名不同：DeepSeek 用 `reasoning_content`，部分 OpenAI 兼容 API 用 `reasoning`，Ollama 用 `thinking`；2) Ollama 需要显式启用 `think: true` 参数；3) 推理思考应该替换"正在生成回答..."占位 step，而不是新建一个独立的 step。







## 2026-06-20 -
Vite 代理端口配置错误导致前端无法连接后端

- 错误现象：前端发送消息后看不到推理思考卡片，甚至可能完全无法与后端通信。
- 错误原因：`vite.config.ts` 中代理配置指向 `http://127.0.0.1:58080`，但后端实际运行在 `http://127.0.0.1:8000`。所有 `/api` 请求都被代理到了错误的端口，前端根本无法与后端通信。
- 修复方式：将 `vite.config.ts` 中的 `target` 从 `http://127.0.0.1:58080` 改为 `http://127.0.0.1:8000`。
- 下次注意：1) 修改后端端口后必须同步更新 `vite.config.ts` 的代理配置；2) 前端无法连接后端时，首先检查 Vite 代理配置和后端端口是否一致；3) 可以用 `netstat -ano | findstr :端口号` 确认后端实际监听的端口。







## 2026-06-20 -
api-contract.md 为 CRLF 行尾导致 Edit 工具匹配失败

- 错误现象：尝试用 `Edit` 工具修改 `docs/api-contract.md` 时，提示 `String to replace not found in file`，但肉眼核对文本完全一致。
- 错误原因：该文件实际使用 CRLF（`\r\n`）行尾，而 `Edit` 工具按 LF（`\n`）匹配；同时 `Read` 工具显示时会将 CRLF 渲染为普通换行，造成"内容一致"的错觉。
- 修复方式：先用 Python 脚本读取文件内容并执行 `.replace('\r\n', '\n')` 统一为 LF 后再写入；后续即可正常使用 `Edit` 工具。
- 下次注意：1) 在 Windows 仓库中编辑 `.md` 等文本文件前，先确认行尾格式；2) 如果 `Edit` 连续失败，用 `python -c "print(b'\\r' in Path(path).read_bytes())"` 快速检查；3) 项目文档统一维护为 LF，避免跨工具协作时产生匹配问题。







## 2026-06-20 -
CORS 跨域 + 模型列表请求无限重试死循环

- 错误现象：控制台大量 CORS 报错 `Access-Control-Allow-Origin`，前端页面完全瘫痪，模型列表请求无限重试。
- 错误原因：1) `SettingsPage.tsx` 和 `InputBar.tsx` 中的 `useQuery` 在后端代理失败后，直接用 `fetch` 请求远程 API（如 `https://9router.zxyzx.bbroot.com/v1/models`），浏览器同源策略拦截跨域请求；2) CORS 请求失败后，React Query 默认重试 3 次，加上 `queryKey` 依赖 `resolvedBaseUrl`/`activeProvider`，可能导致无限重试循环。
- 修复方式：1) 移除所有直接请求远程 API 的 fallback 代码，模型列表请求只走后端代理（`/api/models`）；2) 给 `useQuery` 添加 `retry: 1` 限制重试次数；3) 后端已有 `/api/models` 端点代理请求，不存在 CORS 问题。
- 下次注意：1) 前端永远不要直接请求第三方 API，所有请求都应通过后端代理或 Vite proxy 转发；2) React Query 的 `useQuery` 默认重试 3 次，对于可能因 CORS 失败的请求必须限制 `retry`；3) `useEffect` 或 `useQuery` 的依赖项必须正确，避免状态更新触发无限重渲染/重请求。







## 2026-06-20 -
修改 store action 签名后连带类型错误

- 错误现象：修复 `useChatStore.stopStreaming` 使其支持 `errorMessage?: string` 参数后，`npx tsc --noEmit` 报两处新错误：1) `InputBar.tsx` 中 `onClick={stopStreaming}` 类型不兼容，因为 `stopStreaming` 不再是 `() => void`；2) `useChatStore.ts` 中多处 `loadFromStorage("bookmarkData", ...)` / `saveToStorage("bookmarkData", ...)` 报错，因为 `persistence.ts` 的 `KEYS` 里没有 `bookmarkData`。
- 错误原因：1) 修改函数签名后，没有同步检查所有调用点，直接把带可选参数的函数传给了 React 的 `MouseEventHandler`；2) `bookmarkData`  persistence key 之前遗漏，代码里却已经在使用，只是之前可能没触发严格类型检查或被忽略。
- 修复方式：1) `InputBar.tsx` 改为 `onClick={() => stopStreaming()}`，显式无参调用；2) 在 `src/utils/persistence.ts` 的 `KEYS` 中添加 `bookmarkData: "hwrag_bookmark_data"`。
- 下次注意：1) 修改公共函数/ action 签名后，必须全局搜索调用点并验证 tsc；2) `loadFromStorage` / `saveToStorage` 的 key 必须在 `KEYS` 中显式注册，否则新增存储字段时会触发类型错误；3) 运行 `tsc --noEmit` 时要关注“本次改动引入”的新错误，区分于历史遗留错误。







## 2026-06-20 -
接口字段与契约不一致

- **现象**：联调 `/api/chat` 时发现 `system_prompt` / `long_term_memory` 等字段没有生效；后端收不到，前端以为已发送。类似地，`/api/models` 错误码、`/api/wiring` 响应结构在前后端理解不一致。
- **原因**：前端请求体使用了嵌套结构 `{ messages, settings: { top_k, system_prompt, ... } }`，而后端 `ChatRequest` Pydantic 模型期望扁平结构 `{ messages, top_k, system_prompt, ... }`；接口变更后没有及时先更新 `docs/api-contract.md`，导致双方各自按旧假设编码。
- **修复**：约定 "先改 `docs/api-contract.md`，再对齐代码"。将 `/api/chat` 请求体改为扁平结构，加入 `system_prompt` / `long_term_memory` / `model` / `max_tokens`；同步更新接口目录、字段说明、Mock 规则和变更日志，最后再修改前后端实现。
- **下次注意**：新增或修改接口时，必须先在 `docs/api-contract.md` 中登记并推进状态到 `agreed`；联调不一致时，先更新文档再改代码，禁止双方口头约定字段格式。







## 2026-06-20 -
前端硬件功能 fallback 模拟成功

- **现象**：点击"编译"、"烧录"或"安全检查"按钮后，界面显示"编译成功"、"烧录成功"等绿色状态，但后端实际未调用，串口/设备没有任何反应；用户误以为功能已完成，延误问题定位。
- **原因**：硬件工作台早期为了快速验证 UI，在 `setTimeout` 中模拟了成功响应；随着后端 `/api/build`、`/api/upload`、`/api/audit_pins`、`/api/diagnose` 已实现，这些模拟代码仍残留在组件中，覆盖了真实 API 调用路径。
- **修复**：移除所有 `setTimeout` 模拟逻辑，改为直接调用 `apiSSE` / `apiPost`；失败时通过日志面板和错误提示展示真实后端错误信息，不再伪造成功状态。
- **下次注意**：任何临时 mock / 模拟代码必须标注 TODO 并设置清理时间点；功能联调前要通过浏览器 Network 面板确认确实发出了真实请求，不能仅凭 UI 状态判断成功。







## 2026-06-20 -
FastAPI HTTPException detail 被包装在 `detail` 字段下

- **现象**：测试 `/api/kb/upload` 超大文件时，断言 `response.json()["success"]` 报 `KeyError: 'success'`。
- **原因**：路由里 `raise HTTPException(status_code=400, detail={"success": False, "error": {...}})`，但 Starlette/FastAPI 默认会把 `exc.detail` 再包一层返回为 `{"detail": <detail>}`，导致前端/测试看到的是 `{"detail": {"success": False, ...}}`。
- **修复**：测试端改为读取 `response.json()["detail"]["success"]`；如需统一 API 契约，应自定义 HTTPException 处理程序或改用 `JSONResponse(status_code=400, content={...})` 直接返回。
- **下次注意**：`HTTPException(detail=...)` 的 detail 会被包装成响应体的 `detail` 字段；如果要让响应体与正常成功响应保持同样的 `{success, data/error}` 结构，不要用 HTTPException，直接返回 `JSONResponse` 或自定义异常处理器。







## 2026-06-20 -
Mock 异步流式函数时产生 `coroutine was never awaited` 警告

- **现象**：用 `unittest.mock.patch` 替换 `LLMClient.chat_stream` 为 `async def fake_stream(...): raise RuntimeError(...)` 后，测试能通过但 pytest 报 `RuntimeWarning: coroutine 'fake_stream' was never awaited`。
- **原因**：不含 `yield` 的 `async def` 是协程函数，调用后返回的是 coroutine 对象；`async for` 在协程直接抛异常时，Python 的 coroutine 跟踪机制认为该协程未被正常 await 完毕。
- **修复**：在 `fake_stream` 中加入至少一条 `yield` 语句，使其成为异步生成器函数；调用后返回的是 async generator 对象，`async for` 可以正常迭代并消费异常。
- **下次注意**：mock 流式接口时，优先让 fake 函数成为 async generator（含 `yield`），而不是普通 coroutine；如果确实要模拟立即失败，可以让它 yield 一个空 chunk 后再 raise。







## 2026-06-21 -
安全基线改造：API Key 加密存储 + XSS 防护 + 异常脱敏

- **现象**：API Key 明文存储在前端 localStorage 和 HTTP Header 中传输，存在 XSS 窃取和中间人攻击风险；`dangerouslySetInnerHTML` 渲染未经消毒的 HTML；异常信息中可能泄露 API Key。
- **原因**：1) 前端 `getAuthHeaders()` 将 API Key 放入 `X-API-Key` Header 明文传输；2) `InputBar.tsx` 中 `dangerouslySetInnerHTML={{ __html: previewHtml }}` 未消毒；3) `routes.py` 中多处 `details: str(e)` 直接暴露异常原文，可能包含 API Key。
- **修复**：1) 新建 `backend/app/api/auth.py`，使用 Fernet 对称加密存储 API Key，前端保存时调用 `/api/auth/store-key` 获取 session_token，后续请求通过 `Authorization: Bearer {token}` 认证；2) 前端 `client.ts` 移除 `X-API-Key` Header，改为从 localStorage 读取 session_token；3) `InputBar.tsx` 中 `dangerouslySetInnerHTML` 使用 `DOMPurify.sanitize()` 消毒；4) `routes.py` 中所有 `details: str(e)` 改为 `details: _sanitize_error(str(e))`，`message` 中包含异常的也统一脱敏；5) `.gitignore` 添加 `backend/db/.enc_key` 和 `backend/db/keys_store.json`。
- **下次注意**：1) 敏感凭证（API Key、Token）不应在前端 localStorage 明文存储或通过 HTTP Header 明文传输；2) 所有 `dangerouslySetInnerHTML` 渲染的 HTML 必须经过 DOMPurify 消毒；3) 异常信息返回给前端前必须脱敏，`_sanitize_error` 应覆盖所有 `details` 和包含异常的 `message` 字段。







## 2026-06-21 -
代码质量修复（Task 10-17）踩坑汇总

- **现象**：多个代码质量问题：SSE 畸形 JSON 静默丢弃、localStorage 满时无提示、消息 ID 用 Date.now() 不唯一、数据库会话手动 try/finally 管理易遗漏、HardwareVectorStore 重复实例化、LLMClient 重试逻辑对编程错误也重试、useSessionStore 直接用 fetch 不走统一封装、branchThread 竞态条件。
- **原因**：1) SSE 解析 `catch {}` 静默跳过畸形数据，无法诊断网络问题；2) `saveToStorage` catch 块吞掉 QuotaExceededError；3) `msg-${Date.now()}` 在快速连续操作时可能重复；4) `db = SessionLocal() try/finally: db.close()` 不自动 commit/rollback；5) 每次 API 调用都 `HardwareVectorStore()` 重新加载 ChromaDB；6) `_with_retries` 对 KeyError/TypeError 等编程错误也重试；7) `useSessionStore` 中 `fetch` 不带 auth headers；8) `branchThread` 先 `newSession()` 再读 `activeSessionId`，存在竞态。
- **修复**：1) SSE catch 中计数并 warn 日志，连续 3 次触发 onError；2) `saveToStorage` 检测 QuotaExceededError 并 dispatch CustomEvent；3) 消息 ID 改用 `crypto.randomUUID()`；4) 新增 `get_db_ctx()` 上下文管理器自动 commit/rollback/close；5) 新增 `get_vector_store()` 单例函数；6) `_with_retries` 对 KeyError/AttributeError/TypeError/ValueError 直接抛出，仅重试网络/速率/5xx 错误；7) 新增 `apiPut`/`apiDelete`，useSessionStore 统一使用；8) `branchThread` 先准备数据再一次性 set，最后调 `newSession()`；9) sessionMessages 分片存储（`hwrag_msg_{sid}`），debounce 持久化避免 SSE 期间频繁写入。
- **下次注意**：1) catch 块不能静默丢弃错误，至少要日志记录；2) localStorage 操作必须处理 QuotaExceededError；3) 消息 ID 不能用 Date.now()，必须用 UUID；4) 数据库会话用上下文管理器，不要手动 try/finally；5) 重试逻辑必须区分可恢复/不可恢复异常；6) 前端 API 调用统一走封装函数，不要裸用 fetch；7) 异步操作中的状态读取要考虑竞态，先准备数据再修改状态。







## 2026-06-21 -
Agent 通用功能补强（Task 18-22）踩坑汇总

- **现象**：实现对话摘要自动生成、键盘快捷键扩展、工具参数 Schema 校验与超时控制、消息反馈机制、跨会话搜索时遇到的问题。
- **原因**：1) `_build_messages` 返回值从 `List[Dict]` 改为 `tuple[List, list]` 后，`chat()` 和 `chat_stream()` 中的调用未同步更新；2) `tool_router.py` 的 `_REGISTRY` 从 `dict[str, ToolHandler]` 改为 `dict[str, dict[str, Any]]` 后，`routes.py` 中 `payload.tool not in TOOL_REGISTRY` 仍可用（只检查 key），但 `dispatch` 内部需改为 `entry["fn"]` 取处理器；3) `useAppStore` 接口中 `searchOpen` 字段缺失（只有 `setSearchOpen`），导致 SearchModal TS 报错；4) `branchTreeOpen`/`setBranchTreeOpen` 在接口中声明但 store 实现中缺失；5) ChatArea.tsx 中 `useSessionStore` 未导入但被使用。
- **修复**：1) 同步更新 `chat()` 和 `chat_stream()` 中对 `_build_messages` 的解构调用，添加摘要生成逻辑；2) `dispatch` 改为从 `entry["fn"]` 取处理器，添加参数 Schema 校验和 `asyncio.wait_for` 超时控制；3) 在 `AppState` 接口中添加 `searchOpen: boolean`；4) 在 store 实现中添加 `branchTreeOpen: false` 和 `setBranchTreeOpen` action；5) ChatArea.tsx 添加 `import { useSessionStore }` 导入。
- 下次注意：1) 修改函数返回值类型后必须全局搜索所有调用点并同步更新；2) 修改 store 数据结构后，接口定义和实现必须同步；3) Zustand store 的接口（interface）和实现（create）必须完全匹配，缺失字段会导致 TS 报错；4) 组件中使用外部 store 时必须确保 import 已添加。







## 2026-06-21 -
branchThread 会话 ID 不一致 + 后端 CRUD 不返回分支字段

- **现象**：点击"分支"按钮后，新会话被创建但分支信息丢失，BranchTree 组件无法构建分支树。
- **原因**：1) `branchThread` 先用 `Date.now()` 生成 `newSessionId`，然后调用 `useSessionStore.newSession()` 创建新会话，但 `newSession()` 内部也用 `Date.now()` 生成自己的 `localId`，两个 ID 不一致，导致消息数据设置到了不存在的会话 ID 上；2) `updateSessionMeta` 的类型 `Partial<Pick<Session, "msgCount" | "preview" | "title">>` 不包含 `branchFromSessionId`/`branchFromMessageId`，只能用 `as any` 绕过；3) 后端 `crud.py` 的 `list_sessions`/`create_session`/`update_session` 不返回和处理 `branch_from_session_id`/`branch_from_message_id` 字段，前端 `initSessions` 也不映射这些字段。
- **修复**：1) 重写 `branchThread`：直接通过 `apiPost("sessions", { ..., branch_from_session_id, branch_from_message_id })` 创建带分支信息的新会话，拿到后端返回的 `sid` 后再设置消息数据和切换会话，回退时纯本地创建；2) `updateSessionMeta` 类型扩展为 `Partial<Pick<Session, "msgCount" | "preview" | "title" | "branchFromSessionId" | "branchFromMessageId">>`；3) 后端 `SessionCreate`/`SessionUpdate` Pydantic 模型添加 `branch_from_session_id`/`branch_from_message_id` 可选字段，`list_sessions`/`create_session` 响应中包含这些字段，`update_session` 支持更新这些字段；4) 前端 `initSessions` 映射 `s.branch_from_session_id` → `branchFromSessionId` 和 `s.branch_from_message_id` → `branchFromMessageId`。
- **下次注意**：1) 异步创建资源时，不要假设本地生成的 ID 与后端返回的 ID 一致，应以后端返回为准；2) 新增数据库字段后，CRUD 的 Pydantic 模型、响应序列化、前端映射三处必须同步更新；3) `updateSessionMeta` 等通用更新函数的类型应覆盖所有可更新字段，不要用 `as any` 绕过。






## 2026-06-21 -
全项目 Review 发现的系统性踩坑（共 19 项 P0）

- **现象**：对 01-08 全部线程做代码 review 后，发现 19 项 P0 阻断级问题、51 项 P1、71 项 P2、42 项 P3。详见 `docs/review-result.md`。
- **原因（按共性归类）**：
  1. **鉴权形同虚设**：`backend/app/api/auth.py` 的 `get_provider_key_by_session(token)` 定义但从未被任何路由 `Depends`，所有 `/api/sessions`、`/api/kb/*`、`/api/sandbox/*`、`/api/wiring`、`/api/devices` 路由均无鉴权。
  2. **响应格式违反契约**：`backend/app/api/crud.py` 所有路由返回裸对象（如 `{"sessions": [...]}`），违反契约 `{success, data}` 格式。前端 `unwrapResponse` 用 hack `if (!("success" in json)) return json as T` 兼容，掩盖了问题。
  3. **死代码与 mock 残留**：`backend/app/api_router.py`（完整 mock 路由）、`backend/app/kb/translation_pipeline.py`（完整类从未调用）、`/api/build`/`/api/upload`/`/api/audit_pins`（stub 返回硬编码）。
  4. **`backend/main.py` mock `create_app()` 影子覆盖**：入口文件曾有自己的 mock `create_app()` 返回空数组，shadowed 真实 `app/main.py` 的 `create_app()`，导致所有真实路由从未注册，所有请求 404 或返回 mock 数据。已修复为委托模式。
  5. **`useSSE` 的 `externalController` 死代码**：`apiSSE` 接受 `externalController` 参数但内部仍创建新 `AbortController`，外部传入的从未被使用，导致取消请求无效。
  6. **多模态 RAG `images` 字段未透传**：`/api/chat` 走 RAG 时 `images` 字段未透传给 LLM，后端 `KeyError` 崩溃。
  7. **MCP `handler.run` AttributeError**：`agent/handler.py` 调用 `handler.run(input)`，但 `MCPClient` 无 `run` 方法，工具永远调不通。
  8. **sandbox C/C++ 代码未传入容器**：`sandbox/runner.py` 的 `run_python` 从未通过 stdin 把代码传入容器，`compile` 阶段永远失败。
  9. **`asyncio.run` 在已有事件循环中崩溃**：`sandbox/runner.py` 用 `asyncio.run(self._run_container())`，在 FastAPI 已有事件循环中抛 `RuntimeError: This event loop is already running`。
  10. **`.gitignore` 路径错位**：加密密钥实际在 `backend/app/db/.enc_key` 与 `backend/app/db/keys_store.json`，但 `.gitignore` 写的是 `backend/db/.enc_key`，密钥文件可能被提交。
  11. **原生 SQL `LIKE` 拼接 + 缺 FTS 表**：`crud.py` 的 `/api/sessions/search` 用 `f"%{q}%"` 直接拼接，存在 SQL 注入；且 FTS 虚拟表未创建，查询会崩。
  12. **`apiWS` 硬编码端口**：`frontend/src/api/client.ts` 的 `apiWS` 硬编码 `ws://localhost:8000`，与契约 `ws://127.0.0.1:8000` 不符，生产环境必崩。
  13. **`requirements.txt` 缺依赖**：缺少 `aiofiles`、`python-multipart`、`httpx`、`PyYAML` 等运行时依赖，新环境部署必崩。
  14. **WebSocket 鉴权造假**：`/api/monitor/{port}` 在 `websocket.accept()` 后未校验 token，任何人可连接串口。
  15. **`/api/diagnose` 编译检查硬编码 PASS**：从未真正诊断，永远返回 PASS。
  16. **Arduino 编译器路径硬编码**：`/opt/arduino/arduino-cli` 在容器内不存在。
  17. **CORS 配置死代码**：`main.py` 历史 mock 中的 CORS 配置已失效，实际生效的是 `app/main.py` 的开发态宽松配置 `allow_origins=["*"]`。
  18. **`TranslationPipeline` 死代码**：完整类定义但从未被任何路由调用。
  19. **`/api/audit_pins` 返回硬编码**：`{"conflicts": []}` 从未真正检查引脚冲突。
- **修复方案**：详见 `docs/review-result.md` 的 Phase 1（P0 阻断项）清单。核心修复模式：
  - 鉴权：新建 `backend/app/api/dependencies.py` 的 `current_user` 依赖，注入到所有受保护路由
  - 响应格式：统一用 `{"success": True, "data": ...}` 包装
  - 死代码：删除或标注 `# TODO: implement`
  - `useSSE`：优先使用 `externalController`，仅在其为空时创建内部 controller
  - `asyncio.run`：改为 `await self._run_container()`
  - SQL 注入：用参数化查询 `WHERE title LIKE :q`
- **下次注意**：
  1) 加密/鉴权相关函数定义后，必须用 `grep` 全局搜索调用点，确认至少有一个路由 `Depends` 了它
  2) 契约规定的响应格式必须严格遵守，前端不能用 hack 兼容后端的违规
  3) mock/ stub 代码必须标注 `# TODO: implement` 并设置清理时间点
  4) 入口文件（`main.py`）不应有自己的 `create_app()` 实现，应委托给 `app/main.py`
  5) `AbortController` 参数必须真正被使用，否则取消功能失效
  6) `asyncio.run` 不能在已有事件循环中调用，FastAPI 路由中应直接 `await`
  7) `.gitignore` 路径必须与实际敏感文件位置核对
  8) 原生 SQL 必须用参数化查询，不能用 f-string 拼接
  9) WebSocket 必须在 `accept()` 前校验 token
  10) `requirements.txt` 必须用 `pip freeze` 生成，不能手写

## 2026-06-21 - main.py 入口使用 mock create_app 导致所有真实路由 404/500

- **现象**：前端请求 `/api/sessions`、`/api/auth/keys`、`/api/settings` 等端点返回 404 或 mock 空数据，所有 CRUD/auth/sandbox/MCP 路由均未注册。
- **原因**：`backend/main.py` 有自己的 `create_app()` 包含硬编码 mock 路由（`/api/sessions` 返回空列表等），而真实路由在 `app/main.py` 的 `create_app()` 中。`python main.py --web` 调用的是旧版 mock 的 `create_app()`，导致 crud.py/auth.py/sandbox_routes.py/mcp_routes.py 等路由全部未注册。
- **修复**：将 `main.py` 的 `create_app()` 改为委托给 `app.main.create_app()`，`uvicorn.run` 改为 `uvicorn.run("app.main:app", ...)` 字符串方式启动。
- **下次注意**：入口文件不应有自己的路由实现，必须委托给 `app/main.py`；修改路由后必须重启服务器验证。

## 2026-06-21 - document_processor.py 的 `import logging` 被误放在 docstring 内导致 NameError

- **现象**：`from app.main import create_app` 报 `NameError: name 'logging' is not defined`，服务器无法启动。
- **原因**：`document_processor.py` 的 `import logging` 语句被错误地放在了模块 docstring 三引号内部（第 4 行），Python 将其视为字符串内容而非 import 语句。第 78 行 `logger = logging.getLogger(__name__)` 引用了未导入的 `logging`。
- **修复**：将 `import logging` 从 docstring 内移出，放在 docstring 结束后的正常 import 区域。
- **下次注意**：修改文件头部时注意 docstring 三引号的闭合位置，确保 import 语句不在 docstring 内；服务器启动失败时优先检查 import 错误。

## 2026-06-21 - Windows GBK 终端无法输出 emoji 导致 UnicodeEncodeError 服务器崩溃

- **现象**：`python main.py --web` 启动时崩溃，报 `UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f310'`。
- **原因**：`main.py` 和 `app/main.py` 的 print 语句包含 emoji（🌐📖），Windows 默认终端编码为 GBK，无法编码 emoji 字符。
- **修复**：将所有 print 中的 emoji 替换为纯文本前缀（如 `[Web]`）。
- **下次注意**：Python print 输出在 Windows 终端中必须避免 emoji 和非 GBK 字符；如需使用，设置 `PYTHONIOENCODING=utf-8` 或 `PYTHONUTF8=1` 环境变量。

## 2026-06-21 - P0 修复踩坑

- **现象**：全项目 review 后修复 19 项 P0 阻断级问题，覆盖鉴权形同虚设、响应格式违反契约、RAG 字段未透传、MCP 工具调不通、沙箱 C/C++ 代码无法传入容器、async 事件循环冲突、硬件工作台接口全为 mock、依赖缺失、死代码残留、密钥路径错位、FTS 表缺失、WebSocket 鉴权造假等。
- **原因**：详见 `docs/review-result.md` 的 Phase 1 清单。根本原因是早期为快速验证 UI 写了大量 mock/stub，后续未清理；鉴权函数定义后未接入路由；契约规定的响应格式未被严格遵守；`asyncio.run` 误用在已有事件循环中；Docker 容器挂载 `/tmp` 读写有逃逸风险；C/C++ 代码未通过 stdin 传入容器。
- **修复方式**：见 `docs/completed.md` 的 "P0 修复记录（2026-06-21）" 章节。核心修复模式：
  - 鉴权：新建 `backend/app/api/dependencies.py` 的 `current_user` 依赖，注入到所有受保护路由
  - 响应格式：统一用 `{"success": True, "data": ...}` 包装
  - RAG 透传：`store.search` 前提取文本，`images` 字段透传给 LLM
  - MCP 调用：`tool_router.py` dispatch 兼容 plain function
  - 沙箱执行：C/C++ 代码通过 stdin 传入容器，async 阻塞用 `asyncio.to_thread`
  - 硬件工作台：`/api/audit_pins`、`/api/build`、`/api/upload`、`/api/diagnose`、`/api/monitor/{port}` 全部接入真实逻辑
  - 依赖管理：`requirements.txt` 补全 10 个运行时依赖
  - 死代码：删除 `backend/app/api_router.py`
  - 密钥安全：`.gitignore` 修正密钥路径
  - FTS 检索：`database.py` init_db 创建 FTS5 虚拟表 + 触发器
  - WebSocket 鉴权：`ws_auth` 在 `accept()` 前校验 token
- **下次注意**：
  1) 鉴权函数定义后必须用 grep 确认至少有一个路由 Depends 了它
  2) 契约规定的响应格式必须严格遵守，前端不能用 hack 兼容
  3) mock/stub 代码必须标注 `# TODO: implement` 并设置清理时间点
  4) `asyncio.run` 不能在已有事件循环中调用，FastAPI 路由中应直接 `await` 或用 `asyncio.to_thread`
  5) Docker 容器挂载 `/tmp` 读写有逃逸风险，改用 `tmpfs`
  6) C/C++ 代码必须通过 stdin 或 volume 传入容器，不能只靠 `cat` 命令
  7) WebSocket 必须在 `accept()` 前校验 token
  8) `requirements.txt` 必须用 `pip freeze` 生成，不能手写

## 2026-06-21 - routes.py 使用 threading.Lock() 未 import threading 导致启动崩溃

- **错误现象**：运行 `python -c "from app.main import create_app; app = create_app()"` 验证后端启动时，报 `NameError: name 'threading' is not defined`，定位到 `app/api/routes.py` line 82 的 `_vector_store_lock = threading.Lock()`。
- **错误原因**：`routes.py` 中 `get_vector_store()` 改造为线程安全双重检查锁定模式时，新增了 `_vector_store_lock = threading.Lock()`，但 import 区只导入了 `asyncio`，遗漏了 `import threading`。
- **修复方式**：在 `routes.py` import 区添加 `import threading`。
- **下次注意**：1) 使用任何标准库模块前必须确认已 import，特别是 `threading`/`asyncio`/`os` 等容易遗漏的；2) 改造单例为线程安全模式时，同步检查 import 列表；3) 后端启动验证脚本应作为提交前必跑项，可立即暴露此类 NameError。

## 2026-06-21 - os.chmod 在 Windows 上不支持导致三个接口全部 500

- **错误现象**：`/api/models`、`/api/sessions`、`/api/devices` 三个接口全部返回 500 Internal Server Error，前端 TypeError 崩溃。
- **错误原因**：
  1. `auth.py` 的 `_get_fernet()` 中 `os.chmod(ENCRYPTION_KEY_PATH, 0o600)` 在 Windows 上抛 `OSError`（Windows 不支持 Unix 权限模式），导致加密密钥初始化崩溃
  2. `_load_store()` 依赖 `_get_fernet()` 解密，初始化崩溃后所有鉴权逻辑崩
  3. `current_user` 依赖调用 `_load_store()`，异常未捕获，直接 500
  4. 所有注入 `Depends(current_user)` 的路由（sessions/devices/wiring/diagnose）全部 500
  5. `/api/models` 只捕获 `LLMError`，`LLMClient.__init__` 等异常直接 500
  6. 前端 `useQuery` 的 `queryFn` 抛异常时 UI 崩溃（TypeError）
- **修复方式**：
  1. `auth.py`：`os.chmod` 加 `try/except (OSError, AttributeError)` 包裹，Windows 上静默跳过
  2. `dependencies.py`：`current_user` / `ws_auth` 加 `try/except`，异常时返回匿名用户（`anonymous: True`）
  3. `routes.py`：`/api/models` 加 `except Exception` 兜底
  4. `InputBar.tsx` / `SettingsPage.tsx`：`queryFn` 加 `try/catch`，失败返回空数组
- **下次注意**：
  1) `os.chmod` 在 Windows 上只支持 `0o444`/`0o666` 两种模式（只读/读写），Unix 特定权限模式必须加 try/except
  2) FastAPI 依赖注入函数（`Depends`）必须对自身异常做兜底，否则一个依赖崩了整条路由链全崩
  3) 前端 `useQuery` 的 `queryFn` 不应直接抛异常，应 catch 后返回空值/默认值
  4) Windows 兼容性必须作为测试项，不能只在 Linux/Mac 上验证
## 2026-06-23 - SSE 事件分隔符转义错误导致前端不显示思考卡片与回答

- **错误现象**：用户输入正常提交，后端日志显示 SSE 正常输出内容，但前端只能看到 assistant 消息底部的五个功能按钮，看不到思考卡片和回答内容。
- **错误原因**：
  1. **根因**：`backend/app/api/common.py` 的 `sse_event()` 函数返回的 SSE 数据使用 `\\n\\n`（两个字符 `\n`）作为事件分隔符，而不是真正的换行符 `\n\n`。前端 SSE 解析器以空行作为事件边界，收不到真正的空行，导致所有 `text`/`thinking`/`source`/`done` 事件都解析失败，`onEvent` 从未被调用，消息 content 和 activity 始终保持初始空值。
  2. **附带问题**：`backend/app/api/chat_routes.py` 的 system_prompt 拼接也错误使用 `\\n`，污染提示词；同时 `while...else` 结构导致正常流程中不会发送 `done` 事件。
- **修复方式**：
  1. `common.py`：`sse_event()` 改为 `f"data: {json.dumps(payload)}\n\n"`，使用真正的换行符作为 SSE 事件分隔符。
  2. `chat_routes.py`：将附件/RAG/system_prompt 中的 `\\n` 全部改为 `\n`；重写 LLM 流式循环，正常结束时显式 `yield sse_event("done", ...)`。
  3. `frontend/src/api/client.ts`：SSE 解析前统一把 `\r\n`/`|` 归一化为 `\n`，并兜底处理连接关闭时缓冲区中剩余的完整事件。
  4. `frontend/src/components/chat/ChatArea.tsx`：流式状态判断从对象引用比较改为 `msg.id === messages[last]?.id`。
- **下次注意**：
  1. Python 字符串中 `\\n` 是字面量 `\n`（两个字符），不是换行符；SSE 必须用真正的 `\n\n` 或 `\r\n\r\n` 分隔事件。
  2. 新增/修改 SSE 辅助函数后，用 curl/wget 抓包检查原始字节，确认事件分隔符正确。
  3. 前端 SSE 解析器应兼容 CRLF/LF/CR，不能假设只有一种行尾。
  4. Python `while...else` 的 `else` 只在循环未被 `break` 时执行，流式读取场景下通常不适用，应避免用此模式发送收尾事件。







## 2026-06-23 - BaseHTTPMiddleware 缓冲 SSE 流式响应导致前端不显示内容

- **错误现象**：用户输入正常提交，后端日志显示 SSE 事件逐个生成，但前端界面只显示五个功能按钮，思考卡片和回答内容区域完全空白。curl 直接请求后端端口可以看到 SSE 事件，但通过前端 Vite 代理访问时内容不显示。
- **错误原因**：`app/main.py` 中使用 `@app.middleware("http")` 注册的中间件（`limit_request_body_middleware` 和 `request_log_middleware`）被 Starlette 包装为 `BaseHTTPMiddleware`。`BaseHTTPMiddleware` 的工作机制是：先消费整个响应体（包括所有 SSE 事件），然后再一次性转发给客户端。对于 `StreamingResponse`（SSE），这意味着所有事件被缓冲直到流结束才发送，前端无法逐个接收事件，导致 `onEvent` 回调从未被触发，消息 content 和 activity 始终为空。
- **修复方式**：将两个 `BaseHTTPMiddleware` 函数改为纯 ASGI 中间件类（`_RequestBodyLimitMiddleware` 和 `_RequestLogMiddleware`），直接操作 `scope`/`receive`/`send`，不缓冲响应体。用 `app.add_middleware()` 注册纯 ASGI 中间件。
- **下次注意**：
  1. `@app.middleware("http")` 创建的 `BaseHTTPMiddleware` 会缓冲整个 `StreamingResponse`，绝对不能用于 SSE 端点
  2. 需要在 SSE 流式响应上执行中间件逻辑时，必须用纯 ASGI 中间件（直接操作 scope/receive/send）
  3. SSE 不显示内容时，先用 curl 直接请求后端确认事件是否正常推送，再检查中间件是否缓冲了响应

## 2026-06-23 - 双重 falsy fallback 导致系统提示词无法修改和注入

- **错误现象**：前端设置页修改系统提示词后，发送消息时后端仍使用默认提示词；清空提示词也无法生效，始终回退到默认值。
- **错误原因**：前后端双重 falsy fallback：
  1. 前端 `useChatStore.ts` 中 `system_prompt: systemPrompt || undefined`，空字符串 `""` 被转为 `undefined`，JSON 序列化时该字段被省略
  2. 后端 `chat_routes.py` 中 `payload.system_prompt or DEFAULT_SYSTEM_PROMPT`，Python 的 `or` 对空字符串也视为 falsy，回退到默认值
  3. 两层叠加：前端省略字段 → 后端收到 None → 使用默认值；前端传空字符串 → 后端 `or` 视为 falsy → 使用默认值。用户无论怎么修改都无法生效。
- **修复方式**：
  1. 前端：`system_prompt: systemPrompt`（保留空字符串，不做 `|| undefined` 转换）
  2. 后端：`system_prompt = payload.system_prompt if payload.system_prompt is not None else DEFAULT_SYSTEM_PROMPT`（用 `is None` 检查代替 `or`，空字符串不再被吞没）
- **下次注意**：
  1. JavaScript `||` 和 Python `or` 对空字符串都视为 falsy，当业务语义需要区分"未设置"和"设置为空"时，必须用 `is None`/`=== undefined` 精确判断
  2. 前后端对同一字段的 falsy 处理逻辑必须对齐，避免双重 fallback 导致用户输入永远无法生效
  3. 测试系统提示词时，要同时验证"自定义内容"和"清空为空字符串"两种场景

## 2026-06-24 - newSession 异步 ID 迁移竞态导致新对话输入丢失

- **错误现象**：新对话开始时输入消息后，回答"莫名其妙退出/消失"——用户消息显示了但 assistant 回答永远为空。
- **错误原因**：`useSessionStore.ts` 的 `newSession` 采用"先本地后同步"策略：同步生成 `localId` 并切换为当前会话，异步调后端拿 `res.id` 后迁移。若用户在后端响应前发送消息，SSE 回调闭包捕获 `requestSessionId = localId`，迁移后 `sessionMessages[localId]` 已被删除，SSE 数据全部写入幽灵会话。
- **修复方式**：将 `newSession` 改为 `async` 函数，先 `await apiPost("sessions", ...)` 拿到后端真实 ID，再用该 ID 创建本地会话并切换。彻底消除 localId/res.id 双 ID 并存窗口。后端失败时回退到本地 ID。
- **下次注意**：1) 异步创建资源时不要假设本地生成的临时 ID 与后端返回的 ID 一致，应以后端返回为准；2) `newSession` 类操作应先拿后端 ID 再切换 UI，避免迁移竞态；3) 调用方 `onClick={() => newSession()}` 无需 await，fire-and-forget 兼容 Promise 返回。

## 2026-06-24 - SSE 切换会话不中止导致切回内容被空覆盖

- **错误现象**：SSE 流式输出中途切换到其他会话再切回来，回答内容消失或只剩最后一两个字符。
- **错误原因**：`setActiveSession` 流式切换时保存了部分内容到 `sessionMessages` 但**不中止 SSE**（保留 `currentSseRequest`），同时清空了 `streamingContent=""`。后台 SSE 继续运行，切回原会话时 `isActive=true`，用空的 `streamingContent` 重新累积，覆盖了 `sessionMessages` 中已保存的完整内容。
- **修复方式**：`setActiveSession` 流式切换时调用 `currentSseRequest.abort()` 中止 SSE（`apiSSE` 的 catch 块会识别为用户中止，不触发 `onError`），同时清空 `currentSseRequest`。`stopStreaming` 增加防御：`streamingSessionId` 为 null 时跳过写入避免误伤当前会话；优先取已有 `lastMsg.content` 而非空的 `streamingContent`。
- **下次注意**：1) 流式输出期间切换会话必须中止 SSE 并保存部分内容，不能让后台 SSE 继续运行（`streamingContent`/`streamingSteps` 是单例状态，切回后无法恢复对应缓冲区）；2) `stopStreaming` 必须用 `streamingSessionId` 精确定位会话，不能回退到 `activeSessionId`；3) `apiSSE` 的 `controller.signal.aborted` 检查确保用户中止不触发 `onError`。

## 2026-06-24 - 鉴权异常降级为匿名访问 + 多端点缺少鉴权

- **错误现象**：`dependencies.py` 的 `current_user` 在鉴权存储读取异常时返回 `anonymous=True`，攻击者可通过触发文件异常绕过鉴权。MCP/sandbox/auth 端点完全无鉴权，任何人可执行任意命令/代码/篡改 API Key。
- **错误原因**：1) `except Exception` 捕获器降级为匿名访问而非拒绝；2) `mcp_routes.py`、`sandbox_routes.py`、`auth.py` 的 `list_keys`/`delete_key` 端点未添加 `Depends(current_user)`；3) `auth.py` 与 `dependencies.py` 存在循环依赖，无法直接导入 `current_user`。
- **修复方式**：1) `dependencies.py` 异常时返回 503 而非匿名访问；2) MCP 全部端点和 sandbox 端点添加 `Depends(current_user)`；3) `auth.py` 的 `list_keys`/`delete_key` 用内联 `Header` 检查避免循环导入（`store-key` 作为认证端点本身不要求鉴权）。
- **下次注意**：1) 鉴权异常时必须默认拒绝访问（fail-closed），不能降级为匿名（fail-open）；2) 所有执行代码/命令的端点必须鉴权；3) 存在循环依赖时可用内联 `Header` 检查替代 `Depends(current_user)`；4) 认证端点本身（如 `store-key`）不需要鉴权，因为用户首次设置 Key 时还没有 token。

## 2026-06-24 - deleteSession 未中止 SSE + SSE 解析失败未中止流 + 导出 ContentPart[] 损坏

- **错误现象**：1) 删除正在流式输出的会话后 SSE 继续写入已删除的会话；2) SSE 连续 3 次 JSON 解析失败后仅回调 `onError` 但不中止流，可能无限循环；3) 导出 Markdown 时多模态消息内容输出 `[object Object]`。
- **错误原因**：1) `deleteSession` 未调用 `stopStreaming`；2) `apiSSE` 解析失败 3 次后没有 `controller.abort()`；3) `exportConversation` 用 `${m.content}` 模板字符串对 `ContentPart[]` 输出 `[object Object]`。
- **修复方式**：1) `deleteSession` 删除活跃会话时先调 `chatStore.stopStreaming()`；2) `apiSSE` 3 次失败后调 `controller.abort()` 并 `return`；3) `exportConversation` 对 `ContentPart[]` 转为 Markdown 文本后再导出。
- **下次注意**：1) 删除资源前必须先清理关联的异步操作（SSE/定时器）；2) 错误回调后必须中止流读取，不能仅通知错误；3) 模板字符串对对象类型输出 `[object Object]`，必须先转为字符串。

## 2026-06-21: 模型下拉框不显示上游模型
→- **错误现象**：验证 API Key 成功后，设置页和聊天输入框的模型下拉框只显示硬编码的 fallback 模型（gpt-4o/llama3.3/deepseek-v3），不显示上游 Provider 返回的真实模型列表。
- **错误原因**：
  1. SettingsPage.tsx handleVerify 成功后没有 invalidate TanStack Query 缓存，useQuery 的数据仍是空的
  2. useQuery queryKey 只依赖 baseUrl + provider，切换 Key 不触发 refetch
  3. 后端 routes.py Key 优先级为 stored_key or header_key，导致已存储的旧 Key 永远覆盖前端新输入的 Key
- **修复方式**：
  1. SettingsPage.tsx：导入 useQueryClient，handleVerify 成功后 queryClient.invalidateQueries({ queryKey: ["models"] })
  2. SettingsPage.tsx + InputBar.tsx：queryKey 加上当前 Key 值，Key 变化自动 refetch
  3. routes.py（两处：models + chat）：Key 优先级改为 header_key or stored_key or settings.llm_api_key
- **下次注意**：
  1. TanStack Query 的 cache 是隐式的，手动调用 API（如 handleVerify）后必须 invalidate 对应 query，否则 useQuery 不更新
  2. queryKey 必须包含所有会影响结果的输入，否则值变化不触发 refetch
  3. 后端 Key 优先级：用户刚输入的 X-API-Key header > 后端已加密存储的旧 Key > .env 默认值。Header 代表用户当前意图，必须优先

## 2026-06-23 - kb_routes.py 重写后测试用例与旧 API 不匹配
- **错误现象**：`pytest tests/test_routes_kb.py` 两个测试全部失败。`test_upload_success` 报 `assert False is True`；`test_upload_file_too_large` 报 `assert 200 == 400`。
- **错误原因**：
  1. 旧测试期望 upload 响应中 `chunks > 0`（同步入库），但新 API 改为异步索引，响应返回 `status="indexing"` + `chunks=0`，后台任务完成后再更新 DB
  2. 旧测试期望文件超限时返回 HTTP 400（`HTTPException`），但新 API 改为返回 HTTP 200 + `{"success": False, "error": {"code": "FILE_TOO_LARGE"}}`（错误体包装）
  3. 旧测试 patch `app.api.routes.MAX_UPLOAD_SIZE`，但新代码在 `app.api.kb_routes` 模块，patch 路径不对
  4. 测试 fixture 未创建 builtin KB 记录，导致 `kb_manager.get_kb("builtin-001")` 返回 None → `KB_NOT_FOUND`
- **修复方式**：
  1. 测试 fixture 中调用 `init_db()` + 创建 `KnowledgeBase(is_builtin=True)` 记录
  2. `test_upload_success` 改为断言 `status == "indexing"` 而非 `chunks > 0`
  3. `test_upload_file_too_large` 改为断言 `response.status_code == 200` + `data["error"]["code"] == "FILE_TOO_LARGE"`
  4. patch 路径改为 `app.api.kb_routes.MAX_UPLOAD_SIZE`
- **下次注意**：
  1. 重写 API 路由后必须同步更新对应测试，尤其是响应格式和状态码变更
  2. 异步索引 API 的测试只能验证"已接受请求"，不能验证"索引完成"——后者需要 mock 后台任务或等待完成
  3. `unittest.mock.patch` 的路径必须是代码实际定义的模块，不是旧模块路径

## 2026-06-23 - kb_manager.ingest_chunks 重复实现 vector_store 逻辑
- **错误现象**：`kb_manager.ingest_chunks()` 方法内部重复实现了 embedding 检查、metadata 构建和 ChromaDB 写入逻辑，与 `vector_store.ingest_chunks()` 高度重复。
- **错误原因**：kb_manager 最初设计时没有委托给 vector_store，而是自己处理入库，导致两处逻辑需要同步维护。
- **修复方式**：`kb_manager.ingest_chunks()` 改为：1) 注入 `kb_id` 到每个 chunk 的 metadata；2) 委托 `store.ingest_chunks(chunks, doc_id)` 处理 embedding 检查和写入；3) 标记 BM25 stale。
- **下次注意**：Manager 层应委托给 Store 层处理底层操作，不要重复实现。Manager 职责是路由和协调，Store 职责是持久化。

