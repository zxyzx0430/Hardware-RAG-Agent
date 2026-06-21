# Hardware RAG Agent — 全项目代码 Review 报告

> 评审日期：2026-06-21
> 评审范围：01-app ~ 08-infra 共 8 个线程
> 评审基线：`docs/api-contract.md` (v1.0)、`docs/completed.md`、`docs/pitfalls.md`
> 评审维度：功能对齐 / 代码安全 / 代码质量 / 文档准确性 / 遗留 mock / 可扩展性 / 虚假实现 / 边界降级
> 严重度定义：
> - **P0**：阻断核心功能或存在安全漏洞，必须立即修复
> - **P1**：影响功能正确性或稳定性，应尽快修复
> - **P2**：代码质量 / 可维护性问题，有空再修
> - **P3**：优化建议，可选

## 总览

| 线程 | P0 | P1 | P2 | P3 | 合计 | 核心问题 |
|------|----|----|----|----|------|----------|
| 01-app | 0 | 2 | 4 | 6 | 12 | 面板 resize 闭包、TopBar 类型崩溃 |
| 02-chat | 2 | 6 | 9 | 4 | 21 | 多模态 RAG 崩溃、SSE AbortController 死代码 |
| 03-knowledge | 1 | 5 | 13 | 5 | 24 | TranslationPipeline 死代码、PDF 解析降级 |
| 04-session | 4 | 5 | 8 | 7 | 24 | 假 Bearer 鉴权、gitignore 路径错、原生 SQL 崩溃 |
| 05-agent | 1 | 4 | 7 | 8 | 20 | MCP 工具无法调用 (AttributeError) |
| 06-sandbox | 3 | 7 | 8 | 2 | 20 | C/C++ 代码未传入容器、Arduino 失效、async 阻塞 sync |
| 07-hardware | 6 | 14 | 12 | 8 | 40 | WS 鉴权造假、monitor/build/upload/audit_pins 全 stub |
| 08-infra | 2 | 8 | 10 | 2 | 22 | CORS 配置死代码、requirements.txt 缺依赖 |
| **合计** | **19** | **51** | **71** | **42** | **183** | — |

**P0 阻断项汇总（必须立即修复）**：

1. `02-chat`：多模态 RAG 检索时 `images` 字段未传导致后端崩溃
2. `02-chat`：`useSSE` 中 `AbortController` 死代码，无法真正取消请求
3. `03-knowledge`：`TranslationPipeline` 类完整但从未被调用
4. `04-session`：`get_provider_key_by_session(token)` 定义但从未调用，Bearer 鉴权形同虚设
5. `04-session`：`.gitignore` 路径与实际加密密钥位置不匹配，密钥可能被提交
6. `04-session`：原生 SQL `LIKE` 拼接 + 缺少 FTS 虚拟表，搜索会崩
7. `04-session`：`/api/sessions` 返回裸对象而非 `{success, data}`，违反契约
8. `05-agent`：MCP 工具调用时 `handler.run` AttributeError，工具永远调不通
9. `06-sandbox`：C/C++ 代码从未通过 stdin 传入容器，编译永远失败
10. `06-sandbox`：Arduino 编译器路径硬编码且不存在
11. `06-sandbox`：`asyncio.run` 在已有事件循环中阻塞
12. `07-hardware`：WebSocket `/api/monitor/{port}` 鉴权造假
13. `07-hardware`：`/api/build`、`/api/upload`、`/api/audit_pins` 全部为 stub
14. `07-hardware`：`/api/diagnose` 编译检查硬编码返回 PASS
15. `07-hardware`：`apiWS` 硬编码 8000 端口与契约不符
16. `08-infra`：CORS 配置在 `main.py` 死代码中，实际生效的是开发态宽松配置
17. `08-infra`：`requirements.txt` 缺少 `aiofiles`、`python-multipart`、`httpx` 等运行时依赖

---

## 01-app（布局/导航/主题）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 代码质量 | P1 | `frontend/src/components/ResizablePanel.tsx` | 面板 resize 拖动回调使用闭包旧值，拖动时尺寸跳变 | 用 `useRef` 缓存最新尺寸或 `useCallback` 依赖项加 `size` |
| 代码质量 | P1 | `frontend/src/components/TopBar.tsx` | `ContentPart[]` 类型断言后直接 `.map`，当 `content` 是 string 时崩溃 | 加 `Array.isArray` 守卫或类型 narrowing |
| 代码质量 | P2 | `frontend/src/store/useAppStore.ts` | `switchNav` 中 `if (prev.view === view)` 早返回但未重置 sidebar 状态 | 拆分"切换视图"与"重置布局"两个语义 |
| 代码质量 | P2 | `frontend/src/components/HamburgerMenu.tsx` | 早期版本未调用 `useI18n()`，`t` 函数未定义（已修复，需验证） | 加单测覆盖 i18n 渲染 |
| 可扩展性 | P2 | `frontend/src/theme/tokens.ts` | 主题 token 散落多处，新增主题需改 5+ 文件 | 抽取 `ThemePreset` 类型 + 单一注册表 |
| 可扩展性 | P2 | `frontend/src/components/Sidebar.tsx` | 导航项硬编码数组，新增页面需改 3 处 | 抽取 `navItems` 配置 + 路由表联动 |
| 代码质量 | P3 | `frontend/src/App.tsx` | 顶层 `useEffect` 依赖 `[]` 但内部读了 store 状态 | 用 `useEffect` 依赖项或 `useMemo` |
| 代码质量 | P3 | `frontend/src/components/TopBar.tsx` | 多个 `useState` 可合并为 `useReducer` | 视情况重构 |
| 代码质量 | P3 | `frontend/src/store/useAppStore.ts` | `persist` 中间件未配置 `partialize`，整个 store 被持久化 | 仅持久化 `theme`、`sidebarCollapsed` |
| 可扩展性 | P3 | `frontend/src/theme/index.ts` | 暗色/亮色切换未走 CSS 变量，而是 className 切换 | 迁移到 CSS 变量以支持动态主题 |
| 文档准确性 | P3 | `docs/completed.md` | 01-app 完成度标注 100%，但 i18n 仍有未翻译 key | 补全 i18n key 后再标 100% |
| 边界降级 | P3 | `frontend/src/components/ResizablePanel.tsx` | 拖动到 0px 时未做最小值守卫 | 加 `Math.max(minSize, newSize)` |

---

## 02-chat（SSE 流式聊天）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 虚假实现 | P0 | `frontend/src/api/client.ts` `apiSSE` | `externalController` 参数存在但内部仍创建新 `AbortController`，外部传入的从未被使用，导致取消请求无效 | 优先使用 `externalController`，仅在其为空时创建内部 controller |
| 边界降级 | P0 | `backend/app/api/routes.py` `/api/chat` | 多模态消息（含 `images`）走 RAG 时，`images` 字段未透传给 LLM，导致后端 `KeyError` 或前端无响应 | 在 `chat_request` 中显式提取 `images` 并透传至 `llm.chat()` |
| 代码安全 | P1 | `backend/app/api/routes.py` `/api/chat` | 用户消息未做长度限制，可构造超长 prompt 打爆 LLM | 加 `len(content) <= 32000` 校验 |
| 代码质量 | P1 | `frontend/src/hooks/useSSE.ts` | `onDone` 回调中 `streamingSteps.length > 0` 判断后未清空 `streamingSteps`，下次发送会残留 | `onDone` 末尾 `setStreamingSteps([])` |
| 代码质量 | P1 | `frontend/src/hooks/useSSE.ts` | `source` 事件累积逻辑：当 `source` 切换时旧 step 未关闭，导致多个 thinking 卡片并存 | 切换 `source` 时自动 close 上一个 step |
| 代码质量 | P1 | `frontend/src/components/ThinkingStep.tsx` | `step.id` 在 reasoning 模式下每次更新都变化，导致组件 remount 丢失展开状态 | 用 `source + index` 作为稳定 key |
| 边界降级 | P1 | `frontend/src/api/client.ts` `apiSSE` | 连接超时 60s 后 `onError` 未被调用，前端永远卡在 streaming 状态 | 超时 abort 后显式 `onError(new Error('timeout'))` |
| 边界降级 | P1 | `backend/app/api/routes.py` `/api/chat` | LLM 返回 401（无效 Key）时未捕获，直接 500 | 捕获 `openai.AuthenticationError` 返回 401 |
| 代码质量 | P2 | `frontend/src/store/useChatStore.ts` | `sendMessage` 中 `isStreaming` 守卫与 `messages.length - 1` 判断耦合，难以维护 | 抽取 `isLastMessage` 选择器 |
| 代码质量 | P2 | `frontend/src/components/ChatArea.tsx` | 自动滚动逻辑与流式渲染耦合，用户上滑后仍会被强制滚动 | 用 `IntersectionObserver` 判断是否在底部 |
| 代码质量 | P2 | `frontend/src/components/MessageAction.tsx` | 重试/编辑未加 `isStreaming` 守卫（已部分修复，需验证） | 加 `disabled={isStreaming}` |
| 代码质量 | P2 | `frontend/src/components/ActivityCard.tsx` | `iteration` 字段定义但从未渲染 | 渲染迭代序号或删除字段 |
| 遗留 mock | P2 | `backend/app/api/routes.py` L280-284 | ReAct Agent 标注 TODO，实际仍是普通 RAG | 实现 ReAct 或在文档中标注"未实现" |
| 代码质量 | P2 | `frontend/src/hooks/useSSE.ts` | `thinking` 事件 `source` 字段缺省时默认 `'llm'`，但后端可能不发送 `source` | 前后端约定 `source` 必填 |
| 代码质量 | P2 | `frontend/src/components/ThinkingStep.tsx` | `expanded` 状态在 streaming 时被 prop 变更重置 | 用 `useRef` 锁定首次展开 |
| 代码质量 | P2 | `backend/app/api/routes.py` | `sse_event()` helper 未对 `data` 做 JSON 转义，含换行符时会破坏 SSE 协议 | 用 `json.dumps(data, ensure_ascii=False)` |
| 文档准确性 | P2 | `docs/api-contract.md` 5.1 | SSE 事件类型文档列出 `tool_call`，但实际代码用 `tool` | 同步文档与代码 |
| 代码质量 | P3 | `frontend/src/components/ChatInput.tsx` | Enter 发送逻辑未区分 IME 组合输入（中文输入法回车） | 加 `isComposing` 守卫 |
| 代码质量 | P3 | `frontend/src/store/useChatStore.ts` | `messages` 数组无上限，长对话会 OOM | 加滚动窗口或分页加载 |
| 边界降级 | P3 | `frontend/src/api/client.ts` | `apiSSE` 未处理 `ReadableStream` 读取异常 | 加 `try/catch` 包裹 `reader.read()` |
| 代码质量 | P3 | `frontend/src/components/ActivityCard.tsx` | 工具图标匹配用 `if-else` 链，新增工具需改 5 处 | 用 `Record<string, Icon>` 映射表 |

---

## 03-knowledge（知识库 RAG）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 虚假实现 | P0 | `backend/app/kb/translation_pipeline.py` | `TranslationPipeline` 类完整定义但从未被任何路由调用，知识库翻译功能形同虚设 | 在 `/api/kb/upload` 后接入翻译流水线，或删除该类 |
| 代码质量 | P1 | `backend/app/kb/docling_loader.py` | Docling 解析失败时静默返回空字符串，用户无感知 | 抛出 `HTTPException(422)` 并记录日志 |
| 代码质量 | P1 | `backend/app/kb/chroma_store.py` | `get_vector_store()` 单例未加锁，并发上传时重复初始化 | 用 `threading.Lock` 或 `asyncio.Lock` |
| 边界降级 | P1 | `backend/app/api/routes.py` `/api/kb/upload` | PDF 超过 50MB 时未做前置校验，上传中途 OOM | 加 `Content-Length` 校验 |
| 代码安全 | P1 | `backend/app/kb/docling_loader.py` | PDF 解析时未限制嵌套深度，恶意 PDF 可导致递归栈溢出 | 加 `max_depth=10` 限制 |
| 代码质量 | P1 | `backend/app/kb/chroma_store.py` | `collection.get()` 返回结果未做 `ids is None` 守卫 | 加空值检查 |
| 代码质量 | P2 | `backend/app/kb/chroma_store.py` | `embedding_function` 硬编码 `all-MiniLM-L6-v2`，无法切换 | 抽取为配置项 |
| 代码质量 | P2 | `backend/app/kb/docling_loader.py` | 表格识别失败时降级为纯文本，但未在响应中标注 | 返回 `parse_warnings` 字段 |
| 代码质量 | P2 | `backend/app/kb/chroma_store.py` | `similarity_search` 的 `k` 值硬编码 4 | 从请求参数透传 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/kb/list` | 返回字段 `size` 为字节数，前端展示未格式化 | 后端返回 `size_mb` 或前端格式化 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/kb/delete` | 删除时未校验文件是否存在，404 与 200 行为不一致 | 先 `os.path.exists` 再删 |
| 代码质量 | P2 | `backend/app/kb/chroma_store.py` | 删除文档时仅删向量，未删原始 PDF | 加 `os.remove(pdf_path)` |
| 代码质量 | P2 | `backend/app/kb/docling_loader.py` | 临时文件未用 `tempfile.NamedTemporaryFile`，手动清理易漏 | 用 `with` 上下文管理器 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/kb/upload` | 上传成功后未返回 `document_id`，前端无法追踪 | 返回 `{"document_id": ...}` |
| 代码质量 | P2 | `backend/app/kb/chroma_store.py` | `metadata` 中 `source` 字段为绝对路径，泄露服务器目录 | 改为相对路径或文件名 |
| 代码质量 | P2 | `backend/app/kb/translation_pipeline.py` | 翻译结果未做质量校验，可能返回空字符串 | 加 `len(translated) > 0` 校验 |
| 代码质量 | P2 | `backend/app/api/routes.py` | `/api/kb/list` 未做分页，知识库大时响应慢 | 加 `offset`/`limit` 参数 |
| 代码质量 | P2 | `backend/app/kb/chroma_store.py` | `persist()` 调用频繁，每次上传都写盘 | 改为定时或批量持久化 |
| 文档准确性 | P2 | `docs/api-contract.md` 5.4 | 文档标注 `/api/kb/upload` 返回 `{document_id}`，实际未返回 | 同步代码与文档 |
| 代码质量 | P3 | `backend/app/kb/docling_loader.py` | 日志使用 `print`，未走 `logging` | 改用 `logger.info` |
| 代码质量 | P3 | `backend/app/kb/chroma_store.py` | 集合名硬编码 `hardware_kb` | 抽取为配置 |
| 代码质量 | P3 | `backend/app/kb/translation_pipeline.py` | 翻译模型硬编码 `gpt-4o-mini` | 从设置读取 |
| 代码质量 | P3 | `backend/app/api/routes.py` | `/api/kb/list` 排序逻辑在前端，后端未保证顺序 | 后端按 `created_at DESC` 排序 |
| 代码质量 | P3 | `backend/app/kb/docling_loader.py` | OCR 语言未配置，中文 PDF 识别率低 | 加 `lang=['zh', 'en']` |
| 代码质量 | P3 | `backend/app/kb/chroma_store.py` | 未暴露 `relevance_score` 给前端 | 在 metadata 中加 `score` |

---

## 04-session（持久化/设置）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 代码安全 | P0 | `backend/app/api/auth.py` | `get_provider_key_by_session(token)` 定义但从未被任何路由调用，所有 Bearer 鉴权形同虚设，任何人可访问任意 session | 在所有受保护路由的依赖中注入 `current_user = Depends(get_provider_key_by_session)` |
| 代码安全 | P0 | `.gitignore` | 加密密钥路径 `backend/app/db/.enc_key` 与实际位置 `backend/app/db/keys_store.json` 不匹配，密钥文件可能被提交到 git | 修正 `.gitignore` 路径并加 `**/.enc_key`、`**/keys_store.json` |
| 边界降级 | P0 | `backend/app/api/crud.py` `/api/sessions/search` | 原生 SQL `LIKE` 直接拼接 `f"%{q}%"`，存在 SQL 注入；且 FTS 虚拟表未创建，查询会崩 | 用参数化查询 `WHERE title LIKE :q` + 在 `init_db` 中创建 FTS 表 |
| 功能对齐 | P0 | `backend/app/api/crud.py` | 所有 CRUD 路由返回裸对象（如 `{"sessions": [...]}`），违反契约 `{success, data}` 格式 | 统一用 `{"success": True, "data": ...}` 包装 |
| 代码安全 | P1 | `backend/app/api/auth.py` | Fernet 加密密钥首次启动时生成但未做权限校验，任意用户可读 | `os.chmod(key_path, 0o600)` |
| 代码质量 | P1 | `backend/app/api/crud.py` | `SessionUpdate` 允许更新 `id` 字段，可覆盖主键 | `SessionUpdate` 中排除 `id` |
| 代码质量 | P1 | `backend/app/api/crud.py` | `delete_session` 未级联删除 messages，外键孤儿 | 加 `ON DELETE CASCADE` 或手动级联 |
| 边界降级 | P1 | `backend/app/api/crud.py` | `get_session` 未校验 session 是否属于当前用户 | 加 `WHERE user_id = :uid` |
| 代码质量 | P1 | `backend/app/api/crud.py` | `ALLOWED_SETTINGS_KEYS` 白名单与前端 settings 不一致（缺 `maxTokens` 迁移字段） | 同步白名单与前端 |
| 代码质量 | P2 | `backend/app/api/crud.py` | `branch_from_session_id`/`branch_from_message_id` 字段已加但无业务逻辑 | 实现分支创建逻辑或在文档标注"未实现" |
| 代码质量 | P2 | `backend/app/db/models.py` | `Session.branch_from_session_id` 未加外键约束 | 加 `ForeignKey("sessions.id")` |
| 代码质量 | P2 | `backend/app/api/crud.py` | `get_sessions` 未做分页 | 加 `offset`/`limit` |
| 代码质量 | P2 | `backend/app/db/models.py` | `Message.metadata` 字段名与 SQLAlchemy 保留字冲突 | 改名 `extra_metadata` |
| 代码质量 | P2 | `backend/app/api/crud.py` | `update_settings` 未做 upsert，首次设置会崩 | 用 `INSERT ... ON CONFLICT UPDATE` |
| 代码质量 | P2 | `backend/app/api/crud.py` | `get_settings` 返回裸 dict，缺字段时前端崩 | 用 `SettingsResponse` 模型补默认值 |
| 代码质量 | P2 | `backend/app/db/models.py` | `Session.created_at` 用 `datetime.now` 而非 `datetime.utcnow`，时区不一致 | 用 `datetime.utcnow` 或 `datetime.now(timezone.utc)` |
| 代码质量 | P2 | `backend/app/api/crud.py` | 事务未显式 commit，依赖 `Session` 自动提交 | 显式 `db.commit()` |
| 代码质量 | P2 | `backend/app/db/database.py` | `init_db` 未做迁移，新增字段需手动 `ALTER TABLE` | 接入 Alembic |
| 代码质量 | P3 | `backend/app/api/crud.py` | `SessionCreate` 中 `branch_from_*` 字段无文档说明 | 加 docstring |
| 代码质量 | P3 | `backend/app/db/models.py` | `Message.role` 用 `String` 而非 `Enum` | 用 `Enum("user", "assistant", "tool")` |
| 代码质量 | P3 | `backend/app/api/crud.py` | 日志未记录操作者 user_id | 加 `logger.info(..., extra={"user_id": uid})` |
| 代码质量 | P3 | `backend/app/db/database.py` | `engine` 未配置 `pool_size`/`max_overflow` | 加 `create_engine(..., pool_size=10, max_overflow=20)` |
| 代码质量 | P3 | `backend/app/api/crud.py` | `delete_session` 未返回删除的记录数 | 返回 `{"deleted": n}` |
| 代码质量 | P3 | `backend/app/db/models.py` | `Session` 表无索引 `created_at` | 加 `Index("idx_sessions_created", "created_at")` |
| 代码质量 | P3 | `backend/app/api/crud.py` | `get_sessions` 排序逻辑在前端，后端未保证顺序 | 后端 `ORDER BY pinned DESC, created_at DESC` |

---

## 05-agent（LangGraph Agent）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 虚假实现 | P0 | `backend/app/agent/handler.py` | MCP 工具调用时 `handler.run(input)` 抛 `AttributeError`，`handler` 是 `MCPClient` 实例但无 `run` 方法，工具永远调不通 | 改为 `handler.call_tool(name, args)` 或实现 `run` 方法 |
| 代码质量 | P1 | `backend/app/agent/graph.py` | LangGraph 节点定义但未注册到 `StateGraph`，图无法编译 | 用 `graph.add_node()` 注册 |
| 代码质量 | P1 | `backend/app/agent/handler.py` | `AgentHandler.invoke` 未捕获工具异常，单个工具失败导致整个 agent 崩 | 加 `try/except` 包裹工具调用 |
| 代码质量 | P1 | `backend/app/agent/mcp_client.py` | `MCPClient` 未实现重连，stdio 进程崩溃后无法恢复 | 加 `reconnect()` 方法 + 心跳检测 |
| 边界降级 | P1 | `backend/app/agent/handler.py` | 工具调用超时未设置，恶意工具可永久阻塞 | 加 `asyncio.wait_for(coro, timeout=30)` |
| 代码质量 | P2 | `backend/app/agent/graph.py` | `State` 类型用 `TypedDict` 但字段未加 `Annotated[Reducer]`，并发更新会覆盖 | 用 `Annotated[list, operator.add]` |
| 代码质量 | P2 | `backend/app/agent/handler.py` | `tool_history` 用 `list` 无上限，长对话 OOM | 用 `deque(maxlen=50)` |
| 代码质量 | P2 | `backend/app/agent/mcp_client.py` | JSON-RPC 请求未加 `id` 校验，响应错乱时无法匹配 | 用自增 `id` + `dict` 等待队列 |
| 代码质量 | P2 | `backend/app/agent/handler.py` | `invoke` 返回 `str`，但前端期望 `ActivityBlock` 结构 | 返回结构化 `ActivityBlock` |
| 代码质量 | P2 | `backend/app/agent/graph.py` | 条件边 `should_continue` 硬编码工具名，新增工具需改代码 | 用 `tools` 列表动态判断 |
| 代码质量 | P2 | `backend/app/agent/mcp_client.py` | `stdio` 进程的 `stderr` 未消费，缓冲区满会死锁 | 起 `asyncio.Task` 持续读 `stderr` |
| 代码质量 | P2 | `backend/app/agent/handler.py` | 工具 schema 未做 JSON Schema 校验，恶意工具可注入任意参数 | 用 `jsonschema.validate` |
| 文档准确性 | P2 | `docs/api-contract.md` 5.16 | 文档标注 `/api/tool` 返回 `{tool, result}`，实际返回 `{output}` | 同步文档与代码 |
| 代码质量 | P3 | `backend/app/agent/graph.py` | `StateGraph` 未配置 `memory`，多轮对话无状态 | 用 `MemorySaver` |
| 代码质量 | P3 | `backend/app/agent/handler.py` | 日志未记录工具调用耗时 | 加 `time.perf_counter()` |
| 代码质量 | P3 | `backend/app/agent/mcp_client.py` | 工具列表未缓存，每次调用都重新拉取 | 加 `@lru_cache` 或 TTL 缓存 |
| 代码质量 | P3 | `backend/app/agent/graph.py` | 节点函数用 `lambda`，无法调试 | 改为 `def` 函数 |
| 代码质量 | P3 | `backend/app/agent/handler.py` | `max_iterations` 硬编码 10 | 从配置读取 |
| 代码质量 | P3 | `backend/app/agent/mcp_client.py` | 未支持 SSE transport，仅 stdio | 加 `MCPSSEClient` |
| 代码质量 | P3 | `backend/app/agent/graph.py` | `END` 节点未定义 | 用 `langgraph.END` |
| 代码质量 | P3 | `backend/app/agent/handler.py` | 工具错误未回传给 LLM 做反思 | 把错误塞回 `tool_history` |
| 代码质量 | P3 | `backend/app/agent/mcp_client.py` | 进程退出码未检查 | 加 `returncode != 0` 时抛异常 |

---

## 06-sandbox（沙箱执行）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 虚假实现 | P0 | `backend/app/sandbox/runner.py` `run_python` | C/C++ 代码从未通过 stdin 传入容器，`compile` 阶段永远失败 | 用 `docker exec -i container sh -c "cat > /tmp/code.c"` 或挂载 volume |
| 虚假实现 | P0 | `backend/app/sandbox/runner.py` `run_arduino` | Arduino 编译器路径硬编码 `/opt/arduino/arduino-cli`，容器内不存在 | 改用 `arduino-cli` 镜像或在 Dockerfile 中安装 |
| 边界降级 | P0 | `backend/app/sandbox/runner.py` | `asyncio.run(self._run_container())` 在 FastAPI 已有事件循环中会抛 `RuntimeError: This event loop is already running` | 改为 `await self._run_container()` |
| 代码安全 | P1 | `backend/app/sandbox/runner.py` | 容器虽设 `network_mode='none'`，但 `volumes` 挂载了 `/tmp` 读写，可逃逸 | 改为 `tmpfs` 或只读挂载 |
| 代码安全 | P1 | `backend/app/sandbox/runner.py` | CPU 限制用 `cpu_quota` 但未设 `cpu_period`，实际不生效 | 加 `cpu_period=100000` |
| 代码安全 | P1 | `backend/app/sandbox/runner.py` | 内存限制 `mem_limit='256m'` 但未设 `memswap_limit`，可使用 swap | 加 `memswap_limit='256m'` |
| 边界降级 | P1 | `backend/app/sandbox/runner.py` | Docker daemon 不可用时未捕获 `DockerException`，整个后端崩 | 加 `try/except` 返回 503 |
| 代码质量 | P1 | `backend/app/sandbox/runner.py` | `run_python` 未清理容器，`auto_remove=True` 在异常时不生效 | 加 `finally: container.remove(force=True)` |
| 代码质量 | P1 | `backend/app/api/routes.py` `/api/sandbox/run` | 未校验 `language` 参数，传入 `bash` 可执行任意命令 | 用 `Enum('python','c','cpp','arduino')` 白名单 |
| 代码质量 | P1 | `backend/app/sandbox/runner.py` | 输出未做大小限制，恶意程序可输出 1GB 日志打爆内存 | 截断 `stdout[:1MB]` |
| 代码质量 | P2 | `backend/app/sandbox/runner.py` | `image` 名硬编码 `python:3.11-slim`，无法切换 | 从配置读取 |
| 代码质量 | P2 | `backend/app/sandbox/runner.py` | 编译错误未解析行号，前端无法高亮 | 用 `re` 提取 `file.c:10:5: error` |
| 代码质量 | P2 | `backend/app/sandbox/runner.py` | `run_c` 与 `run_cpp` 逻辑重复 | 抽取 `run_compiled(lang, compiler)` |
| 代码质量 | P2 | `backend/app/api/routes.py` | `/api/sandbox/run` 同步等待，长任务阻塞事件循环 | 用 `BackgroundTasks` 或返回 `task_id` 轮询 |
| 代码质量 | P2 | `backend/app/sandbox/runner.py` | 未记录执行者 user_id | 加日志 |
| 代码质量 | P2 | `backend/app/sandbox/runner.py` | `timeout=10` 硬编码 | 从请求参数读取 |
| 代码质量 | P2 | `backend/app/sandbox/runner.py` | 容器内用户为 root | 加 `user='nobody'` |
| 代码质量 | P2 | `backend/app/api/routes.py` | 并发执行未加信号量，可起 100 个容器打爆宿主机 | 加 `asyncio.Semaphore(4)` |
| 代码质量 | P3 | `backend/app/sandbox/runner.py` | 日志用 `print` | 改用 `logger` |
| 代码质量 | P3 | `backend/app/sandbox/runner.py` | 镜像未预拉取，首次执行慢 | 启动时 `docker pull` |

---

## 07-hardware（硬件工作台）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 代码安全 | P0 | `backend/app/api/routes.py` `/api/monitor/{port}` | WebSocket 鉴权造假，`websocket.accept()` 后未校验 token，任何人可连接串口 | 在 `accept` 前校验 `token` query 参数 |
| 虚假实现 | P0 | `backend/app/api/routes.py` `/api/build` | 标注为 mock SSE，实际只发 `progress` 事件后 `done`，从未真正编译 | 接入 `sandbox.runner.run_arduino` 或删除路由 |
| 虚假实现 | P0 | `backend/app/api/routes.py` `/api/upload` | 标注为 mock SSE，实际只发 `progress` 事件后 `done`，从未真正烧录 | 接入 `pyserial` 烧录或删除路由 |
| 虚假实现 | P0 | `backend/app/api/routes.py` `/api/audit_pins` | 返回硬编码 `{"conflicts": []}`，从未真正检查引脚冲突 | 实现 `audit_pin_conflicts(wiring)` 逻辑 |
| 虚假实现 | P0 | `backend/app/api/routes.py` `/api/diagnose` | 编译检查硬编码返回 `PASS`，从未真正诊断 | 接入 `sandbox` 编译 + `audit_pins` |
| 功能对齐 | P0 | `frontend/src/api/client.ts` `apiWS` | 硬编码 `ws://localhost:8000`，与契约 `ws://127.0.0.1:8000` 不符，且生产环境必崩 | 用 `window.location` 动态拼接 + Vite 代理 |
| 代码安全 | P1 | `backend/app/api/routes.py` `/api/wiring` | `wiring` 数据未做 schema 校验，恶意 JSON 可注入 | 用 `pydantic` 模型校验 |
| 代码安全 | P1 | `backend/app/api/routes.py` `/api/devices` | 串口列表未做权限校验，任意用户可枚举宿主机串口 | 加 `Depends(current_user)` |
| 代码安全 | P1 | `backend/app/api/routes.py` `/api/monitor/{port}` | `port` 参数未做白名单校验，可传 `/dev/..` 路径遍历 | 用 `serial.tools.list_ports` 校验 |
| 代码质量 | P1 | `backend/app/api/routes.py` `/api/monitor/{port}` | WebSocket 连接未做心跳，断网后容器端 serial 不释放 | 加 `websocket.ping()` + 超时关闭 |
| 代码质量 | P1 | `backend/app/api/routes.py` `/api/wiring` | 并发写入同一 `wiring.json` 无锁，会丢数据 | 加 `asyncio.Lock` |
| 代码质量 | P1 | `backend/app/api/routes.py` `/api/devices` | 串口打开后未关闭，FD 泄漏 | 用 `with serial.Serial(...)` |
| 代码质量 | P1 | `backend/app/api/routes.py` `/api/monitor/{port}` | 多客户端连接同一串口未互斥 | 加全局 `port_locks` 字典 |
| 边界降级 | P1 | `backend/app/api/routes.py` `/api/monitor/{port}` | 串口被占用时 `SerialException` 未捕获，崩 500 | 捕获并返回 409 |
| 边界降级 | P1 | `backend/app/api/routes.py` `/api/diagnose` | `wiring` 为空时未做守卫，`audit_pins` 崩 | 加 `if not wiring: return ...` |
| 代码质量 | P1 | `frontend/src/components/WiringPane.tsx` | 拖拽事件未在 `useEffect` 清理，组件卸载后仍监听 | 加 `removeEventListener` |
| 代码质量 | P1 | `frontend/src/components/WiringPane.tsx` | 缩放中心计算错误，鼠标位置与缩放点不一致 | 用 `getBoundingClientRect()` 修正 |
| 代码质量 | P1 | `frontend/src/components/HardwareWorkbench.tsx` | `flashCode` 状态在 `WorkbenchPanel` 与 `useAppStore` 间双向同步，易死循环 | 单一数据源 |
| 代码质量 | P1 | `frontend/src/components/WiringPane.tsx` | 连线数据格式 `{from:{component,pin}, to:{component,pin}}` 与后端期望的 `{from, pin, to_component, to_pin}` 不一致（已部分修复，需验证） | 统一格式 |
| 代码质量 | P2 | `frontend/src/components/WiringPane.tsx` | SVG 节点数量无上限，复杂电路卡顿 | 加虚拟化或节点合并 |
| 代码质量 | P2 | `frontend/src/components/HardwareWorkbench.tsx` | DTR/RTS 控件未真正调用后端 | 接入 `/api/monitor/{port}` 控制帧 |
| 代码质量 | P2 | `frontend/src/components/WiringPane.tsx` | 连线删除未加确认 | 加 `confirm()` |
| 代码质量 | P2 | `frontend/src/components/HardwareWorkbench.tsx` | 日志导出未做过滤 | 加日志级别筛选 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/wiring` | `wiring.json` 存储路径硬编码 | 从配置读取 |
| 代码质量 | P2 | `frontend/src/components/WiringPane.tsx` | 引脚标签重叠时无法点击 | 加 `pointer-events: none` 给标签 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/devices` | 返回字段 `name` 为设备名，缺 `vendor_id`/`product_id` | 补全 USB 信息 |
| 代码质量 | P2 | `frontend/src/components/HardwareWorkbench.tsx` | 串口波特率选项硬编码 | 从配置读取 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/monitor/{port}` | 二进制数据未做 base64 编码，WebSocket 文本帧会崩 | 用 `base64.b64encode(data)` |
| 代码质量 | P2 | `frontend/src/components/WiringPane.tsx` | 撤销/重做未实现 | 加 `history` 栈 |
| 代码质量 | P2 | `frontend/src/components/HardwareWorkbench.tsx` | 串口日志无颜色区分 | 加 ANSI 解析 |
| 代码质量 | P2 | `backend/app/api/routes.py` `/api/audit_pins` | 即使实现，也只检查引脚冲突，未检查电压/电流兼容性 | 扩展 `audit` 逻辑 |
| 代码质量 | P2 | `frontend/src/components/WiringPane.tsx` | 连线类型（电源/信号/地）未区分 | 加 `line_type` 字段 |
| 代码质量 | P3 | `frontend/src/components/WiringPane.tsx` | 节点图标硬编码 | 用 `Record<string, Icon>` |
| 代码质量 | P3 | `backend/app/api/routes.py` `/api/devices` | 未做轮询，前端需手动刷新 | 加 SSE 推送设备变更 |
| 代码质量 | P3 | `frontend/src/components/HardwareWorkbench.tsx` | 串口日志无自动滚动开关 | 加 `autoScroll` toggle |
| 代码质量 | P3 | `frontend/src/components/WiringPane.tsx` | 缩放级别无限制 | 加 `min=0.5, max=3` |
| 代码质量 | P3 | `backend/app/api/routes.py` `/api/monitor/{port}` | 未记录串口通信日志 | 加 `logger.info` |
| 代码质量 | P3 | `frontend/src/components/HardwareWorkbench.tsx` | 串口日志无搜索 | 加 `Ctrl+F` |
| 代码质量 | P3 | `frontend/src/components/WiringPane.tsx` | 连线弯折样式固定 | 加 `orthogonal`/`bezier` 切换 |
| 代码质量 | P3 | `backend/app/api/routes.py` `/api/wiring` | 未做版本管理 | 加 `version` 字段 |
| 代码质量 | P3 | `frontend/src/components/HardwareWorkbench.tsx` | 串口日志无导出格式选择 | 加 `txt`/`csv`/`json` |
| 代码质量 | P3 | `frontend/src/components/WiringPane.tsx` | 节点拖动未做网格对齐 | 加 `snapToGrid` |

---

## 08-infra（Docker/CI/日志）

| 类别 | 严重度 | 文件 | 问题 | 建议 |
|------|--------|------|------|------|
| 代码安全 | P0 | `backend/main.py` | 历史遗留的 mock `create_app()`（已修复为委托模式），但 `backend/app/api_router.py` 仍含 mock 路由，存在被误导入风险 | 删除 `api_router.py` 或加 `# DEPRECATED` 标注 |
| 代码质量 | P0 | `backend/requirements.txt` | 缺少 `aiofiles`、`python-multipart`、`httpx`、`PyYAML` 等运行时依赖，新环境部署必崩 | 补全依赖并 `pip freeze > requirements.txt` |
| 代码安全 | P1 | `backend/app/main.py` | CORS 配置 `allow_origins=["*"]` 在开发态可接受，但生产环境未做环境变量切换 | 用 `os.getenv("CORS_ORIGINS", "").split(",")` |
| 代码安全 | P1 | `backend/app/main.py` | 请求体大小限制 20MB 硬编码 | 从配置读取 |
| 代码质量 | P1 | `backend/app/main.py` | `init_db()` 在 `create_app()` 中同步调用，启动慢 | 改为 `@app.on_event("startup")` 异步初始化 |
| 代码质量 | P1 | `backend/app/main.py` | 未配置 `gunicorn`/`uvicorn-workers`，单进程无法利用多核 | 加 `Dockerfile` CMD 用 `gunicorn -k uvicorn.workers.UvicornWorker` |
| 代码质量 | P1 | `backend/app/main.py` | 未配置 `access_log`，生产环境无日志 | 加 `logging.config.dictConfig` |
| 代码质量 | P1 | `Dockerfile`（若存在） | 未做多阶段构建，镜像体积大 | 用 `python:3.11-slim` + multi-stage |
| 代码质量 | P1 | `.github/workflows/`（若存在） | 未配置 CI，PR 无自动测试 | 加 `pytest` + `tsc --noEmit` 步骤 |
| 代码质量 | P1 | `backend/app/main.py` | 未配置 `/health` 端点，K8s 无法做存活探针 | 加 `@app.get("/health")` 返回 `{"status": "ok"}` |
| 代码质量 | P1 | `backend/app/main.py` | 未配置 `lifespan`，资源初始化/清理散落 | 用 `@asynccontextmanager async def lifespan()` |
| 代码质量 | P1 | `backend/app/main.py` | 全局异常处理未捕获 `Exception`，500 错误堆栈泄露 | 加 `@app.exception_handler(Exception)` 返回通用错误 |
| 代码质量 | P2 | `backend/app/main.py` | 路由注册顺序无规律 | 按 `/api/auth` → `/api/sessions` → ... 分组 |
| 代码质量 | P2 | `backend/app/main.py` | 中间件顺序未优化，CORS 在最外层但日志中间件未加 | 加 `RequestLoggingMiddleware` |
| 代码质量 | P2 | `backend/app/main.py` | 未配置 `OpenAPI` 文档自定义 | 加 `app.openapi_schema` 覆盖 |
| 代码质量 | P2 | `backend/requirements.txt` | 依赖未锁版本，`fastapi>=0.100` 可能引入破坏性更新 | 用 `pip-compile` 生成 `requirements.lock` |
| 代码质量 | P2 | `backend/app/main.py` | 未配置 `prometheus` 指标暴露 | 加 `prometheus_fastapi_instrumentator` |
| 代码质量 | P2 | `backend/app/main.py` | 未配置 `sentry` 错误上报 | 加 `sentry_sdk.init` |
| 代码质量 | P2 | `backend/app/main.py` | 未配置 `gzip` 中间件 | 加 `GZipMiddleware(minimum_size=1000)` |
| 代码质量 | P2 | `backend/app/main.py` | 未配置 `TrustedHostMiddleware` | 加 `TrustedHostMiddleware` |
| 代码质量 | P2 | `backend/app/main.py` | 未配置 `HTTPSRedirectMiddleware` | 生产环境加 |
| 代码质量 | P3 | `backend/app/main.py` | `title`/`description` 硬编码 | 从配置读取 |
| 代码质量 | P3 | `backend/app/main.py` | 未配置 `docs_url`/`redoc_url` 隐藏 | 生产环境设 `None` |

---

## 跨线程共性问题

### 1. 鉴权形同虚设（P0）
`backend/app/api/auth.py` 的 `get_provider_key_by_session(token)` 定义但从未被任何路由 `Depends`，所有 `/api/sessions`、`/api/kb/*`、`/api/sandbox/*`、`/api/wiring`、`/api/devices` 路由均无鉴权，任意用户可访问任意数据。

**修复方案**：
```python
# backend/app/api/dependencies.py
async def current_user(token: str = Depends(oauth2_scheme)):
    user = get_provider_key_by_session(token)
    if not user:
        raise HTTPException(401, "Invalid token")
    return user

# 所有受保护路由
@router.get("/api/sessions")
async def list_sessions(user = Depends(current_user), db = Depends(get_db)):
    ...
```

### 2. 响应格式违反契约（P0）
`backend/app/api/crud.py` 所有路由返回裸对象，违反契约 `{success, data}` 格式。前端 `unwrapResponse` 用 hack `if (!("success" in json)) return json as T` 兼容，但掩盖了问题。

**修复方案**：统一用 `{"success": True, "data": ...}` 包装。

### 3. 死代码与 mock 残留（P1）
- `backend/app/api_router.py`：完整 mock 路由，未被导入
- `backend/app/kb/translation_pipeline.py`：完整类定义，从未调用
- `backend/app/api/routes.py`：`/api/build`、`/api/upload`、`/api/audit_pins` 为 stub
- `backend/main.py`：历史 mock `create_app()`（已修复为委托）

**修复方案**：删除或标注 `# TODO: implement`。

### 4. 日志缺失（P2）
全项目大量使用 `print`，未走 `logging` 模块，生产环境无结构化日志。

### 5. 类型校验缺失（P2）
`pydantic` 模型未覆盖所有请求体，多处用 `dict` 接收参数，运行时才崩。

### 6. 并发未加锁（P2）
`get_vector_store()` 单例、`wiring.json` 写入、串口访问均无锁，并发会崩。

---

## 修复优先级建议

### Phase 1：P0 阻断项（立即修复）
1. 接入真实鉴权 `Depends(current_user)` 到所有受保护路由
2. 统一 CRUD 响应格式为 `{success, data}`
3. 修复 `useSSE` 的 `externalController` 死代码
4. 修复多模态 RAG `images` 字段透传
5. 修复 MCP `handler.run` AttributeError
6. 修复 sandbox C/C++ 代码传入容器
7. 修复 `asyncio.run` 在已有事件循环中崩溃
8. 实现 `/api/audit_pins` 真实逻辑
9. 修复 `apiWS` 硬编码端口
10. 补全 `requirements.txt` 依赖
11. 删除 `api_router.py` 死代码
12. 修复 `.gitignore` 密钥路径
13. 修复原生 SQL 注入 + 创建 FTS 表

### Phase 2：P1 稳定性（一周内）
1. 全局异常处理 + 错误脱敏
2. 容器资源限制生效（CPU/mem/swap）
3. WebSocket 心跳 + 串口互斥
4. Docker daemon 不可用降级
5. SSE 超时 `onError` 回调
6. 文件上传大小前置校验
7. Fernet 密钥权限 `0o600`
8. 串口 port 白名单

### Phase 3：P2 代码质量（两周内）
1. 接入 Alembic 迁移
2. 全项目 `print` → `logging`
3. `pydantic` 模型覆盖所有请求
4. 并发加锁
5. 死代码清理
6. 文档与代码同步

### Phase 4：P3 优化（按需）
1. 可观测性（Prometheus/Sentry）
2. 性能优化（分页/缓存/虚拟化）
3. 用户体验（撤销重做/快捷键）

---

## 可扩展性评估

**新增一个 LLM Provider 需改文件数**：3
- `backend/app/llm/client.py`（加 provider 分支）
- `backend/app/api/auth.py`（加 provider key 存储）
- `frontend/src/components/SettingsPanel.tsx`（加 UI）

**新增一个工具需改文件数**：4
- `backend/app/agent/handler.py`（加工具实现）
- `backend/app/agent/tools/`（加工具类）
- `backend/app/api/routes.py`（加路由）
- `frontend/src/components/ActivityCard.tsx`（加图标匹配）

**新增一个页面需改文件数**：5
- `frontend/src/App.tsx`（加路由）
- `frontend/src/components/Sidebar.tsx`（加导航项）
- `frontend/src/store/useAppStore.ts`（加 view 状态）
- `frontend/src/pages/NewPage.tsx`（新建页面）
- `frontend/src/i18n/`（加翻译）

**结论**：可扩展性中等，主要痛点是配置散落、无注册表机制，新增功能需改 3-5 个文件。

---

## 虚假实现清单

| 功能 | 文件 | 现状 | 严重度 |
|------|------|------|--------|
| Bearer 鉴权 | `auth.py` | `get_provider_key_by_session` 从未调用 | P0 |
| 知识库翻译 | `translation_pipeline.py` | `TranslationPipeline` 从未实例化 | P0 |
| MCP 工具调用 | `agent/handler.py` | `handler.run` AttributeError | P0 |
| C/C++ 沙箱编译 | `sandbox/runner.py` | 代码未传入容器 | P0 |
| Arduino 编译 | `sandbox/runner.py` | 编译器路径不存在 | P0 |
| `/api/build` | `routes.py` | mock SSE，从未编译 | P0 |
| `/api/upload` | `routes.py` | mock SSE，从未烧录 | P0 |
| `/api/audit_pins` | `routes.py` | 硬编码 `{"conflicts": []}` | P0 |
| `/api/diagnose` 编译检查 | `routes.py` | 硬编码返回 PASS | P0 |
| ReAct Agent | `routes.py` L280 | TODO 标注，仍是普通 RAG | P2 |
| 分支会话 | `crud.py` | 字段已加，无业务逻辑 | P2 |
| DTR/RTS 控制 | `HardwareWorkbench.tsx` | UI 有，未调后端 | P2 |

---

## 边界降级评估

| 场景 | 现状 | 严重度 |
|------|------|--------|
| 后端断连 | 前端无重连机制，SSE 卡死 | P1 |
| LLM 401 | 后端未捕获，返回 500 | P1 |
| LLM 超时 | SSE 60s 超时但 `onError` 未触发 | P1 |
| Docker 不可用 | 后端崩，无降级 | P1 |
| 串口被占用 | 后端崩 500 | P1 |
| 并发上传知识库 | `get_vector_store` 重复初始化 | P2 |
| 并发写 wiring.json | 数据丢失 | P1 |
| 长对话 | `messages` 数组无上限 OOM | P3 |
| 大文件上传 | 50MB PDF 上传中途 OOM | P1 |
| 恶意 PDF | 递归栈溢出 | P1 |

---

**报告完成。建议从 Phase 1（P0 阻断项）开始修复，预计需处理 19 项。**
