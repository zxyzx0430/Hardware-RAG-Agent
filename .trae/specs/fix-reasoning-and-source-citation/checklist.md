# 修复 reasoning 提取与 source 引用 Checklist

## reasoning 多 provider 字段兼容

- [x] reasoning_chat.py 新增 `_REASONING_FIELDS` 常量含 3 个字段名
- [x] `_extract_reasoning` 遍历 3 字段返回首个非空 str
- [x] DeepSeek/QwQ 的 `reasoning_content` 仍正常提取（无回归，优先级第一）
- [x] GLM-5.x 的 `thinking` 字段能提取（优先级第二）
- [x] OpenAI o1 兼容的 `reasoning` 字段能提取（优先级第三）
- [x] 普通模型无 reasoning 字段时返回空字符串

## SYSTEM_PROMPT 强化 [srcN]

- [x] SYSTEM_PROMPT 含"来源引用规范（必须遵守）"独立章节
- [x] 章节含 good-example（用 [srcN]）
- [x] 章节含 bad-example（用文档名加粗）
- [x] 章节含 4 条规则（每个片段引用至少一次 / 紧跟信息后 / 不用加粗 / 闲聊不需要）
- [x] SYSTEM_PROMPT 不含"工具调用前的意图说明"章节（已删除）

## search_docs summary 引用提示

- [x] `_build_search_summary` 末尾含"回答时必须用 [srcN] 格式引用上述片段"
- [x] summary 的 [srcN] 编号与 source 卡片 ID 对齐

## 删除 placeholder thinking

- [x] sse_adapter.py 无 `PLACEHOLDER_THINKING` 常量
- [x] sse_adapter.py 无 `_maybe_emit_placeholder_thinking` 函数
- [x] sse_adapter.py 无 `reasoning_step_open` 字段（state dict + 所有引用）
- [x] `_emit_tool_calls_from_message` 不再调 placeholder 逻辑
- [x] 非推理模型不显示思考卡片（无 placeholder，source="agent" 事件不再发出）
- [x] 推理模型仍显示真实 reasoning 卡片（_append_reasoning_event 保留，source="reasoning"）

## 端到端验证

- [x] 后端 Python import 检查通过（exit 0）
- [x] grep `_REASONING_FIELDS` 含 reasoning_content / thinking / reasoning
- [x] grep SYSTEM_PROMPT 含"来源引用规范" + 不含"工具调用前的意图说明"
- [x] grep sse_adapter 无 PLACEHOLDER_THINKING / _maybe_emit_placeholder_thinking / reasoning_step_open
- [x] grep search_docs summary 含"回答时必须用 [srcN]"

## 非目标确认（不应做）

- [x] 未优化 search_docs 性能
- [x] 未改前端 thinking 事件处理
- [x] 未改 MarkdownRenderer
- [x] 未实现 reasoning 多轮保留（preserve thinking）
