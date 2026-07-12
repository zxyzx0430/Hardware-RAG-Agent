# 04-session 线程上下文

## 负责范围
- **做什么**：持久化/设置/会话管理 — 会话 CRUD、消息 CRUD、设置持久化、书签、快照、API Key 加密、beforeunload 兜底
- **不做什么**：不负责聊天业务逻辑、不负责知识库入库、不负责 LangGraph Agent 工具、不负责硬件工作台

## 当前状态
- **已完成**：
  - 后端 Session/Message/Settings 的 SQLAlchemy ORM 模型（models.py）
  - 后端 Session CRUD 路由（list/create/get/update/delete + 分页）
  - 后端 Message CRUD 路由（list/create）
  - 后端 Settings 读写路由（get/put）带白名单校验
  - 后端 API Key 加密存储路由（auth.py — Fernet 加密）
  - 后端 Feedback 反馈路由（feedback_routes.py）
  - 后端对话分支管理 — 分支字段 + 创建时复制消息（crud.py + models.py）
  - 前端 Session 类型定义（session.ts）
  - 前端 SessionStore（useSessionStore.ts）— 完整 CRUD + 本地持久化 + API 同步
  - 前端 SessionPanel 组件（会话列表、搜索、项目分组、右键菜单、置顶/重命名/删除/移至项目）
  - 前端 SettingsStore（useSettingsStore.ts）— 设置管理 + subscribe 自动持久化
  - 前端 SettingsPage 组件（Provider/模型/技能/MCP/外观设置）
  - 前端 SnapshotPanel 组件（快照保存/恢复/删除/diff对比）— 纯前端
  - 前端 BookmarkPanel 组件（书签收藏/文件夹/删除/移动/跳转）— 纯前端
  - 前端消息分片持久化（persistence.ts — hwrag_msg_{sid} 分片 + debounce 500ms）
  - 前端对话分支管理（useChatStore.ts branchThread — 跨 session 消息复制）
  - 路由冲突已解决：api_router.py 已删除，routes.py 已拆分为独立路由模块
- **部分完成**：
  - 书签后端：模型已建但无 CRUD 路由（纯前端 localStorage）
  - 快照后端：无模型无路由（纯前端 localStorage）
  - 设置后端同步：仅 API Key 变更时调后端，其他设置仅存 localStorage
- **未完成**：
  - beforeunload 事件兜底未 flush 的数据
  - 长期记忆系统（对话摘要 ConversationSummaryBufferMemory）
  - useChatStore.ts 拆分（message/session/bookmark/export）

## TODO 引用
- TODO 清单：`docs/todos/04-session.md`
- 当前任务（前 2 项）：
  1. [ ] useChatStore.ts 拆分（message/session/bookmark/export）
  2. [ ] SQLAlchemy + Alembic 会话持久化

## 接口契约
- 涉及 `docs/api-contract.md`：
  - `POST /api/sessions` (5.11) — agreed，已实现
  - `GET /api/sessions` (5.12) — agreed，已实现
  - `GET /api/sessions/{session_id}` (5.13) — agreed，已实现
  - `PUT /api/sessions/{session_id}` (5.14) — agreed，已实现
  - `DELETE /api/sessions/{session_id}` (5.15) — agreed，已实现
  - `POST /api/sessions/{session_id}/messages` (5.16) — agreed，已实现
  - `GET /api/sessions/{session_id}/messages` (5.17) — agreed，已实现
  - `GET /api/settings` (5.18) — agreed，已实现
  - `PUT /api/settings` (5.19) — agreed，已实现
  - 书签和快照接口未在 API contract 中定义

## 关键文件
- **前端**：
  - `frontend/src/stores/useSessionStore.ts` — Session 状态管理
  - `frontend/src/stores/useSettingsStore.ts` — 设置状态管理
  - `frontend/src/stores/useChatStore.ts` — 消息状态管理（含书签、分支、分片存储）
  - `frontend/src/components/session/SessionPanel.tsx` — 会话面板 UI
  - `frontend/src/components/settings/SettingsPage.tsx` — 设置页面 UI
  - `frontend/src/components/shared/SnapshotPanel.tsx` — 快照面板 UI
  - `frontend/src/components/bookmarks/BookmarkPanel.tsx` — 书签面板 UI
  - `frontend/src/utils/persistence.ts` — localStorage 分片持久化工具
  - `frontend/src/api/client.ts` — API 客户端
  - `frontend/src/types/session.ts` — Session 类型定义
  - `frontend/src/types/settings.ts` — Settings 类型定义
- **后端**：
  - `backend/app/db/models.py` — ORM 模型（Session、Message、Settings、Bookmark、BookmarkFolder、Feedback）
  - `backend/app/db/database.py` — 数据库初始化
  - `backend/app/api/crud.py` — CRUD 路由（sessions/messages/settings，含分支和分页）
  - `backend/app/api/auth.py` — API Key 加密存储路由（Fernet）
  - `backend/app/api/feedback_routes.py` — 反馈路由
  - `backend/app/api/dependencies.py` — 认证依赖
- **文档**：
  - `docs/thread-map.md` — 线程拆分备忘录
  - `docs/api-contract.md` — API 接口契约
  - `docs/pitfalls.md` — 踩坑记录
  - `docs/completed.md` — 项目完成记录
  - `docs/todos/04-session.md` — 当前 TODO 清单

## 决策记录
- 2026-06-19：会话数据从静态字段改为动态 createdAt (epoch ms) + getSessionGroup() 计算分组
- 2026-06-19：消息分片存储：每个 session 独立 localStorage key (hwrag_msg_{sid})
- 2026-06-19：SSE 回调捕获 requestSessionId，避免流式写入错误会话
- 2026-06-20：useSessionStore 统一使用 apiGet/apiPost/apiPut/apiDelete 封装函数
- 2026-06-21：消息 ID 改为 crypto.randomUUID() 替代 Date.now()
- 2026-06-21：api_router.py 删除，routes.py 拆分为独立路由模块

## 踩坑记录
- 关联 `docs/pitfalls.md`：
  - 2026-06-19 — React 版本功能缺失：selectSession 为空操作
  - 2026-06-19 — SessionPanel 右键菜单 CSS display:none 覆盖条件渲染
  - 2026-06-20 — 会话数据不真实：静态字段 + 多处硬编码
  - 2026-06-20 — SSE 回调写入错误会话 + retry/edit 丢失消息
  - 2026-06-21 — 消息 ID 用 Date.now() 不唯一
  - 2026-06-21 — useSessionStore 直接 fetch 不走统一封装
  - 2026-06-21 — sessionMessages 分片存储 debounce
  - 2026-06-21 — os.chmod 在 Windows 上崩溃导致 500

## 完成状态
- **持久化**：✅ 后端 SQLAlchemy + 前端 localStorage 分片存储 + debounce 定时 flush
- **设置**：✅ 后端 GET/PUT /api/settings + 前端 auto-persist subscribe
  - ⚠️ 后端同步不完整：仅 API Key 变更时主动推后端
- **会话**：✅ 完整 CRUD + 分支管理 + 项目分组 + 置顶/搜索/右键菜单
- **书签**：⚠️ 纯前端 localStorage，后端模型已建但无路由
- **快照**：⚠️ 纯前端 localStorage，后端无模型无路由
- **beforeunload**: ❌ 未实现
- **长期记忆**: ❌ 未实现

## 下次开工先看
1. 读 `docs/todos/04-session.md` 前 2 项，开始干活
2. 优先考虑书签后端路由和 beforeunload 兜底
3. 注意：`crud.py` 响应格式不是标准 { success, data }，如需统一格式需改
4. 涉及跨线程代码（如 useChatStore.ts）注意通知相关线程
