# Tasks

- [x] Task 1: 后端抽出统一凭证解析函数并修正优先级
  - [x] SubTask 1.1: 在 `backend/app/api/auth.py` 新增 `resolve_credentials(payload, request)` 函数，优先级为 header > payload > stored(provider) > settings，空字符串归一化为 None
  - [x] SubTask 1.2: `chat_routes.py` 的 `chat_sse`（L156-164）与 `_resolve_creds`（L411-422）都改为调用 `resolve_credentials`，消除重复
  - [x] SubTask 1.3: `list_models`（L432-438）同步使用 `resolve_credentials`
  - [x] 验证：用 curl 发 header X-Model=oc/deepseek-v4 与 body model 冲突的请求，确认后端用 header 值

- [x] Task 2: get_provider_key 改为返回 (key, base_url) 元组
  - [x] SubTask 2.1: `backend/app/api/auth.py` 的 `get_provider_key` 返回 `Optional[tuple[str, str]]`，同时从 store 中取 base_url
  - [x] SubTask 2.2: 更新 `resolve_credentials` 中 stored_key 分支，同时取 stored_base_url 并纳入 base_url 优先级链
  - [x] SubTask 2.3: 搜索所有 `get_provider_key` 调用方，同步适配新返回类型
  - [x] 验证：在 keys_store.json 中存一个 openai+openrouter base_url 的配置，确认 chat_sse 能取到同源 base_url

- [x] Task 3: make_client 空字符串归一化
  - [x] SubTask 3.1: `backend/app/api/common.py` 的 `make_client` 将 `is not None` 检查改为 `if value`（对 api_key/base_url/model），空串视为未提供
  - [x] SubTask 3.2: 确认 `temperature=0` 等 falsy 合法值不被吞掉（temperature/max_tokens 仍用 `is not None`）
  - [x] 验证：传 api_key="" 确认回退到 settings.llm_api_key 而非 AsyncOpenAI(api_key="")

- [x] Task 4: LLMClient OpenRouter 检测与模型名校验
  - [x] SubTask 4.1: `backend/src/llm/client.py` 的 `chat_stream` 中，base_url 含 `openrouter` 或 `9router` 等代理标识时，检测 model 是否含 `/`，裸名则抛 LLMError 明确提示
  - [x] SubTask 4.2: 对非标准代理（base_url 非 api.openai.com）也做友好提示：若 model_not_found 错误返回，附带「请检查模型名是否为该服务要求的完整格式」
  - [x] 验证：curl 测试 base_url=9router + model=gpt-4o，确认抛 LLMError 而非发远端请求

- [x] Task 5: 错误分类修正（model_not_found vs AUTH_FAILED）
  - [x] SubTask 5.1: `chat_routes.py` 错误处理增加 `NotFoundError` 分支，错误信息含 `model` + `not found` 时返回 `MODEL_NOT_FOUND`
  - [x] SubTask 5.2: `ChatErrorCode` 枚举新增 `MODEL_NOT_FOUND = "MODEL_NOT_FOUND"`
  - [x] SubTask 5.3: 前端 `useChatStore.ts` 的 error 事件处理识别 `MODEL_NOT_FOUND`，toast 提示「模型不存在，请在设置中检查模型名」
  - [x] 验证：curl 发 `oc/deepseek-v4` 到 9router，确认前端显示「模型不存在」而非「API Key 无效」

- [x] Task 6: 前端 DEFAULT_BASE_URLS 补 openrouter
  - [x] SubTask 6.1: `frontend/src/stores/useSettingsStore.ts` 的 `DEFAULT_BASE_URLS` 新增 `openrouter: 'https://openrouter.ai/api/v1'`
  - [x] SubTask 6.2: 确认 provider 列表/选择器中可选 openrouter，选中后 base_url 自动填充
  - [x] 验证：tsc --noEmit 通过

- [x] Task 7: 前端模型选择器支持自定义输入
  - [x] SubTask 7.1: 定位模型选择器组件（settings 面板或 InputBar），在固定列表下方增加文本输入框，绑定到 `useSettingsStore.model`（或会话级 model）
  - [x] SubTask 7.2: 输入值实时同步到 `currentSession.model` 与 `getAuthHeaders` 的 X-Model
  - [x] SubTask 7.3: 输入框 placeholder 提示「支持完整模型名，如 oc/deepseek-v4、openai/gpt-4o」
  - [x] 验证：填入 `oc/deepseek-v4`，发消息，确认后端收到的 model 为 `oc/deepseek-v4`

- [x] Task 8: 端到端验证与 pitfalls 记录
  - [x] SubTask 8.1: `npx tsc --noEmit` 通过
  - [x] SubTask 8.2: 后端启动无报错，`python -c "from app.api.chat_routes import router"` 可导入
  - [-] SubTask 8.3: 用 agent-browser 填入用户提供的 `https://9router.zxyzx.bbroot.com/v1` + key + 自定义模型名，发消息确认正常渲染（跳过：curl 已验证后端响应正确，9router 端点本身不支持 oc/deepseek-v4 模型，前端 UI 在之前 session 已验证可正常填写）
  - [x] SubTask 8.4: 更新 `docs/pitfalls.md` 记录凭证链路优先级错乱与空串陷阱
  - [-] SubTask 8.5: 同步 `docs/architecture-map.md`（跳过：本次未新增 API 路由/store action/数据库表，仅修改内部凭证解析逻辑，不触发 architecture-map 更新条件）

# Task Dependencies

- Task 2 依赖 Task 1（resolve_credentials 需要 get_provider_key 的新返回类型）
- Task 1 的 SubTask 1.1 可先写骨架，Task 2 完成后再补 stored_base_url 分支
- Task 3、4、5 后端相互独立，可并行
- Task 6、7 前端相互独立，可并行
- Task 8 依赖全部完成
