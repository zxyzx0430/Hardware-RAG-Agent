# 05-agent TODO

## 功能：LangGraph Agent / 工具调用 / 任务规划

> 新任务加到最前面（倒序排列），每次只读前 2 项

- [x] 5 个 stub 工具统一调用真实实现（架构深化 Phase D）
      tool_router.py 的 AuditPinsTool/WiringTool/SearchDocsTool 改为调用 audit_pins_core/generate_wiring_svg/search_docs_core；BuildTool/UploadTool 明确标注 v2 范围（PLUR ENG-2026-0616-001）。新增 app/hardware/audit.py + src/rag/search.py，hardware_routes.py 的 audit_pins 委托给 audit_pins_core 消除重复实现。19 测试通过。
- [x] 修复 CodeExecutorTool 的 NameError bug（code/language 未定义）
      验证：tool_router.py L273-277 代码正常（`code = args.get('code', '')` / `language = args.get('language', 'python')`），execute_code 签名匹配。历史已修复，标记为过时。如未来复现需检查 args 是否传入正确字段名。
- [ ] 工具调用框架（工具注册表 + LLM Function Calling + ReAct Agent）
- [ ] 任务规划（task decomposition + SSE plan/progress 事件）
- [ ] 意图识别（意图分类器，不同问题走不同流程）
- [ ] LangGraph 工作流调度（state graph + 节点编排 + 条件跳转）
- [ ] Agent 可观测性（X-Request-Id、结构化日志）

---

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**：全部 [x] 后通知 00-control 审查