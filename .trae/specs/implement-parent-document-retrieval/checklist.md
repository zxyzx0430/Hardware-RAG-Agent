# Checklist

## 数据层
- [x] BigChunk SQLAlchemy model 定义正确（id/doc_id/kb_id/section_title/text/page_start/page_end/created_at）
- [x] alembic migration 脚本可成功执行（`alembic upgrade head`）
- [x] big_chunks 表在数据库中创建成功
- [x] KnowledgeBase model 新增 big_chunk_max_chars 字段（默认 4000）

## Chunking 层
- [x] `truncate_at_boundary` 工具函数实现正确（段落 > 句号 > 换行 > 硬截断）
- [x] HybridChunker 生成 big_chunk_id，metadata 不再有 big_chunk_text
- [x] AgentChunker 生成 big_chunk_id，metadata 有 big_chunk_id
- [x] MultimodalChunker 生成 big_chunk_id，metadata 有 big_chunk_id
- [x] ChunkResult dataclass 有 big_chunk_id 和 big_chunk_text 字段
- [x] 三种 chunker 的 chunk() 输出经过 truncate_at_boundary 处理

## Ingest 层
- [x] vector_store.ingest_chunks 同时写 big_chunks 表和 ChromaDB
- [x] 小块 metadata 只存 big_chunk_id，不存 big_chunk_text
- [x] 同 big_chunk_id 的大块在 big_chunks 表中只存一份（去重）
- [x] vector_store.get_big_chunks_by_ids 批量查询接口正常工作

## 检索层
- [x] kb_manager.search_all_enabled 在 reranker 后新增 big_chunk_lookup 阶段
- [x] 同 big_chunk_id 去重（保留最高分小块的 score）
- [x] FusedResult.content 是大块文本
- [x] FusedResult.metadata 保留小块信息（small_chunk_ids 列表）
- [x] timing 日志显示 big_chunk_lookup 阶段耗时
- [x] top_k 保持 15 不变

## Agent 工具层
- [x] search_docs 返回的 content 字段是大块文本
- [x] source 事件保持小块粒度（每个小块一个 [srcN]）
- [x] source 事件携带 big_chunk_id 字段
- [x] 同 big_chunk_id 的多个小块各自独立编号 [srcN]

## 前端
- [x] SourceRef 类型有 big_chunk_id 字段
- [x] useChatStore source 事件处理存 big_chunk_id
- [x] RightPanel source 详情显示大块文本
- [x] 大块文本中高亮命中小块区域
- [x] 多个 [srcN] 指向同一大块时高亮多个区域
- [x] `npx tsc --noEmit` 零错误

## 后端 API
- [x] `GET /api/kb/big-chunks/{big_chunk_id}` 接口正常返回大块文本
- [x] 接口返回字段包含 text/section_title/page_start/page_end

## 数据迁移
- [x] `scripts/reindex_with_big_chunks.py` 脚本可执行
- [x] 脚本支持 --kb-id 参数指定单个 KB
- [x] 重新索引后 big_chunks 表有数据
- [x] 重新索引后小块 metadata 有 big_chunk_id

## 可回滚性
- [x] 实施前 git commit 保存基线点
- [x] 每个 Task 完成后单独 commit
- [x] commit message 符合 conventional commits 规范

## 端到端验证
- [x] 上传测试文档（multimodal chunk_method）成功，big_chunks 表有数据
- [x] 发送检索问题，LLM 回答包含大块上下文（非 800 字符小块）
- [x] 后端日志显示四阶段 + big_chunk_lookup 五阶段 timing
- [x] [srcN] 引用保持小块粒度（同 section 3 个小块 → 3 个 [srcN]）
- [x] 点击 [srcN] 显示大块文本，命中小块区域高亮
- [x] 同 section 多个小块去重正确（LLM context 只有 1 个大块）
- [x] search_docs 性能未显著退化（big_chunk_lookup 阶段 < 500ms）

## 三个 subagent 检测
- [x] Task 12 完成度检测 subagent 报告：所有 Requirements 实现
- [x] Task 13 缺陷检测维修 subagent 报告：无未修复缺陷
- [x] Task 14 端到端测试 subagent 报告：所有场景通过
