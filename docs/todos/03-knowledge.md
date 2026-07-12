# 03-knowledge TODO

## 功能：知识库 RAG

> 新任务加到最前面（倒序排列），每次只读前 2 项

- [x] RAG 全量化实现（2026-06-23 完成）
  - chunking/ 模块：HybridChunker（结构切分+递归细切+小-大映射）+ AgentChunker（LLM 3 轮投票+TOC+漏页检测）
  - KnowledgeBase 数据模型 + KnowledgeBaseManager（多 KB CRUD + BM25 + RRF 融合检索）
  - vector_store.py 重构：per-KB embedding 配置 + ingest_chunks() + get_all_texts()
  - kb_routes.py 重写：9 个端点（upload/list/delete/collections CRUD/toggle/embedding-models）
  - 前端：KbCollectionManager 管理弹窗 + KnowledgePanel 改造（KB 选择器+分块方式覆盖）+ RagSettingsPanel 全局默认
  - 内置 KB 构建脚本：scripts/build_builtin_kb.py（--force 清空重建，幂等跳过已索引）
  - .gitignore：builtin_kb/ 和 bm25/hardware-docs.pkl 走 git，用户 KB 数据 gitignored
  - 测试：test_chunking.py（10 项全通过）+ test_routes_kb.py 更新匹配新 API
  - api-contract.md 同步更新 6 个新端点 + 6 个新错误码
- [x] /api/kb/upload 分块后向量化入库（2026-06-23 重写，支持 kb_id + chunk_method + 异步索引）
- [x] 实现 /api/kb/list 后端（2026-06-23 支持 kb_id 过滤）
- [x] 实现 /api/kb/delete 后端（2026-06-23 删除 ChromaDB + BM25 stale 标记）
- [x] BM25 + Vector 混合检索（2026-06-23 RRF 融合，jieba 分词）
- [x] ChromaDB 集合管理（创建/删除/查看）（2026-06-23 collections CRUD + toggle）
- [x] 前端知识库管理页面（上传/搜索/删除文档）（2026-06-23 KbCollectionManager + KnowledgePanel）
- [x] 回答标注来源（检索到的文档片段）— 已完成（2026-06-29 核实）：search_all_enabled() 已接入 /api/chat（[chat_helpers.py](file:///E:/Desktop/agent/backend/app/api/chat_helpers.py) L306 调用，传入 kb_ids），source 事件含 kb_id/kb_name（L333 yield sse_event("source", source_data)，L433-434 _build_source_event 返回 kb_id/kb_name）
- [x] /api/chat 接入 search_all_enabled()，source 事件加 kb_id + kb_name 字段 — 已完成（2026-06-29 核实）：[chat_helpers.py](file:///E:/Desktop/agent/backend/app/api/chat_helpers.py) L306 `kb_manager.search_all_enabled(rag_query_text, k=payload.top_k, kb_ids=payload.kb_ids, score_threshold=...)`，L384-436 `_build_source_event` 构建 source payload 含 `kb_id: r.kb_id` / `kb_name: r.kb_name`

---

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**：全部 [x] 后通知 00-control 审查