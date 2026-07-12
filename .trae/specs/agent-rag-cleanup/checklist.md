# Checklist

## 后端清理
- [x] chat_helpers.py 的 `_classify_relevance` / `_build_source_event` / `_build_rag_context` 三个函数已删除
- [x] chat_helpers.py 顶部 docstring 已同步删除对这 3 个函数的提及
- [x] `_build_system_prompt` 的 `rag_context` 参数及 `if rag_context:` 拼接逻辑已删除
- [x] chat_routes.py L186 调用点已同步（不再传第三个空串参数）
- [x] grep 确认 `_classify_relevance\|_build_source_event\|_build_rag_context` 在 backend/ 下零代码引用（仅剩 search_docs.py 自己的独立同名实现）

## 前端清理
- [x] useChatStore.ts 4 处 `source === "rag"` 收尾分支已删除
- [x] useChatStore.ts mock 数据的 `source: "rag"` 已改为 `source: "reasoning"`
- [x] useChatStore.test.ts 测试数据的 `source: "rag"` 已改为 `source: "reasoning"`
- [x] ActivityBlock.tsx L69 三元分支的 `'rag'` 分支已删除
- [x] types/session.ts L80 + types/api.ts L49 的 `"rag"` 字面量已从枚举删除
- [x] grep 确认 `source.*"rag"` 在 frontend/src 下零命中
- [x] `npx tsc --noEmit` 通过

## sse legacy 分支清理
- [x] sse_helpers.py `_extract_search_results` 的 legacy shape 分支已删除
- [x] py_compile 通过

## Agent 触发条件简化
- [x] agent_factory.py `_should_use_agent` 内部的 langgraph 检查已删除
- [x] chat_routes.py L197 的 `_AGENT_PATH_AVAILABLE` 守卫保留（langgraph 可用性唯一守卫）
- [x] grep 确认 `import langgraph` 在 agent_factory.py 零命中
- [x] 后端启动正常（reranker + BM25 加载完成，Uvicorn running）

## prompt 精简
- [x] prompts.py SYSTEM_PROMPT "知识库可能未覆盖"文案已删除
- [x] prompts.py "工具选择规则"与"文档定位策略"重叠部分已合并
- [x] grep 确认"知识库可能未覆盖"在 prompts.py 零命中（search_docs.py 保留）
- [x] SYSTEM_PROMPT 字符数减少

## 端到端验证
- [x] 后端 py_compile 5 个文件全部通过
- [x] 前端 `npx tsc --noEmit` 通过（exit 0）
- [x] 后端启动正常（Uvicorn running on http://127.0.0.1:58080，reranker + BM25 加载完成）
