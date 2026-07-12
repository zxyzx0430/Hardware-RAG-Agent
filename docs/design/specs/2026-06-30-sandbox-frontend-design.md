# Sandbox 前端体验设计稿

> 日期：2026-06-30
> 线程：06-sandbox（前端体验线程 / Trae）
> 任务：D12（主线提前完成才做）
> 状态：设计待审

---

## 1. 背景与定位

### 1.1 概念澄清

项目原有的 "sandbox" 指 Docker 隔离跑代码（[sandbox_routes.py](file:///e:/Desktop/agent/backend/app/api/sandbox_routes.py) + [executor.py](file:///e:/Desktop/agent/backend/src/sandbox/executor.py)），属于历史遗留，**前端 0 调用，死代码**。

本设计稿将 "Sandbox" 重新定义为：**Agent 工作沙箱** —— AI 在用户本机操作文件 + 跑命令的能力，配合 3 种权限模式门控。参考 Claude Code / Cline / Aider 的本地操作 + 审批门做法，不使用 Docker 隔离。

### 1.2 为什么不用 Docker

调研 5 个 GitHub 成熟 AI 编程工具：

| 项目 | Docker | 原因 |
|---|---|---|
| Aider | ❌ | 贴身副驾，git commit 兜底 |
| Cline | ❌ | 逐条审批 + diff 回退 + checkpoint |
| Continue | ❌ | 编辑器 diff/undo |
| Cursor 本地 | ❌ | 命令逐条批准 |
| OpenHands | ✅ | 20 分钟全自主场景，必须强隔离 |

硬件 RAG Agent 是贴身副驾场景（用户主动问、AI 答完就停），不是 OpenHands 那种长链路自主。Docker 对嵌入式开发者还是重依赖（Windows 需 Docker Desktop + WSL2）。故选本地操作。

### 1.3 3 种权限模式

| 模式 | 英文名 | 行为 |
|---|---|---|
| 全部询问 | `ask_all` | 每个工具调用都弹窗确认 |
| 自动审查 | `auto_review` | 低风险自动放行，中/高风险弹窗 |
| 完全放开 | `full_open` | 跳过所有权限检查，直接执行 |

模式 + 工作目录范围运行时可切换，下次工具调用生效。

---

## 2. 范围与职责边界

### 2.1 本设计稿涵盖（06-sandbox 前端体验线程）

**前端**：
- `frontend/src/components/workbench/SandboxPolicyBar.tsx`
- `frontend/src/components/workbench/SandboxConfirmDialog.tsx`
- `frontend/src/components/workbench/SandboxAuditPanel.tsx`
- `frontend/src/components/workbench/sandboxConstants.ts`
- `frontend/src/stores/useSandboxStore.ts`

**后端（仅配合前端）**：
- `backend/app/api/sandbox_routes.py`（覆盖现有，需先删除旧代码）
- 策略状态表 SQLite schema
- 4 个 HTTP 端点：策略 CRUD + 审计日志查询 + 确认回传

**契约**：
- 与 05-agent 线程的 SSE 事件格式
- 策略状态表读写约定

### 2.2 本设计稿不涵盖（外部依赖）

**05-agent 线程负责**：
- 4 个本地工具的 run 实现（`run_command` / `write_file` / `read_file` / `edit_file`）
- `permission_gate.py` 门控逻辑
- `risk_classifier.py` 风险分级（V1 关键字 / V2 LLM）
- `path_guard.py` 路径校验
- `audit_logger.py` 写审计日志

**00-control 协调删除（实现前必须先完成）**：
- 删除 `backend/app/api/sandbox_routes.py`（旧 Docker 沙箱 API）
- 删除 `backend/src/sandbox/executor.py` + `__init__.py`
- 移除 `backend/src/agent/tool_router.py` L309-334 的 `code_executor` 工具注册
- 移除 `backend/requirements.txt` L27 `docker==7.1.0`
- 更新 `docs/api-contract.md` §5.21-5.23 状态
- 更新 `docs/threads/06-sandbox.md`

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────────┐
│ 前端 (React)                                          │
│  ├─ SandboxPolicyBar      策略切换条（聊天输入框上方）  │
│  ├─ SandboxConfirmDialog  确认弹窗（SSE 触发）         │
│  ├─ SandboxAuditPanel     审计日志面板（Workbench Tab）│
│  └─ ToolStep 卡片         复用现有 ChatArea.tsx 渲染   │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / SSE
┌──────────────────────┴──────────────────────────────┐
│ 后端 (FastAPI)                                       │
│  ├─ sandbox_routes.py        本设计稿：策略/审计/确认  │
│  ├─ tool_router.py           05-agent：4 个本地工具   │
│  ├─ permission_gate.py       05-agent：门控逻辑       │
│  ├─ risk_classifier.py       05-agent：风险分级       │
│  ├─ path_guard.py            05-agent：路径校验       │
│  └─ audit_logger.py          05-agent：写日志         │
└──────────────────────┬──────────────────────────────┘
                       │ subprocess
┌──────────────────────┴──────────────────────────────┐
│ 用户本机 (Windows)                                    │
│  └─ 项目目录 + 白名单目录（可读写）                    │
│     强制 deny：.git/, .vscode/, settings.json, .env   │
└─────────────────────────────────────────────────────┘
```

### 3.1 与 05-agent 的交互流程

```
05-agent 调工具前
    ↓ 读策略状态（本设计稿维护的 SQLite 表）
    ↓ 按 mode 决策
    ├─ full_open       → ALLOW，直接执行
    ├─ ask_all         → ASK，SSE 推 tool_pending
    └─ auto_review
        ├─ risk=low    → ALLOW，直接执行
        ├─ risk=medium → ASK，SSE 推 tool_pending
        └─ risk=high   → ASK，SSE 推 tool_pending
                    ↓
              06-sandbox 前端弹窗
                    ↓ 用户选择
              06-sandbox POST /api/sandbox/decide
                    ↓
              05-agent 收到结果，继续/中断
```

---

## 4. 前端组件设计

### 4.1 文件清单

| 文件 | 职责 | 行数预估 |
|---|---|---|
| `SandboxPolicyBar.tsx` | 策略切换条 | ~120 |
| `SandboxConfirmDialog.tsx` | 确认弹窗 | ~180 |
| `SandboxAuditPanel.tsx` | 审计日志面板 | ~150 |
| `sandboxConstants.ts` | 常量（模式枚举、风险等级、颜色） | ~30 |
| `useSandboxStore.ts` | 状态管理 | ~200 |

所有文件位于 `frontend/src/components/workbench/`，符合任务独占要求。

### 4.2 SandboxPolicyBar

**位置**：聊天输入框上方，一行高度约 32px。

**展示**：
```
[模式: 自动审查 ▼]  [范围: 项目目录+白名单 ▼]  [待确认: 0]  [审计日志]
```

**交互**：
- 点"模式"下拉：3 选项（全部询问 / 自动审查 / 完全放开）
- 点"范围"下拉：勾选项（项目目录默认勾选 / 白名单目录可多选 / "添加目录..."打开目录选择器）
- "待确认"数字 > 0 时高亮闪烁，点击跳到最新待确认弹窗
- "审计日志"按钮打开 SandboxAuditPanel

**实时切换**：用户切换模式或范围后，立即 POST 到后端，下次输入生效。当前正在执行的工具不受影响，下一个工具调用按新策略。

### 4.3 SandboxConfirmDialog

**触发**：05-agent 通过 SSE 推 `tool_pending` 事件时弹出。

**位置**：聊天区域顶部覆盖层（不挡住消息流，类似 IDE 的通知条）。

**结构**：
```
┌─────────────────────────────────────────────────────┐
│ ⚠ AI 要执行操作（风险: 中）                    [×]  │
├─────────────────────────────────────────────────────┤
│ 工具: run_command                                    │
│ 命令: pip install requests                           │
│ 工作目录: e:\Desktop\agent                           │
├─────────────────────────────────────────────────────┤
│ [允许本次] [永久允许] [拒绝] [拒绝并停止]            │
└─────────────────────────────────────────────────────┘
```

**4 个按钮**：
| 按钮 | decision | scope | 说明 |
|---|---|---|---|
| 允许本次 | `allow` | `temporary` | 仅本次放行 |
| 永久允许 | `allow` | `permanent` | 写入白名单规则 |
| 拒绝 | `deny` | `temporary` | 拒绝本次 |
| 拒绝并停止 | `deny` | `temporary` + `interrupt=true` | 拒绝并中断整个 Agent 循环 |

**多任务排队**：多个 pending 显示"1/3"分页，处理完一个显示下一个。

**永久允许规则预填**：点"永久允许"时展开一行输入框，预填自动生成的规则（如 `pip install *`），用户可编辑后确认。

### 4.4 SandboxAuditPanel

**位置**：WorkbenchPanel 第 6 个 Tab"沙箱审计"（和 serial/flash/preview/wiring/safety 并列）。

需修改 `frontend/src/components/workbench/WorkbenchPanel.tsx` 的 `TAB_IDS` 数组，追加 `"sandbox_audit"`。

**展示**：审计日志表格，按时间倒序。

| 时间 | 工具 | 参数摘要 | 决策 | 风险 | 耗时 | 退出码 |
|---|---|---|---|---|---|---|
| 14:32:15 | run_command | pip install requests | 允许(用户) | 中 | 2.3s | 0 |
| 14:31:50 | write_file | src/main.py | 允许(自动) | 低 | 5ms | 0 |

**过滤**：
- 按决策：全部 / 仅拒绝 / 仅询问
- 按工具：全部 / run_command / write_file / read_file / edit_file
- 按会话：当前会话 / 所有会话

**分页**：每页 50 条，可加载更多。

### 4.5 复用现有 ToolStep 卡片

[ChatArea.tsx:576](file:///e:/Desktop/agent/frontend/src/components/chat/ChatArea.tsx) 的 `ToolStep` 组件已支持工具调用卡片渲染（折叠：工具名+参数预览+耗时；展开：Input+Output）。沙箱工具调用**直接复用**，不新建消息类型。

需扩展：`ToolStep` 增加 `pending` 状态（等待用户确认时显示"待确认..."spinner）。

---

## 5. Store 设计（useSandboxStore）

### 5.1 状态结构

```typescript
interface SandboxState {
  // 策略状态
  mode: 'ask_all' | 'auto_review' | 'full_open';
  projectRoot: string;        // 项目根目录
  whitelistDirs: string[];    // 用户白名单目录
  
  // 待确认队列
  pendingQueue: PendingTool[];
  currentPending: PendingTool | null;
  
  // 审计日志缓存
  auditLogs: AuditLog[];
  auditFilters: {
    decision: 'all' | 'deny' | 'ask';
    tool: 'all' | 'run_command' | 'write_file' | 'read_file' | 'edit_file';
    sessionId: 'current' | 'all';
  };
  auditPage: number;
  auditHasMore: boolean;
  
  // 连接状态
  connected: boolean;          // SSE 连接状态
  
  // Actions
  loadPolicy: () => Promise<void>;
  setMode: (mode: SandboxMode) => Promise<void>;
  addWhitelistDir: (path: string) => Promise<void>;
  removeWhitelistDir: (path: string) => Promise<void>;
  submitDecision: (decision: ToolDecision) => Promise<void>;
  loadAuditLogs: (reset: boolean) => Promise<void>;
  setAuditFilters: (filters: Partial<AuditFilters>) => void;
}

interface PendingTool {
  id: string;                  // tool_use_id
  toolName: 'run_command' | 'write_file' | 'read_file' | 'edit_file';
  args: Record<string, unknown>;
  riskLevel: 'low' | 'medium' | 'high';
  cwd?: string;
  timestamp: number;
}

interface ToolDecision {
  toolUseId: string;
  decision: 'allow' | 'deny';
  scope: 'temporary' | 'permanent';
  rule?: string;               // scope=permanent 时必填
  interrupt?: boolean;         // decision=deny 时可填
}

interface AuditLog {
  id: string;
  timestamp: number;
  sessionId: string;
  toolName: string;
  argsSummary: string;         // 前 200 字符
  decision: 'allow' | 'ask' | 'deny';
  decisionSource: string;      // mode_bypass / classifier_low / user_temporary 等
  riskLevel: 'low' | 'medium' | 'high' | 'na';
  exitCode: number | null;
  durationMs: number | null;
  error: string | null;
}
```

### 5.2 SSE 事件订阅

Store 初始化时订阅 SSE 流，监听 `tool_pending` 事件：

```typescript
// 伪代码
eventSource.addEventListener('tool_pending', (e) => {
  const pending: PendingTool = JSON.parse(e.data);
  state.pendingQueue.push(pending);
  if (!state.currentPending) {
    state.currentPending = state.pendingQueue.shift();
  }
});
```

---

## 6. 后端设计（sandbox_routes.py）

### 6.1 端点清单

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/sandbox/policy` | 查询当前策略（mode + whitelist_dirs） |
| POST | `/api/sandbox/policy` | 更新策略（mode 或 whitelist_dirs） |
| GET | `/api/sandbox/audit` | 查询审计日志（支持过滤分页） |
| POST | `/api/sandbox/decide` | 用户确认结果回传（05-agent 监听） |

### 6.2 策略状态表（SQLite）

```sql
CREATE TABLE IF NOT EXISTS sandbox_policy (
  key TEXT PRIMARY KEY,        -- 'mode' / 'whitelist_dirs' / 'project_root'
  value TEXT NOT NULL,         -- JSON 字符串
  updated_at INTEGER NOT NULL  -- Unix 毫秒
);

-- 初始数据
INSERT OR IGNORE INTO sandbox_policy (key, value, updated_at) VALUES
  ('mode', '"ask_all"', 0),
  ('whitelist_dirs', '[]', 0),
  ('project_root', '""', 0);
```

数据库文件：`data/sandbox.db`（与现有 sessions.db 同级）。

05-agent 在 `permission_gate.py` 中读这个表做决策。

### 6.3 端点详情

#### GET /api/sandbox/policy

```json
// 响应
{
  "success": true,
  "data": {
    "mode": "auto_review",
    "project_root": "e:\\Desktop\\agent",
    "whitelist_dirs": ["d:\\projects\\lib"]
  }
}
```

#### POST /api/sandbox/policy

```json
// 请求体（任一字段可选，只更新提供的）
{
  "mode": "ask_all",
  "whitelist_dirs_add": ["d:\\new_dir"],
  "whitelist_dirs_remove": ["d:\\old_dir"]
}
// 响应
{ "success": true, "data": { /* 同 GET */ } }
```

#### GET /api/sandbox/audit

查询参数：
- `session_id`（可选，默认当前会话）
- `decision`（可选：`all` / `allow` / `ask` / `deny`）
- `tool`（可选：`all` / `run_command` / `write_file` / `read_file` / `edit_file`）
- `page`（可选，默认 1）
- `page_size`（可选，默认 50，上限 200）

```json
// 响应
{
  "success": true,
  "data": {
    "logs": [/* AuditLog[] */],
    "total": 142,
    "page": 1,
    "page_size": 50,
    "has_more": true
  }
}
```

#### POST /api/sandbox/decide

```json
// 请求体
{
  "tool_use_id": "tu_abc123",
  "decision": "allow",
  "scope": "temporary",
  "rule": "pip install *",
  "interrupt": false
}
// 响应
{ "success": true }
```

后端收到后：
1. 如果 `scope=permanent`，把 `rule` 写入 `sandbox_policy` 的 `allow_rules`（新增 key）
2. 通过内存事件总线（asyncio.Queue 或 Redis pub/sub）通知 05-agent 等待中的协程

### 6.4 与 05-agent 的契约

#### SSE 事件（05-agent 推，前端消费）

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

#### 确认回传（前端调，05-agent 监听）

通过 `POST /api/sandbox/decide`，后端用 `asyncio.Event` 或 `asyncio.Queue` 把结果传给等待中的 05-agent 协程。

#### 策略状态共享

05-agent 在 `permission_gate.py` 中：
1. 每次工具调用前，读 `sandbox_policy` 表的 `mode` 字段
2. 按 mode 决策（详见 §3.1 流程图）
3. `full_open` 直接 allow
4. `ask_all` 直接 ask（SSE 推 tool_pending）
5. `auto_review` 调 `risk_classifier`，按风险等级决定 allow/ask

---

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| 后端不可用 | PolicyBar 显示"沙箱离线"红色标记，模式降级为 `ask_all`（最安全） |
| SSE 断连 | pending 队列保留，重连后 05-agent 重推最新 pending |
| 用户 5 分钟未响应 | 05-agent 默认 deny（避免 Agent 卡死），前端弹窗自动关闭 |
| 策略表读取失败 | 05-agent 默认 `ask_all`（最安全） |
| 审计日志查询失败 | AuditPanel 显示错误提示，支持重试 |

---

## 8. 测试策略

### 8.1 前端单元测试

- `SandboxPolicyBar.test.tsx`：模式切换、目录增删、待确认计数
- `SandboxConfirmDialog.test.tsx`：4 个按钮回调、永久允许规则编辑、多任务分页
- `SandboxAuditPanel.test.tsx`：过滤、分页、空状态
- `useSandboxStore.test.ts`：pending 队列进出、决策提交、SSE 事件订阅

### 8.2 后端测试

- `test_sandbox_routes.py`：4 个端点的 CRUD + 边界条件
- 策略表读写一致性
- decide 端点的事件通知

### 8.3 端到端

mock SSE 推 `tool_pending` → 弹窗 → 用户点"允许本次" → POST /decide → 05-agent 收到结果继续执行。

---

## 9. 实现阶段划分

### V1（本设计稿范围）
- 3 模式权限门控（规则匹配，不走 LLM）
- 4 个前端组件 + Store + 后端 2 个端点
- 关键字黑名单（HIGH/MEDIUM/LOW 三级）
- 审计日志 SQLite 存储

### V2（未来升级，不在本设计稿）
- 自动审查模式调 LLM 判断命令风险（参考 Claude-Code `yoloClassifier`）
- 永久允许规则持久化到 settings
- 审计日志导出（CSV/JSON）
- 危险命令实时告警通知

---

## 10. 风险与待确认

| 风险 | 缓解 |
|---|---|
| 05-agent 线程未实现 `permission_gate` / `risk_classifier` | 本设计稿提供契约，05-agent 按契约实现。可先用 mock 走通前端流程 |
| 现有 Docker 沙箱代码未删除会导致命名冲突 | 实现前必须先完成 §2.2 列出的 00-control 协调删除项 |
| Windows 路径大小写敏感问题 | `path_guard` 用 `os.path.normcase` 统一处理（05-agent 负责） |
| SSE 长连接断连后 pending 丢失 | 05-agent 重连时重推未决 pending（前端队列保留已显示的） |

---

## 11. 参考资料

- [Claude-Code 源码研究](https://github.com/pengchengneo/Claude-Code)：权限模式 5 种、`yoloClassifier` LLM 分类、`permissionExplainer` 风险等级
- [Cline](https://github.com/cline/cline)：本地 shell + 逐条审批 + checkpoint
- [Aider](https://github.com/Aider-AI/aider)：本地 shell + git commit 兜底
- [OpenHands](https://github.com/All-Hands-AI/OpenHands)：Docker 沙箱（仅自主场景参考）
- 项目内现有 [ChatArea.tsx ToolStep](file:///e:/Desktop/agent/frontend/src/components/chat/ChatArea.tsx)：工具卡片复用基础
