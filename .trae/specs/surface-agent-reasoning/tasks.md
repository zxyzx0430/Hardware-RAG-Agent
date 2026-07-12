# Tasks

- [x] Task 1: 验证 LangChain ChatOpenAI 对 reasoning_content 的捕获行为
  - [x] SubTask 1.1: 查 langchain-openai 版本，确认是否自动把 DeepSeek/QwQ 的 `reasoning_content` 放到 `additional_kwargs`
    - 结论：langchain-openai 1.2.2 的 ChatOpenAI **不**捕获 reasoning_content（base.py L8-L11 官方声明 + L428-L481 _convert_delta_to_message_chunk 源码确认，只处理 function_call/tool_calls）
  - [x] SubTask 1.2: 改为子类化方案——写 ReasoningChatOpenAI(ChatOpenAI) 重写 _convert_chunk_to_generation_chunk，调 super 后从 chunk["choices"][0]["delta"] 读 reasoning_content 补到 additional_kwargs
  - [-] SubTask 1.3: 跳过——源码已确认行为，无需跑 API 验证（节省 API key 调用）

- [x] Task 2: sse_adapter.py 读取 reasoning_content 并发 thinking 事件
  - [x] SubTask 2.1: 在 `_handle_message_chunk` 中读取 `msg_chunk.additional_kwargs.get("reasoning_content")`
  - [x] SubTask 2.2: 若非空，发 `sse_event("thinking", {"content": delta, "source": "reasoning"})`，增量发送（不缓冲）
  - [x] SubTask 2.3: 在 `state` dict 加 `reasoning_step_open: bool` 标记当前是否有未关闭的 reasoning step

- [x] Task 3: sse_adapter.py 普通模型占位思考逻辑
  - [x] SubTask 3.1: 检测到 AIMessage 带 `tool_calls` 但 `reasoning_step_open=False`，在 tool_call 事件前补发 `sse_event("thinking", {"content": "模型正在思考...", "source": "agent"})`
  - [x] SubTask 3.2: 检测到 tool_result 后下一轮 LLM 推理无 reasoning_content，在 text/tool_call 事件前补发占位
  - [x] SubTask 3.3: 占位发完后立即标记 `reasoning_step_open=True`，由前端在收到 text/tool_call 时关闭

- [x] Task 4: prompts.py SYSTEM_PROMP 加可选前置意图指令
  - [x] SubTask 4.1: 在 SYSTEM_PROMPT 末尾加一节"工具调用前的意图说明"，要求 LLM 在调用工具前用一句中文简述要做什么（不强制标签，普通文本即可）
  - [x] SubTask 4.2: 把这句意图文本作为占位文案的动态来源（替代静态"模型正在思考..."，若 LLM 产出了前置 text 则用其作为 thinking content）

- [x] Task 5: 前端兼容性确认（只读，不改代码）
  - [x] SubTask 5.1: 确认 useChatStore.ts `case "thinking"` 分支兼容 `source: "agent"`（走通用分支，新建 step）— 兼容，无需改动
  - [x] SubTask 5.2: 确认 ActivityBlock.tsx ThinkingStep 对 `source: "agent"` 显示"思考中"标签（走"其他"分支）— 兼容，无需改动
  - [x] SubTask 5.3: 类型定义补丁 — api.ts L49 + session.ts L80 加 `'agent'` 到 source 联合类型

- [ ] Task 6: 端到端验证
  - [ ] SubTask 6.1: 用 DeepSeek-R1 测试"查 ESP32 怎么配置一个东西"，确认前置 reasoning 卡片 + tool_call 卡片 + 后置 reasoning 卡片 + 最终答案都可见
  - [ ] SubTask 6.2: 用普通模型（GPT-4o 或 Claude）测试同一问题，确认占位"模型正在思考..."卡片在工具调用前后各出现一次
  - [ ] SubTask 6.3: 测试"你好"闲聊场景，确认无 reasoning 时也不报错（Agent 不调工具，直接发 text）

# Task Dependencies

- Task 2 依赖 Task 1（需先确认 reasoning_content 可读）
- Task 3 依赖 Task 2（占位逻辑与 reasoning 逻辑共用 `reasoning_step_open` 状态）
- Task 4 可与 Task 2/3 并行
- Task 5 可与 Task 2/3/4 并行（只读确认）
- Task 6 依赖 Task 1-5 全部完成
