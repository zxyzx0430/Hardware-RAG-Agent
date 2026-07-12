# Tasks

- [x] Task 1: 后端 chat_helpers.py 清理 3 个死函数 + _build_system_prompt 简化
      删除 _classify_relevance / _build_source_event / _build_rag_context 三个零调用函数；
      _build_system_prompt 删除 rag_context 参数；chat_routes.py L186 调用点同步；
      chat_helpers.py 从 187 行缩到 121 行；py_compile + grep 零引用验证通过
  - [x] SubTask 1.1: 删除 `_classify_relevance`（L33-39）+ 同步删除顶部 docstring 对它的提及
  - [x] SubTask 1.2: 删除 `_build_source_event`（L113-141）+ 同步删除顶部 docstring 迁移说明
  - [x] SubTask 1.3: 删除 `_build_rag_context`（L144-161）+ 同步删除顶部 docstring 废弃说明
  - [x] SubTask 1.4: `_build_system_prompt` 删除 `rag_context` 参数 + L108-109 的 `if rag_context:` 拼接逻辑
  - [x] SubTask 1.5: chat_routes.py L186 调用点同步——从 `_build_system_prompt(payload, attachment_texts, "")` 改为 `_build_system_prompt(payload, attachment_texts)`
  - [x] 验证：py_compile 通过；grep 确认 3 个函数在 backend/ 下零代码引用（仅剩 search_docs.py 自己的独立同名实现 + 2 行已清理的历史注释）

- [x] Task 2: 前端 useChatStore.ts 清理 source: "rag" 死分支 + mock 数据更新
      4 处死分支删除（外部进程先把 "rag" 改成 "agent"，本 task 再把 "agent" 死分支删除）；
      mock 数据 + 测试数据改为 source: "reasoning"；tsc 通过；grep 零命中
  - [x] SubTask 2.1: 删除 useChatStore.ts L767 的 `source === "rag"` 收尾分支
  - [x] SubTask 2.2: 删除 useChatStore.ts L792 的同上分支（后台 session 分支）
  - [x] SubTask 2.3: 删除 useChatStore.ts L811 的同上分支（tool_call 事件触发时）
  - [x] SubTask 2.4: 删除 useChatStore.ts L836 的同上分支（tool_confirm_required 事件）
  - [x] SubTask 2.5: useChatStore.ts mock 数据改为 `source: "reasoning"`
  - [x] SubTask 2.6: useChatStore.test.ts 测试数据改为 `source: "reasoning"`
  - [x] 验证：`npx tsc --noEmit` 通过；grep 确认 `source.*"rag"` 在 frontend/src 下零命中

- [x] Task 3: 前端 ActivityBlock.tsx + types 清理 "rag" 字面量
      ActivityBlock.tsx 三元分支简化；types/session.ts + types/api.ts 删除 "rag" 字面量；tsc 通过
  - [x] SubTask 3.1: ActivityBlock.tsx L69 简化三元分支——删除 `'rag'` 分支，直接 `'思考中'`
  - [x] SubTask 3.2: types/session.ts L80 从枚举删除 `"rag"` 字面量
  - [x] SubTask 3.3: types/api.ts L49 同上删除 `"rag"` 字面量
  - [x] 验证：`npx tsc --noEmit` 通过；grep 确认 `"rag"` 字面量在 types/ 下零命中

- [x] Task 4: sse_helpers.py _extract_search_results 删 legacy 分支
      legacy shape 分支删除，只保留 envelope 解析；docstring 同步更新；py_compile 通过
  - [x] SubTask 4.1: `_extract_search_results` 删除 legacy shape 分支，只保留 envelope 解析（`data.results`）
  - [x] 验证：py_compile 通过

- [x] Task 5: agent_factory.py 简化 Agent 触发条件
      _should_use_agent 删除内部 langgraph 检查块（6 行）；chat_routes.py L197 守卫保留；py_compile 通过
  - [x] SubTask 5.1: agent_factory.py `_should_use_agent` 删除内部 `try: import langgraph` 检查块
  - [x] SubTask 5.2: chat_routes.py L197 **保留** `_AGENT_PATH_AVAILABLE` 守卫（langgraph 可用性唯一守卫）
  - [x] 验证：grep 确认 `import langgraph` 在 agent_factory.py 零命中；后端启动正常

- [x] Task 6: prompts.py SYSTEM_PROMPT 精简重复文案
      删除"知识库未覆盖"重复文案（与 _KB_COVERAGE_HINT 重复）；合并"工具选择规则"与"文档定位策略"重叠部分；grep 零命中
  - [x] SubTask 6.1: 删除"请如实告诉用户知识库可能未覆盖该内容"文案——与 search_docs.py `_KB_COVERAGE_HINT` 重复
  - [x] SubTask 6.2: 合并"工具选择规则"与"文档定位策略"的重叠部分——文档定位策略只保留 good/bad example
  - [x] 验证：grep 确认"知识库可能未覆盖"在 prompts.py 零命中；SYSTEM_PROMPT 字符数减少

# Task Dependencies

- Task 1-6 互相独立，已全部并行完成
- 端到端验证通过：后端 py_compile + 前端 tsc + 后端启动确认 Agent 路径正常
