# 功能缺口分析 — 修正版（2026-06-21）

说明：基于用户分析 + 我核实代码后的修正。✅ = 已验证已有，❌ = 确实缺失。

## 01-app 范围内

| 功能 | 状态 | 说明 |
|------|------|------|
| 附件上传（文件/图片） | ⚠️ 半成品 | InputBar 有 attachments state 和 fileInputRef，未通真实接口 |
| Loading / Skeleton | ❌ 未实现 | 页面切换/首次加载无骨架屏 |
| 响应式布局退化 | ❌ 未实现 | 窄屏/移动端无退化策略 |
| App 级偏好持久化 | ❌ 未实现 | 面板宽度、主题偏好刷新后丢失 |
| 前端日志面板入口 | ❌ 未实现 | useLogStore 存在但无 UI 入口 |
| 对话导出/分享 | ⚠️ 半成品 | HamburgerMenu 有导出功能，需验证是否可用 |
| 快捷键统一管理 | ⚠️ 部分实现 | Ctrl+K 搜索已接入，无统一面板展示 |

## P0 — 阻塞级

| 功能 | 线程 | 说明 |
|------|------|------|
| 工具调用机制 | 05-agent | ✅ 已确认缺失。dispatch 存在但 ReAct loop 未实现，tool 事件是 stub |
| RAG 知识库闭环 | 03-knowledge | ❌ 用户分析有误。kb_upload/list/delete 已实现，向量化入库已完成，ChromaDB/HardwareVectorStore 都在。不应用作 P0 缺口 |

## P1 — 高优先级

| 功能 | 线程 | 说明 |
|------|------|------|
| 任务规划 | 05-agent | 无 task decomposition，SSE 无 plan/progress 事件 |
| 长期记忆 | 04-session | 无对话摘要（ConversationSummaryBufferMemory） |
| 意图识别 | 05-agent | 所有问题走同一流程，无意图分类 |
| Agent 可观测性 | 08-infra | 无 X-Request-Id、结构化日志 |
| 安全性 | 08-infra | 无用户认证、速率限制（但本地部署不需要太严） |
| 部署 | 08-infra | 无 Docker Compose、CI/CD（等业务稳定再推） |
| 错误恢复 | 02-chat | SSE 无指数退避重连 |

## P2 — 中优先级

| 功能 | 线程 | 说明 |
|------|------|------|
| 人机协作反馈 | 02-chat | 无点赞/点踩、危险操作无确认 |
| 测试与文档 | 08-infra | 后端测试过期、无前端测试、无 README |

## 建议优先级路线

Phase 1（立即）：
  02-chat - API Key 请求头修复（已改完代码，需验证）
  03-knowledge - /api/models 动态 provider（已改完代码，需验证）
  04-session - auth router 注册确认

Phase 2（P0+P1 核心 Agent）：
  05-agent - 工具调用框架（ReAct loop + Function Calling）
  02-chat - SSE 错误恢复（指数退避重连）
  04-session - 长期记忆系统

Phase 3（01-app 产品外壳）：
  01-app - 附件上传 / Skeleton / 响应式 / 持久化 / 日志入口

Phase 4（P2 完善）：
  08-infra - 可观测性 / Docker / 测试
  02-chat - 反馈机制
