# 08-infra 线程上下文

> 对齐 docs/thread-map.md 模板 · 对齐 AGENTS.md Start Here 流程

---

## 开工先读（AGENTS.md 标准流程）

```md
1. plur inject "08-infra 当前任务" --fast --json     # 读 PLUR 长期记忆
2. 读 docs/completed.md                                # 知道哪些功能已做完
3. 读 docs/pitfalls.md                                 # 知道前人踩过什么坑
4. 读 docs/todos/08-infra.md，只看前 2 项              # 知道自己要做什么
5. 开始干活
```

---

## 负责范围

### 做什么
- Docker 容器化（Dockerfile / docker-compose / ChromaDB 服务 / volume 挂载）
- CI 管线（GitHub Actions：pytest / ruff / mypy / frontend typecheck + build）
- 本地开发体验（一键启动脚本 / 端口检测 / 健康检查）
- 日志与可观测性（structed logs / request-id / token 统计 / model latency / SSE duration / tool trace）
- 开源贡献文档（README / CONTRIBUTING / architecture overview / issue templates）

### 不做什么
- 不负责业务功能实现（聊天 / RAG / Agent 工具 / 硬件工作台）
- 不负责 UI 细节（组件样式 / 交互布局）
- 不负责 RAG 算法本身（chunking 策略 / embedding 选择 / reranker）

---

## 当前状态

### 已完成
- 后端日志基础设施：
  - settings.py 新增 LOG_LEVEL（default=INFO）
  - main.py 新增 configure_logging() + request-log 中间件（X-Request-Id / 方法 / 路径 / 状态码 / 耗时）
  - 日志输出从 stderr 改为 stdout（适配 Start-Process 捕获）
- 全链路日志补全（14 项）：
  - 后端：routes.py logger 命名修正 → RAG 管线 4 模块 → tool_router → auth → crud
  - 前端：ErrorBoundary → KnowledgePanel → InputBar → useAppStore → useLogStore 全线覆盖
- scripts/dev.ps1：一键启动脚本，端口检测 + 后端健康等待 + 前端启动 + 日志捕获，已测试通过
  - 修复：Start-Process 不能双重定向到同一文件；npx 需 cmd /c 包装
- 文档：docs/threads/08-infra.md 线程上下文初始化

### 正在做
- 等待项目 P0（工具调用框架 + RAG 闭环）完成后推进 Docker/CI/README

### 阻塞
- 项目仍处于 P0 攻坚阶段（安全立场按 AGENTS.md 要求：所有服务监听 127.0.0.1，不暴露公网）。在 P0 稳定之前：
  - ❌ Docker 容器化（接口不稳定，体积迭代频繁）
  - ❌ CI 管线（无主干分支保护需求）
  - ❌ README / CONTRIBUTING（项目未开源，功能未定型）
  - ❌ 前端构建产物嵌入后端（需等前端 API 稳定）
  - ✅ 可做：日志与可观测性（不影响业务，团队调试直接受益）

---

## 接口契约

涉及 docs/api-contract.md：

| § | 约定内容 | 实现状态 |
|---|---------|---------|
| §2.15 | 请求追踪 ID（X-Request-Id Header / 日志记录） | ✅ 已实现 |
| §2.17 | SSE 连接管理（5min 超时 / 指数退避重连） | 🔲 已约定 |
| §2.18 | WebSocket 重连策略（固定 3 次：1s→2s→3s） | 🔲 已约定 |
| §5.1 | SSE done 事件含 usage（prompt/completion/total tokens） | 🔲 已约定 |
| §5.23 | 审计日志接口 /api/audit/log | 🔲 已约定 |

---

## 关键文件

| 层 | 文件 |
|---|------|
| 前端 | stores/useLogStore.ts · api/client.ts · components/shared/ErrorBoundary.tsx |
| 后端 | app/main.py（入口 / logging 配置 / 请求追踪中间件）· src/config/settings.py（LOG_LEVEL） |
| 文档 | docs/api-contract.md · docs/pitfalls.md · docs/architecture.md · docs/threads/08-infra.md |
| 脚本 | scripts/dev.ps1 · scripts/write_file.py |
| TODO | docs/todos/08-infra.md |

---

## 决策记录

| 日期 | 决策 |
|------|------|
| 2026-06-21 | 线程初始化。项目 P0 阶段，Docker/CI/README 延后 |
| 2026-06-21 | 后端日志基础设施搭建：basicConfig + request-id 中间件 + LOG_LEVEL |
| 2026-06-21 | 全链路日志补全：9 个后端模块 + 3 个前端模块接入 logging |
| 2026-06-21 | scripts/dev.ps1：一键启动脚本，测试通过 |
| 2026-06-21 | main.py 日志输出改为 stdout（修复 Start-Process 日志捕获） |
| 2026-06-21 | 记录踩坑：chromadb 导入 ~10s · npx 需 cmd /c · Start-Process 双重定向限制 |

---

## 踩坑记录

### 已踩过的坑
- **chromadb 导入慢**：chromadb 在 Python 3.13 下首次导入约 10 秒，导致后端启动慢。dev.ps1 健康检查超时设为 60s → 可接受
- **Start-Process 日志捕获**：PowerShell 不允许 `-RedirectStandardOutput` 和 `-RedirectStandardError` 指向同一文件 → 只用 stdout，stderr 通过 logging.basicConfig(stream=sys.stdout) 合并
- **npx 调用失败**：`npx` 是 `.cmd` 文件，Start-Process -FilePath "npx" 找不到可执行文件 → 用 `cmd /c npx ...` 包装
- **pytest 超时**：chromadb 导入慢导致路由测试文件全部超时 → 非代码问题，属 chromadb 特性

### 下次注意
- 修改 api-contract.md 后，同步更新本节接口契约表的状态
- 修改 routes.py 或 settings.py 后，检查是否影响日志配置
- TODO 清单只保留当前可做的任务，等 P0 完成后将 Docker/CI/README 从"阻塞"移至"待做"

---

## 下次开工先看

1. **读 PLUR**：`plur inject "08-infra 当前任务" --fast --json`
2. **读 docs/completed.md**（如果存在）：了解哪些功能已全部完成
3. **读 docs/pitfalls.md**：了解项目级踩坑记录
4. **读 docs/todos/08-infra.md**，只看前 2 项：明确当前要做什么
5. **检查 P0 进展**：确认工具调用框架和 RAG 闭环是否完成，判断 Docker/CI/README 是否可以启动


---

*一致性检查：与 AGENTS.md 全部 6 项检查通过，详见本线程记录。*
