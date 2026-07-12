# RAG 全链路优化 Spec

## 基线

- Git commit: `f3e56685`
- 回滚命令: `git reset --hard f3e56685`

## 背景

ParentDocument 检索 spec 完成后，对 RAG 从上传文件入库到 LLM 引用回答的完整链路进行审查，发现 14 个问题（4 严重 / 6 中等 / 4 低）。本 spec 覆盖全部 14 个问题的修复。

## 问题清单与修复方案

### 严重（S1-S4）

#### S1: 删除文档/KB 不清理 big_chunks 表

**位置**: `vector_store.py:362 delete_document` / `kb_manager.py:628 delete_kb`

**问题**: `delete_document` 只删 ChromaDB 向量，不删 big_chunks 表。`delete_kb` 同样不清理 big_chunks。导致孤儿数据累积。

**修复**:
- `delete_document`: 在删 ChromaDB 后，加 `DELETE FROM big_chunks WHERE doc_id = ?`
- `delete_kb`: 在删 KnowledgeDoc 后，加 `DELETE FROM big_chunks WHERE kb_id = ?`
- 两处都包在 try/except 中，失败只 warning 不阻断

#### S2: 切换 embedding 模型后无维度一致性检查

**位置**: `kb_manager.py:491 update_kb_config` / `vector_store.py:321 search`

**问题**: 用户改 embedding_model 后旧向量维度不匹配，搜索报错或返回垃圾结果。

**修复**:
- `update_kb_config`: 检测 `embedding_model` 变化 + KB 已有向量数据 → 拒绝修改，返回错误提示"切换 embedding 模型需要删除知识库重建"
- `search`: 在查询前检查查询向量维度与 collection 维度是否一致，不一致时抛出明确错误（而非静默返回空列表）

#### S3: big_chunk_max_chars 配置项完全无效

**位置**: `hybrid_chunker.py:144` / `agent_chunker.py:1033,1373` / `multimodal_chunker.py:1269`

**问题**: 3 个 chunker 全部硬编码 `max_chars=4000`，KB 配置的 `big_chunk_max_chars` 从未被读取。

**修复**:
- 3 个 chunker 的构造函数加 `big_chunk_max_chars: int = 4000` 参数
- `_get_kb_chunker` 传 `big_chunk_max_chars=kb.big_chunk_max_chars or 4000`
- `truncate_at_boundary` 调用处用 `self.big_chunk_max_chars` 替代硬编码 4000

#### S4: data 字段未从 ToolMessage 剥离（docstring 与实现不符）

**位置**: `tool_result_envelope.py:1-8` docstring

**问题**: docstring 声称 "data 不进 LLM 上下文"，但实际 data 完整进入 ToolMessage.content。这是设计意图（LLM 需要看大块文本），不是 bug。

**修复**: 更新 docstring，说明 data 确实进入 LLM 上下文（这是 ParentDocument 检索的设计意图）。不改变代码行为。

### 中等（M1-M6）

#### M1: reranker 失败后永久禁用，无恢复机制

**位置**: `reranker.py:101`

**问题**: `_RERANKER_PREDICT_FAILED = True` 一旦触发，整个进程生命周期不再尝试 reranker。

**修复**:
- 改为基于时间戳的退避：记录 `_failed_at` 时间，5 分钟后允许重试
- 重试成功则清除失败状态
- 重试失败则更新 `_failed_at` 为当前时间

#### M2: search 吞掉所有异常返回空列表

**位置**: `vector_store.py:329`

**问题**: 所有异常都 `return []`，用户无法区分"无结果"和"搜索失败"。

**修复**:
- 可恢复错误（网络/超时/ChromaDB 连接）→ 保留 `return []`
- 不可恢复错误（维度不匹配/collection 不存在/认证失败）→ `raise` 向上传播
- 用异常类型区分：`ConnectionError` / `TimeoutError` → 可恢复；`ValueError` / `RuntimeError` → 不可恢复

#### M3: 同 big_chunk 多小块 content 重复存储

**位置**: `search_docs.py:194 _expand_result_entries`

**问题**: 一个 big chunk 有 N 个 small chunks → N 个 entries，每个 entry 的 `content` 都是同一份 big chunk 文本。

**修复**:
- 同 big_chunk_id 的 entries 共享 content 引用（Python 字符串是 immutable，天然共享）
- `_expand_result_entries` 中先提取 big chunk content 一次，所有 entries 的 `content` 指向同一字符串
- 实际上当前代码已经是这样（`_entry_base` 里 `content` 来自同一个 `getattr(r, "content", "")`），但被 `_truncate_chunk_content` 调用了 N 次
- 修复：将 `_truncate_chunk_content` 调用提到循环外，只截断一次

#### M4: 重新上传同名文档 big_chunks 可能残留

**位置**: `vector_store.py:509 _upsert_big_chunks`

**问题**: 只删除本次要写入的 big_chunk_id，不清理该 doc_id 下其他旧 big_chunk_id。section 编号变化时旧行残留。

**修复**:
- `_write_big_chunks` 在 `_upsert_big_chunks` 前加一步：`DELETE FROM big_chunks WHERE doc_id = ?`
- 然后再 insert 本次所有 big_chunks

#### M5: reranker score 不展示给用户

**位置**: `kb_manager.py:1006-1022`

**问题**: FusedResult.score 是 RRF display score，不是 reranker score。用户看到的"相关度"不准确。

**修复**:
- reranker 返回的 raw logit 用 sigmoid 归一化：`sigmoid(logit) = 1 / (1 + exp(-logit))`
- display score = `0.7 * sigmoid(reranker_score) + 0.3 * rrf_score`
- reranker 失败/降级时保持原 RRF score
- 需要在 `search_all_enabled` 的 batch rerank 中把 reranker score 回写到 FusedResult

#### M6: chunking 无全局超时保护

**位置**: `agent_chunker.py` chunk() 方法

**问题**: 大文档 LLM 切分可能跑 4-6 分钟，无超时保护。

**修复**:
- `kb_routes.py _index_document` 中调用 `chunker.chunk()` 包在 `asyncio.wait_for(timeout=300)` 中
- 超时后 fallback 到 hybrid chunker
- 300s 超时值放在 `settings.py` 可配置

### 低（L1-L4）

#### L1: reranker 模型/阈值不可配置

**位置**: `reranker.py` 全硬编码

**修复**:
- `settings.py` 新增：`RERANKER_MODEL`（默认 `BAAI/bge-reranker-base`）、`RERANKER_MIN_SCORE`（默认 -2.0）、`RERANKER_MIN_KEEP`（默认 2）、`RERANKER_RETRY_INTERVAL_SEC`（默认 300）
- `reranker.py` 从 settings 读取

#### L2: embedding batch_size 硬编码 10

**位置**: `vector_store.py:177`

**修复**:
- `settings.py` 新增 `EMBEDDING_BATCH_SIZE`（默认 10）
- `vector_store.py` 从 settings 读取

#### L3: HybridChunker chunk_overlap 死参数

**位置**: `hybrid_chunker.py:57-79`

**修复**:
- 删除构造函数的 `chunk_overlap` 参数
- `_get_kb_chunker` 调用处不再传 `chunk_overlap`

#### L4: 三层去重逻辑不一致

**位置**: `kb_routes.py:414` / `kb_manager.py:708` / `agent_chunker.py:1153`

**问题**: kb_routes 用单 `fingerprint`，kb_manager 和 agent_chunker 用 `(fingerprint, section_title)` 复合键。可能导致误删。

**修复**:
- kb_routes 的去重改为 `(fingerprint, section_title)` 复合键
- 统一三层去重逻辑

## 执行策略

4 个批次并行 subagent：

### 批次 1：数据一致性修复（S1 + M4）
- 文件：`vector_store.py` / `kb_manager.py`
- S1: delete_document + delete_kb 加 big_chunks 清理
- M4: _write_big_chunks 改为先按 doc_id 全删再 insert

### 批次 2：配置接线 + 维度安全（S2 + S3 + L1 + L2 + L3）
- 文件：`settings.py` / `vector_store.py` / `kb_manager.py` / `hybrid_chunker.py` / `agent_chunker.py` / `multimodal_chunker.py` / `kb_routes.py` / `reranker.py`
- S2: update_kb_config 维度检查 + search 维度检查
- S3: 3 个 chunker 读 big_chunk_max_chars
- L1: reranker 配置化
- L2: embedding batch_size 配置化
- L3: 删除死参数 chunk_overlap

### 批次 3：检索链路优化（M1 + M2 + M3 + M5 + M6）
- 文件：`reranker.py` / `vector_store.py` / `search_docs.py` / `kb_manager.py` / `kb_routes.py`
- M1: reranker 定时重试
- M2: search 错误区分
- M3: content 共享引用（截断一次）
- M5: reranker score 加权平均
- M6: chunking 超时保护

### 批次 4：去重统一 + 文档修正（L4 + S4）
- 文件：`kb_routes.py` / `tool_result_envelope.py`
- L4: 三层去重统一复合键
- S4: 更新 docstring

## 约束

- 每个批次完成后 `python -m py_compile` + `npx tsc --noEmit` 验证
- 每个批次独立 git commit
- 不破坏现有接口签名
- 函数 ≤ 10 行，圈复杂度 ≤ 10
- DB 调用必须有 try/except

## 验证

- 14 个问题全部修复
- py_compile + tsc --noEmit 通过
- 端到端测试：上传文档 → 检索 → LLM 回答 → source 展示
- 删除文档后 big_chunks 表无残留
- 切换 embedding 模型被拒绝（有数据时）
- reranker 失败后 5 分钟可恢复
- source 卡片显示加权平均分数
