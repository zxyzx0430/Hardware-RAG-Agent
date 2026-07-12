# Tasks

## 阶段 1：安全鉴权（独立可并行）

- [x] Task 1: WebSocket /api/monitor/{port} 加鉴权 + 异常分支补 close
  - [x] SubTask 1.1: tool_routes.py serial_monitor 加 ws_auth 鉴权（accept 前检查 token，失败 close 4401）
  - [x] SubTask 1.2: SerialException 分支补 await websocket.close(code=4003, reason="串口打开失败")
  - [x] SubTask 1.3: generic Exception 分支补 await websocket.close(code=4000, reason="串口监视器异常")
  - [x] SubTask 1.4: 前端 apiWS 中心化注入 token（client.ts 改造，SerialPane 自动受益）

- [x] Task 2: path_guard Windows 路径 + .. 精确匹配
  - [x] SubTask 2.1: DENY_PATTERNS 加 Windows/ / System32/ / $Recycle.Bin/ / Boot/bootmgr（含大小写不敏感子串匹配）
  - [x] SubTask 2.2: .. 检查改为 _has_traversal_component 按路径组件精确匹配
  - [x] SubTask 2.3: 测试 notes..draft.txt 通过 / ../etc/passwd 拒绝 / Windows\System32 拒绝

## 阶段 2：RAG 稳定性（独立可并行）

- [x] Task 3: reranker 冷却恢复
  - [x] SubTask 3.1: _RERANKER_PREDICT_FAILED 改为 _reranker_failed_at: Optional[float]（时间戳）
  - [x] SubTask 3.2: _check_cooldown 辅助函数，300s 冷却期满清除标志重试
  - [x] SubTask 3.3: 日志 "reranker entering 5min cooldown" / "reranker cooldown expired, retrying"

- [x] Task 4: vector_store.search 接入 _with_retry
  - [x] SubTask 4.1: similarity_search_with_relevance_scores 包进 _with_retry
  - [x] SubTask 4.2: 重试失败仍返回 [] + logger.exception

## 阶段 3：静默吞异常补日志（独立可并行）

- [x] Task 5: client.py / multimodal_chunker / chat_routes 补日志
  - [x] SubTask 5.1: client.py L286/L353 except Exception: pass → logger.warning
  - [x] SubTask 5.2: multimodal_chunker.py L729-732 to_markdown 失败补 logger.warning（含页码 i+1）
  - [x] SubTask 5.3: chat_routes.py L268 CancelledError 补 logger.debug

## 阶段 4：死代码 + 导入修复（独立可并行）

- [x] Task 6: 删死代码副本
  - [x] SubTask 6.1: 删 backend/src/agent/tools/wrappers.py
  - [x] SubTask 6.2: 删 backend/src/agent/tools/workbench_tools.py
  - [x] SubTask 6.3: 删 backend/src/agent/tools/web_search.py
  - [x] SubTask 6.4: grep 确认无任何 import 残留

- [x] Task 7: 删未注册工具
  - [x] SubTask 7.1: 删 backend/src/agent/tools/groups/execution/diagnostic.py
  - [x] SubTask 7.2: 删 backend/src/agent/tools/groups/execution/skills.py
  - [x] SubTask 7.3: execution/__init__.py 检查（本身就没有这三个导出，无需改）
  - [x] SubTask 7.4: grep 确认 agent_factory.py 未 import 这三个类

- [x] Task 8: autocompact.py 修复导入
  - [x] SubTask 8.1: 改 from src.llm.client import LLMClient
  - [x] SubTask 8.2: 调用处改 LLMClient._estimate_tokens(content)
  - [x] SubTask 8.3: 验证 import 不报错

## 阶段 5：.gitignore 补全（独立）

- [x] Task 9: 根 .gitignore 补全
  - [x] SubTask 9.1: 加 *.key / *.pem / *credentials* / .env / settings.json
  - [x] SubTask 9.2: 加 stdout.txt / stderr.txt
  - [x] SubTask 9.3: 加 backend/_*.py（临时脚本隔离）
  - [x] SubTask 9.4: git status 不再出现这些文件

## 阶段 6：前端 + 重复代码（独立可并行）

- [x] Task 10: useSessionStore.ts any → unknown
  - [x] SubTask 10.1: migrateSession(s: any) → s: unknown
  - [x] SubTask 10.2: 加类型守卫 + spread 兜底
  - [x] SubTask 10.3: tsc --noEmit 0 error

- [x] Task 11: SafetyPane.tsx DOM → state
  - [x] SubTask 11.1: 加 state highlightId
  - [x] SubTask 11.2: className 切换高亮 + workbench.css 加 .safety-highlight
  - [x] SubTask 11.3: tsc --noEmit 0 error

- [x] Task 12: 共享常量抽取
  - [x] SubTask 12.1: 新建 backend/src/rag/chunking/_constants.py（INLINE_CODE_RE + SUB_SPLIT_SEPARATORS）
  - [x] SubTask 12.2: 三个 chunker 改为从 _constants 导入
  - [x] SubTask 12.3: 验证导入正常

- [x] Task 13: build_routes 复用锁
  - [x] SubTask 13.1: 删 build_routes.py 的 _upload_locks + _get_port_lock
  - [x] SubTask 13.2: 改用 from app.api.locks import get_port_lock
  - [x] SubTask 13.3: 验证编译/烧录并发锁仍生效（test_build_routes 168 passed）

## 阶段 7：验证

- [x] Task 14: 全量验证
  - [x] SubTask 14.1: 后端 python -c 导入所有修改文件 OK
  - [x] SubTask 14.2: pytest tests/ 168 passed（跳过已知 pre-existing 失败：test_routes_tool audit_pins + test_settings host 0.0.0.0）
  - [x] SubTask 14.3: 前端 npx tsc --noEmit 0 error
  - [x] SubTask 14.4: 后端启动 + 59 路由 + 1 WebSocket 正常
  - [x] SubTask 14.5: docs/pitfalls.md 已更新（autocompact ImportError + PowerShell $Recycle 变量展开）
  - [x] SubTask 14.6: git commit + push（commit e334b1c 已推送）

# Task Dependencies

- Task 1-13 互相独立，已并行完成
- Task 14 依赖 Task 1-13 全部完成
