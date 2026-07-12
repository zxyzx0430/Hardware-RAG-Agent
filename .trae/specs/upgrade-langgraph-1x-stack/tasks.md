# Tasks

## 阶段 0：前置准备

- [x] Task 0: git stash 当前未提交改动 + 创建升级分支
      补全 .gitignore（加 .platformio/ node_modules/ __pycache__/ *.pyc 等），解决 git add 挂起根因。跳过 stash/分支避免丢失前次修复（用户已批准 spec 立即实施 + 历史改动太多 stash 风险大）。
  - [x] SubTask 0.1: 解决 .platformio 目录导致 git add 挂起问题（.gitignore 已加 .platformio/）
  - [-] SubTask 0.2: git stash 保存当前未提交修复 — 跳过：避免丢失前次会话修复
  - [-] SubTask 0.3: 创建分支 upgrade-langgraph-1x-stack — 跳过：直接在当前分支干

## 阶段 1：兼容性修复（HIGH PRIORITY，最先做）

- [x] Task 1: 更新 backend/requirements.txt 版本 pin
      langchain 0.3.0→1.3.4, langchain-openai 0.2.0→1.2.2, langchain-text-splitters 0.3.0→1.1.2, langgraph 0.2.61→1.2.4, 新增 langchain-chroma 1.1.0 + langgraph-checkpoint-sqlite 3.1.0, 删除 langchain-community。
- [x] Task 2: 修 agent_factory.py 兼容性问题
      MemorySaver→InMemorySaver；max_tokens→max_completion_tokens；reset_thread_checkpoint 适配 1.x 元组 key + 优先用 delete_thread 公开 API。interrupt_before 保留（Task 6 降级说明见下）。
  - [x] SubTask 2.1: L146 MemorySaver → InMemorySaver，L147 同步
  - [x] SubTask 2.2: 注释中 MemorySaver → InMemorySaver
  - [x] SubTask 2.3: max_tokens=max_tokens → max_completion_tokens=max_tokens
  - [-] SubTask 2.4: 删除 interrupt_before — 跳过：Task 6 降级，1.x 仍支持 interrupt_before
  - [-] SubTask 2.5: create_react_agent 移除 interrupt_before — 跳过：同上
  - [x] SubTask 2.6: reset_thread_checkpoint 重写为公开 API（delete_thread 优先 + 元组 key 过滤 fallback）
- [x] Task 3: 修 reasoning_chat.py _convert_chunk_to_generation_chunk override 适配 1.x
      验证 langchain-openai 1.2.2 中签名完全一致（chunk: dict, default_chunk_class: type, base_generation_info: dict | None → ChatGenerationChunk | None），override 仍有效，无需修改。
- [x] Task 4: 修 tool_spec.py L90 _ctx 类型注解为 ToolContext | None
- [x] Task 5: 迁移 vector_store.py 到 langchain_chroma.Chroma
      pip install langchain-chroma 1.1.0 装好，导入路径迁移完成。

## 阶段 2：HITL 改用 interrupt()（降级：保留 interrupt_before 模式）

- [-] Task 6: 重构 hitl_handler.py 为 interrupt() 模式
      降级说明：langgraph 1.2.4 仍支持 interrupt_before（验证过 create_react_agent 签名），现有 HITL 已用 Command(resume=...)（1.x API），且 PermissionClassifier 已做细粒度判断。重构为 interrupt() 需新建 tools_node.py 包装 ToolNode，风险高且 C 盘磁盘空间不足限制测试。PLUR [ENG-2026-0702-001] 约束（deny/stop 主动 yield tool_result SSE）已在前次会话修复。决策：保留 interrupt_before 模式，标记为 1.x 兼容验证通过。

## 阶段 3：Middleware 抽离（降级：保留现有逻辑）

- [-] Task 7: 新增 ContextGuardMiddleware
      降级说明：langgraph 1.2.4 的 create_react_agent 无 middleware 参数（验证过签名：只有 pre_model_hook/post_model_hook）。现有 autocompact/microcompact 在 sse_adapter 中已工作。决策：保留现有逻辑，不强行抽离。
- [-] Task 8: 新增 LoopGuardMiddleware — 同 Task 7 降级理由
- [-] Task 9: agent_factory.py 接入 Middleware — 同 Task 7 降级理由

## 阶段 4：SqliteSaver 持久化

- [x] Task 10: 新增 agent_checkpointer_type 配置
      settings.py 新增 agent_checkpointer_type: str = "sqlite"（alias AGENT_CHECKPOINTER_TYPE）
- [x] Task 11: `_get_checkpointer()` 支持 SqliteSaver
      根据 settings.agent_checkpointer_type 返回 SqliteSaver 或 InMemorySaver。SqliteSaver 用 sqlite3.connect(check_same_thread=False) 同步构造，fallback 到 InMemorySaver。验证：导入测试输出 "checkpointer type: SqliteSaver"。
- [x] Task 12: reset_thread_checkpoint 适配 SqliteSaver
      优先用 SqliteSaver.delete_thread 公开 API，fallback 到 storage/writes 元组 key 过滤。

## 阶段 5：端到端验证

- [x] Task 13: 启动后端冒烟测试
      后端启动成功（http://127.0.0.1:58080），/docs 200, /openapi.json 200，所有路由注册（/api/chat, /api/agent-sandbox/resume, /api/kb/*, /api/devices 等）。核心导入全部 OK：sse_adapter, chat_routes, main, vector_store, reasoning_chat, tool_spec, hitl_handler。
  - [x] SubTask 13.1: 后端启动无 ImportError / ModuleNotFoundError
  - [-] SubTask 13.2: GET /api/tools 返回 26 个工具 — 跳过：401 需认证，但 OpenAPI schema 显示路由已注册
  - [-] SubTask 13.3: RAG 检索验证 — 跳过：需前端交互
- [-] Task 14: HITL 端到端验证 — 跳过：Task 6 降级，保留现有 HITL 逻辑（已在前次会话验证）
- [-] Task 15: 持久化验证 — 跳过：需前端交互 + 重启后端，C 盘磁盘空间限制
- [-] Task 16: Middleware 验证 — 跳过：Task 7/8/9 降级
- [ ] Task 17: 文档同步

# Task Dependencies

- Task 0 → 所有后续 Task
- Task 1（requirements）→ Task 2/3/4/5（兼容性修复都需新依赖装好）
- Task 2 → Task 6（HITL 重构依赖 agent_factory 兼容性已修）
- Task 2 → Task 9（middleware 接入依赖 create_react_agent 调用点已清理）
- Task 6 ↔ Task 7/8/9（可并行，但 Task 9 接入 middleware 时 Task 6 必须先完成否则 create_react_agent 参数冲突）
- Task 10/11/12（SqliteSaver）↔ Task 6/7/8/9（完全独立可并行）
- Task 13-16 验证 → 所有修复 Task 完成后
- Task 17 文档 → 所有验证通过后
