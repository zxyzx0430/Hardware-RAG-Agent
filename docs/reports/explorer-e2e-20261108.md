# 文件资源管理器真实浏览器 E2E 调查报告

- 日期：2026-11-08
- 分支 / 工作树：`claude/interesting-brahmagupta-c3eac9`（基线 `w1-baseline`，HEAD `f2f5bd7d`）
- 前端：本工作树 Vite，`http://127.0.0.1:5399`（临时 `vite.e2e.config.ts`，代理到 58081）
- 后端：本工作树 `backend/main.py --web --port 58081`，临时 DB `E:/DevCaches/Temp/explorer-e2e/hardware_rag.db`
- 浏览器：ego-browser（ego lite 0.6.2.7），视口 1148×752；另用 CDP 模拟 900×752 / 1440×900
- 测试目录：`E:/DevCaches/Temp/explorer-e2e/fixture`（嵌套目录、文本/代码、空文件、长文件名、中文/emoji/特殊字符、3MB 文本、二进制、隐藏文件、只读文件、空目录）
- 证据目录：`E:/DevCaches/Temp/explorer-e2e/shots/`
- 认证：临时 DB 首次配置写入 dummy key 换取 session token；未写入报告。未修改产品代码，仅新增本报告。

结论：**未发现 P0**。发现 4 个 P1（刷新/监听导致树状态或数据不一致、Ctrl+S 不保存）、15 个 P2、若干 P3/观察项。核心问题集中在 `ExplorerPanel` 的树合并/刷新逻辑与 `useExplorerWatch` 的连接生命周期。

---

## P1

### P1-01 刷新或监听事件后，文件树展开状态丢失（路径分隔符不一致）

**现象**
- 通过“打开文件夹”选择 `E:/DevCaches/Temp/explorer-e2e/fixture`，展开 `fixture → docs → zh-中文目录` 后，只要发生一次 watch 静默刷新（保存文件、外部新建/删除）或点“刷新”，树会退回只剩折叠的根节点；`docs/zh` 的子项消失。
- 此时 `localStorage` 的展开记录仍是完整的 `E:\...` 路径，但 DOM 根节点 `data-path` 已变成 `E:/...`；两者不匹配，导致 `expanded.has(rootPath)` 为 false。

**复现步骤**
1. 资源管理器 → 打开文件夹，输入 `E:/DevCaches/Temp/explorer-e2e/fixture` → 选择文件夹。
2. 依次展开 `fixture`、`docs`、`zh-中文目录`。
3. 在 `docs/zh-中文目录/说明文件.txt` 中编辑并点“保存”（或外部创建任意文件触发 watch）。
4. 观察树：根节点变回 `▶`，`docs` 子项和展开状态消失。

**期望 vs 实际**
- 期望：刷新只更新内容，已展开目录保持展开，滚动位置/选中尽量保留。
- 实际：根节点折叠，展开层级丢失；用户需要重新展开，且刷新期间树内容短暂/持续不完整。

**证据**
- `shots/04-root-expanded.png`（展开状态）
- `shots/12-after-refresh.png`（刷新后只剩折叠根）
- `shots/13-watch-create.png`（根级新文件出现，但树根已折叠）
- 实测：DOM 根 `data-path = E:/DevCaches/Temp/explorer-e2e/fixture`；`hwrag_explorer_root_path = E:\DevCaches\Temp\explorer-e2e\fixture`；`hwrag_explorer_expanded_...` 中保存的是 `E:\...\fixture`、`E:\...\docs`、`E:\...\docs\zh-中文目录`。

**疑似根因（文件:行）**
- `frontend/src/components/explorer/ExplorerPanel.tsx:85-87` `normalizePath()` 把选择器路径转为 `/`。
- `ExplorerPanel.tsx:168` `loadTree()` 把传入的（可能是 `/`）路径写入 `expectedPathRef`；而后端返回的 `first.path` 是 `\`，`ExplorerPanel.tsx:183` 用它设置了 `rootPath`。
- `ExplorerPanel.tsx:211-219` `loadAuthorizedTree()` 用传入的 `path` 合成根节点；静默刷新使用 `expectedPathRef.current`（`ExplorerPanel.tsx:240-244`），于是根节点路径为 `/` 写法。
- `ExplorerPanel.tsx:52` `mergeTreeDataFresh()` 用字符串相等比较 `prev.path === fresh.path`，分隔符不同直接失败；`expanded` 集合按 `\` 路径保存，根节点按 `/` 路径查询，展开状态失效。
- 说明：通过“最近”打开时路径是 `\` 写法，不会触发该问题；从目录选择器打开则必现，表现为“有时正常、有时刷新后折叠”。

**稳定性**：稳定复现（3/3；从选择器打开后任意 watch/刷新均触发）。

---

### P1-02 已展开目录内的新建/重命名/删除/粘贴不更新树；外部变更也不出现

**现象**
- 在已展开的 `docs` 目录内：
  - 右键“新建文件” `ui-new.txt`：磁盘创建成功、`创建成功` toast 出现，但树中看不到 `ui-new.txt`。
  - 外部创建 `docs/watcher-new.txt`：树不出现；随后在根目录创建 `root-watch-new.txt`：根级文件会正常出现。
  - UI 重命名 `watcher-renamed.txt → ui-renamed.txt`：磁盘已改名，树仍显示旧名；旧名点击会报“文件不存在”。
  - UI 删除 `readme.md`：文件进入回收区、磁盘已删，树仍显示 `readme.md`。
  - 复制两个文件后粘贴到 `docs`：磁盘出现副本，树不显示。
- 手动点“刷新”、折叠再展开 `docs` 都不能恢复；只有通过“最近”重新打开文件夹或整页刷新才会出现新内容。原因是被合并后的 `docs` 节点为 `lazy=false`，不再触发目录重新加载。

**复现步骤**
1. 打开 fixture，展开 `fixture` 和 `docs`。
2. 外部在 `docs` 下新建 `watcher-new.txt`，等待 2s；观察树无该文件。
3. 在根目录新建 `root-watch-new.txt`，等待 2s；根级新文件出现。
4. 或右键 `docs` → 新建文件 → 输入 `ui-new.txt` → 回车；磁盘存在、树无。

**期望 vs 实际**
- 期望：新建/重命名/删除/粘贴成功后树立即反映；外部变更经 watch 刷新后反映。
- 实际：根级变更能反映；已展开子目录内的变更永远被旧的 `children` 覆盖，直到重新打开文件夹/整页刷新。

**证据**
- `shots/13-watch-create.png`、`shots/14-after-manual-refresh.png`、`shots/15-after-reopen-docs.png`
- `shots/16-watch-delete.png`（外部删除 `readme.md` 后树仍显示）
- `shots/18-ui-create.png`（UI 新建后树无 `ui-new.txt`）
- `shots/19-ui-rename.png`（改名后树仍旧名）
- `shots/21-trash-panel.png`（回收区有条目，树仍有 `readme.md`）
- `shots/33-paste-docs.png`（粘贴后磁盘有副本，树无）
- 磁盘对照：`ls docs` 显示 `ui-new.txt`/`ui-renamed.txt`/`watcher-renamed.txt`，树行列表无对应项。

**疑似根因（文件:行）**
- `ExplorerPanel.tsx:49-76` `mergeTreeDataFresh()`：当新树的子目录是 `lazy` 且旧树已加载（`!prev.lazy && prev.children`）时，直接保留旧 `prev.children` 并置 `lazy:false`（`ExplorerPanel.tsx:53-62`）；后端刷新根树时子目录总是 `lazy:true`（`backend/src/explorer/tree.py:29-46`），于是旧的子项永不更新。
- `frontend/src/components/explorer/treeUtils.ts:125-137` `collectLazyExpandedPaths()` 只收集“expanded 且仍 lazy”的目录；被合并后的目录已是 `lazy=false`，不会重新请求。
- `frontend/src/components/explorer/FileTree.tsx:163-178` `handleToggle()` 只在 `node.lazy` 时调用 `onLoadNodeChildren`；折叠/展开非 lazy 节点不会重新加载。
- `ExplorerPanel.tsx:240-244` 手动刷新同样走 `loadAuthorizedTree(..., silent=true)` 的合并路径，因此也无法修复。

**稳定性**：稳定复现（UI 新建/重命名/删除/外部创建均复现；根级对照正常）。

---

### P1-03 后端重启后文件监听静默失效，指示灯仍显示“已连接”，重新打开文件夹也不恢复

**现象**
- 资源管理器打开且监听指示灯为绿点（●）。停止并重启后端后：
  - 外部新建文件不再出现在树中；指示灯仍为 ●，没有“文件监听异常”toast。
  - 点击文件可复现 403：`打开文件失败: security check failed: path is outside authorized directories`。
  - 通过“最近 → fixture”重新打开（树重新加载、文件可打开）后，再外部新建文件仍不出现；灯仍为 ●。
  - 只有整页刷新后，灯先变 ○，随后重新连接为 ●，外部文件才恢复同步。

**复现步骤**
1. 打开 fixture 并确认监听灯为 ●。
2. 停止后端进程，再以同一临时 DB/端口重启（授权根在内存中丢失）。
3. 外部在 fixture 根目录新建 `post-restart-watch.txt`，等待 2s：树不出现，灯仍 ●。
4. 点“最近 → fixture”重新授权，树加载成功；再新建 `recovered-watch.txt`：仍不出现，灯仍 ●。
5. 整页刷新：灯变为 ○ → ●；新建 `after-reload-watch.txt` 出现。

**期望 vs 实际**
- 期望：连接断开后灯变 ○/显示错误；重新授权或后端恢复后自动重连；心跳超时应在秒级发现死连接。
- 实际：Vite 代理下的旧 SSE 流未触发前端错误，灯保持上次状态；`useExplorerWatch` 的 idle 超时为 5 分钟；重新打开同一 `rootPath` 不会重启监听 effect。监听实际已死但 UI 宣称正常。

**证据**
- 该阶段浏览器窗口被最小化，未能截取死连接画面；DOM/控制台记录：
  - 外部文件创建后 `document.querySelectorAll('.explorer-watch-dot')` 仍为 `explorer-watch-dot on`，树无 `post-restart-watch.txt` / `recovered-watch.txt`。
  - 打开文件 toast：`打开文件失败: security check failed: path is outside authorized directories`。
- `shots/46-reload.png`：整页刷新后树/标签恢复，随后外部文件可同步，证明只有刷新才恢复。

**疑似根因（文件:行）**
- `frontend/src/components/explorer/useExplorerWatch.ts:13` `IDLE_TIMEOUT_MS = 5 * 60 * 1000`；`:57-157` effect 只依赖 `rootPath`；`:130-141` 只在上游流真正抛错时重连。
- 后端重启后经 Vite 代理的旧流没有以 error/done 形式到达前端（实测灯未变），前端只能等 5 分钟 idle 超时。
- `useExplorerWatch.ts:157` 依赖数组只有 `[rootPath]`；“最近”重新打开同一路径不改 `rootPath`，不会建立新连接。
- `ExplorerPanel.tsx:416-421` 把 watch 生命周期与 `rootPath` 绑定；没有在“重新授权成功”后重启监听的逻辑。

**稳定性**：稳定复现（后端重启 2 次，行为一致；刷新后恢复）。

---

### P1-04 编辑器内 Ctrl+S 不保存，只有“保存”按钮生效

**现象**
- 打开可写文本文件，编辑后按 Ctrl+S：标签仍带 `*` 脏标记，后端没有 `POST /api/explorer/write`，磁盘内容不变。
- 点击右上角“保存”按钮：脏标记消失，后端返回 200，磁盘内容更新。

**复现步骤**
1. 打开 `utf8-中文内容.txt`。
2. 点击编辑器内容区，Ctrl+A，输入 `ctrl-s test`。
3. 按 Ctrl+S，等待 1.5s：标签仍为 `utf8-中文内容.txt*`。
4. 点“保存”：标签变为无 `*`，磁盘内容为 `ctrl-s test`。
5. 后端日志对照：Ctrl+S 后无 write 请求；点按钮后出现 `POST /api/explorer/write 200`。

**期望 vs 实际**
- 期望：Ctrl+S 与保存按钮等价。
- 实际：Monaco 处理/拦截了 Ctrl+S，`window` 上的 keydown 监听收不到，快捷键完全无效。

**证据**
- `shots/28-readonly-save.png`、`shots/31-ctrl-s.png`
- 后端日志：18:04:50 `POST /api/explorer/write 500`（按钮保存只读文件）；18:06:16 `POST /api/explorer/write 200`（按钮保存 `utf8`）；两次 Ctrl+S 均无请求。

**疑似根因（文件:行）**
- `frontend/src/components/explorer/EditorPanel.tsx:314-323`：在 `window` 上注册 keydown 监听；Monaco 的键盘服务会消费 Ctrl+S 并阻止冒泡/默认行为，事件到不了 window。
- 修复方向：在 Monaco `onMount` 中用 `editor.addCommand(KeyMod.CtrlCmd | KeyCode.KeyS, ...)` 绑定；或使用 capture 阶段监听。

**稳定性**：稳定复现（可写文件、只读文件均复现）。

---

## P2

### P2-01 文件夹选择器被压缩在 280px 资源管理器面板内

- 现象：选择器不是全窗口模态，而是 `.folder-picker-embed` 绝对定位在资源管理器面板内（280px 宽）。面包屑只能看到开头 `E: › DevCaches › Temp`，当前 `fixture` 被裁掉；底部提示“双击进入文件夹，单击选中路径”竖排成 3 行；“选择文件夹”按钮拥挤。
- 复现：打开文件夹 → 输入路径 → 回车。截图 `shots/02-folder-picker.png`。
- 期望：全窗口/足够宽度的选择器，面包屑能显示当前目录，提示横排。
- 根因：`ExplorerPanel.tsx:578-584` 在面板内渲染 `<FolderPickerDialog>`；`frontend/src/styles/explorer.css:493-501` `.folder-picker-embed { position:absolute; inset:0 }`；面包屑 `FolderPickerDialog.tsx:231-247` 渲染所有段，`explorer.css:573-583` 用 `overflow:hidden` 截断但不加省略号/末段优先；提示 `FolderPickerDialog.tsx:328-330`。
- 稳定性：稳定（1148×752、900、1440 均如此，因为受面板宽度而非窗口宽度约束）。

### P2-02 资源管理器工具栏溢出，刷新按钮被裁、监听指示灯完全在视口外

- 现象（1148×752，面板 280/284px）：`.explorer-toolbar` `scrollWidth=303 / clientWidth=279`；刷新按钮右边界 1153 > 视口 1148（约 5px 被裁）；watch dot x=1161 完全不可见。900 和 1440 下同样存在（面板仍 280）：刷新右边界 905/1445，dot 913/1453。
- 同时 header 因 5 个动作按钮在 280px 内换行，高度 106px（标题行 + 两个动作行，“切换资源管理器”单独一行）。
- 复现：打开任意文件夹，观察工具栏右侧；截图 `shots/03-folder-opened.png`、`04-root-expanded.png`、`34-900.png`、`35-1440.png`、`37-dark.png`。
- 期望：默认宽度下所有工具栏按钮和连接指示可见，或可换行/横向滚动；头部不要占三行。
- 根因：`frontend/src/styles/explorer-tree.css:3-10` 工具栏无 `flex-wrap/overflow`；`:12-21` `.explorer-search-input { flex:1 }` 未设 `min-width:0`，placeholder 宽度撑开；`:360-372` watch dot 用 `margin-left:auto` 追加，最终溢出。`explorer.css:993-1083` 容器查询在 ≤520px 时只处理 header/actions，未处理 toolbar。默认宽度来自 `persistence.ts` 的 280。
- 稳定性：稳定（与窗口宽度无关，取决于面板 280px）。
- 补充：在 1148 且左栏展开时，CDP 真实拖拽 resizer 最大只能到 284（聊天列最小宽度约束）；用户不先收起左栏就无法把面板拉宽到能容纳工具栏。`usePanelResize.ts:39-83`、`layoutBudget.ts:211-244`。

### P2-03 文件树长名称截断但没有 tooltip

- 现象：180 字符文件名在树中显示为省略号，节点和 label 都没有 `title`；无法通过悬停查看完整名称。实测 label `scrollWidth=1343 / clientWidth=231`，`title=null`。
- 复现：打开 fixture，悬停 `LLLL...txt`；截图 `shots/03-folder-opened.png`、`shots/27-longname.png`。
- 期望：悬停显示完整文件名（编辑器标签已有 `title`）。
- 根因：`frontend/src/components/explorer/FileTreeNode.tsx:199-211` 的 `.explorer-tree-label` 未设置 `title`；`explorer-tree.css:199-203` 只做 ellipsis。
- 稳定性：稳定。

### P2-04 搜索只覆盖已加载/已展开节点，未展开目录中的文件搜不到

- 现象：搜索 `readme` 返回 1 个结果；搜索 `inside` 返回 `0 个结果` / `未找到匹配结果`，但 `src/.hidden-dir/inside.txt` 实际存在（`src` 未展开）。搜索 `说明` 正常。
- 复现：在搜索框依次输入 `readme`、`inside`、`说明`；截图 `shots/23-search-inside.png`、`24-search-cleared.png`。
- 期望：“搜索文件…”应覆盖整个项目（或明确提示只搜索已加载节点）。
- 根因：`FileTree.tsx:402-445` 仅对内存中的 `tree` 做 `filterTree`；`treeUtils.ts:68-76` 对 lazy 目录无 `children` 可递归。后端已有 `/api/explorer/search`（`backend/app/api/explorer_routes.py:562`、`backend/src/explorer/search.py`），前端未使用。
- 稳定性：稳定。

### P2-05 跟随模式下一次文件创建产生两条相同 toast

- 现象：开启跟随模式后，Node 外部写入 `follow-test3.txt`，树中高亮正常，但页面出现两条 `follow-test3.txt 已更新` toast（同一事件被 watchdog 的 created+modified 各触发一次）。
- 复现：开启跟随模式（👁），外部新建文件；观察顶部 toast 与 `body.innerText`。
- 期望：一次操作一条提示（或按路径去抖）。
- 根因：`ExplorerPanel.tsx:392-414` 对每个 watch 事件都调用 `showInfo`，不分类型/不按路径节流；`useToastStore.ts:45-49` 无相同消息去重；watch 后端 `watcher.py:36-62` 会把 created/modified 都广播。
- 稳定性：稳定。

### P2-06 Toast 覆盖资源管理器头部与工具栏

- 现象：右上角固定 toast（240–360px 宽）覆盖资源管理器标题、跟随/最近/打开文件夹按钮和工具栏。只读保存失败时尤其明显；900px 宽时 toast 直接盖住面板头部。
- 证据：`shots/29-readonly-error.png`、`shots/34-900.png`。
- 期望：toast 不遮挡可交互控件（例如资源管理器打开时偏移、放到底部或限制宽度）。
- 根因：`frontend/src/components/shared/Toast.tsx:31-40` 固定 `top:16,right:16,zIndex:10000`；资源管理器占右侧 280px。
- 稳定性：稳定（任何 toast 都会覆盖）。

### P2-07 打开多个标签后，当前活动标签可能被挤出可视区

- 现象：依次打开 4 个文件后，活动标签 `readonly.txt` 不在标签栏可视区域（截图 28 仅显示前 3 个标签），用户看不到当前文件/关闭按钮；需要横向滚动或点击保存按钮触发聚焦滚动，活动标签才出现（截图 29）。
- 证据：`shots/28-readonly-save.png`、`shots/29-readonly-error.png`、`shots/34-900.png`。
- 期望：切换/打开标签时自动 `scrollIntoView` 活动标签。
- 根因：`EditorPanel.tsx:350-389` 标签栏 `overflow-x:auto`，但没有在 `activeFileId` 变化时滚动到活动项；`.editor-tab-actions` 固定在右侧，进一步压缩可见标签数。
- 稳定性：稳定（标签数 ≥4 时）。

### P2-08 “展开所有”只改 expanded 集合，不加载 lazy 目录；首次点击反而折叠

- 现象：折叠所有后点“展开所有”，`node_modules` 显示 `▼` 但没有任何子项（也未显示“空文件夹”）；第一次点击它变成 `▶`（折叠），第二次点击才加载 `pkg` 并展开。
- 复现：点 ⊖ → 点 ⊕ → 观察 `node_modules` → 点击一次/两次。
- 期望：展开所有应加载已展开的 lazy 目录；或不要把未加载目录标记为 expanded。
- 根因：`ExplorerPanel.tsx:363-366` `expandAll` 只 `setExpanded(collectDirPaths(tree))`；`FileTree.tsx:163-178` `handleToggle` 在已 expanded 时直接折叠，只有 `node.lazy` 且未 expanded 才加载。
- 稳定性：稳定。

### P2-09 启动时“未授权”状态弹 2–3 条误导性错误 toast

- 现象：后端重启后首次打开页面（上次根目录仍在 localStorage、尚未授权）：面板正确显示“该项目尚未授权，请明确重新打开”，但 1.4s 内同时出现：
  - `文件监听异常: SSE 403: Forbidden`（两次）
  - `打开文件夹失败: security check failed: path is outside authorized directories`
  4s 时仍有一条 watch 403。
- 复现：重启后端 → 刷新页面 → 1.5s 内观察 toast；截图 `shots/47-unauthorized.png`；`body.innerText` 记录。
- 期望：只显示占位和“重新打开项目”，或提示“需重新授权”，不要报错。
- 根因：`ExplorerPanel.tsx:424-427` 挂载时直接 `loadAuthorizedTree(rootPath)`，403 走 `showError`（`:232-235`）；`useExplorerWatch.ts:130-141` 每次 403 都 `onError` → `ExplorerPanel.tsx:416-421` toast；无授权状态短路/去重。
- 稳定性：稳定。

### P2-10 打开文件夹后根节点默认折叠，内容需再点一次

- 现象：通过选择器选中 fixture 后，树只显示一行 `▶ fixture`，不显示任何子项；用户必须再点根节点。之后展开/刷新还受 P1-01 影响。
- 证据：`shots/03-folder-opened.png`。
- 期望：打开文件夹后根节点自动展开，或记住该根目录的展开状态（首次可默认展开）。
- 根因：`ExplorerPanel.tsx:117-122` 初始 expanded 来自 `loadExpandedPaths(rootPath)`；首次为空；`loadTree` 不自动加入根路径。
- 稳定性：稳定（首次打开；若之前保存过根路径展开则可能不同）。

### P2-11 900px 自动收起左栏后，拉宽到 1440px 不自动恢复

- 现象：CDP 将视口设为 900×752 时，左栏自动收成图标栏，提示“空间不足，已暂时收起部分面板：可从侧边按钮重新打开。”；改回 1440×900 后左栏仍是图标栏，未恢复。
- 证据：`shots/34-900.png`、`shots/35-1440.png`。
- 期望：“暂时收起”在空间恢复后还原用户原偏好。
- 根因：`AppRoot` / `layoutBudget` 的自动收起路径（本基线未含此前的“只参与渲染、不写 store”修复）；具体写状态位置需 00/布局线程确认。此项跨资源管理器边界，列为相关发现。
- 稳定性：稳定（900→1440 两次）。

### P2-12 错误提示暴露原始路径/WinError，且措辞重复、中英混杂

- 现象：
  - 只读文件保存：`保存失败: write failed: [WinError 5] 拒绝访问。: 'E:\...\.readonly.txt.9kwu98ve.tmp' -> 'E:\...\readonly.txt'`（暴露临时文件路径）。
  - 点击已删除缓存节点：`打开文件失败: security check failed: path does not exist`。
  - 重复新建同名文件：`创建失败: already exists: already exists: E:\...\big-3mb.txt`（双重 already exists）。
- 证据：`shots/29-readonly-error.png`；`body.innerText` 记录（18:05 只读保存、18:08 重复创建、18:12 删除文件打开）。
- 期望：用户可读的中文提示；不暴露内部临时路径；缺失文件用“文件不存在”而非 security check。
- 根因：`backend/src/explorer/files.py:40-72` `os.replace` 的 `OSError` 直接透传；`ExplorerPanel.tsx:199`、`FileTree.tsx:231/246` 等 `formatError` 直接拼后端 message；后端 `explorer_routes.py:384-410` 写接口 500 返回原始异常；`files.py:114` `FileExistsError("already exists: ...")` 与路由 `_error_response` 再次加前缀。
- 稳定性：稳定。

### P2-13 无权限目录显示为“空文件夹”，与真正空目录不可区分

- 现象：创建 `denied-dir` 并 `icacls /deny` 拒绝当前用户读取；在树中展开后显示 `空文件夹`，没有错误/重试提示。后端 `_sorted_children` 捕获 `PermissionError` 返回 `[]`。
- 复现：ACL 拒绝目录 → 等 watch 出现 → 展开 → `空文件夹`；测试后已恢复 ACL 并删除目录。
- 证据：该阶段窗口最小化，无截图；DOM 记录 `emptyHints=["空文件夹","空文件夹"]`。
- 期望：显示“无权限读取”及重试，或至少与空目录区分。
- 根因：`backend/src/explorer/tree.py:57-63` `except PermissionError: return []`；前端无法区分空目录与读失败。
- 稳定性：稳定（ACL 测试一次）。

### P2-14 窄面板下树/编辑器上下分栏，树仅约 225px 高

- 现象：280px 宽且打开文件时，容器查询把 `.explorer-split` 改为纵向，树 pane `max-height:40%`（实测 225px），编辑器 338px。树中大量条目在折叠区外，右键/新建目标需要内部滚动；E2E 中多次出现目标在可视区外。
- 证据：`shots/18-ui-create.png`、`19-ui-rename.png`、`21-trash-panel.png`；实测 tree 225 / editor 338。
- 期望：可拖拽分栏高度或记住树/编辑器比例；至少保证树可滚动且目标可见。
- 根因：`explorer.css:1060-1082` 容器查询固定 40/60，`.explorer-tree-resizer` 在该宽度 `display:none`；`ExplorerPanel.tsx:644-647` 分栏。
- 稳定性：稳定。

### P2-15 主题菜单标签与动作不一致（相关观察）

- 现象：当前浅色主题，菜单显示“切换深色模式”；点击后实际进入“跟随系统”（系统为浅色，界面不变），菜单标签变为“跟随系统”；再点一次才真正变深色。
- 证据：菜单项实测 `['切换深色模式', ...]` → 点击后 `['跟随系统', ...]`；`shots/37-dark.png` 为第二次点击后的深色界面。
- 期望：标签描述点击后的实际结果（直接深色），或循环顺序改为 light→dark→auto。
- 根因：`frontend/src/components/shared/HamburgerMenu.tsx:30-32` 的循环 `dark→light, light→auto, auto→dark` 与 `toggleDark` 文案不匹配。
- 稳定性：稳定。

---

## P3 / 观察项

- **隐藏文件默认可见**：Windows `hidden` 属性文件 `hidden-win.txt`、点文件 `.hidden-file`、`.hidden-dir` 都出现在树中；后端仅按 `DENY_PATTERNS` 过滤 `.env*`、`.git`、密钥等（`security.py:20-24`）。若产品预期“默认隐藏系统隐藏文件”，需要增加属性过滤；当前未视为缺陷。
- **排序**：目录优先、名称不区分大小写排序（`tree.py:57-63`）；没有排序 UI/菜单。中文/emoji 按码点排序，`utf8-中文内容.txt` 排在末尾。
- **二进制预览**：`binary.bin` 只显示“此文件类型不支持预览”，没有大小/mime/“在系统中打开”入口（`editorTabParts.tsx` 的 `ImagePreview`）。
- **多选删除部分失败风险**：`FileTree.tsx:251-272` 顺序删除，只有全部成功才 `refresh()`；中途失败会留下已删除项但树不刷新的状态（未构造失败用例）。
- **“在文件管理器中显示”未实测**：菜单项存在；调用会打开系统资源管理器窗口（`explorer_routes.py:586-610`），为避免干扰未点击。
- **权限失败**：仅验证目录级 ACL；文件级权限、网络盘、长路径（>260）未测。
- **虚拟滚动/200 子项上限**：未构造 >400 可见节点或单目录 >200 子项，未验证 `VirtualFileTree` 与“显示更多”。
- **拖放移动**：未用真实鼠标测试树内拖拽/跨目录拖放；复制/粘贴已测（磁盘成功、树不刷新，见 P1-02）。
- **SSE 主动断网**：CDP `Network.emulateNetworkConditions(offline)` 未切断已建立流，灯仍 ●；最终用“停止/重启后端”验证了连接生命周期问题（P1-03）。
- **截图缺口**：ego 窗口在后半程被最小化，P1-03 死连接状态、P2-05 重复 toast、P2-13 无权限空目录没有截图；均有 DOM/控制台记录。原始截图索引见下。

---

## 已验证正常项（真实页面）

- 首次未打开文件夹：占位“尚未打开文件夹”与“打开文件夹”按钮正常（`01-initial.png`）。
- 打开文件夹（路径输入）、最近列表重新打开、根级懒加载展开、16px 缩进、空目录“空文件夹”、深层嵌套展示。
- 中文目录/文件、emoji、`[]`、`#`、空格文件名可显示与打开；内容正确（`39-special-names.png`）。
- 多选：Ctrl+点击追加、Shift+点击范围选择；右键菜单显示“复制选中 (n)/删除选中 (n)”（`32-multiselect.png`）。
- 右键菜单：新建文件/文件夹、重命名、复制/剪切、复制路径/相对路径、删除；菜单在视口右/下边缘会翻转，实测 `right=1130≤1134`、`bottom=768≤772`。
- 文件操作：UI 新建/重命名/删除到回收区/恢复均在后端与磁盘成功；回收区列表、恢复后文件回来（`22-restored.png`）。
- 编辑器：通过“保存”按钮保存成功、脏标记 `*`、关闭未保存提示（取消/不保存/保存）、标签置顶/关闭其他/关闭所有、特殊文件名标签。
- 边界文件：空文件（1 行空内容）、二进制（不支持预览）、3MB 文本（加载 <1s，Monaco 虚拟化，`40-bigfile.png`）、180 字符文件名（标签 max-width 160 + title 全路径）。
- 搜索：已加载树内按名称过滤、计数、自动展开含匹配的已加载目录、清空恢复。
- 监听：根级外部新建/重命名/删除会同步；跟随模式高亮 + “已更新”提示（但重复两条，见 P2-05）。
- 主题：深色下树、标签、编辑器 Monaco `vs-dark`、面板/文字颜色正常（`37-dark.png`）；恢复浅色正常。
- 响应式：900px 时自动收起左栏、出现“空间不足”提示，无横向页面滚动；1440px 布局无横向滚动（但左栏未恢复，见 P2-11）。
- 错误路径：只读保存 500 有 toast；重复创建 409 有 toast；删除后点击缓存节点 400 有 toast；后端不可用时打开文件 403 有 toast。
- 控制台：测试期间未捕获未处理异常（`Runtime.exceptionThrown` 为空）；仅有 watch 重连 `console.warn`（1/10、2/10、3/10）。

---

## 截图索引

| 文件 | 内容 |
| --- | --- |
| `01-initial.png` | 首次打开，未选择文件夹 |
| `02-folder-picker.png` | 文件夹选择器被压在 280px 面板内 |
| `03-folder-opened.png` | 选择 fixture 后根节点折叠、工具栏裁切 |
| `04-root-expanded.png` | 展开根目录，watch dot 在视口外 |
| `05-expanded-dirs.png` | docs/empty-dir 展开与空目录提示 |
| `06-editor-open.png` | 打开中文文件，树自动隐藏 |
| `07-dirty.png` / `08-saved.png` | 编辑脏标记 / 按钮保存后 |
| `09-split-tree-editor.png` | 窄面板上下分栏（树 225px） |
| `12-after-refresh.png` | 刷新后展开状态丢失 |
| `13-watch-create.png` | 根级外部新建出现，docs 内不出现 |
| `14-after-manual-refresh.png` | 手动刷新仍不出现 |
| `15-after-reopen-docs.png` | 折叠/展开 docs 仍不出现 |
| `16-watch-delete.png` | 外部删除 readme 后树仍显示 |
| `17-reopened-fresh.png` | 最近重新打开后树恢复新鲜 |
| `18-ui-create.png` | UI 新建 ui-new.txt 后树无该项 |
| `19-ui-rename.png` | UI 改名后树仍旧名 |
| `20-file-context-menu.png` | 文件右键菜单 |
| `21-trash-panel.png` | 回收区有 readme，树仍显示 readme |
| `22-restored.png` | 恢复后回收区清空 |
| `23-search-inside.png` | 搜索未展开目录内文件 0 结果 |
| `24-search-cleared.png` | 清空搜索恢复 |
| `25-explorer-wide.png` | CDP 拖拽后宽度仅 284 |
| `26-binary.png` | 二进制不支持预览 |
| `27-longname.png` | 长文件名标签截断（树无 tooltip） |
| `28-readonly-save.png` | 只读文件脏标记、活动标签不可见 |
| `29-readonly-error.png` | 原始 WinError toast 覆盖头部 |
| `30-unsaved-close.png` | 关闭未保存文件提示 |
| `31-ctrl-s.png` | Ctrl+S 后仍脏 |
| `32-multiselect.png` | Ctrl/Shift 多选与选中计数菜单 |
| `33-paste-docs.png` | 粘贴后磁盘有副本、树无 |
| `34-900.png` | 900px 布局、toast 遮挡头部、工具栏裁切 |
| `35-1440.png` | 1440px 左栏仍未恢复、工具栏仍裁切 |
| `36-restored.png` | 恢复视口 |
| `37-dark.png` / `38-light-restored.png` | 深色 / 恢复浅色 |
| `39-special-names.png` | `[]`、`#`、空格文件名打开 |
| `40-bigfile.png` | 3MB 文本打开 |
| `46-reload.png` | 整页刷新后树/标签恢复 |
| `47-unauthorized.png` | 后端重启后未授权占位 |

---

## 建议修复优先级（供 00 参考）

1. 统一路径规范（分隔符/大小写）并在 `mergeTreeDataFresh` 中按归一化路径比较；`loadTree` 成功后用后端返回路径更新 `expectedPathRef`。
2. 刷新时对“所有已展开目录”重新拉取 children（不要只处理 lazy），或让后端在静默刷新中返回两层数据；`handleToggle` 对非 lazy 目录提供强制重载。
3. watch 增加心跳超时（如 30s）与“重新授权/后端恢复”后的强制重连；指示灯以心跳为准，不以最后一次成功为准。
4. Monaco 内绑定 Ctrl+S（或 capture 监听）。
5. 工具栏/头部布局按 280px 宽度重排（`min-width:0`、换行/滚动、连接点内联）；默认资源管理器宽度建议 ≥320 或允许收起左栏后拖宽。
6. 文件夹选择器改为全窗口 portal 模态（此前修复分支已有实现，本基线未包含）。
7. 树 label 增加 `title`；搜索接入后端 `/explorer/search` 或明确范围；toast 去重/避让资源管理器；活动标签自动滚动。

---

## 清理与进程

- 调查期间启动的前端（5399）与后端（58081，临时 DB）将在报告完成后停止。
- 临时 `.env`、认证文件、`vite.e2e.config.ts`、`node_modules` junction 将移除；截图/日志保留在 `E:/DevCaches/Temp/explorer-e2e/`。
- 未提交 `.env`、DB、截图、fixture；未 push。

---

# 修复记录 r2（2026-11-08，按收窄范围）

- 基线：先 fast-forward 合并主检出 `feat/plan-any-mode-markdown` @ `44c5df53`（已含原报告 commit 与部分 watch/选择器修复）。
- 本轮负责：P1-01/02/03/04；相关 P2-05/07/09/10/12（ExplorerPanel 部分）。其余 P2/P3 由其他线程负责，未改 FileTree/FileTreeNode/CSS/backend/editorTabParts。
- 前端：68 文件 / 471 测试通过；`tsc --noEmit` exit 0；explorer 目录 eslint 0 error。
- 端口：后端 58084（临时 DB `E:/DevCaches/Temp/explorer-fix`），前端 5184；未触碰 58080。

## Commits

| Commit | 内容 |
| --- | --- |
| `bc4b8d52` | `fix(explorer): normalize tree paths and refresh loaded dirs` — P1-01 + P1-02；同文件内一并落地 P2-05 调用点、P2-09 静默启动、P2-10 根节点展开、P2-12 前端文案 |
| `7391377c` | `fix(explorer): reconnect watch after heartbeat loss` — P1-03；watch 文件内 P2-05 按路径节流 |
| `d17db9fe` | `fix(explorer): bind Ctrl+S in Monaco and reveal active tab` — P1-04 + P2-07 |

> P1-01 与 P1-02 共用 `loadTree` / `loadAuthorizedTree` / `mergeTreeDataFresh` 同一批函数，按 hunk 拆分会互相污染，故合并为一个提交；两者分别有独立测试与浏览器复测。

## 浏览器复测（ego 0.6.2.7，真实 5184+58084）

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| P1-01 路径归一化 / 刷新保持展开 | 根/子节点路径均为 `/`；刷新后 root/docs/zh 保持展开，树不折叠 | `E:/DevCaches/Temp/explorer-fix/fix-02-open.png`、`fix-03-refresh.png` |
| P1-02 已展开目录刷新 | 外部 `docs/fix-watch-new.txt` 与根级文件自动出现；UI 新建/重命名/删除即时反映 | `fix-03-refresh.png`、`fix-04-ui-ops.png` |
| P1-03 watch 心跳与重连 | 后端重启 + 40s 后灯变灰、无 toast；最近重新打开后自动重连，外部文件出现 | `fix-06-watch-idle.png`、`fix-07-watch-recovered.png` |
| P1-04 Monaco Ctrl+S | 编辑后 Ctrl+S：`POST /api/explorer/write 200`，脏标记清除，磁盘内容更新 | `fix-05-ctrl-s.png` |
| P2-05 重复 toast | 跟随模式一次外部创建只出现一条“已更新” | 页面文本计数 = 1 |
| P2-07 活动标签可见 | 6 个标签时活动标签完整可见（scrollLeft 661.6，右边界 1135≤1136） | `fix-11-tabs.png` |
| P2-09 未授权启动 | 只显示“该项目尚未授权，请明确重新打开”，1.6s/4.4s 均无 403/打开失败 toast | `fix-09-unauthorized.png` |
| P2-10 根节点自动展开 | 选择文件夹后根节点直接 `is-expanded`，子项可见 | `fix-02-open.png` |
| P2-12 前端错误文案 | 选择文件作为根目录 → `打开文件夹失败: 目标不是有效文件夹`，无原始 security 前缀 | `fix-12-friendly-error.png` |

另外确认合并带入的选择器全窗口 modal 生效（1136×774，内层 680×401），根路径与 `localStorage` 均归一化为 `/`。

## 本轮仍未验证 / 边界

- P1-03 的 30s 心跳超时用单元测试（注入 20ms）验证；真实后端重启场景等待 40s 后观察灯变灰，未逐秒计时。
- P2-12 仅真实页面验证“非目录”一类；其余映射（权限、版本冲突、无法解析、文件过大等）由 `treeUtils.test.ts` 覆盖。
- Windows 路径大小写差异（盘符大小写）未归一化；如需大小写不敏感比较需另行决定。
- 30s idle 超时在极端 GC/网络停顿下可能重连一次；心跳周期 15s，当前取 2 次未收到即重连。
- 其余 P2（选择器之外）与 P3 未在本轮修改，按主控分派由其他线程处理。

## 清理

- 本轮启动的后端 58084、前端 5184 已停止；临时 `.env`、`vite.e2e.config.ts`、认证文件、`node_modules` junction 已移除。
- 证据保留在 `E:/DevCaches/Temp/explorer-fix/`；未提交 `.env`、DB、截图、fixture；未 push。

