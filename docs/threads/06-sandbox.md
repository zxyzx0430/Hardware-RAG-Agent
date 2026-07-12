# 06-sandbox 线程上下文 — DEPRECATED

> **状态：已废弃（2026-06-30）**
>
> Docker 沙箱代码已全部删除。原 Docker 隔离执行方案被废弃，原因：
>
> 1. 调研 Aider / Cline / Continue / Cursor / OpenHands 5 个成熟 AI 编程项目，4 个不用 Docker
> 2. 硬件 RAG Agent 是贴身副驾场景，不需要 Docker 隔离
> 3. 现有 Docker 沙箱前端 0 调用，是死代码
>
> **替代方案**：Agent 的 `run_command` 工具（本地 shell 执行 + 权限门控 + 审计日志）
>
> - 设计稿：`docs/superpowers/specs/2026-06-30-agent-react-design.md` 第 3、7、9.4 节
> - 接管线程：05-agent（Agent ReAct 主线）
>
> 以下内容仅作历史记录保留。

---

## 历史状态（2026-06-21 ~ 2026-06-30）

### 已删除的文件

| 文件 | 删除日期 | 说明 |
|------|---------|------|
| `backend/src/sandbox/executor.py` | 2026-06-30 | Docker 沙箱执行器 |
| `backend/src/sandbox/__init__.py` | 2026-06-30 | 模块导出 |
| `backend/app/api/sandbox_routes.py` | 2026-06-30 | 沙箱 API 路由 |
| `backend/requirements.txt` 的 `docker==7.1.0` | 2026-06-30 | Docker SDK 依赖 |

### 已删除的引用

| 位置 | 删除内容 |
|------|---------|
| `backend/app/main.py` L35 | `from app.api.sandbox_routes import router as sandbox_router` |
| `backend/app/main.py` L309 | `app.include_router(sandbox_router)` |
| `backend/src/agent/tool_router.py` L309-334 | `CodeExecutorTool` 注册 |
| `frontend/src/components/settings/SettingsPage.tsx` L22 | `code_executor` 工具选项 |
| `frontend/src/stores/useSettingsStore.ts` L110 | `code_executor` 工具配置 |

### 已标记 deprecated 的契约

- `docs/api-contract.md` §5.21 `POST /api/sandbox/execute` — DEPRECATED
- `docs/api-contract.md` §5.22 `GET /api/sandbox/status` — DEPRECATED
- `docs/api-contract.md` §5.23 `POST /api/sandbox/audit` — DEPRECATED

---

## 历史实现细节（仅作参考）

### 原 06A. 运行隔离（已废弃）

Docker 容器级隔离，docker-py SDK 管理：

```python
# 原 executor.py 安全策略（已删除）
mem_limit="256m"           # 内存上限
network_disabled=True       # 禁用网络
read_only=True              # 只读根文件系统
tmpfs={"/tmp": "size=50m"}  # /tmp 用内存 tmpfs（防逃逸）
user="nobody"               # 非 root 运行
cpu_quota=100000            # 1 CPU 上限
wait(timeout=10)            # 10 秒超时，超时 kill
finally: container.remove() # 保证清理
```

### 原 06B-06E（已废弃）

- 06B 文件审计规则 → 改由 Agent `permission_gate.py` + `audit_logger.py` 实现
- 06C 命令风险等级 → 改由 Agent `risk_classifier.py` 实现（HIGH/MEDIUM/LOW 三级）
- 06D 工具调用策略 → 改由 LangGraph `create_react_agent` + `interrupt_before=["tools"]` 实现
- 06E 审计日志 → 改由 Agent `audit_logger.py` 写入 SQLite `tool_audit` 表

---

## 历史踩坑记录（保留，供 Agent 实现参考）

1. **C/C++ 代码未通过 stdin 传入** → 编译永远失败 → 修复：`cat > /tmp/code.c && gcc ...`
2. **asyncio.run 在已有事件循环中抛 RuntimeError** → 修复：改用 `asyncio.to_thread`
3. **Docker 容器 /tmp 读写有逃逸风险** → 修复：改用 `tmpfs`
4. **sandbox_routes 缺少语言白名单** → 修复：添加 `ALLOWED_LANGUAGES`
5. **并发容器无限制可能资源耗尽** → 修复：添加 `_sandbox_semaphore`

**Agent 时代注意**：
- 上述 1-5 都是 Docker 沙箱特有问题，`run_command` 工具不存在这些问题
- 但 `run_command` 有新风险：直接执行用户本机 shell，必须配合权限门控（spec 第 7 节）
