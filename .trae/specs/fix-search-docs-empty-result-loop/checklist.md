# 修复 search_docs 不相关结果死循环 Checklist

## search_docs.py 工具内部追踪连续低相关度（主要）

- [x] 新增模块常量 `_KB_COVERAGE_HINT_TRIGGER = 3`
- [x] 新增模块常量 `_KB_COVERAGE_HINT`（提示文案）
- [x] SearchDocsTool 新增 `_consecutive_low_relevance_count: int = PrivateAttr(default=0)`
- [x] 新增 `_extract_max_score(results)` 函数（返回最高 score，空结果返回 0.0）
- [x] 新增 `_append_kb_coverage_hint(output_text)` 函数（在 output 末尾追加提示）
- [x] `_arun` 调用 `_format_search_output` 后更新计数器（< 0.8 累加，>= 0.8 重置）
- [x] `_arun` 计数器 >= 3 时在 `output["output"]` 末尾追加提示
- [x] `_run` 同步路径也实现一致的计数逻辑（避免同步/异步行为分叉）
- [x] 计数器 per-request 隔离（依赖工具实例 per-request 创建，无需额外处理）

## SYSTEM_PROMPT 调用纪律追加一行（辅助）

- [x] SYSTEM_PROMPT "调用纪律" 章节追加"知识库内容可能不全"
- [x] 含"改写 3 次查询词仍然没有高度相关结果"
- [x] 含"相关度 < 80%"
- [x] 含"如实告诉用户知识库可能未覆盖该内容"
- [x] 含"基于通用知识回答或建议用户上传相关文档"
- [x] 含"不要无限改写查询"

## 非目标确认（不应做）

- [x] 未新增 LoopDetectedError 异常类（exceptions.py 未改）
- [x] 未改 sse_adapter.py（无实时检测 + raise）
- [x] 未改 loop_detector.py（无 detect_search_no_progress）
- [x] 未注入 SystemMessage 到 messages 列表
- [x] 未改 MAX_RECURSION（41 保留作兜底）
- [x] 未改相关度阈值（HIGH=0.8 / MEDIUM=0.5 保留）
- [x] 未改前端

## 端到端验证

- [x] 后端 Python import 检查通过
- [x] grep SYSTEM_PROMPT 含"知识库可能未覆盖"
- [x] grep search_docs.py 含 `_consecutive_low_relevance_count`
- [x] grep search_docs.py 含 `_extract_max_score`
- [x] grep search_docs.py 含 `_append_kb_coverage_hint`
- [x] grep search_docs.py 含 `_KB_COVERAGE_HINT_TRIGGER`
- [x] grep exceptions.py 不含 `LoopDetectedError`
- [x] grep sse_adapter.py 不含 `LoopDetectedError` / `detect_search_no_progress`
- [x] grep loop_detector.py 不含 `detect_search_no_progress`
