# Agent ReAct 全链路 Spec

> 基于 `docs/superpowers/specs/2026-06-30-agent-react-design.md`（已经过三轮修订）
> Change-ID: `agent-react-fullstack`
> Owner: Trae (05-agent 线程)

---

## Why

当前 `/api/chat` 仍是单轮 RAG 问答（[chat_routes.py L178](file:///e:/Desktop/agent/backend/app/api/chat_routes.py#L178) 的 `# TODO: ReAct loop` 仍在），LLM 无法自主决策调用工具。需要接入 LangGraph `create_react_agent` prebuilt，让 LLM 多轮推理、自主调工具，覆盖字节创造力大赛 demo 三段式能力：「检索 → 分析 → 代码」。目标 7.15 提交。

## What Changes

### 后端新建（13 个文件）
- `backend/src/agent/agent_factory.py` — `create_react_agent` 封装，每请求新建 agent + 工具实例
- `backend/src/agent/sse_adapter.py` — langgraph `astream` 输出转项目 SSE 事件协议
- `backend/src/agent/prompts.py` — Agent 系统 prompt + 工具描述
- `backend/src/agent/loop_detector.py` — 重复调用 + 无进展检测
- `backend/src/agent/permission_gate.py` — 4 步权限门控（path_guard→模式分流→风险分级→决策落地）
- `backend/src/agent/path_guard.py` — 工作目录范围校验（允许目录 + 强制 deny）
- `backend/src/agent/risk_classifier.py` — V1 关键字黑名单分级
- `backend/src/agent/audit_logger.py` — SQLite 审计日志写入（30 天保留）
- `backend/src/agent/tools/__init__.py` — 工具包入口
- `backend/src/agent/tools/wrappers.py` — search_docs / audit_pins / wiring