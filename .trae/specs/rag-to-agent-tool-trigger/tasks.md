# Tasks

- [ ] Task 1: 工具按领域分目录重组（代码组织，全量注入不变）
  - [ ] SubTask 1.1: 新建 `backend/src/agent/tools/groups/` 目录，创建 6 个子目录（retrieval / hardware / workbench / code / file_ops / execution）
  - [ ] SubTask 1.2: 将 `wrappers.py` 中的 SearchDocsTool 迁移到 `groups/retrieval/search_docs.py`
  - [ ] SubTask 1.3: 将 `web_search.py` 迁移到 `groups/retrieval/web_search.py`
  - [ ] SubTask 1.4: 将 `wrappers.py` 中的 AuditPinsTool 迁移到 `groups/hardware/audit_pins.py`
  - [ ] SubTask 1.5: 将 `wrappers.py` 中的 WiringTool 迁移到 `groups/hardware/wiring.py`
  - [ ] SubTask 1.6: 将 `generate_code.py` 迁移到 `groups/code/generate_code.py`
  - [ ] SubTask 1.7: 将 `workbench_tools.py` 中的 RenderWiringTool 迁移到 `groups/workbench/render_wiring.py`
  - [ ] SubTask 1.8: 将 `workbench_tools.py` 中的 RenderCodeTool 迁移到 `groups/workbench/render_code.py`
  - [ ] SubTask 1.9: 将 `workbench_tools.py` 中的 RenderSafetyReportTool 迁移到 `groups/workbench/render_safety_report.py`
  - [ ] SubTask 1.10: 将 `file_ops.py` 中的 ReadFileTool/WriteFileTool/EditFileTool 迁移到 `groups/file_ops/`（3 个文件）
  - [ ] SubTask 1.11: 将 `run_command.py` 迁移到 `groups/execution/run_command.py`
  - [ ] SubTask 1.12: 每个子目录建 `__init__.py` 导出工具类
  - [ ] SubTask 1.13: 保留 `wrappers.py` / `workbench_tools.py` / `file_ops.py` / `run_command.py` / `web_search.py` / `generate_code.py` 作为 re-export shim（向后兼容），或直接删除并更新所有 import
  - [ ] 验证：`python -c "from src.agent.tools.groups.retrieval import SearchDocsTool, WebSearchTool"` 等所有导入无报错

- [ ] Task 2: chat_sse 去掉 pre-RAG，直接进 Agent 主路径
  - [ ] SubTask 2.1: `backend/app/api/chat_routes.py` 的 `chat_sse` 删除 `_run_rag_retrieval` 调用与 `rag_context` 注入
  - [ ] SubTask 2.2: Agent 主路径改为默认路径（不再 try-fallback 语义），Agent 不可用时走纯 LLM 流式
  - [ ] SubTask 2.3: fallback 路径 yield 一个 `thinking` 事件提示「当前模型不支持工具调用，无法检索知识库」
  - [ ] 验证：curl 发「你好」到 Agent 可用模型，确认不触发 search_docs；发「STM32F4 DMA」确认触发

- [x] Task 3: SearchDocsTool 内部去掉 query rewrite，返回值扩展 source 元数据
  - [x] SubTask 3.1: `groups/retrieval/search_docs.py` 的 `SearchDocsTool._arun` 确认无 `_rewrite_query_for_rag` 调用
        原实现直接调用 `search_docs_core(query, ...)`，从未引用 `_rewrite_query_for_rag`；grep 确认无残留
  - [x] SubTask 3.2: `_format_search_output` 扩展 `results` 数组，每个 result 含完整 source 元数据（id/title/chunk_index/page_start/page_end/section_title/source_url/category/chunk_method/kb_id/kb_name/small_chunk_id/citation）
        `_simplify_chunks` 重构为 `_build_result_dicts` + `_build_result_entry`，从 FusedResult.metadata 映射全部 15 个字段；citation 由 `_build_citation` 拼接
  - [x] SubTask 3.3: 复用 `chat_helpers._build_source_event` 的 citation 构建逻辑（迁移到 `groups/retrieval/search_docs.py` 或共享 utils）
        新增模块级 `build_source_event_from_dict(res, i)` + `_build_citation` + `_classify_relevance`；chat_helpers.`_build_source_event` 改为 thin shim 转发（保留向后兼容），`_build_rag_context` 保留待 Task 8 处理
  - [x] 验证：单元测试 `SearchDocsTool._arun` 返回值含所有 source 字段
        `_build_result_entry` 输出 15 字段全 present；`build_source_event_from_dict` 输出 18 字段全 present（含 score_percentage/relevance_level/excerpt）；citation="硬件手册库 / STM32F4 手册 / DMA / p12"，relevance_level="high"

- [x] Task 4: search_docs_core / search_all_enabled 改并行检索 + LRU 缓存
  - [x] SubTask 4.1: 定位 `search_all_enabled` 实现（`backend/src/rag/search.py` 或 `kb_manager.py`）
        实现在 kb_manager.py:858（KnowledgeBaseManager.search_all_enabled），原用 ThreadPoolExecutor 并行；search_docs_core 在 search.py:16 调用它
  - [x] SubTask 4.2: 串行 for 循环改为 `asyncio.gather`（每个 KB 一个 task，`to_thread` 包装同步检索）
        kb_manager.py: search_all_enabled 改为 async def，ThreadPoolExecutor → asyncio.gather + asyncio.to_thread(self.search, ...)，return_exceptions=True 容错
  - [x] SubTask 4.3: 新增模块级 `_SEARCH_CACHE: dict[str, tuple[list, float]]`，TTL 5 分钟，LRU 256 条
        search.py 新增 _SEARCH_CACHE (OrderedDict) + _CACHE_TTL_SECONDS=300 + _CACHE_MAX_SIZE=256 + _get_cached/_set_cached（move_to_end LRU + popitem 淘汰）
  - [x] SubTask 4.4: 缓存 key = `(query, tuple(sorted(kb_ids)), top_k, threshold)`，命中则直接返回
        _cache_key 函数实现，kb_ids sorted 保证顺序无关；search_docs_core 开头检查缓存命中直接 return，检索后 _set_cached 写入
  - [x] 验证：mock 3 个 KB，确认并行执行（总耗时 ≈ 最慢 KB）；相同 query 二次调用命中缓存
        tests/test_task4_parallel_cache.py: 并行 3KB×1s=1.01s（非3s）；缓存冷查0.52s→命中0.0009s；LRU 淘汰+key顺序无关均通过
        调用方同步更新：search_docs.py(_run→asyncio.run, _arun→await)、tool_router.py(await)、chat_helpers.py(pre-RAG已被Task2/8删除无需改)

- [x] Task 5: sse_adapter 拦截 search_docs 工具调用，实时发 source 事件
  - [x] SubTask 5.1: `backend/src/agent/sse_adapter.py` 在 `tool` 事件 yield 之前，检测 `tool_name == "search_docs"`
        `_convert_tool_message_to_sse` 在构造 tool_result 事件前判断 `tool_name == "search_docs"`
  - [x] SubTask 5.2: 若是 search_docs，解析工具返回值的 `results` 数组，对每个 result yield 一个 `source` 事件（格式与 chat_helpers._build_source_event 一致）
        新增 `_build_source_events(result)` 辅助函数，复用 `build_source_event_from_dict` 生成 source payload；`_try_parse_structured_content` 扩展为识别 `"results"` 字段，让 search_docs 的 JSON-序列化返回值能被解析为 dict
  - [x] SubTask 5.3: source 事件在 tool 事件之前 yield（前端先看到引用卡片，再看到工具调用摘要）
        `events.extend(_build_source_events(result))` 先于 `events.append(sse_event("tool_result", ...))`，最后 `"\n".join(events)` 返回
  - [x] 验证：Agent 调 search_docs 后，前端实时看到 source 卡片 + tool 摘要
        `_build_source_events` 单元测试通过：2 个 results → 2 个 source SSE 事件，type="source"，含 id/citation/relevance_level；空 results / 非 dict 输入返回空列表

- [x] Task 6: Agent system prompt 新增检索策略 + 工具使用策略（参考 Claude Code）
  - [x] SubTask 6.1: `backend/src/agent/prompts.py` 的 `SYSTEM_PROMPT` 新增「知识库检索策略」章节（见 spec.md MODIFIED Requirements），含 IMPORTANT 强调 + good-example/bad-example
  - [x] SubTask 6.2: `SYSTEM_PROMPT` 新增「工具使用策略」章节，按 6 个领域分组描述所有工具（retrieval / hardware / workbench / code / file_ops / execution）
  - [x] SubTask 6.3: `SYSTEM_PROMPT` 新增「工具选择规则」章节（6 条规则 + 优先高层工具的 IMPORTANT 强调）
  - [x] SubTask 6.4: 指导 Agent：技术问题先调 search_docs，闲聊不调，调用时传精炼检索词（含在「知识库检索策略」3 条 IMPORTANT + good/bad-example 中）
  - [x] 验证：prompt 含 3 新章节 + 6 领域关键词 + 4 处 IMPORTANT + good/bad-example，长度 1523 字符

- [x] Task 7: build_tools 从新目录导入工具（全量注入不变）
  - [x] SubTask 7.1: `backend/src/agent/agent_factory.py` 的 `build_tools` 改为从 `tools.groups.*` 导入所有工具类
  - [x] SubTask 7.2: 保持全量实例化（12 个工具全部绑定到 LLM，Agent 自己决定调哪个）
  - [x] SubTask 7.3: 删除旧的 `from src.agent.tools.wrappers import ...` / `from src.agent.tools.workbench_tools import ...` 等导入
  - [x] 验证：`build_tools(payload)` 返回 12 个工具实例，工具名与之前一致

- [x] Task 8: 废弃 chat_helpers 中的 pre-RAG 函数
  - [x] SubTask 8.1: 删除 `_run_rag_retrieval`（已无调用方）
  - [x] SubTask 8.2: 删除 `_rewrite_query_for_rag` / `_get_cached_rewrite` / `_set_cached_rewrite` / `_QUERY_REWRITE_SYSTEM` / `_REWRITE_CACHE*`
  - [x] SubTask 8.3: 保留 `_build_source_event` / `_build_rag_context` 若被其他地方引用，否则迁移到 `groups/retrieval/search_docs.py` 并从 chat_helpers 删除（Task 3 已迁移 _build_source_event 到 search_docs.py 的 build_source_event_from_dict，chat_helpers 保留 thin shim）
  - [x] SubTask 8.4: 保留 `_process_attachments` / `_build_chat_history` / `_build_system_prompt` / `_record_token_usage` / `_classify_relevance`（仍被使用）
  - [x] 验证：`grep -r "_run_rag_retrieval\|_rewrite_query_for_rag" backend/` 无结果（仅注释提及）

- [x] Task 9: 端到端验证 + 性能基线
  - [x] SubTask 9.1: `npx tsc --noEmit` 通过（前端无类型错误）
  - [x] SubTask 9.2: 后端 `python -c "from app.api.chat_routes import router"` 导入无报错（12 工具 + SYSTEM_PROMPT 1523 字符全部 OK）
  - [-] SubTask 9.3: curl 发「你好」确认不触发 search_docs（无 source 事件）—— 待手动验证（需启动后端 + 配置可用模型）
  - [-] SubTask 9.4: curl 发「STM32F4 DMA 配置」确认触发 search_docs + source 事件，记录检索耗时 —— 待手动验证
  - [-] SubTask 9.5: curl 发「ESP32-S3 GPIO4 GPIO5 冲突」确认 Agent 自己决定调 audit_pins —— 待手动验证
  - [-] SubTask 9.6: curl 发「查 STM32F4 DMA 并生成代码」确认 Agent 自己决定调 search_docs + generate_code —— 待手动验证
  - [-] SubTask 9.7: 对比改造前后检索耗时（目标：技术问题从 90s 降到 5-8s）—— 待手动验证（静态验证：并行 1.01s vs 串行 3s，缓存命中 0.0009s）
  - [x] SubTask 9.8: 更新 `docs/pitfalls.md` 记录双重 RAG 问题与修复

# Task Dependencies

- Task 1（工具目录重组）独立，最先做
- Task 2（chat_sse 去 pre-RAG）独立，可与 Task 1 并行
- Task 3（SearchDocsTool 改造）依赖 Task 1（工具已迁移到新目录）
- Task 4（并行检索+缓存）独立，可与 Task 1-2 并行
- Task 5（sse_adapter source 事件）依赖 Task 3（需要返回值格式定义）
- Task 6（system prompt）独立，可与 Task 1-2 并行
- Task 7（build_tools 导入更新）依赖 Task 1
- Task 8（废弃 pre-RAG）依赖 Task 2
- Task 9（端到端验证）依赖全部完成
