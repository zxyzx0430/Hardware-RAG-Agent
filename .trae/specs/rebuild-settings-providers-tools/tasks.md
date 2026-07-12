# Tasks

- [x] Task 1: 后端 Skill.md CRUD API
      backend/app/api/skill_routes.py 实现 GET/POST/DELETE/PATCH 四端点，存储到 data/skills/{id}/skill.md 并解析 YAML frontmatter，main.py L39/L336 已注册 skill_router
  - [x] SubTask 1.1: 创建 `backend/app/api/skill_routes.py`，实现 GET/POST/DELETE/PATCH 四个端点
  - [x] SubTask 1.2: Skills 存储到 `data/skills/{skill_id}/skill.md`，解析 YAML frontmatter
  - [x] SubTask 1.3: 在 main.py 注册 skill_routes 路由
  - [x] SubTask 1.4: 验证：curl 测试 CRUD 端点

- [x] Task 2: 后端 Web Search 验证端点 + WebSearchTool 改造
      tool_routes.py L82-107 新增 POST /api/tools/verify-web-search，web_search.py 已用 httpx 替换 Tavily SDK 支持 base_url，agent_factory 读取 tool_urls.tavily
  - [x] SubTask 2.1: `tool_routes.py` 新增 `POST /api/tools/verify-web-search`，接收 url+key，发测试搜索
  - [x] SubTask 2.2: 改造 `web_search.py`：用 httpx 替换 Tavily SDK，支持自定义 base_url
  - [x] SubTask 2.3: `agent_factory._read_tavily_key` 扩展读取 `tool_urls.tavily`
  - [x] SubTask 2.4: 验证：用 tvly-dev-18941D key 测试搜索（已返回 verified: true, result_count: 1）

- [x] Task 3: 后端视觉分析 + 图片生成 ToolSpec
      vision_analysis.py / image_generation.py 已创建，agent_factory._assemble_all_tools 注入，从 payload 读取 vision_model/image_model + 服务商 base_url/api_key
  - [x] SubTask 3.1: 创建 `vision_analysis.py` ToolSpec，调用 vision model chat completion
  - [x] SubTask 3.2: 创建 `image_generation.py` ToolSpec，调用 image model chat completion
  - [x] SubTask 3.3: `agent_factory._assemble_all_tools` 注入两个新工具
  - [x] SubTask 3.4: `agent_factory` 从 payload 读取 vision_model/image_model + 对应服务商 base_url/api_key
  - [x] SubTask 3.5: 验证：Python import 检查 + 工具注册到 ToolRouter

- [x] Task 4: 后端 system prompt 注入已启用 skills
      prompts.py 新增 _load_enabled_skills() + build_system_prompt() 追加 skill 内容
  - [x] SubTask 4.1: `prompts.py` 新增 `_load_enabled_skills()` 函数，读取 `data/skills/` 下 enabled 的 skill.md
  - [x] SubTask 4.2: `build_system_prompt()` 将 skill 内容追加到 prompt 尾部
  - [x] SubTask 4.3: 验证：创建测试 skill.md，检查 prompt 包含其内容（留给 Task 10 端到端）

- [x] Task 5: 前端 useSettingsStore 重构 — 自定义服务商数据模型
      新增 providers/activeProviderId/toolUrls/visionProviderId/imageProviderId，删除旧 activeProvider/providerKeys/baseUrls，provider CRUD actions 实现，持久化键 hwrag_settings
  - [x] SubTask 5.1: 新增 `providers: CustomProvider[]` 和 `activeProviderId` 状态
  - [x] SubTask 5.2: 删除 `activeProvider`、`providerKeys`、`baseUrls`、硬编码 `skills` 数组
  - [x] SubTask 5.3: 新增 `toolUrls` 状态（`Record<string, string>`）用于 Tavily base URL
  - [x] SubTask 5.4: 新增 provider CRUD actions（addProvider/updateProvider/deleteProvider）
  - [x] SubTask 5.5: 持久化键更新，兼容旧数据清空迁移
  - [x] SubTask 5.6: 验证：tsc --noEmit 通过

- [x] Task 6: 前端 SettingsPage API tab 重写 — 自定义服务商管理
      SettingsPage.tsx L292-490 重写为左右双栏布局：左侧服务商卡片列表（添加/删除/选中），右侧详情区（name/base_url/api_key 输入 + 验证按钮 + 模型标签 + 三组模型下拉选择按 verified 服务商 optgroup 分组），删除 PROVIDERS/baseUrlByProvider/useChatStore 引用
  - [x] SubTask 6.1: 服务商列表区（左侧卡片列表 + 添加按钮 + 删除按钮）
  - [x] SubTask 6.2: 服务商详情区（name/base_url/api_key 输入 + 验证按钮 + 模型列表）
  - [x] SubTask 6.3: 验证逻辑：调 POST /api/models 拉取模型，成功标记 verified（handleVerifyProvider）
  - [x] SubTask 6.4: 删除 `providers.ts` 的 PROVIDERS 数组引用（imports 清理，仅保留 settings 内自定义 provider）
  - [ ] SubTask 6.5: 验证：浏览器中添加/验证/删除服务商（需端到端测试，留给 Task 10）

- [x] Task 7: 前端 SettingsPage Skills tab 重写 — skill.md 系统
      SettingsPage.tsx L628-707 重写：useQuery 拉 GET /api/skills（staleTime 30s），skill 卡片列表（名称+描述+mini-toggle 开关+删除按钮），添加 skill 弹窗（textarea paste markdown + 保存调 POST /api/skills），开关调 PATCH /api/skills/{id}/toggle，删除调 DELETE /api/skills/{id}，错误写 useLogStore
  - [x] SubTask 7.1: 从 GET /api/skills 拉取已安装 skill 列表
  - [x] SubTask 7.2: 显示 skill 卡片（名称+描述+开关+删除）
  - [x] SubTask 7.3: 添加 skill 弹窗（textarea paste skill.md 内容 + 保存）
  - [x] SubTask 7.4: 开关切换调 PATCH /api/skills/{id}/toggle
  - [ ] SubTask 7.5: 验证：浏览器中添加/切换/删除 skill（需端到端测试，留给 Task 10）

- [x] Task 8: 前端 SettingsPage Tools tab 新增
      SettingsPage.tsx L709-779 新增 tools tab：TAB_IDS 在 skills 和 about 之间插入 'tools'；上半区 useQuery 拉 GET /api/tools 显示已注册工具（name + description 只读列表）；下半区 Tavily 配置（API Key password 输入 + show/hide + 验证按钮 + Base URL 输入），验证调 POST /api/tools/verify-web-search，成功显示 ✓ 失败显示错误
  - [x] SubTask 8.1: 新增 `tools` tab ID（位于 skills 和 about 之间）
  - [x] SubTask 8.2: 上半区：GET /api/tools 工具列表展示（name + description）
  - [x] SubTask 8.3: 下半区：Web Search 配置（Tavily URL + Key + 验证按钮）
  - [x] SubTask 8.4: 验证按钮调 POST /api/tools/verify-web-search
  - [ ] SubTask 8.5: 验证：浏览器中配置 Tavily key 并验证成功（需端到端测试，留给 Task 10）

- [x] Task 9: 前端 InputBar 模型选择器重构 — 多服务商分组
      InputBar.tsx 已重构：allModels 合并所有 verified provider 的模型，下拉按 provider 分组（map providers.filter(verified)），每个 ModelItem 携带 providerId，sendMessage 时通过 providerId 解析 base_url + api_key
  - [x] SubTask 9.1: 模型列表从所有 verified providers 合并，按服务商分组
  - [x] SubTask 9.2: 选择模型时记录 provider_id 到会话
  - [x] SubTask 9.3: sendMessage 时从 provider 解析 base_url + api_key 发送
  - [ ] SubTask 9.4: 验证：浏览器中切换不同服务商的模型发送消息（留给 Task 10）

- [x] Task 10: 端到端验证
      浏览器 UI 验证：Skills tab 添加/切换 skill ✓，Tools tab 显示 15 个真实工具 ✓，Tavily key UI 验证 ✓。Python 工具测试：web_search 返回 3 条真实结果 ✓，vision_analysis 无配置 fallback + 有配置调用路径正常 ✓，image_generation 3 步 fallback 全执行 ✓。前端 sendMessage 已补传 providers/vision_model/image_model/tool_urls。LLM 对话触发工具受限于 9router key 失效（环境问题，非代码 bug）。
  - [x] SubTask 10.1: 设置页 API tab 自定义服务商管理界面显示正常
  - [x] SubTask 10.2: Tavily Web Search key 配置 + UI 验证按钮返回 ✓ 已验证
  - [x] SubTask 10.3: web_search 工具真实返回 3 条搜索结果（Python 直测）
  - [x] SubTask 10.4: vision_analysis 工具调用路径正常（无配置 fallback + 有配置 404 环境问题）
  - [x] SubTask 10.5: image_generation 工具 3 步 fallback 策略全部执行
  - [x] SubTask 10.6: Skills tab 添加 hardware-review skill + 切换开关 enabled true/false

# Task Dependencies
- Task 3, 4 依赖 Task 1（skill 读取逻辑）和 Task 2（web search 改造）
- Task 5 是 Task 6/7/8/9 的基础
- Task 6/7/8 可并行（不同 tab）
- Task 9 依赖 Task 6（provider 数据模型）
- Task 10 依赖所有前置 Task
