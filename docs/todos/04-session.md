# 04-session TODO

## 功能：持久化 / 设置 / 会话管理

> 新任务加到最前面（倒序排列），每次只读前 2 项

- [ ] useChatStore.ts 拆分（message/session/bookmark/export）
- [ ] SQLAlchemy + Alembic 会话持久化
- [ ] 设置持久化（API Key / Base URL / 主题偏好）
- [ ] beforeunload 事件兜底未 flush 的数据
- [ ] 长期记忆系统（对话摘要 ConversationSummaryBufferMemory）
- [ ] 对话分支管理（新建分支/切换分支/删除分支）
- [ ] WorkbenchPanel.tsx 拆分

---

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**：全部 [x] 后通知 00-control 审查