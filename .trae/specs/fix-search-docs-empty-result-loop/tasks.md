# Tasks

- [x] Task 1: search_docs.py 工具内部追踪连续低相关度并按需追加提示（主要修复）
  - [x] SubTask 1.1: 新增模块常量 `_KB_COVERAGE_HINT_TRIGGER: int = 3` 和 `_KB_COVERAGE_HINT: str`（提示文案）
  - [x] SubTask 1.2: SearchDocsTool 新增 `_consecutive_low_relevance_count: int = PrivateAttr(default=0)` 私有属性
  - [x] SubTask 1.3: 新增 `_extract_max_score(results: list) -> float` 辅助函数——返回 results 中最高 score（空结果返回 0.0）
  - [x] SubTask 1.4: 新增 `_append_kb_coverage_hint(output_text: str) -> str` 辅助函数——在 output 末尾追加 `_KB_COVERAGE_HINT`
  - [x] SubTask 1.5: 修改 `_arun` 方法——调用 `_format_search_output` 后，根据 max_score 更新 `_consecutive_low_relevance_count`（< 0.8 累加，>= 0.8 重置为 0），>= 3 时在 `output["output"]` 末尾追加提示
  - [x] SubTask 1.6: 同步修改 `_run` 方法（保持与 `_arun` 一致的计数逻辑，避免同步/异步路径行为分叉）

- [x] Task 2: prompts.py SYSTEM_PROMPT 调用纪律追加一行（辅助修复）
  - [x] SubTask 2.1: SYSTEM_PROMPT "调用纪律" 章节追加一行："知识库内容可能不全，并非所有硬件问题都能在手册中找到答案。如果改写 3 次查询词仍然没有高度相关结果（相关度 < 80%），请如实告诉用户'知识库可能未覆盖该内容'，基于通用知识回答或建议用户上传相关文档。不要无限改写查询。"

- [x] Task 3: 端到端验证
  - [x] SubTask 3.1: 后端 Python import 检查通过（`python -c "from src.agent.tools.groups.retrieval.search_docs import SearchDocsTool"`）
  - [x] SubTask 3.2: grep 确认 SYSTEM_PROMPT 含"知识库可能未覆盖"
  - [x] SubTask 3.3: grep 确认 search_docs.py 含 `_consecutive_low_relevance_count`
  - [x] SubTask 3.4: grep 确认 search_docs.py 含 `_extract_max_score`
  - [x] SubTask 3.5: grep 确认 search_docs.py 含 `_append_kb_coverage_hint`
  - [x] SubTask 3.6: grep 确认 search_docs.py 含 `_KB_COVERAGE_HINT_TRIGGER`
  - [x] SubTask 3.7: 确认未新增 LoopDetectedError 异常类（exceptions.py 未改）
  - [x] SubTask 3.8: 确认 sse_adapter.py 未改（无实时检测 + raise）
  - [x] SubTask 3.9: 确认 loop_detector.py 未改（无 detect_search_no_progress）

# Task Dependencies

- Task 1 和 Task 2 相互独立，可并行
- Task 3 依赖 Task 1 + Task 2 全部完成
