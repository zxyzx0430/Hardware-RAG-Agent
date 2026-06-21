# 08-infra 线程上下文

## 负责范围
- 做什么：Docker 化部署、CI/CD、结构化日志、请求追踪、可观测性、token 统计、README 与开源贡献文档
- 不做什么：业务功能实现、UI 细节、RAG 算法本身

## 当前状态
- 已完成：
  - 前端 useLogStore 已在多个模块投入使用，SettingsPage 有日志面板
  - 全链路日志审计与补全（9 个后端模块 + 2 个前端模块）：
    1. routes.py：logger 命名从硬编码 "api" 修正为 __name__
    2. document_loader.py：print→logger 替换，下载/校验/失败全面覆盖
    3. document_processor.py：print→logger，解析/翻译/保存阶段全记录
    4. file_parsers.py：新增 logger，编码检测失败有 warning
    5. pipeline.py：print→logger，管线三个阶段（下载/处理/入库）全记录
    6. tool_router.py：工具注册/调度/超时全部有日志
    7. auth.py：API Key 存储/删除操作记录
    8. crud.py：会话/消息 CRUD 操作全记录
    9. ErrorBoundary.tsx：前端崩溃记录到 useLogStore
    10. useAppStore.ts：接入 useLogStore（UI 面板开关日志）
    11. KnowledgePanel.tsx：上传成功/失败/轮询状态日志
    12. InputBar.tsx：附件处理失败日志
    13. useAppStore.ts：主题/语言/导航切换日志补齐
  - scripts/dev.ps1：一键开发启动脚本 — 端口检测/后端启动+健康等待/前端启动/日志捕获，已测试通过
  - 后端 main.py 日志输出从 stderr 改为 stdout，确保 Start-Process 能正确捕获
  - 后端健康检查超时调整为 60s（chromadb 导入需 ~10s）
  - 修复 Start-Process 不能同时重定向 stdout/stderr 到同一文件的问题
  - 使用 cmd /c 包装 npx，解决 .cmd 文件无法被 Start-Process 直接调用的问题
  - 后端日志基础设施搭建完成：
    1. settings.py 新增 LOG_LEVEL 字段（默认 INFO），支持通过环境变量调整
    2. main.py 新增 configure_logging() 统一日志格式：时间 级别 模块 消息
    3. main.py 新增 request-log 中间件：每个请求自动分配 UUID，记录方法/路径/状态码/耗时，返回 X-Request-Id 响应头（对应 api-contract.md §2.15）
    4. .env.example 新增 LOG_LEVEL 配置项
- 正在做：无
- 阻塞：项目处于 P0 攻坚阶段（工具调用框架 + RAG 闭环未就绪），Docker/CI/README 等基础设施需等业务稳定后再推进

## 接口契约
- 涉及 docs/api-contract.md：
  - §2.15 请求追踪 ID：✅ 已实现 — 后端返回 X-Request-Id Header
  - §2.17 SSE 连接管理：5 分钟超时断连、指数退避重连（已约定，未实现）
  - §2.18 WebSocket 重连策略（已约定）
  - §5.1 SSE done 事件含 usage 字段（已约定）
  - §5.23 审计日志接口 /api/audit/log（已约定）

## 关键文件
- 前端：frontend/src/stores/useLogStore.ts、frontend/src/api/client.ts
- 后端：backend/app/main.py（入口，含 logging 配置 + 请求追踪中间件）、backend/src/config/settings.py（含 LOG_LEVEL）
- 文档：docs/api-contract.md、docs/pitfalls.md、docs/architecture.md

## 决策记录
- 2026-06-21：线程初始化。项目处于 P0 阶段，Docker/CI/README 延后
- 2026-06-21：全链路日志补全：RAG 管线/tool_router/auth/crud/routes 加日志，ErrorBoundary/useAppStore 接入前端日志
- 2026-06-21：创建 scripts/dev.ps1 — 一键启动脚本
- 2026-06-21：前端日志补齐 — KnowledgePanel（上传）、InputBar（附件）、useAppStore（UI 事件）
- 2026-06-21：完成后端日志基础设施：
  - 日志格式：asctime levelname name message
  - LOG_LEVEL 支持 DEBUG/INFO/WARNING/ERROR 切换
  - 请求追踪中间件覆盖所有接口

## 踩坑记录
- 关联 docs/pitfalls.md：待补充

## 下次开工先看
1. 检查项目当前开发进度与 P0 完成情况
2. 运行 scripts/dev.ps1 体验一键启动流程，收集反馈
3. 排查 chromadb 导入慢导致 pytest 超时的问题
4. 为各关键路由补充结构化日志（RAG 检索耗时、LLM token 用量、工具调用轨迹）
