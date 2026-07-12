# Search_docs 性能可观测性与 Reranker 优化 Spec

## Why

用户反馈 search_docs 调用耗时不稳定（5 分钟 vs 5 秒）。调研发现：
1. **可观测性严重缺失**——Prometheus 指标 `rag_retrieval_seconds` / `rag_reranker_seconds` 定义了但零 observe 调用，reranker 和 kb_manager.search 没有 per-stage timing 日志，出问题无法定位是哪个阶段慢
2. **reranker 全局锁串行化**——13 个 KB 并行检索在 reranker 阶段被 `_RERANKER_LOCK` 串行化，单次 predict 250ms × 13 = 3.25s
3. **reranker 冷却期过长**——predict 偶发失败后 300s（5 分钟）内所有查询降级返回 0.0 分数

本 spec 不动 embedding API 配置（用户明确排除），聚焦可观测性 + reranker 优化。

## What Changes

### 可观测性
- **修改** `search_docs_core`（search.py）—— 入口/出口加 timing + cache hit/miss 标记，observe 到 `rag_retrieval_seconds`
- **修改** `kb_manager.search`（kb_manager.py:822-955）—— vector / BM25 / RRF / reranker 四阶段分别打 timing 日志
- **修改** `rerank`（reranker.py:78-124）—— 记录 predict 耗时 + chunk 数量，observe 到 `rag_reranker_seconds`

### Reranker 优化
- **修改** `reranker.py` —— 缩小 `_RERANKER_LOCK` 锁粒度：模型加载用锁保护一次，predict 本身不加锁（CrossEncoder 加载后线程安全）
- **修改** `reranker.py` —— 冷却期从 300s 缩短到 60s

## Impact
- Affected specs: 无
- Affected code:
  - `backend/src/rag/search.py` — search_docs_core 加 timing observe
  - `backend/src/rag/kb_manager.py` — search 四阶段 timing 日志
  - `backend/src/rag/reranker.py` — rerank timing observe + 锁粒度优化 + 冷却期缩短

## ADDED Requirements

### Requirement: search_docs 链路可观测性

`search_docs_core` 和其下游阶段 SHALL 记录耗时到 Prometheus metrics 和结构化日志，让运维能定位性能瓶颈。

#### Scenario: search_docs_core 总耗时可观测
- **WHEN** Agent 调用 search_docs 工具
- **THEN** `rag_retrieval_seconds` Histogram observe 本次总耗时
- **AND** 日志记录 `cache_hit=true/false` + `total_ms` + `result_count`

#### Scenario: kb_manager.search 四阶段耗时可观测
- **WHEN** kb_manager.search 执行
- **THEN** 对 vector / BM25 / RRF / reranker 四个阶段分别打日志
- **AND** 日志格式 `search_stage kb=%s stage=%s elapsed_ms=%d`

#### Scenario: reranker 推理耗时可观测
- **WHEN** rerank 函数执行 predict
- **THEN** `rag_reranker_seconds` Histogram observe 本次 predict 耗时
- **AND** 日志记录 `chunk_count` + `predict_ms`

## MODIFIED Requirements

### Requirement: Reranker 锁粒度优化

`_RERANKER_LOCK` 原本保护整个 rerank 函数（模型加载 + predict），导致多 KB 并行检索在 reranker 阶段串行化。修改为：锁只保护模型懒加载，predict 本身不加锁（CrossEncoder 模型加载后 predict 是线程安全的）。

#### Scenario: 多 KB 并行 reranker 不串行
- **WHEN** 13 个 KB 并行调 rerank
- **THEN** 模型已加载后，多个 predict 并行执行
- **AND** 不再出现 13 × 250ms = 3.25s 的串行累计耗时

### Requirement: Reranker 冷却期缩短

reranker predict 失败后的冷却期从 300s 缩短到 60s，让偶发失败快速恢复。

#### Scenario: predict 偶发失败后快速恢复
- **WHEN** reranker predict 抛异常
- **THEN** 进入 60s 冷却期（原为 300s）
- **AND** 60s 后下一次调用重新尝试 predict
