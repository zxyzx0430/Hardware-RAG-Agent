# Checklist

- [x] session 表有 context_window 字段，默认 262144（256K）
- [x] autocompact 的 should_autocompact 接收动态 threshold 参数，不再用固定 13000
- [x] autocompact 失败 3 次后降级为 snip（带"已删除 N 条"存在标记），不调 LLM
- [x] context_guard.compute_token_limit 优先用 session 级 context_window
- [x] sse_adapter 在工具调用后检查 token，超 80% 触发 autocompact，压缩完继续循环
- [x] 压缩开始时发送 SSE context_compressing 事件
- [x] chat_routes 在发送前检查历史 token，超 80% 先压缩再发给 LLM
- [x] 设置页有 256K / 1M 下拉框，带说明文字
- [x] 切换窗口时调 API 更新当前 session 的 context_window
- [x] 切换 session 时前端读取该 session 的 context_window
- [x] 前端收到 context_compressing SSE 事件时显示"正在压缩上下文..."
- [x] 后端启动无报错
- [x] 前端 TypeScript 编译通过
- [x] microcompact.py 未被主循环调用（确认未被接入）
