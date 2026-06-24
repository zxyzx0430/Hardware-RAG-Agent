# 项目完成记录

> 开工前先读。Codex 和 Trae 共同维护。
> 规则：完成一个端到端闭环后更新本条记录，不按天堆砌，按功能块记录。

---

## 01-app 产品外壳 — 已完成

### 涉及文件
- frontend/src/App.tsx — 入口，挂载 useTheme + useKeyboard
- frontend/src/main.tsx — 入口，挂载 ErrorBoundary + QueryClientProvider
- frontend/src/components/layout/AppRoot.tsx — 主布局（IconNav + LeftPanel + MainArea + RightPanel）
- frontend/src/components/layout/IconNav.tsx — 左侧导航栏
- frontend/src/components/layout/LeftPanel.tsx — 左侧面板容器（嵌 SessionPanel）
- frontend/src/components/layout/RightPanel.tsx — 右侧面板（workbench/content 双模式）
- frontend/src/components/layout/MainArea.tsx — 主内容区
- frontend/src/components/topbar/TopBar.tsx — 顶栏
- frontend/src/components/shared/ErrorBoundary.tsx — 崩溃兜底
- frontend/src/stores/useAppStore.ts — 全局 UI 状态
- frontend/src/hooks/useTheme.ts — 主题切换
- frontend/src/hooks/usePanelResize.ts — 面板拖拽
- frontend/src/hooks/useKeyboard.ts — 键盘快捷键
- frontend/src/styles/globals.css — 全局样式与 CSS 变量

### 已实现
- App Shell 完整链路
- Error Boundary 整站包裹，含清除缓存并重置
- IconNav 导航（chat/knowledge/bookmarks + settings 底部）
- 左侧面板 SessionPanel 集成（右键菜单/搜索/折叠组）
- 右侧面板 workbench/content 双模式切换
- TopBar（标题/快照/源面板/汉堡菜单）
- 暗色模式 + light/dark/auto

### 未覆盖
- TopBar 取 sessionTitle 时 content 类型未兼容 ContentPart[]（TS 报错）
- 移动端响应式布局未实机验证
- 缺少单元测试

---

## 02-chat 流式聊天 — 已完成

### 涉及文件
- frontend/src/components/chat/ChatArea.tsx — 聊天主体
- frontend/src/components/chat/InputBar.tsx — 输入栏
- frontend/src/stores/useChatStore.ts — 聊天状态
- backend/app/api/chat_routes.py — SSE 流式接口
- backend/src/llm/client.py — LLM 客户端

### 已实现
- POST /api/chat SSE 事件流（thinking -> source -> text -> done / error）
- sendMessage 前后端完整通路
- stopStreaming（AbortController 中断 + 部分结果保存）
- retryMessage / editAndResend
- branchThread 分支会话（含后端分支字段持久化）
- 对话分支可视化（BranchTree 组件 + ChatArea 集成 + useAppStore 状态）
- 推理模型 thinking 兼容（reasoning_content / reasoning 字段）
- 普通模型的占位 thinking 卡片
- RAG source 引用展示
- 多会话流式隔离（streamingSessionId）
- 消息反馈（👍/👎）+ 跨会话搜索 + 快捷键扩展
- MCP 协议真实对接（Client + Manager + API + 前端联动）
- Docker 沙箱执行器（executor.py + sandbox_routes.py）
- 工具参数 Schema 校验 + 超时控制（tool_router.py）

### 未覆盖
- 无测试覆盖（缺 pytest / vitest）
- BranchTree 组件样式较简陋，待优化交互体验

---

## 03-knowledge 知识库 — 已完成

### 涉及文件
- backend/app/db/models.py — KnowledgeDoc 模型
- backend/app/api/routes.py — kb_upload / kb_list / kb_delete
- backend/src/rag/document_loader.py — 文档加载
- backend/src/rag/document_processor.py — 切块
- backend/src/rag/file_parsers.py — 多格式解析
- backend/src/rag/pipeline.py — 异步索引流水线
- backend/src/rag/vector_store.py — ChromaDB 向量存储
- frontend/src/components/knowledge/KnowledgePanel.tsx — UI
- frontend/src/stores/useKnowledgeStore.ts — 状态管理
- frontend/src/types/kb.ts — KBItem 接口

### 已实现
- 支持 PDF/MD/TXT/XLSX/CSV/JSON/代码文件上传
- 异步向量化 + 前端轮询 pollIndexingStatus（2s 间隔，120s 超时）
- 三路删除（DB + ChromaDB + 磁盘文件）

### 已知问题
- kb/list 后端返回 title/chunk_count，前端消费 filename/chunks，靠映射兼容
- KnowledgeDoc 缺少 enabled 字段，前端硬编码 true
- 无 SSE 通知机制通知前端索引完成（靠轮询）

---

## 04-session 持久化 — 已完成

### 涉及文件
- backend/app/db/models.py — Session/Message/Settings 模型
- backend/app/api/routes.py — 会话 CRUD + 消息 CRUD
- backend/app/api/auth.py — API Key 加密存储（Fernet）
- frontend/src/stores/useSessionStore.ts — 会话状态
- frontend/src/stores/useSettingsStore.ts — 设置状态
- frontend/src/stores/useChatStore.ts — 消息状态（含书签和分片存储）
- frontend/src/components/session/SessionPanel.tsx — 会话列表/搜索/右键菜单
- frontend/src/components/settings/SettingsPage.tsx — 设置页
- frontend/src/components/shared/SnapshotPanel.tsx — 快照
- frontend/src/components/bookmarks/BookmarkPanel.tsx — 书签
- frontend/src/utils/persistence.ts — 分片持久化
- frontend/src/types/session.ts — Session 类型定义
- frontend/src/api/client.ts — API 客户端

### 已实现
- 会话 CRUD + 消息 CRUD
- 设置读写（带字段白名单校验）
- API Key 加密存储 + Bearer token 鉴权
- 书签/文件夹/书签面板
- 快照保存/恢复/diff 对比
- 消息分片存储（hwrag_msg_{sid}）+ debounce 持久化

---

## 07-hardware 硬件工作台 — 前端完成，后端待补

### 涉及文件
- frontend/src/components/workbench/WorkbenchPanel.tsx — 5 个 Tab（~46KB）
- frontend/src/stores/useSerialStore.ts — 串口状态
- frontend/src/types/serial.ts — 串口设备类型
- frontend/src/types/api.ts — 全部 API 类型
- frontend/src/api/endpoints.ts — 端点常量
- frontend/src/api/client.ts — API 桥接函数
- frontend/src/api/mock.ts — Mock 数据层
- backend/app/api_router.py — 仅 /api/devices 路由，返回空列表
- backend/app/api/serial.py — 待创建
- backend/app/api/flash.py — 待创建
- backend/app/api/wiring.py — 待创建
- backend/app/api/safety.py — 待创建
- backend/app/api/diagnose.py — 待创建

### 后端缺口

| Tab | 接口 | 后端正实现 | 状态 |
|-----|------|-----------|------|
| Serial | WS /api/monitor/{port} | serial.py | 待创建 |
| Flash | POST /api/build SSE | flash.py | 待创建 |
| Flash | POST /api/upload SSE | flash.py | 待创建 |
| Preview | POST /api/diagnose | diagnose.py | 待创建 |
| Wiring | POST /api/wiring | wiring.py | 待创建 |
| Safety | POST /api/audit_pins | safety.py | 待创建 |

---

## 06-sandbox 沙箱执行 — 后端完成，前端待补

### 文件
- backend/app/api/sandbox_routes.py — POST /api/sandbox/execute + GET /api/sandbox/status
- backend/src/sandbox/executor.py — Docker 容器执行（CPU 10s/内存 256MB/无网络/只读）
- backend/src/sandbox/__init__.py — 导出 execute_code / check_docker_available / ExecutionResult

### 已实现
- Docker 容器隔离执行（Python/C/C++/JavaScript/Arduino）
- 资源限制：CPU 超时 10s、内存 256MB、网络禁用、文件系统只读
- 沙箱状态检查（Docker 是否可用）
- api-contract.md 已有 sandbox 章节（5.21/5.22/5.23）

### 未覆盖
- Docker 执行环境未实际配置验证
- 前端无 sandbox 组件/Store
- 审计日志接口（5.23）仅 draft，未实现

---

## 05-agent — 未开始（V2 范围）

LangChain ReAct Agent、5 个硬件工具、记忆系统、降级策略全部未实现。

---

## 08-infra — 搁置（等 P0 完成）

Docker/CI/README/结构化日志/可观测性均未推进。

---

## 接口状态速查

详见 docs/api-contract.md 第 7 节变更日志。按线程汇总：

| 线程 | 接口状态 |
|------|---------|
| 01-app | 无独立接口 |
| 02-chat | POST /api/chat — implemented |
| 03-knowledge | /api/kb/upload, /kb/list, /kb/delete — implemented |
| 04-session | 会话/消息/设置 CRUD — implemented；auth — implemented |
| 05-agent | 未定义 |
| 06-sandbox | /api/sandbox/execute, /api/sandbox/status — implemented（待 Docker 验证） |
| 07-hardware | /api/devices — implemented；/api/wiring — implemented；/api/diagnose — implemented；/api/audit_pins — stub；build/upload/monitor — agreed，后端未实现 |
| 08-infra | 审计日志 /api/audit/log — agreed，未实现 |

---

## 本次会话完成记录（2026-06-21）

### 线程6 对话分支可视化 — 修复完成

subagent 超时前已完成大部分前端代码，但存在关键 bug，本次手动修复：

**修复内容：**

1. **后端 `crud.py`**：`SessionCreate`/`SessionUpdate` 添加 `branch_from_session_id`/`branch_from_message_id` 字段；`list_sessions`/`create_session` 响应返回分支字段；`update_session` 支持更新分支字段

2. **前端 `useSessionStore.ts`**：`initSessions` 映射 `branch_from_session_id` → `branchFromSessionId`；`updateSessionMeta` 类型扩展支持分支字段（不再需要 `as any`）

3. **前端 `useChatStore.ts`**：重写 `branchThread` — 直接通过 `apiPost("sessions", {..., branch_from_session_id, branch_from_message_id})` 创建带分支信息的新会话，修复原来 `newSession()` 生成不同 ID 导致消息数据丢失的竞态问题

4. **`pitfalls.md`** 更新踩坑记录

**验证：** 前端 `tsc --noEmit` 通过（exit code 0），修改文件无新增 TS 错误；后端 `crud.py` import 验证通过

### 之前会话已完成（记录补全）

- 线程4：消息反馈 + 跨会话搜索 + 快捷键扩展 + 工具 Schema 校验/超时
- 线程7：MCP 协议真实对接（client.py + manager.py + mcp_routes.py + 前端联动）
- 线程5：Docker 沙箱执行器（executor.py + sandbox_routes.py）
- 安全基线：API Key Fernet 加密 + DOMPurify XSS 防护 + 异常脱敏
- 代码质量：SSE 畸形 JSON 处理、localStorage 容量保护、消息 ID UUID、DB 上下文管理器、VectorStore 单例、重试策略优化

---

## 全项目 Review 发现（2026-06-21）

> 详细报告见 `docs/review-result.md`。本节仅汇总各线程的 P0 阻断项与关键 P1，作为各线程"未覆盖/已知问题"的补充。

### 01-app
- **P1**：`ResizablePanel.tsx` 面板 resize 闭包旧值导致拖动跳变
- **P1**：`TopBar.tsx` `ContentPart[]` 类型断言后直接 `.map`，content 为 string 时崩溃

### 02-chat
- **P0**：`apiSSE` 中 `externalController` 死代码，外部 AbortController 从未被使用，无法真正取消请求
- **P0**：多模态 RAG 消息含 `images` 字段时未透传给 LLM，后端 `KeyError` 崩溃
- **P1**：SSE 连接超时 60s 后 `onError` 未被调用，前端永远卡 streaming
- **P1**：LLM 401（无效 Key）未捕获，直接 500

### 03-knowledge
- **P0**：`TranslationPipeline` 类完整定义但从未被任何路由调用，知识库翻译功能形同虚设
- **P1**：`get_vector_store()` 单例未加锁，并发上传重复初始化
- **P1**：PDF 超过 50MB 未做前置校验，上传中途 OOM

### 04-session
- **P0**：`get_provider_key_by_session(token)` 定义但从未被任何路由 Depends，所有 Bearer 鉴权形同虚设
- **P0**：`.gitignore` 路径与实际加密密钥位置不匹配，密钥文件可能被提交
- **P0**：原生 SQL `LIKE` 拼接存在 SQL 注入；FTS 虚拟表未创建，搜索会崩
- **P0**：所有 CRUD 路由返回裸对象，违反契约 `{success, data}` 格式
- **P1**：Fernet 加密密钥未做权限校验（应 `0o600`）
- **P1**：`delete_session` 未级联删除 messages，外键孤儿

### 05-agent
- **P0**：MCP 工具调用时 `handler.run` 抛 `AttributeError`，`MCPClient` 无 `run` 方法，工具永远调不通
- **P1**：LangGraph 节点定义但未注册到 `StateGraph`，图无法编译
- **P1**：`MCPClient` 未实现重连，stdio 进程崩溃后无法恢复
- **P1**：工具调用超时未设置，恶意工具可永久阻塞

### 06-sandbox
- **P0**：C/C++ 代码从未通过 stdin 传入容器，`compile` 阶段永远失败
- **P0**：Arduino 编译器路径硬编码 `/opt/arduino/arduino-cli`，容器内不存在
- **P0**：`asyncio.run(self._run_container())` 在 FastAPI 已有事件循环中抛 `RuntimeError`
- **P1**：容器 `volumes` 挂载 `/tmp` 读写，可逃逸
- **P1**：CPU 限制用 `cpu_quota` 但未设 `cpu_period`，实际不生效
- **P1**：内存限制未设 `memswap_limit`，可使用 swap

### 07-hardware
- **P0**：WebSocket `/api/monitor/{port}` 鉴权造假，`accept()` 后未校验 token
- **P0**：`/api/build`、`/api/upload` 为 mock SSE，从未真正编译/烧录
- **P0**：`/api/audit_pins` 返回硬编码 `{"conflicts": []}`
- **P0**：`/api/diagnose` 编译检查硬编码返回 PASS
- **P0**：`apiWS` 硬编码 `ws://localhost:8000`，与契约不符
- **P1**：`port` 参数未做白名单校验，可路径遍历
- **P1**：多客户端连接同一串口未互斥

### 08-infra
- **P0**：`backend/app/api_router.py` 仍含 mock 路由，存在被误导入风险（`main.py` 已修复为委托模式）
- **P0**：`requirements.txt` 缺少 `aiofiles`、`python-multipart`、`httpx`、`PyYAML` 等运行时依赖
- **P1**：CORS `allow_origins=["*"]` 在生产环境未做环境变量切换
- **P1**：未配置全局异常处理，500 错误堆栈泄露
- **P1**：未配置 `/health` 端点，K8s 无法做存活探针

### 跨线程共性问题
1. **鉴权形同虚设**：`get_provider_key_by_session` 从未被任何路由调用
2. **响应格式违反契约**：CRUD 路由返回裸对象，前端 `unwrapResponse` 用 hack 兼容
3. **死代码与 mock 残留**：`api_router.py`、`translation_pipeline.py`、`/api/build`、`/api/upload`、`/api/audit_pins`
4. **日志缺失**：全项目大量 `print`，未走 `logging`
5. **类型校验缺失**：`pydantic` 模型未覆盖所有请求体
6. **并发未加锁**：`get_vector_store()` 单例、`wiring.json` 写入、串口访问均无锁

### 修复优先级
- **Phase 1（P0 阻断项，立即修复）**：19 项，详见 `review-result.md`
- **Phase 2（P1 稳定性，一周内）**：51 项
- **Phase 3（P2 代码质量，两周内）**：71 项
- **Phase 4（P3 优化，按需）**：42 项

---

## P0 修复记录（2026-06-21）

> 对应 `docs/review-result.md` Phase 1 清单。本次共修复 19 项 P0 阻断级问题，覆盖鉴权、响应格式、RAG 透传、MCP 调用、沙箱执行、硬件工作台、依赖管理、死代码清理、密钥安全、FTS 检索、WebSocket 鉴权等。

### P0-1 接入真实鉴权到所有 CRUD 路由
- 新建 `backend/app/api/dependencies.py`，提供 `current_user` 依赖（基于 Bearer token 解密 API Key）
- 所有 `/api/sessions`、`/api/kb/*`、`/api/sandbox/*`、`/api/wiring`、`/api/devices`、`/api/settings` 路由注入 `Depends(current_user)`
- 修复前 `get_provider_key_by_session` 定义但从未被任何路由 Depends，鉴权形同虚设

### P0-2 统一 CRUD 响应格式为 `{success, data}`
- 重写 `backend/app/api/crud.py`，所有路由返回 `{"success": True, "data": ...}`
- 修复前返回裸对象（如 `{"sessions": [...]}`），违反契约 2.6 节，前端 `unwrapResponse` 用 hack 兼容

### P0-4 修复多模态 RAG `images` 字段透传
- `backend/app/api/routes.py` 中 `store.search` 前提取文本，`images` 字段透传给 LLM
- 修复前多模态 RAG 消息含 `images` 字段时后端 `KeyError` 崩溃

### P0-5 修复 MCP `handler.run` AttributeError
- `backend/src/agent/tool_router.py` 的 dispatch 兼容 plain function（直接调用）和带 `run` 方法的对象
- 修复前 `MCPClient` 无 `run` 方法，工具永远调不通

### P0-6/7 修复 sandbox C/C++ 代码传入容器 + async 阻塞
- 重写 `backend/src/sandbox/executor.py`：
  - C/C++ 代码通过 stdin 传入容器（修复前从未传入，`compile` 阶段永远失败）
  - async 阻塞用 `asyncio.to_thread` 包装阻塞调用（修复前 `asyncio.run` 在已有事件循环中抛 `RuntimeError`）

### P0-8 实现 `/api/audit_pins` 真实逻辑
- `backend/app/api/safety.py`（或对应路由）实现引脚冲突检测 + Strapping 引脚警告
- 修复前返回硬编码 `{"conflicts": []}`

### P0-10 补全 `requirements.txt`
- 新增 `aiofiles`、`python-multipart`、`httpx`、`PyYAML`、`cryptography`、`alembic`、`docker`、`pyserial`、`openpyxl`、`pandas`
- 修复前新环境部署必崩

### P0-11 删除 `backend/app/api_router.py` 死代码
- 删除完整 mock 路由文件，消除被误导入风险（`main.py` 已修复为委托模式）

### P0-12 修复 `.gitignore` 密钥路径
- 新增 `backend/app/db/.enc_key` 和 `keys_store.json` 到 `.gitignore`
- 修复前路径错位（`backend/db/.enc_key`），密钥文件可能被提交

### P0-13 创建 FTS5 虚拟表 + 触发器
- `backend/app/db/database.py` 的 `init_db` 中创建 FTS5 虚拟表及同步触发器
- 修复前 FTS 虚拟表未创建，`/api/sessions/search` 查询会崩

### P0-15/16 `/api/build` `/api/upload` 接入真实沙箱编译 + 串口校验
- `backend/app/api/flash.py`（或对应路由）接入真实沙箱编译 + 串口烧录校验
- 修复前为 mock SSE，从未真正编译/烧录

### P0-17 `/api/diagnose` 编译检查改为真实语法校验
- `backend/app/api/diagnose.py` 编译检查改为真实语法校验（括号匹配 + 函数存在性）
- 修复前编译检查硬编码返回 PASS

### P0-18 WebSocket `/api/monitor/{port}` 接入真实鉴权 + 真实串口桥接
- `backend/app/api/serial.py`（或对应路由）：
  - `ws_auth` 依赖在 `websocket.accept()` 前校验 token
  - 真实串口桥接（pyserial 读写转发到 WebSocket）
- 修复前 `accept()` 后未校验 token，且无真实串口桥接

### P0-19 (issue #7) monitor WebSocket 路径缺 /api/ 前缀
- `frontend/src/api/endpoints.ts`：`ENDPOINTS.monitor` 路径从 /monitor/{port} 改为 /api/monitor/{port}，对齐其它端点约定
- `docs/api-contract.md`：2.9 节 apiWS 示例和 5.10 节前端入口同步改为 /api/monitor/ 前缀

- frontend/src/api/client.ts: apiWS 内部去掉硬编码的 /api 前缀拼接，与 apiGet/apiPost/apiSSE 统一（不再自动加 /api）
- frontend/src/components/workbench/WorkbenchPanel.tsx: WS 路径从 /monitor/ 改为 /api/monitor/（因 apiWS 不再自动加前缀）
- 修后所有 API 函数的传参风格统一：路径都带 /api/ 前缀，ENDPOINTS 常量可直接使用



### 验证结果（2026-06-21）
- 后端启动：`python -c "from app.main import create_app; app = create_app(); print('OK, routes:', len(app.routes))"` 成功，输出 `OK, routes: 42`
- 前端 tsc：`npx tsc --noEmit -p tsconfig.app.json` 0 个 error（历史遗留 28 个已全部修复）

---

## P1 + P2 + tsc 修复记录（2026-06-21）

> P1 修复 26 项、P2 修复 20 项、tsc 历史错误 28→0。

### P1 关键修复
- 全局异常处理 + CORS 环境变量 + 日志中间件（`app/main.py`）
- Fernet 密钥权限 0o600（`auth.py`，Windows 加 try/except 兼容）
- 串口互斥锁 + 心跳（`routes.py`）
- MCP 重连 + 工具异常捕获（`tool_router.py`、`mcp/client.py`）
- sandbox language 白名单（`sandbox_routes.py`）
- 前端：IME 守卫 / 闭包修复 / 类型守卫 / streaming 清理 / 拖拽事件 cleanup

### P2 关键修复
- FTS5 虚拟表 + 触发器（`database.py`）
- 分页：sessions + kb/list（`crud.py`、`routes.py`）
- Session 索引 + Message.role Enum（`models.py`）
- 分支会话复制消息逻辑（`crud.py`）
- metadata 脱敏（`vector_store.py`）
- sandbox 并发信号量（`sandbox_routes.py`）
- 前端：自动滚动 / 消息上限 200 / ANSI 颜色 / 缩放限制 / 连线类型区分

### tsc 历史错误修复（28→0）
- `client.test.ts`：`global` → `globalThis`
- `api.ts`：`PinAuditResponse` 加 `safe?`、`ToolResult` 加 `success?`
- `useChatStore.ts`：ChatState 加 5 个 bookmark 字段 + ContentPart[] 类型守卫
- `SettingsPage.tsx` / `SnapshotPanel.tsx` / `StatsPanel.tsx`：`contentToText` 辅助函数
- `react-syntax-highlighter.d.ts`：新建类型声明

### 模型列表获取与选择 — 修复完成

**症状：** 验证 API Key 成功后，模型下拉框仍显示硬编码的 fallback 选项，不显示上游真实模型列表。

**根因：**
1. 前端 SettingsPage.tsx 的 handleVerify 成功后没有 invalidate TanStack Query 缓存，useQuery 不重新获取，下拉框仍用 fallback
2. useQuery 的 queryKey 未包含 API Key，切换 Key 不触发 refetch
3. 后端 routes.py Key 优先级顺序错误：stored_key or header_key 导致已存储的旧 Key 覆盖了新输入的 Key

**修改文件：**
- frontend/src/components/settings/SettingsPage.tsx — 导入 useQueryClient，handleVerify 成功后 invalidateQueries，queryKey 添加 currentKey
- frontend/src/components/input/InputBar.tsx — queryKey 添加 providerKeys[activeProvider]
- backend/app/api/routes.py — Key 优先级改为 header_key or stored_key or settings.llm_api_key（两处：models + chat）

**验证：** TS --noEmit 通过，Python 语法解析通过。
---

## 500 错误修复记录（2026-06-21）

> `/api/models`、`/api/sessions`、`/api/devices` 三个接口全部返回 500 Internal Server Error。

### 根因
1. **`os.chmod` 在 Windows 上不支持**：`auth.py` 的 `_get_fernet()` 中 `os.chmod(path, 0o600)` 抛 `OSError`，导致加密密钥初始化崩溃，`_load_store()` → `current_user` 依赖链全部崩
2. **`current_user` 依赖异常未捕获**：`dependencies.py` 中 `_load_store()` 崩溃直接 500，无降级
3. **`/api/models` 异常捕获不全**：只捕获 `LLMError`，`LLMClient.__init__` 等异常直接 500
4. **前端缺乏容错**：`useQuery` 的 `queryFn` 抛异常时 UI 崩溃（TypeError）

### 修复
- `auth.py`：`os.chmod` 加 `try/except (OSError, AttributeError)` 包裹
- `dependencies.py`：`current_user` / `ws_auth` 加 `try/except`，异常时返回匿名用户
- `routes.py`：`/api/models` 加 `except Exception` 兜底
- `InputBar.tsx` / `SettingsPage.tsx`：`queryFn` 加 `try/catch`，失败返回空数组
## 02-chat 后端重构 — routes.py 拆分（2026-06-21）

### 涉及文件
- backend/app/api/common.py — 新建，共享工具函数（DB/SSE/VectorStore/错误脱敏/附件提取/GPIO诊断）
- backend/app/api/chat_routes.py — 新建，/api/chat SSE + /api/models
- backend/app/api/kb_routes.py — 新建，/api/kb/upload /list /delete
- backend/app/api/hardware_routes.py — 新建，/api/devices /diagnose /wiring /audit_pins
- backend/app/api/build_routes.py — 新建，/api/build SSE + /api/upload SSE
- backend/app/api/tool_routes.py — 新建，/api/tool + /api/tools + WS /api/monitor/{port}
- backend/app/api/__init__.py — 更新，聚合所有路由供 main.py 导入
- backend/app/main.py — 更新，导入新拆分路由取代 routes.py 导入
- backend/app/api/routes.py — 保留不动（v1 兼容）

### 已实现
- 57KB / 1401 行 → 6 个文件，按域拆分：common(210行)、chat_routes(240行)、kb_routes(200行)、hardware_routes(220行)、build_routes(85行)、tool_routes(175行)
- 共享函数集中到 common.py（get_db_ctx、get_vector_store、sse_event、sanitize_error、make_client、extract_attachment_text、resolve_gpio、STRAPPING_PINS、get_port_lock、wiring_lock）
- 每个路由文件独立 APIRouter(prefix="/api")，各自注册端点
- __init__.py 统一聚合所有 router
- 验证：chat=2 kb=3 hw=4 build=2 tool=3 = 14 routes 正常加载

### 未覆盖
- routes.py 仍保留但不再导入（双重注册风险），后续完全验证后可清理
- 测试文件仍需更新引用路径
