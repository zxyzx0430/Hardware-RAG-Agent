# 05-agent TODO

## 功能：LangGraph Agent / 工具调用 / 任务规划

> 新任务加到最前面（倒序排列），每次只读前 2 项

- [ ] 工具调用框架（工具注册表 + LLM Function Calling + ReAct Agent）
- [ ] 任务规划（task decomposition + SSE plan/progress 事件）
- [ ] 意图识别（意图分类器，不同问题走不同流程）
- [ ] 修复 CodeExecutorTool 的 NameError bug（code/language 未定义）
- [ ] LangGraph 工作流调度（state graph + 节点编排 + 条件跳转）
- [ ] Agent 可观测性（X-Request-Id、结构化日志）

---

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**：全部 [x] 后通知 00-control 审查