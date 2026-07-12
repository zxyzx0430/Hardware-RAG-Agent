# 聊天 UX 问题批量修复 Spec

## Why

用户反馈刷新/切换会话后不自动滚到最新对话，且怀疑还有其他影响体验的问题。经系统扫描 11 个核心文件，发现 35 个 UX 问题。本 spec 对每个问题给出"修复后用户能看到什么变化"的描述，并标注哪些变好、哪些可能变差。用户后续追加要求修复问题 19（原生弹窗）和问题 32（多 tab 同步），共 17 项修复。

---

## 修复后你会看到的变化（17 项修复）

### 必须修复（7 项）

#### 1. ConfirmDialog ESC 关闭后 Agent 永久卡死
- **修复前**：HITL 确认框出现时按 ESC，对话框消失，但 Agent 永远不再继续，对话彻底死掉，只能刷新页面
- **修复后**：按 ESC 等价于点击"拒绝"按钮，Agent 收到拒绝决策继续往下走，对话不死
- **变好**：不会再因为一个 ESC 把整个对话搞死
- **变差**：无（ESC 原本就是"取消"的本能操作，符合用户预期）

#### 2. ActivityBlock reasoning 标签显示为 'thing'
- **修复前**：推理模型（如 o1/DeepSeek-R1）的回答中，思考过程卡片上显示一个无意义的 'thing' 标签
- **修复后**：显示 'thinking' 标签
- **变好**：UI 文案专业，不再有拼写错误
- **变差**：无

#### 3. sendMessage 异常吞掉错误无反馈
- **修复前**：发送消息时如果代码异常（网络层之外的同步错误），消息发不出去，UI 也没任何提示，你以为卡住了或者点了没反应
- **修复后**：发送失败时显示红色 ErrorBlock，告诉你具体错误信息
- **变好**：失败有反馈，能知道为什么没发出去
- **变差**：无（原本就是异常场景，只是加了提示）

#### 4. 切换会话不自动滚到最新对话（用户原始反馈）
- **修复前**：刷新页面 / 切换会话后，聊天界面从第一条历史消息开始显示，需要手动往下拖很久才能看到最新对话
- **修复后**：自动滚到最后一条消息底部，最新对话直接可见
- **变好**：刷新/切会话后立刻看到最新对话，不用手动滚
- **变差**：无

#### 5. 切换到消息数相同的会话不滚动
- **修复前**：从会话 A（4 条）切到会话 B（4 条），B 不会滚到底，因为代码只看消息数变化。这是一个隐蔽的 bug，和 #4 同根因
- **修复后**：切换会话一律滚到底，不管消息数是否相同
- **变好**：所有切换会话场景都正确滚到底
- **变差**：无

#### 6. 空会话状态与加载中状态未区分
- **修复前**：第一次进入一个有历史消息但本地无缓存的会话，会先显示"开始你的第一个问题"的空状态，过几秒消息加载完突然变成历史消息，体验割裂
- **修复后**：加载期间显示骨架屏/loading 动画，加载完才显示消息。真正的空会话才显示"开始你的第一个问题"
- **变好**：不会再被"先空状态后突然有消息"误导
- **变差**：无

#### 7. ChatArea 编辑框无法换行
- **修复前**：编辑已发送的消息时，120px 高的 textarea 只能写一行，多行内容无法换行，长文本编辑体验差
- **修复后**：Shift+Enter 换行，Enter 发送（与输入框一致）
- **变好**：编辑长消息时可以正常换行
- **变差**：无

#### 8. ErrorBlock 暗色模式硬编码颜色
- **修复前**：暗色模式下错误提示卡片用硬编码的黄色背景，颜色刺眼，和整体主题不协调
- **修复后**：用 CSS 变量适配主题，暗色模式下颜色协调
- **变好**：暗色模式视觉统一
- **变差**：无

### 建议修改（8 项）

#### 9. 输入框草稿切换会话时丢失
- **修复前**：在会话 A 输入一半"STM32 怎么接线" → 切到 B 看个东西 → 切回 A，输入框空了，要重新打字
- **修复后**：草稿按会话保存，切回 A 时"STM32 怎么接线"还在输入框
- **变好**：跨会话思考不被打断，输入内容不丢失
- **变差**：刷新页面后草稿会丢失（用内存存储，不写 localStorage，可接受）

#### 10. 全局 toast 错误反馈
- **修复前**：很多错误（加载会话失败、保存失败等）被静默吞掉，用户完全无感知，不知道操作失败了
- **修复后**：所有错误统一用右上角 toast 弹出提示，3 秒后自动消失
- **变好**：操作失败有感知，便于排查问题
- **变差**：偶尔会看到错误 toast（但原本就是该看到的错误，只是以前被隐藏了）

#### 11. sendMessage 流式中静默拒绝
- **修复前**：流式生成中点发送，消息直接没发出去，没有任何提示，你以为 UI 卡了
- **修复后**：流式中点发送会弹 toast "正在生成中，请先停止当前回答"
- **变好**：知道为什么没发出去，知道要先停止
- **变差**：无

#### 12. ESC 键统一取消行为
- **修复前**：ESC 键在很多场景下没反应——流式中按 ESC 不停止、引用消息按 ESC 不取消、附件按 ESC 不移除、编辑消息按 ESC 不取消、图片灯箱按 ESC 不关闭
- **修复后**：所有上述场景 ESC 都能取消当前操作
- **变好**：ESC 符合用户本能取消预期，操作更顺畅
- **变差**：如果你习惯了 ESC 无反应，可能需要重新适应（但适应成本极低）

#### 13. ConfirmDialog focus trap
- **修复前**：HITL 确认框出现时按 Tab，焦点会跑到对话框外面的按钮（比如发送按钮），可能误触发其他操作
- **修复后**：Tab 只能在确认框内循环，不会跑出去
- **变好**：避免误操作
- **变差**：无

#### 14. ImageLightbox 无障碍
- **修复前**：点击图片放大后，只能再点一下关闭，键盘用户无法退出
- **修复后**：ESC 可关闭灯箱，图片加载时显示 loading
- **变好**：键盘操作友好
- **变差**：无

#### 15. 切换会话滚动位置保留
- **修复前**：在会话 A 滚到中间查看历史 → 切到 B → 切回 A，A 自动滚到底部，刚才看的位置丢了
- **修复后**：切回 A 时恢复到刚才的滚动位置，继续看历史
- **变好**：跨会话查看历史不被打断
- **变差**：无（首次进入会话仍滚到底，只有切回才恢复位置）

#### 16. 删除当前活跃会话后立即切换
- **修复前**：删除当前正在看的会话后，右侧聊天区短暂空白，过一会儿才切到其他会话
- **修复后**：删除后立即切到剩余会话的第一个，无空白
- **变好**：删除会话体验连贯
- **变差**：无

#### 17. 原生 alert/prompt/confirm 弹窗改为应用内 Modal（问题 19）
- **修复前**：删除会话、新建项目、恢复快照、删除服务商时弹出的是浏览器原生弹窗（白底灰框，样式突兀，无法用 ESC 取消，无法定制）
- **修复后**：所有弹窗改为应用内 Modal，跟随主题（暗色模式协调），支持 ESC 取消、Tab 焦点循环，视觉与应用统一
- **变好**：
  - 弹窗视觉和应用风格一致，不再有"跳出应用"的割裂感
  - 暗色模式下弹窗也是暗色
  - ESC 可取消（原生 confirm 在部分浏览器不可 ESC）
  - Tab 焦点被困在 Modal 内，不会误触发外部操作
- **变差**：无（原生弹窗本身体验就差）

**涉及 4 处原生弹窗替换**：
1. `SessionPanel.tsx:151` — `window.confirm(t('deleteSessionConfirm'))` 删除会话确认
2. `SessionPanel.tsx:136` — `window.prompt(...)` 新建项目输入名称
3. `SnapshotPanel.tsx:118` — `window.confirm(...)` 恢复快照确认
4. `SettingsPage.tsx:150` — `window.confirm(...)` 删除服务商确认

#### 18. 多 tab 打开同一会话同步（问题 32）
- **修复前**：在 tab A 发了消息，tab B 看不到新消息，必须手动刷新；在 tab A 删除会话，tab B 侧边栏还显示已删除的会话，点进去报错
- **修复后**：tab A 的操作（发消息/删会话/新建会话）会实时同步到 tab B，无需刷新
- **变好**：
  - 多 tab 场景下数据一致，不会看到过期数据
  - 避免"点了已删除会话报错"的体验
- **变差**：
  - 多 tab 同时编辑同一会话时可能消息顺序抖动（但本地单用户场景概率极低）
  - 实现用 BroadcastChannel API，老旧浏览器不支持（项目目标浏览器是现代 Chrome/Edge，可接受）

**实现方式**：用 `BroadcastChannel API` 在不同 tab 间广播事件（`session_deleted` / `messages_updated` / `session_created`），各 tab 监听并刷新对应数据。

---

## 不修复的 18 个问题（保持现状的原因）

| # | 问题 | 保持现状的原因（对用户意味着什么） |
|---|------|----------------------------------|
| 4 | 流式中切会话导致并发 SSE | **用户刻意为之**：后台 SSE 继续跑是为了让旧会话流式完成不丢内容，这是设计选择 |
| 15 | 错误处理全程无 toast | 与 #10 重复，#10 已纳入修复 |
| 16 | folder-picker 弹出框定位可能被裁剪 | 实际几乎不会触发（消息很少在视口顶部），保持现状不影响使用 |
| 17 | folder-picker 新建文件夹用 setTimeout | 实际工作正常，只是代码层面脆弱，对你无可见影响 |
| 18 | 删除当前活跃会话后短暂空白 | 改为修复了（见 #16），此条与 #16 重复 |
| 20 | ActivityBlock 计时器 startTime 缺失显示 0 | 实际 startTime 总是有值，这个场景没发生过 |
| 21 | 引用消息视觉反馈弱 | 需要单独设计高亮样式，涉及视觉设计，本次不做 |
| 22 | ImageLightbox 缺 ESC 和 focus trap | 与 #14 重复，#14 已纳入 ESC，focus trap 优先级低 |
| 23 | 流式中断后空白消息 | "流式开始前停止"这个场景罕见，实际用不到 |
| 24 | 超长消息/代码块无折叠 | **用户明确说不修**，保持长内容完整显示 |
| 25 | 文件类型校验与 accept 不一致 | 实际拖拽和选择器都能工作，对你无可见影响 |
| 26 | TopBar 解构整个 store 重渲染 | 性能问题非 UX 问题，TopBar 重渲染你看不到 |
| 27 | ActivityBlock 折叠状态切换会话后重置 | 切回会话时活动卡片重新折叠是合理行为（重新查看） |
| 28 | setActiveSession 三元表达式两分支相同 | 代码层面问题，功能正确，对你无可见影响 |
| 29 | branchThread saveToStorage 时机 | 实际风险极低（5 行代码间刷新几乎不可能） |
| 30 | MarkdownRenderer 行内代码判断误判 | 实际渲染符合预期，边界场景罕见 |
| 31 | 会话标题省略号需 CSS 确认 | 大概率已处理，对你无可见影响 |
| 33 | 按钮缺少 aria-label | 项目无屏幕阅读器用户，对你无可见影响 |
| 34 | 图片 alt 文本不够描述性 | 同 #33，工作量大但约束小 |

---

## What Changes

### 必须修复（7 项）
1. ConfirmDialog ESC 等价"拒绝"——Agent 不再卡死
2. ActivityBlock 'thing' → 'thinking'
3. sendMessage 异常设置 streamingError——显示 ErrorBlock
4. 切换会话自动滚动（依赖 [messages, activeSessionId] + rAF + ResizeObserver 兜底）
5. 空会话与加载中区分（isLoadingMessages + 骨架屏）
6. ChatArea 编辑框 Shift+Enter 换行
7. ErrorBlock 暗色模式用 CSS 变量

### 建议修改（10 项）
8. 输入框草稿按会话持久化（内存）
9. 全局 toast（Toast.tsx + useToastStore.ts）
10. sendMessage 流式中拒绝加 toast
11. ESC 统一取消（流式/引用/附件/编辑/灯箱）
12. ConfirmDialog focus trap
13. ImageLightbox ESC + role=dialog
14. 切换会话滚动位置保留（Map<sessionId, scrollTop>）
15. 删除当前活跃会话立即切换
16. **应用内 Modal 替换原生 alert/prompt/confirm（4 处）**
17. **多 tab 同步（BroadcastChannel API）**

## Impact

- Affected code:
  - `frontend/src/components/chat/ChatArea.tsx` — 滚动修复、编辑框 ESC、灯箱 ESC、滚动位置缓存
  - `frontend/src/components/chat/ConfirmDialog.tsx` — ESC、focus trap
  - `frontend/src/components/chat/ActivityBlock.tsx` — 'thing' 拼写
  - `frontend/src/components/chat/ErrorBlock.tsx` — 暗色模式颜色
  - `frontend/src/components/input/InputBar.tsx` — 草稿、ESC
  - `frontend/src/components/session/SessionPanel.tsx` — 删除会话切换、原生 confirm/prompt 替换
  - `frontend/src/components/shared/SnapshotPanel.tsx` — 原生 confirm 替换
  - `frontend/src/components/settings/SettingsPage.tsx` — 原生 confirm 替换
  - `frontend/src/stores/useChatStore.ts` — sendMessage 错误、isLoadingMessages、drafts、多 tab 同步
  - `frontend/src/stores/useSessionStore.ts` — 多 tab 同步（session 列表变更广播）
  - 新增 `frontend/src/components/shared/Toast.tsx` — 全局 toast
  - 新增 `frontend/src/stores/useToastStore.ts` — toast 状态
  - 新增 `frontend/src/components/shared/Modal.tsx` — 应用内通用 Modal（confirm/prompt 两用）
  - 新增 `frontend/src/stores/useModalStore.ts` — Modal 状态（confirm/prompt 异步 Promise 接口）
  - 新增 `frontend/src/utils/broadcast.ts` — BroadcastChannel 封装
- 不涉及后端
- 不涉及数据结构变化（store 加字段，无 schema 变更）

## ADDED Requirements

### Requirement: 会话进入自动滚动到底部（含位置保留）

系统 SHALL 在以下场景处理滚动：
1. 切到新会话（之前未访问过）：滚到最后一条
2. 切回曾访问过的会话：恢复之前的滚动位置
3. 刷新页面：滚到最后一条（无历史位置）

#### Scenario: 刷新页面进入会话
- **WHEN** 用户刷新页面，messages 渲染到 DOM
- **THEN** 聊天容器自动滚到最后一条消息底部

#### Scenario: 首次切换到会话 B
- **WHEN** 用户从会话 A 切到会话 B（B 未访问过）
- **THEN** 会话 B 自动滚到底部

#### Scenario: 切回曾访问的会话 A
- **WHEN** 用户从会话 B 切回会话 A（A 之前滚动到中间查看历史）
- **THEN** 会话 A 恢复之前的滚动位置（不强制滚到底）

#### Scenario: 切换到消息数相同的会话
- **WHEN** 用户从会话 A（4 条）切到会话 B（4 条）
- **THEN** 会话 B 滚到底部（不被"消息数相同"误判）

#### Scenario: 切换到空会话
- **WHEN** 用户切换到无消息的新会话
- **THEN** 显示空状态，不滚动

### Requirement: 切换会话时重置滚动状态 ref

系统 SHALL 在 `activeSessionId` 变化时重置 `userScrolledRef.current = false` 和 `isAtBottomRef.current = true`，避免旧会话的滚动状态污染新会话。

### Requirement: DOM 渲染完成后才滚动

系统 SHALL 用 `requestAnimationFrame` 双帧延迟或 `ResizeObserver` 等待 DOM 渲染完成后再滚动，避免 `scrollHeight` 还是旧值。

#### Scenario: 会话含图片
- **WHEN** 会话最后一条消息含图片
- **THEN** 图片加载后滚动仍定位到底部（ResizeObserver 兜底）

### Requirement: ConfirmDialog ESC 等价拒绝

系统 SHALL 让 ESC 键等价于点击"拒绝"按钮，调用 `resumeAgent("deny")` 而非 `clearPendingConfirm()`。

#### Scenario: 用户按 ESC 关闭确认框
- **WHEN** HITL 确认框出现，用户按 ESC
- **THEN** 调用 `resumeAgent("deny")`，Agent 收到拒绝决策继续执行

### Requirement: ConfirmDialog focus trap

系统 SHALL 在 ConfirmDialog 显示时捕获焦点，Tab 只能在对话框内循环。

### Requirement: ActivityBlock reasoning 标签正确

系统 SHALL 在 reasoning 步骤显示 'thinking' 而非 'thing'。

### Requirement: sendMessage 异常有错误反馈

系统 SHALL 在 sendMessage 同步异常时设置 `streamingError`，让用户看到 ErrorBlock。

### Requirement: 空会话与加载中状态区分

系统 SHALL 在 `fetchMessages` 期间显示加载指示，而非 EmptyState。

#### Scenario: 首次进入无本地缓存的会话
- **WHEN** 用户进入有后端消息但本地无缓存的会话
- **THEN** 显示骨架屏/loading，而非"开始你的第一个问题"

### Requirement: 全局 toast 错误反馈

系统 SHALL 提供全局 toast，所有错误（fetchMessages 失败、persistLastTurn 失败、流式中发送等）统一通过 toast 显示。

#### Scenario: 流式中尝试发送
- **WHEN** 流式中用户尝试发送新消息
- **THEN** 显示 toast "正在生成中，请先停止当前回答"

### Requirement: ESC 键统一取消行为

系统 SHALL 在以下场景支持 ESC：
- 流式中：停止流式
- 引用消息：取消引用
- 附件预览：移除附件
- 编辑消息：取消编辑
- 图片灯箱：关闭灯箱
- ConfirmDialog：等价拒绝（见上）

### Requirement: 输入框草稿按会话持久化

系统 SHALL 维护 `drafts: Record<sessionId, string>`，切换会话时加载对应草稿。

#### Scenario: 会话 A 输入一半切到 B 再切回
- **WHEN** 用户在会话 A 输入"STM32" → 切到 B → 切回 A
- **THEN** 输入框显示"STM32"

### Requirement: ImageLightbox 支持ESC关闭和无障碍

系统 SHALL 为 ImageLightbox 加 ESC 关闭、`role="dialog" aria-modal="true"`。

### Requirement: ErrorBlock 暗色模式颜色协调

系统 SHALL 用 CSS 变量替代 ErrorBlock 中的硬编码颜色。

### Requirement: 应用内 Modal 替换原生弹窗

系统 SHALL 提供一个通用应用内 Modal 组件，替换所有 `window.alert` / `window.prompt` / `window.confirm` 调用。Modal SHALL：
- 跟随主题（暗色模式协调）
- 支持 ESC 取消（等价于"否"）
- 支持 Tab 焦点循环（focus trap）
- 提供异步 Promise 接口：`const ok = await confirmDialog({ title, message })` / `const name = await promptDialog({ title, placeholder })`
- 显示时自动聚焦默认按钮（confirm 聚焦"确认"，prompt 聚焦输入框）

#### Scenario: 删除会话弹窗
- **WHEN** 用户点击删除会话
- **THEN** 弹出应用内 Modal "确认删除此会话？"，确认后才删除

#### Scenario: 新建项目弹窗
- **WHEN** 用户点击"新建项目"
- **THEN** 弹出应用内 Modal 含输入框，输入名称确认后创建

#### Scenario: 恢复快照弹窗
- **WHEN** 用户点击恢复快照
- **THEN** 弹出应用内 Modal "确定要恢复快照「X」吗？当前对话将被替换。"

#### Scenario: 删除服务商弹窗
- **WHEN** 用户点击删除服务商
- **THEN** 弹出应用内 Modal "确定删除服务商「X」？该操作不可撤销。"

#### Scenario: Modal ESC 取消
- **WHEN** Modal 显示时按 ESC
- **THEN** 等价于点击"取消"，Promise resolve(false) / resolve(null)

### Requirement: 多 tab 同步

系统 SHALL 用 BroadcastChannel API 在多个浏览器 tab 间同步关键状态变更：
- 会话列表变化（新建/删除/重命名/移动项目）
- 当前会话消息变化（新消息/删除消息/编辑消息）

各 tab SHALL 监听 channel 消息并刷新对应数据：
- 收到 `sessions_changed` → 重新加载会话列表
- 收到 `messages_changed:sessionId` → 若该会话当前正在查看，刷新消息
- 收到 `session_deleted:sessionId` → 若正在查看该会话，切到其他会话

#### Scenario: tab A 发消息，tab B 同步
- **WHEN** tab A 在会话 X 发送新消息
- **THEN** tab B 若也在看会话 X，自动显示新消息（无需刷新）

#### Scenario: tab A 删会话，tab B 同步
- **WHEN** tab A 删除会话 X
- **THEN** tab B 侧边栏立即移除会话 X；若 tab B 正在看会话 X，自动切到第一个剩余会话

#### Scenario: tab A 新建会话，tab B 同步
- **WHEN** tab A 新建会话 Y
- **THEN** tab B 侧边栏立即出现会话 Y

## MODIFIED Requirements

### Requirement: 流式输出期间不自动滚动

原有逻辑保留：流式输出期间不自动滚动，由用户滑轮控制。

## Assumptions & Decisions

1. **不引入虚拟滚动** — 当前消息量不需要
2. **toast 自建不引第三方** — 避免 bundle 膨胀
3. **focus trap 用简单 keydown 实现** — 不引第三方库
4. **草稿持久化用 store 内存** — 不写 localStorage，刷新后丢失可接受
5. **滚动位置缓存用 Map** — `Map<sessionId, scrollTop>`，切会话时存旧取新
6. **不修问题 4（并发 SSE）** — 用户确认刻意为之
7. **不修问题 24（超长折叠）** — 用户明确说不修
8. **不修问题 18（删除会话空白）** — 改为顺手修（Task 15，工作量小）
9. **i18n 仅修 'thing' 拼写** — 其他硬编码文案本次不动
10. **aria-label/alt 不全面补全** — 工作量大，本次跳过
11. **Modal 用 Promise 异步接口** — 与原生 confirm/prompt 行为一致，调用方 await 即可，避免回调嵌套
12. **多 tab 同步用 BroadcastChannel** — 现代 API，无需轮询；老旧浏览器不支持可接受（项目目标 Chrome/Edge）
13. **Modal 与 ConfirmDialog 不合并** — ConfirmDialog 是 HITL 专用有特殊逻辑（resumeAgent），Modal 是通用弹窗，职责不同

## Out of Scope

- 问题 4：并发 SSE（用户刻意为之）
- 问题 24：超长消息折叠（用户明确不修）
- 虚拟滚动
- aria-label 全面补全
- TopBar 性能优化
- i18n 全面补全

## Verification

- 刷新页面 → 自动滚到最后一条
- 首次切到会话 B → 滚到底部
- 切回曾访问的会话 A → 恢复之前滚动位置
- 切换会话（消息数相同）→ 滚到底部
- 切换到空会话 → 显示空状态
- 流式输出中向上滚动 → 不被打断
- 流式中按 ESC → 停止流式
- HITL 确认框 ESC → 等价拒绝，Agent 继续
- HITL 确认框 Tab → 焦点循环
- reasoning 标签显示 'thinking'
- sendMessage 异常 → 显示 ErrorBlock
- 首次进入无缓存会话 → 显示加载指示
- fetchMessages 失败 → 显示 toast
- 流式中尝试发送 → 显示 toast "正在生成中"
- 会话 A 输入草稿 → 切到 B → 切回 A → 草稿保留
- 图片灯箱 ESC → 关闭
- 编辑消息 Shift+Enter 换行，Enter 发送，ESC 取消
- ErrorBlock 暗色模式颜色协调
- 删除当前活跃会话 → 立即切到第一个剩余会话
- 删除会话/新建项目/恢复快照/删除服务商 → 弹应用内 Modal（不是浏览器原生弹窗）
- Modal ESC → 取消（等价"否"）
- Modal Tab → 焦点循环
- Modal 暗色模式 → 颜色协调
- 多 tab：tab A 发消息 → tab B 同步显示
- 多 tab：tab A 删会话 → tab B 侧边栏移除，若在看该会话则切换
- 多 tab：tab A 新建会话 → tab B 侧边栏出现
- `npx tsc --noEmit` 通过
