# scripts/legacy/2026-11-04

W1 阶段 2 的暂存区。**这里的东西没有被删除,只是移出了构建与测试路径。**

删除需要单独批准;在确认无引用后才会真正移除。

## dead-modules/

| 文件 | 原位置 | 判定依据 |
|---|---|---|
| `permission_matcher.py` | `backend/src/agent/permission_matcher.py` | 全仓（app / src / tests）零 import |
| `plugins/` | `backend/src/agent/plugins/` | 全仓零 import;`RedactPlugin` 的密钥脱敏能力目前在审计侧另有实现 |

## backend-root-scripts/

52 个 `backend/_*.py` 一次性脚本（检查 KB、重索引、验证 chunk 等）。
全部未被 git 跟踪（`.gitignore:176`），因此移动不影响任何提交。

`_reindex_*` / `_verify_*` 仍出现在 `docs/completed-history.local.md:1467` 的运维步骤里；
如果以后还要用，从这里取回，不要放回 `backend/` 根目录。

## 未移动的项及原因

| 项 | 原因 |
|---|---|
| `backend/src/agent/compact/microcompact.py` | `compact/` 目录正被上下文压缩工作流占用,等 W4 结束后再处理 |
| `backend/app/api/tool_events.py` | 生产未接线,但 `backend/tests/test_tool_event_body_store.py` 直接 import;W1 本批要修它的序列化 bug |
| `backend/src/agent/store_adapter.py` + `session_search.py` | 仍被 `tools/groups/retrieval/search_history.py` 注册为工具;需先决定 `search_history` 存废 |
