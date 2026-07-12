# 交办单 250621-split-routes

## 归属
- 线程：02-chat
- 优先级：P1
- 关联问题：#1 routes.py 57KB

## 任务
将 backend/app/api/routes.py（1401 行，28 个函数）按业务域拆成 5 个文件：

| 新文件 | 包含路由 |
|--------|---------|
| chat_routes.py | /api/chat SSE + /api/models |
| kb_routes.py | /api/kb/upload /list /delete |
| hardware_routes.py | /api/devices /diagnose /wiring /audit_pins |
| build_routes.py | /api/build SSE + /api/upload SSE |
| tool_routes.py | /api/tool + /monitor/{port} WS |

## 要求
1. 共享函数（get_db_ctx、get_vector_store、sse_event、_sanitize_error）抽出到 common.py
2. 各文件独立 router，在 __init__.py 统一注册
3. 原 routes.py 保留不删（v1 兼容），新文件逐步迁移
4. api-contract.md 同步更新

## 开工前必读
AGENTS.md / docs/completed.md / docs/api-contract.md / docs/issue-tracker.md


## TODO 清单

- [ ] 抽共享函数到 common.py（get_db_ctx / get_vector_store / sse_event / _sanitize_error / make_client）
- [ ] 创建 chat_routes.py（chat SSE + models）
- [ ] 创建 kb_routes.py（upload / list / delete）
- [ ] 创建 hardware_routes.py（devices / diagnose / wiring / audit_pins）
- [ ] 创建 build_routes.py（build + upload SSE）
- [ ] 创建 tool_routes.py（tool + monitor WS）
- [ ] 更新 backend/app/api/__init__.py 注册新 router
- [ ] 同步更新 docs/api-contract.md 路由归属
- [ ] 验证所有路由功能正常
