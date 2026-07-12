# Checklist

## 安全 / 鉴权
- [x] WebSocket /api/monitor/{port} 加了 ws_auth 鉴权（accept 前检查 token，失败 close 4401）
- [x] WebSocket SerialException 分支调用了 await websocket.close(code=4003)
- [x] WebSocket generic Exception 分支调用了 await websocket.close(code=4000)
- [x] path_guard.DENY_PATTERNS 包含 Windows/ / System32/ / $Recycle.Bin/ / Boot/bootmgr
- [x] path_guard 的 .. 检查只拦路径组件，不误杀 notes..draft.txt（实测通过）
- [x] path_guard 仍能拦 ../../../etc/passwd（实测通过）

## RAG 稳定性
- [x] reranker.py 失败后进入 5 分钟冷却（非永久禁用）
- [x] reranker 冷却期满后下次调用自动重试（_check_cooldown 辅助函数）
- [x] vector_store.search 接入了 _with_retry
- [x] vector_store search 重试失败仍返回 [] + logger.exception

## 静默吞异常
- [x] client.py L286/L353 except Exception: pass 改为 logger.warning
- [x] multimodal_chunker.py to_markdown 失败 continue 前有 logger.warning（含页码 i+1）
- [x] chat_routes.py L268 CancelledError 有 logger.debug

## 死代码删除
- [x] backend/src/agent/tools/wrappers.py 已删除
- [x] backend/src/agent/tools/workbench_tools.py 已删除
- [x] backend/src/agent/tools/web_search.py 已删除
- [x] grep 确认无任何 import 这三个文件

## 未注册工具删除
- [x] backend/src/agent/tools/groups/execution/diagnostic.py 已删除
- [x] backend/src/agent/tools/groups/execution/skills.py 已删除
- [x] execution/__init__.py 检查（本身就没有这三个导出）
- [x] agent_factory.py 未 import 这三个类

## 导入修复
- [x] autocompact.py 不再 from src.agent.context_guard import estimate_tokens
- [x] autocompact.py 改用 LLMClient._estimate_tokens
- [x] autocompact.py 可正常 import

## .gitignore
- [x] 根 .gitignore 包含 *.key / *.pem / *credentials* / .env / settings.json
- [x] 根 .gitignore 包含 stdout.txt / stderr.txt
- [x] 根 .gitignore 包含 backend/_*.py
- [x] git status 不再出现这些文件

## 前端清理
- [x] useSessionStore.ts migrateSession(s: any) 改为 s: unknown + 类型守卫
- [x] SafetyPane.tsx 不再直接操作 el.style.outline
- [x] SafetyPane.tsx 用 state + className 切换高亮
- [x] npx tsc --noEmit 0 error

## 重复代码
- [x] backend/src/rag/chunking/_constants.py 已创建
- [x] _SUB_SPLIT_SEPARATORS / _INLINE_CODE_RE 在 _constants.py 定义（改名 SUB_SPLIT_SEPARATORS / INLINE_CODE_RE）
- [x] hybrid_chunker.py 从 _constants 导入
- [x] agent_chunker.py 从 _constants 导入
- [x] multimodal_chunker.py 从 _constants 导入
- [x] build_routes.py 删除了 _upload_locks + _get_port_lock
- [x] build_routes.py 复用 app/api/locks.py 的 get_port_lock

## 最终验证
- [x] 后端所有修改文件 python -c 导入正常
- [x] pytest tests/ 168 passed（跳过已知 pre-existing 失败）
- [x] 前端 tsc --noEmit 0 error
- [x] 后端启动 + 59 路由 + 1 WebSocket 正常
- [x] docs/pitfalls.md 已更新（autocompact ImportError + PowerShell $Recycle 变量展开）
- [x] git commit + push（commit e334b1c 已推送）
