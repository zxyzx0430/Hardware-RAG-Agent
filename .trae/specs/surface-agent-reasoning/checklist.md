# Checklist

## 推理模型思考链透传
- [x] ChatOpenAI 能捕获 DeepSeek-R1/QwQ 的 `reasoning_content` 字段（验证脚本打印 `additional_kwargs.reasoning_content` 非空）
  - 注：通过 ReasoningChatOpenAI 子类实现，原生 ChatOpenAI 不捕获（langchain-openai 1.2.2 官方声明）
  - 单元测试通过：`_extract_reasoning` 正确处理 3 种 chunk 场景
- [x] sse_adapter.py `_handle_message_chunk` 读取 `additional_kwargs.reasoning_content` 并发 `thinking` 事件
- [x] reasoning_content 逐 chunk 增量发送（不整体缓冲，用户能看到流式效果）
- [x] thinking 事件 schema 为 `{"content": delta, "source": "reasoning"}`

## 普通模型占位思考
- [x] 检测到 `tool_calls` 即将到来但前面无 thinking 事件时，补发 `source: "agent"` 占位
- [x] 占位事件在 tool_call 事件**之前**发送（时序正确）
- [x] tool_result 后下一轮无 reasoning 时，在 text/tool_call 前补发占位
- [x] 占位文案"模型正在思考..."或动态取自 LLM 前置意图文本

## 思考链状态机
- [x] state dict 维护 `reasoning_step_open: bool` 标记
- [x] text 事件到来时，前端自动关闭 thinking step（已有逻辑，确认未被破坏）
- [x] tool_call 事件到来时，前端自动关闭 thinking step 并新建 ToolStep（已有逻辑，确认未被破坏）
- [x] 工具调用后第二轮 reasoning 新建第二个 ThinkingStep（不覆盖第一个）

## 前端兼容性
- [x] useChatStore.ts `case "thinking"` 分支兼容 `source: "agent"`（走通用分支新建 step）
- [x] ActivityBlock.tsx ThinkingStep 对 `source: "agent"` 显示"思考中"标签
- [x] ActivityBlock.tsx ThinkingStep 对 `source: "reasoning"` 显示"推理思考"标签并默认展开
- [x] 类型定义含 `'agent'`（api.ts L49 + session.ts L80 补丁）

## SYSTEM_PROMPT
- [x] SYSTEM_PROMPT 加"工具调用前简述意图"指令（不强制标签）
- [x] 普通模型若产出前置 text，作为占位思考内容来源

## 端到端验证
- [ ] DeepSeek-R1 测试：前置 reasoning 卡片 → tool_call → tool_result → 后置 reasoning 卡片 → 最终答案，全链可见（需用户手动 API 测试）
- [ ] 普通模型测试：占位思考卡片在工具调用前后各出现一次（需用户手动 API 测试）
- [ ] "你好"闲聊测试：无 reasoning 时不报错，Agent 不调工具直接发 text（需用户手动 API 测试）
- [x] 无 TypeError / 无 INVALID_CHAT_HISTORY 回归（后端启动无 import 错误，/docs 返回 200）
- [x] ReasoningChatOpenAI 子类单元测试通过（3 种 chunk 场景全过）
