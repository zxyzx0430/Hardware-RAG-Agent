"""
Hardware RAG Agent — RunCommandTool (execution group).

Executes shell commands with timeout.
Migrated from tools/run_command.py (Task 1, SubTask 1.11).

Spec §5 (tools), §6.2 (truncation).
PLUR constraint: default timeout 30s, max 5min; stdout/stderr truncated.

industrial-tool-runtime Task 2: refactored to ToolSpec. Permission gating
removed (PermissionClassifier handles it pre-ToolNode); audit logging
removed (ToolRouter records via AuditRecorder). Business-level try/except
inside _do_run (TimeoutError/OSError -> exit_code/timed_out dict) is
preserved per spec §ToolRouter business-level error recovery.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.agent.path_guard import validate_path
from src.agent.tools.groups.file_ops._git_lock import get_git_lock, get_git_sync_lock
from src.config.settings import ROOT_DIR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Named constants
# ═══════════════════════════════════════════

DEFAULT_TIMEOUT_MS: int = 30000
MAX_TIMEOUT_MS: int = 300000  # 5 minutes (PLUR constraint)
MAX_OUTPUT_CHARS: int = 5000
TRUNCATE_SUFFIX: str = "...[truncated]"
LOG_CMD_PREVIEW_CHARS: int = 100
SHELL_EXECUTABLE: str = "powershell.exe" if sys.platform == "win32" else "/bin/bash"
SHELL_PREFIX_ARGS: tuple[str, ...] = ("-NoProfile", "-Command") if sys.platform == "win32" else ("-c",)
_IS_WINDOWS: bool = sys.platform == "win32"

# Patterns that indicate the command writes to a file (bypassing write_file).
# When detected, a git snapshot is taken after execution so undo_edit can
# roll back the change — same safety net as write_file/edit_file.
#
# Every alternative is anchored at command start (^) or right after a command
# separator (; & |) via (?:^|[;&|]\s*). This prevents false matches on '>'
# characters that appear inside string literals or echo arguments
# (e.g. echo "a > b", echo a > b) while still catching real write operators.
_FILE_WRITE_RE: re.Pattern[str] = re.compile(
    r"""(?:
        (?:^|[;&|]\s*)\d*>+\s*\S+
        | (?:^|[;&|]\s*)tee\s+
        | (?:^|[;&|]\s*)Set-Content\s+
        | (?:^|[;&|]\s*)Add-Content\s+
        | (?:^|[;&|]\s*)Out-File\s+
        | (?:^|[;&|]\s*)Clear-Content\s+
        | (?:^|[;&|]\s*)Copy-Item\s+
        | (?:^|[;&|]\s*)Move-Item\s+
        | (?:^|[;&|]\s*)Rename-Item\s+
        | (?:^|[;&|]\s*)Remove-Item\s+
        | (?:^|[;&|]\s*)Export-Csv\s+
        | (?:^|[;&|]\s*)Export-Clixml\s+
        | (?:^|[;&|]\s*)Invoke-WebRequest\s+.*?-OutFile\s+
        | (?:^|[;&|]\s*)New-Item\s+
        | (?:^|[;&|]\s*)echo\s+.*?>+\s*\S+
        | (?:^|[;&|]\s*)cat\s+.*?>+\s*\S+
        | (?:^|[;&|]\s*)printf\s+.*?>+\s*\S+
        | \[(?:System\.)?IO\.File\]::(?:WriteAll|AppendAll)
        | (?:^|[;&|]\s*)(?:del|erase|rd|rmdir)\s+
        | curl\s+.*?(?:-o|--output)\s+\S
        | wget\s+.*?-O\s+\S
        | git\s+checkout\s+.*?--
        | git\s+restore\s+
        | git\s+stash\s+(?:pop|apply)
        | python\s+(?:-c|--command)\s+.*?open\s*\([^)]*['\"][wa]
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# ── Write-target extraction (precise `git add -- <files>` instead of `git add -A`) ──
# Each regex captures the file path a command writes to, so _git_snapshot_files
# stages exactly those files. Best-effort: if extraction yields nothing, the
# snapshot is skipped (safer than staging the whole repo and sweeping in the
# user's own uncommitted changes).
_REDIRECT_FILE_RE: re.Pattern[str] = re.compile(
    r"(?:^|[;&|]\s*)\d*>+\s*(\S+)", re.IGNORECASE | re.MULTILINE,
)
_TEE_FILE_RE: re.Pattern[str] = re.compile(
    r"(?:^|[;&|]\s*)tee\s+(\S+)", re.IGNORECASE | re.MULTILINE,
)
_DOTNET_FILE_RE: re.Pattern[str] = re.compile(
    r"\[(?:System\.)?IO\.File\]::(?:WriteAll|AppendAll)\w*\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_CURL_OUT_RE: re.Pattern[str] = re.compile(r"curl\s+.*?(?:-o|--output)\s+(\S+)", re.IGNORECASE)
_WGET_OUT_RE: re.Pattern[str] = re.compile(r"wget\s+.*?-O\s+(\S+)", re.IGNORECASE)
_GIT_CHECKOUT_FILE_RE: re.Pattern[str] = re.compile(r"git\s+checkout\s+--\s+(\S+)", re.IGNORECASE)
_GIT_RESTORE_FILE_RE: re.Pattern[str] = re.compile(r"git\s+restore\s+(\S+)", re.IGNORECASE)
_WRITE_CMDLETS: tuple[str, ...] = (
    "Set-Content", "Add-Content", "Clear-Content", "Out-File",
    "New-Item", "Export-Csv", "Export-Clixml",
)

_AGENT_AUTHOR: str = "hardware-rag-agent <agent@local>"
_GIT_SNAPSHOT_TIMEOUT: int = 10

# Long-running command patterns that need extended timeout (smart fallback).
# If LLM forgets to set timeout_ms and command matches these, auto-extend.
_LONG_CMD_PATTERNS: tuple[str, ...] = (
    "platformio", "pio run", "pio project", "pio pkg install", "pio platform",
    "pip install", "pip download", "python -m pip",
    "npm install", "npm ci", "yarn install", "pnpm install",
    "git clone", "git pull", "git fetch --all",
    "esptool", "avrdude", "openocd",
    "docker build", "docker pull",
)
# Recommended timeout (ms) when a long-running command pattern is detected.
_LONG_CMD_TIMEOUT_MS: int = 300000  # 5 minutes

# ToolRouter-level timeout (seconds). MUST exceed MAX_TIMEOUT_MS/1000 so the
# subprocess timeout (timeout_ms) fires first and returns a proper timeout_result
# dict — otherwise ToolRouter's asyncio.wait_for raises TimeoutError at 120s
# (the old value) and the LLM gets an opaque error instead of "命令超时".
# Buffer of 10s covers process creation + output truncation overhead.
_TOOL_TIMEOUT_SECONDS: int = MAX_TIMEOUT_MS // 1000 + 10  # 310s

_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r'(--(?:token|key|password|secret|api[_-]?key)=)\S+', r'\1***REDACTED***'),
    (r'(Authorization:\s*Bearer\s+)\S+', r'\1***REDACTED***'),
]


def _redact_command(cmd: str) -> str:
    """Redact sensitive values (tokens/keys/passwords) from command before logging."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        cmd = re.sub(pattern, replacement, cmd, flags=re.IGNORECASE)
    return cmd


def _smart_timeout(command: str, requested_timeout_ms: int) -> int:
    """Auto-extend timeout for known long-running commands.

    LLM often forgets to set timeout_ms for commands like `platformio` or
    `pip install`, hitting the 30s default. If the command matches a known
    long-running pattern, bump to _LONG_CMD_TIMEOUT_MS — but never shrink
    an explicit larger value the LLM passed.
    """
    cmd_lower = command.lower()
    if any(pattern in cmd_lower for pattern in _LONG_CMD_PATTERNS):
        return max(requested_timeout_ms, _LONG_CMD_TIMEOUT_MS)
    return requested_timeout_ms


# ═══════════════════════════════════════════
# RunCommandTool
# ═══════════════════════════════════════════

class RunCommandArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令")
    timeout_ms: int = Field(
        default=DEFAULT_TIMEOUT_MS,
        ge=1000,
        description=(
            "超时毫秒数，最大 300000(5min)，最小 1000。按命令类型选择："
            "普通命令(ls/cat/grep)用默认 30000；"
            "编译/烧录/安装(platformio/pio/esptool/pip install/npm install/git clone)"
            "传 300000。系统会自动识别长命令并兜底延长，但仍建议你主动传大值"
        ),
    )
    cwd: str = Field(default="", description="工作目录（空=当前目录）")


class RunCommandOutput(BaseModel):
    """Describes envelope.data shape (output is lifted out by ToolRouter).
    stderr is absent on timeout/error paths — default keeps soft-check quiet."""
    stderr: str = Field("", description="标准错误输出")
    exit_code: int = Field(-1, description="进程退出码")
    duration: int = Field(0, description="执行耗时（毫秒）")
    timed_out: bool = Field(False, description="是否超时")


class RunCommandTool(ToolSpec):
    """Execute a shell command with timeout."""
    name: str = "run_command"
    description: str = (
        "执行 shell 命令（PowerShell）并返回 stdout/stderr/退出码。"
        "这是通用命令行工具，能干很多事情，优先使用。"
        "\n\n常用场景："
        "\n- PlatformIO: pio device monitor（看串口日志）、pio lib search（查库）、pio pkg list"
        "\n- 串口监视: pio device monitor -p COM3 -b 115200（烧录后看启动日志、诊断问题）"
        "\n- 包管理: pip install xxx / npm install xxx / pio pkg install -g xxx"
        "\n- Git: git status / git log / git diff"
        "\n- 系统信息: 查看串口列表 (mode Windows 下 / Get-SerialPort)、查磁盘空间、查进程"
        "\n- 网络工具: curl / wget / ping（测试外网连通性）"
        "\n- 时序计算: python -c 'print(1000/(16+2))'（算波特率分频等）"
        "\n- 查寄存器: grep -r 'TIM_CR1' data/ 或 Select-String -Pattern 'TIM_CR1' -Recurse"
        "\n- 跑脚本: python scripts/xxx.py"
        "\n\n与专用工具的区分："
        "\n- 读文件内容用 read_file（有 path_guard 安全+分段读取），但管道操作（如 cat | grep）用 run_command"
        "\n- 搜文件名用 glob（有 path_guard+排序），但复杂管道（如 Get-ChildItem | Where-Object）用 run_command"
        "\n- 搜文件内容用 grep（有 path_guard+时间预算），但复杂管道（如 Select-String | Sort-Object）用 run_command"
        "\n- 编译固件用 build_firmware（有 lib_deps 自动解析+临时项目管理），不要用 pio run"
        "\n- 烧录固件用 flash_firmware（有 binary_path 校验+端口检测），不要用 pio run -t upload"
        "\n\n高风险命令（rm/format/regedit/shutdown 等）需用户确认。"
        + f"\n当前 shell: {SHELL_EXECUTABLE}（{'Windows PowerShell' if _IS_WINDOWS else 'Bash'}）。"
        f"{'cmdlet 需用 powershell -Command 包裹' if _IS_WINDOWS else '支持管道、变量展开等 bash 语法'}。"
    )
    args_schema: type = RunCommandArgs
    output_schema: type[BaseModel] | None = RunCommandOutput

    risk_level: RiskLevel = RiskLevel.HIGH
    timeout_seconds: int = _TOOL_TIMEOUT_SECONDS  # 310s, exceeds MAX_TIMEOUT_MS
    max_retries: int = 0

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        """Execute command; business-level TimeoutError/OSError caught in _do_run."""
        command = args.get("command", "")
        requested_timeout_ms = args.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        cwd = args.get("cwd", "")
        if cwd:
            ok, reason = validate_path(cwd, is_write=False)
            if not ok:
                return {
                    "output": f"INVALID_ARGS: cwd 路径校验失败 ({reason})",
                    "stderr": "",
                    "exit_code": -1,
                    "duration": 0,
                    "timed_out": False,
                }
        # Smart fallback: if LLM forgot to set a big timeout for long commands
        # (platformio/pip install/...), auto-extend so we don't hit 30s default.
        timeout_ms = _smart_timeout(command, requested_timeout_ms)
        if timeout_ms != requested_timeout_ms:
            logger.info(
                f"run_command smart-timeout extended {requested_timeout_ms}ms "
                f"-> {timeout_ms}ms (long command detected)"
            )
        logger.info(f"run_command cmd={_redact_command(command)[:LOG_CMD_PREVIEW_CHARS]}... timeout={timeout_ms}ms")
        result = await _do_run(command, timeout_ms, cwd)
        logger.info(
            f"run_command done exit={result.get('exit_code')} "
            f"duration={result.get('duration')}ms timed_out={result.get('timed_out')}"
        )
        # If the command likely wrote files (>, >>, tee, Set-Content...),
        # take a git snapshot so undo_edit can roll it back.
        await _maybe_git_snapshot(command, result, cwd)
        return result


# ═══════════════════════════════════════════
# Execution helpers
# ═══════════════════════════════════════════

async def _do_run(command: str, timeout_ms: int, cwd: str) -> dict:
    """Execute command with timeout, return truncated output.

    Business-level recovery: TimeoutError/OSError return a dict with
    exit_code/timed_out fields (kept per spec §ToolRouter). ToolRouter
    wraps this dict as ToolResultEnvelope(success=true, data=...).
    """
    timeout = min(timeout_ms, MAX_TIMEOUT_MS) / 1000
    start = time.perf_counter()
    try:
        proc = await _create_process(command, cwd)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return _timeout_result(command, start)
    except OSError as exc:
        return _error_result(exc)
    return _success_result(stdout, stderr, proc.returncode, start)


async def _create_process(command: str, cwd: str) -> Any:
    """Create subprocess running command via PowerShell (not cmd.exe).

    Uses create_subprocess_exec with powershell.exe -NoProfile -Command so
    PowerShell cmdlets (Get-Date / Get-ChildItem / Remove-Item ...) work
    directly without manual cmd.exe wrappers.
    """
    return await asyncio.create_subprocess_exec(
        SHELL_EXECUTABLE, *SHELL_PREFIX_ARGS, command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or None,
    )


def _timeout_result(command: str, start: float) -> dict:
    """Build timeout result dict."""
    duration = int((time.perf_counter() - start) * 1000)
    return {"output": f"命令超时: {command}", "exit_code": -1,
            "duration": duration, "timed_out": True}


def _error_result(exc: Exception) -> dict:
    """Build error result dict."""
    return {"output": f"执行失败: {exc}", "exit_code": -1,
            "duration": 0, "timed_out": False}


def _success_result(stdout: bytes, stderr: bytes, exit_code: int, start: float) -> dict:
    """Build success result dict with truncated output."""
    duration = int((time.perf_counter() - start) * 1000)
    return {
        "output": _truncate(stdout.decode("utf-8", errors="replace")),
        "stderr": _truncate(stderr.decode("utf-8", errors="replace")),
        "exit_code": exit_code,
        "duration": duration,
        "timed_out": False,
    }


def _truncate(text: str) -> str:
    """Truncate text to MAX_OUTPUT_CHARS."""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + TRUNCATE_SUFFIX


async def _maybe_git_snapshot(command: str, result: dict, cwd: str) -> None:
    """If the command wrote files, git add + commit exactly those files.

    Detection uses _FILE_WRITE_RE (anchored to avoid false matches on '>'
    inside strings). The actual files are extracted with _extract_write_targets
    so we `git add -- <abs paths>` only what the command touched — never
    `git add -A`, which would silently sweep in the user's own uncommitted
    changes (a far more dangerous data-loss vector than missing a snapshot).
    """
    if not _should_snapshot(command, result):
        return
    targets = _extract_write_targets(command, cwd)
    if not targets:
        logger.debug("run_command git snapshot skipped: no write targets parsed")
        return
    await _take_snapshot(targets)


def _should_snapshot(command: str, result: dict) -> bool:
    """True only on successful commands that look like file writes."""
    return result.get("exit_code", -1) == 0 and bool(_FILE_WRITE_RE.search(command))


async def _take_snapshot(targets: list[str]) -> None:
    """Serialize via the global asyncio lock, then thread off the sync snapshot."""
    try:
        async with get_git_lock():
            await asyncio.to_thread(_git_snapshot_files, targets, "run_command")
        logger.info("run_command git snapshot taken (%d files)", len(targets))
    except Exception as exc:
        logger.debug("run_command git snapshot skipped: %s", exc)


# ── Write-target extraction ──

def _extract_write_targets(command: str, cwd: str) -> list[str]:
    """Parse the command for file paths it writes to. Best-effort + validated."""
    raw = _collect_raw_targets(command)
    return _resolve_and_validate(raw, cwd)


def _collect_raw_targets(command: str) -> list[str]:
    """Gather all raw path tokens from every supported write pattern."""
    raw: list[str] = []
    raw += _collect_regex_targets(command)
    raw += _extract_cmdlet_targets(command)
    raw += _extract_copy_move_dst(command)
    return raw


def _collect_regex_targets(command: str) -> list[str]:
    """Collect paths from the simple single-capture regexes."""
    raw: list[str] = []
    for pat in (_REDIRECT_FILE_RE, _TEE_FILE_RE, _DOTNET_FILE_RE,
                _CURL_OUT_RE, _WGET_OUT_RE, _GIT_CHECKOUT_FILE_RE, _GIT_RESTORE_FILE_RE):
        raw += pat.findall(command)
    return raw


def _extract_cmdlet_targets(command: str) -> list[str]:
    """Paths from Set-Content/Add-Content/Out-File/etc. (named -Path or positional)."""
    targets: list[str] = []
    for cmdlet in _WRITE_CMDLETS:
        targets += _named_path_after(command, cmdlet)
        targets += _positional_path_after(command, cmdlet)
    return targets


def _named_path_after(command: str, cmdlet: str) -> list[str]:
    """Capture value of -Path/-FilePath following the cmdlet."""
    pat = rf"{re.escape(cmdlet)}\b.*?(?:-Path|-FilePath)\s+(\S+)"
    return re.findall(pat, command, re.IGNORECASE)


def _positional_path_after(command: str, cmdlet: str) -> list[str]:
    """Capture first bare (non-option) token after the cmdlet."""
    pat = rf"{re.escape(cmdlet)}\s+(\S+)"
    matches = re.findall(pat, command, re.IGNORECASE)
    return [m for m in matches if not m.startswith("-")]


def _extract_copy_move_dst(command: str) -> list[str]:
    """Destination of Copy-Item/Move-Item (-Destination or 2nd positional)."""
    targets: list[str] = []
    targets += _named_destination(command)
    targets += _positional_destination(command)
    return targets


def _named_destination(command: str) -> list[str]:
    """Capture value of -Destination following Copy-Item/Move-Item."""
    return re.findall(r"(?:Copy-Item|Move-Item)\b.*?-Destination\s+(\S+)", command, re.IGNORECASE)


def _positional_destination(command: str) -> list[str]:
    """2nd bare token after Copy-Item/Move-Item is the destination."""
    targets: list[str] = []
    for m in re.finditer(r"(?:Copy-Item|Move-Item)\s+(\S+(?:\s+\S+)*)", command, re.IGNORECASE):
        tokens = [t for t in m.group(1).split() if not t.startswith("-")]
        if len(tokens) >= 2:
            targets.append(tokens[1])
    return targets


def _resolve_and_validate(raw: list[str], cwd: str) -> list[str]:
    """Resolve relative paths to absolute (vs cwd/ROOT_DIR) and deny-list check."""
    base = cwd or str(ROOT_DIR)
    resolved: list[str] = []
    for target in raw:
        abs_path = _resolve_target(target, base)
        if abs_path and _is_safe_path(abs_path):
            resolved.append(abs_path)
    return _dedupe(resolved)


def _resolve_target(target: str, base: str) -> str:
    """Resolve a raw token to an absolute path, stripping surrounding quotes."""
    clean = target.strip("'\"")
    p = Path(clean)
    if not p.is_absolute():
        p = Path(base) / clean
    try:
        return str(p.resolve())
    except (OSError, ValueError):
        return ""


def _is_safe_path(abs_path: str) -> bool:
    """validate_path gate — blocks traversal, .git, .env, secrets, system dirs."""
    ok, _ = validate_path(abs_path, is_write=True)
    return ok


def _dedupe(items: list[str]) -> list[str]:
    """Preserve order while removing duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ── Sync git snapshot (serialized by global threading lock) ──

def _git_snapshot_files(file_paths: list[str], tool_name: str) -> None:
    """git add -- <abs paths> && commit. Serialized to avoid index.lock fights."""
    if not file_paths:
        logger.debug("git_snapshot_files skipped: no write targets")
        return
    try:
        with get_git_sync_lock():
            if not _git_add(file_paths):
                return
            _commit_snapshot(tool_name)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("git_snapshot_files skipped: %s", exc)


def _git_add(file_paths: list[str]) -> bool:
    """Stage exactly the given absolute paths. Returns False on git failure."""
    r = subprocess.run(
        ["git", "add", "--", *file_paths], cwd=str(ROOT_DIR),
        capture_output=True, text=True, timeout=_GIT_SNAPSHOT_TIMEOUT,
    )
    if r.returncode != 0:
        logger.debug("git add failed for run_command snapshot: %s", r.stderr.strip())
        return False
    return True


def _commit_snapshot(tool_name: str) -> None:
    """Commit staged changes with the agent author; ignore 'nothing to commit'."""
    r = subprocess.run(
        ["git", "commit", "-m", f"agent edit: {tool_name}", "--author", _AGENT_AUTHOR],
        cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=_GIT_SNAPSHOT_TIMEOUT,
    )
    if r.returncode != 0 and "nothing to commit" not in r.stdout and "nothing to commit" not in r.stderr:
        logger.debug("git commit for run_command snapshot: %s", r.stderr.strip())
