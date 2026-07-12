# RAG 系统报告

> 本文档汇总 Hardware RAG Agent 知识库子系统的完成度、生命周期、全流程、优缺点与已知问题。
> 最后更新：2026-06-25

---

## 1. 完成度报告

### 1.1 后端（backend/）

| 模块 | 状态 | 说明 |
|------|------|------|
| `kb_manager.py` — 多 KB 管理 | ✅ 完成 | 创建/删除/列表/详情/重命名/配置更新均已实现 |
| `vector_store.py` — ChromaDB 向量存储 | ✅ 完成 | 支持单 KB 向量检索 + 按 doc_id 取 chunks |
| `chunking/` — 分块器 | ✅ 完成 | hybrid（规则）+ agent（LLM 辅助）两种策略 |
| `document_processor.py` — Docling PDF 解析 | ✅ 完成 | 提取文本 + 页码 + 章节标题 |
| `file_parsers.py` — Excel/CSV/JSON 解析 | ✅ 完成 | 表格类文件单独解析器 |
| BM25 检索 + RRF 融合 | ✅ 完成 | `BM25Index` + `rrf_fusion()` |
| 来源归因（13 字段透传） | ✅ 完成 | page_start/page_end/section_title/source_url/category/chunk_method/kb_id/kb_name 全部透传 |
| KB 多选检索 | ✅ 完成 | `search_all_enabled(query, k, kb_ids)` 支持按 KB ID 列表过滤 |
| 重复上传检测 | ✅ 完成 | 同 KB 同名文件返回 `DUPLICATE_FILE` |
| 删除一致性（先向量后记录） | ✅ 完成 | 向量删除失败保留 DB 记录以便重试 |
| Chunk 查看 API | ✅ 完成 | `GET /api/kb/documents/{doc_id}/chunks` |
| KB 配置更新 API | ✅ 完成 | `PATCH /api/kb/collections/{kb_id}/config`（含缓存失效） |
| KB 重命名 API | ✅ 完成 | `PATCH /api/kb/collections/{kb_id}/rename` |
| 内置 KB 自动创建 | ✅ 完成 | `ensure_builtin_kb()` 启动时检查 |
| 导入/导出 | ✅ 完成 | JSON 格式跨 KB 迁移 |

### 1.2 前端（frontend/）

| 模块 | 状态 | 说明 |
|------|------|------|
| 知识库面板（KnowledgePanel） | ✅ 完成 | 文档列表/上传/删除/预览 |
| KB 集合管理（KbCollectionManager） | ✅ 完成 | 创建/删除/启用/重命名 |
| Chunk 查看器（RightPanel.ChunkViewer） | ✅ 完成 | 展开/折叠每个 chunk，显示页码/章节/方法 |
| 来源芯片（ChatArea） | ✅ 完成 | 显示文件名 + 章节标题 + 页码范围 + 归一化分数 |
| 来源详情（RightPanel） | ✅ 完成 | 完整 13 字段 + KB 徽章 + 分类 |
| KB 多选（RagSettingsPanel） | ✅ 完成 | Checkbox 列表，空选 = 检索全部 |
| KB 配置编辑 UI | ❌ **缺失** | 后端 API + store 方法已就绪，无前端表单 |
| Embedding 模型获取 | ✅ 完成 | `fetchEmbeddingModels(base_url, api_key)` |

### 1.3 数据库

| 表 | 状态 | 说明 |
|----|------|------|
| `KnowledgeBase` | ✅ 完成 | 多 KB 元数据 + 加密 API Key |
| `KnowledgeDoc` | ✅ 完成 | 文档记录 + 状态 + chunk_count + coverage_json |
| `TokenUsage` | ✅ 完成 | Token 用量记录 |

---

## 2. 生命周期报告

### 2.1 文档上传 → 入库流程

```
用户上传文件
   │
   ▼
[1] POST /api/kb/upload (kb_id, chunk_method, chunk_size)
   │
   ├─ 校验文件扩展名 / magic bytes / 大小 (≤50MB)
   ├─ 校验 KB 是否存在
   ├─ 重复检测：同 KB 同名文件 → 返回 DUPLICATE_FILE
   ├─ 写 KnowledgeDoc 记录 (status="indexing")
   ├─ 保存文件到 data/uploads/{doc_id}{ext}
   │
   ▼
[2] 后台任务 _index_document() 异步执行
   │
   ├─ 解析文件（PDF→Docling / Excel→openpyxl / CSV/JSON/文本）
   │   └─ PDF 额外用 PyMuPDF 取 total_pages
   ├─ 获取 chunker（hybrid 或 agent）
   ├─ 执行分块：chunker.chunk(text, metadata, file_path, total_pages)
   │   └─ 每块带 metadata: doc_id, title, chunk_index, page_start, page_end, section_title
   ├─ 入库：kb_manager.ingest_chunks(kb_id, chunks, doc_id)
   │   ├─ 获取 store（_get_store）
   │   ├─ 若 store.embeddings is None → 跳过向量化，返回 0
   │   └─ 否则 store.add_documents(chunks) → 写入 ChromaDB
   ├─ 校验页码覆盖：verify_page_coverage(chunks, total_pages)
   └─ 更新 KnowledgeDoc:
       ├─ ingested > 0 → status="indexed", chunk_count=len(chunks), coverage_json
       └─ ingested == 0 → status="indexed", chunk_count=len(chunks),
                              error_message="未配置 Embedding 模型，已分块但未向量化"
```

### 2.2 文档更新流程

**当前实现：** 无自动更新。用户需手动删除旧文档再上传新版本。

**推荐流程：**
1. 用户上传同名文件 → 后端返回 `DUPLICATE_FILE`，提示"请先删除旧文件"
2. 用户删除旧文档（见 2.3）
3. 用户重新上传新版本

**未来可优化：** 增加 `PUT /api/kb/documents/{doc_id}/replace` 接口，原子性替换（删旧向量 + 入库新向量）。

### 2.3 文档删除流程

```
POST /api/kb/delete {doc_id}
   │
   ├─ 查询 KnowledgeDoc 获取 kb_id + actual_doc_id
   │
   ▼
[Step 1] 删除 ChromaDB 向量（先删向量）
   │   store = kb_manager._get_store(kb)
   │   store.db.delete(where={"doc_id": actual_doc_id})
   │   ├─ 成功 → 继续
   │   └─ 失败 → 返回错误，保留 DB 记录（可重试）
   │
   ▼
[Step 2] 删除上传的物理文件
   │   save_path.unlink() (失败仅 warning，不阻断)
   │
   ▼
[Step 3] 删除 KnowledgeDoc DB 记录（最后删记录）
   │   db.delete(record); db.commit()
   │
   ▼
返回 {success: true, deleted_chunks: N}
```

**一致性保证：** 向量删除成功后才会删 DB 记录；向量删除失败则 DB 记录保留，用户可重试删除。

### 2.4 KB 删除流程

```
DELETE /api/kb/collections/{kb_id}
   │
   ├─ 删除该 KB 下所有 KnowledgeDoc 记录
   ├─ 删除 ChromaDB collection
   ├─ 删除 BM25 索引文件
   ├─ 删除 KnowledgeBase 记录
   └─ 清理内存缓存 (_stores.pop, _bm25_indices.pop)
```

---

## 3. 全流程报告（端到端检索）

### 3.1 用户提问 → 回答生成

```
用户在聊天界面输入问题
   │
   ▼
[1] 前端 InputBar.sendMessage()
   │   ├─ 构建 ChatRequest {messages, model, top_k, kb_ids, system_prompt}
   │   ├─ kb_ids 来自 useSettingsStore.selectedKbIds（空=检索全部已启用 KB）
   │   └─ POST /api/chat (SSE)
   │
   ▼
[2] 后端 chat_routes.py /chat
   │   ├─ 解析 messages + system_prompt（None 时用默认）
   │   ├─ 提取最后一条 user message 作为 RAG query
   │   ├─ kb_manager.search_all_enabled(query, k=top_k, kb_ids=kb_ids)
   │   │   ├─ 对每个启用的 KB（或 kb_ids 列表）：
   │   │   │   ├─ 向量检索：store.similarity_search(query, k) → SearchResult[]
   │   │   │   ├─ BM25 检索：bm25_index.search(query, k) → SearchResult[]
   │   │   │   └─ RRF 融合：rrf_fusion(vector_results, bm25_results)
   │   │   ├─ 合并所有 KB 的融合结果
   │   │   └─ 返回 top_k 结果
   │   │
   │   ├─ 发送 SSE source 事件（每个结果一条，13 字段）
   │   │   {id, title, doc, page, chunk_index, page_start, page_end,
   │   │    section_title, source_url, category, chunk_method, score, excerpt,
   │   │    kb_id, kb_name}
   │   │
   │   ├─ 构建 context（top_k chunks 的内容拼接）
   │   ├─ 调用 LLM：client.chat_stream(messages_with_context, model, ...)
   │   │
   │   ├─ 流式转发：
   │   │   ├─ thinking 事件（source: "rag" / "llm" / "reasoning"）
   │   │   ├─ text 事件（逐 token）
   │   │   └─ tool 事件（如有）
   │   │
   │   ├─ 流结束：发送 done 事件 + activity 摘要
   └─ 写入 TokenUsage 记录（真实 usage 或 fallback 估算）
   │
   ▼
[3] 前端渲染
   │   ├─ ThinkingStep（黄色💡/紫色👁 推理）
   │   ├─ ActivityBlock（工具执行节点）
   │   ├─ 文本流式渲染（Markdown + 代码高亮）
   │   ├─ 来源芯片（文件名 · 章节 · 页X-Y · 分数%）
   │   └─ 右侧面板来源详情（点击芯片展开）
```

### 3.2 分数归一化

- 后端返回 RRF 原始分数（约 0.01-0.03 量级）
- 前端按当前结果集 max 归一化到 0-100%：
  ```ts
  const maxScore = Math.max(...msg.sources.map(s => s.score || 0), 0.001);
  const normalizedScore = Math.round((src.score / maxScore) * 100);
  ```

---

## 4. 优缺点报告

### 4.1 优点

1. **多 KB 架构** — 每个知识库独立 embedding 模型/分块策略，支持硬件手册、代码片段、数据手册分库管理
2. **混合检索** — 向量（语义）+ BM25（关键词）+ RRF 融合，兼顾召回率与精确率
3. **完整来源归因** — 13 字段透传，用户可验证每个引用的真实出处（页码/章节/文件名/KB）
4. **分块策略可选** — hybrid（规则，快）+ agent（LLM 辅助，语义完整）
5. **PDF 页码保留** — Docling 解析保留页码与章节标题，溯源精确
6. **删除一致性** — 先删向量后删记录，失败可重试
7. **重复上传检测** — 避免同一文件多次入库污染检索
8. **加密存储** — API Key 加密入库，不明文持久化
9. **跨 KB 迁移** — 导入/导出 JSON 格式，便于环境迁移

### 4.2 缺点与已知问题

#### P0 — 阻断使用

1. **Builtin KB 缺失 Embedding 配置** ⚠️ **本次修复重点**
   - 现象：用户在前端配置好 embedding 模型后，上传文件仍显示"未配置 Embedding 模型，已分块但未向量化"
   - 根因：`ensure_builtin_kb()` 创建 builtin KB 时只设 `embedding_model`，**未设 `embedding_api_key_encrypted` 与 `embedding_base_url`**；前端全局 embedding 设置存于 localStorage，从未同步到后端 KB 记录
   - 触发链：`HardwareVectorStore.__init__` 中 `api_key = embedding_api_key or settings.embedding_api_key`，builtin KB 的 `embedding_api_key_encrypted` 为 None → `api_key` 为空 → `self.embeddings = None` → `ingest_chunks` 返回 0
   - 修复状态：
     - ✅ 后端 `update_kb_config()` 方法已加（含缓存失效 `_stores.pop`）
     - ✅ `PATCH /api/kb/collections/{kb_id}/config` 接口已加
     - ✅ 前端 `updateKbConfig()` store 方法已加
     - ❌ **前端配置编辑 UI 表单未实现**（用户无法在界面上修改 KB 的 embedding 配置）

2. **导入文档 doc_id 不匹配**
   - 现象：通过 `/api/kb/{kb_id}/import` 导入的文档无法通过 `/api/kb/delete` 删除
   - 根因：导入时使用源 KB 的 doc_id，但新 KB 中可能已存在同 doc_id，或删除时按当前 KB 查不到该 doc_id
   - 修复状态：❌ 未修复

#### P1 — 影响体验

3. **服务重启后 status="indexing" 永久卡住**
   - 现象：上传中后端崩溃，重启后该文档永远显示"索引中"
   - 修复状态：❌ 缺少启动时清扫器（startup sweeper）

4. **BM25 索引不持久化**
   - 现象：`_bm25_stale` 集合在重启后丢失，BM25 索引需重建
   - 修复状态：❌ 部分持久化（`save()`/`load()` 已实现，但未自动调用）

5. **KB 配置更新后已有文档未重新向量化**
   - 现象：用户更新 KB 的 embedding 模型后，旧 chunks 仍用旧模型向量
   - 修复状态：❌ 缺少"重新向量化"按钮

#### P2 — 优化项

6. **按页向量化 vs 按块向量化**
   - 当前：按块（chunk）向量化，每块约 1000 字符
   - 评估：硬件手册场景下，按页向量化（每页一个向量）会导致长页信息密度过高，短页信息不足。**当前按块方案更适合**
   - GraphRAG：适合"芯片 X 的引脚 Y 连接到外设 Z"这类关系查询，但构建成本高，当前规模（< 1000 文档）不必要
   - 建议：维持当前 hybrid chunk + 向量 + BM25 方案，待文档量 > 5000 时再评估 GraphRAG

7. **Chunk 预览无高亮**
   - 当前：ChunkViewer 显示原文，无查询词高亮
   - 建议：未来可加 `<mark>` 高亮匹配词

8. **无文档版本管理**
   - 当前：删除 + 重新上传，无版本历史
   - 建议：未来可加 `version` 字段与 `previous_doc_id` 关联

---

## 5. Embedding 配置问题 — 详细根因分析

### 5.1 数据流

```
前端 RagSettingsPanel
   │  用户输入 embedding_model / base_url / api_key
   │  存入 localStorage（useSettingsStore）
   │
   ✗ 从未发送到后端
   │
后端 ensure_builtin_kb()
   ├─ 创建 KnowledgeBase 记录
   ├─ embedding_model = "text-embedding-3-small"
   ├─ embedding_api_key_encrypted = None  ← 问题根源
   └─ embedding_base_url = None           ← 问题根源
   │
后端 _get_store(kb)
   ├─ 解密 embedding_api_key_encrypted → None
   ├─ HardwareVectorStore(api_key=None, ...)
   └─ self.embeddings = None  ← 不向量化
   │
后端 ingest_chunks()
   ├─ store.embeddings is None
   └─ return 0  ← 不入库
   │
后端 _update_doc_status()
   └─ error_message = "未配置 Embedding 模型，已分块但未向量化"
```

### 5.2 修复方案

**已实施：**
- `KnowledgeBaseManager.update_kb_config(kb_id, embedding_model, embedding_base_url, embedding_api_key, ...)` — 更新 KB 配置，加密 API Key，失效 store 缓存（`_stores.pop(kb_id)`），标记 BM25 为 stale
- `PATCH /api/kb/collections/{kb_id}/config` — RESTful 接口，接收 `UpdateKBConfigRequest`
- `useKnowledgeStore.updateKbConfig(kbId, config)` — 前端 store 方法，调用 API 后刷新 collections

**待实施：**
- 前端 KB 管理界面加"配置"按钮，弹出表单：
  - Embedding 模型名（如 `text-embedding-3-small`）
  - Base URL（如 `https://api.openai.com/v1`）
  - API Key（password 输入框）
  - "测试连接"按钮（调用 `fetchEmbeddingModels` 验证）
  - "保存"按钮（调用 `updateKbConfig`）

### 5.3 分块是否真的执行？

**结论：✅ 是，分块真实执行。**

验证依据：
1. `kb_upload` 接口的 `_index_document()` 后台任务中，`chunker.chunk()` 在 `ingest_chunks()` 之前调用
2. 即使 `ingest_chunks` 返回 0（embedding 未配置），`chunk_count = len(chunks)` 仍写入 DB
3. DB 中 `KnowledgeDoc.chunk_count > 0` 即证明分块执行成功
4. Chunk 内容可通过 `GET /api/kb/documents/{doc_id}/chunks` 查看（从 ChromaDB 读取）
5. **注意：** 若 embedding 未配置，chunks 仅在内存中生成，**未持久化到 ChromaDB**。`chunk_count` 来自 DB 记录，但 ChromaDB 中无对应向量。配置 embedding 后需删除重新上传

**潜在问题：** 若 embedding 未配置，chunks 生成后未持久化，用户配置 embedding 后无法补向量化已有 chunks —— 必须删除重新上传。未来可加"重新向量化"功能。

---

## 6. 待办事项

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 前端 KB 配置编辑 UI 表单 | ❌ 待实施 |
| P0 | 修复导入文档 doc_id 不匹配 | ❌ 待实施 |
| P1 | 启动时清扫 stale "indexing" 记录 | ❌ 待实施 |
| P1 | BM25 索引自动持久化/加载 | ❌ 待实施 |
| P1 | "重新向量化"按钮（配置 embedding 后） | ❌ 待实施 |
| P2 | Chunk 预览查询词高亮 | ❌ 待实施 |
| P2 | 文档版本管理 | ❌ 待实施 |

---

## 7. 接口清单

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/kb/upload` | 上传文档（支持 kb_id, chunk_method, chunk_size） |
| GET | `/api/kb/list` | 文档列表（可按 kb_id 过滤） |
| POST | `/api/kb/delete` | 删除文档（先向量后记录） |
| GET | `/api/kb/collections` | KB 列表 |
| POST | `/api/kb/collections` | 创建 KB |
| DELETE | `/api/kb/collections/{kb_id}` | 删除 KB |
| PATCH | `/api/kb/collections/{kb_id}/toggle` | 启用/禁用 KB |
| PATCH | `/api/kb/collections/{kb_id}/rename` | 重命名 KB |
| PATCH | `/api/kb/collections/{kb_id}/config` | 更新 KB 配置（embedding/agent chunker） |
| GET | `/api/kb/collections/{kb_id}` | KB 详情 |
| GET | `/api/kb/documents/{doc_id}/chunks` | 文档 chunks 列表 |
| POST | `/api/kb/embedding-models` | 获取可用 embedding 模型列表 |
| POST | `/api/kb/{kb_id}/export` | 导出 KB |
| POST | `/api/kb/{kb_id}/import` | 导入 KB |
| POST | `/api/chat` | 聊天（SSE，支持 kb_ids） |
