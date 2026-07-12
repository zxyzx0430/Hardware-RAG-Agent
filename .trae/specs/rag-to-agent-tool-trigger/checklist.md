# Checklist

## 架构改造：去 pre-RAG

- [x] `chat_sse` 不再调用 `_run_rag_retrieval`，所有请求直接进 Agent 主路径
- [x] Agent 不可用时（langgraph 未装/模型不支持 tools）走纯 LLM 流式，不检索知识库
- [x] fallback 路径 yield `thinking` 事件提示「当前模型不支持工具调用，无法检索知识库」
- [-] 闲聊（「你好」）不触发 `search_docs`，无 `source` 事件 —— 待手动 curl 验证（需启动后端 + 可用模型）
- [-] 技术问题（「STM32F4 DMA」）触发 `search_docs`，有 `source` 事件 —— 待手动 curl 验证

## 工具按领域分目录（代码组织）

- [x] 新建 `backend/src/agent/tools/groups/` 目录
- [x] 创建 6 个子目录：retrieval / hardware / workbench / code / file_ops / execution
- [x] `groups/retrieval/` 含 search_docs.py, web_search.py
- [x] `groups/hardware/` 含 audit_pins.py, wiring.py
- [x] `groups/workbench/` 含 render_wiring.py, render_code.py, render_safety_report.py
- [x] `groups/code/` 含 generate_code.py
- [x] `groups/file_ops/` 含 read_file.py, write_file.py, edit_file.py（+ _permission.py 共享权限模块）
- [x] `groups/execution/` 含 run_command.py
- [x] 每个子目录有 `__init__.py` 导出工具类
- [x] 旧文件（wrappers.py / workbench_tools.py / file_ops.py / run_command.py 等）改为 re-export shim（向后兼容）
- [x] `python -c "from src.agent.tools.groups.retrieval import SearchDocsTool, WebSearchTool"` 等所有导入无报错
- [x] 新增工具只需在对应领域子目录创建文件 + __init__.py 导出
- [x] 新建领域只需新建子目录 + 在 build_tools 追加实例化

## build_tools 全量注入（不变）

- [x] `build_tools(payload)` 仍然返回全部 12 个工具实例
- [x] 从 `tools.groups.*` 导入所有工具类
- [x] Agent 创建时绑定全部 12 个工具（Agent 自己决定调哪个）
- [x] 工具名与之前一致（search_docs / audit_pins / wiring / web_search / generate_code / render_wiring / render_code / render_safety_report / read_file / write_file / edit_file / run_command）

## SearchDocsTool 改造

- [x] `SearchDocsTool._arun` 内部无 `_rewrite_query_for_rag` 调用
- [x] `_format_search_output` 返回的 `results` 数组每个元素含完整 source 元数据（id/title/chunk_index/page_start/page_end/section_title/source_url/category/chunk_method/kb_id/kb_name/small_chunk_id/citation）
- [x] citation 构建逻辑与原 `chat_helpers._build_source_event` 一致（迁移为 build_source_event_from_dict）

## 检索性能优化

- [x] `search_all_enabled` 用 `asyncio.gather` 并行检索每个 KB（总耗时 ≈ 最慢 KB）
- [x] LRU 缓存 key = `(query, tuple(sorted(kb_ids)), top_k, threshold)`
- [x] 缓存 TTL 5 分钟，容量 256 条
- [x] 缓存命中时直接返回结果，不执行实际检索
- [x] 缓存过期时重新检索并更新

## sse_adapter source 事件

- [x] sse_adapter 检测 `tool_name == "search_docs"` 时拦截
- [x] 解析工具返回值的 `results` 数组，对每个 result yield 一个 `source` 事件
- [x] `source` 事件格式与 pre-RAG 路径一致（前端无需改前端组件）
- [x] `source` 事件在 `tool` 事件之前 yield（先看卡片再看摘要）

## Agent system prompt（参考 Claude Code 工具 prompt 设计）

- [x] `SYSTEM_PROMPT` 含「知识库检索策略」章节（含 IMPORTANT 强调 + good-example/bad-example）
- [x] `SYSTEM_PROMPT` 含「工具使用策略」章节（按 6 个领域分组描述所有工具：retrieval / hardware / workbench / code / file_ops / execution）
- [x] `SYSTEM_PROMPT` 含「工具选择规则」章节（6 条规则）
- [x] 指导 Agent：技术问题先调 search_docs，闲聊不调
- [x] 指导 Agent：调用时传精炼检索词（芯片型号 + 外设名 + 协议名）
- [x] 指导 Agent：检索结果未覆盖时声明「知识库未找到相关文档」
- [x] 指导 Agent：优先使用高层封装工具，低层工具仅在高层无法满足时使用
- [x] Agent 根据 prompt 自己决定调用什么工具，后端不做意图分类

## 废弃代码清理

- [x] `chat_helpers.py` 删除 `_run_rag_retrieval`
- [x] `chat_helpers.py` 删除 `_rewrite_query_for_rag` / `_get_cached_rewrite` / `_set_cached_rewrite` / `_QUERY_REWRITE_SYSTEM` / `_REWRITE_CACHE*`
- [x] `_build_source_event` 迁移到 `groups/retrieval/search_docs.py`（build_source_event_from_dict），chat_helpers 保留 thin shim；`_build_rag_context` 保留待后续清理
- [x] `grep -r "_run_rag_retrieval\|_rewrite_query_for_rag" backend/` 无结果（仅注释提及）

## 端到端验证

- [x] `npx tsc --noEmit` 通过
- [x] 后端 `python -c "from app.api.chat_routes import router"` 导入无报错（12 工具 + SYSTEM_PROMPT 1523 字符）
- [-] curl「你好」：无 source 事件，Agent 直接回复（Agent 自己判断为闲聊）—— 待手动验证
- [-] curl「STM32F4 DMA 配置」：有 source 事件 + tool 事件，Agent 基于检索结果回答 —— 待手动验证
- [-] curl「ESP32-S3 GPIO4 GPIO5 冲突」：Agent 自己决定调 audit_pins —— 待手动验证
- [-] curl「查 STM32F4 DMA 并生成代码」：Agent 自己决定调 search_docs + generate_code —— 待手动验证
- [-] 检索耗时从 90s 降到 5-8s（技术问题）—— 待手动验证（静态：并行 1.01s vs 串行 3s，缓存命中 0.0009s）
- [-] 闲聊耗时 ≈ 0s（不检索）—— 待手动验证
- [x] `docs/pitfalls.md` 追加双重 RAG 踩坑记录
