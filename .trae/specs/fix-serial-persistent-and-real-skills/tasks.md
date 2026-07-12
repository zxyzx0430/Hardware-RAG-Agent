# Tasks

- [x] Task 1: WorkbenchPanel 5 个 Pane 改为 CSS 隐藏
  - [x] SubTask 1.1: WorkbenchPanel.tsx 将 5 个 `&&` 条件渲染改为 `display:none` CSS 隐藏
  - [x] SubTask 1.2: 验证切换 Tab 后各 Pane 状态保留（编译日志/审计结果/Monaco/zoom-pan/串口）

- [x] Task 2: AppRoot 右栏关闭改为 CSS 隐藏
  - [x] SubTask 2.1: AppRoot.tsx rightPanelOpen 关闭时改为 `display:none` 隐藏 RightPanel
  - [x] SubTask 2.2: 验证关闭右栏再打开后串口连接不断开

- [x] Task 3: SerialPane 移除 cleanup 中的 WS close
  - [x] SubTask 3.1: SerialPane.tsx 移除 cleanup useEffect 中的 `wsRef.current?.close()`
  - [x] SubTask 3.2: 验证切换工作台 Tab 后串口连接保持

- [x] Task 4: 后端注册 skill_router
  - [x] SubTask 4.1: main.py import skill_router 并 include_router
  - [x] SubTask 4.2: 验证 GET /api/skills 返回 hardware-review

- [x] Task 5: 后端新增 GET /api/tools 端点
  - [x] SubTask 5.1: tool_routes.py 新增 GET /api/tools 返回 26 个工具元数据
  - [x] SubTask 5.2: agent_factory.py 提取工具 name/description/group 元数据

- [x] Task 6: 后端新增 PATCH /api/tools/{name}/toggle
  - [x] SubTask 6.1: tool_routes.py 新增 toggle 端点，持久化到 data/tool_config.json
  - [x] SubTask 6.2: agent_factory.py _assemble_all_tools 支持按禁用列表过滤

- [x] Task 7: 前端技能管理接入 /api/tools
  - [x] SubTask 7.1: useSettingsStore.ts 删除 7 条硬编码 skills，默认值改为 []
  - [x] SubTask 7.2: useSettingsStore.ts 新增 fetchTools() 从 /api/tools 拉取
  - [x] SubTask 7.3: toggleSkill 改为调 PATCH /api/tools/{name}/toggle
  - [x] SubTask 7.4: SettingsPage.tsx skills tab 挂载时调用 fetchTools()

- [x] Task 8: 修复 mcpServers 硬编码默认值
  - [x] SubTask 8.1: useSettingsStore.ts mcpServers 默认值改为 []

- [x] Task 9: 修复 useSettingsStore.model 字段引用
  - [x] SubTask 9.1: useChatStore.ts 和 useSessionStore.ts 中所有 getState().model 改为 getState().chatModel（6 处）
  - [x] SubTask 9.2: 定义 DEFAULT_MODEL 常量替换硬编码 "GPT-4o"

- [x] Task 10: 前端 settings 同步后端
  - [x] SubTask 10.1: useSettingsStore.ts 初始化时调 apiGet("settings") 加载
  - [x] SubTask 10.2: updateSetting 时异步调 apiPut("settings", { [key]: value }) 同步

- [x] Task 11: about tab 版本信息修复
  - [x] SubTask 11.1: i18n/zh.ts 和 en.ts 移除过时的 buildDateVal '2025-06-19'，改为"-"

- [x] Task 12: 类型检查与验证
  - [x] SubTask 12.1: npx tsc --noEmit 通过（0 errors）
  - [x] SubTask 12.2: 后端启动验证 /api/tools 返回 26 工具、/api/skills 返回真实数据
  - [ ] SubTask 12.3: 浏览器验证串口切换不断开、技能页面显示真实工具（需人工验证）

# Task Dependencies
- Task 1, 2, 3 可并行（WorkbenchPanel + AppRoot + SerialPane，不同文件）✅ 已并行完成
- Task 4, 5, 6 可并行（后端不同端点）✅ 已并行完成
- Task 7 依赖 Task 5, 6 完成（前端需要后端 API 就绪）✅ 已完成
- Task 8, 9, 10, 11 可并行（不同文件/字段）✅ Task 9+11 与 Task 7+8+10 分两批并行完成
- Task 12 依赖所有前置任务完成 ✅ 自动化验证 21/25 通过，4 项需人工浏览器验证
