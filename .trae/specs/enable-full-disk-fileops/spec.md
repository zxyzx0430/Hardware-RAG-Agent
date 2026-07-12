# Enable Full-Disk File Operations & Terminal Execution Spec

## Why

当前 Agent 的文件工具被 `path_guard.py` 限制在项目目录 + sandbox 临时目录，用户让 Agent 读写桌面文件、打开微信等操作时被 DENY 或弹窗按钮无响应（已修复弹窗）。用户需要 Agent 像 Claude Code / codex 一样自由操作本地电脑——读写整个磁盘的文件、执行 PowerShell/cmd 命令。

## What Changes

- **BREAKING** `path_guard.py`：`ALLOWED_DIRS` 从 `(PROJECT_ROOT, SANDBOX_TEMP_DIR)` 改为整个磁盘（移除 allowed-dir 检查，仅保留 DENY_PATTERNS 黑名单）
- **BREAKING** `read_file.py` / `write_file.py` / `edit_file.py`：description 去掉"路径必须在项目目录内"的误导说明，改为"可读写任意本地路径"
- `permission_classifier.py`：`_decide_medium` 中 write_file/edit_file 不再因 path_guard 拒绝而 DENY，改为统一走 acceptEdits/default 模式
- `run_command.py`：description 去掉误导说明，明确告知 LLM 可以执行 PowerShell/cmd 命令（打开应用、读写文件等）
- `prompts.py`：系统提示词新增"本地文件操作"指引，告知 LLM 可以读写任意路径 + 执行终端命令
- 新增 `DENY_PATTERNS` 扩展：保留 `.git/.vscode/.idea/.claude/settings.json/.env/*.key/*.pem/*credentials*`，新增系统敏感目录（`Windows`、`System32`、`$Recycle.Bin`）

## 与现有权限审查机制的联动

本改动不新建权限系统，而是复用已有的 5 层权限链路：

```
工具调用 → risk_level 判定 → permission_classifier → permission_matcher → HITL ConfirmDialog
```

### 各工具的权限流转（改后）

| 工具 | risk_level | classifier 路径 | matcher 规则 | 用户看到 |
|------|-----------|----------------|-------------|---------|
| read_file | LOW | `_decide_low` → auto allow | 不经过 | 直接执行，无弹窗 |
| write_file | MEDIUM | `_decide_medium` → acceptEdits=allow / default=ask | 不经过 | default 模式弹窗确认 |
| edit_file | MEDIUM | `_decide_medium` → acceptEdits=allow / default=ask | 不经过 | default 模式弹窗确认 |
| run_command | HIGH | `_decide_high` → `_grade_command` → risk_classifier | ls/pwd/cat→allow, rm→ask, format→deny | 安全命令自动放行，危险命令弹窗 |

### 关键联动点

1. **path_guard 与 permission_classifier 解耦**：
   - 之前：`permission_classifier._decide_medium` 调用 `path_guard.validate_path`，路径不在 ALLOWED_DIRS → DENY
   - 改后：`permission_classifier._decide_medium` 不再调用 path_guard，直接按 permission_mode 路由
   - path_guard 仅在工具 execute 内部做 DENY_PATTERNS 拦截（防御性二次检查）

2. **permission_matcher 继续生效**：
   - run_command 工具仍经过 `hitl_handler._check_permission_matcher`，ls/pwd 自动 allow，rm/format 自动 deny
   - 文件工具不经过 permission_matcher（matcher 只匹配命令模式）

3. **HITL ConfirmDialog 继续生效**：
   - write_file/edit_file 在 default 模式下仍弹窗确认（MEDIUM risk → ask）
   - run_command 的危险命令仍弹窗确认（HIGH risk → ask）
   - 用户可切 acceptEdits 模式跳过 MEDIUM 弹窗，切 bypassPermissions 跳过所有弹窗

4. **DENY_PATTERNS 保留**：
   - `.git/.vscode/.idea/.claude/settings.json/.env/*.key/*.pem/*credentials*` 仍被拦截
   - 新增 `Windows/System32/$Recycle.Bin` 系统敏感目录

## Impact

- Affected specs: industrial-tool-runtime §2 (PermissionClassifier), §7.2 (path_guard)
- Affected code:
  - `backend/src/agent/path_guard.py` — 核心改动（移除 allowed-dir，保留 deny-pattern）
  - `backend/src/agent/core/toolkit/permission_classifier.py` — 移除 path_guard 调用
  - `backend/src/agent/tools/groups/file_ops/read_file.py` — description 更新 + execute 内加 DENY 检查
  - `backend/src/agent/tools/groups/file_ops/write_file.py` — description 更新 + execute 内加 DENY 检查
  - `backend/src/agent/tools/groups/file_ops/edit_file.py` — description 更新 + execute 内加 DENY 检查
  - `backend/src/agent/tools/groups/execution/run_command.py` — description 更新
  - `backend/src/agent/prompts.py` — 系统提示词新增本地操作指引

## ADDED Requirements

### Requirement: Full-Disk File Access

The system SHALL allow Agent file tools (read_file/write_file/edit_file) to operate on any path on the local filesystem, restricted only by a deny-pattern blacklist.

#### Scenario: Read desktop file
- **WHEN** user asks Agent to read `C:\Users\奶茶丸\Desktop\notes.txt`
- **THEN** read_file tool successfully reads the file content（LOW risk → 自动放行）

#### Scenario: Write to desktop
- **WHEN** user asks Agent to write a file to `C:\Users\奶茶丸\Desktop\output.c`
- **AND** permission_mode is "default"
- **THEN** HITL ConfirmDialog 弹窗，用户确认后 write_file 执行

#### Scenario: Write to desktop in acceptEdits mode
- **WHEN** user asks Agent to write a file to `C:\Users\奶茶丸\Desktop\output.c`
- **AND** permission_mode is "acceptEdits"
- **THEN** write_file 自动放行，无弹窗

#### Scenario: Deny sensitive path
- **WHEN** Agent attempts to read `.env` or `*.key` file
- **THEN** path_guard denies the operation with reason "path matches deny pattern"

### Requirement: Terminal Command Execution

The system SHALL allow Agent to execute PowerShell/cmd commands via run_command, including opening applications and managing files.

#### Scenario: Open application
- **WHEN** user asks Agent to open WeChat
- **THEN** run_command executes `start weixin.exe` or equivalent PowerShell command
- **AND** permission_matcher 判定为 ask（非白名单命令）→ HITL 弹窗确认

#### Scenario: Safe command auto-allow
- **WHEN** Agent runs `ls`/`pwd`/`cat`/`grep` commands
- **THEN** permission_classifier auto-allows (LOW risk)

#### Scenario: Dangerous command deny
- **WHEN** Agent runs `format`/`shutdown`/`regedit` commands
- **THEN** permission_matcher 判定为 deny，直接拒绝

## MODIFIED Requirements

### Requirement: Path Guard

Previous: ALLOWED_DIRS restricts file operations to project root + sandbox temp dir.
Current: DENY_PATTERNS blacklist is the only restriction. All other paths are allowed. path_guard.validate_path 仍被工具 execute 内部调用做防御性 DENY 检查。

### Requirement: Permission Classifier Medium Branch

Previous: `_decide_medium` calls `path_guard.validate_path` and denies on violation.
Current: `_decide_medium` skips path_guard check. Route purely by permission_mode: acceptEdits → allow, default → ask.

## REMOVED Requirements

### Requirement: Allowed Directories Restriction

**Reason**: User requested full-disk access like Claude Code / codex.
**Migration**: path_guard's `is_in_allowed_dir` check removed from `_check_access`. Only `matches_deny_pattern` remains. `ALLOWED_DIRS` 常量保留（供 sandbox 工具引用）但不再用于文件工具拦截。

## 验证方式

实施完成后，开 2 个 subagent 并行检查：

1. **Subagent A — 完成度检查**：逐条核对 checklist.md，确认每个改动点都已实现，运行后端无导入错误，工具 description 正确。
2. **Subagent B — 缺陷检查**：尝试各种边界情况（读 .env 是否被拦截、写 System32 是否被拦截、读桌面文件是否成功、run_command 打开应用是否成功），报告发现的缺陷。
