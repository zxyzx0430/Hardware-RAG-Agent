# 05-agent — LangGraph / 工具 / 召回 / 记忆 / 检查点 / 多 Agent 编排

> 创建时间：2026-06-21
> 前置条件：V1 后端骨架已搭建，tool_router 已实现，5 个硬件工具待构建

## 负责范围

- **Agent 核心**：LangChain ReAct Agent 编排，5 个硬件工具的注册与实现
- **记忆系统**：ConversationSummaryBufferMemory 多轮上下文，Agent 检查点
- **工具实现**：search_hardware_kb / generate_hardware_code / review_hardware_code / compare_hardware_components / diagnose_hardware_problem
- **MCP 集成**：MCP Server 工具动态注册到 Agent 调度系统
- **检查点**：Agent 状态持久化与恢复
- **降级策略**：Agent 不可用时降级到纯 RAG 模式

## V1 里程碑

- [ ] LangChain ReAct Agent 核心搭建
- [ ] 5 个硬件工具的 RAG 增强实现（非 stub）
- [ ] ConversationSummaryBufferMemory 集成
- [ ] Agent 降级策略（Agent 不可用时→纯 RAG）
- [ ] Agent 检查点持久化
- [ ] 与 /api/chat 路由集成

## V2 前瞻

- compile_and_flash（USB 烧录）
- read_serial（串口读取）
- ota_flash（WiFi OTA 烧录）
- hardware_guardrails（输出安全校验）

## 已知踩坑 / 决策记录

- src/agent/__init__.py 当前为空，需按惰性导入风格导出
- tool_router.py 已实现 register/dispatch 系统，Agent 应基于此调度
- /api/chat 路由当前直连 LLM + RAG，需要改为 Agent 路由模式

## 关键文件

| 文件 | 说明 |
|------|------|
| backend/src/agent/ | Agent 核心模块目录 |
| backend/app/api/tool_router.py | 工具注册与分发系统 |
| docs/pitfalls.md | 项目踩坑记录（所有线程共用） |
| docs/api-contract.md | 接口契约 |

## 踩坑记录

本项目所有踩坑统一记录在 docs/pitfalls.md，不按线程分散。
修复 bug 后直接追加到 docs/pitfalls.md。
