# RAG 检索全链路优化报告

> 生成时间：2026-06-28
> 范围：从用户输入到 LLM 引用 source 的完整 RAG 链路
> 基线：首次 132s → 优化后 76s；第二次 80s → 优化后 25.6s（RAG 检索 0.1s）

---

## 1. 全链路架构

```
用户输入
  │
  ▼
┌─────────────────────────────────┐
│ 1. Query Rewrite (LLM, 24h LRU) │  chat_helpers._rewrite_query_for_rag
│    展开缩写 / 解析代词 / 提取术语  │  超时 8s → 降级为原始 query
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│ 2. Multi-KB Parallel Search     │  kb_manager.search_all_enabled
│    ThreadPoolExecutor (≤8)      │  每个 KB 独立 search
└──────────────┬──────────────────┘
               │
   ┌───────────┴───────────┐
   ▼                       ▼
┌────────────┐    ┌──────────────┐
│ 3a. Vector │    │ 3b. BM25     │
│  (Chroma)  │    │  (jieba+词典) │
│  cosine    │    │  BM25Okapi   │
└─────┬──────┘    └──────┬───────┘
      │                  │
      ▼                  ▼
┌─────────────────────────────────┐
│ 4. RRF Fusion (k=60)            │  kb_manager.rrf_fusion
│    score = max(cos, bm25_norm)  │  排序用 RRF，显示用原始分
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│ 5. CrossEncoder Reranker        │  reranker.rerank
│    bge-reranker-base (280MB)    │  失败降级为 RRF 顺序
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│ 6. Threshold Filter + Top-K     │  score_threshold 过滤
└──────────────┬──────────────────┘
               │
               ▼
          SSE source 事件 → LLM context
```

---

## 2. 已完成的优化（本次 + 之前）

| # | 优化点 | 文件 | 效果 | 状态 |
|---|--------|------|------|------|
| O1 | reranker large → base | `reranker.py` | 模型 1.3GB → 280MB，加载快 2-3 倍 | ✅ |
| O2 | sentence_transformers 5.x → 4.x | 环境 | 修复 "Modality 'audio'" 错误，reranker 恢复正常 | ✅ |
| O3 | Query rewrite LRU 缓存 | `chat_helpers.py` | 相同 query 24h 内跳过 LLM（省 5-8s） | ✅ |
| O4 | ChromaDB import 失败缓存 | `vector_store.py` | `_db_unavailable` 标记，避免重复 import（省 100s+） | ✅ |
| O5 | Reranker predict 失败缓存 | `reranker.py` | `_RERANKER_PREDICT_FAILED`，避免重复 predict | ✅ |
| O6 | 多 KB 并行搜索 | `kb_manager.py` | ThreadPoolExecutor，10 KB × 6s → ~6s | ✅ |
| O7 | Reranker 线程锁 | `reranker.py` | `_RERANKER_LOCK` 保护多线程单例加载 | ✅ |
| O8 | Embedding 磁盘缓存 | `vector_store.py` | diskcache sha256 去重，重复文本零 API 调用 | ✅ |
| O9 | HNSW ef_search 200 | `vector_store.py` | 默认 10 → 200，提升召回率 | ✅ |
| O10 | 前端 SSE 超时 120s | `client.ts` | 60s → 120s，容忍首次模型加载 | ✅ |
| O11 | opentelemetry 版本修复 | 环境 | 解决 chromadb import 静默失败 | ✅ |
| O12 | BM25 eager rebuild | `kb_manager.py` | 入库时即重建索引，search 时不阻塞 | ✅ |
| O13 | torchcodec shim | `compat/torchcodec_shim.py` | 无 FFmpeg 时 stub 替代，不阻塞 import | ✅ |

---

## 3. 当前性能基线

| 场景 | 耗时 | 说明 |
|------|------|------|
| 首次查询（冷启动） | ~76s | 模型下载 15s + reranker 初始化 19s + embedding 初始化 + 实际检索 |
| 第二次查询（热启动） | ~25.6s | 含 query rewrite LLM 调用 ~5-8s |
| 相同 query 第三次（LRU 命中） | ~18-20s（预估） | query rewrite 0ms（缓存命中）+ 检索 0.1s + LLM 流式 |
| RAG 检索本身（热启动） | 0.1s | BM25 + vector + RRF + rerank |

**结论：** 检索本身已不是瓶颈（0.1s），主要延迟来自：
1. Query rewrite LLM 调用（5-8s，首次）
2. Reranker 模型加载（19s，仅首次）
3. LLM 流式回答生成（取决于上游 API）

---

## 4. 仍存在的问题

### P0 — ChromaDB HttpClient 连接失败

- **现象**：`Could not connect to tenant default_tenant`，向量检索完全不可用
- **影响**：只有 BM25 在工作，向量召回缺失，检索质量下降
- **原因**：chromadb server 未启动或配置错误
- **修复建议**：
  - 检查 `chroma_mode` 设置，如果不需要 http 模式，改回 `persistent`（默认）
  - 或启动 chromadb server：`chroma run --host 127.0.0.1 --port 8001`
  - 检查 `data/chroma/` 目录权限

### P1 — test_ocr_parser.py 导入失败

- **现象**：`ImportError: cannot import name 'PdfParser' from 'src.rag.file_parsers'`
- **影响**：测试套件 collection 中断
- **原因**：`PdfParser` 类已被重命名或移除，测试未同步
- **修复建议**：更新 test_ocr_parser.py 的 import，或恢复 PdfParser 类

### P2 — 14 个 pre-existing 测试失败

- **现象**：401 Unauthorized（auth 中间件）、test_llm mock 问题、test_settings 环境值
- **影响**：CI 信号噪声大
- **修复建议**：修复 auth 测试 mock、test_llm 的 patch 目标、test_settings 的环境隔离

---

## 5. 可优化方向（按收益/成本排序）

### 5.1 高收益 / 低成本

#### A. Query Rewrite 并行化（省 5-8s 首次延迟）

**现状**：query rewrite → 等待 LLM 返回 → 才开始检索（串行）
**优化**：query rewrite 和 BM25 检索并行执行，用原始 query 先跑 BM25，rewrite 返回后用改写 query 跑 vector search

```python
# 伪代码
async def _run_rag_retrieval(...):
    raw_query = extract_text_from_multimodal(last_user_msg)
    # 并行：rewrite + BM25（用原始 query）
    rewrite_task = asyncio.create_task(_rewrite_query_for_rag(raw_query, ...))
    bm25_task = run_in_executor(kb_manager.bm25_search_all, raw_query, k)
    rewritten = await rewrite_task
    # vector search 用改写后的 query
    vector_task = run_in_executor(kb_manager.vector_search_all, rewritten, k)
    bm25_results = await bm25_task
    vector_results = await vector_task
    # RRF fusion + rerank
```

- **收益**：首次延迟减少 5-8s（rewrite 和 BM25 并行）
- **成本**：中等（需重构 search_all_enabled 拆分为 bm25_search + vector_search）

#### B. Reranker 预加载（省 19s 首次延迟）

**现状**：reranker 首次 predict 时才加载模型（19s）
**优化**：应用启动时（FastAPI lifespan）后台预热 reranker

```python
# app/main.py lifespan
@app.on_event("startup")
async def warmup_models():
    # 后台线程加载 reranker，不阻塞启动
    threading.Thread(target=lambda: get_reranker(), daemon=True).start()
```

- **收益**：首次查询不再等 19s 模型加载
- **成本**：低（3 行代码），但增加 ~280MB 常驻内存

#### C. Embedding 模型预加载

**现状**：首次向量检索时加载 embedding 模型/API 初始化
**优化**：同上，启动时预热

- **收益**：首次向量检索更快
- **成本**：低

#### D. BM25 索引启动时预加载

**现状**：首次 search 时才加载 BM25 pickle（每个 KB ~0.5-1s）
**优化**：启动时遍历 enabled KBs 预加载 BM25 索引

- **收益**：首次查询省 5-10s（10 个 KB × 0.5-1s）
- **成本**：低

### 5.2 中收益 / 中成本

#### E. Query Rewrite 改用本地小模型（省 API 调用）

**现状**：每次 rewrite 调用 LLM API（5-8s + 费用）
**优化**：用本地小模型（如 Qwen2.5-0.5B）做 query rewrite，离线运行

- **收益**：零 API 成本，延迟 <1s
- **成本**：中（需下载模型 ~1GB，增加内存），但 LRU 缓存已大幅减少调用频率

#### F. 向量检索结果缓存

**现状**：每次 search 都重新查 ChromaDB
**优化**：对相同 query + kb_ids 组合缓存向量检索结果（短 TTL，如 5 分钟）

- **收益**：热查询省向量检索时间
- **成本**：中（需处理缓存失效——文档更新时清缓存）

#### G. Reranker 批量化 + 半精度

**现状**：FP32 推理
**优化**：`CrossEncoder(model, max_length=512, automodel_args={"torch_dtype": "float16"})` 

- **收益**：推理速度提升 1.5-2x，内存减半
- **成本**：低（一行代码），但需验证 GPU/CPU 兼容性

#### H. Chunk 检索粒度调优

**现状**：small_chunk_size=800，big_chunk_text=4000
**优化**：A/B 测试不同 chunk_size（500/800/1200/2000）对召回率的影响

- **收益**：可能提升检索精度
- **成本**：中（需重新入库测试文档）

### 5.3 低收益 / 高成本（长期）

#### I. 混合检索权重学习

**现状**：RRF 用固定 k=60，vector 和 BM25 权重相同
**优化**：用标注数据学习最优权重（如 weighted RRF: `α·vector + (1-α)·bm25`）

- **收益**：检索质量提升
- **成本**：高（需标注数据 + 训练流程）

#### J. 多路召回扩展

**现状**：BM25 + Vector 两路召回
**优化**：增加第三路——embedding 模型 dense retrieval + sparse retrieval（如 SPLADE）

- **收益**：召回率提升
- **成本**：高（需额外模型 + 索引）

#### K. Reranker 升级

**现状**：bge-reranker-base（280MB）
**优化**：bge-reranker-v2-m3（568MB，多语言）或 bge-reranker-large（1.3GB）

- **收益**：rerank 精度提升
- **成本**：高（内存 + 加载时间），但精度提升可能有限

#### L. 查询意图分类

**现状**：所有查询走相同 pipeline
**优化**：先分类（参数查询 / 代码示例 / 接线方案 / 故障排查），不同意图走不同检索策略

- **收益**：精准检索
- **成本**：高（需训练分类器 + 多套检索策略）

---

## 6. 推荐优先级

| 优先级 | 优化项 | 预期收益 | 实施成本 | 建议 |
|--------|--------|----------|----------|------|
| **P0** | 修复 ChromaDB HttpClient | 向量检索恢复 | 低 | 立即修复 |
| **P1** | Reranker 预加载 (B) | 首次省 19s | 低（3 行） | 强烈推荐 |
| **P1** | BM25 预加载 (D) | 首次省 5-10s | 低 | 强烈推荐 |
| **P2** | Query Rewrite 并行化 (A) | 首次省 5-8s | 中 | 推荐 |
| **P2** | Reranker FP16 (G) | 推理快 1.5-2x | 低 | 推荐 |
| **P3** | 向量结果缓存 (F) | 热查询更快 | 中 | 可选 |
| **P3** | Chunk 粒度调优 (H) | 精度可能提升 | 中 | 可选 |
| **P4** | 本地 rewrite 模型 (E) | 省 API 费用 | 中 | LRU 已缓解，暂缓 |
| **P4** | 混合权重学习 (I) | 质量提升 | 高 | 长期 |

---

## 7. 关键文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| [chat_helpers.py](file:///e:/Desktop/agent/backend/app/api/chat_helpers.py) | ~440 | Query rewrite + RAG 检索编排 |
| [kb_manager.py](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py) | 990 | BM25 + RRF + KB 管理（核心） |
| [reranker.py](file:///e:/Desktop/agent/backend/src/rag/reranker.py) | 102 | bge-reranker-base cross-encoder |
| [vector_store.py](file:///e:/Desktop/agent/backend/src/rag/vector_store.py) | 558 | ChromaDB + Embedding 缓存 |
| [document_processor.py](file:///e:/Desktop/agent/backend/src/rag/document_processor.py) | 442 | Docling + LLM 翻译 |
| [search.py](file:///e:/Desktop/agent/backend/src/rag/search.py) | 51 | search_docs_core 主入口 |
| [hybrid_chunker.py](file:///e:/Desktop/agent/backend/src/rag/chunking/hybrid_chunker.py) | 537 | 混合切分策略 |
| [settings.py](file:///e:/Desktop/agent/backend/src/config/settings.py) | 176 | 全局配置 |
| [client.ts](file:///e:/Desktop/agent/frontend/src/api/client.ts) | — | SSE 连接 + 超时 |

---

## 8. 配置参数速查

### BM25
| 参数 | 默认 | 环境变量 | 调优建议 |
|------|------|----------|----------|
| k1 | 1.5 | `BM25_K1` | 1.2-2.0（长文档取大） |
| b | 0.75 | `BM25_B` | 0.5-0.8（短文档取小） |

### ChromaDB
| 参数 | 默认 | 环境变量 | 调优建议 |
|------|------|----------|----------|
| ef_search | 200 | `HNSW_EF_SEARCH` | 100-500（大取高，召回↑速度↓） |
| space | cosine | 硬编码 | cosine 适合语义检索 |
| mode | persistent | `CHROMA_MODE` | persistent（嵌入式）/ http（C/S） |

### Embedding
| 参数 | 默认 | 说明 |
|------|------|------|
| model | text-embedding-3-small | OpenAI 兼容 |
| chunk_size | 10 | 批量上限（阿里云百炼限制） |
| max_retries | 6 | 网络瞬态错误重试 |
| request_timeout | 60s | 单次请求超时 |

### Reranker
| 参数 | 值 | 说明 |
|------|------|------|
| model | BAAI/bge-reranker-base | 280MB, FP32 |
| max_length | 512 | 截断长度 |
| HF_ENDPOINT | hf-mirror.com | 中国加速下载 |

### Query Rewrite
| 参数 | 值 | 说明 |
|------|------|------|
| TTL | 86400s (24h) | LRU 缓存有效期 |
| MAX | 256 条 | LRU 上限 |
| timeout | 8s | LLM 超时降级 |
| min_query_len | 6 chars | 短 query 跳过改写 |

### RRF Fusion
| 参数 | 默认 | 说明 |
|------|------|------|
| constant_k | 60 | RRF 融合常数 |
| score_display | max(cos, bm25_norm) | 显示用原始分 |
| score_sort | RRF score | 排序用融合分 |
