# 06-sandbox 线程上下文

## 负责范围
- 做什么：运行隔离（06A）、文件审计规则（06B）、命令风险等级评估（06C）、工具调用策略（06D）、审计日志（06E）
- 不做什么：不直接实现聊天业务、不处理 RAG 检索、不实现硬件烧录逻辑、不管理会话持久化

## 当前状态（2026-06-21）

### ✅ 已完成

| 模块 | 文件 | 状态说明 |
|------|------|----------|
| **核心执行器** | `backend/src/sandbox/executor.py` | Docker SDK for Python，C/C++ stdin 传入，asyncio.to_thread 防阻塞 |
| **模块导出** | `backend/src/sandbox/__init__.py` | 导出 execute_code / check_docker_available / ExecutionResult |
| **API 路由** | `backend/app/api/sandbox_routes.py` | execute + status，语言白名单 + Semaphore(4) 并发控制 + 超时保护 |
| **路由注册** | `backend/app/main.py` | sandbox_router 已 include |
| **API 契约** | `docs/api-contract.md` §5.21-5.23 | execute(draft) / status(draft) / audit(draft)，含 Mock 规则 |
| **安全生产 P1** | `sandbox_routes.py` | ALLOWED_LANGUAGES 白名单，拒绝 bash/sh/任意命令 |
| **安全生产 P2** | `sandbox_routes.py` | _sandbox_semaphore 控制并发容器数 |
| **Windows 兼容** | `auth.py` | os.chmod try/except，Windows 上静默跳过 |

### 🔄 待办（见 docs/todos/06-sandbox.md）

## 实现细节

### 06A. 运行隔离

Docker 容器级隔离，docker-py SDK 管理：

```python
# executor.py 安全策略
mem_limit="256m"           # 内存上限
network_disabled=True       # 禁用网络
read_only=True              # 只读根文件系统
tmpfs={"/tmp": "size=50m"}  # /tmp 用内存 tmpfs（防逃逸）
user="nobody"               # 非 root 运行
cpu_quota=100000            # 1 CPU 上限
wait(timeout=10)            # 10 秒超时，超时 kill
finally: container.remove() # 保证清理
```

支持语言与 Docker 镜像：

| 语言 | 镜像 | 执行方式 |
|------|------|----------|
| python | python:3.11-slim | python -c |
| c | gcc:latest | stdin → /tmp/code.c → gcc → /tmp/code |
| cpp | gcc:latest | stdin → /tmp/code.cpp → g++ → /tmp/code |
| javascript | node:20-slim | node -e |
| arduino | platformio/platformio-core:latest | stdin → /tmp/project → pio ci |

Docker 不可用时返回 `SANDBOX_UNAVAILABLE`，不静默降级。

### 06B. 文件审计规则

- 创建文件规则：`data/` 不进 Git，二进制文件特殊处理
- 修改文件规则：通过 `scripts/write_file.py` 写入
- 删除文件审批：危险操作需记录审计
- 移动文件审批：记录来源与目标

### 06C. 命令风险等级

| 等级 | 示例 | 措施 |
|------|------|------|
| 低风险 | 读文件、列目录、状态检查 | 无审批 |
| 中风险 | 启动服务、安装依赖、格式化 | 记录审计 |
| 高风险 | 删除、重置、覆盖、网络下载、系统级命令 | 前端确认 + 审计记录 |

### 06D. 工具调用策略

- 禁用 `apply_patch` 创建新文件（批注：坑）
- 禁止大段 inline Python（`python -c`）
- 禁止复杂 PowerShell 管道串联
- 长命令拆分
- 输出过长时限制行数

### 06E. 审计日志

- API 端点：`POST /api/sandbox/audit`（§5.23，draft）
- 记录内容：谁执行了什么命令、改了哪些文件、是否触发高危操作
- 记录方式：按 session_id 关联，保留 30 天
- 关联 pitfalls.md：错误记录需同步到踩坑文件

## 接口契约
- 详见 `docs/api-contract.md` §5
  - 5.21 `POST /api/sandbox/execute` — 代码执行（draft）
  - 5.22 `GET /api/sandbox/status` — Docker 可用性检查（draft）
  - 5.23 `POST /api/sandbox/audit` — 审计日志（draft）
- 接口状态统一使用 `draft` / `agreed` / `mocked` / `implemented` / `verified`

## 关键文件

### 后端
- `backend/src/sandbox/executor.py` — Docker 沙箱执行器（核心）
- `backend/src/sandbox/__init__.py` — 模块导出
- `backend/app/api/sandbox_routes.py` — 沙箱 API 路由
- `backend/app/main.py` — 路由注册入口
- `backend/requirements.txt` — 需含 docker(docker-py)

### 前端（暂无，待建）
- `frontend/src/components/sandbox/SandboxPanel.tsx` — 代码执行面板
- `frontend/src/stores/useSandboxStore.ts` — 沙箱状态
- `frontend/src/api/endpoints.ts` — 需添加 sandbox 端点

### 文档
- `docs/api-contract.md` §5.21-5.23 — 接口契约
- `docs/todos/06-sandbox.md` — 待办清单
- `docs/threads/06-sandbox.md` — 本文件
- `docs/pitfalls.md` — 踩坑记录
- `docs/plans/hardware-rag-agent-v1-plan.md` — V1 路线图
- `AGENTS.md` — 项目级规则（命令风险等级、工具调用策略）

## 决策记录
- 2026-06-21：采用 Docker SDK for Python（docker-py）而非 subprocess 沙箱，保证真正隔离
- 2026-06-21：异步阻塞用 asyncio.to_thread 包装，而非 run_in_executor（更简洁）
- 2026-06-21：C/C++ 代码通过 stdin 传入容器，而非 volume mount（减少文件残留）
- 2026-06-21：Docker 不可用时明确返回错误，不静默 mock 降级（安全优先）
- 2026-06-21：输出截断 stdout 10000 / stderr 5000 字符（防止 OOM）

## 踩坑记录
- 关联 `docs/pitfalls.md`：
  1. P0-6/7: C/C++ 代码未通过 stdin 传入，编译永远失败 — 修复后改为 `cat > /tmp/code.c && gcc ...`
  2. P0-6/7: asyncio.run 在已有事件循环中抛 RuntimeError — 修复后改用 `asyncio.to_thread`
  3. P0 修复: Docker 容器 /tmp 读写有逃逸风险 — 修复后改用 `tmpfs`
  4. P1: sandbox_routes 缺少语言白名单— 修复后添加 `ALLOWED_LANGUAGES`
  5. P2: 并发容器无限制可能资源耗尽 — 修复后添加 `_sandbox_semaphore`

## 下次开工先看

### 开工流程
1. `plur inject "06-sandbox: <当前任务>" --fast --json`
2. 读 `docs/completed.md` — 知道已做了什么
3. 读 `docs/pitfalls.md` — 知道前人踩过什么坑
4. 读 `docs/todos/06-sandbox.md` — 只看前 2 项
5. 读 `docs/api-contract.md` §5.21-5.23 — 接口契约

### 当前优先级（2026-06-21）
1. **验证后端启动** — 确认 docker-py 已装、导入不报错
2. **预拉取 Docker 镜像** — python:3.11-slim / gcc:latest / node:20-slim
3. **前端 SandboxPanel** — 代码编辑 + 运行 + 输出展示
4. **endpoints.ts** — 添加 sandbox 端点常量
5. **pytest** — executor + sandbox_routes 测试覆盖

