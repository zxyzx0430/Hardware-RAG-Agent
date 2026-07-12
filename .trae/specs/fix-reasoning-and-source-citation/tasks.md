# Tasks

- [x] Task 1: 后端 reasoning 多 provider 字段兼容
  - [x] SubTask 1.1: reasoning_chat.py 新增 `_REASONING_FIELDS: tuple[str, ...] = ("reasoning_content", "thinking", "reasoning")` 常量
  - [x] SubTask 1.2: `_extract_reasoning` 改为遍历 `_REASONING_FIELDS`，返回首个非空 str 值
  - [x] SubTask 1.3: 验证 `python -c "from src.agent.reasoning_chat import _extract_reasoning; ..."` 无报错

- [x] Task 2: 后端 SYSTEM_PROMPT 强化 [srcN] + 删除意图说明章节
  - [x] SubTask 2.1: prompts.py SYSTEM_PROMPT "回答规范" 章节的 [srcN] bullet 删除，新增独立"来源引用规范（必须遵守）"章节（含 good/bad 示例 + 4 条规则）
  - [x] SubTask 2.2: prompts.py SYSTEM_PROMPT 末尾删除"工具调用前的意图说明"章节
  - [x] SubTask 2.3: 验证 `python -c "from src.agent.prompts import SYSTEM_PROMPT; print('来源引用规范' in SYSTEM_PROMPT); print('工具调用前的意图说明' not in SYSTEM_PROMPT)"` 输出 True True

- [x] Task 3: 后端 search_docs summary 加引用提示
  - [x] SubTask 3.1: search_docs.py `_build_search_summary` 末尾加一行 "回答时必须用 [srcN] 格式引用上述片段，N 对应 src1/src2/..."
  - [x] SubTask 3.2: 验证 summary 输出含 "回答时必须用 [srcN]"

- [x] Task 4: 后端删除 placeholder thinking 逻辑
  - [x] SubTask 4.1: sse_adapter.py 删除 `PLACEHOLDER_THINKING` 常量
  - [x] SubTask 4.2: sse_adapter.py 删除 `_maybe_emit_placeholder_thinking` 函数
  - [x] SubTask 4.3: sse_adapter.py `_emit_tool_calls_from_message` 删除对 `_maybe_emit_placeholder_thinking` 的调用
  - [x] SubTask 4.4: sse_adapter.py state dict 删除 `reasoning_step_open` 字段 + 所有对它的引用
  - [x] SubTask 4.5: 验证 import 无报错 + grep 确认无 PLACEHOLDER_THINKING 残留

- [x] Task 5: 端到端验证
  - [x] SubTask 5.1: 后端 Python import 检查通过（exit 0）
  - [x] SubTask 5.2: grep 确认 `_REASONING_FIELDS` 含 3 个字段名
  - [x] SubTask 5.3: grep 确认 SYSTEM_PROMPT 含"来源引用规范" + 不含"工具调用前的意图说明"
  - [x] SubTask 5.4: grep 确认 sse_adapter 无 PLACEHOLDER_THINKING / _maybe_emit_placeholder_thinking / reasoning_step_open 残留
  - [x] SubTask 5.5: grep 确认 search_docs summary 含"回答时必须用 [srcN]"

# Task Dependencies

- Task 1/2/3/4 后端全可并行（不同文件 / 同文件不同函数）
- Task 5 依赖 Task 1-4 全部完成
