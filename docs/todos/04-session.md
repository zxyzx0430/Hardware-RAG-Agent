# 04-session TODO

## 功能：持久化 / 设置 / 会话管理

> 新任务加到最前面（倒序排列），每次只读前 2 项

- [x] useChatStore.ts 拆分（message/session/bookmark/export）— 已完成（2026-06-29 核实）：拆为 8 个 store 文件（useChat/useSession/useBookmark/useSettings/useKnowledge/useSerial/useLog/useApp，见 [frontend/src/stores/](file:///E:/Desktop/agent/frontend/src/stores/)）
- [?] SQLAlchemy + Alembic 会话持久化 — 部分完成（2026-06-29 核实）：SQLAlchemy 已实现（[crud.py](file:///E:/Desktop/agent/backend/app/api/crud.py) + [models.py](file:///E:/Desktop/agent/backend/app/db/models.py)），[alembic.ini](file:///E:/Desktop/agent/backend/alembic.ini) 已配但无 alembic/ 目录和迁移脚本，实际用 `database.py` 的 `create_all` 建表。是否需补 alembic 迁移脚本请 00-control 决定（review-result.md #11 P2 建议接入）
- [x] 设置持久化（API Key / Base URL / 主题偏好）— 已完成（2026-06-29 核实）：[crud.py](file:///E:/Desktop/agent/backend/app/api/crud.py) L269 `get_settings` + L276 `update_settings` 已实现，API Key 加密存储（auth.py Fernet）
- [ ] beforeunload 事件兜底未 flush 的数据 — 未修复（2026-06-29 核实）：frontend/src 下无 beforeunload 引用
- [-] 长期记忆系统（对话摘要 ConversationSummaryBufferMemory）— 跳过（2026-06-29）：V2 范围（见 completed.md 05-agent 节"未开始（V2 范围）"）
- [x] 对话分支管理（新建分支/切换分支/删除分支）— 已完成（2026-06-29 核实）：[crud.py](file:///E:/Desktop/agent/backend/app/api/crud.py) L48-49 SessionCreate 有 branch_from_session_id/branch_from_message_id 字段，L106-126 创建分支时复制源会话消息（支持 branch_from_message_id 截断），L170-173 update_session 支持更新分支字段；前端 BranchTree 组件已集成（completed.md 02-chat 节）
- [ ] WorkbenchPanel.tsx 拆分 — 未修复（2026-06-29 核实）：[frontend/src/components/workbench/](file:///E:/Desktop/agent/frontend/src/components/workbench/) 目录下仅 WorkbenchPanel.tsx 1 个文件，未按 tab 拆分

---

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**：全部 [x] 后通知 00-control 审查