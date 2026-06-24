# 03-knowledge 扫描报告

> 日期：2026-06-25
> 方法：Pass 1（广度扫描）+ Pass 2（深度扫描）
> 结果：只记录不修改

---

## [P1] kb_routes._parse_file 与 file_parsers 功能重复

- 位置：`backend/app/api/kb_routes.py` → `_parse_file()`
- 现象：`kb_routes.py` 定义了自己的 `_parse_file()`，解析逻辑与 `backend/src/rag/file_parsers.py` 中的 `XlsxParser`/`CsvParser`/`JsonParser` 重复。
- 影响评估：两端代码不一致时易出现行为差异。例如 `ExcelParser` 在 `file_parsers.py` 中有改进时，`kb_routes._parse_file` 不会自动同步。
- 建议修复方式：`kb_routes._parse_file` 改为调用 `file_parsers` 中的统一接口。

## [P1] kb_routes 集合管理端点未写进 api-contract.md

- 位置：`docs/api-contract.md` 5.3
- 现象：`kb_routes.py` 已实现多个集合管理端点（`kb/collections` CRUD、`kb/{kb_id}/export`、`kb/{kb_id}/import`、`kb/embedding-models`），但 `api-contract.md` 5.3 只记录了 upload/list/delete 三个端点。
- 影响评估：前后端联调时可能因接口理解不一致导致对接问题。新开发者只看契约会遗漏功能。
- 建议修复方式：将集合管理相关端点补充到 `api-contract.md` 5.3，标注状态。

## [P1] kb/list 返回字段与前端期望不匹配

- 位置：`backend/app/api/kb_routes.py` `kb_list()` vs `frontend/src/stores/useKnowledgeStore.ts` `fetchItems()`
- 现象：后端返回 `title`/`chunk_count`，前端 `fetchItems` 映射为 `filename`/`chunks`。后端无 `enabled` 字段，前端硬编码 `true`。
- 影响评估：前端靠映射兼容，长期维护成本高。`enabled` 字段缺失导致用户无法通过开关控制 KB 是否参与搜索。
- 建议修复方式：后端 `kb/list` 响应增加 `filename`（alias `title`）、`chunks`（alias `chunk_count`）、`enabled` 字段，或统一前端字段映射。

## [P2] AgentChunker 硬编码 API Key 内存明文存储

- 位置：`backend/src/rag/chunking/agent_chunker.py` L18
- 现象：`AgentChunker.__init__` 接收 `api_key` 参数，直接存储在 self.api_key 中（明文）。`_run_chunking_round` 中直接传给 `AsyncOpenAI()`。
- 影响评估：API Key 在进程内存中长期驻留。如果进程 core dump 或被 attach，可能泄漏。
- 建议修复方式：参考 `kb_manager.py` 的做法，使用 `encrypt_key`/`decrypt_key` 加密存储，仅在调用 LLM 时解密使用。

## [P2] BM25 Index 使用 pickle 序列化，存在安全风险

- 位置：`backend/src/rag/kb_manager.py` `BM25Index.save()` / `.load()`
- 现象：`BM25Index.save()` 使用 `pickle.dump` 存到磁盘，`load()` 用 `pickle.load` 反序列化。pickle 在加载恶意数据时可执行任意代码。
- 影响评估：本地部署场景风险较低（只有用户自己能写入 BM25 目录），但如果未来扩展为多人共享或导入导出功能可能构成攻击面。
- 建议修复方式：使用 JSON 或 `safetensors` 替代 pickle 序列化 BM25 索引。

## [P2] BM25 重建期间搜索同步阻塞事件循环

- 位置：`backend/src/rag/kb_manager.py` `_bm25_search()` → `_rebuild_bm25()`
- 现象：`_bm25_search()` 检测到 stale 后同步调用 `_rebuild_bm25()`，其中 `store.db.get()` 读取 ChromaDB 所有文档、`jieba.lcut` 中文分词、`BM25Okapi` 构建索引，都是 CPU 密集型操作，会阻塞 asyncio 事件循环。
- 影响评估：上传文档后首次搜索会明显卡顿（可能数百毫秒到数秒），影响用户体验。
- 建议修复方式：将 `_rebuild_bm25` 改为 `asyncio.to_thread()` 或放入后台任务队列。

## [P2] _bm25_stale 非线程安全

- 位置：`backend/src/rag/kb_manager.py` L241 `self._bm25_stale: set[str] = set()`
- 现象：`_bm25_stale` 是普通 `set`，`add()` 和 `discard()` 操作不是原子性的。多个并发上传触发 BM25 标记 stale 时可能丢失状态。
- 影响评估：极端情况下 BM25 可能不会按预期重建，搜索使用过期索引。
- 建议修复方式：改用 `asyncio.Lock()` 保护，或使用 `threading.Lock` / `set` 的上下文管理器。

## [P2] KnowledgeDoc 缺少 enabled 字段

- 位置：`backend/app/db/models.py` `KnowledgeDoc` 类
- 现象：`KnowledgeDoc` 模型缺少 `enabled` 字段（Boolean），前端 `KBItem` 接口有 `enabled` 字段并在 `toggleItem` 中使用，但开关状态不会持久化到后端。
- 影响评估：用户开关 KB 文档的设置在页面刷新后丢失，所有文档始终为 enabled。
- 建议修复方式：在 `KnowledgeDoc` 中添加 `enabled = Column(Boolean, default=True)`，并在 toggle 时调用后端 API 持久化。

## [P2] Agent chunker LLM 失败会抛出异常导致整个上传失败

- 位置：`backend/src/rag/chunking/agent_chunker.py` `_run_chunking_round()`
- 现象：当 LLM 调用失败（网络超时、API 错误等）时，`_run_chunking_round` 会 raise 异常。第一轮失败直接终止整个上传，不会降级到 hybrid chunker。
- 影响评估：用户如果配置了 agent chunker 但 LLM 临时不可用，文档上传完全失败，不会自动 fallback。
- 建议修复方式：在 `kb_routes._index_document()` 中 catch `AgentChunkError` 后自动降级到 hybrid chunker。

## [P2] vector_store.ingest_chunks 无超时控制

- 位置：`backend/src/rag/vector_store.py` `ingest_chunks()` L35
- 现象：`self.db.add_documents(lc_docs)` 没有 timeout 参数。当 ChromaDB 后端响应慢或嵌入 API 调用超时时，整个上传接口会挂住。
- 影响评估：上传大文件时前端可能等待超过 120s 轮询才感知到失败。
- 建议修复方式：为 ChromaDB 操作添加超时，或确保 LLM 客户端 level 的超时能传递到 embedding 调用。

## [P3] Pipeline skip_translate 路径手动构造 ProcessedDocument

- 位置：`backend/src/rag/pipeline.py` L106-125
- 现象：`skip_translate=True` 时手动构造 `ProcessedDocument(doc_id=..., source=..., pdf_path=..., raw_markdown=..., translated_markdown=..., metadata=...)`。这段代码容易与 `document_processor.py` 中的构造函数定义不同步。
- 影响评估：如果 `ProcessedDocument` 增加/删除字段，`pipeline.py` 不会收到编译错误，运行时可能隐藏问题。
- 建议修复方式：从 `ProcessedDocument` 类中提供工厂方法 `from_markdown_file()` 或统一走 `processor.process_one()`。

## [P3] HybridChunker 页面估算过于粗糙

- 位置：`backend/src/rag/chunking/hybrid_chunker.py` `_split_markdown()` / `_split_plain_text()`
- 现象：用 `3000 chars/page` 估算页面范围。对于高密度技术文档或稀疏文档，偏差可能很大。
- 影响评估：页面覆盖检测（`verify_page_coverage`）的准确性受限，可能误报漏页或多报。
- 建议修复方式：对于 PDF 文件优先使用 PyMuPDF 读取真实页数映射（如 `agent_chunker.py` 的做法），非 PDF 文件接受估算误差。

## [P3] _update_doc_status 的 coverage 参数未使用

- 位置：`backend/app/api/kb_routes.py` `_update_doc_status()` L10-12
- 现象：`coverage` 参数被接收但只在 `pass` 中丢弃。上传响应的 `coverage` 字段也没有返回给前端。
- 影响评估：页覆盖检测的结果存在但未暴露给用户，用户无法感知文档是否有漏页。
- 建议修复方式：将 coverage 信息写入 KnowledgeDoc 的 `error_message` 或新增字段，并在 `kb/detail` 中返回。

## [P3] 使用 `asyncio.ensure_future` 而非 `create_task`

- 位置：`backend/app/api/kb_routes.py` `kb_upload()` → `asyncio.ensure_future(_index_document())`
- 现象：`ensure_future()` 比 `create_task()` 更低级，在某些 asyncio 事件循环配置下可能不按预期调度。
- 影响评估：极低概率下后台索引任务启动失败，用户上传成功但文档永远不会被索引。
- 建议修复方式：改用 `asyncio.create_task()`，并在任务异常时记录日志。

---

## 汇总

| 级别 | 数量 | 关键项 |
| --- | --- | --- |
| **P1** | 3 | _parse_file 重复、集合端点未进契约、kb/list 字段不匹配 |
| **P2** | 6 | API Key 明文、pickle 安全、BM25 同步阻塞、_bm25_stale 非线程安全、KnowledgeDoc 缺 enabled、Agent chunker 无降级、ingest_chunks 无超时 |
| **P3** | 3 | Pipeline 手构 ProcessedDocument、混合分块页面估算粗糙、coverage 参数未用、ensure_future 不如 create_task |
| **总计** | 12 | 需修复 9 项 / 信息性 3 项 |
