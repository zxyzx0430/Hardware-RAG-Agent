"""PlatformIO compile/upload runner with SSE streaming.

Wraps `pio run` and `pio run --target upload` subprocess calls.
Yields SSE event dicts with `type` field: thinking / progress / compile_log / done.
"""

from __future__ import annotations

import os
from pathlib import Path

_PLATFORMIO_CORE_DIR = str(Path(__file__).resolve().parents[2] / ".platformio")
os.environ.setdefault("PLATFORMIO_CORE_DIR", _PLATFORMIO_CORE_DIR)

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import serial.tools.list_ports

logger = logging.getLogger(__name__)

# === Board id -> PlatformIO board name ===
SUPPORTED_PLATFORMS: tuple[str, ...] = ("espressif32", "ststm32")
DEFAULT_PLATFORM: str = "espressif32"
# framework per platform — user can override via CompileRequest.framework
PLATFORM_FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "espressif32": ("arduino", "espidf"),
    "ststm32": ("arduino", "stm32cube"),
}
DEFAULT_FRAMEWORK: str = "arduino"
# STM32 串口烧录默认协议（用户可在 options 里覆盖为 stlink/dfu/jlink 等）
STM32_DEFAULT_UPLOAD_PROTOCOL: str = "serial"
DEFAULT_COMPILE_TIMEOUT_S: int = 600
DEFAULT_UPLOAD_TIMEOUT_S: int = 120
HEARTBEAT_INTERVAL_S: float = 15.0
ALLOWED_BINARY_PREFIX: str = ".build/tmp/"
BUILD_TMP_ROOT: str = ".build/tmp"
DEFAULT_UPLOAD_SPEED: int = 921600
DEFAULT_CLEANUP_AGE_HOURS: int = 24
SECONDS_PER_HOUR: int = 3600
LINE_READ_TIMEOUT_S: float = 0.5
LOG_TAIL_LINES: int = 50
PROGRESS_AFTER_PROJECT_CREATED: int = 5
PROGRESS_BEFORE_UPLOAD: int = 10
SOURCE_BUILD = "build"
SOURCE_FLASH = "flash"

# === Error codes ===
ERR_PIO_NOT_INSTALLED = "PIO_NOT_INSTALLED"
ERR_COMPILE_FAILED = "COMPILE_FAILED"
ERR_COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
ERR_UPLOAD_TIMEOUT = "UPLOAD_TIMEOUT"
ERR_PORT_NOT_FOUND = "PORT_NOT_FOUND"
ERR_PORT_BUSY = "PORT_BUSY"
ERR_INVALID_BINARY_PATH = "INVALID_BINARY_PATH"
ERR_UNSUPPORTED_BOARD = "UNSUPPORTED_BOARD"
ERR_SPAWN_FAILED = "SPAWN_FAILED"
ERR_BINARY_NOT_FOUND = "BINARY_NOT_FOUND"
ERR_UPLOAD_FAILED = "UPLOAD_FAILED"


@dataclass(frozen=True)
class CompileRequest:
    """Inputs for compile_firmware."""

    code: str
    board: str          # PlatformIO board id, e.g. esp32-s3-devkitc-1 / black_f407vg
    platform: str       # espressif32 / ststm32
    options: dict[str, Any]
    session_id: str
    lib_deps: tuple[str, ...] = ()  # PlatformIO lib_deps entries, e.g. ("adafruit/Adafruit NeoPixel",)
    framework: str = DEFAULT_FRAMEWORK  # arduino / espidf / stm32cube


@dataclass(frozen=True)
class UploadRequest:
    """Inputs for upload_firmware."""

    binary_path: str
    board: str
    platform: str
    port: str
    options: dict[str, Any]
    session_id: str
    lib_deps: tuple[str, ...] = ()
    framework: str = DEFAULT_FRAMEWORK


@dataclass(frozen=True)
class StreamConfig:
    """Subprocess streaming config: timeout + error code/message on timeout."""

    timeout_s: int
    timeout_code: str
    timeout_msg: str


@dataclass(frozen=True)
class DoneConfig:
    """Config for building the final done event after pio completes."""

    success_payload: dict[str, Any]
    fail_code: str
    fail_msg: str


@dataclass(frozen=True)
class PioTaskConfig:
    """Bundle of args + stream cfg + done cfg for spawning one pio subprocess."""

    args: list[str]
    stream_cfg: StreamConfig
    done_cfg: DoneConfig


@dataclass
class StreamContext:
    """Mutable state while streaming pio subprocess output."""

    proc: asyncio.subprocess.Process
    cfg: StreamConfig
    start: float
    buffer: list[str] = field(default_factory=list)
    last_heartbeat: float = field(default_factory=time.monotonic)
    done_sent: bool = False


# === Event builders ===


def _event(event_type: str, **fields: Any) -> dict[str, Any]:
    """Build an SSE event dict with the given type and fields."""
    return {"type": event_type, **fields}


def _error_done(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """Build an error done SSE event."""
    err: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"type": "done", "success": False, "error": err}


# === Validation helpers ===


def _validate_board(board: str) -> str:
    """Validate board is non-empty. Let PlatformIO validate the actual board id."""
    if not board or not board.strip():
        raise ValueError("board cannot be empty")
    return board.strip()


def _resolve_env_name(board: str) -> str:
    """esp32-s3 -> esp32s3 (strip dashes)."""
    return board.replace("-", "")


def _validate_binary_path(binary_path: str) -> bool:
    """True if path is under .build/tmp/ (path traversal protection)."""
    norm = Path(os.path.normpath(binary_path))
    prefix = Path(os.path.normpath(ALLOWED_BINARY_PREFIX))
    try:
        norm.relative_to(prefix)
        return True
    except ValueError:
        return False


def _validate_port(port: str) -> tuple[bool, list[str]]:
    """Return (is_valid, available_ports). Empty list on enumeration error."""
    try:
        available = [p.device for p in serial.tools.list_ports.comports()]
    except Exception as e:
        logger.warning("Port enumeration failed: %s", e)
        return (False, [])
    return (port in available, available)


def _ini_lines(board: str, platform: str, options: dict[str, Any],
               lib_deps: tuple[str, ...] = (), framework: str = DEFAULT_FRAMEWORK) -> list[str]:
    """Build platformio.ini lines based on platform + framework."""
    fw = _resolve_framework(platform, framework)
    lines = [
        f"[env:{_resolve_env_name(board)}]",
        f"platform = {platform}",
        f"board = {_validate_board(board)}",
        f"framework = {fw}",
    ]
    if platform == "espressif32":
        lines.append(f"upload_speed = {DEFAULT_UPLOAD_SPEED}")
        lines.append("board_build.arduino.ldvariant = default")
    elif platform == "ststm32":
        # STM32 串口烧录需要 upload_protocol = serial（除非 options 里指定了别的）
        if "upload_protocol" not in options:
            lines.append(f"upload_protocol = {STM32_DEFAULT_UPLOAD_PROTOCOL}")
    if "upload_port" in options:
        lines.append(f"upload_port = {options['upload_port']}")
    # Pass through other custom options
    for key, value in options.items():
        if key not in ("upload_port", "upload_protocol"):
            lines.append(f"{key} = {value}")
    if lib_deps:
        lines.append("lib_deps =")
        for dep in lib_deps:
            lines.append(f"  {dep}")
    return lines


def _generate_platformio_ini(board: str, platform: str, options: dict[str, Any],
                             lib_deps: tuple[str, ...] = (),
                             framework: str = DEFAULT_FRAMEWORK) -> str:
    """Render platformio.ini content for board + platform + options + lib_deps + framework."""
    return "\n".join(_ini_lines(board, platform, options, lib_deps, framework)) + "\n"


def _resolve_framework(platform: str, framework: str) -> str:
    """Validate framework against platform's supported list; fallback to default."""
    supported = PLATFORM_FRAMEWORKS.get(platform, ())
    if framework in supported:
        return framework
    # fallback: first supported framework for the platform
    if supported:
        return supported[0]
    return DEFAULT_FRAMEWORK


def _find_binary_path(project_dir: Path, env_name: str) -> Path | None:
    """Locate firmware.bin under .pio/build/{env_name}/ or None."""
    binary = project_dir / ".pio" / "build" / env_name / "firmware.bin"
    return binary if binary.exists() else None


def _expected_binary_path(project_dir: Path, env_name: str) -> Path:
    """Return expected firmware.bin path (whether or not it exists)."""
    return project_dir / ".pio" / "build" / env_name / "firmware.bin"


# === Subprocess helpers ===


async def _check_pio_installed() -> bool:
    """True if `pio --version` runs successfully."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pio", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        logger.warning("pio --version spawn failed: %s", e)
        return False
    await proc.communicate()
    return proc.returncode == 0


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Kill subprocess and wait for it to exit (errors logged, not raised)."""
    try:
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass
    except Exception as e:
        logger.warning("Failed to kill subprocess: %s", e)


async def _read_with_timeout(proc: asyncio.subprocess.Process) -> str | None:
    """Read one stdout line. None=EOF, ''=short timeout (skip)."""
    try:
        raw = await asyncio.wait_for(
            proc.stdout.readline(), timeout=LINE_READ_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return ""
    if not raw:
        return None
    return raw.decode(errors="replace").rstrip()


def _is_timed_out(start: float, timeout_s: int) -> bool:
    """True if elapsed since start exceeds timeout_s."""
    return time.monotonic() - start > timeout_s


async def _spawn_pio(args: list[str], cwd: Path) -> asyncio.subprocess.Process | None:
    """Spawn `pio <args>` subprocess in cwd. None on spawn failure."""
    try:
        return await asyncio.create_subprocess_exec(
            "pio", *args, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        logger.error("Failed to spawn pio %s: %s", args, e)
        return None


# === Streaming pipeline ===


async def _next_pio_event(ctx: StreamContext) -> tuple[dict[str, Any] | None, bool]:
    """Return (event, should_stop). None event means skip this iteration."""
    if _is_timed_out(ctx.start, ctx.cfg.timeout_s):
        await _kill_process(ctx.proc)
        ctx.done_sent = True
        return _error_done(ctx.cfg.timeout_code, ctx.cfg.timeout_msg), True
    line = await _read_with_timeout(ctx.proc)
    if line is None:
        return None, True
    if not line:
        return _maybe_heartbeat(ctx), False
    ctx.last_heartbeat = time.monotonic()
    _append_log_line(ctx.buffer, line)
    return _event("compile_log", line=line, stream="stdout"), False


def _maybe_heartbeat(ctx: StreamContext) -> dict[str, Any] | None:
    """Yield a heartbeat event if interval elapsed since last output."""
    now = time.monotonic()
    if now - ctx.last_heartbeat < HEARTBEAT_INTERVAL_S:
        return None
    ctx.last_heartbeat = now
    elapsed = int(now - ctx.start)
    return _event("heartbeat", elapsed=elapsed, message=f"正在处理... ({elapsed}s)")


def _append_log_line(buffer: list[str], line: str) -> None:
    """Append log line and trim to last LOG_TAIL_LINES entries."""
    buffer.append(line)
    del buffer[:-LOG_TAIL_LINES]


async def _stream_pio_output(ctx: StreamContext) -> AsyncIterator[dict[str, Any]]:
    """Stream pio stdout as compile_log events, kill on timeout."""
    while True:
        event, stop = await _next_pio_event(ctx)
        if event is not None:
            yield event
        if stop:
            return


async def _build_pio_done_event(ctx: StreamContext, done_cfg: DoneConfig) -> dict[str, Any] | None:
    """Build done event after pio completes. None if a done event was already sent."""
    if ctx.done_sent:
        return None
    await ctx.proc.wait()
    ctx.done_sent = True
    if ctx.proc.returncode == 0:
        return {"type": "done", "success": True, **done_cfg.success_payload}
    details = "\n".join(ctx.buffer[-LOG_TAIL_LINES:])
    return _error_done(done_cfg.fail_code, done_cfg.fail_msg, details)


async def _stream_pio_subprocess(
    project_dir: Path, task: PioTaskConfig,
) -> AsyncIterator[dict[str, Any]]:
    """Spawn pio subprocess, stream output, yield final done event."""
    proc = await _spawn_pio(task.args, project_dir)
    if proc is None:
        yield _error_done(ERR_SPAWN_FAILED, "启动 pio 失败")
        return
    ctx = StreamContext(proc=proc, cfg=task.stream_cfg, start=time.monotonic())
    try:
        async for event in _stream_pio_output(ctx):
            yield event
        done_event = await _build_pio_done_event(ctx, task.done_cfg)
        if done_event is not None:
            yield done_event
    finally:
        if proc.returncode is None:
            await _kill_process(proc)


# === Temp project creation + timeout resolution ===


def _create_temp_project(req: CompileRequest) -> Path:
    """Create .build/tmp/{session}/ with src/main.cpp + platformio.ini."""
    project_dir = Path(BUILD_TMP_ROOT) / req.session_id
    src_dir = project_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "main.cpp").write_text(req.code, encoding="utf-8")
    ini = _generate_platformio_ini(
        req.board, req.platform, req.options, req.lib_deps, req.framework,
    )
    (project_dir / "platformio.ini").write_text(ini, encoding="utf-8")
    return project_dir


def _resolve_timeout(env_key: str, default: int) -> int:
    """Read int from env, fallback to default on parse error."""
    try:
        return int(os.environ.get(env_key, str(default)))
    except ValueError:
        logger.warning("Invalid int for %s, using default %s", env_key, default)
        return default


# === Public: compile ===


async def compile_firmware(req: CompileRequest) -> AsyncIterator[dict[str, Any]]:
    """Stream compile progress: thinking -> progress -> compile_log -> done."""
    yield _event("thinking", content="正在编译固件...", source=SOURCE_BUILD)
    if not await _check_pio_installed():
        yield _error_done(ERR_PIO_NOT_INSTALLED, "请先 pip install platformio")
        return
    async for event in _run_compile(req):
        yield event


async def _run_compile(req: CompileRequest) -> AsyncIterator[dict[str, Any]]:
    """Create temp project (or yield error), then stream pio run output."""
    project_dir = _try_create_project(req)
    if isinstance(project_dir, dict):
        yield project_dir
        return
    yield _event("progress", percent=PROGRESS_AFTER_PROJECT_CREATED, message="已创建临时项目")
    task = _compile_task(project_dir, _resolve_env_name(req.board))
    async for event in _stream_pio_subprocess(project_dir, task):
        yield event


def _try_create_project(req: CompileRequest) -> Path | dict[str, Any]:
    """Create temp project; return Path on success or error event dict on failure."""
    try:
        return _create_temp_project(req)
    except ValueError as e:
        return _error_done(ERR_UNSUPPORTED_BOARD, str(e))
    except OSError as e:
        return _error_done(ERR_SPAWN_FAILED, f"创建临时项目失败: {e}")


def _compile_task(project_dir: Path, env_name: str) -> PioTaskConfig:
    """Build PioTaskConfig for compile (with expected binary path)."""
    binary = _find_binary_path(project_dir, env_name)
    binary_path = binary or _expected_binary_path(project_dir, env_name)
    return PioTaskConfig(
        args=["run"],
        stream_cfg=StreamConfig(
            _resolve_timeout("HWRAG_COMPILE_TIMEOUT", DEFAULT_COMPILE_TIMEOUT_S),
            ERR_COMPILE_TIMEOUT, "编译超时",
        ),
        done_cfg=DoneConfig({"binary_path": str(binary_path)}, ERR_COMPILE_FAILED, "编译失败"),
    )


# === Public: upload ===


async def upload_firmware(req: UploadRequest) -> AsyncIterator[dict[str, Any]]:
    """Stream upload progress: thinking -> progress -> compile_log -> done."""
    yield _event("thinking", content="正在烧录固件...", source=SOURCE_FLASH)
    preflight = _check_upload_preflight(req)
    if preflight is not None:
        yield preflight
        return
    async for event in _run_upload(req):
        yield event


def _check_upload_preflight(req: UploadRequest) -> dict[str, Any] | None:
    """Return error event if validation fails, else None."""
    if not _validate_binary_path(req.binary_path):
        return _error_done(ERR_INVALID_BINARY_PATH, f"非法路径: {req.binary_path}")
    valid, ports = _validate_port(req.port)
    if not valid:
        return _error_done(ERR_PORT_NOT_FOUND, f"端口不存在: {req.port}", ports)
    if req.platform not in SUPPORTED_PLATFORMS:
        return _error_done(
            ERR_UNSUPPORTED_BOARD,
            f"不支持的 platform: {req.platform}，支持: {', '.join(SUPPORTED_PLATFORMS)}",
        )
    return None


def _resolve_project_dir(binary_path: str) -> Path | None:
    """Derive project dir from binary_path. None if invalid shape."""
    parts = Path(os.path.normpath(binary_path)).parts
    # Expected: ('.build', 'tmp', '{session}', '.pio', 'build', '{env}', 'firmware.bin')
    if len(parts) < 3:
        return None
    return Path(parts[0], parts[1], parts[2])


def _ensure_upload_port(project_dir: Path, req: UploadRequest) -> None:
    """Re-write platformio.ini with upload_port + framework + lib_deps."""
    options: dict[str, Any] = {"upload_port": req.port}
    ini = _generate_platformio_ini(
        req.board, req.platform, options, req.lib_deps, req.framework,
    )
    (project_dir / "platformio.ini").write_text(ini, encoding="utf-8")


async def _run_upload(req: UploadRequest) -> AsyncIterator[dict[str, Any]]:
    """Resolve project dir, ensure upload_port, then stream pio upload."""
    project_dir = _resolve_project_dir(req.binary_path)
    if project_dir is None or not project_dir.exists():
        yield _error_done(ERR_INVALID_BINARY_PATH, f"路径不存在: {req.binary_path}")
        return
    _ensure_upload_port(project_dir, req)
    yield _event("progress", percent=PROGRESS_BEFORE_UPLOAD, message="开始烧录")
    async for event in _stream_pio_subprocess(project_dir, _upload_task()):
        yield event


def _upload_task() -> PioTaskConfig:
    """Build PioTaskConfig for upload."""
    return PioTaskConfig(
        args=["run", "--target", "upload"],
        stream_cfg=StreamConfig(
            _resolve_timeout("HWRAG_UPLOAD_TIMEOUT", DEFAULT_UPLOAD_TIMEOUT_S),
            ERR_UPLOAD_TIMEOUT, "烧录超时",
        ),
        done_cfg=DoneConfig({"message": "烧录完成"}, ERR_UPLOAD_FAILED, "烧录失败"),
    )


# === Cleanup ===


async def cleanup_old_builds(max_age_hours: int = DEFAULT_CLEANUP_AGE_HOURS) -> int:
    """Scan .build/tmp/ and delete dirs older than max_age_hours."""
    return await asyncio.to_thread(_sync_cleanup_old_builds, max_age_hours)


def _sync_cleanup_old_builds(max_age_hours: int) -> int:
    """Synchronous implementation of cleanup_old_builds."""
    root = Path(BUILD_TMP_ROOT)
    if not root.exists():
        return 0
    deleted = 0
    for sub in root.iterdir():
        if _is_dir_stale(sub, max_age_hours):
            deleted += _safe_rmtree(sub)
    return deleted


def _is_dir_stale(path: Path, max_age_hours: int) -> bool:
    """True if path is a dir older than max_age_hours."""
    if not path.is_dir():
        return False
    age_s = time.time() - path.stat().st_mtime
    return age_s > max_age_hours * SECONDS_PER_HOUR


def _safe_rmtree(path: Path) -> int:
    """rmtree with error handling. Returns 1 on success, 0 on failure."""
    try:
        shutil.rmtree(path)
        logger.info("cleanup_old_builds deleted %s", path)
        return 1
    except OSError as e:
        logger.warning("Failed to delete %s: %s", path, e)
        return 0
