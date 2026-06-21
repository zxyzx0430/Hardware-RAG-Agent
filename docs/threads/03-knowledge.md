# 03-knowledge 线程上下文

## 负责范围

- 文档上传、解析、切块、向量化入库（PDF/MD/TXT/XLSX/CSV/JSON/代码文件）
- 知识库列表查询、开关、删除
- ChromaDB 向量存储与检索
- RAG 检索与 Agent 工具对接（`search_hardware_kb`）
- Source 引用标注（SSE `source` 事件）
- 文档拖拽上传、异步索引轮询
- 前端 KnowledgePanel + useKnowledgeStore 状态管理

## 不负责

- 不负责聊天业务逻辑与 SSE 流式事件分发（02-chat）
- 不负责 LangGraph Agent 工具编排（05-agent）
- 不负责硬件烧录/串口/接线图（07-hardware）
- 不负责会话/消息/书签/Settings 持久化（04-session）

## 当前状态

### 已完成

- [x] `POST /api/kb/upload` — 后端实现，支持多种格式、校验、异步向量化
- [x] `GET /api/kb/list` — 后端实现，从 `knowledge_docs` 表查询
- [x] `POST /api/kb/delete` — 三路删除（DB + ChromaDB + 磁盘文件）
- [x] `backend/src/rag/` 全套模块：document_loader / document_processor / file_parsers / pipeline / vector_store
- [x] `frontend/src/components/knowledge/KnowledgePanel.tsx` — 完整 UI
- [x] `frontend/src/stores/useKnowledgeStore.ts` — Zustand store（fetchItems / deleteItemWithAPI / 乐观更新）
- [x] `frontend/src/types/kb.ts` — KBItem 接口
- [x] 前端上传后轮询机制 `pollIndexingStatus`
- [x] 后端 `get_vector_store()` 单例模式

### 正在做

- (无 — 线程首次初始化)

### 阻塞

- (无)

## 接口契约

涉及 `docs/api-contract.md` 5.3 知识库管理：

| 接口 | 状态 | 前端入口 | 备注 |
| --- | --- | --- | --- |
| `POST /api/kb/upload` | `agreed` | `apiPost('kb/upload', formData)` | 支持 pdf/md/txt/xlsx/csv/json/代码文件 |
| `GET /api/kb/list` | `agreed` | `apiGet('kb/list')` | 需对齐返回字段（见踩坑记录） |
| `POST /api/kb/delete` | `agreed` | `apiPost('kb/delete', { doc_id })` | 乐观删除 |

### 字段对齐差异

前端 `fetchItems` 期望字段：
- `doc_id` / `id` → `KBItem.id`
- `filename` / `name` → `KBItem.name`
- `chunks` / `chunk_count` → `KBItem.chunks`
- `status` → `KBItem.status`（indexing / indexed / error）
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
| 后端路由 | `backend/app/api/routes.py`（kb_upload / kb_list / kb_delete） |
| 后端模型 | `backend/app/db/models.py` → `KnowledgeDoc` |
| RAG 文档加载 | `backend/src/rag/document_loader.py` |
| RAG 文档处理 | `backend/src/rag/document_processor.py` |
| RAG 文件解析 | `backend/src/rag/file_parsers.py` |
| RAG 流水线 | `backend/src/rag/pipeline.py` |
| RAG 向量存储 | `backend/src/rag/vector_store.py` |
| 接口契约 | `docs/api-contract.md` → 5.3 知识库管理 |
| 踩坑记录 | `docs/pitfalls.md` |

## 决策记录

- (暂无)

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

## 下次开工先看

1. 确认当前是否有未完成的 knowledge 相关 task
2. 阅读 `docs/api-contract.md` 5.3 确认接口状态
3. 检查 `KnowledgeDoc` 模型是否需要补充 `enabled` 字段
4. 检查 `docs/pitfalls.md` 是否有新增的相关踩坑
5. 运行后端 `pytest` 确认 KB 相关测试通过
