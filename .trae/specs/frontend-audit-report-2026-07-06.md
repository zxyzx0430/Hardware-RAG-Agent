# 前端界面实测审计报告

> 审计时间：2026-07-06  
> 审计工具：agent-browser (Chrome CDP)  
> 目标地址：http://127.0.0.1:5173/  
> 截图目录：e:\Desktop\agent\.trae\specs\  
> 审计范围：首页、会话列表、导航切换、设置页各 tab、知识库列表、输入控件状态

---

## 一、执行摘要

本次以真实用户视角对 Hardware RAG Agent 前端进行无代码实测，共发现 **2 个 P1 严重问题**、**4 个 P1 重要问题**、**3 个 P2 优化项**。核心问题集中在：**右侧工作台默认展开对新用户造成压迫**、**知识库列表项可访问性缺失（generic 无标题）**、**会话/项目创建入口语义不清**、**全页面可访问性树被根 div 文本污染**。

未配置真实 API Key，因此涉及后端鉴权的页面仅记录其表现，不做功能修复要求。

---

## 二、问题清单（按严重程度排序）

### P0 / P1 严重问题

| 编号 | 严重程度 | 问题描述 | 复现步骤 | 截图/证据 | 涉及文件（推断） |
|------|---------|---------|---------|-----------|----------------|
| 1 | **P1** | 右侧「工作台」面板在聊天页默认展开，大量串口/烧录/接线图控件占据约 1/4 屏幕，对未配置 API Key 的新用户形成认知压迫 | 1. 打开首页 http://127.0.0.1:5173/；2. 观察右侧「工作台」已展开 | audit-browser-home.png | `frontend/src/components/workbench/WorkbenchPanel.tsx` 或相关 layout；`useChatStore` 中 rightPanelOpen 默认值 |
| 2 | **P1** | 知识库文档列表项在可访问性树中显示为 generic 空元素，屏幕阅读器和自动化工具无法识别标题；虽然视觉上有标题 | 1. 进入「知识库」页；2. 查看列表项 DOM / agent-browser snapshot；3. `get text @e12` 返回空字符串 | audit-browser-kb.png | `frontend/src/components/knowledge/DocumentList.tsx` 或 `KbPage.tsx` 中列表项未将文件名写入文本节点 |
| 3 | **P1** | 全站根节点被包裹在一个巨大的 generic clickable div 中，其文本为整页文本拼接（如 "未命名对话0 来源会话新建全部..."），严重污染可访问性树 | 1. 任意页面执行 `agent-browser snapshot -i`；2. 观察根节点 `generic` 包含所有子元素文本 | 所有 snapshot 输出 | `frontend/src/App.tsx` 或根布局组件中过度使用 `div` + `onClick` 导致事件委托未做角色隔离 |
| 4 | **P1** | 创建项目/会话的「+ 新建」按钮语义不清：点击后变成「项目名称」输入框，用户会误以为是新建会话，实际在新建项目；且输入框无任何说明 | 1. 在聊天页点击左侧「+ 新建」；2. 按钮变为项目名输入框 | audit-browser-session-list.png、audit-browser-project-created.png | `frontend/src/components/session/SessionList.tsx` 或 `ProjectTabs.tsx` |

### P1 重要问题

| 编号 | 严重程度 | 问题描述 | 复现步骤 | 截图/证据 | 涉及文件（推断） |
|------|---------|---------|---------|-----------|----------------|
| 5 | **P1** | 创建项目后立即弹出多个「加载会话消息失败: API 401: Unauthorized」toast，体验糟糕；日志页也充斥 401 错误 | 1. 点击「+ 新建」；2. 输入项目名回车；3. 观察右上角连续 toast | audit-browser-project-created.png、audit-browser-settings-logs.png | `frontend/src/stores/useSessionStore.ts` 或 `useChatStore.ts` 中请求未处理 401 友好降级；鉴权相关 API 调用 |
| 6 | **P1** | 会话列表中会话标题固定为「新对话」，且全部显示「0 条消息 · 日期」，若存在多个空会话将无法区分 | 1. 创建项目后观察会话列表；2. 所有会话项标题均为「新对话」 | audit-browser-project-created.png | `frontend/src/components/session/SessionList.tsx` 默认标题逻辑 |

### P2 优化项

| 编号 | 严重程度 | 问题描述 | 复现步骤 | 截图/证据 | 涉及文件（推断） |
|------|---------|---------|---------|-----------|----------------|
| 7 | **P2** | 设置页「用量」tab 在未配置 API Key 时直接展示「API 401: Unauthorized」原始错误，缺少空状态/引导 | 1. 进入设置；2. 切换到「用量」 | audit-browser-settings-usage.png | `frontend/src/components/settings/UsageTab.tsx` |
| 8 | **P2** | 设置页「技能」tab 仅有标题和副标题，内容完全空白，缺少空状态提示或「暂无技能」引导 | 1. 进入设置；2. 切换到「技能」 | audit-browser-settings-skills.png | `frontend/src/components/settings/SkillsTab.tsx` |
| 9 | **P2** | 设置页「记忆」tab 的两个 textarea 在可访问性树中无 label，仅有 placeholder，屏幕阅读器无法识别字段含义 | 1. 进入设置 → 记忆；2. `agent-browser snapshot -i` 中 textbox 无对应 label | audit-browser-settings-memory.png | `frontend/src/components/settings/MemoryTab.tsx` |
| 10 | **P2** | 浏览器标签页标题为「未命名对话」/「新对话」，未包含应用名，多标签场景下辨识度低 | 1. 观察浏览器 tab 标题 | 首页截图 | `frontend/index.html` 或 `useDocumentTitle` 相关逻辑 |

---

## 三、正常/可接受项

- 首页空状态引导清晰：「第一步：配置 API Key」+「去设置」按钮明确。
- 收藏夹空状态有图标和说明「暂无收藏内容 / 在对话中点击书签图标即可收藏消息」。
- 设置页「API 配置」「RAG 参数」「外观」「日志」「工具审计」「MCP 服务」「关于」tab 可正常切换并显示内容。
- 右侧工作台可正常折叠/展开（折叠后主区域明显变宽）。
- 输入框、模型选择器、串口参数下拉框等控件状态符合预期（未配置 API Key 时发送按钮禁用合理）。

---

## 四、限制说明

- 未配置真实 API Key，因此「用量」tab 的 401 错误、部分后端请求失败属于预期限制，本报告仅记录其前端表现，不建议投入后端修复。
- 未测试真实聊天发送、SSE 流式输出、文件上传、串口连接等需要 Key/硬件的操作。

---

## 五、建议修复优先级

1. **第一优先级**：修复右侧工作台默认展开（改为默认折叠或首次访问引导式展开） + 知识库列表项可访问性文本缺失。
2. **第二优先级**：清理全站根节点 generic clickable div 的文本污染；明确「+ 新建」是新建项目，并给出会话创建入口；处理 401 toast 轰炸。
3. **第三优先级**：优化设置页「用量」「技能」空状态；为记忆 textarea 补充 label；优化浏览器标题。
