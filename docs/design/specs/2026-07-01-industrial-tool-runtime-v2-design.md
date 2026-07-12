# Industrial Tool Runtime v2 — 4 项增强设计

> Date: 2026-07-01
> Status: Design (revised after 2-subagent review)
> Owner: Trae T2
> Predecessor: industrial-tool-runtime v1 (已实施，见 docs/completed.md)

## Why

industrial-tool-runtime v1 实施后，4 个中等问题/完成度缺口留作已知限制。本 spec 定义这 4 项的修复方案，每项都参考了 GitHub 上成熟产品级 AI Agent 的做法（Claude Code / OpenAI Codex / OpenCode / OpenClaw / Hermes / LangChain 官方）。

本版本是经过两个 subagent 审查（完成度 + 缺陷）后修正的版本，修复了 8 个严重问题 + 9 个中等问题，所有架构方向决策均已由用户确认。

## What Changes

4 项独立增强，可分别实施，建议按顺序：

| # | 增强 | 难度 | 风险 | 依赖 | 关键决策 |
|---|------|------|------|------|---------|
| 1 | output_schema 声明 + 软校验 + 长输出截断 | 低 | 低 | 无 | 软校验降级 |
| 2 | risk_classifier 命令风险评估（复用现有模块） | 中 | 低 | 无 | 正则+AI复核，不加 shellfirm |
| 3 | LoopGuard 生命周期重构（收敛到流式层） | 中 | 中 | 无 | 不进 State，per-session 内存 |
| 4 | MCP 工具迁移到新 ToolRouter（适配器 + 默认 HIGH） | 中 | 中 | 建议1 | bypass 全放行（含 MCP） |

---

## 1. output_schema 声明 + 软校验 + 长输出截断

### 1.1 Why

v1 的 ToolRouter 没有 output_schema 校验步骤，13 个工具都没声明 output_schema。工具返回格式不统一，前端解析复杂，长输出可能 token 爆炸。

### 1.2 GitHub 研究结论

| 项目 | 输出 schema | 校验方式 | 长输出处理 |
|------|-----------|---------|-----------|
| LangChain | 无 output_schema，靠 content/artifact 分流 | 软 coerce | artifact 旁路 |
| OpenHands | 事件级 Pydantic | 事件构造时校验 | message 字段进 LLM |
| LlamaIndex | 无 | 不校验 | LoadAndSearchToolSpec 旁路 |
| MCP/Claude Code | 无 | 不校验 | 全进 LLM |
| OpenCode | 无 | 不校验 | output + attachments + compacted 三字段 |

**业界共识**：输入严（args_schema 校验），输出松（软校验不硬拦），大数据必须旁路。

### 1.3 Design

#### 校验策略：软校验降级

- 工具返回结果不符合 output_schema 时，记 warning 日志（工具名+哪里不对），**照常返回，不拦不断**
- 绝不抛异常中断 Agent（5 个产品铁律）
- output_schema 当"说明书/契约"用，不当"警察"用

#### 长输出处理：截断 + 旁路 + 标记（抄 OpenCode）

- 给 AI 看的 `output` 字段超过 `OUTPUT_TRUNCATE_THRESHOLD`（8000 字符）就截断
- 截断后：`output` = 前 8000 字符 + "...(已截断，完整数据见 data)"
- 标记：`compacted=true` 写进 envelope.metadata，前端知道这数据被截过
- 旁路：完整数据保留在 `data` 字段，前端照样能看到全部

#### 13 个工具各自声明 output_schema

每个工具加一个 Pydantic model 描述 `data` 字段结构，举例：
- search_docs: `{results: [{title, score, content, source_file}], total, kb_coverage_hint}`
- audit_pins: `{pins: [{name, function, voltage}], warnings: [...]}`
- run_command: `{exit_code, stdout, stderr, duration_ms}`
- read_file: `{path, content, lines}`

#### ToolRouter 流水线步骤（修正后）

v1 的 ToolRouter 8 步流水线中，第 4 步 `_apply_loop_check` 在建议3 中移除（循环检测收敛到流式层）。output_schema 校验作为新步骤插入：

1. args_schema 校验（输入严）
2. 读 ctx.permission_mode（决策已在上游 PermissionClassifier 完成）
3. timeout 包裹
4. ~~_apply_loop_check~~（建议3 移除，收敛到 sse_helpers）
5. **execute 执行**
6. **output_schema 软校验**（新增，不硬拦）
7. **长输出截断**（新增，>8000 字符截断+旁路+标记）
8. 重试（v1 已有）
9. 审计记录（v1 已有）

### 1.4 Files Changed

| File | Change |
|------|--------|
| `core/toolkit/tool_spec.py` | 加 `output_schema: type[BaseModel] | None = None` 类属性 |
| `core/toolkit/tool_router.py` | 移除第4步 loop_check（建议3）；execute 后加软校验+截断步骤 |
| `core/toolkit/tool_result_envelope.py` | ResultMetadata 加 `compacted: bool = False` 字段 |
| 13 个工具文件 | 各加一个 output_schema Pydantic model（描述 data 结构） |

### 1.5 Constants

```python
OUTPUT_TRUNCATE_THRESHOLD: int = 8000  # 给 LLM 看的 output 最大字符数
```

---

## 2. risk_classifier 命令风险评估（复用现有模块）

### 2.1 Why

v1 的新 `PermissionClassifier` 只看工具的**静态** risk_level，没集成 `risk_classifier`。所以 `run_command` 跑 `ls`（安全）和 `rm -rf`（危险）都被同等弹窗——HIGH 一刀切。

同时 v1 已存在 3 个 legacy 模块（`risk_classifier.py` / `path_guard.py` / `permission_gate.py`），其中 `path_guard.py` 已被新 PermissionClassifier 在 MEDIUM 分支使用，但 `risk_classifier.py` 完全没被集成，`permission_gate.py` 是被取代的旧版。

### 2.2 GitHub 研究结论

| 项目 | 评估方式 | 白名单 | 黑名单 | AI 复核 |
|------|---------|--------|--------|---------|
| Claude Code | 前缀匹配 + AI 分类器 | ✅ bash(prefix*) | ❌ | ✅ auto 模式 |
| Codex | 白名单函数 + 沙盒 | ✅ is_known_safe_command | ❌ | 部分 on-request |
| OpenCode | Tree-sitter AST + 路径边界 | ❌ | ✅ 20+ AST 规则 | ❌ |
| Hermes | 黑名单 regex + LLM 复核 | ❌ | ✅ DANGEROUS_PATTERNS | ✅ smart 模式 |
| OpenClaw | allowlist/safe bins | ✅ | ❌ | ❌ |

**业界共识**：白名单放行明显安全的 + 黑名单拦明显危险的 + 灰区交给 AI/人。

### 2.3 Design

#### 复用现有 3 个 permission_mode（不新增模式）

- `bypassPermissions` = 全放行（最宽松，含 MCP——用户决策接受风险）
- `default` = HIGH 弹窗（严格）—— risk_classifier 在这模式下生效
- `acceptEdits` = 编辑类自动放行（中等）

#### 复用现有模块，不新建

| 现有模块 | 处置 | 改动 |
|---------|------|------|
| `risk_classifier.py` | **增强** | 黑名单从关键词包含改正则；加可配置追加 |
| `path_guard.py` | **保留** | 已完善（ALLOWED_DIRS + DENY_PATTERNS + 遍历检查），不改 |
| `permission_gate.py` | **删除** | v1 旧版，已被 PermissionClassifier 完全取代 |
| `core/toolkit/permission_classifier.py` | **集成** | HIGH 分支调用 risk_classifier 按 args 细分 |

#### PermissionClassifier 集成 risk_classifier（核心改动）

当前 `PermissionClassifier.check` 的 HIGH 分支直接 `return ASK`。改为：

```python
def check(self, spec, args, ctx):
    mode = ctx.permission_mode
    if mode == BYPASS_MODE:
        return ALLOW  # bypass 全放行（含 MCP，用户决策）
    if spec.risk_level == RiskLevel.LOW:
        return ALLOW
    if spec.risk_level == RiskLevel.HIGH:
        return self._decide_high(spec, args, ctx)  # 新：按 args 细分
    return self._decide_medium(spec, args, ctx)  # MEDIUM：path_guard 已在用

def _decide_high(self, spec, args, ctx):
    # run_command 按命令参数细分
    if spec.name == "run_command":
        from src.agent.risk_classifier import classify_risk, LOW
        risk = classify_risk("run_command", args)
        if risk == LOW:  # 白名单命中（ls/cat/grep 等）
            return ALLOW
    # 黑名单命中或灰区 → ask（AI 复核兜底：模型看到可疑命令会要求确认）
    return ASK
```

#### risk_classifier 增强（黑名单正则 + 可配置追加）

现有 `risk_classifier.py` 用关键词包含匹配（`kw in cmd_lower`）。增强为正则匹配，更灵活：

```python
# 现有（关键词包含）：
HIGH_RISK_KEYWORDS: tuple = ("rm -rf", "rm -r", ...)

# 增强后（正则）：
import re
HIGH_RISK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\brm\s+(-[a-z]*r[a-z]*\s+)+"),  # rm -r, rm -rf, rm -r -f
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"curl.*\|\s*(sh|bash)"),
    re.compile(r">\s*/etc/|>\s*/sys/"),
    re.compile(r"\bkillall\b|\bkill\s+-9\b"),
    re.compile(r":\(\)\s*\{.*\|.*&"),  # fork bomb
)
```

可配置追加：读 `settings.risk_extra_patterns`（逗号分隔），编译成正则加进 HIGH_RISK_PATTERNS。

#### 黑名单局限性（诚实声明）

正则黑名单**可被绕过**，这是已知限制，靠 AI 复核兜底：

| 绕过方式 | 例子 | 兜底 |
|---------|------|------|
| 拆参数 | `rm -r -f` 拆开 | 正则已覆盖（`\s+(-[a-z]*r[a-z]*\s+)+` 匹配多段） |
| 变量 | `$RM -rf /` | AI 复核：模型看到变量会警觉 |
| 等价命令 | `find / -delete` | AI 复核：模型识别等价危险操作 |
| 引号拼接 | `r"m" -rf /` | AI 复核：模型识别异常拼接 |

**不引入 shellfirm**（用户决策）：不加依赖，靠正则挡明显危险 + AI 模型复核兜底灰区。

#### 路径边界检查（复用现有 path_guard，用户决策范围）

**只对 file_ops 工具的 path 参数做**（run_command 不做路径检查）：

- `write_file` / `edit_file`（MEDIUM 分支）→ `PermissionClassifier._decide_medium` 已在调 `path_guard.validate_path`，保持
- `read_file`（LOW，自动放行）→ path_guard 不触发（LOW 直接 ALLOW）。**用户决策接受风险**：Agent 理论上可读 `.env`/`.key`/`.pem`。本地自部署 + 用户自用场景下可接受；未来若开放多用户需在 LOW 分支补 `path_guard.matches_deny_pattern` 检查
- `run_command`（HIGH）→ 不做路径检查，只走黑名单+白名单+AI 复核

理由（审查 A2）：shell 命令解析（管道/重定向/变量/引号）极其复杂，正则提路径不可靠。file_ops 的 path 是结构化参数，能准确解析。

### 2.4 Files Changed

| File | Change |
|------|--------|
| `core/toolkit/permission_classifier.py` | HIGH 分支加 `_decide_high` 调 risk_classifier |
| `risk_classifier.py` | 黑名单从关键词包含改正则；加 `classify_risk` 读 `settings.risk_extra_patterns` |
| `permission_gate.py` | **删除**（已被 PermissionClassifier 取代） |
| `tools/groups/file_ops/_permission.py` | **删除**（死代码，注释自称"不被任何工具调用"，仍 import permission_gate，删 permission_gate 前必须先删它） |
| `scripts/test_v2_scenarios.py` | 改用 `PermissionClassifier.check` 或标注废弃（当前 import permission_gate） |
| `src/config/settings.py` | 加 `risk_extra_patterns: str = ""` 配置项 |

### 2.5 Constants

```python
# risk_classifier.py
HIGH_RISK_PATTERNS: tuple[re.Pattern, ...] = (...)   # 硬编码正则
LOW_RISK_PREFIXES: tuple[str, ...] = (               # 白名单前缀
    "ls", "cat", "echo", "grep", "pwd", "wc",
    "git status", "git diff", "git log", "git show", "git branch",
    "dir", "type", "where",
)
```

---

## 3. LoopGuard 生命周期重构（收敛到流式层）

### 3.1 Why

v1 的 LoopGuard 是 per-request（每次请求新建，请求结束就扔）。用户点"继续"后新 LoopGuard 啥都不记得，cooldown 冷却机制形同虚设。两个 LoopGuard 实例（sse_adapter + ToolRouter）状态不共享。

### 3.2 GitHub 研究结论

| 项目 | 循环检测 | 生命周期 | 冷却 | 中断恢复记忆 |
|------|---------|---------|------|------------|
| Claude Code | ❌ 靠预算上限 | per-session | ❌ | 会话上下文 resume |
| Codex | ❌ 靠 Turn 终止 | per-session | ❌ | response_id 持久化 |
| OpenCode | ✅ DOOM_LOOP=3 | **per-session** | ❌ | 会话快照恢复 |
| OpenClaw | ✅ 三级告警 5/10/15 | **per-session** | ❌ | sessionState 跟会话 |
| Hermes | ❌ | per-process | ❌ | 三层记忆跨会话 |
| LangGraph | 框架不内置 | per-session（thread_id+Checkpointer） | 需自建 | ✅ Checkpointer 自动恢复 |

**业界共识**：per-session 是绝对主流；警告必须注入 prompt 让 AI 看到（OpenClaw 血泪教训）；冷却业界都没做好，是空白地带。

### 3.3 Design

#### 关键决策：收敛到流式层（不进 LangGraph State）

**审查发现问题**：原设计想把 LoopGuard 状态搬进 LangGraph State，但 `ToolRouter.dispatch(call_id, tool_name, args, ctx)` 只有 ToolContext，拿不到 LangGraph State 对象——架构走不通。

**用户决策**：收敛到流式层（sse_helpers），不进 State。

| 维度 | 原设计（进 State） | 修正后（收敛流式层） |
|------|------------------|-------------------|
| 状态位置 | LangGraph AgentState | per-session 内存 dict |
| ToolRouter 检测 | 第4步 _apply_loop_check | **移除**（不再做） |
| 流式层检测 | sse_helpers LoopGuard | **保留**（唯一检测点） |
| 跨请求保持 | Checkpointer 自动 | per-session 内存 dict（key=session_id） |
| 进程重启恢复 | ✅ 自动恢复 | ❌ 丢失（可接受，对话历史靠前端发完整消息列表） |
| 实现复杂度 | 高（动 State reducer） | 低（v1 代码基本复用） |

#### per-session LoopGuard 内存管理

新建 `LoopGuardRegistry` 单例（在 `loop_guard.py`），管理 per-session 的 LoopGuard 实例：

```python
class LoopGuardRegistry:
    """Per-session LoopGuard 管理器。进程内 dict，重启丢失。"""
    _instance: "LoopGuardRegistry | None" = None

    def __init__(self) -> None:
        self._guards: dict[str, LoopGuard] = {}  # key = session_id

    @classmethod
    def get_default(cls) -> "LoopGuardRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, session_id: str) -> LoopGuard:
        if session_id not in self._guards:
            self._guards[session_id] = LoopGuard()
        return self._guards[session_id]

    def clear(self, session_id: str) -> None:
        """用户点继续/换思路时清空该 session 的历史。"""
        guard = self._guards.get(session_id)
        if guard:
            guard.clear()
```

sse_helpers 调用方式（用 `state["session_id"]`，sse_helpers 用 state dict 不是 ctx 对象）：
```python
# 每次工具返回后
guard = LoopGuardRegistry.get_default().get(state["session_id"])
signal = guard.check(tool_call, result_output)
if signal:
    # 注入 SystemMessage + 黑名单 3 轮
    ...

# 用户点继续后（先 add_cooldown 上次循环的 tool+args，再 clear）
guard.add_cooldown(last_loop_tool, last_loop_args)  # 硬拦触发点
LoopGuardRegistry.get_default().clear(state["session_id"])
```

#### 检测规则（复用 v1 的 4 条，实现不变）

v1 的 `LoopGuard.check` 已实现 4 条规则，收敛到流式层后**逻辑完全复用**，只是生命周期从 per-request 改成 per-session：

- 重复调用：同工具+同参数，最近 3 次里 ≥2 次
- 无进展：最近 3 次返回结果 hash 相同
- 超最大次数：总调用 ≥15 次
- 冷却命中：工具+参数组合在黑名单里

**关键**：单纯调同工具 3 次不算循环，必须参数+结果都重复才算（用户确认）。

#### 双保险处理（注入提示 + 硬拦）

1. **注入提示**：插 SystemMessage "你刚才连续 N 次调用 X 工具用同样参数没进展，这可能是死循环，请换思路或基于已有信息回答"
2. **硬拦**：导致循环的工具+参数组合黑名单 3 轮，3 轮内 AI 再调直接拒绝

#### 冷却衰减（在 per-session LoopGuard 内存里）

- 黑名单项存 `{"tool":"search_docs", "args_hash":"xxx", "expires_at_turn":5}`
- 每轮工具调用后剩余轮数 -1
- 剩余轮数 = 0 时自动解除
- 3 轮后 AI 可重新调（LoopGuard 继续监控）

#### 持久化：保持 MemorySaver（用户决策，不换 SqliteSaver）

**审查发现问题**：原设计想换 SqliteSaver 持久化对话历史，但当前架构每轮请求开始都 `reset_thread_checkpoint`（清旧状态）+ 前端发完整历史，Agent 不靠 checkpoint 跨请求恢复。换 SqliteSaver 的"进程重启恢复对话"卖点不成立。

**用户决策**：不换 SqliteSaver，保持 MemorySaver。

- `reset_thread_checkpoint` **不需要重写**（继续操作 MemorySaver 的 `.storage` / `.writes`，v1 代码原样保留）
- LoopGuard 状态进程重启丢失（可接受：循环检测是辅助，丢了重新检测即可）
- 对话历史靠前端每轮发完整消息列表（v1 已如此）

#### 并发约束（审查 F1）

同一 session_id 的两个请求并发时，per-session LoopGuard 内存 dict 会写冲突。

**约束**：同一 session_id 的请求必须串行化（前端发起新请求前等上一个完成）。spec 不实现锁，靠前端保证。

### 3.4 Files Changed

| File | Change |
|------|--------|
| `core/toolkit/loop_guard.py` | 加 `LoopGuardRegistry` 单例；`LoopGuard` 类不变（v1 复用） |
| `core/toolkit/tool_router.py` | 移除第4步 `_apply_loop_check`（不再做循环检测） |
| `sse_helpers.py` / `sse_adapter.py` | LoopGuard 调用从 per-request 实例改为 `LoopGuardRegistry.get(state["session_id"])`；`init_stream_state` 里 `LoopGuard()` 改为从 registry 取 |
| `agent_factory.py` | **不改**（保持 MemorySaver，reset_thread_checkpoint 原样） |
| `chat_routes.py` | `_restart_after_loop` 调 `LoopGuardRegistry.clear(session_id)` 而非新建 |

### 3.5 Constants

```python
# loop_guard.py（v1 已有，保持不变）
REPEAT_WINDOW: int = 3
REPEAT_THRESHOLD: int = 2
NO_PROGRESS_WINDOW: int = 3
MAX_CALLS: int = 15
COOLDOWN_ROUNDS: int = 3           # 黑名单 3 轮后自动解除
RESULT_HISTORY_MAX: int = 5        # v1 现有值；OpenClaw 实测 30-50 最稳，实施时可调大
```

### 3.6 Pitfalls to Avoid

- ⚠️ historySize 别贪大（OpenClaw 实测 30→100 失效，36 才稳定）—— v1 当前是 5，调大需实测
- ⚠️ 警告必须注入 prompt，不能只记日志
- ⚠️ 冷却必须有出口（3 轮后自动解除，不能永久黑名单）
- ⚠️ 同一 session_id 请求必须串行化（否则 LoopGuard 内存 dict 写冲突）
- ⚠️ `add_cooldown` 在 v1 是死代码无调用点，实施时必须在 `_restart_after_loop` 的 `clear` 之前先 `add_cooldown` 上次循环的 tool+args，"硬拦"才有触发点
- ⚠️ `ToolRouter.__init__(loop_guard, audit_recorder)` 的 `loop_guard` 参数移除 loop_check 后变孤儿，实施时改为 `__init__(self, audit_recorder)`，`get_default` 同步
- ⚠️ `LoopGuardRegistry._guards` 是进程内 dict 无清理，单用户可接受；多用户场景需补 LRU 上限或 TTL 清理

---

## 4. MCP 工具迁移到新 ToolRouter

### 4.1 Why

v1 的 MCP 工具走旧 `tool_router_legacy.py`，没享受新体系的超时/审计/权限门控。两套系统并存，乱。

> 注：循环检测在建议3 收敛到流式层后，MCP 工具自动享受（流式层对所有工具统一检测）。

### 4.2 GitHub 研究结论

| 项目 | 统一管理 | 包装方式 | MCP 享受保护 | 动态增删 |
|------|---------|---------|------------|---------|
| Claude Code | ✅ | 适配器+字段扩展 | 权限✅ 审计部分 | 启动发现+断开报错 |
| Codex | 🟡 半统一 | 运行时保留 MCP 边界 | 暴露控制✅ | config 配置 |
| OpenCode | ✅ | local/remote 配置 | ✅ allow/deny/ask | 懒加载+5min超时 |
| OpenClaw | ✅ | Gateway+Plugin | ✅ 最完善 7 层过滤 | plugin availability |
| Hermes | ✅ | Registry | ✅ 危险命令审批 | ✅ 监听 tools/list_changed |
| langchain-mcp-adapters | ✅ | load_mcp_tools | ❌ 只转换不保护 | session 生命周期 |

**业界共识**：统一管理是绝对主流；MCP 工具应该比内置工具更严（外部不可信代码）；MCP 协议无 risk 字段，框架自己定。

### 4.3 Design

#### 适配器模式（抄 Claude Code）

新建 `MCPToolAdapter`，把 MCP 工具转成 ToolSpec：
- **name**：`mcp__{server_name}__{tool_name}` 格式
- **description**：用 MCP server 返回的描述
- **args_schema**：用 MCP server 返回的 JSON Schema 转 Pydantic
- **risk_level**：默认 HIGH
- **mcp_info**：新增可选字段 `{server_name, tool_name}` 标记来源

ToolSpec 加可选字段 `mcp_info`，内置工具不填（None），MCP 工具填来源信息。

#### 动态注册/注销（抄 Claude Code）

**审查发现问题**：原 spec 说"不做懒加载，启动时连"，但 MCP server 是用户在前端**动态添加**的，不是启动时就有。

**修正**：用户在前端添加 server 时，后端 `MCPServerManager.start(server_id)` 立即连接并保持。

| 事件 | 行为 |
|------|------|
| 用户添加 MCP server | `MCPServerManager.start` → `tools/list` 发现工具 → 转成 ToolSpec → 注册到新 ToolRouter |
| server 断开 | 从 ToolRouter 注销该 server 的所有工具 |
| 调用已断开 server 的工具 | 返回明确错误 `"MCP server disconnected"`，不崩 Agent |
| server 运行中新增工具 | 不主动更新（重启 server 后重新发现） |

保持连接，不断开（单用户不需要省资源）。

#### 权限门控（默认 HIGH + bypass 全放行）

**审查发现问题**：原 spec 说"bypass 模式下照样放行"和"bypass 直接 return ALLOW"矛盾——bypass 模式下 MCP 的 HIGH 形同虚设，恶意 MCP 可读 .env 窃取 API Key。

**用户决策**：bypass 全放行（含 MCP）。用户选 bypass 就是要快，接受风险。

| 模式 | MCP 工具行为 |
|------|------------|
| `bypassPermissions` | **全放行**（用户决策，接受风险：恶意 MCP 可窃取 .env） |
| `default` | 每次调用都弹窗确认（HIGH） |
| `acceptEdits` | 每次调用都弹窗确认（HIGH，acceptEdits 不降级 HIGH） |

用户可在配置里把信任的 server 标记为 `allow`（免确认，排期 v3+）。

> acceptEdits 模式下 MCP 仍弹窗是有意为之：MCP 是外部不可信代码，比内置工具危险，不应因用户选了 acceptEdits（针对本地文件编辑）就放行外部工具。信任 server 白名单（v3+）落地后可缓解。

#### 享受的保护

| 保护 | MCP 工具 |
|------|---------|
| 超时 | ✅ |
| 循环检测 | ✅（流式层统一检测） |
| 审计 | ✅ |
| 权限门控 | ✅（默认 HIGH，bypass 放行） |
| output_schema 校验 | ⚠️ 可选（MCP 协议没有，不强求） |
| 命令风险评估 | ⚠️ 不走 risk_classifier（只有 run_command 走） |

### 4.4 Files Changed

| File | Change |
|------|--------|
| 新增 `core/toolkit/mcp_tool_adapter.py` | MCP 工具 → ToolSpec 适配器（JSON Schema → Pydantic 用 `pydantic.create_model` 动态生成；无 schema 时 fallback 为 `dict` 入参；复杂特性 oneOf/anyOf 展开为 Optional 联合） |
| `core/toolkit/tool_spec.py` | 加 `mcp_info: dict | None = None` 可选字段 |
| `mcp/manager.py` | `register_mcp_tools` 改为注册到新 ToolRouter（当前注册到 `tool_router_legacy.py`） |
| `app/api/tool_routes.py` | `/api/tool` 的 `dispatch` / `_REGISTRY` 从 legacy 迁到新 `ToolRouter.dispatch`（需适配 ctx 参数，构造 ToolContext） |
| `tool_router_legacy.py` | `/api/tool` 迁移完成后**整个删除**（不再保留两套系统） |

### 4.5 Not Doing（避免过度工程）

- ❌ 不做 7 层过滤链路（OpenClaw，我们只监听 127.0.0.1）
- ❌ 不做运行时单独建模（Codex，和现有体系重复）
- ❌ 不做懒加载（OpenCode，单用户不需要省资源）
- ❌ 不给 MCP 协议加 risk 字段（协议没有，框架自己定 HIGH）

---

## Impact

### Positive

- output_schema：Agent 不被乱返回带偏，前端解析更稳定，长输出不 token 爆炸
- risk_classifier：少打扰用户（安全命令不弹窗），危险操作仍挡住；复用现有模块不重写
- LoopGuard 重构：真正防重复掉坑，per-session 跨请求保持；改动小（v1 代码复用）
- MCP 迁移：外挂工具也有全套护栏，统一管理

### Negative / Risk

- LoopGuard 状态进程重启丢失（可接受，对话历史靠前端每轮发完整消息列表恢复）
- read_file 是 LOW 不查 path_guard，Agent 可读 `.env`/`.key`/`.pem`（用户决策接受风险，本地自部署场景）
- MCP bypass 全放行有安全风险（用户决策接受：恶意 MCP 可窃取 .env）
- 黑名单正则可被绕过（靠 AI 复核兜底，用户决策不加 shellfirm）
- 同一 session_id 并发请求需串行化（靠前端保证）
- acceptEdits 模式下 MCP 工具仍弹窗（有意为之，信任 server 白名单 v3+ 落地后缓解）

## Research Sources

- LangChain core 源码（tools/base.py, messages/tool.py）
- LlamaIndex 官方文档
- Claude Code query.ts / StreamingToolExecutor 源码分析
- OpenAI Codex codex-rs AGENTS.md + protocol_v1.md
- OpenCode sst/opencode ToolState 类型定义
- OpenClaw 三级告警 + sessionState 分析
- Hermes DANGEROUS_PATTERNS + classify_api_error + ContextCompressor
- langchain-mcp-adapters 官方文档

## Review History

### 第 1 轮审查（2026-07-01）
两个 subagent 审查发现 8 严重 + 9 中等问题，修正要点：
  - A1: LoopGuard 不进 State，收敛到流式层（per-session 内存 dict）
  - A2: 路径检查只对 file_ops 的 path 参数，run_command 不做
  - B1: ~~reset_thread_checkpoint 重写适配 SqliteSaver~~ → 第 2 轮改为不换 SqliteSaver
  - C1: 黑名单正则+AI 复核兜底，不加 shellfirm
  - C3: bypass 全放行（含 MCP，用户接受风险）
  - E1: 不进 State 后检测规则实现不变，矛盾消除
  - E2: "启动时连"改为"用户添加 server 时立即连接"
  - F1: 加同一 session_id 串行化约束
  - 复用现有 risk_classifier.py / path_guard.py，删除 permission_gate.py

### 第 2 轮审查（2026-07-01）
两个 subagent 复审发现 3 新严重 + 6 中等，修正要点：
  - S1: 删 permission_gate.py 前先删死代码 `_permission.py` + 改 `test_v2_scenarios.py`（避免 ImportError）
  - S2: `/api/tool` 路由全迁到新 ToolRouter.dispatch（用户决策），legacy 整个删
  - S3: read_file 读 .env 风险 → 用户决策接受风险（本地自部署场景）
  - M1: `add_cooldown` 死代码 → 明确在 `clear` 之前先调，作为硬拦触发点
  - M2: `ToolRouter.__init__` 移除 loop_guard 参数 → 补 Pitfall
  - M3: SqliteSaver 收益存疑 → 用户决策不换，保持 MemorySaver
  - M4: sse_helpers 代码示例 ctx → 改为 `state["session_id"]`
  - M5: LoopGuardRegistry 无清理 → 补 Pitfall（单用户可接受，多用户需 LRU）
  - M6: acceptEdits 下 MCP 弹窗 → 补说明有意为之，信任白名单 v3+
