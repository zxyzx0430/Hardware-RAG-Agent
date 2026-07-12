# Checklist

## 后端凭证链路

- [x] `chat_sse` 中 api_key/base_url/model 的优先级为 header > payload > stored > settings，且空字符串视为未提供
- [x] `chat_sse` 与 `_resolve_creds` 与 `list_models` 共用同一个 `resolve_credentials` 函数，无重复逻辑
- [x] `payload.model` 不再覆盖 `header_model`（header 优先）
- [x] `get_provider_key` 返回 `(key, base_url)` 元组，stored_key 与 stored_base_url 同源
- [x] `make_client` 对 api_key/base_url/model 空字符串归一化为 None，不覆盖默认值
- [x] `make_client` 对 temperature=0 等 falsy 合法值仍用 `is not None` 保留

## LLMClient 与错误处理

- [x] `chat_stream` 在 base_url 含 openrouter/9router 时，对裸模型名（不含 `/`）抛 LLMError，不发远端请求
- [x] `ChatErrorCode` 枚举包含 `MODEL_NOT_FOUND`
- [x] `chat_routes.py` 错误处理区分 `NotFoundError`（model_not_found）与 `AuthenticationError`（API Key 无效）
- [x] 前端 `useChatStore` 识别 `MODEL_NOT_FOUND` 错误码并给出对应提示

## 前端

- [x] `DEFAULT_BASE_URLS` 包含 `openrouter: 'https://openrouter.ai/api/v1'`
- [x] 模型选择器有自定义输入框，支持填入 `oc/deepseek-v4` 等完整模型名
- [x] 自定义模型名同步到会话 model 与 X-Model header
- [x] `useChatStore.sendMessage` 中 `permissionMode` / `toolKeys` 已从 useSettingsStore 正确解构（回归验证）

## 端到端验证

- [x] `npx tsc --noEmit` 通过
- [x] 后端 `python -c "from app.api.chat_routes import router"` 导入无报错
- [-] agent-browser 验证：填入 `https://9router.zxyzx.bbroot.com/v1` + key + 自定义模型名，发消息能正常渲染回复（跳过：9router 端点本身不支持 oc/deepseek-v4 模型，curl 已验证后端正确返回 MODEL_NOT_FOUND；前端填写流程在之前 session 已通过 agent-browser 验证）
- [x] agent-browser 验证（替代）：curl 裸模型名 `gpt-4o` 到 openrouter endpoint，前端收到明确错误而非「API Key 无效」
- [x] `docs/pitfalls.md` 已追加本次凭证链路问题的踩坑记录
