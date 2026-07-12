# Rebuild Settings: Custom Providers + Real Tools + Skill.md System Spec

## Why

当前设置页存在三个核心问题：
1. **Skills tab 显示 7 个硬编码假技能**（search_docs/lookup_register 等），不反映真实后端工具，且不支持 skill.md 下载配置
2. **21 个硬编码服务商**（PROVIDERS 数组）限制用户选择，用户无法添加自定义服务商
3. **缺少工具展示和工具 API Key 配置**——后端有 12 个 ToolSpec 但前端没有专门展示，Web Search 的 Tavily Key 也没有独立配置入口

同时需要新增两个 Agent 工具（视觉分析 + 图片生成），对齐现有 ToolSpec 格式。

## What Changes

### 1. Skill.md 系统（替换硬编码 Skills）
- **BREAKING**：删除 `useSettingsStore` 中的硬编码 `skills: Skill[]` 数组
- 新增后端 CRUD：`GET /api/skills`、`POST /api/skills`（上传/paste skill.md）、`DELETE /api/skills/{id}`、`PATCH /api/skills/{id}/toggle`
- Skills 存储为 markdown 文件：`data/skills/{skill_id}/skill.md`
- 每个 skill.md 包含 YAML frontmatter（name, description, triggers）+ markdown body（指令内容）
- 已启用的 skill 内容注入 Agent system prompt 尾部作为额外指令
- 前端 Skills tab：显示已安装 skill 列表（名称+描述+开关）、添加按钮（paste/upload skill.md）、删除按钮

### 2. Tools tab（新增）
- 新增设置页 tab：`tools`（位于 skills 和 about 之间）
- 上半区：从 `GET /api/tools` 拉取真实工具列表，显示 name + description（只读）
- 下半区：Web Search API Key 配置
  - Tavily API Key 输入框（password 类型，可切换显示）
  - Tavily Base URL 输入框（默认 `https://api.tavily.com`，可自定义）
  - 「验证」按钮：发送测试搜索请求，成功显示 ✓，失败显示错误信息
  - 验证通过后 Key 持久化到 `toolKeys.tavily` + 新增 `toolUrls.tavily`

### 3. 自定义服务商系统（替换硬编码 PROVIDERS）
- **BREAKING**：删除 `frontend/src/config/providers.ts` 中的 `PROVIDERS` 数组（21 个硬编码服务商）
- **BREAKING**：清空现有 `providerKeys`、`baseUrls`、`activeProvider`（用户重新配置）
- 新数据模型 `CustomProvider`：
  ```
  {
    id: string;          // UUID
    name: string;        // 用户自定义名称，如 "DeepSeek"
    base_url: string;    // 如 "https://api.deepseek.com/v1"
    api_key: string;     // API Key
    verified: boolean;   // 是否通过验证
    models: string[];    // 缓存的上游模型列表
  }
  ```
- 设置页 API tab：
  - 服务商列表（左侧卡片，可多选高亮表示"已验证"）
  - 「+ 添加服务商」按钮 → 弹出表单（name + base_url + api_key）
  - 每个服务商卡片：名称、base_url、验证状态、模型数量、删除按钮
  - 选中服务商后：显示 base_url/api_key 输入框 + 验证按钮 + 模型列表
  - 验证流程不变：填完 → 点验证 → 调 `POST /api/models` 拉取上游模型 → 成功则标记 verified
- 对话栏模型选择器：
  - 显示所有 verified 服务商的模型，按服务商分组
  - 格式：`{provider_name} / {model_id}`
  - 选择模型时同时记录所属服务商（用于发送时解析 base_url + api_key）
- 模型配置（在 API tab 或独立区域）：
  - `model`：对话模型，从所有 verified 服务商的模型列表中选
  - `visionModel` + `visionProviderId`：视觉模型，可来自与对话模型不同的服务商
  - `imageModel` + `imageProviderId`：生图模型，可来自与对话模型不同的服务商
  - 三个模型各自独立关联一个 provider_id，发送时分别解析对应的 base_url + api_key

### 4. 视觉分析工具（新 ToolSpec）
- 工具名：`vision_analysis`
- 所属分组：`retrieval`（与现有工具分组对齐）
- Args schema：
  ```python
  class VisionAnalysisArgs(BaseModel):
      image: str = Field(description="图片 URL 或 base64 编码（data:image/...;base64,...）")
      question: str = Field(description="对图片的分析问题，如 '这个芯片的引脚定义是什么？'")
  ```
- 执行逻辑：
  - 从 payload 读取 `vision_model` + `vision_provider_id`，从该 provider 解析 base_url + api_key
  - vision_model 和 chat model 可来自不同服务商（如 chat 用服务商1，vision 用服务商2）
  - 调用 OpenAI-compatible chat completion API，image 作为 image_url content part
  - 返回模型的文字分析结果
- RiskLevel: LOW（只读分析）
- timeout_seconds: 60
- 格式完全对齐现有 ToolSpec（继承 ToolSpec, args_schema, output_schema, execute 方法）

### 5. 图片生成工具（新 ToolSpec）
- 工具名：`image_generation`
- 所属分组：`retrieval`
- Args schema：
  ```python
  class ImageGenerationArgs(BaseModel):
      prompt: str = Field(description="图片生成提示词，如 'ESP32-S3 最小系统接线示意图'")
      size: str = Field(default="1024x1024", description="图片尺寸")
  ```
- 执行逻辑（三段式 fallback，覆盖所有主流生图 API 格式）：
  - 从 payload 读取 `image_model` + `image_provider_id`，从该 provider 解析 base_url + api_key
  - image_model 和 chat model 可来自不同服务商
  - **Step 1 — Chat Completion**：调 `POST {base_url}/chat/completions`，发送 prompt，解析响应文本中的图片（markdown `![](url)` 或 base64）
  - **Step 2 — /v1/images/generations**：Step 1 无图片时，调 `POST {base_url}/images/generations`（DALL-E 风格），prompt + size，解析返回的 image URL
  - **Step 3 — 异步轮询**：Step 2 失败时，提交 prompt → 获取 task_id → 轮询 `GET {base_url}/tasks/{task_id}` 直到完成 → 返回图片
  - 任一步骤成功即返回，三步全失败返回 fallback 文本
  - 返回格式：`{ "output": "图片已生成", "image_url": "...", "image_base64": "..." }`
- RiskLevel: LOW
- timeout_seconds: 120（生图可能较慢）

### 6. WebSearchTool 改造
- `WebSearchTool.__init__` 新增 `tavily_base_url` 参数（默认 `https://api.tavily.com`）
- `_do_search` 方法改为使用 `httpx` 直接调用 REST API（不再依赖 Tavily SDK），支持自定义 base_url
- 请求格式：`POST {base_url}/search`，Bearer auth，body: `{ "query": "...", "max_results": N }`
- `agent_factory._read_tavily_key` 扩展为同时读取 `tool_urls.tavily`

## Impact

- Affected code:
  - `frontend/src/config/providers.ts` — 删除 PROVIDERS 数组
  - `frontend/src/stores/useSettingsStore.ts` — 重构 provider 数据模型
  - `frontend/src/components/settings/SettingsPage.tsx` — API tab 重写 + 新增 Tools tab + Skills tab 重写
  - `frontend/src/components/input/InputBar.tsx` — 模型选择器重构
  - `backend/app/api/skill_routes.py` — 新增 Skills CRUD
  - `backend/app/api/tool_routes.py` — 新增 Web Search 验证端点
  - `backend/src/agent/tools/groups/retrieval/vision_analysis.py` — 新增
  - `backend/src/agent/tools/groups/retrieval/image_generation.py` — 新增
  - `backend/src/agent/tools/groups/retrieval/web_search.py` — 改造支持自定义 URL
  - `backend/src/agent/agent_factory.py` — 注入新工具 + 读取 vision/image model 配置
  - `backend/src/agent/prompts.py` — system prompt 注入已启用 skills

## ADDED Requirements

### Requirement: Skill.md System
The system SHALL provide a skill management system where skills are defined as markdown files (skill.md) that can be created, deleted, and toggled on/off.

#### Scenario: User creates a new skill
- **WHEN** user pastes skill.md content in the Skills tab and clicks save
- **THEN** the system creates `data/skills/{skill_id}/skill.md` and the skill appears in the list

#### Scenario: Enabled skill injected into Agent
- **WHEN** user has skills A and B enabled, and sends a chat message
- **THEN** the Agent system prompt includes the content of skills A and B as additional instructions

#### Scenario: User deletes a skill
- **WHEN** user clicks delete on a skill
- **THEN** the skill directory is removed and the skill disappears from the list

### Requirement: Tools Tab
The system SHALL display the real registered tool list from `GET /api/tools` with name and description, and provide Web Search API key configuration.

#### Scenario: Viewing tools
- **WHEN** user opens the Tools tab
- **THEN** the system displays all registered tools (search_docs, web_search, audit_pins, etc.) with their descriptions

#### Scenario: Configuring Web Search
- **WHEN** user enters Tavily API key and clicks verify
- **THEN** the system sends a test search request to the Tavily API and shows success/failure

### Requirement: Custom Provider System
The system SHALL allow users to create custom service providers with name, base_url, and api_key, replacing all hardcoded providers.

#### Scenario: User creates a provider
- **WHEN** user clicks "Add Provider", fills in name/base_url/api_key, and clicks verify
- **THEN** the system fetches models from the upstream API and marks the provider as verified

#### Scenario: Multiple providers in chat
- **WHEN** user has 3 verified providers and opens the chat model selector
- **THEN** all 3 providers' models are shown, grouped by provider name

### Requirement: Vision Analysis Tool
The system SHALL provide a `vision_analysis` tool that accepts an image (URL or base64) and a question, calls the configured vision model, and returns text analysis.

#### Scenario: Agent analyzes a circuit diagram
- **WHEN** Agent calls vision_analysis with an image URL and question "What chip is this?"
- **THEN** the tool calls the vision model and returns the model's text analysis

### Requirement: Image Generation Tool
The system SHALL provide an `image_generation` tool that accepts a prompt, calls the configured image model, and returns the generated image.

#### Scenario: Agent generates a wiring diagram
- **WHEN** Agent calls image_generation with prompt "ESP32-S3 minimum system wiring diagram"
- **THEN** the tool calls the image model and returns the generated image URL or base64

## MODIFIED Requirements

### Requirement: WebSearchTool
The WebSearchTool SHALL accept a custom `tavily_base_url` parameter and use raw HTTP requests instead of the Tavily SDK, allowing users to configure self-hosted Tavily-compatible endpoints.

## REMOVED Requirements

### Requirement: Hardcoded Providers
**Reason**: The 21 hardcoded providers in `PROVIDERS` array limit user flexibility; users should create their own.
**Migration**: Clean reset — all existing `providerKeys`, `baseUrls`, `activeProvider` values are cleared. Users reconfigure from scratch.

### Requirement: Hardcoded Skills Array
**Reason**: The 7 hardcoded skills in `useSettingsStore` are fake placeholders that don't reflect real capabilities.
**Migration**: Replaced by skill.md system.
