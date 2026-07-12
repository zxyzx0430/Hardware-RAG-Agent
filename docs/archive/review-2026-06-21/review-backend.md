# Backend 代码质量审查报告

审查范围：

- `E:\Desktop\agent\backend\app\api\routes.py`
- `E:\Desktop\agent\backend\src\llm\client.py`
- `E:\Desktop\agent\backend\src\config\settings.py`
- 依据 `E:\Desktop\agent\docs\api-contract.md`（2.14 错误码表、5.x 接口详情、2.6 标准响应格式、2.7 SSE 约定）

> 说明：本次只审查不修改代码。下列问题按 `high` / `medium` / `low` 划分优先级，high 为联调前必须解决。

---

## 1. `backend/app/api/routes.py`

### R1. `/api/chat` SSE `thinking` 事件字段与契约不一致

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:107,146,167,181`
- **具体问题**：契约 5.1 规定 `thinking` 事件前端消费字段为 `message`（示例：`{ "type": "thinking", "message": "正在分析问题" }`）。代码中发送的是 `{"content": "...", "source": "rag|llm|reasoning"}`，缺少 `message` 字段，前端按文档解析会拿不到 thinking 文本。
- **建议修复方式**：将 thinking 文本放入 `message` 字段，例如 `{"message": "正在检索知识库...", "source": "rag"}`；`source` 可作为扩展字段保留。
- **优先级**：high

### R2. `/api/chat` SSE `source` 事件字段与契约不一致

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:120-127`
- **具体问题**：契约 5.1 示例/source 事件消费字段为 `title`、`chunk_id`。代码发送的是 `id`、`title`、`doc`、`page`、`score`、`excerpt`，没有 `chunk_id`，且 `page` 实际取的是 `chunk_index`。
- **建议修复方式**：按契约提供 `chunk_id`（可与现有 `id` 同值），核心字段名必须与文档一致；其余字段可作为扩展保留。
- **优先级**：high

### R3. `/api/chat` SSE `tool` 事件 `args` 字段类型与契约不一致

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:128-133`
- **具体问题**：契约 5.1 示例中 `tool` 事件的 `args` 是 JSON 对象（`{"query":"ESP32 I2C NACK"}`）。代码将 `args` 编码为字符串 `query="..." · top_k=...`，前端按对象解析会失败。
- **建议修复方式**：将 `args` 改为结构化对象，例如 `{"query": last_user_msg, "top_k": payload.top_k}`；若需要字符串展示，可在额外字段中提供。
- **优先级**：high

### R4. `/api/models` 错误码未在契约 2.14 登记

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:220,234`
- **具体问题**：代码使用 `MISSING_API_KEY` 和 `FETCH_FAILED`，但契约 2.14 错误码表中对应的码是 `AUTH_FAILED`（API Key 无效或未提供）和 `MODEL_FETCH_FAILED`（模型列表获取失败）。
- **建议修复方式**：统一改为契约登记的 `AUTH_FAILED` 和 `MODEL_FETCH_FAILED`，避免前端做两套码表映射。
- **优先级**：high

### R5. `/api/kb/upload` 未返回标准响应格式

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:245-284`
- **具体问题**：
  - 成功时返回 `{"chunks": N}`，缺少契约 5.3 要求的 `success` / `data` 包装，且未返回 `doc_id`、`filename`。
  - 错误时返回 `{"chunks": 0, "error": "字符串"}`，缺少 `success` 与标准 `error.code`。
- **建议修复方式**：
  - 生成并持久化 `doc_id`，返回 `{ "success": true, "data": { "doc_id": "...", "filename": "...", "chunks": N } }`。
  - 错误统一使用标准格式 `{ "success": false, "error": { "code": "UPLOAD_FAILED"|"UNSUPPORTED_FORMAT", "message": "..." } }`。
- **优先级**：high

### R6. `/api/devices` 未返回标准格式

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:285-299`
- **具体问题**：直接返回 `{"devices": [...]}`；契约 5.4 要求 `{ "success": true, "data": { "devices": [...] } }`。
- **建议修复方式**：用标准 `{success, data}` 包装响应。
- **优先级**：medium

### R7. `/api/wiring` 未返回标准格式

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:327-336`
- **具体问题**：返回 `{ "svg": "...", "bom": [] }`；契约 5.5 要求 `{ "success": true, "data": { "svg": "..." } }`。
- **建议修复方式**：用标准 `{success, data}` 包装，`bom` 可作为 `data` 内扩展字段。
- **优先级**：medium

### R8. `/api/audit_pins` 未返回标准格式

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:355-358`
- **具体问题**：返回 `{ "conflicts": [], "warnings": [], "pin_map": {} }`；契约 5.6 要求 `{ "success": true, "data": { "conflicts": [], "warnings": [], "pin_map": {} } }`。
- **建议修复方式**：用标准 `{success, data}` 包装。
- **优先级**：medium

### R9. `/api/tool` 成功响应未按契约包装 `data`

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:426-433`
- **具体问题**：返回 `{ "success": true, "output": "...", "duration_ms": 0 }`；契约 5.9 要求 `{ "success": true, "data": { "output": "...", "duration_ms": 0 } }`。
- **建议修复方式**：将 `output` / `duration_ms` 移入 `data` 字段。
- **优先级**：medium

### R10. `/api/kb/upload` 未校验文件大小

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:245-284`
- **具体问题**：契约 2.12 规定最大文件大小 50 MB，超限应返回 `FILE_TOO_LARGE`（400）。代码中没有大小检查，直接 `await file.read()` 读取全部内容。
- **建议修复方式**：读取前检查 `len(content_bytes) > 50 * 1024 * 1024`，超限时返回标准错误 `{ "success": false, "error": { "code": "FILE_TOO_LARGE", "message": "..." } }`。
- **优先级**：medium

### R11. `/api/chat` 读取了 `X-Provider` Header 但未使用

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:87,91`
- **具体问题**：从 Header 读取 `x-provider` 后没有参与模型选择或客户端构造，属于死代码/未完成的逻辑。
- **建议修复方式**：若暂不支持按 provider 路由，应移除该读取逻辑或在文档中明确其用途；若支持，应将其传给 `LLMClient` 或用于 base_url/model 选择。
- **优先级**：low

### R12. `/api/models` 依赖魔术字符串过滤错误结果

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:229-232`
- **具体问题**：通过 `models[0].startswith("无法获取")` 判断是否发生错误，脆弱且不符合契约错误响应约定。
- **建议修复方式**：让 `client.list_models()` 在失败时抛出 `LLMError`，外层 `except` 统一返回 `MODEL_FETCH_FAILED`。
- **优先级**：low

### R13. `/api/chat` RAG 检索异常未记录日志

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:144-146`
- **具体问题**：`except Exception` 仅向前端发送 thinking 事件，未记录异常详情，线上排查困难。
- **建议修复方式**：添加 `logger.warning` / `logger.exception` 记录异常及堆栈。
- **优先级**：low

### R14. JSON 错误响应普遍缺少 `details` 字段

- **问题位置**：`E:\Desktop\agent\backend\app\api\routes.py:220,234,267,270,284,297,299,336,358` 等
- **具体问题**：契约 2.6 错误响应示例包含 `"details": null`，代码返回的错误对象普遍缺少该字段。虽然契约标注为可选，但前后端按统一结构解析时可能遇到 KeyError。
- **建议修复方式**：所有错误响应统一包含 `"details": null` 或具体详情对象。
- **优先级**：low

---

## 2. `backend/src/llm/client.py`

### C1. `_with_retries` 会吞掉未捕获的最终异常

- **问题位置**：`E:\Desktop\agent\backend\src\llm\client.py:137-165`
- **具体问题**：重试循环只捕获 `RateLimitError`、`AuthenticationError`、`APIError`、`OSError`。如果 `operation()` 抛出其他异常（如 `ValueError`、`TypeError`、`asyncio.TimeoutError`、JSON 解析错误等），循环结束后 `last_error` 仍为 `None`，最终抛出 `LLMError("LLM operation failed without error detail")`，丢失原始异常类型、消息和堆栈。
- **建议修复方式**：
  - 方案 A：在循环内增加 `except Exception as e: last_error = e; ...` 并在循环结束后 `raise LLMError(...) from last_error`。
  - 方案 B：保留 `last_error` 的原始异常，最终 `raise last_error if last_error else ...`，确保调用方能拿到原始错误信息。
- **优先级**：high

### C2. `chat_stream` 中 Ollama `extra_body` 检测逻辑不可靠

- **问题位置**：`E:\Desktop\agent\backend\src\llm\client.py:249-251`
- **具体问题**：
  - 仅通过 URL 子串 `"ollama"` 或 `"11434"` 判断，任何包含 `11434` 的域名/路径都会误判。
  - 反向代理到 Ollama 但 URL 不含这些子串时会漏判。
  - `extra_body={"think": True}` 不是 OpenAI 标准参数，误发给非 Ollama 兼容服务可能导致 400。
- **建议修复方式**：增加显式的 `provider` 参数（例如 `provider="ollama"`）来决定是否附加 `think`；不要依赖 URL 子串或端口号。
- **优先级**：high

### C3. 流式 usage 提取可能丢失同 chunk 正文内容

- **问题位置**：`E:\Desktop\agent\backend\src\llm\client.py:257-267`
- **具体问题**：当 `chunk.usage` 存在时直接 `yield usage` 并 `continue`，跳过后续 `chunk.choices` 处理。虽然主流实现把 usage 放在 choices 为空的最后一个 chunk，但 API 不保证不会同时携带正文 delta，存在漏 token 的风险。
- **建议修复方式**：先处理 usage，再处理 delta；或仅在 `not chunk.choices` 时才直接 continue。
- **优先级**：low

### C4. 非流式 `chat` 未提取 reasoning 字段

- **问题位置**：`E:\Desktop\agent\backend\src\llm\client.py:167-210`
- **具体问题**：模块注释声明支持 `reasoning_content` / `reasoning` / `thinking`，但 `chat` 只读取 `resp.choices[0].message.content`，会丢弃推理模型返回的思考内容。
- **建议修复方式**：从 `message` 中读取 `reasoning_content` / `reasoning` / `thinking` 并加入 `LLMResponse`（例如新增 `reasoning` 字段），或至少返回给调用方。
- **优先级**：medium

### C5. `chat_stream` 捕获所有 `Exception` 隐藏非 API 错误

- **问题位置**：`E:\Desktop\agent\backend\src\llm\client.py:290-291`
- **具体问题**：`except Exception as e` 将代码自身 bug、类型错误、除零等统一包装为 `LLMError(f"流式请求出错：{e}")`，丢失原始异常类型，调试困难。
- **建议修复方式**：仅捕获 `APIError`、`OSError`、`LLMError` 等已知异常；其余异常应记录后抛出或包装时保留 `from e`。
- **优先级**：medium

### C6. 异常转换未统一记录日志

- **问题位置**：`E:\Desktop\agent\backend\src\llm\client.py:137-165,284-291`
- **具体问题**：`_with_retries` 和 `chat_stream` 在转换异常时均未记录日志，线上出现 `LLMError` 时无法追踪原始 API 错误。
- **建议修复方式**：在 catch 分支使用 `logger.warning` / `logger.exception` 记录异常类型、消息和关键上下文（如 base_url、model）。
- **优先级**：low

---

## 3. `backend/src/config/settings.py`

### S1. 服务端监听端口默认值与契约不一致

- **问题位置**：`E:\Desktop\agent\backend\src\config\settings.py:49`
- **具体问题**：默认端口为 `8000`，契约 2.1 明确本地后端默认服务地址为 `http://127.0.0.1:58080`。
- **建议修复方式**：将默认值改为 `58080`，或在 `.env` 中显式配置 `PORT=58080`。
- **优先级**：low

### S2. 持久化路径缺少启动时可写性校验

- **问题位置**：`E:\Desktop\agent\backend\src\config\settings.py:53-60`
- **具体问题**：`chroma_persist_dir` 和 `sqlite_db_path` 只是归一化为绝对路径，启动时未检查父目录是否存在/可写。若 `data/` 目录不存在或权限不足，运行时首次访问会报错。
- **建议修复方式**：在 `__init__` 或应用启动时对路径父目录执行 `Path(...).parent.mkdir(parents=True, exist_ok=True)`，并捕获 `OSError` 给出友好提示。
- **优先级**：low

### S3. `save_to_env` 写入的键名可能与 alias 不一致

- **问题位置**：`E:\Desktop\agent\backend\src\config\settings.py:97-135`
- **具体问题**：`save_to_env` 直接使用 `overrides` 中的 key 写入 `.env`。如果调用方使用字段名（小写）作为 key，会写入小写 key；而配置读取时通过大写 alias 读取，导致重复或配置不生效。
- **建议修复方式**：将 overrides key 统一映射为字段对应的 alias（大写环境变量名），或在文档中强制约定必须使用 alias。
- **优先级**：low

### S4. `reload()` 未刷新 `_config_path`

- **问题位置**：`E:\Desktop\agent\backend\src\config\settings.py:85-95`
- **具体问题**：`reload()` 创建新 `Settings(_env_file=self._config_path)` 后，把字段写回 `self`，但没有把新实例的 `_config_path` 同步回来（虽然通常相同）。若后续 `_env_file` 来源发生变化，可能导致保存路径不一致。
- **建议修复方式**：在 `reload()` 末尾同步 `self._config_path = new._config_path`。
- **优先级**：low

---

## 4. 汇总

| 优先级 | 数量 | 关键问题 |
| --- | --- | --- |
| high | 6 | `/api/chat` thinking/source/tool 字段、/api/models 错误码、/api/kb/upload 格式、`_with_retries` 吞异常、Ollama 检测不可靠 |
| medium | 8 | 多个端点标准响应格式缺失、文件大小校验、非流式 reasoning、异常捕获过宽 |
| low | 8 | 死代码、魔术字符串、日志缺失、details 字段、端口默认值、路径可写性、save_to_env key、_config_path 同步 |

**首要建议**：在联调前，先集中修复 `routes.py` 中 `/api/chat` 的 SSE 字段、/api/models 错误码、/api/kb/upload 标准格式，以及 `client.py` 的 `_with_retries` 异常吞没和 Ollama 检测问题。
