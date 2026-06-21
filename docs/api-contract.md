# Hardware RAG Agent API Contract

这份文档是 `Hardware RAG Agent` 前后端联调时唯一认可的接口契约来源。

## 1. 工地铁律

- 任何新接口，先写文档，再写代码。
- 前端只要新增一个接口调用，就必须同步补全文档。
- 后端只要新增路由，或修改路径、请求体、响应体、字段名、状态码、错误码，就必须同步改文档。
- 没写进本文档的接口，默认视为不存在，不纳入联调与验收范围。
- 联调时如果前后端理解不一致，先更新本文档，再改代码。
- 每一次对本文档的修改，必须在变更日志中登记。

## 2. 全局约定

### 2.1 Base URL

- 开发环境统一使用 `/api` 作为前端请求前缀。
- 本地后端默认服务地址为 `http://127.0.0.1:58080`。
- WebSocket 地址为 `ws://127.0.0.1:58080/api`。
- 前端通过代理把 `/api` 转发到后端，避免在页面中写死多个地址。

### 2.2 API 版本化

- V1 阶段所有接口路径以 `/api/` 开头，不显式标注版本号。
- V2 及以后新增或破坏性变更的接口必须以 `/api/v2/` 开头。
- 对已有 V1 接口的向后兼容修改（仅新增可选字段）可以不升版本，但必须在变更日志中登记。
- 删除或重命名字段、修改必填项、修改事件类型属于破坏性变更，必须升版本。

### 2.3 接口状态

每个接口必须标记当前状态，状态只允许使用以下几种：

- `draft`：需求已提出，契约未达成。
- `agreed`：前后端已确认字段与行为。
- `mocked`：前端已按契约接入 mock 数据。
- `implemented`：后端已完成实现。
- `verified`：前后端联调通过。

### 2.4 HTTP 状态码

所有接口统一使用以下状态码：

| 场景 | 状态码 | 说明 |
| --- | --- | --- |
| 成功 | `200` | 请求正常处理完成 |
| 请求参数错误 | `400` | 必填参数缺失、字段类型错误、枚举值不合法 |
| 认证失败 | `401` | API Key 无效或未提供 |
| 资源不存在 | `404` | 请求的资源不存在 |
| 请求冲突 | `409` | 资源已存在等 |
| 服务器内部错误 | `500` | 未预期的服务端错误 |

错误响应的 body 格式统一按 2.5 节。

### 2.5 认证与鉴权

所有涉及 LLM 调用的接口通过 HTTP Header 传递认证和模型选择信息：

| Header | 必填 | 说明 | 示例 |
| --- | --- | --- | --- |
| `X-API-Key` | 是 | 用户配置的 API Key | `sk-proj-xxx` |
| `X-Model` | 是 | 当前选择的模型 ID | `gpt-4o` |
| `X-Provider` | 否 | Provider 标识，不传则默认为 `openai` | `openai` |

不需要 LLM 调用的接口（如 `/api/devices`、`/api/kb/list`）不需要传这些 Header。

### 2.6 标准响应格式

所有 JSON 接口统一使用以下格式：

成功响应：

```json
{
  "success": true,
  "data": {}
}
```

`data` 的内容是各接口的业务数据，在接口详情中写明。

错误响应：

```json
{
  "success": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "请求参数不合法",
    "details": null
  }
}
```

- `code`：稳定错误码，给前端做分支判断用。全大写 + 下划线。
- `message`：给开发调试看的可读信息。
- `details`：必填，无额外信息时传 `null`。放字段级错误、原始报错摘要或补充信息。

此格式对所有 JSON 接口强制生效，不接受"接口直接返回业务对象"的例外。

### 2.7 SSE 约定

所有 SSE 接口统一使用：

- `Content-Type: text/event-stream`
- 每条事件格式：`data: {JSON}\n\n`
- 流结束时必须发送 `done` 事件。

事件对象统一包含：

- `type`：事件类型。
- `message`：可选的人类可读说明。
- 其他事件专属字段。

聊天、编译、烧录等流式接口，流结束时必须发送 `done` 事件。

### 2.8 字段命名

- JSON 字段统一使用 `snake_case`。
- 布尔值字段用 `true/false`，不要用 `0/1` 或 `"true"` 字符串。
- 时间字段统一使用 ISO 8601 字符串，除非接口明确约定 Unix 时间戳。
- 枚举字段必须在文档中列出允许值。
- 可空字段明确写 `null` 是否允许，不要让前后端各自猜测。

### 2.9 前端的 API 桥接函数

前端已封装四个桥接函数，后端只需返回标准格式即可：

| 函数 | 用途 | 说明 |
| --- | --- | --- |
| `apiGet(path)` | GET JSON 请求 | 自动拼接 `API_BASE + path`，无需补 `/api/` |
| `apiPost(path, body)` | POST JSON/multipart 请求 | 自动判断 `FormData`，JSON 自动 `Content-Type: application/json` |
| `apiSSE(path, body, callbacks)` | SSE 流式请求 | POST 请求，通过 `onEvent(evt)` 回调逐事件消费 |
| `apiWS(endpoint, handlers)` | WebSocket 连接 | 自动拼接 `WS_BASE + endpoint` |

调用示例：

- `apiPost('models', { base_url })` → `POST http://127.0.0.1:58080/api/models`
- `apiSSE('chat', body, { onEvent, onDone })` → `POST http://127.0.0.1:58080/api/chat`
- `apiWS('/monitor/COM3?baud=115200', { onMessage })` → `ws://127.0.0.1:58080/api/monitor/COM3?baud=115200`
- `apiGet('devices')` → `GET http://127.0.0.1:58080/api/devices`


### 2.10 分页

列表类接口（如 `/api/kb/list`）统一使用以下分页参数：

请求参数：
- `page`：页码，从 1 开始，默认 1。
- `page_size`：每页条数，默认 20，最大 100。

响应中 `data` 结构扩展为：

```json
{
  "success": true,
  "data": {
    "items": [],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 42,
      "total_pages": 3
    }
  }
}
```

- `items` 替代了无分页版本的直接数组名（如 `documents`）。
- 不分页的接口（如 `/api/devices`）继续返回原有格式。

### 2.11 日期时间格式

所有日期时间字段统一使用 ISO 8601 格式，包含时区信息：

- 推荐格式：`2026-06-19T10:00:00+08:00`
- 服务端统一使用 UTC 存储：`2026-06-19T02:00:00Z`
- 前端在展示时转换为本地时区。
- 仅日期不含时间时使用：`2026-06-19`
- 不使用 Unix 时间戳，除非接口详情中明确注明。

### 2.12 文件上传约束

| 约束 | 值 | 说明 |
| --- | --- | --- |
| 最大文件大小 | 50 MB | 超过时返回 `FILE_TOO_LARGE`（400） |
| 支持格式 | `pdf`、`md`、`txt` | 其他格式返回 `UNSUPPORTED_FORMAT`（400） |
| 编码 | UTF-8 | `md` 和 `txt` 文件必须为 UTF-8 编码 |
| 文件名字段 | `file` | 上传接口统一使用 `multipart/form-data`，字段名为 `file` |

### 2.13 空值与缺省值

| 场景 | 规则 | 说明 |
| --- | --- | --- |
| 字符串无值 | 传 `null` | 不传空字符串 `""` |
| 数组无值 | 传 `[]` | 不传 `null` |
| 对象无值 | 传 `null` | 按接口详情约定 |
| 布尔值 | 必填 | 不设默认值，不允许缺省 |
| 数字或字符串缺省 | 在接口详情中写明默认值 | 前端不传则后端使用默认值 |

### 2.14 错误码列表

所有接口共享以下错误码（`code` 字段）：

| code | 状态码 | 说明 |
| --- | --- | --- |
| `INVALID_REQUEST` | 400 | 请求参数不合法 |
| `AUTH_FAILED` | 401 | API Key 无效或未提供 |
| `MODEL_FETCH_FAILED` | 500 | 模型列表获取失败 |
| `UPLOAD_FAILED` | 500 | 文件上传或解析失败 |
| `FILE_TOO_LARGE` | 400 | 文件超过 50 MB |
| `UNSUPPORTED_FORMAT` | 400 | 文件格式不支持 |
| `TOOL_NOT_FOUND` | 400 | 请求的工具不存在 |
| `CHIP_NOT_SUPPORTED` | 400 | 芯片型号不支持 |
| `SERIAL_NOT_FOUND` | 404 | 串口设备不存在或已被占用 |
| `BUILD_FAILED` | 500 | 编译失败（具体错误在 SSE done 事件返回） |
| `FLASH_FAILED` | 500 | 烧录失败 |
| `INTERNAL_ERROR` | 500 | 未预期的服务端错误 |
| `DOC_NOT_FOUND` | 404 | 知识库文档不存在 |

历史错误码 `MISSING_API_KEY`、`FETCH_FAILED` 已废弃，分别统一使用 `AUTH_FAILED`、`MODEL_FETCH_FAILED`。

新增错误码必须先登记到此表后再使用。

### 2.15 请求追踪 ID

后端在每个响应的 HTTP Header 中返回 `X-Request-Id`，值为 UUID v4。

- 前端无需关心此 Header，但可在调试时用于向开发者反馈问题。
- 后端在日志中记录每个请求的 `X-Request-Id`、方法、路径、耗时和状态码。

### 2.16 CORS 策略

开发阶段：

- 后端允许所有来源：`Access-Control-Allow-Origin: *`
- 允许所有标准方法和自定义 Header（`X-API-Key`、`X-Model`、`X-Provider`）
- 允许 `Content-Type: application/json` 和 `multipart/form-data`

生产部署建议：

- 将 `*` 替换为具体的前端域名。
- 通过环境变量 `CORS_ORIGINS` 配置。

### 2.17 SSE 连接管理

- 后端设置响应超时：聊天 SSE 建议 5 分钟内无新 token 则自动断开。
- 前端自动重连策略：聊天 SSE 断开后按 1s → 2s → 4s → 8s → 16s 指数退避重试，最长间隔 30 秒。
- 编译、烧录 SSE 不自动重连，由用户手动触发。
- 后端主动关闭前先发送 `type: "error"` 事件再断开。

### 2.18 WebSocket 重连策略

串口监视器 WebSocket 断开时：

- 前端自动重连，固定重试三次：1s → 2s → 3s。
- 三次均失败后显示"连接失败"，由用户手动重连。
- 断开期间前端保留已收到的数据，不清除历史。

## 3. 协作流程

### 3.1 前端新增接口时

前端在写 `apiGet`、`apiPost`、`apiSSE`、`apiWS` 调用之前，先补：

- 接口目录中的一行。
- 对应接口详情中的路径、用途、请求体、响应体、mock 规则。

### 3.2 后端实现接口时

后端开始写路由前，先核对：

- 路径是否已存在。
- 请求体是否齐全。
- 成功响应是否有明确示例。
- 错误响应和状态码是否写明。
- 是否需要 SSE 或 WebSocket。

### 3.3 联调验收时

每个接口最少检查：

- 路径、方法是否一致。
- 必填参数和 Header 是否一致。
- 成功响应字段是否一致。
- 错误响应格式是否一致。
- 空数据、异常数据、失败场景是否一致。
- 文档状态是否能推进到 `verified`。

## 4. 接口目录

| 状态 | 方法 | 路径 | 用途 | 前端入口 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `agreed` | `POST` | `/api/chat` | RAG 流式聊天 | `apiSSE('chat', ...)` | SSE |
| `agreed` | `POST` | `/api/models` | 拉取模型列表 | `apiPost('models', ...)` | JSON |
| `agreed` | `POST` | `/api/kb/upload` | 上传知识库文件 | `apiPost('kb/upload', formData)` | multipart |
| `agreed` | `GET` | `/api/kb/list` | 列出已上传文档 | `apiGet('kb/list')` | JSON |
| `agreed` | `POST` | `/api/kb/delete` | 删除知识库文档 | `apiPost('kb/delete', { doc_id })` | JSON |
| `agreed` | `GET` | `/api/devices` | 扫描串口设备 | `apiGet('devices')` | JSON |
| `implemented` | `POST` | `/api/wiring` | 生成接线图 SVG | `apiPost('wiring', ...)` | JSON |
| `agreed` | `POST` | `/api/audit_pins` | 引脚冲突审计 | `apiPost('audit_pins', ...)` | JSON |
| `implemented` | `POST` | `/api/diagnose` | 代码与引脚诊断 | `apiPost('diagnose', ...)` | JSON |
| `agreed` | `POST` | `/api/build` | 编译固件 | `apiSSE('build', ...)` | SSE |
| `agreed` | `POST` | `/api/upload` | 烧录固件 | `apiSSE('upload', ...)` | SSE |
| `agreed` | `POST` | `/api/tool` | 前端直接调 Agent 工具 | `apiPost('tool', ...)` | JSON |
| `agreed` | `WS` | `/api/monitor/{port}?baud=` | 串口监视器数据流 | `apiWS(...)` | WebSocket |


| greed | POST | /api/sessions | 创建会话 | CRUD | JSON |
| greed | GET | /api/sessions | 列出会话 | CRUD | JSON |
| greed | GET | /api/sessions/{id} | 获取会话（含消息） | CRUD | JSON |
| greed | PUT | /api/sessions/{id} | 更新会话 | CRUD | JSON |
| greed | DELETE | /api/sessions/{id} | 删除会话 | CRUD | JSON |
| greed | POST | /api/sessions/{id}/messages | 添加消息 | CRUD | JSON |
| greed | GET | /api/sessions/{id}/messages | 列出消息 | CRUD | JSON |
| greed | GET | /api/settings | 获取设置 | CRUD | JSON |
| greed |  PUT | /api/settings | 保存设置 | CRUD | JSON |
| `agreed` | `POST` | `/api/diagnose` | 代码诊断 | `apiPost('diagnose', ...)` | JSON |

## 5. 接口详情

### 5.1 `POST /api/chat`

- 状态：`agreed`
- 用途：聊天面板发起 RAG 流式问答。
- 前端入口：`apiSSE('chat', body, { onEvent, onDone })`
- 请求 Header：

| Header | 值示例 |
| --- | --- |
| `X-API-Key` | `sk-proj-xxx` |
| `X-Model` | `gpt-4o` |
| `X-Provider` | `openai` |

- 请求体：

```json
{
  "messages": [
    { "role": "user", "content": "ESP32 I2C NACK 怎么排查？" }
  ],
  "settings": {
    "top_k": 5,
    "temperature": 0.2,
    "system_prompt": "你是一个嵌入式硬件助手"
  }
}
```

- SSE 事件类型：

| 事件类型 | 发生时机 | 前端消费字段 |
| --- | --- | --- |
| `thinking` | RAG 检索、Agent 思考或推理输出 | `content`、`source` |
| `text` | LLM 逐 token 输出 | `content` |
| `tool` | Agent 调用工具时 | `name`、`icon`、`args`、`result` |
| `source` | RAG 检索到来源时 | `id`、`title`、`doc`、`page`、`score`、`excerpt` |
| `progress` | 长任务进度更新（编译/烧录等） | `percent`、`message` |
| `done` | 流结束时 | `success`、`usage?` |
| `error` | 出错时 | `message` |

- 字段说明：
  - `thinking` 的 `content` 为人类可读思考文本；`source` 枚举 `rag` / `llm` / `reasoning`，标识思考来源。
  - `source` 的 `id` 为文档片段唯一标识；`doc` 为所属文档 ID；`page` 为可选页码/位置；`score` 为相似度得分；`excerpt` 为命中片段摘要。
  - `tool` 的 `icon` 为前端展示图标名；`args` 为结构化调用参数；`result` 为工具执行结果字符串（聊天流中可空，完成后再填充）。
  - `done` 的 `usage` 可选，包含本次请求的 token 统计：`prompt_tokens`、`completion_tokens`、`total_tokens`。

- SSE 示例：

```json
{ "type": "thinking", "content": "正在检索知识库...", "source": "rag" }
{ "type": "source", "id": "chunk-001", "title": "ESP32 Technical Reference Manual", "doc": "doc-001", "page": 42, "score": 0.92, "excerpt": "I2C 总线需要 4.7kΩ 上拉电阻..." }
{ "type": "tool", "name": "search_docs", "icon": "search", "args": { "query": "ESP32 I2C NACK" }, "result": "" }
{ "type": "thinking", "content": "已找到相关资料，正在生成回答。", "source": "reasoning" }
{ "type": "text", "content": "先检查上拉电阻和时钟配置。" }
{ "type": "done", "success": true, "usage": { "prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200 } }
{ "type": "error", "message": "模型调用失败" }
```

- 约束：
  - `messages` 至少包含一条用户消息。
  - `type` 枚举只允许：`thinking`、`text`、`tool`、`source`、`progress`、`done`、`error`。
  - `done` 事件和 `error` 事件是终态事件，发送后流结束。
  - `settings` 均非必填，后端有默认值。

- Mock 规则：前端先使用固定 `thinking → text → done` 三段流占位。

### 5.2 `POST /api/models`

- 状态：`agreed`
- 用途：根据用户配置的 API Key 和 Base URL 获取可用模型列表。
- 前端入口：`apiPost('models', { base_url })`
- 请求 Header：`X-API-Key`
- 请求体：

```json
{
  "base_url": "https://api.openai.com/v1"
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "models": ["gpt-4o", "gpt-4.1-mini"]
  }
}
```

- 错误响应：

| 场景 | code |
| --- | --- |
| 未提供 API Key 或 Key 无效 | `AUTH_FAILED` |
| 模型列表获取失败 | `MODEL_FETCH_FAILED` |

```json
{
  "success": false,
  "error": {
    "code": "MODEL_FETCH_FAILED",
    "message": "模型列表获取失败",
    "details": null
  }
}
```

### 5.3 知识库管理

知识库接口已扩展到四个端点，覆盖基本 CRUD。

#### `POST /api/kb/upload`

- 状态：`agreed`
- 用途：上传知识库资料。后端自动处理解析、切块、向量化入库。
- 前端入口：`apiPost('kb/upload', formData)`，需传 `FormData`，非 JSON。
- 请求体：`multipart/form-data`
- 请求字段：`file`，支持 `pdf`、`md`、`txt`。
- 成功响应：

```json
{
  "success": true,
  "data": {
    "doc_id": "uuid-xxx",
    "filename": "esp32_datasheet.pdf",
    "chunks": 128
  }
}
```

- 错误响应：

```json
{
  "success": false,
  "error": {
    "code": "UPLOAD_FAILED",
    "message": "文件上传或解析失败",
    "details": null
  }
}
```

#### `GET /api/kb/list`

- 状态：`agreed`
- 用途：列出所有已上传的知识库文档。
- 前端入口：`apiGet('kb/list')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "documents": [
      { "doc_id": "uuid-xxx", "filename": "esp32_datasheet.pdf", "chunks": 128, "uploaded_at": "2026-06-19T10:00:00Z" }
    ]
  }
}
```

#### `POST /api/kb/delete`

- 状态：`agreed`
- 用途：删除知识库中的一篇文档及其向量数据。
- 前端入口：`apiPost('kb/delete', { doc_id: "uuid-xxx" })`
- 请求体：

```json
{
  "doc_id": "uuid-xxx"
}
```

- 成功响应：

```json
{
  "success": true,
  "data": null
}
```

### 5.4 `GET /api/devices`

- 状态：`agreed`
- 用途：扫描当前可用串口设备。
- 前端入口：`apiGet('devices')`
- 不需要认证 Header。
- 成功响应：

```json
{
  "success": true,
  "data": {
    "devices": [
      { "port": "COM3", "description": "USB Serial Device" }
    ]
  }
}
```

### 5.5 `POST /api/wiring`

- 状态：`agreed`
- 用途：根据器件和连接关系生成接线图 SVG。
- 前端入口：`apiPost('wiring', body)`
- 请求体：

```json
{
  "title": "ESP32 + BME280",
  "connections": [],
  "components": []
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "svg": "<svg viewBox=\"0 0 640 420\">...</svg>",
    "bom": []
  }
}
```

### 5.6 `POST /api/audit_pins`

- 状态：`agreed`
- 用途：检查引脚占用冲突、Strapping 风险或不安全分配。
- 前端入口：`apiPost('audit_pins', body)`
- 请求体：

```json
{
  "chip": "esp32-s3",
  "pin_assignments": {
    "GPIO0": { "function": "BUTTON", "config": "INPUT_PULLUP" },
    "GPIO2": { "function": "WS2812_DATA", "config": "OUTPUT" }
  }
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "conflicts": [],
    "warnings": [],
    "pin_map": {}
  }
}
```

- 约束：`pin_assignments` 为对象（key=引脚名，value=分配信息），非数组。`conflicts` / `warnings` 为空数组表示无问题。

### 5.7 `POST /api/build`

- 状态：`agreed`
- 用途：触发固件编译。
- 前端入口：`apiSSE('build', body, { onEvent, onDone })`
- 请求体：

```json
{
  "env": "esp32-s3-devkitc-1",
  "project_dir": "/projects/hardware-rag"
}
```

- SSE 事件：

| 事件类型 | 前端消费字段 | 说明 |
| --- | --- | --- |
| `progress` | `percent`（0-100 整数）, `message` | 编译进度更新 |
| `done` | `success`（true/false） | 编译结束，终态事件 |

- SSE 示例：

```json
{ "type": "progress", "percent": 10, "message": "Processing esp32-s3-devkitc-1..." }
{ "type": "progress", "percent": 80, "message": "Linking firmware.elf" }
{ "type": "done", "success": true, "message": "SUCCESS" }
{ "type": "done", "success": false, "message": "Build failed", "errors": ["src/main.cpp:42:5: error: 'foo' was not declared"] }
```

- 约束：`done` 事件中除 `type` 和 `success` 外均为可选字段。
- Mock 规则：前端不再 fallback 模拟，失败时显示错误日志。

### 5.8 `POST /api/upload`

- 状态：`agreed`
- 用途：编译并烧录固件到指定串口设备。
- 前端入口：`apiSSE('upload', body, { onEvent, onDone })`
- 请求体：

```json
{
  "env": "esp32-s3-devkitc-1",
  "port": "COM3",
  "project_dir": "/projects/hardware-rag"
}
```

- SSE 事件：同 `/api/build`，事件类型 `progress` / `done`，字段一致。
- Mock 规则：前端不再 fallback 模拟，失败时显示错误日志。

### 5.9 `POST /api/tool`

- 状态：`agreed`
- 用途：**前端直接调用 Agent 工具**（如接线图面板上的"检查引脚"按钮）。注意与 SSE chat 中的 `type: "tool"` 事件的区别——后者是后端在聊天流中报告"Agent 内部调用了工具"，不经过此端点。
- 前端入口：`apiPost('tool', body)`
- 请求体：

```json
{
  "tool": "audit_pins",
  "args": {
    "chip": "esp32-s3"
  }
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "output": "工具返回结果文本",
    "duration_ms": 1240
  }
}
```

- 约束：
  - `tool` 字段为工具名（字符串），非 `name`。
  - `output` 为纯文本或 JSON 字符串，前端直接展示。
  - `duration_ms` 可选，前端用于显示耗时。
- 错误响应：

```json
{
  "success": false,
  "error": {
    "code": "TOOL_NOT_FOUND",
    "message": "不支持的 tool: xxx",
    "details": null
  }
}
```

### 5.10 `WS /api/monitor/{port}?baud=`

- 状态：`agreed`
- 用途：推送串口实时数据到前端 Serial Monitor。
- 前端入口：`apiWS('/monitor/' + port + '?baud=' + baud, onMessage)`
- 路径参数：`port`，串口号，例如 `COM3`。
- 查询参数：`baud`，波特率，例如 `115200`。
- 连接后行为：前端连接成功后立即发送 `{ "type": "start" }`，后端持续推送串口数据。
- 数据帧格式：后端发送 JSON 对象 `{ "type": "data", "payload": "串口文本" }`。确需发送纯文本时前端兜底包装。
- Mock 规则：连接失败时前端显示错误日志，不再 fallback 到模拟数据。


### 5.11 `POST /api/sessions`

- 状态：`agreed`
- 用途：创建新对话会话。
- 请求体：

```json
{
  "title": "新对话",
  "model": "gpt-4o",
  "project": "嵌入式开发"
}
```

- 成功响应：

```json
{
  "id": "s3ed52d63",
  "title": "新对话",
  "model": "gpt-4o",
  "project": "嵌入式开发",
  "pinned": false,
  "msg_count": 0,
  "created_at": "2026-06-19T12:00:00"
}
```

### 5.12 `GET /api/sessions`

- 状态：`agreed`
- 用途：列出所有会话（不含消息体），按更新时间倒序。
- 成功响应：

```json
{
  "sessions": [
    {
      "id": "s3ed52d63",
      "title": "STM32 I2C 调试",
      "model": "agent",
      "project": "嵌入式开发",
      "pinned": false,
      "msg_count": 12,
      "created_at": "2026-06-19T12:00:00",
      "updated_at": "2026-06-19T14:30:00"
    }
  ]
}
```

### 5.13 `GET /api/sessions/{session_id}`

- 状态：`agreed`
- 用途：获取单个会话及其所有消息。
- 路径参数：`session_id` — 会话 ID。
- 成功响应：

```json
{
  "id": "s3ed52d63",
  "title": "STM32 I2C 调试",
  "model": "agent",
  "pinned": false,
  "msg_count": 2,
  "created_at": "2026-06-19T12:00:00",
  "messages": [
    {
      "id": "m1a2b3c4",
      "role": "user",
      "content": "I2C NACK 怎么排查？",
      "sources": [],
      "tool_calls": [],
      "created_at": "2026-06-19T12:00:05"
    },
    {
      "id": "m5e6f7g8",
      "role": "assistant",
      "content": "在 400kHz 下出现 NACK，最常见的原因是上拉电阻过大...",
      "sources": [{"id": "src1", "title": "I2C 协议规范"}],
      "tool_calls": [{"name": "search_docs", "args": "..."}],
      "created_at": "2026-06-19T12:00:10"
    }
  ]
}
```

### 5.14 `PUT /api/sessions/{session_id}`

- 状态：`agreed`
- 用途：更新会话属性（标题、模型、项目、固定状态）。
- 请求体（全部可选）：

```json
{
  "title": "新标题",
  "model": "gpt-4o",
  "project": "新项目",
  "pinned": true
}
```

- 成功响应：`{ "success": true }`

### 5.15 `DELETE /api/sessions/{session_id}`

- 状态：`agreed`
- 用途：删除会话及其所有消息。
- 成功响应：`{ "success": true }`
- 错误响应（404）：`{ "success": false, "error": { "code": "NOT_FOUND", "message": "Session not found", "details": null } }`

### 5.16 `POST /api/sessions/{session_id}/messages`

- 状态：`agreed`
- 用途：在会话中添加一条消息。
- 请求体：

```json
{
  "role": "user",
  "content": "I2C NACK 怎么排查？",
  "sources": [],
  "tool_calls": []
}
```

- 约束：`role` 枚举值 `user` / `assistant`。`sources` 和 `tool_calls` 可选，默认为空数组。
- 成功响应：

```json
{
  "id": "m1a2b3c4",
  "role": "user",
  "content": "I2C NACK 怎么排查？",
  "created_at": "2026-06-19T12:00:05"
}
```

### 5.17 `GET /api/sessions/{session_id}/messages`

- 状态：`agreed`
- 用途：获取会话的所有消息，按创建时间正序。
- 成功响应：

```json
{
  "messages": [
    {
      "id": "m1a2b3c4",
      "role": "user",
      "content": "I2C NACK 怎么排查？",
      "sources": [],
      "tool_calls": [],
      "created_at": "2026-06-19T12:00:05"
    }
  ]
}
```

### 5.18 `GET /api/settings`

- 状态：`agreed`
- 用途：获取所有设置键值对。
- 成功响应：

```json
{
  "settings": {
    "theme": "dark",
    "lang": "zh",
    "activeProvider": "openai"
  }
}
```

- 约束：所有值均为字符串。前端自行做类型转换。

### 5.19 `PUT /api/settings`

- 状态：`agreed`
- 用途：批量保存设置键值对。
- 请求体：

```json
{
  "theme": "dark",
  "lang": "zh"
}
```

- 成功响应：`{ "success": true }`
### 5.20 `POST /api/diagnose`

- 状态：`implemented`
- 用途：对嵌入式代码做静态扫描，检查 GPIO 安全、引脚冲突、内存与 Flash 兼容性。
- 前端入口：`apiPost('diagnose', ...)`
- 请求体：

```json
{
  "code": "void setup() { pinMode(LED, OUTPUT); digitalWrite(LED, HIGH); }",
  "env": "esp32-s3",
  "chip": "esp32-s3"
}
```

- 字段说明：
  - `code`：待诊断的代码字符串，必填。
  - `env`：编译环境，默认 `esp32-s3`。
  - `chip`：目标芯片，默认 `esp32-s3`，影响 Strapping 引脚集合。

- 成功响应：

```json
{
  "success": true,
  "data": {
    "results": [
      { "name": "GPIO 安全检查", "status": "WARN", "detail": "GPIO0 为 Strapping 引脚，建议避免使用" },
      { "name": "编译预检", "status": "PASS", "detail": "语法预检通过" },
      { "name": "引脚冲突检测", "status": "PASS", "detail": "未发现同一引脚被同时配置为输入和输出" },
      { "name": "内存估算", "status": "PASS", "detail": "估算 SRAM 使用约 42%" },
      { "name": "Flash 兼容性", "status": "PASS", "detail": "识别到常见库: delay, Serial" }
    ]
  }
}
```

- 错误响应：

| 场景 | code |
| --- | --- |
| 后端诊断异常 | `DIAGNOSE_FAILED` |

```json
{
  "success": false,
  "error": {
    "code": "DIAGNOSE_FAILED",
    "message": "诊断失败: ...",
    "details": "..."
  }
}
```

- 约束：`status` 枚举值只允许 `PASS` / `WARN` / `FAIL`。
- 更新时间：2026-06-20

### 5.21 `POST /api/sandbox/execute`

- 状态：`draft`
- 用途：在沙箱中执行代码片段，返回执行结果。支持 Python / C / C++ / JavaScript。
- 前端入口：`apiPost('sandbox/execute', { code, language })`
- 请求 Header：不需要 LLM 认证 Header。权限敏感操作需经前端确认。
- 请求体：

```json
{
  "code": "print(\"hello world\")",
  "language": "python"
}
```

- 字段说明：
  - `code`：待执行的代码字符串，必填，最长 50000 字符。
  - `language`：语言标识，默认 `python`。枚举值见下方。

- 枚举说明：
  | language | 说明 |
  | --- | --- |
  | `python` | Python 3 |
  | `c` | C 语言 |
  | `cpp` | C++ / Arduino |
  | `javascript` | Node.js |

- 成功响应：

```json
{
  "success": true,
  "data": {
    "stdout": "hello world\n",
    "stderr": "",
    "exit_code": 0,
    "duration_ms": 42,
    "timed_out": false
  }
}
```

- 字段说明（响应 data）：
  | 字段 | 类型 | 说明 |
  | --- | --- | --- |
  | `stdout` | string | 标准输出 |
  | `stderr` | string | 标准错误 |
  | `exit_code` | int | 退出码，0 表示正常 |
  | `duration_ms` | int | 执行耗时（毫秒） |
  | `timed_out` | bool | 是否超时被终止 |

- 错误响应：

| 场景 | code |
| --- | --- |
| 代码为空 | `EMPTY_CODE` |
| 代码超长（>50000） | `CODE_TOO_LONG` |
| 不支持的 language | `UNSUPPORTED_LANGUAGE` |
| 沙箱不可用 | `SANDBOX_UNAVAILABLE` |
| 执行超时（默认 30s） | `EXECUTION_TIMEOUT` |
| 执行内部错误 | `EXECUTION_FAILED` |

- 后端约束：
  - 代码在隔离环境运行（Docker 容器或 subprocess 沙箱），不可访问宿主机文件系统。
  - 默认超时 30 秒。
  - 禁止网络访问、禁止写入持久化存储。

- Mock 规则：后端无 Docker 时，返回模拟执行结果 `{ stdout: "(mock) hello sandbox", stderr: "", exit_code: 0, duration_ms: 5, timed_out: false }`。
- 更新时间：2026-06-21

### 5.22 `GET /api/sandbox/status`

- 状态：`draft`
- 用途：检查沙箱执行环境是否可用（Docker 是否安装并运行）。
- 前端入口：`apiGet('sandbox/status')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "docker_available": true,
    "supported_languages": ["python", "c", "cpp", "javascript"]
  }
}
```

- 字段说明：
  | 字段 | 类型 | 说明 |
  | --- | --- | --- |
  | `docker_available` | bool | Docker 是否可用 |
  | `supported_languages` | string[] | 当前支持的语言列表 |

- Mock 规则：无 Docker 时返回 `{ docker_available: false, supported_languages: ["python"] }`。
- 更新时间：2026-06-21

### 5.23 `POST /api/sandbox/audit`

- 状态：`draft`
- 用途：记录工具调用审计日志，用于安全审计和危险操作追踪。
- 前端入口：`apiPost('sandbox/audit', { action, level, detail })`
- 请求体：

```json
{
  "action": "create_file",
  "level": "high",
  "target": "data/sensitive.txt",
  "detail": "创建文件，包含 API Key",
  "session_id": "s1a2b3c4"
}
```

- 字段说明：
  | 字段 | 类型 | 必填 | 说明 |
  | --- | --- | --- | --- |
  | `action` | string | 是 | 操作类型 |
  | `level` | string | 是 | 风险等级 |
  | `target` | string | 否 | 操作目标 |
  | `detail` | string | 否 | 操作详情 |
  | `session_id` | string | 否 | 所属对话 ID |

- 枚举说明：
  | level | 说明 |
  | --- | --- |
  | `low` | 读文件、列目录、状态检查 |
  | `medium` | 启动服务、安装依赖、格式化 |
  | `high` | 删除、重置、覆盖、网络下载、系统级命令 |

  | action | 说明 |
  | --- | --- |
  | `read_file` | 读文件 |
  | `write_file` | 写文件 |
  | `delete_file` | 删除文件 |
  | `execute_command` | 执行命令 |
  | `network_request` | 网络请求 |
  | `docker_operation` | Docker 操作 |
  | `system_config` | 系统配置变更 |

- 成功响应：`{ "success": true, "data": { "id": "audit-001" } }`
- 错误响应：

| 场景 | code |
| --- | --- |
| action 为空 | `INVALID_ACTION` |
| level 不支持 | `INVALID_LEVEL` |
| 审计存储写入失败 | `AUDIT_WRITE_FAILED` |

- 后端约束：审计日志至少保留最近 30 天，支持按 session_id 查询。
- 更新时间：2026-06-21

## 6. 新增接口模板

```md
### X.X `METHOD /api/xxx`

- 状态：`draft`
- 用途：
- 前端入口：
- 请求 Header：
- 路径参数：
- 查询参数：
- 请求体：
- 成功响应：
- 错误响应：
- 字段说明：
- 枚举说明：
- 前端约束：
- 后端约束：
- Mock 规则：
- 更新时间：
```

## 7. 变更日志

| 日期 | 变更内容 | 变更人 |
| 2026-06-19 | 初始版本 | Codex |
| 2026-06-19 | 补充 9 项开发约定：分页约定（2.10）、日期时间格式（2.11）、文件上传约束（2.12）、空值与缺省值（2.13）、错误码列表（2.14）、请求追踪 ID（2.15）、CORS 策略（2.16）、SSE 连接管理（2.17）、WebSocket 重连策略（2.18） | Codex |
| 2026-06-19 | 新增 CRUD 接口文档（5.11–5.19）：sessions CRUD、messages CRUD、settings 读写 | Codex |
| 2026-06-19 | 新增 HTTP 状态码约定（2.4）；新增认证 Header 约定（2.5）；收严标准响应格式（2.6）；新增 API 版本化约定（2.2）；KB 扩展为多个端点（5.3）；WS 路径从 `/monitor` 改为 `/api/monitor`（5.10）；`/api/tool` 与 SSE `tool` 事件边界说明（5.9）；新增变更日志（7） | Codex |
| 2026-06-20 | 新增 `/api/diagnose` 接口（5.20），实现 GPIO 安全、编译预检、引脚冲突、内存估算、Flash 兼容性五类诊断；`/api/wiring` 状态推进为 `implemented` 并补充请求/响应字段说明；错误码表新增 `DIAGNOSE_FAILED` | Trae |
| 2026-06-20 | 对齐 SSE 事件字段：`thinking` 改 `content` 并加 `source`、`source` 改 `id` 并扩展字段、`tool` 加 `icon`/`result`、done 加 `usage`、新增 `progress` 事件（5.1/5.7/5.8）；标准化接口响应：`/api/kb/upload` 增加错误响应、`/api/wiring` 增加 `bom`、`/api/models` 错误码区分 `AUTH_FAILED`/`MODEL_FETCH_FAILED`；错误响应 `details` 统一必填可 null（2.6）；废弃 `MISSING_API_KEY`、`FETCH_FAILED`（2.14） | Codex |
| 2026-06-21 | 新增沙箱执行接口（5.21）和沙箱状态接口（5.22）、审计日志接口（5.23） | Codex |
| 2026-06-20 | 前端真实对接：SerialPane WebSocket 去 fallback、FlashPane build/upload 去模拟、SafetyPane 动态提取引脚、PreviewPane 接入 `/api/diagnose`；`/api/diagnose` 补入接口目录；移除 5.7/5.8/5.10 前端 mock 规则 | Trae |

## 8. 包工头检查清单

每次前后端准备开工或联调前，至少检查以下问题：

- 这个接口是否已经写进本文档？
- 状态是 `draft` 还是 `agreed`？
- 前端调用名、路径、方法是否一致？
- 后端返回字段名是否和文档完全一致？
- 错误格式是否统一？
- SSE / WebSocket 的事件结构是否写清？
- 认证 Header 是否需要传递？
- 有没有 mock 占位规则，避免一方空等另一方？

如果以上任一问题答案是否定的，这个接口就不应直接进入联调。


