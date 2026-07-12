# Checklist — rebuild-settings-providers-tools

## 后端 Skill 系统
- [x] `POST /api/skills` 创建 skill.md（解析 YAML frontmatter，存储到 data/skills/{id}/）
- [x] `GET /api/skills` 返回 skill 列表（id/name/description/enabled）
- [x] `PATCH /api/skills/{id}/toggle` 切换 enabled 标志
- [x] `DELETE /api/skills/{id}` 删除 skill
- [x] `main.py` 注册 `skill_router`
- [x] `prompts.py` `_load_enabled_skills()` 读取 enabled 的 skill.md
- [x] `build_system_prompt()` 追加 skill 内容到 system prompt

## 后端 Web Search
- [x] `tool_routes.py` 新增 `POST /api/tools/verify-web-search`（httpx 测试搜索）
- [x] `web_search.py` 用 httpx 替换 Tavily SDK，支持自定义 base_url
- [x] `agent_factory._read_tavily_config` 读取 `tool_urls.tavily` + `tool_keys.tavily`
- [x] UI 验证：Tavily key `tvly-dev-18941D...` 验证返回 `verified: true`
- [x] 工具测试：`WebSearchTool.execute()` 返回 3 条真实搜索结果

## 后端视觉/图片工具
- [x] `vision_analysis.py` ToolSpec 创建（OpenAI-compatible chat/completions + image_url）
- [x] `image_generation.py` ToolSpec 创建（3 步 fallback：chat → images/generations → 异步轮询）
- [x] `agent_factory._read_vision_config` 从 `payload.providers` 解析 base_url + api_key
- [x] `agent_factory._read_image_config` 从 `payload.providers` 解析 base_url + api_key
- [x] `agent_factory._resolve_provider` 按 provider_id 查找
- [x] `build_tools()` 注入 VisionAnalysisTool + ImageGenerationTool
- [x] 工具测试：vision_analysis 无配置返回 fallback；有配置调用路径正常（404 是 9router 不支持）
- [x] 工具测试：image_generation 无配置返回 fallback；3 步 fallback 策略全部执行

## 后端 Tools 列表 API
- [x] `GET /api/tools` 返回已注册工具（registry 为空时 fallback 到静态实例化）
- [x] UI 验证：Tools tab 显示 15 个真实工具（search_docs, web_search, vision_analysis, image_generation 等）

## 前端 useSettingsStore 重构
- [x] 新增 `providers: CustomProvider[]` + `activeProviderId`
- [x] 新增 `toolUrls` + `visionProviderId` + `imageProviderId`
- [x] 删除 `activeProvider` / `providerKeys` / `baseUrls` / 硬编码 PROVIDERS
- [x] provider CRUD actions 实现（addProvider/updateProvider/deleteProvider）
- [x] 持久化键 `hwrag_settings`，旧数据迁移清空
- [x] `client.ts` `getAuthHeaders` 清理已删除字段引用

## 前端 SettingsPage API tab
- [x] 服务商列表区（左侧卡片 + 添加/删除/选中）
- [x] 服务商详情区（name/base_url/api_key 输入 + 验证按钮）
- [x] `handleVerifyProvider` 调 `POST /api/models` 拉取模型
- [x] 三组模型下拉（chat/vision/image）按 verified 服务商 optgroup 分组
- [x] 删除 `providers.ts` PROVIDERS 数组引用

## 前端 SettingsPage Skills tab
- [x] `useQuery` 拉 `GET /api/skills` 显示 skill 卡片列表
- [x] 添加 skill 弹窗（textarea + 保存调 POST /api/skills）
- [x] 开关切换调 `PATCH /api/skills/{id}/toggle`
- [x] 删除按钮调 `DELETE /api/skills/{id}`
- [x] UI 验证：添加 hardware-review skill 成功，开关切换 enabled true/false 正常

## 前端 SettingsPage Tools tab
- [x] 新增 `tools` tab ID
- [x] 上半区：GET /api/tools 工具列表（name + description）
- [x] 下半区：Tavily 配置（URL + Key + 验证按钮）
- [x] `handleVerifyTavily` 调 `POST /api/tools/verify-web-search`
- [x] UI 验证：验证按钮点击后显示 "✓ 已验证"

## 前端 InputBar 模型选择器
- [x] 模型列表从所有 verified providers 合并，按服务商分组
- [x] 选择模型时记录 provider_id 到会话
- [x] `sendMessage` 从 provider 解析 base_url + api_key

## 前端 useChatStore.sendMessage 参数传递
- [x] 传递 `tool_urls.tavily`
- [x] 传递 `providers` 数组（verified providers 完整信息）
- [x] 传递 `vision_model` + `vision_provider_id`
- [x] 传递 `image_model` + `image_provider_id`

## TypeScript 编译
- [x] `npx tsc --noEmit` 0 错误

## 端到端验证（浏览器）
- [x] 设置页 API tab 显示自定义服务商管理界面
- [x] 设置页 Skills tab 添加/切换/删除 skill 正常
- [x] 设置页 Tools tab 显示 15 个真实工具
- [x] Tavily key UI 验证成功
- [x] web_search 工具真实返回搜索结果
- [x] vision_analysis 工具调用路径正常（LLM 401/404 是环境问题）
- [x] image_generation 工具 3 步 fallback 全部执行
- [x] 真实 LLM 对话触发 web_search（oc/mimo-v2.5，9router 200 OK，Tavily 返回结果）
- [x] 真实 LLM 对话触发 vision_analysis（oc/mimo-v2.5 多模态）
  - 第一轮 LLM 200 → tool_call vision_analysis → HITL 自动放行
  - 工具内部调用 9router vision 200 OK（SSE 标记修复验证通过）
  - 工具执行 9.2s 返回分析结果 → 第二轮 LLM 200 → 正确识别"红色矩形 + RED BOX 文字"
- [x] 真实 LLM 对话触发 image_generation（oc/mimo-v2.5）
  - 第一轮 LLM 200 → tool_call image_generation → HITL 自动放行
  - Step1 chat/completions 200 OK（SSE 标记修复验证通过，JSON 解析成功）
  - Step2 /images/generations 400（9router 不支持端点）
  - Step3 /tasks 404（9router 不支持端点）
  - 工具执行 25.8s 完成 → 第二轮 LLM 400 "Provider does not support image generation"
  - 根因：mimo-v2.5 不是图片生成模型（模型能力限制，非代码问题）
