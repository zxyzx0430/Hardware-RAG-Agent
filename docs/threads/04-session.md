# 04-session 线程上下文

## 负责范围
- **做什么**：会话(Session) CRUD、消息(Message) CRUD、设置(Settings)读写、API Key 加密存储、书签(Bookmark)、快照(Snapshot)、会话持久化
- **不做什么**：不负责聊天业务逻辑、不负责知识库入库、不负责 LangGraph Agent 工具、不负责硬件工作台

## 当前状态
- **已完成**：
  - 后端 Session/Message/Settings 的数据库模型（models.py）
  - 后端 Session CRUD 路由（list/create/get/update/delete）
  - 后端 Message CRUD 路由（list/create）
  - 后端 Settings 读写路由（get/put）带白名单校验
  - 后端 API Key 加密存储路由（auth.py）
  - 后端 Feedback 反馈路由（feedback_routes.py）
  - 前端 Session 类型定义（session.ts）
  - 前端 SessionStore（useSessionStore.ts）— 完整 CRUD + 本地持久化
  - 前端 SessionPanel 组件（会话列表、搜索、项目分组、右键菜单、置顶/重命名/删除/移至项目）
  - 前端 SettingsStore（useSettingsStore.ts）— 设置管理 + 自动持久化
  - 前端 SettingsPage 组件（Provider/模型/技能/MCP/外观设置）
  - 前端 SnapshotPanel 组件（快照保存/恢复/删除/diff对比）
  - 前端 BookmarkPanel 组件（书签收藏/文件夹/删除/移动/跳转）
  - 前端 Persistence 工具（分片存储消息 + localStorage 持久化）
- **正在做**：等待任务分配
- **阻塞**：无

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
  - `frontend/src/stores/useChatStore.ts` — 消息状态管理（含书签和分片存储）
  - `frontend/src/components/session/SessionPanel.tsx` — 会话面板 UI
  - `frontend/src/components/settings/SettingsPage.tsx` — 设置页面 UI
  - `frontend/src/components/shared/SnapshotPanel.tsx` — 快照面板 UI
  - `frontend/src/components/bookmarks/BookmarkPanel.tsx` — 书签面板 UI
  - `frontend/src/utils/persistence.ts` — localStorage 持久化工具
  - `frontend/src/api/client.ts` — API 客户端
  - `frontend/src/types/session.ts` — Session 类型定义
  - `frontend/src/types/settings.ts` — Settings 类型定义
- **后端**：
  - `backend/app/db/models.py` — ORM 模型（Session、Message、Settings、Bookmark、BookmarkFolder、Feedback）
  - `backend/app/db/database.py` — 数据库初始化
  - `backend/app/api/crud.py` — CRUD 路由（sessions/messages/settings）
  - `backend/app/api/auth.py` — API Key 加密存储路由
  - `backend/app/api/feedback_routes.py` — 反馈路由
  - `backend/app/api_router.py` — 旧的 mock 路由（含 mock sessions/settings）
  - `backend/app/main.py` — FastAPI 入口
- **文档**：
  - `docs/thread-map.md` — 线程拆分备忘录
  - `docs/api-contract.md` — API 接口契约
  - `docs/pitfalls.md` — 踩坑记录
  - `docs/feature-gap-analysis.md` — 功能完整性评估

## 决策记录
- 2026-06-19: 会话数据从静态字段改为动态 createdAt (epoch ms) + getSessionGroup() 计算分组
- 2026-06-19: 消息分片存储：每个 session 独立 localStorage key (hwrag_msg_{sid})
- 2026-06-19: SSE 回调捕获 requestSessionId，避免流式写入错误会话
- 2026-06-20: useSessionStore 统一使用 apiGet/apiPost/apiPut/apiDelete 封装函数
- 2026-06-21: 消息 ID 改为 crypto.randomUUID() 替代 Date.now()

## 踩坑记录
- 关联 `docs/pitfalls.md`：
  - 2026-06-19 - React 版本功能缺失：selectSession 为空操作
  - 2026-06-19 - SessionPanel 右键菜单 CSS display:none 覆盖条件渲染
  - 2026-06-20 - 会话数据不真实：静态字段 + 多处硬编码
  - 2026-06-20 - SSE 回调写入错误会话 + retry/edit 丢失消息
  - 2026-06-21 - 消息 ID 用 Date.now() 不唯一
  - 2026-06-21 - useSessionStore 直接 fetch 不走统一封装
  - 2026-06-21 - sessionMessages 分片存储 debounce

## 已知问题/待办
1. **路由冲突**：`backend/app/api_router.py` 的 mock `/api/sessions` 和 `backend/app/api/crud.py` 的 `db_router` 同时被挂载到 `/api` 前缀，mock 路由可能覆盖真实 CRUD 路由
2. **响应格式不一致**：`crud.py` 直接返回 `{"sessions": [...]}` 而非标准 `{"success": true, "data": {...}}` 格式
3. **书签纯前端存储**：Bookmark 数据完全在 localStorage 中，没有后端 API 同步（Bookmark 表已创建但无路由）
4. **快照纯前端存储**：Snapshot 数据完全在 localStorage 中，没有后端 API 同步
5. **Settings 前后端同步不完整**：前端 useSettingsStore 有自动 persist，但后端 `/api/settings` 仅在 API Key 变更时调用，其他设置变更仅存 localStorage

## 下次开工先看
1. 优先解决路由冲突问题（api_router.py vs crud.py）
2. 统一后端响应格式为标准 { success, data } 格式
3. 检查前端 API 调用是否与后端接口实际返回格式一致
