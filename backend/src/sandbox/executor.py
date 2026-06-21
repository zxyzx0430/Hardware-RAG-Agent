"""Docker 沙箱代码执行器

修复点：
1. C/C++ 代码通过 stdin 传入容器（原实现 cat 未收到输入）
2. 同步 Docker 调用包装到 asyncio.to_thread，避免阻塞事件循环
3. 资源限制补全 cpu_period / memswap_limit
4. 容器清理用 finally 保证
"""
import asyncio
import logging
import time
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timed_out: bool = False


# 支持的语言 -> Docker 镜像映射
LANGUAGE_IMAGES = {
    "python": "python:3.11-slim",
    "c": "gcc:latest",
    "cpp": "gcc:latest",
    "javascript": "node:20-slim",
    "arduino": "platformio/platformio-core:latest",
}

# 资源限制
CPU_TIMEOUT = 10  # 秒
MEMORY_LIMIT = "256m"
NETWORK_DISABLED = True


async def check_docker_available() -> bool:
    """检查 Docker 是否可用（同步调用包装到线程）"""
    try:
        def _check():
            import docker
            client = docker.from_env()
            client.ping()
            return True
        return await asyncio.to_thread(_check)
    except Exception as e:
        logger.warning(f"Docker 不可用: {e}")
        return False


def _run_container_sync(code: str, language: str) -> ExecutionResult:
    """同步执行容器（在线程中运行）。"""
    import docker
    start_time = time.time()
    timed_out = False

    image = LANGUAGE_IMAGES[language]

    # 根据语言准备执行命令
    if language == "python":
        cmd = ["python", "-c", code]
        stdin_input = None
    elif language in ("c", "cpp"):
        ext = "c" if language == "c" else "cpp"
        compiler = "gcc" if language == "c" else "g++"
        # 通过 stdin 把代码写入容器内文件
        cmd = ["sh", "-c", f"cat > /tmp/code.{ext} && {compiler} /tmp/code.{ext} -o /tmp/code && /tmp/code"]
        stdin_input = code
    elif language == "javascript":
        cmd = ["node", "-e", code]
        stdin_input = None
    elif language == "arduino":
        # Arduino: 通过 stdin 写入源码，用 platformio ci 编译
        cmd = ["sh", "-c", "mkdir -p /tmp/project/src && cat > /tmp/project/src/main.ino && platformio ci --board=esp32dev /tmp/project"]
        stdin_input = code
    else:
        cmd = ["sh", "-c", code]
        stdin_input = None

    client = docker.from_env()
    container = None
    try:
        container = client.containers.run(
            image=image,
            command=cmd,
            stdin_open=True,
            detach=True,
            mem_limit=MEMORY_LIMIT,
            memswap_limit=MEMORY_LIMIT,  # 禁止 swap
            cpu_period=100000,           # 100ms（必须配 cpu_quota 才生效）
            cpu_quota=100000,            # 1 个 CPU
            network_disabled=NETWORK_DISABLED,
            tmpfs={"/tmp": "size=50m"},
            read_only=True,
            stdout=True,
            stderr=True,
            user="nobody",  # 非 root 运行
        )

        # 若需要 stdin 输入，通过 socket 发送
        if stdin_input is not None:
            try:
                sock = container.attach(stdin=True, stdout=False, stderr=False, stream=False)
                sock.send(stdin_input.encode("utf-8"))
                sock.close()
            except Exception as e:
                logger.warning(f"stdin 输入失败: {e}")

        # 等待容器完成（带超时）
        try:
            result = container.wait(timeout=CPU_TIMEOUT)
            exit_code = result.get("StatusCode", -1)
        except Exception:
            container.kill()
            exit_code = -1
            timed_out = True

        # 获取输出
        logs = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        err_logs = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            stdout=logs[-10000:],  # 限制输出长度
            stderr=err_logs[-5000:],
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        return ExecutionResult(
            stdout="",
            stderr=f"执行失败: {str(e)}",
            exit_code=-1,
            duration_ms=duration_ms,
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


async def execute_code(code: str, language: str = "python") -> ExecutionResult:
    """在 Docker 容器中执行代码（async 安全，不阻塞事件循环）"""
    language = language.lower().strip()
    image = LANGUAGE_IMAGES.get(language)
    if not image:
        return ExecutionResult(
            stdout="",
            stderr=f"不支持的语言: {language}。支持: {', '.join(LANGUAGE_IMAGES.keys())}",
            exit_code=-1,
            duration_ms=0,
        )

    # 检查 Docker 可用性
    if not await check_docker_available():
        return ExecutionResult(
            stdout="",
            stderr="Docker 未安装或未启动。请安装 Docker Desktop 并确保其正在运行。",
            exit_code=-1,
            duration_ms=0,
        )

    # 同步 Docker 调用包装到线程，避免阻塞事件循环
    return await asyncio.to_thread(_run_container_sync, code, language)
