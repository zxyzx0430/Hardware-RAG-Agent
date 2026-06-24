## 任务：全面代码扫描（两轮，不修）

目标线程：03-knowledge（知识库 / RAG）

范围文件：
- backend/src/rag/chunking/*.py
- backend/src/rag/kb_manager.py
- backend/src/rag/vector_store.py
- backend/src/rag/document_loader.py
- backend/src/rag/document_processor.py
- backend/src/rag/file_parsers.py
- backend/src/rag/pipeline.py
- backend/app/api/kb_routes.py
- frontend/src/components/knowledge/*.tsx
- frontend/src/stores/useKnowledgeStore.ts
- frontend/src/components/settings/RagSettingsPanel.tsx
- frontend/src/types/kb.ts
- backend/app/db/models.py (KnowledgeBase + KnowledgeDoc)
- backend/tests/test_chunking.py

方法：做两轮扫描，每轮把结果追加到 `docs/review/03-knowledge-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 30 分钟通读全部范围文件，找以下问题：

1. 类型安全
   - Python 类型注解缺失
   - ChromaDB 调用的异常未捕获
   - embedding 为 None 时的守卫

2. 功能完整性
   - 所有 chunk_method 分支都覆盖了吗（hybrid / agent）
   - BM25 索引在 KB 增删文档后是否重建
   - 内置 KB 启动检测是否可靠

3. 代码异味
   - 重复的分块逻辑
   - 硬编码的模型名称/URL
   - 不必要的同步操作

4. 资源泄漏
   - PyMuPDF 打开的文档是否关闭
   - ChromaDB 连接是否有超时

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 3-5 个最可疑的路径做深度检查：

1. 契约对齐
   - kb_routes 的响应字段和 api-contract.md 一致吗
   - 新的 collection 路由写进文档了吗

2. 错误处理
   - Agent 分块 LLM 失败时返回什么
   - 漏页检测不通过时前端显示什么
   - 向量化超时怎么办

3. 竞态条件
   - 多个文件同时上传入库时数据一致吗
   - BM25 重建期间搜索是否阻塞

4. 边界情况
   - 空知识库搜索返回什么
   - 不支持的 embedding 模型配置
   - 超大文件（>50MB）的分块

### 输出格式

每个问题按这个格式写：

```
## [P0/P1/P2] 简短标题

- 位置：文件路径:行号
- 现象：
- 影响评估（出故障时用户看到什么）：
- 建议修复方式（一句话）：
```

### 完成后

两轮都做完后，通知 00-control：「03-knowledge 扫描完成，结果在 docs/review/03-knowledge-scan.md」

注意：不要修，只记录。
