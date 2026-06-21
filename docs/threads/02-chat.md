# 02-chat — 聊天线程上下文

## 负责范围
- 做什么：
  - SSE 流式聊天（/api/chat）前后端完整闭环
  - 用户输入、发送、停止、重试、编辑重发
  - 消息渲染（Markdown、代码块、thinking 卡片、source 引用）
  - streaming 状态管理（isStreaming、streamingContent、streamingSteps）
  - 推理模型兼容（reasoning_content / thinking 字段）
  - RAG source 事件消费与展示
  - 分支对话（branchThread）
  - 收藏/引用/导出对话
- 不做什么：
  - 不负责知识库入库（03-knowledge）
  - 不负责 LangGraph Agent 工具编排（05-agent）
  - 不负责会话持久化 CRUD（04-session）
  - 不负责产品外壳与布局（01-app）

## 当前状态
- 已完成：
  - POST /api/chat SSE 事件流（thinking -> source -> text -> done / error）
  - sendMessage 前后端完整通路
  - stopStreaming（主动中止 + 部分结果保存）
  - retryMessage 与 editAndResend
  - branchThread 分支会话
  - 推理模型 thinking（reasoning_content / thinking / reasoning 字段兼容）
  - RAG source 引用展示
  - 普通模型占位 thinking 事件
  - 多会话流式隔离（streamingSessionId）
  - AbortController 外部传入以支持真正的中止
  - SSE JSON 解析连续 3 次失败触发错误
  - 后端测试 test_routes_chat.py（事件序列 + error -> done）
  - InputBar 模型选择、附件上传、Markdown 预览
  - 收藏/引用/导出功能
- 正在做：
  - （等待任务分配）
- 已修复：
  - #3 vite.config.ts test 配置移除（构建阻断）
  - #10 SSE tool args 改为 JSON 对象
  - #11 onDone 移到 stream 结束后触发
  - #13 添加 SSE 读超时（5 分钟无数据断开）
  - #21 onDone 非活跃会话清理流式状态
  - ReAct TODO 注释增强（保持可扩展性）
- 阻塞：
  - 无

## 接口契约
- 涉及 docs/api-contract.md：
  - S5.1 POST /api/chat — SSE 流式聊天（agreed）
  - S5.2 POST /api/models — 模型列表（agreed）
  - S5.16 POST /api/sessions/{id}/messages — 添加消息（agreed）
  - S5.17 GET /api/sessions/{id}/messages — 获取消息（agreed）

## 关键文件
- 前端：
  - frontend/src/stores/useChatStore.ts
  - frontend/src/api/client.ts
  - frontend/src/hooks/useSSE.ts
  - frontend/src/components/chat/ChatArea.tsx
  - frontend/src/components/chat/BranchTree.tsx
  - frontend/src/components/input/InputBar.tsx
  - frontend/src/types/session.ts
  - frontend/src/types/api.ts
- 后端：
  - backend/app/api/routes.py
  - backend/src/llm/client.py
  - backend/src/rag/vector_store.py
- 测试：
  - backend/tests/test_routes_chat.py
- 文档：
  - docs/api-contract.md S5.1
  - docs/pitfalls.md

## 决策记录
- 2026-06-21：SSE 客户端中将 onDone 移到 read loop 自然结束后，避免早发
- 2026-06-21：增加 SSE idle timeout（5分钟无数据断开），避免界面卡死
- 2026-06-21：非活跃会话的 onDone 也清理全局流式状态，避免 streaming 状态残留
- 2026-06-19：AbortController 必须从外部传入 apiSSE，避免 store 与 fetch 各持一个 controller 导致中止无效
- 2026-06-20：thinking step 合并必须检查 source 字段，不同来源（rag/llm/reasoning）不可合并
- 2026-06-20：普通模型不返回 reasoning_content，后端主动发 thinking 事件模拟思考状态
- 2026-06-20：streamingSessionId 标识流式请求所属会话，防止切换会话后状态写错

## 踩坑记录
- 关联 docs/pitfalls.md：
  - 2026-06-19：Zustand store 模板字符串语法被破坏导致全部功能失效
  - 2026-06-19：React 版本 15+ 组件功能缺失
  - 2026-06-20：AbortController 双实例导致切换会话时中止无效
  - 2026-06-20：思考卡片不显示 + Source 事件捕获 Bug + thinking step 合并错误
  - 2026-06-20：推理模型思考内容不显示 + Ollama 兼容性
  - 2026-06-20：Vite 代理端口配置错误导致前端无法连接后端

## 下次开工先看
1. 读 docs/thread-map.md 确认职责边界
2. 读 docs/api-contract.md 中聊天相关章节（S5.1）
3. 读 docs/pitfalls.md 中聊天相关踩坑
4. 修复错误后更新 docs/pitfalls.md
