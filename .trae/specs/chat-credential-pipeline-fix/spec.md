# 对话全链路凭证传递修复 Spec

## Why

用户在前端设置页填入有效的 API Key 与 base_url（如 `https://9router.zxyzx.bbroot.com/v1` 这类 OpenAI 兼容代理），后端却返回 `model_not_found` / `API Key 无效`。根因是前端→后端的凭证（api_key / base_url / model / provider）传递链路存在多处断裂与优先级错乱，导致 key 与 endpoint、key 与 model 来源不一致。

## What Changes

- **统一凭证优先级**：后端 `chat_sse` / `_resolve_creds` / `list_models` 统一改为「header 优先于 payload，payload 优先于 stored，stored 优先于 settings 默认值」，消除 `payload.model` 覆盖 `header_model` 的 bug。
- **抽出共用凭证解析函数**：消除 `chat_routes.py` L156-164 与 L411-422 的重复逻辑，单点维护。
- **key 与 base_url 同源**：`get_provider_key` 改为返回 `(key, base_url)` 元组，保证从加密存储取 key 时能取到配套 base_url，避免 key 用 OpenRouter、base_url 用 OpenAI 默认值的错配。
- **空字符串归一化为 None**：`chat_sse` 入口对 `api_key/base_url/model` 空串统一转 None，`make_client` 用 `if value` 而非 `if value is not None` 判空，避免空串覆盖默认值导致 `AsyncOpenAI(api_key="")` 报错。
- **前端模型选择器支持自定义输入**：在现有固定列表基础上增加一个文本输入框，允许用户填入 OpenRouter / 自建代理要求的完整模型名（如 `oc/deepseek-v4`、`openai/gpt-4o`），并同步到会话 model 与 header。
- **DEFAULT_BASE_URLS 补 openrouter**：前端默认 base_url 表新增 `openrouter` 项，避免 fallback 到 Ollama 本地地址。
- **OpenRouter 端点检测与模型名校验**：`LLMClient.chat_stream` 在 base_url 含 `openrouter` 或非标准代理域名时，对裸模型名（不含 `/`）给出明确错误提示，不直接发远端请求。
- **错误分类修正**：`chat_routes.py` 错误处理区分 `model_not_found`（404/NotFoundError）与 `API Key 无效`（401/AuthenticationError），避免误判。

### BREAKING

- `get_provider_key` 返回类型从 `Optional[str]` 变为 `Optional[tuple[str, str]]`（key, base_url）。调用方需同步适配。

## Impact

- Affected specs: `implement-react-agent-fullstack`（Agent 路径复用同一凭证链路）、`frontend-ux-p0-batch`（模型选择器交互）
- Affected code:
  - 后端：`backend/app/api/chat_routes.py`、`backend/app/api/auth.py`、`backend/app/api/common.py`、`backend/src/llm/client.py`
  - 前端：`frontend/src/stores/useSettingsStore.ts`、`frontend/src/stores/useChatStore.ts`、`frontend/src/api/client.ts`、`frontend/src/components/settings/`（模型选择器组件）

## ADDED Requirements

### Requirement: 统一凭证优先级

系统 SHALL 在所有需要解析 api_key/base_url/model/provider 的入口（chat_sse、_resolve_creds、list_models、Agent 路径）使用同一优先级链：header > payload > stored(provider) > settings 默认值，且任一层为空字符串时视为未提供。

#### Scenario: header 与 payload 同时提供 model

- **WHEN** 请求 header 带 `X-Model: oc/deepseek-v4` 且 body 中 `model: gpt-4o`
- **THEN** 后端使用 header 的 `oc/deepseek-v4`，不被 body 覆盖

#### Scenario: 空字符串凭证归一化

- **WHEN** header 未传 X-API-Key 且 payload 无 api_key 且 stored_key 为空字符串
- **THEN** 后端将 api_key 视为 None，回退到 settings.llm_api_key，而非用空字符串创建 AsyncOpenAI

### Requirement: key 与 base_url 同源

系统 SHALL 在从加密存储读取 provider key 时，同时返回该 provider 存储时配套的 base_url，并在 chat_sse 中优先使用与 key 同源的 base_url。

#### Scenario: 用户在 openai 名下存了 OpenRouter 配置

- **WHEN** 用户在 activeProvider="openai" 下存入 key=sk-or-xxx, base_url=https://openrouter.ai/api/v1
- **AND** 后续请求 header 未带 X-Base-URL
- **THEN** 后端从 stored 取 key 时同时取到 base_url=https://openrouter.ai/api/v1，二者同源使用

### Requirement: 前端模型选择器支持自定义模型名

系统 SHALL 在模型选择器中提供一个文本输入框，允许用户填入任意模型名（如 `oc/deepseek-v4`、`openai/gpt-4o`），该值同步到会话 model 与 X-Model header。

#### Scenario: 用户填入 OpenRouter 完整模型名

- **WHEN** 用户在模型选择器自定义输入框填入 `oc/deepseek-v4`
- **THEN** 会话 model 更新为 `oc/deepseek-v4`
- **AND** 后续请求 X-Model header 为 `oc/deepseek-v4`
- **AND** 后端 payload.model 也为 `oc/deepseek-v4`（与 header 一致，不再冲突）

### Requirement: OpenRouter 端点检测与模型名校验

系统 SHALL 在 LLMClient 发起请求前，当 base_url 指向 OpenRouter 或非标准代理时，校验模型名是否包含 `/` 分隔符；若为裸名则返回明确错误，不发起远端请求。

#### Scenario: OpenRouter 收到裸模型名

- **WHEN** base_url 含 `openrouter` 且 model 为 `gpt-4o`（不含 `/`）
- **THEN** 系统返回错误：「OpenRouter 要求完整模型名（如 openai/gpt-4o），请在前端模型选择器填入完整名」
- **AND** 不发起远端 HTTP 请求

## MODIFIED Requirements

### Requirement: 错误分类

`chat_routes.py` 错误处理 SHALL 区分以下错误类型，不再将 model_not_found 误判为 API Key 无效：

- `AuthenticationError`（401）→ `AUTH_FAILED`「API Key 无效」
- `NotFoundError`（404）/ 错误信息含 `model` + `not found` → `MODEL_NOT_FOUND`「模型不存在：{model}」
- 其他 `APIError` → `INTERNAL_ERROR`

## REMOVED Requirements

### Requirement: payload.model 优先于 header_model

**Reason**: 导致 body 中的默认 model（gpt-4o）覆盖 header 中用户实时切换的模型，是 model_not_found 的直接根因。
**Migration**: 统一改为 header 优先；前端 sendMessage 仍可在 body 带 model 但仅作冗余，后端不优先采纳。
