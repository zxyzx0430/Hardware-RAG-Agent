# 03-knowledge 线程上下文

## 负责范围

- 文档上传、解析、切块、向量化入库（PDF/MD/TXT/XLSX/CSV/JSON/代码文件）
- 知识库列表查询、开关、删除
- ChromaDB 向量存储与检索
- RAG 检索与 Agent 工具对接（`search_docs`）
- Source 引用标注（SSE `source` 事件）
- 文档拖拽上传、异步索引轮询
- 前端 KnowledgePanel + useKnowledgeStore 状态管理

## 不负责

- 不负责聊天业务逻辑与 SSE 流式事件分发（02-chat）
- 不负责 LangGraph Agent 工具编排（05-agent）
- 不负责硬件烧录/串口/接线图（07-hardware）
- 不负责会话/消息/书签/Settings 持久化（04-session）

## 当前状态

> 开工前先读 TODO: [docs/todos/03-knowledge.md](../todos/03-knowledge.md)
> 完成记录见 [docs/completed.md](../completed.md) → 03-knowledge 知识库

### 已完成

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| `POST /api/kb/upload` | ✅ 已实现 | 在 `kb_routes.py`，支持 PDF/MD/TXT/XLSX/CSV/JSON/代码文件，异步向量化入库 |
| `GET /api/kb/list` | ✅ 已实现 | 在 `kb_routes.py`，从 `knowledge_docs` 表查询 |
| `POST /api/kb/delete` | ✅ 已实现 | 在 `kb_routes.py`，三路删除（DB + ChromaDB + 磁盘文件） |
| ChromaDB 向量存储 | ✅ 已实现 | `vector_store.py` — `ingest()` / `search()` / `delete_document()` / `get_collection_stats()` / `delete_collection()` |
| RAG 检索（聊天中） | ✅ 已实现 | `chat_routes.py` — 聊天时自动检索 ChromaDB，yield `source` SSE 事件 |
| 前端 KnowledgePanel | ✅ 已实现 | 上传/列表/开关/删除/拖拽/pollIndexingStatus |
| 前端 useKnowledgeStore | ✅ 已实现 | Zustand store，含 fetchItems / deleteItemWithAPI / 乐观更新 |
| 前端类型 KBItem | ✅ 已实现 | `types/kb.ts` |
| 共享函数 | ✅ 分离到 common.py | `get_db_ctx()` / `get_vector_store()` / `sse_event()` / `sanitize_error()` |
| 路由拆分 | ✅ 已迁移 | KB 路由从 `routes.py` → `kb_routes.py` |

### 已知问题

- `kb/list` 后端返回 `title`/`chunk_count`，前端消费 `filename`/`chunks`，靠 `fetchItems` 映射兼容
- `KnowledgeDoc` 模型缺少 `enabled` 字段，前端硬编码 `true`
- 无 SSE 通知机制通知前端索引完成（靠轮询 2s/120s）
- `kb_routes.py` 调用 `vector_store.index_document()`，但 `vector_store.py` 实际方法名为 `ingest()` — **潜在运行时 bug** ⚠️

### TODO 同步状态

`docs/todos/03-knowledge.md` 中所有 7 条待办标记为 [ ]，但实际多数已实现。建议下次开工同步 TODO：

| TODO 项 | 实际状态 |
| --- | --- |
| `/api/kb/upload` 分块后向量化入库 | ✅ 已完成 |
| 实现 `/api/kb/list` 后端 | ✅ 已完成 |
| 实现 `/api/kb/delete` 后端 | ✅ 已完成 |
| BM25 + Vector 混合检索 | ❌ 未实现（目前只有纯向量检索） |
| ChromaDB 集合管理 | ⚠️ 部分实现（有 stats/delete_collection，缺创建/列表 UI） |
| 前端知识库管理页面 | ✅ 已完成 |
| 回答标注来源 | ✅ 已完成（SSE source 事件） |

### 正在做

- (无)

### 阻塞

- (无)

## 接口契约

涉及 `docs/api-contract.md` 5.3 知识库管理：

| 接口 | 状态 | 前端入口 | 备注 |
| --- | --- | --- | --- |
| `POST /api/kb/upload` | `agreed` | `apiPost('kb/upload', formData)` | `kb_routes.py` |
| `GET /api/kb/list` | `agreed` | `apiGet('kb/list')` | 返回字段需对齐 |
| `POST /api/kb/delete` | `agreed` | `apiPost('kb/delete', { doc_id })` | 乐观删除 |

### 字段对齐差异

前端 `fetchItems` 期望字段：
- `doc_id` / `id` → `KBItem.id`
- `filename` / `name` → `KBItem.name`
- `chunks` / `chunk_count` → `KBItem.chunks`
- `status` → `KBItem.status`（indexing / indexed / error / ready）
- `enabled` → 后端 `KnowledgeDoc` 模型**缺少 `enabled` 字段**
- `doc_type` / `file_type` → `KBItem.docType`
- `updated_at` / `created_at` → `KBItem.updatedAt`

见 `useKnowledgeStore.ts` 中 `fetchItems` 字段映射逻辑。

## 关键文件

| 位置 | 文件 |
| --- | --- |
| 前端 UI | `frontend/src/components/knowledge/KnowledgePanel.tsx` |
| 前端 Store | `frontend/src/stores/useKnowledgeStore.ts` |
| 前端类型 | `frontend/src/types/kb.ts` |
| 后端路由 | `backend/app/api/kb_routes.py`（kb_upload / kb_list / kb_delete） |
| 后端共享 | `backend/app/api/common.py`（get_db_ctx / get_vector_store / sanitize_error） |
| 后端模型 | `backend/app/db/models.py` → `KnowledgeDoc` |
| RAG 向量存储 | `backend/src/rag/vector_store.py` |
| RAG 文档处理 | `backend/src/rag/document_processor.py` |
| RAG 文件解析 | `backend/src/rag/file_parsers.py` |
| RAG 流水线 | `backend/src/rag/pipeline.py` |
| RAG 文档加载 | `backend/src/rag/document_loader.py` |
| 前端聊天 RAG 整合 | `frontend/src/components/chat/ChatArea.tsx`（source 引用渲染） |
| 后端聊天 RAG 检索 | `backend/app/api/chat_routes.py`（search → SSE source 事件） |
| 后端聊天 store | `frontend/src/stores/useChatStore.ts`（source 引用存储） |
| TODO | `docs/todos/03-knowledge.md` |
| 完成记录 | `docs/completed.md` → 03-knowledge 知识库 |
| 接口契约 | `docs/api-contract.md` → 5.3 知识库管理 |
| 踩坑记录 | `docs/pitfalls.md` |

## 决策记录

| 日期 | 决策 |
| --- | --- |
| 2026-06-21 | routes.py 按域拆分为 kb_routes.py / chat_routes.py / hardware_routes.py / build_routes.py / tool_routes.py，共享函数集中到 common.py |

## 踩坑记录

关联 `docs/pitfalls.md`：

### 前后端字段名不匹配
- `kb/list` 后端返回 `title`/`chunk_count`，前端消费 `filename`/`chunks`
- 当前通过 `fetchItems` 映射做兼容，长期应让后端返回对齐字段或修改 api-contract
- `KnowledgeDoc` 模型缺少 `enabled` 字段，前端默认 `enabled: true`

### 异步索引状态同步
- 后端 `kb_upload` 返回后立即将状态设为 `indexing`，后台异步执行向量化
- 前端 `pollIndexingStatus` 每 2s 轮询 `kb/list`，120s 超时
- 当前没有 SSE 通知机制通知前端索引完成

### `kb_routes.py` 方法名不匹配
- `kb_routes.py` 第 83 行调用 `vector_store.index_document(processor)`，但 `vector_store.py` 中实际方法名为 `ingest()`
- 当前未触发是因为没有上下游完整的端到端测试覆盖
- 修复：将调用改为 `vector_store.ingest(processor)` 或用别名包装

## 下次开工先看

1. 确认 `docs/todos/03-knowledge.md` 前 2 项任务并开始执行
2. 阅读 `docs/api-contract.md` 5.3 确认接口状态
3. 检查 `docs/completed.md` 是否有新的 KB 相关完成记录
4. 检查 `docs/pitfalls.md` 是否有新增的相关踩坑
5. 运行后端 `pytest` 确认 KB 相关测试通过
6. 如果涉及修改 KB 路由，确认引用的是 `kb_routes.py` 而非旧 `routes.py`

> **小结**：03-knowledge 子系统核心链路（上传→解析→向量化→检索→前端展示+source 标注）已闭环。当前主要缺口是：(1) TODO 文件标记过期需要同步；(2) BM25+Vector 混合检索未实现；(3) `enabled` 字段在后端缺失；(4) `index_document` 方法名 bug；(5) 缺少端到端测试。下次开工建议从 TODO 同步 + 修 `index_document` bug 切入。
