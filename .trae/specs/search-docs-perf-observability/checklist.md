# Checklist

## 可观测性
- [x] search_docs_core 总耗时 observe 到 rag_retrieval_seconds Histogram
- [x] search_docs_core 日志记录 cache_hit + total_ms + result_count
- [x] kb_manager.search 四阶段（vector/BM25/RRF）分别打 timing 日志
- [x] rerank 函数 predict 耗时 observe 到 rag_reranker_seconds Histogram
- [x] rerank 函数日志记录 chunk_count + predict_ms
- [x] 批量 rerank 日志 stage=batch_reranker elapsed_ms + chunk_count

## Reranker 优化
- [x] _RERANKER_LOCK 锁粒度缩小——只保护模型懒加载，predict 移到锁外
- [x] 冷却期从 300s 缩短到 60s
- [x] 跨 KB 批量 rerank——reranker 从单 KB search 移到 search_all_enabled 合并后
- [x] agent-browser 测试验证：多 KB 并行时 reranker 不再 13 次串行

## 端到端验证
- [x] 后端 py_compile 3 个文件通过（search.py / kb_manager.py / reranker.py）
- [x] 后端启动正常（reranker 5.4s 加载 + BM25 6.5s 加载）
- [x] agent-browser 测试：调一次 search_docs，日志能看到各阶段耗时
- [x] agent-browser 测试：search_docs_core 总耗时从 134s 降到 4.4s（30 倍提升）
- [x] agent-browser 测试：批量 rerank predict_ms=3962ms（原 13×47s=611s）
