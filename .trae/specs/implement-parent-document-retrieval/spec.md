# Parent Document Retrieval 完整实现 Spec

## Why

大小分块策略目前只实现了一半：分块阶段生成了 `big_chunk_text`（仅 HybridChunker），但检索→LLM→来源引用链路完全断裂——`big_chunk_text` 在 metadata 里一路透传却无人提取，LLM 拿到的是 800 字符小块拼接，用户点击 [srcN] 看到的也是小块。需要打通完整链路，实现"小块检索精度 + 大块返回 LLM 上下文完整"的 ParentDocument 模式。

## What Changes

### 后端

- **新增 `big_chunks` 表**（SQLAlchemy model）：存储大块独立于小块，避免冗余存储
  - 字段：`big_chunk_id` / `doc_id` / `kb_id` / `section_title` / `text` / `page_start` / `page_end` / `created_at`
  - **BREAKING**：现有 KB 需重新索引（migration 脚本）
- **`hybrid_chunker.py` 改造**：
  - `big_chunk_text` 字段改为 `big_chunk_id`（指向 big_chunks 表）
  - 硬截断 `[:4000]` 改为**边界截断**（在 3900-4100 范围内找最近的句子/段落边界）
- **`agent_chunker.py` + `multimodal_chunker.py` 补上 big_chunk_id 生成**：
  - 在 section 切分时为每个 section 生成 big_chunk_id
  - section 全文（边界截断后）作为大块文本
  - 小块 metadata 写入 `big_chunk_id` 指向大块
- **`vector_store.py` 改造**：
  - `ingest_chunks` 时同时写 big_chunks 表 + 小块 metadata 存 big_chunk_id
  - 新增 `get_big_chunks_by_ids(big_chunk_ids: list[str])` 批量查询接口
- **`kb_manager.py` 改造**：
  - 检索后通过 big_chunk_id 批量查 big_chunks 表拿大块文本
  - **同 big_chunk_id 去重**（保留最高分小块的 score 作为大块 score）
  - 返回的 FusedResult.content 改为**大块文本**
  - FusedResult.metadata 保留小块信息（small_chunk_id / chunk_index / score）
- **`search_docs.py` 改造**：
  - LLM context 用大块 content（去重后的 15 个大块）
  - source 事件保持**小块粒度**（15 个 [srcN]，各带 big_chunk_id）
  - 每个 source 携带 big_chunk_id 字段

### 前端

- **`useChatStore.ts` 改造**：
  - `SourceRef` 类型加 `big_chunk_id?: string` 字段
  - source 事件处理时存 big_chunk_id
- **`RightPanel.tsx` 改造**：
  - source 详情直接显示**大块文本**
  - 高亮显示命中小块区域（通过 small_chunk_id 定位）

### 数据迁移

- **新建 alembic migration**：创建 big_chunks 表
- **重新索引脚本**：为现有 KB 重建索引（chunk + ingest big_chunks）
- **KB 级配置**：`big_chunk_max_chars` 字段（默认 4000，可配置）

## Impact

- **Affected specs**:
  - `chunk-baseline-v1` / `chunk-baseline-v2-iterative-deepeval`：chunking 策略变更
  - `rag-to-agent-tool-trigger`：检索结果格式变更
  - `search-docs-perf-observability`：检索链路新增大块查询阶段
- **Affected code**:
  - `backend/src/rag/chunking/hybrid_chunker.py` — 改 big_chunk_text → big_chunk_id
  - `backend/src/rag/chunking/agent_chunker.py` — 补 big_chunk_id 生成
  - `backend/src/rag/chunking/multimodal_chunker.py` — 补 big_chunk_id 生成
  - `backend/src/rag/vector_store.py` — ingest 时写 big_chunks 表 + 新增批量查询接口
  - `backend/src/rag/kb_manager.py` — 检索后查大块表 + 去重
  - `backend/src/agent/tools/groups/retrieval/search_docs.py` — content 用大块 + source 带 big_chunk_id
  - `backend/app/db/models.py` — 新增 BigChunk model
  - `backend/app/api/kb_routes.py` — 新增 /api/kb/big-chunks/{big_chunk_id} 接口（如需）
  - `frontend/src/types/session.ts` — SourceRef 加 big_chunk_id
  - `frontend/src/stores/useChatStore.ts` — source 事件处理
  - `frontend/src/components/layout/RightPanel.tsx` — 显示大块 + 高亮小块

## ADDED Requirements

### Requirement: Big Chunks 独立存储表

系统 SHALL 提供独立的 `big_chunks` 表存储大块文本，避免在每个小块 metadata 中冗余存储。

#### Scenario: 上传文件时自动生成大块
- **WHEN** 用户上传文件到知识库
- **THEN** chunker 生成小块的同时生成对应的大块
- **AND** 大块写入 big_chunks 表，获得 big_chunk_id
- **AND** 小块 metadata 中存 big_chunk_id 指向大块（不存 big_chunk_text）

#### Scenario: 大块边界截断
- **WHEN** section 文本超过 big_chunk_max_chars（默认 4000）
- **THEN** 系统在 [max_chars-100, max_chars+100] 范围内寻找最近的句子/段落边界
- **AND** 在边界处截断，不切断句子
- **AND** 若找不到合适边界，回退到硬截断

#### Scenario: 大块批量查询
- **WHEN** 检索返回 N 个小块
- **THEN** 系统通过 big_chunk_id 批量查询 big_chunks 表（单次 SQL IN 查询）
- **AND** 返回 big_chunk_id → big_chunk_text 的映射

### Requirement: Parent Document 检索模式

系统 SHALL 在检索阶段实现"小块检索 + 大块返回 LLM"的 ParentDocument 模式。

#### Scenario: 检索返回大块 content
- **WHEN** Agent 调用 search_docs 工具
- **THEN** 向量搜索 + BM25 在小块上执行（保持检索精度）
- **AND** RRF 融合后 reranker 用小块 content 重排（保持重排速度）
- **AND** 重排后通过 big_chunk_id 查询大块表
- **AND** 同 big_chunk_id 去重（保留最高分小块的 score 作为大块 score）
- **AND** 返回给 LLM 的 content 是大块文本（去重后 ≤ top_k 个大块）

#### Scenario: top_k 不变
- **WHEN** 检索参数 top_k=15
- **THEN** reranker 重排 15 个小块
- **AND** 去重后可能少于 15 个大块（同 section 合并）
- **AND** LLM context 收到去重后的大块列表

### Requirement: Source 引用小块粒度 + 大块展示

系统 SHALL 保持 [srcN] 引用的小块粒度，但 source 详情展示大块文本。

#### Scenario: [srcN] 保持小块编号
- **WHEN** 同一 section 的 3 个小块都被检索到
- **THEN** source 事件发送 3 个独立的 [src1] [src2] [src3]
- **AND** 每个 source 携带各自的 small_chunk_id 和 big_chunk_id
- **AND** 每个 source 显示各自的小块 score

#### Scenario: source 详情显示大块
- **WHEN** 用户点击 [srcN]
- **THEN** RightPanel 显示对应的大块文本（完整 section 上下文）
- **AND** 高亮显示该 [srcN] 对应的命中小块区域
- **AND** 若多个 [srcN] 指向同一大块，高亮多个区域

### Requirement: 三种 Chunker 统一支持大小分块

系统 SHALL 让 HybridChunker / AgentChunker / MultimodalChunker 都支持大小分块。

#### Scenario: AgentChunker 生成大块
- **WHEN** 用户用 agent chunk_method 上传文件
- **THEN** 每个 LLM 识别的 section 生成一个 big_chunk
- **AND** section 全文（边界截断）作为大块文本
- **AND** section 内的 sub_chunk 作为小块，metadata 带 big_chunk_id

#### Scenario: MultimodalChunker 生成大块
- **WHEN** 用户用 multimodal chunk_method 上传文件
- **THEN** 每个 Vision LLM 识别的 section 生成一个 big_chunk
- **AND** section 全文（边界截断）作为大块文本
- **AND** section 内的 sub_chunk 作为小块，metadata 带 big_chunk_id

### Requirement: 数据迁移与可回滚性

系统 SHALL 提供数据迁移脚本，并保证改动可回滚。

#### Scenario: Git 提交保证可回滚
- **WHEN** 开始实施前
- **THEN** 当前所有未提交改动（reranker FP16 修复 + KB 上限移除等）git commit
- **AND** 后续每个 Task 完成后单独 commit
- **AND** 若整体失败可 `git reset --hard <pre-spec-commit>` 回滚

#### Scenario: 现有 KB 重新索引
- **WHEN** 用户首次升级到新版本
- **THEN** 系统检测到 big_chunks 表为空
- **AND** 提示用户需要重新索引现有 KB
- **AND** 提供 `scripts/reindex_with_big_chunks.py` 脚本批量重建

## MODIFIED Requirements

### Requirement: RAG 检索链路

原有：小块检索 → 小块 reranker → 小块返回 LLM → 小块展示 source

修改为：小块检索 → 小块 reranker → 大块去重 → 大块返回 LLM → 小块 [srcN] + 大块展示

### Requirement: Chunking 元数据

原有：仅 HybridChunker 的 metadata 有 small_chunk_id + big_chunk_text（截断 4000 硬切）

修改为：三种 chunker 的 metadata 都有 small_chunk_id + big_chunk_id；big_chunk_text 移到独立表，边界截断

## REMOVED Requirements

### Requirement: big_chunk_text 冗余存储

**Reason**: 每个 small chunk metadata 存一份 big_chunk_text 导致存储冗余（同 section N 个小块存 N 份大块文本）

**Migration**: big_chunk_text 从 metadata 移除，改存 big_chunks 表；小块 metadata 只存 big_chunk_id 指针；现有 KB 需重新索引
