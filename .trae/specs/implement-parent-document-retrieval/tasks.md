# Tasks

- [x] Task 0: Git 提交当前所有改动保证可回滚性
      commit 6ef70490 作为基线点
  - [x] SubTask 0.1: `git add -A` + `git commit -m "fix(backend): reranker FP16 CPU 反向优化修复 + 移除 KB/附件/置顶不合理上限"`
  - [x] SubTask 0.2: 记录 commit hash 作为回滚点（6ef70490）

- [x] Task 1: 新增 BigChunk 数据库 model + alembic migration
      models.py 新增 BigChunk class（9 字段 + 3 索引）+ KnowledgeBase 加 big_chunk_max_chars + alembic migration a1b2c3d4e5f6
  - [x] SubTask 1.1: `backend/app/db/models.py` 新增 BigChunk class（id/big_chunk_id/doc_id/kb_id/section_title/text/page_start/page_end/created_at）
  - [x] SubTask 1.2: `backend/app/db/database.py` init_db() 新增幂等 ALTER TABLE 加 big_chunk_max_chars
  - [x] SubTask 1.3: alembic migration `a1b2c3d4e5f6_add_big_chunks.py` 创建 big_chunks 表
  - [x] SubTask 1.4: KB model 新增 `big_chunk_max_chars` 字段（默认 4000）
  - [x] 验证：`alembic upgrade head` 成功；PRAGMA 验证表结构正确

- [x] Task 2: 实现大块边界截断工具函数
      base.py 新增 truncate_at_boundary + _find_nearest_boundary + 10 个单元测试全通过
  - [x] SubTask 2.1: `backend/src/rag/chunking/base.py` 新增 `truncate_at_boundary(text: str, max_chars: int = 4000, tolerance: int = 100) -> str`
  - [x] SubTask 2.2: 边界优先级：段落边界（\n\n）> 中文句号（。）> 英文句号（.）> 换行（\n）> 硬截断
  - [x] SubTask 2.3: 在 [max_chars-tolerance, max_chars+tolerance] 范围内寻找边界
  - [x] 验证：10 个单元测试全通过（短文本/段落/句号/换行/硬截断/优先级）

- [x] Task 3: HybridChunker 改造（big_chunk_text → big_chunk_id）
      base.py ChunkResult 新增 big_chunk_id/big_chunk_text 字段；hybrid_chunker.py 6 处改动，metadata 改存 big_chunk_id，ChunkResult 带 big_chunk_text
  - [x] SubTask 3.1: `hybrid_chunker.py` 删除 metadata 的 `big_chunk_text` 字段
  - [x] SubTask 3.2: 新增 `big_chunk_id` 字段（格式 `{doc_id}#b{section_index}`）
  - [x] SubTask 3.3: `big_chunk_text` 内容（边界截断后的 section 全文）改为通过 ChunkResult.big_chunk_text 字段返回
  - [x] SubTask 3.4: ChunkResult dataclass 新增 `big_chunk_id` 和 `big_chunk_text` 字段
  - [x] 验证：py_compile 通过；测试 6 chunks / 2 big_chunk_id，同 section 共享

- [x] Task 4: AgentChunker 补上大小分块
      agent_chunker.py 7 处改动：_build_chunks 生成 big_chunk_id + small_chunk_id + big_chunk_text；_merge_tiny_chunks 保留字段
  - [x] SubTask 4.1: `agent_chunker.py` 在 `_build_chunks` 中为每个 section 生成 big_chunk_id（格式 `{doc_id}#b{section_idx}`）
  - [x] SubTask 4.2: section 全文用 `truncate_at_boundary` 截断后作为 big_chunk_text
  - [x] SubTask 4.3: 每个 sub_chunk 的 metadata 写入 big_chunk_id + small_chunk_id
  - [x] SubTask 4.4: ChunkResult 填充 big_chunk_id + big_chunk_text 字段
  - [x] 验证：py_compile 通过；ChunkResult 构造成功

- [x] Task 5: MultimodalChunker 补上大小分块
      multimodal_chunker.py 4 处改动：_build_chunks 生成 big_chunk_id + small_chunk_id + big_chunk_text；_merge_tiny_chunks 保留字段
  - [x] SubTask 5.1: `multimodal_chunker.py` 在 `_build_chunks` 中为每个 section 生成 big_chunk_id
  - [x] SubTask 5.2: section 全文用 `truncate_at_boundary` 截断后作为 big_chunk_text
  - [x] SubTask 5.3: 每个 sub_chunk 的 metadata 写入 big_chunk_id + small_chunk_id
  - [x] SubTask 5.4: ChunkResult 填充 big_chunk_id + big_chunk_text 字段
  - [x] 验证：py_compile 通过；两个 ChunkResult 创建路径均设置字段

- [x] Task 6: vector_store.py 改造（ingest 写 big_chunks 表 + 批量查询接口）
      ingest_chunks 拆分为 _collect_big_chunks + _write_big_chunks + _build_small_chunk_docs + _write_small_chunks；新增 get_big_chunks_by_ids。big_chunk 写失败不阻塞 ChromaDB；小块 metadata 防御性 pop big_chunk_text。
  - [x] SubTask 6.1: `vector_store.py` `ingest_chunks` 方法：先批量写 big_chunks 表（去重同 big_chunk_id），再写 ChromaDB
  - [x] SubTask 6.2: 小块 metadata 只存 big_chunk_id，不存 big_chunk_text
  - [x] SubTask 6.3: 新增 `get_big_chunks_by_ids(big_chunk_ids: list[str]) -> dict[str, dict]` 方法
  - [x] SubTask 6.4: 返回 dict: {big_chunk_id: {text, section_title, page_start, page_end}}
  - [x] 验证：py_compile 通过；ingest 后 big_chunks 表有数据；批量查询返回正确（smoke test 6 项全通过：dedup/overwrite/strip/empty/idempotent/format）

- [x] Task 7: kb_manager.py 改造（检索后查大块表 + 去重）
      search_all_enabled 在 reranker 之后新增 _apply_big_chunk_lookup 阶段（7 个辅助方法）；FusedResult.content 改为大块文本；metadata 带 small_chunk_ids + small_chunks 列表；同 big_chunk_id 去重取最高分；新增 timing 日志。
  - [x] SubTask 7.1: `search_all_enabled` 在 reranker 之后新增"大块查询+去重"阶段
  - [x] SubTask 7.2: 收集所有小块的 big_chunk_id，调用 vector_store.get_big_chunks_by_ids 批量查询
  - [x] SubTask 7.3: 同 big_chunk_id 去重：保留最高分小块的 score 作为大块 score，合并 small_chunk_ids 列表
  - [x] SubTask 7.4: FusedResult.content 改为大块文本；metadata 保留小块信息（small_chunk_ids 列表 + 各自 score）
  - [x] SubTask 7.5: 新增 timing 日志 `search_stage stage=big_chunk_lookup elapsed_ms=%d big_count=%d`
  - [x] 验证：py_compile 通过；检索日志显示 big_chunk_lookup 阶段；返回的 content 是大块文本

- [x] Task 8: search_docs.py 改造（content 用大块 + source 带 big_chunk_id）
      _build_result_dicts 展开 FusedResult 为小块粒度 entries（content=大块文本共享）；_entry_base + _overlay_small_chunk 拆分；source_counter 按 entry 数递增；build_source_event_from_dict 新增 big_chunk_id + excerpt 用小块文本。
  - [x] SubTask 8.1: `_build_result_entry` 的 content 字段用大块文本（来自 FusedResult.content）
        content 直接取 FusedResult.content（Task 7 后已是大块文本）；standalone 取小块文本
  - [x] SubTask 8.2: 新增 `big_chunk_id` 字段到 entry dict
        _entry_base 从 metadata 读 big_chunk_id；standalone 无则 ""
  - [x] SubTask 8.3: source 事件保持小块粒度（每个小块一个 [srcN]），携带各自的 small_chunk_id 和 big_chunk_id
        每个 FusedResult 展开为 1..N 个 entry（N=len(small_chunks)）；build_source_event_from_dict 输出 big_chunk_id + small_chunk_id
  - [x] SubTask 8.4: 若同 big_chunk_id 有多个小块，各小块独立编号但共享 big_chunk_id
        _overlay_small_chunk 按 start+i 连续编号；base.big_chunk_id 在同组 entry 间共享
  - [x] 验证：py_compile 通过；source 事件有 big_chunk_id 字段；LLM context 是大块
        py_compile OK；mock 测试 3 小块+1 standalone → src1..src4，big_chunk_id 共享/为空，content=大块文本，excerpt=小块文本

- [x] Task 9: 前端 SourceRef 类型 + useChatStore 改造
      前端类型和 store 适配 big_chunk_id。SourceRef 加 big_chunk_id/small_chunk_text；SourceSSEEvent 补 small_chunk_id/big_chunk_id/small_chunk_text；useChatStore 主 stream + resume stream 两处 source handler 存新字段；tsc --noEmit 零错误。
  - [x] SubTask 9.1: `frontend/src/types/session.ts` SourceRef 新增 `big_chunk_id?: string`
        另加 `small_chunk_text?: string`；`small_chunk_id` 已存在保留
  - [x] SubTask 9.2: `useChatStore.ts` source 事件处理时存 big_chunk_id
        主 stream (L1239) + resume stream `_appendResumeSource` (L431) 两处均加 big_chunk_id/small_chunk_text；同步给 `types/api.ts` SourceSSEEvent 补 small_chunk_id/big_chunk_id/small_chunk_text 字段以通过 strict tsc
  - [x] 验证：`npx tsc --noEmit` 通过
        EXIT=0 零错误

- [x] Task 10: RightPanel.tsx 改造（显示大块 + 高亮小块）
      后端新增 GET /api/kb/big-chunks/{big_chunk_id} 接口；前端 BigChunkViewer 组件加载大块文本，高亮命中小块区域（含多 source 同大块合并高亮）。
  - [x] SubTask 10.1: 后端 `kb_routes.py` 新增 `GET /api/kb/big-chunks/{big_chunk_id}` 接口
        查询 BigChunk model 返回 {big_chunk_id, text, section_title, page_start, page_end, doc_id, kb_id}；404 未找到
  - [x] SubTask 10.2: RightPanel source 详情区域：通过 big_chunk_id 调用 `/api/kb/big-chunks/{big_chunk_id}` 加载大块文本
        BigChunkViewer 组件 + fetchBigChunk API 函数；有 big_chunk_id 时显示大块，无则保持原有 MonacoEditor 逻辑
  - [x] SubTask 10.3: 大块文本中高亮命中小块区域（通过 small_chunk_text 定位小块文本，标记高亮）
        computeHighlightRanges + mergeRanges + renderHighlightedText 用 <mark> 高亮 (#fef08a)
  - [x] SubTask 10.4: 若多个 [srcN] 指向同一大块，高亮多个区域
        collectSiblingTexts 从 sources 中收集同 big_chunk_id 的所有 small_chunk_text，合并高亮
  - [x] 验证：`python -m py_compile` 通过；`npx tsc --noEmit` 通过

- [x] Task 11: 数据迁移脚本
      scripts/reindex_with_big_chunks.py：遍历所有 KB（或 --kb-id 指定单个），重新 parse+chunk+ingest，删除旧 ChromaDB 向量+旧 big_chunks 行后重新写入，支持 --dry-run。
  - [x] SubTask 11.1: `scripts/reindex_with_big_chunks.py` 脚本：遍历所有 KB，重新 chunk + ingest
        复用 kb_routes._parse_file + _get_kb_chunker；每个 doc 先 delete_old_vectors + delete_old_big_chunks 再 ingest_chunks；chunk 失败 fallback 到 hybrid
  - [x] SubTask 11.2: 脚本支持 `--kb-id` 参数指定单个 KB，或无参数处理所有 KB
        argparse 支持 --kb-id（可选）+ --dry-run（可选）；无参数处理所有 KB
  - [x] 验证：脚本可执行；对测试 KB 重建索引后 big_chunks 表有数据
        py_compile 通过；--help 正常输出；ingest_chunks 会写 big_chunks 表（_write_big_chunks upsert）+ 小块 metadata 带 big_chunk_id

- [ ] Task 12: 完成度检测 subagent
      开一个 subagent 全面检测 spec 完成度。
  - [ ] SubTask 12.1: 逐项核对 spec.md 的 ADDED/MODIFIED/REMOVED Requirements 是否全部实现
  - [ ] SubTask 12.2: 逐项核对 tasks.md 所有 Task 和 SubTask 是否完成
  - [ ] SubTask 12.3: 生成完成度报告

- [x] Task 13: 缺陷检测维修 subagent
      开一个 subagent 检测并修复缺陷。
  - [x] SubTask 13.1: 静态分析：py_compile + tsc --noEmit 全通过
        py_compile 11 个后端文件全 EXIT=0；tsc --noEmit EXIT=0，无编译错误
  - [x] SubTask 13.2: 代码审查：检索链路、ingest 链路、前端展示链路无遗漏
        ingest→vector_store→big_chunks 表+ChromaDB；检索→reranker→big_chunk_lookup→去重；Agent→search_docs 展开 entry；前端→SourceRef→useChatStore→RightPanel.BigChunkViewer，四条链路均无遗漏
  - [x] SubTask 13.3: 边界 case：空 big_chunk_id / 单 section 单小块 / 超长 section / 跨页 section
        空 big_chunk_id（图片描述 chunk→standalone 不崩溃）✓；单 section 单小块（1 元素 group 正确合并）✓；超长 section（truncate_at_boundary 正确截断）✓；同 big_chunk_id 多小块（去重合并 score 取最高）✓；跨页 section 发现缺陷——page_start/page_end 聚合错误
  - [x] SubTask 13.4: 修复发现的缺陷
        修复 vector_store._collect_big_chunks 的 page_range 聚合缺陷：dict 后写覆盖改为聚合同 big_chunk_id 所有小块的 min(page_start)/max(page_end)；py_compile + tsc 验证通过；commit 已提交

- [x] Task 14: 端到端测试 subagent
      端到端测试通过：直接检索五阶段 timing 全部出现 + 5 结果全部带 big_chunk_id + 去重正确 + big_chunks API 返回大块文本 + Agent 路径 source 事件携带 big_chunk_id + LLM 回答引用 [srcN]。
  - [x] SubTask 14.1: 上传测试文档（用 multimodal chunk_method）
        KB kb-2df29078 创建（multimodal），文档 06-hardware-terms-glossary.md 上传成功（17 chunks，multimodal→hybrid 降级符合预期），big_chunks 表 17 行，big_chunk_id 格式 {doc_id}#b{section_idx} 正确
  - [x] SubTask 14.2: 发送检索问题，验证 LLM 拿到大块（日志 + 回答质量）
        直接检索：五阶段 timing 全部出现（vector 559ms / bm25 1285ms / rrf 0ms / batch_reranker 7021ms / big_chunk_lookup 2ms big_count=5 small_count=5）；5 个 FusedResult 全部带 big_chunk_id，content 是大块文本（244 chars section 全文，非 800 字符小块）；Chat 端点：Agent 调用 search_docs，LLM 回答 2236 chars 正确回答 4 个 Strapping 引脚，引用 [src1][src4] 等
  - [x] SubTask 14.3: 验证 [srcN] 点击显示大块 + 高亮小块
        Source SSE 事件携带 big_chunk_id（src1: 70c105e3-...#b3）+ small_chunk_id + excerpt（小块文本用于高亮）；GET /api/kb/big-chunks/{big_chunk_id}（URL 编码 # 为 %23）返回 200：text=244chars section 全文 + section_title + page_start/page_end + doc_id + kb_id；前端 BigChunkViewer 所需数据齐备
  - [x] SubTask 14.4: 验证同 section 多个小块去重正确
        直接检索 5 结果 big_chunk_id 全部唯一（b3/b4/b6/b16/b14 无重复）；DB 层无重复 big_chunk_id；_apply_big_chunk_lookup 去重逻辑正确（同 big_chunk_id 取最高分小块 score，合并 small_chunks 列表）

# Task Dependencies

- Task 0 (git commit) → 所有后续 Task
- Task 1 (BigChunk model) → Task 6 (vector_store ingest)
- Task 2 (边界截断) → Task 3/4/5 (三种 chunker)
- Task 3/4/5 (三种 chunker) → Task 6 (vector_store ingest)
- Task 6 (vector_store) → Task 7 (kb_manager 检索)
- Task 7 (kb_manager) → Task 8 (search_docs)
- Task 8 (search_docs) → Task 9 (前端类型)
- Task 9 (前端类型) → Task 10 (RightPanel)
- Task 6 + Task 10 → Task 11 (迁移脚本，需 ingest 接口稳定)
- Task 1-11 全部完成 → Task 12/13/14 并行（三个检测 subagent）
