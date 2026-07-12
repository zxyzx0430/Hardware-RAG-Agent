# Tasks

- [x] Task 1: search_docs_core 加 timing + cache hit/miss observe
      新增 _observe_rag_retrieval 辅助函数（延迟 import _RAG_RETRIEVAL_SECONDS）；
      cache 命中/未命中两条路径都打结构化日志 + observe Histogram；
      日志格式 cache_hit=true/false total_ms=%d result_count=%d query=%r
  - [x] SubTask 1.1: 在 search_docs_core 入口记录 start_time + cache_key
  - [x] SubTask 1.2: 调用 rag_retrieval_seconds.observe(elapsed_seconds)
  - [x] SubTask 1.3: 加结构化日志
  - [x] 验证：py_compile 通过；日志输出 search_docs_core cache_hit=false total_ms=4401

- [x] Task 2: kb_manager.search 加四阶段 timing 日志
      vector/BM25/RRF 三阶段 timing 日志保留；reranker 阶段移至 search_all_enabled（stage=batch_reranker）
  - [x] SubTask 2.1: vector 检索 timing 日志
  - [x] SubTask 2.2: BM25 检索 timing 日志
  - [x] SubTask 2.3: RRF fusion timing 日志
  - [x] SubTask 2.4: reranker timing 日志（移到 search_all_enabled，stage=batch_reranker）
  - [x] 验证：日志输出 search_stage kb=%s stage=vector/bm25/rrf elapsed_ms=%d

- [x] Task 3: rerank 加 timing observe + 锁粒度优化 + 冷却期缩短
      锁只保护 get_reranker() 懒加载，predict 移到锁外；
      冷却期 300s→60s；predict 耗时 observe 到 _RAG_RERANKER_SECONDS
  - [x] SubTask 3.1: rerank 函数加 timing observe
  - [x] SubTask 3.2: 加 logger.info("rerank chunk_count=%d predict_ms=%d") 日志
  - [x] SubTask 3.3: 缩小 _RERANKER_LOCK 锁粒度——锁只保护 get_reranker()，predict 移到锁外
  - [x] SubTask 3.4: 冷却期 300s→60s
  - [x] 验证：日志输出 rerank chunk_count=5 predict_ms=3962

- [x] Task 4（新增）: 跨 KB 批量 rerank 优化
      根因：13 KB 各自独立 rerank，CPU 推理 47s/predict × 13 = 611s 串行；
      优化：reranker 从单 KB search 抽出，移到 search_all_enabled 合并后做一次批量 predict
  - [x] SubTask 4.1: search 方法删除 reranker 调用块（约 58 行）
  - [x] SubTask 4.2: search_all_enabled 合并后新增批量 rerank 块
  - [x] SubTask 4.3: 批量 rerank 复用 _RERANKER_MIN_SCORE / _RERANKER_MIN_KEEP 过滤
  - [x] 验证：search_docs_core 总耗时从 134s 降到 4.4s（30 倍提升）

# Task Dependencies

- Task 1-3 互相独立，已并行完成
- Task 4 在 agent-browser 测试发现 reranker 串行瓶颈后新增，已完成
- 端到端验证通过：134s → 4.4s
