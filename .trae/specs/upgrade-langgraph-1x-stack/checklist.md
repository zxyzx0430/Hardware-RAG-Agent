# Checklist

## 兼容性修复验证

- [ ] `backend/requirements.txt` 中 langchain==1.3.4、langgraph>=1.0、langchain-chroma 已添加、langchain-community 已移除
- [ ] `pip show langchain langgraph langchain-openai langchain-chroma` 输出与 requirements.txt 一致
- [ ] `agent_factory.py` 不再出现 `MemorySaver` 字样（除注释说明历史名）
- [ ] `agent_factory.py` 不再出现 `max_tokens=` 参数（改为 `max_completion_tokens=`）
- [ ] `agent_factory.py` 不再出现 `interrupt_before=` 参数
- [ ] `agent_factory.py` `reset_thread_checkpoint` 不直接访问 `cp.storage[thread_id]` 或 `cp.writes` 中按 `k[0] == thread_id` 过滤
- [ ] `reasoning_chat.py` 在 DeepSeek-R1 模型下能从 `additional_kwargs.reasoning_content` 读到推理内容
- [ ] `tool_spec.py` L90 `_ctx` 类型注解为 `ToolContext | None`
- [ ] `vector_store.py` L20 导入 `from langchain_chroma import Chroma`
- [ ] 后端启动后 GET /api/tools 返回 26 个工具，无 ImportError

## HITL interrupt() 验证

- [ ] `hitl_handler.py` 不再依赖 `agent.get_state(config).next == ["tools"]` 判断中断
- [ ] `hitl_handler.py` 改为读取 `agent.get_state(config).tasks[0].interrupts` 或等价 1.x API
- [ ] 触发 risk_level=HIGH 工具时前端收到 `tool_confirm_required` SSE 事件
- [ ] 用户点"拒绝"后前端工具卡片状态变为"已拒绝"，**不卡 pending**（PLUR [ENG-2026-0702-001]）
- [ ] 用户点"允许"后工具执行 + Agent 继续推理输出最终答案
- [ ] 用户点"停止"后流式正确终结，工具卡片状态正确

## Middleware 验证

- [ ] `backend/src/agent/middlewares/context_guard_middleware.py` 存在并实现 `before_node` hook
- [ ] `backend/src/agent/middlewares/loop_guard_middleware.py` 存在并实现 `before_node` hook
- [ ] `agent_factory.py` `create_react_agent(...)` 调用包含 `middleware=[ContextGuardMiddleware(), LoopGuardMiddleware()]`
- [ ] `sse_adapter.py` 不再直接调用 autocompact/microcompact（Middleware 接管）
- [ ] 长对话（超过 100k token）触发 ContextGuardMiddleware 自动压缩，不中断流式输出
- [ ] 连续 3 次相同工具调用触发 LoopGuardMiddleware 注入"换方法"提示

## SqliteSaver 持久化验证

- [ ] `backend/src/config/settings.py` 包含 `agent_checkpointer_type: str = "sqlite"` 字段
- [ ] `agent_factory.py` `_get_checkpointer()` 根据 settings 返回 SqliteSaver 或 InMemorySaver
- [ ] `backend/data/agent_checkpoints.sqlite` 文件在首次 Agent 请求后生成
- [ ] 发起 Agent 请求 → 重启后端 → 同 session_id 再请求 → **不出现 INVALID_CHAT_HISTORY**
- [ ] `agent_checkpointer_type=memory` 时回退到 InMemorySaver，重启后状态丢失（验证切换有效）
- [ ] `reset_thread_checkpoint` 在 SqliteSaver 模式下能正确删除指定 thread_id 的 checkpoint

## 文档与回归验证

- [ ] `docs/pitfalls.md` 追加 langchain 1.x 升级踩坑记录
- [ ] `docs/architecture-map.md` 中 Agent 构造链路章节已更新（middleware + SqliteSaver）
- [ ] `C:\Users\奶茶丸\Desktop\agent-architecture-map.md` 与项目内副本同步
- [ ] `docs/completed.md` 追加本次升级记录
- [ ] 现有功能（聊天 / RAG 检索 / 工具调用 / HITL / 烧录）全部回归通过
