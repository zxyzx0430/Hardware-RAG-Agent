# Sandbox 前端体验 — 跨线程交接说明

> 创建：2026-06-30
> 来源线程：06-sandbox（Trae / 前端体验）
> 关联设计稿：[docs/design/specs/2026-06-30-sandbox-frontend-design.md](../design/specs/2026-06-30-sandbox-frontend-design.md)
> 状态：设计待审，实现未启动

---

## 一句话背景

把 "Sandbox" 重新定义为 **Agent 工作沙箱**（本地操作 + 3 模式权限门控），废弃现有 Docker 沙箱。06-sandbox 线程负责前端体验 + 配套后端端点，05-agent 线程负责工具实现 + 门控逻辑，00-control 协调删除旧代码。

---

## 各线程职责

### 00-control（协调删除旧代码）

实现前必须先完成以下删除（否则 `sandbox_routes.py` 命名冲突）：

| 文件 | 操作 |
|---|---|
| `backend/app/api/sandbox_routes.py` | 删除（旧 Docker 沙箱 API） |
| `backend/src/sandbox/executor.py` + `__init__.py` | 删除整个 `src/sandbox/` 目录 |
| `backend/src/agent/tool_router.py` L309-334 | 移除 `code_executor` 工具注册 |
| `backend/requirements.txt` L27 | 移除 `docker==7.1.0` |
| `backend/app/main.py` | 移除 `sandbox_router` import 和 include |
| `docs/api-contract.md` §5.21-5.23 | 标记为 `deprecated` 或重写 |
| `docs/threads/06-sandbox.md` | 更新为前端体验定位 |
| `docs/completed.md` | 同步删除 Docker 沙箱相关完成记录 |

**前置验证**：删除后跑 `pytest` 确保无导入错误，跑前端 `tsc --noEmit` 确保无类型错误。

### 05-agent（工具实现 + 门控逻辑）

按设计稿契约实现以下 5 个模块（设计稿 §2.2 标注为外部依赖）：

#### 1. 4 个本地工具（注册到 `tool_router.py`）

| 工具 | 参数 | 返回 |
|---|---|---|
| `run_command` | `command: str, timeout_ms: int=30000, cwd: str` | `{stdout, stderr, exit_code, duration_ms, timed_out}` |
| `write_file` | `path: str, content: str` | `{bytes_written, path}` |
| `read_file` | `path: str, offset: int=0, limit: int=2000` | `{content, total_lines, truncated}` |
| `edit_file` | `path: str, old_string: str, new_string: str, replace_all: bool=false` | `{replacements_made, path}` |

所有 `path` 必须是**绝对路径**，经 `path_guard` 校验。

#### 2. `permission_gate.py`（核心门控）

读 `data/sandbox.db` 的 `sandbox_policy` 表的 `mode` 字段，按设计稿 §3.1 流程决策：
- `full_open` → 直接 allow
- `ask_all` → SSE 推 `tool_pending`，等前端 POST `/api/sandbox/decide`
- `auto_review` → 调 `risk_classifier`，按风险等级决定 allow/ask

#### 3. `risk_classifier.py`（V1 关键字黑名单）

| 等级 | 关键字示例 |
|---|---|
| HIGH | `rm -rf`, `del /s`, `format`, `diskpart`, `regedit`, `shutdown` |
| MEDIUM | `pip install`, `npm install`, `git push`, `git reset --hard`, `curl`, `wget` |
| LOW | `ls`, `dir`, `cat`, `type`, `echo`, `python xxx.py`, `node xxx.js` |

V2 升级为 LLM 分类（参考 Claude-Code `yoloClassifier`），不在本次范围。

#### 4. `path_guard.py`（路径校验）

- 允许目录：项目根 + `sandbox_policy.whitelist_dirs` + 系统临时目录
- 强制 deny：`.git/`, `.vscode/`, `.idea/`, `.claude/`, `settings.json`, `.env`, `*.key`, `*.pem`, `*credentials*`
- Windows 路径用 `os.path.normcase` 统一大小写

#### 5. `audit_logger.py`（写审计日志）

写入 `data/sandbox.db` 的 `sandbox_audit` 表（schema 见设计稿 §5.1 `AuditLog` 接口），保留 30 天。

#### 与 06-sandbox 的契约

**SSE 事件（05-agent 推，前端消费）**：
```typescript
// 事件类型: tool_pending
{
  type: 'tool_pending',
  data: {
    id: string,                // tool_use_id
    tool_name: 'run_command' | 'write_file' | 'read_file' | 'edit_file',
    args: Record<string, unknown>,
    risk_level: 'low' | 'medium' | 'high',
    cwd?: string,
  }
}
```

**等待前端确认**：
05-agent 推 `tool_pending` 后，挂起协程等待。后端 `/api/sandbox/decide` 端点收到前端响应后，通过 `asyncio.Event` 或 `asyncio.Queue` 唤醒等待中的 05-agent 协程。

**超时**：5 分钟未收到前端响应，05-agent 默认 deny。

### 06-sandbox（本线程，前端体验 + 配套后端）

#### 前端
- `frontend/src/components/workbench/SandboxPolicyBar.tsx`
- `frontend/src/components/workbench/SandboxConfirmDialog.tsx`
- `frontend/src/components/workbench/SandboxAuditPanel.tsx`
- `frontend/src/components/workbench/sandboxConstants.ts`
- `frontend/src/stores/useSandboxStore.ts`
- 修改 `frontend/src/components/workbench/WorkbenchPanel.tsx` 追加 `sandbox_audit` Tab

#### 后端
- `backend/app/api/sandbox_routes.py`（新建，覆盖现有——需 00-control 先删除）
- 4 个端点：
  - `GET /api/sandbox/policy`
  - `POST /api/sandbox/policy`
  - `GET /api/sandbox/audit`
  - `POST /api/sandbox/decide`

#### 数据库
`data/sandbox.db`（与 sessions.db 同级），两张表：
- `sandbox_policy`（key-value 存 mode/whitelist_dirs/project_root）
- `sandbox_audit`（审计日志，schema 见设计稿 §5.1）

---

## 启动顺序

1. **00-control 先做**：删除旧 Docker 沙箱代码（§00-control 章节）
2. **06-sandbox 做**：前端组件 + 后端端点 + 数据库表
3. **05-agent 做**：4 个工具 + 门控逻辑 + 风险分类 + 路径校验 + 审计写入
4. **联合调试**：mock SSE 走通前端 → 后端 → 05-agent 全链路

06-sandbox 和 05-agent 可并行开发（契约已对齐），但都依赖 00-control 的删除完成。

---

## 关键决策记录

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-06-30 | 废弃 Docker 沙箱，Sandbox 改为本地操作 | 5 个 GitHub 成熟项目 4 个不用 Docker，硬件 RAG Agent 是贴身副驾场景 |
| 2026-06-30 | 3 模式（ask_all/auto_review/full_open） | 对应 Claude-Code 的 default/acceptEdits/bypassPermissions |
| 2026-06-30 | V1 用关键字黑名单，V2 升级 LLM 分类 | Claude-Code 外部版也是 stub，LLM 分类是 ANT-ONLY |
| 2026-06-30 | 06-sandbox 负责前端体验 + 配套后端，不碰工具实现 | 职责清晰，与 05-agent 解耦并行 |
