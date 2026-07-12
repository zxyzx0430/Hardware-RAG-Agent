# CAB Tasks

## 第一阶段：后端基础接口

- [x] Task 1: 新增 explorer_routes.py 基础文件接口
  - [x] SubTask 1.1: 实现 `POST /api/explorer/open` 打开文件夹并返回树结构
  - [x] SubTask 1.2: 实现 `GET /api/explorer/read` 读取文本/二进制文件内容
  - [x] SubTask 1.3: 实现 `POST /api/explorer/write` 保存文件内容
  - [x] SubTask 1.4: 实现文件操作接口：新建、重命名、删除、移动
  - [x] SubTask 1.5: 添加路径安全校验，禁止越界访问用户未授权的目录

- [x] Task 2: 后端文件系统实时监听
  - [x] SubTask 2.1: 引入 watchdog 监听用户打开的文件夹
  - [x] SubTask 2.2: 通过 SSE 或 WebSocket 向前端推送 change/create/delete/rename 事件
  - [x] SubTask 2.3: 处理监听边界：文件夹关闭时停止监听、监听失败时降级为轮询

- [x] Task 3: 在 main.py 注册 explorer_routes

## 第二阶段：前端第四栏骨架

- [x] Task 4: 改造 AppRoot.tsx 支持四栏布局
  - [x] SubTask 4.1: 在 RightPanel 右侧新增 ExplorerPanel 容器
  - [x] SubTask 4.2: 新增第四栏 resizer，支持 200px~50% 宽度拖拽
  - [x] SubTask 4.3: 实现第四栏折叠为窄边条，点击展开

- [x] Task 5: 新增 useAppStore 中 explorer 相关状态
  - [x] SubTask 5.1: `explorerOpen`、`explorerWidth`、`explorerRootPath`
  - [x] SubTask 5.2: `openFiles`、`activeFileId`、`pinnedFileIds`
  - [x] SubTask 5.3: `followMode`、`recentFolders`
  - [x] SubTask 5.4: 持久化到 localStorage（宽度、打开状态、recentFolders、openFiles）

## 第三阶段：文件树组件

- [x] Task 6: 实现 FileTree 递归组件
  - [x] SubTask 6.1: 渲染文件夹/文件节点，支持展开折叠
  - [x] SubTask 6.2: 文件夹选择对话框打开（webkitdirectory）
  - [x] SubTask 6.3: 顶部搜索框过滤文件树
  - [x] SubTask 6.4: 右键菜单：新建文件/文件夹、重命名、删除、复制路径
  - [x] SubTask 6.5: 拖拽移动文件/文件夹

- [x] Task 7: 实现 ExplorerPanel 容器
  - [x] SubTask 7.1: 整合 FileTree、打开文件夹按钮、搜索框
  - [x] SubTask 7.2: 监听后端 SSE 事件并刷新文件树
  - [x] SubTask 7.3: 显示最近打开的文件夹下拉列表

## 第四阶段：编辑器组件

- [x] Task 8: 实现 EditorPanel
  - [x] SubTask 8.1: 顶部 tab 栏渲染 openFiles，支持切换、关闭、pin
  - [x] SubTask 8.2: 集成 MonacoEditor，支持编辑、语法高亮、主题跟随
  - [x] SubTask 8.3: 手动保存（Ctrl+S / 按钮）调用后端 write 接口
  - [x] SubTask 8.4: 未保存状态标记（tab 上星号/圆点）
  - [x] SubTask 8.5: 关闭未保存 tab 时弹窗确认

- [x] Task 9: 编辑器状态管理
  - [x] SubTask 9.1: 打开文件时 push 到 openFiles 并激活
  - [x] SubTask 9.2: 文件内容变更时标记 dirty
  - [x] SubTask 9.3: 保存成功后清除 dirty 状态
  - [x] SubTask 9.4: 外部变更冲突提示「是否重新加载」

## 第五阶段：Agent 联动与 diff

- [x] Task 10: Agent 代码一键打开到编辑器
  - [x] SubTask 10.1: generate_code / render_code 输出代码块时添加「在编辑器中打开」按钮
  - [x] SubTask 10.2: 点击后在第四栏打开未保存 buffer

- [x] Task 11: 跟随模式
  - [x] SubTask 11.1: 第四栏顶部添加「跟随模式」开关
  - [x] SubTask 11.2: 开启后高亮/自动滚动到最新变化的文件

- [x] Task 12: diff 视图
  - [x] SubTask 12.1: 文件 tab 上显示变更标记
  - [x] SubTask 12.2: 点击标记打开 diff 视图（Git HEAD 优先，否则内存快照）
  - [x] SubTask 12.3: diff 视图支持左右/上下布局切换

## 第六阶段：验证与收尾

- [x] Task 13: 端到端手动验证
  - [x] SubTask 13.1: 打开文件夹、浏览、新建、重命名、删除、移动
  - [x] SubTask 13.2: 编辑文件并保存，验证磁盘内容更新
  - [x] SubTask 13.3: 外部修改文件，验证前端实时刷新和冲突提示
  - [x] SubTask 13.4: Agent 生成代码后点击「在编辑器中打开」
      前后端 TypeScript / Python 语法检查通过；/explorer/diff 冒烟测试通过；路由已在 main.py 注册。

- [x] Task 14: 更新 api-contract.md 和 pitfalls.md（如遇到新问题）
      已更新 api-contract.md 接口目录与详情。

# Task Dependencies

- Task 5 depends on Task 4
- Task 6 depends on Task 5
- Task 7 depends on Task 6
- Task 8 depends on Task 5
- Task 9 depends on Task 8
- Task 10 depends on Task 9
- Task 11 depends on Task 7
- Task 12 depends on Task 9
- Task 13 depends on Task 12
